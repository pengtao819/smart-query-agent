"""Baseline 评测：全 schema + zero-shot，拿到「未优化前」的起点数字。

为什么要有 baseline：
后面每一步优化（语义层、护栏、重试）都要和它比，没有起点数字的优化都是自嗨。

用法：
    uv run python scripts/run_baseline.py              # 跑全部可执行题
    uv run python scripts/run_baseline.py --limit 5    # 冒烟测试
    uv run python scripts/run_baseline.py --ids 1,9,21 # 指定题号
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from app.db.executor import execute_sql  # noqa: E402
from app.services.guard import check_sql  # noqa: E402
from app.services.nl2sql import generate_sql  # noqa: E402
from eval import load_questions, results_equal  # noqa: E402

RESULTS_DIR = ROOT / "results"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1))))
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description="baseline NL2SQL 评测")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--ids", type=str, default="", help="指定题号，如 1,9,21")
    parser.add_argument("--model", type=str, default=None, help="覆盖模型名")
    parser.add_argument("--sleep", type=float, default=0.5, help="每题间隔秒数")
    parser.add_argument("--no-semantics", action="store_true", help="关闭语义层，用于 A/B 对比")
    parser.add_argument("--questions", type=str, default="", help="评测集路径，默认 data/eval/questions.jsonl")
    parser.add_argument("--output", type=str, default="", help="报告路径，默认按变体命名")
    args = parser.parse_args()

    use_semantics = not args.no_semantics
    variant = "semantics" if use_semantics else "baseline"
    if args.questions:
        variant = f"{Path(args.questions).stem}_{variant}"
    output_path = args.output or f"results/{variant}_report.json"

    questions = [q for q in load_questions(Path(args.questions) if args.questions else None) if q.get("gold_sql")]
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        sys.exit("没有可评测的题目")

    print(f"共 {len(questions)} 题待评测（变体：{variant}）\n")

    # 先算一次 gold 结果，避免每题重复执行
    gold_rows: dict[int, list[tuple]] = {}
    for q in questions:
        _, rows = execute_sql(q["gold_sql"])
        gold_rows[q["id"]] = rows

    details = []
    for i, q in enumerate(questions, 1):
        t0 = time.perf_counter()
        detail = {
            "id": q["id"],
            "question": q["question"],
            "category": q.get("category", "未分类"),
            "gold_sql": q["gold_sql"],
        }
        try:
            gen = generate_sql(
                q["question"], model=args.model, use_semantics=use_semantics
            )
        except Exception as exc:  # noqa: BLE001
            detail.update({"status": "llm_error", "error": f"{type(exc).__name__}: {exc}"})
            details.append(detail)
            print(f"[{i}/{len(questions)}] #{q['id']:<3} 调用失败: {type(exc).__name__}")
            time.sleep(args.sleep)
            continue

        detail.update(
            {
                "pred_sql": gen["sql"],
                "reason": gen["reason"],
                "prompt_tokens": gen["prompt_tokens"],
                "completion_tokens": gen["completion_tokens"],
                "latency_ms": gen["latency_ms"],
                "model": gen["model"],
                "semantics": gen.get("semantics", {}),
            }
        )

        if not gen["sql"]:
            detail["status"] = "no_sql"  # 模型认为需要澄清
            print(f"[{i}/{len(questions)}] #{q['id']:<3} 无SQL（{gen['reason'][:40]}）")
        else:
            guarded = check_sql(gen["sql"])
            if not guarded.ok:
                detail["status"] = "blocked"
                detail["error"] = guarded.reason
                details.append(detail)
                print(f"[{i}/{len(questions)}] #{q['id']:<3} 护栏拦截: {guarded.rule}")
                time.sleep(args.sleep)
                continue
            try:
                pred_cols, pred_rows = execute_sql(guarded.sql)
                detail["pred_columns"] = pred_cols
                detail["pred_rows"] = len(pred_rows)
                detail["pred_first_row"] = [str(c) for c in pred_rows[0]] if pred_rows else []
                if results_equal(gold_rows[q["id"]], pred_rows):
                    detail["status"] = "correct"
                    print(f"[{i}/{len(questions)}] #{q['id']:<3} 对   {gen['latency_ms']/1000:4.1f}s")
                else:
                    detail["status"] = "wrong_result"
                    print(f"[{i}/{len(questions)}] #{q['id']:<3} 错   {gen['latency_ms']/1000:4.1f}s")
            except Exception as exc:  # noqa: BLE001
                detail["status"] = "exec_error"
                detail["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[{i}/{len(questions)}] #{q['id']:<3} 执行报错: {type(exc).__name__}")

        detail["total_latency_ms"] = (time.perf_counter() - t0) * 1000
        details.append(detail)
        time.sleep(args.sleep)

    # ---------- 汇总 ----------
    total = len(details)
    counts = {}
    for d in details:
        counts[d["status"]] = counts.get(d["status"], 0) + 1

    correct = counts.get("correct", 0)
    executed = correct + counts.get("wrong_result", 0)  # SQL 能跑通的比例
    latencies = [d["latency_ms"] for d in details if d.get("latency_ms")]

    by_category: dict[str, dict] = {}
    for d in details:
        cat = d["category"]
        agg = by_category.setdefault(cat, {"total": 0, "correct": 0})
        agg["total"] += 1
        agg["correct"] += 1 if d["status"] == "correct" else 0

    report = {
        "variant": variant,
        "model": details[0].get("model", "") if details else "",
        "enable_thinking": False,
        "total": total,
        "execution_accuracy": round(correct / total, 4) if total else 0,
        "legal_rate": round(executed / total, 4) if total else 0,
        "status_counts": counts,
        "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "latency_p95_ms": round(percentile(latencies, 95), 1),
        "prompt_tokens_total": sum(d.get("prompt_tokens", 0) for d in details),
        "completion_tokens_total": sum(d.get("completion_tokens", 0) for d in details),
        "by_category": {
            k: {"total": v["total"], "correct": v["correct"],
                "accuracy": round(v["correct"] / v["total"], 4)}
            for k, v in sorted(by_category.items())
        },
        "details": details,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ROOT / output_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"执行准确率：{correct}/{total} = {report['execution_accuracy']:.1%}")
    print(f"SQL 合法率：{executed}/{total} = {report['legal_rate']:.1%}")
    print(f"  ├ 无SQL（模型要求澄清）：{counts.get('no_sql', 0)}")
    print(f"  ├ 执行报错：{counts.get('exec_error', 0)}")
    print(f"  └ 调用失败：{counts.get('llm_error', 0)}")
    print(f"每类：")
    for cat, agg in report["by_category"].items():
        print(f"  {cat:<10} {agg['correct']}/{agg['total']} = {agg['accuracy']:.0%}")
    print(f"耗时：平均 {report['latency_avg_ms']/1000:.1f}s | P95 {report['latency_p95_ms']/1000:.1f}s")
    print(f"token：输入 {report['prompt_tokens_total']} / 输出 {report['completion_tokens_total']}")
    print(f"\n报告：{out_path}")

    failed = [d for d in details if d["status"] != "correct"]
    if failed:
        print("\n失败题目：")
        for d in failed:
            print(f"  #{d['id']:<3} [{d['category']}] {d['status']:<12} {d['question']}")


if __name__ == "__main__":
    main()