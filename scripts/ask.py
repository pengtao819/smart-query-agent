"""命令行问答：走完整链路（语义层 → 生成 → 护栏 → 执行）。

用法：
    uv run python scripts/ask.py "2026年9月的GMV是多少？"
    uv run python scripts/ask.py --no-semantics "9月的客单价是多少"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.pipeline import answer  # noqa: E402

STATUS_LABEL = {
    "ok": "成功",
    "no_sql": "需要澄清",
    "blocked": "被护栏拒绝",
    "timeout": "执行超时",
    "exec_error": "执行报错",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="智能问数命令行")
    parser.add_argument("question", help="问题")
    parser.add_argument("--no-semantics", action="store_true", help="关闭语义层")
    parser.add_argument("--rows", type=int, default=20, help="最多打印多少行")
    args = parser.parse_args()

    result = answer(args.question, use_semantics=not args.no_semantics)
    label = STATUS_LABEL.get(result.status, result.status)

    print(f"问题：{result.question}")
    if result.metrics:
        print(f"命中指标：{'、'.join(result.metrics)}")
    print(f"状态：{label}")
    if result.sql:
        print(f"SQL：{result.sql}")

    if result.status != "ok":
        print(f"说明：{result.message}")
        return

    print(f"列：{result.columns}")
    print(f"行数：{len(result.rows)}")
    for row in result.rows[: args.rows]:
        print("  ", tuple(str(c) for c in row))


if __name__ == "__main__":
    main()