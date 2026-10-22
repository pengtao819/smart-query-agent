"""问答主链路：问题 → 生成 SQL → 安全护栏 → 只读执行。

为什么单独抽一层：
- 命令行脚本、评测脚本、后面的 FastAPI 都调这里，保证三处行为完全一致
- 护栏在链路里是"必经关卡"，不依赖调用方记得手动检查
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.executor import QueryTimeout, execute_sql
from app.services.guard import check_sql
from app.services.nl2sql import generate_sql


@dataclass
class Answer:
    """一次问答的完整结果。status 取值见下方常量。"""

    question: str
    status: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    message: str = ""  # 给人看的说明：澄清问题 / 拒绝理由 / 报错
    guard_rule: str = ""
    metrics: list[str] = field(default_factory=list)


STATUS_OK = "ok"
STATUS_NO_SQL = "no_sql"          # 模型主动要求澄清
STATUS_BLOCKED = "blocked"        # 被护栏拒绝
STATUS_TIMEOUT = "timeout"        # 执行超时被中断
STATUS_EXEC_ERROR = "exec_error"  # 数据库报错


def answer(
    question: str,
    *,
    model: str | None = None,
    use_semantics: bool = True,
    timeout_ms: int = 5_000,
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

    guarded = check_sql(gen["sql"])
    if not guarded.ok:
        result.status = STATUS_BLOCKED
        result.sql = gen["sql"]
        result.guard_rule = guarded.rule
        result.message = guarded.reason
        return result

    result.sql = guarded.sql  # 注意：这是补过 LIMIT 之后的 SQL
    try:
        columns, rows = execute_sql(guarded.sql, timeout_ms=timeout_ms)
    except QueryTimeout as exc:
        result.status = STATUS_TIMEOUT
        result.message = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result.status = STATUS_EXEC_ERROR
        result.message = f"{type(exc).__name__}: {exc}"
        return result

    result.columns = columns
    result.rows = rows
    result.status = STATUS_OK
    return result