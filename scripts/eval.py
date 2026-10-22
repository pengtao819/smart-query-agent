"""评测脚本：gold SQL 自检 + 结果集比对（执行准确率的判定核心）。

用法：
    uv run python scripts/eval.py --self-check    # 自检所有 gold SQL（标准答案本身得对）
    uv run python scripts/eval.py --selftest      # 验证比对器的容错逻辑是否正常

结果集比对规则（后面接模型时就用这套判对错）：
- 行序无关、列序相关（SELECT 列表顺序即列序）
- 数值容差 0.01（金额是 DECIMAL，先量化到 2 位小数）
- NULL 等价
- 行数必须一致
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = ROOT / "data" / "eval" / "questions.jsonl"
RESULTS_DIR = ROOT / "results"

sys.path.insert(0, str(ROOT))
from app.db.executor import execute_sql  # noqa: E402


def load_questions(path: Path | None = None) -> list[dict]:
    path = path or QUESTIONS_PATH
    if not path.exists():
        return []
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            questions.append(json.loads(line))
    return questions


# ---------------------------------------------------------------------------
# 结果集比对
# ---------------------------------------------------------------------------
def canonical_cell(value: object) -> tuple:
    """把一个值变成可哈希、可比较的规范形式，消除浮点/格式差异。"""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", int(value))
    if isinstance(value, Decimal):
        return ("num", float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
    if isinstance(value, float):
        return ("num", round(value, 2))
    if isinstance(value, int):
        return ("num", float(value))
    if isinstance(value, (date, datetime)):
        return ("t", value.isoformat())
    if isinstance(value, str):
        return ("s", value.strip())
    return ("o", str(value))


def row_key(row: tuple) -> tuple:
    return tuple(canonical_cell(v) for v in row)


def results_equal(gold: list[tuple], pred: list[tuple]) -> bool:
    if len(gold) != len(pred):
        return False
    if gold and pred and len(gold[0]) != len(pred[0]):
        return False
    return Counter(map(row_key, gold)) == Counter(map(row_key, pred))


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def self_check(path: Path | None = None) -> int:
    questions = load_questions(path)
    sql_qs = [q for q in questions if q.get("gold_sql")]
    clarify_qs = [q for q in questions if not q.get("gold_sql")]

    by_category: dict[str, list[str]] = {}
    report = []
    failed = 0

    print(f"共 {len(questions)} 条：{len(sql_qs)} 条可执行 SQL，{len(clarify_qs)} 条需澄清/拒答。\n")

    for q in sql_qs:
        qid, question = q["id"], q["question"]
        cat = q.get("category", "未分类")
        try:
            cols, rows = execute_sql(q["gold_sql"])
            status = "ok"
            head = [str(c) for c in rows[0]] if rows else []
        except Exception as exc:  # noqa: BLE001
            status = "fail"
            failed += 1
            cols, rows, head = [], [], []
            print(f"[FAIL] #{qid} {question}")
            print(f"       {exc}\n")
            by_category.setdefault(cat, []).append(f"#{qid} {question}")
            report.append({"id": qid, "question": question, "category": cat,
                           "status": status, "error": str(exc)})
            continue

        by_category.setdefault(cat, []).append("ok")
        report.append({
            "id": qid, "question": question, "category": cat, "status": status,
            "columns": cols, "row_count": len(rows), "first_row": head,
        })

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "self_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("分类分布：")
    for cat, items in by_category.items():
        n_ok = sum(1 for x in items if x == "ok")
        print(f"  {cat:<8} {n_ok}/{len(items)} 通过")

    clarify_note = [q["id"] for q in clarify_qs]
    print(f"\n需澄清/拒答（暂无 gold SQL，后续单独评测）：{clarify_note}")
    print(f"\n自检结果：{'全部通过' if failed == 0 else str(failed) + ' 条失败'}，报告写入 results/self_check.json")
    return failed


def selftest() -> None:
    a = [(Decimal("1.00"), None, 2), (Decimal("2.50"), "x", 3)]
    b = [(Decimal("2.50"), "x", 3), (Decimal("1.000"), None, 2)]
    c = [(Decimal("1.00"), None, 2)]
    d = [(0.1 + 0.2,), (Decimal("2.50"),)]
    e = [(0.3,), (Decimal("2.5000"),)]
    assert results_equal(a, b), "行序互换应判等"
    assert not results_equal(a, c), "行数不同应判不等"
    assert results_equal(d, e), "浮点容差应判等"
    print("selftest passed：比对器容错逻辑正确")


def main() -> None:
    parser = argparse.ArgumentParser(description="智能问数 Agent 评测")
    parser.add_argument("--self-check", action="store_true", help="自检所有 gold SQL")
    parser.add_argument("--selftest", action="store_true", help="验证结果集比对逻辑")
    parser.add_argument("--questions", type=str, default="", help="评测集路径")
    args = parser.parse_args()
    qpath = Path(args.questions) if args.questions else None

    if args.selftest:
        selftest()
    elif args.self_check:
        sys.exit(self_check(qpath))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()