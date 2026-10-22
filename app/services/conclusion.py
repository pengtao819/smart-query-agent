"""结论生成：把结果集交给模型，写 2~3 句中文结论。

三条约束（都写进提示词了）：
1. 只能引用给定的数字，禁止推算出没出现过的数
2. 说人话，不要复述 SQL
3. 数据不足以下结论时，直接说"数据不足以判断"
"""

from __future__ import annotations

from app.core.llm import chat_text

CONCLUSION_SYSTEM_PROMPT = """你是数据分析助手，负责把查询结果翻译成 2~3 句中文结论。

硬性要求：
1. 只能用【查询结果】里出现过的数字，禁止自己推算、四舍五入成新数字
2. 不要复述 SQL，不要解释用了什么函数
3. 先说结论（最大的数 / 最高最低 / 趋势方向），再补一句关键对比
4. 如果结果为空，或数据不足以支撑结论，直接说明，不要编
5. 不超过 120 字

输出纯文本，不要 markdown 标题。"""

MAX_ROWS_IN_PROMPT = 30


def _format_rows(columns: list[str], rows: list[tuple], limit: int = MAX_ROWS_IN_PROMPT) -> str:
    lines = [" | ".join(columns)]
    for row in rows[:limit]:
        lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
    if len(rows) > limit:
        lines.append(f"...（共 {len(rows)} 行，此处只列前 {limit} 行）")
    return "\n".join(lines)


def make_conclusion(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[tuple],
    *,
    semantic_text: str = "",
    model: str | None = None,
) -> dict:
    """生成结论。失败不抛异常，由调用方降级处理。"""
    if not rows:
        return {"text": "查询结果为空，没有可总结的数据。", "model": "", "prompt_tokens": 0,
                "completion_tokens": 0, "latency_ms": 0.0}

    parts = []
    if semantic_text:
        parts.append(semantic_text)
    parts.append(f"【业务问题】\n{question}")
    parts.append(f"【查询结果】\n{_format_rows(columns, rows)}")
    parts.append("请写 2~3 句中文结论。")

    result = chat_text(
        CONCLUSION_SYSTEM_PROMPT,
        "\n\n".join(parts),
        model=model,
        max_tokens=400,
        temperature=0.0,
    )
    return {
        "text": result.content,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_ms": result.latency_ms,
    }