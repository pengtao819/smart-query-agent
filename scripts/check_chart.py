"""图表选型自检：纯规则，不调模型。

用法：
    uv run python scripts/check_chart.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.chart import select_chart  # noqa: E402

D = Decimal

# (说明, 列, 行, 期望图表类型)
CASES: list[tuple[str, list[str], list[tuple], str]] = [
    ("单值指标", ["GMV"], [(D("1851025.52"),)], "metric"),
    ("月份 × GMV", ["月份", "GMV"], [("2026-01", D("100")), ("2026-02", D("200"))], "line"),
    ("品类 × 销售额", ["一级品类", "销售额"], [("数码", D("100")), ("美妆", D("80"))], "bar"),
    (
        "带排名列的品类",
        ["一级品类", "销售额", "排名"],
        [("数码", D("100"), 1), ("美妆", D("80"), 2)],
        "bar",
    ),
    (
        "月(int) × 渠道 × GMV",
        ["月份", "渠道名称", "GMV"],
        [(1, "APP", D("100")), (1, "京东", D("50")), (2, "APP", D("120")), (2, "京东", D("60"))],
        "multi_line",
    ),
    ("两列数值", ["客单价", "复购率"], [(D("100"), D("0.2")), (D("120"), D("0.3"))], "scatter"),
    ("分类过多", ["商品名", "销售额"], [(f"商品{i}", D(i)) for i in range(50)], "table"),
    ("空结果", ["GMV"], [], "table"),
    ("只有排名列", ["排名"], [(1,), (2,)], "table"),
]


def main() -> int:
    failed = 0
    for note, cols, rows, expect in CASES:
        spec = select_chart(cols, rows)
        ok = spec.type == expect
        failed += not ok
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {note:<22} 期望 {expect:<11} 实际 {spec.type:<11} | {spec.reason}")

    total = len(CASES)
    print(f"\n通过 {total - failed}/{total}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())