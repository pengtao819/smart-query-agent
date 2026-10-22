"""查看一个问题命中了哪些指标/维度，以及将要注入 prompt 的口径文本。

用法：
    uv run python scripts/show_semantics.py "2026年9月的客单价是多少？"
    uv run python scripts/show_semantics.py            # 跑内置的 6 个示例
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.semantic_layer import render_semantic_context  # noqa: E402

DEFAULT_CASES = [
    "2026年9月的GMV是多少？",
    "2026年9月的净GMV是多少？",
    "2026年9月的客单价是多少？",
    "2026年9月各渠道的订单数分别是多少？",
    "2026年9月各一级品类的销售额Top3是哪些？",
    "哪个商品卖得最好？",
]


def main() -> None:
    questions = sys.argv[1:] or DEFAULT_CASES
    for i, question in enumerate(questions, 1):
        context, info = render_semantic_context(question)
        metrics = "、".join(f"{m['name']}({m['alias']})" for m in info["metrics"]) or "无"
        dims = "、".join(f"{d['name']}({d['alias']})" for d in info["dimensions"]) or "无"
        print("=" * 74)
        print(f"[{i}] 问题：{question}")
        print(f"    命中指标：{metrics}")
        print(f"    命中维度：{dims}")
        print(f"    注入文本长度：{len(context)} 字符")
        if info.get("rules_applied"):
            print(f"    口径规则：{'；'.join(info['rules_applied'])}")
        print("-" * 74)
        print(context)
        print()


if __name__ == "__main__":
    main()