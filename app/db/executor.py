"""SQL 执行器：只读连接 + 超时 + 行数上限。

评测脚本和后面的 Agent 共用这套执行逻辑，保证两边行为一致。
只读连接从根上禁止任何写操作（DML/DDL 都会被 DuckDB 拒绝）。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "analytics.duckdb"

DEFAULT_TIMEOUT_MS = 10_000  # 单条 SQL 最多跑 10 秒
MAX_ROWS = 10_000            # 最多返回 1 万行，防止结果爆炸


@contextmanager
def read_only_conn(db_path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """只读连接上下文管理器，用完自动关闭。"""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在: {path}")
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()


def execute_sql(
    sql: str,
    *,
    db_path: Path | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_rows: int = MAX_ROWS,
) -> tuple[list[str], list[tuple]]:
    """执行一条 SQL，返回 (列名列表, 行列表)。只读，超时会抛异常。"""
    if not sql or not sql.strip():
        return [], []

    # 注意：DuckDB 没有 statement_timeout（那是 PostgreSQL 的设置）。
    # 超时控制放到 Step 4 的护栏里，用后台线程 + con.interrupt() 实现。
    with read_only_conn(db_path) as con:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows)

    return columns, rows