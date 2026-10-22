"""问答主链路：问题 → 生成 SQL → 安全护栏 → 只读执行 →（失败则）自修复。

为什么单独抽一层：
- 命令行脚本、评测脚本、后面的 FastAPI 都调这里，保证三处行为完全一致
- 护栏是"必经关卡"，不依赖调用方记得手动检查
- 自修复是唯一会让模型二次生成 SQL 的环节，修复结果必须重新过护栏
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.executor import QueryTimeout, execute_sql
from app.services.chart import select_chart
from app.services.conclusion import make_conclusion
from app.services.guard import check_sql
from app.services.nl2sql import generate_sql
from app.services.repair import repair_sql

MAX_REPAIRS = 2

STATUS_OK = "ok"
STATUS_NO_SQL = "no_sql"          # 模型主动要求澄清
STATUS_BLOCKED = "blocked"        # 被护栏拒绝
STATUS_TIMEOUT = "timeout"        # 执行超时被中断
STATUS_EXEC_ERROR = "exec_error"  # 数据库报错（含修复失败）


@dataclass
class Answer:
    """一次问答的完整结果。"""

    question: str
    status: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    message: str = ""       # 给人看的说明：澄清问题 / 拒绝理由 / 报错
    guard_rule: str = ""
    metrics: list[str] = field(default_factory=list)
    repairs: list[dict] = field(default_factory=list)  # 每轮修复的尝试记录
    chart: dict = field(default_factory=dict)          # 图表建议（规则产出）
    conclusion: str = ""                               # 一句话结论（模型产出）


def answer(
    question: str,
    *,
    model: str | None = None,
    use_semantics: bool = True,
    timeout_ms: int = 5_000,
    max_repairs: int = MAX_REPAIRS,
    with_conclusion: bool = True,
) -> Answer:
    """跑完整链路。不抛异常，所有失败都体现在 status 上。"""
    gen = generate_sql(question, model=model, use_semantics=use_semantics)
    result = Answer(
        question=question,
        metrics=[m["name"] for m in gen.get("semantics", {}).get("metrics", [])],
    )

    if not gen["sql"]:
        result.status = STATUS_NO_SQL
        result.message = gen["reason"] or "问题信息不足，需要澄清"
        return result

    sql = gen["sql"]
    attempt = 0

    while True:
        # 每一版 SQL（包括模型修复后的）都必须过护栏
        guarded = check_sql(sql)
        if not guarded.ok:
            result.status = STATUS_BLOCKED
            result.sql = sql
            result.guard_rule = guarded.rule
            result.message = guarded.reason
            return result

        result.sql = guarded.sql  # 注意：这是补过 LIMIT 之后的 SQL

        try:
            columns, rows = execute_sql(guarded.sql, timeout_ms=timeout_ms)
        except QueryTimeout as exc:
            # 超时不重试：重试大概率还是超时，白烧钱
            result.status = STATUS_TIMEOUT
            result.message = str(exc)
            return result
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            if attempt >= max_repairs:
                result.status = STATUS_EXEC_ERROR
                result.message = error
                return result

            attempt += 1
            fixed = repair_sql(
                question, guarded.sql, error, model=model, attempt=attempt
            )
            result.repairs.append(
                {
                    "attempt": attempt,
                    "error": error,
                    "sql": fixed["sql"],
                    "reason": fixed["reason"],
                }
            )

            if not fixed["sql"]:
                result.status = STATUS_EXEC_ERROR
                result.message = f"{error}（模型未能给出修正）"
                return result

            sql = fixed["sql"]
            continue

        result.columns = columns
        result.rows = rows
        result.status = STATUS_OK
        result.chart = select_chart(columns, rows).as_dict()

        if with_conclusion:
            semantic_hint = "【指标口径】" + "、".join(result.metrics) if result.metrics else ""
            try:
                concl = make_conclusion(
                    question,
                    result.sql,
                    columns,
                    rows,
                    semantic_text=semantic_hint,
                    model=model,
                )
                result.conclusion = concl["text"]
            except Exception as exc:  # noqa: BLE001 - 结论失败不影响查询结果
                result.conclusion = f"（结论生成失败：{type(exc).__name__}）"

        return result