"""NL2SQL 核心：拼 prompt → 调模型 → 抽取并清洗 SQL。

这一版是 **baseline**：把全部 12 张表结构塞进 prompt，zero-shot，不带任何指标口径定义。

刻意不做的事（留给 Step 4 做，好形成对比）：
- 不做 schema 链接（不筛表，全给）
- 不给 GMV / 客单价这类指标口径
- 不做 few-shot
- 不做执行反馈重试
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.llm import chat_json

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_META_PATH = ROOT / "data" / "schema_meta.json"

SYSTEM_PROMPT = """你是资深数据分析师，负责把中文业务问题翻译成 DuckDB SQL。

硬性规则：
1. 只能输出单条 SELECT 查询（可用 WITH 子查询），禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
2. 使用 DuckDB 语法，日期写成 DATE '2026-09-01' 这种形式，时间戳写成 TIMESTAMP '2026-09-01 00:00:00'。
3. 表名、列名必须来自下方给出的表结构，不要臆造不存在的字段。
4. 需要跨表时，严格按【表关系】里的外键字段 JOIN。
5. 金额类指标用 SUM/AVG 汇总后用 ROUND(x, 2) 保留两位小数。
6. 给结果起中文列别名，方便业务方阅读。
7. 如果问题缺少关键信息（比如没说时间范围、指标定义有歧义），把 sql 设为空字符串，在 reason 里说明缺什么、建议怎么问。

输出格式（严格 JSON）：
{"sql": "单条 SQL 或空字符串", "reason": "一句话说明思路或为什么需要澄清"}"""

ENUMS_TEXT = """【业务枚举值】
- fact_order.order_status：paid=已支付 / shipped=已发货 / completed=已完成 / refunded=已退款 / cancelled=已取消
- fact_payment.payment_status：success=成功 / failed=失败
- fact_refund.refund_status：pending=处理中 / completed=已完成 / rejected=已拒绝
- dim_product.status：on_sale=在售 / off_shelf=已下架
- dim_customer.member_level：白银 / 黄金 / 铂金 / 钻石
- dim_channel.channel_name：APP / 小程序 / 天猫 / 京东 / 线下门店
- dim_region.region_name：华东 / 华北 / 华南 / 华中 / 西南 / 东北 / 西北
- dim_category.level：1=一级品类，2=二级品类（二级的 parent_id 指向一级品类）"""


def load_schema_meta() -> dict:
    return json.loads(SCHEMA_META_PATH.read_text(encoding="utf-8"))


def render_schema_text(meta: dict | None = None) -> str:
    """把 schema_meta.json 渲染成给模型看的紧凑文本。"""
    meta = meta or load_schema_meta()
    lines = ["【表结构】"]
    for table in meta["tables"]:
        lines.append(
            f"\n## {table['name']}（{table['description'].rstrip('。')}；"
            f"粒度：{table['grain']}；约 {table['row_count']} 行）"
        )
        for col in table["columns"]:
            desc = f" —— {col['description'].rstrip('。')}" if col.get("description") else ""
            lines.append(f"  - {col['name']} {col['type']}{desc}")

    lines.append("\n【表关系（外键）】")
    for rel in meta.get("relationships", []):
        lines.append(
            f"  - {rel['from_table']}.{rel['from_column']} → {rel['to_table']}.{rel['to_column']}"
        )
    return "\n".join(lines)


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_START_RE = re.compile(r"\b(select|with)\b", re.IGNORECASE)


def clean_sql(text: str) -> str:
    """从模型输出里抠出干净的一条 SQL。"""
    if not text:
        return ""
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    text = text.strip()
    start = _START_RE.search(text)
    if start:
        text = text[start.start() :]
    # 只保留第一条语句，去掉结尾分号
    return text.split(";")[0].strip()


def build_user_prompt(question: str, schema_text: str) -> str:
    return f"{schema_text}\n\n{ENUMS_TEXT}\n\n【业务问题】\n{question}\n\n请输出 JSON。"


def generate_sql(question: str, *, model: str | None = None) -> dict:
    """问题 → SQL。返回解析后的结果 + 成本信息。"""
    schema_text = render_schema_text()
    result = chat_json(
        SYSTEM_PROMPT,
        build_user_prompt(question, schema_text),
        model=model,
        max_tokens=1500,
        temperature=0.0,
    )
    data = result.data or {}
    sql = clean_sql(str(data.get("sql") or ""))
    return {
        "sql": sql,
        "reason": str(data.get("reason") or ""),
        "raw": result.content,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_ms": result.latency_ms,
        "schema_chars": len(schema_text),
    }