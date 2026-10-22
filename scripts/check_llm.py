"""检查 LLM 配置：能不能连上、能用哪些模型、目标模型是否可用。

用法：
    uv run python scripts/check_llm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402

CANDIDATES = [
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen-coder-plus",
    "qwen3-max",
    "qwen3-coder-plus",
    "qwen2.5-coder-32b-instruct",
]


def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        sys.exit("LLM_API_KEY 为空，先填 .env")

    print(f"base_url = {settings.llm_base_url}")
    print(f"配置的模型 = {settings.llm_model}\n")

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=30,
    )

    # 1. 尝试列出可用模型（部分兼容层不支持，失败不算错）
    try:
        ids = sorted(m.id for m in client.models.list().data)
        print(f"可用模型（{len(ids)} 个）:")
        for mid in ids:
            print(f"  - {mid}")
        print()
    except Exception as exc:  # noqa: BLE001
        print(f"模型列表接口不可用（正常，兼容层常不实现）: {type(exc).__name__}: {str(exc)[:100]}\n")

    # 2. 逐个尝试候选模型
    print("连通性测试：")
    ok_models = []
    for model in dict.fromkeys([settings.llm_model, *CANDIDATES]):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "只回复两个字：可用"}],
                max_tokens=10,
                temperature=0,
            )
            text = (resp.choices[0].message.content or "").strip()
            print(f"  [OK]   {model:<32} -> {text}")
            ok_models.append(model)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {model:<32} -> {type(exc).__name__}: {str(exc)[:90]}")

    print()
    if ok_models:
        print("可用模型：" + ", ".join(ok_models))
    else:
        print("没有模型调通，检查 Key / base_url。")


if __name__ == "__main__":
    main()