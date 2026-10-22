"""SQL 安全护栏：执行前用 AST 校验，而不是正则匹配文本。

为什么用 AST 而不是正则：
- 正则挡不住等价写法：大小写、注释、换行、子查询、分号拼接
- AST 能精确判断"这是一条 SELECT"还是"这是一条 DROP"

五道检查（本文件实现前三条，表白名单与 LIMIT 在后续步骤）：
1. 必须能解析
2. 只能有一条语句
3. 顶层类型必须是查询类（SELECT / UNION / INTERSECT / EXCEPT）
4. 禁用读写文件与系统的函数
5. FROM 后不能直接跟文件路径
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

DIALECT = "duckdb"

# 只允许查询类顶层语句。WITH ... SELECT 解析后仍然是 Select，所以自动放行。
ALLOWED_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# 这些函数能读写本地文件、安装扩展或访问系统，必须禁用。
# DuckDB 允许 SELECT * FROM read_csv('C:/x.csv')，只读连接拦不住这种"读"。
FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "read_ndjson_auto",
    "read_text",
    "parquet_scan",
    "csv_scan",
    "sniff_csv",
    "glob",
    "install",
    "load",
    "attach",
    "detach",
    "copy",
}


@dataclass
class GuardResult:
    """校验结果。ok=False 时 reason 是给人看的拒绝理由。"""

    ok: bool
    sql: str = ""
    reason: str = ""
    rule: str = ""

    def __bool__(self) -> bool:  # 支持 `if result:`
        return self.ok


# DuckDB 允许 SELECT * FROM 'C:/x.csv'，sqlglot 会把带引号的路径解析成"表名"，
# 所以除了看字面量，还要看表名长得像不像文件路径。
PATH_HINTS = ("/", "\\", ":")
FILE_EXTS = (
    ".csv", ".parquet", ".json", ".ndjson", ".txt", ".gz",
    ".db", ".duckdb", ".arrow", ".feather", ".xlsx",
)


def _looks_like_path(name: str) -> bool:
    low = str(name or "").lower()
    return any(hint in low for hint in PATH_HINTS) or low.endswith(FILE_EXTS)


def _func_name(node: Any) -> str:
    """取函数名，统一小写。兼容未注册到 sqlglot 的匿名函数。"""
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    if isinstance(node, exp.Func):
        try:
            return str(node.sql_name()).lower()
        except Exception:  # noqa: BLE001 - 个别方言函数取不到名字，忽略即可
            return ""
    return ""


def check_sql(sql: str) -> GuardResult:
    """校验一条 SQL。通过时返回规范化后的 SQL。"""
    raw = (sql or "").strip().rstrip(";").strip()
    if not raw:
        return GuardResult(False, reason="SQL 为空", rule="empty")

    try:
        statements = [s for s in sqlglot.parse(raw, dialect=DIALECT) if s is not None]
    except (ParseError, TokenError) as exc:
        return GuardResult(False, reason=f"SQL 解析失败：{exc}", rule="parse")

    if not statements:
        return GuardResult(False, reason="没有解析出任何语句", rule="empty")

    if len(statements) > 1:
        kinds = "、".join(type(s).__name__ for s in statements[1:])
        return GuardResult(
            False,
            reason=f"检测到多条语句（第 2 条起：{kinds}），只允许单条查询",
            rule="multi_statement",
        )

    stmt = statements[0]

    if not isinstance(stmt, ALLOWED_TYPES):
        return GuardResult(
            False,
            reason=f"不允许的语句类型 {type(stmt).__name__}，只允许 SELECT 查询",
            rule="statement_type",
        )

    for node in stmt.walk():
        name = _func_name(node)
        if name and name in FORBIDDEN_FUNCTIONS:
            return GuardResult(
                False,
                reason=f"禁用的函数 {name}()：可读写本地文件或安装扩展",
                rule="function",
            )

    # FROM 'C:/x.csv' 这类直接把文件当表用的写法
    for table in stmt.find_all(exp.Table):
        if isinstance(table.this, exp.Literal):
            return GuardResult(
                False,
                reason=f"FROM 后直接跟文件路径（{table.this.this}），禁止读取外部文件",
                rule="file_source",
            )
        if _looks_like_path(table.name):
            return GuardResult(
                False,
                reason=f"表名看起来是文件路径（{table.name}），禁止读取外部文件",
                rule="file_source",
            )

    return GuardResult(True, sql=stmt.sql(dialect=DIALECT))