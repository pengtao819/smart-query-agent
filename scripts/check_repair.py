"""自修复验证：三个场景，覆盖"修好了 / 修不好 / 修出危险语句"。

用法：
    uv run python scripts/check_repair.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.services.pipeline as pipeline  # noqa: E402

QUESTION = "2026年9月的GMV是多少？"
# 故意写错列名（total_amounts 不存在），模拟模型常见的"字段拼错"
BROKEN_SQL = (
    "SELECT ROUND(SUM(total_amounts), 2) AS gmv FROM fact_order "
    "WHERE order_date >= DATE '2026-09-01' AND order_date < DATE '2026-10-01'"
)


def fake_gen(sql: str) -> dict:
    return {
        "sql": sql,
        "reason": "人为注入的错误 SQL",
        "semantics": {"metrics": [], "dimensions": [], "rules_applied": []},
        "model": "fake",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0,
    }


def scenario_real_repair() -> bool:
    print("[场景 1] 真实报错回灌：列名写错，看模型能否修好")
    original = pipeline.generate_sql
    pipeline.generate_sql = lambda q, **kw: fake_gen(BROKEN_SQL)  # type: ignore[assignment]
    try:
        result = pipeline.answer(QUESTION)
    finally:
        pipeline.generate_sql = original  # type: ignore[assignment]

    ok = result.status == "ok" and len(result.repairs) >= 1
    print(f"  状态: {result.status} | 修复轮次: {len(result.repairs)}")
    if result.repairs:
        print(f"  原始报错: {result.repairs[0]['error'][:90]}")
        print(f"  修复理由: {result.repairs[0]['reason'][:90]}")
    print(f"  最终 SQL: {result.sql[:110]}")
    print(f"  结果: {result.rows[:1]}")
    print(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def scenario_still_broken() -> bool:
    print("[场景 2] 修复无效：模型一直返回同样的错 SQL，看是否在 2 轮后停止")
    original_gen, original_repair = pipeline.generate_sql, pipeline.repair_sql
    pipeline.generate_sql = lambda q, **kw: fake_gen(BROKEN_SQL)  # type: ignore[assignment]
    pipeline.repair_sql = lambda q, s, e, **kw: {  # type: ignore[assignment]
        "sql": BROKEN_SQL,
        "reason": "假装没改对",
        "model": "fake",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0,
    }
    try:
        result = pipeline.answer(QUESTION)
    finally:
        pipeline.generate_sql, pipeline.repair_sql = original_gen, original_repair  # type: ignore[assignment]

    ok = result.status == "exec_error" and len(result.repairs) == 2
    print(f"  状态: {result.status} | 修复轮次: {len(result.repairs)}（应为 2）")
    print(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def scenario_repair_returns_attack() -> bool:
    print("[场景 3] 修复时模型返回 DROP：看护栏是否拦住")
    original_gen, original_repair = pipeline.generate_sql, pipeline.repair_sql
    pipeline.generate_sql = lambda q, **kw: fake_gen(BROKEN_SQL)  # type: ignore[assignment]
    pipeline.repair_sql = lambda q, s, e, **kw: {  # type: ignore[assignment]
        "sql": "DROP TABLE fact_order",
        "reason": "假装模型被注入",
        "model": "fake",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": 0,
    }
    try:
        result = pipeline.answer(QUESTION)
    finally:
        pipeline.generate_sql, pipeline.repair_sql = original_gen, original_repair  # type: ignore[assignment]

    ok = result.status == "blocked" and result.guard_rule == "statement_type"
    print(f"  状态: {result.status} | 拦截规则: {result.guard_rule}")
    print(f"  拒绝理由: {result.message[:90]}")
    print(f"  {'PASS' if ok else 'FAIL'}\n")
    return ok


def main() -> int:
    results = [
        scenario_real_repair(),
        scenario_still_broken(),
        scenario_repair_returns_attack(),
    ]
    passed = sum(results)
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())