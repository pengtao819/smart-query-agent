"""执行反馈自修复：把数据库原始报错回灌给模型，让它改 SQL。

三条约束：
1. 最多 2 轮 —— 第 3 轮基本在瞎猜，成本还不低
2. 修复后的 SQL 必须重新过护栏 —— 自修复是唯一的二次生成环节，不能免检
3. 只修"执行报错"，不修"被护栏拒绝" —— 护栏拒绝往往意味着请求本身越界，
   不该用重试去绕过它

为什么报错信息要原样回灌：
`Binder Error: Referenced column "total_amounts" not found` 这种信息里，
字段名、候选列、甚至正确写法都在里面，比让模型猜有效得多。
"""

from __future__ import annotations

from app.core.llm import chat_json
from app.services.nl2sql import ENUMS_TEXT, clean_sql, render_schema_text
from app.services.semantic_layer import render_semantic_context

REPAIR_SYSTEM_PROMPT = """你是 SQL 修复助手。用户会给出一条执行失败的 SQL 和数据库的原始报错。

要求：
1. 只输出单条 SELECT 查询，禁止任何写操作
2. 只能使用【表结构】里真实存在的表和列名
3. 必须遵守【业务口径】，不要擅自改变指标定义
4. 保持原查询的分析意图，只修错的地方
5. 输出严格 JSON：{"sql": "...", "reason": "改了什么、为什么"}

如果报错说明信息不足无法修复，返回 {"sql": "", "reason": "原因"}。"""


def repair_sql(
    question: str,
    failed_sql: str,
    error: str,
    *,
    model: str | None = None,
    attempt: int = 1,
) -> dict:
    """根据报错生成修正后的 SQL。不抛异常由调用方处理。"""
    semantic_text, _ = render_semantic_context(question)
    schema_text = render_schema_text()

    user_prompt = "\n\n".join(
        [
            semantic_text,
            schema_text,
            ENUMS_TEXT,
            f"【业务问题】\n{question}",
            f"【第 {attempt} 次尝试的 SQL】\n{failed_sql}",
            f"【数据库报错】\n{error}",
            "请输出修正后的 JSON。",
        ]
    )

    result = chat_json(
        REPAIR_SYSTEM_PROMPT,
        user_prompt,
        model=model,
        max_tokens=1200,
        temperature=0.0,
    )
    data = result.data or {}
    return {
        "sql": clean_sql(str(data.get("sql") or "")),
        "reason": str(data.get("reason") or ""),
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_ms": result.latency_ms,
    }