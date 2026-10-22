"""命令行查库小工具。

用法：
    uv run python scripts/query.py                      # 交互模式，输入 SQL 回车执行
    uv run python scripts/query.py "SELECT * FROM dim_channel"
    uv run python scripts/query.py --tables             # 列出所有表和行数
    uv run python scripts/query.py --schema fact_order  # 查看表结构
    uv run python scripts/query.py --sample fact_order  # 查看前 10 行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "analytics.duckdb"

PD_OPTIONS = {
    "display.max_rows": 100,
    "display.max_columns": 40,
    "display.width": 240,
    "display.max_colwidth": 40,
}


def print_df(df: pd.DataFrame) -> None:
    if df.empty:
        print("(空结果)")
        return
    with pd.option_context(*[item for pair in PD_OPTIONS.items() for item in pair]):
        print(df.to_string(index=False))
    print(f"\n({len(df)} 行)")


def run(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    try:
        print_df(con.execute(sql).fetchdf())
    except Exception as exc:  # noqa: BLE001 - 交互工具，错误直接展示给用户
        print(f"SQL 执行失败: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="查询电商业务库（DuckDB）")
    parser.add_argument("sql", nargs="?", help="要执行的 SQL；不传则进入交互模式")
    parser.add_argument("--tables", action="store_true", help="列出所有表及行数")
    parser.add_argument("--schema", metavar="TABLE", help="查看表结构")
    parser.add_argument("--sample", metavar="TABLE", help="查看表的前 10 行")
    args = parser.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"数据库不存在: {DB_PATH}\n先运行: uv run python scripts/init_db.py")

    con = duckdb.connect(str(DB_PATH), read_only=True)

    if args.tables:
        tables = sorted(r[0] for r in con.execute("SHOW TABLES").fetchall())
        rows = [(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables]
        print_df(pd.DataFrame(rows, columns=["表名", "行数"]))
        return

    if args.schema:
        print_df(con.execute(f"DESCRIBE {args.schema}").fetchdf())
        return

    if args.sample:
        print_df(con.execute(f"SELECT * FROM {args.sample} LIMIT 10").fetchdf())
        return

    if args.sql:
        run(con, args.sql)
        return

    print("交互模式：输入 SQL 回车执行，输入 q 退出。")
    while True:
        try:
            sql = input("\nsql> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if sql.lower() in {"q", "quit", "exit"}:
            break
        if sql:
            run(con, sql)


if __name__ == "__main__":
    main()