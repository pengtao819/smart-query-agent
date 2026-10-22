"""护栏自检：攻击样本 + LIMIT 补全 + 超时中断，三段验证。

用法：
    uv run python scripts/check_guard.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db.executor import QueryTimeout, execute_sql  # noqa: E402
from app.services.guard import DEFAULT_ROW_LIMIT, check_sql  # noqa: E402

# (SQL, 期望是否通过, 说明)
CASES: list[tuple[str, bool, str]] = [
    # ---------- 应该拒绝 ----------
    ("SELECT 1; DROP TABLE fact_order", False, "分号拼接第二条语句"),
    ("DROP TABLE fact_order", False, "DROP"),
    ("DELETE FROM fact_order", False, "DELETE"),
    ("UPDATE fact_order SET total_amount = 0", False, "UPDATE"),
    ("INSERT INTO fact_order SELECT * FROM fact_order", False, "INSERT"),
    ("CREATE TABLE evil AS SELECT 1", False, "CREATE"),
    ("ATTACH 'C:/secret.db' AS s", False, "ATTACH 挂载外部库"),
    ("COPY fact_order TO 'C:/out.csv'", False, "COPY 导出文件"),
    ("SELECT * FROM read_csv('C:/secret.csv')", False, "read_csv 读本地文件"),
    ("SELECT * FROM read_parquet('C:/x.parquet')", False, "read_parquet 读本地文件"),
    ("SELECT * FROM 'C:/secret.csv'", False, "FROM 直接跟文件路径"),
    ("PRAGMA database_list", False, "PRAGMA 读系统信息"),
    ("SELECT 1 UNION SELECT 2; SELECT 3", False, "UNION 后再拼语句"),
    ("", False, "空 SQL"),
    # ---------- 表白名单 ----------
    ("SELECT * FROM duckdb_settings()", False, "表函数读系统配置"),
    ("SELECT table_name FROM information_schema.tables", False, "information_schema"),
    ("SELECT * FROM sqlite_master", False, "sqlite 系统表"),
    ("SELECT * FROM not_a_table", False, "不在白名单的表"),
    ("SELECT * FROM secret_schema.fact_order", False, "非法 schema 前缀"),
    # ---------- 应该放行 ----------
    ("SELECT COUNT(*) FROM fact_order", True, "普通聚合"),
    ("WITH a AS (SELECT 1 AS x) SELECT * FROM a", True, "CTE"),
    ("SELECT region_id, SUM(total_amount) FROM fact_order GROUP BY 1 ORDER BY 2 DESC", True, "分组排序"),
    ("SELECT * FROM fact_order UNION ALL SELECT * FROM fact_order", True, "UNION ALL"),
    ("SELECT * FROM fact_order WHERE total_amount > 100", True, "带过滤条件"),
    ("SELECT COUNT(*) FROM main.fact_order", True, "库名 main 前缀"),
    ("WITH recent AS (SELECT 1 AS x) SELECT * FROM recent", True, "CTE 别名不算未知表"),
    ("SELECT * FROM generate_series(1, 5)", True, "放行的表函数"),
    ("SELECT o.order_id, c.customer_id FROM fact_order o JOIN dim_customer c ON c.customer_id = o.customer_id", True, "多表 JOIN"),
]

# (SQL, 期望出现在结果 SQL 里的片段, 说明)
LIMIT_CASES: list[tuple[str, str, str]] = [
    ("SELECT * FROM fact_order", f"LIMIT {DEFAULT_ROW_LIMIT}", "没有 LIMIT 时自动补"),
    ("SELECT * FROM fact_order LIMIT 50", "LIMIT 50", "已有更小的 LIMIT 保留"),
    ("SELECT * FROM fact_order LIMIT 5000", f"LIMIT {DEFAULT_ROW_LIMIT}", "过大 LIMIT 收紧"),
    ("SELECT region_id, SUM(total_amount) FROM fact_order GROUP BY 1", f"LIMIT {DEFAULT_ROW_LIMIT}", "聚合结果也补"),
]


def check_rules() -> int:
    failed = 0
    for sql, should_pass, note in CASES:
        result = check_sql(sql, max_rows=None)  # 只看校验，不改 LIMIT
        ok = result.ok == should_pass
        failed += not ok
        mark = "OK  " if ok else "FAIL"
        expect = "放行" if should_pass else "拒绝"
        actual = "放行" if result.ok else f"拒绝[{result.rule}]"
        shown = sql.replace("\n", " ")[:56] or "(空)"
        print(f"[{mark}] 期望{expect} 实际{actual}  {shown}")
        if not ok or not result.ok:
            reason = result.reason if not result.ok else "（已放行）"
            print(f"        {note} | {reason}")
    return failed


def check_limit() -> int:
    print("\n--- LIMIT 补全 ---")
    failed = 0
    for sql, expect, note in LIMIT_CASES:
        result = check_sql(sql)
        ok = result.ok and expect in result.sql
        failed += not ok
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {note}  ->  {result.sql if result.ok else result.reason}")
    return failed


def check_timeout() -> int:
    print("\n--- 超时中断 ---")
    # 注意不能用 COUNT(*)：DuckDB 会把笛卡尔积按基数乘法优化掉，秒出结果。
    # 用三角函数做逐行计算，确保真的在跑。
    sql = (
        "SELECT SUM(sin(a.item_amount) * cos(b.item_amount)) "
        "FROM fact_order_item a CROSS JOIN fact_order_item b"
    )
    started = time.perf_counter()
    try:
        execute_sql(sql, timeout_ms=800)
    except QueryTimeout:
        cost = time.perf_counter() - started
        print(f"[OK  ] 笛卡尔积查询在 {cost:.2f}s 后被中断")
        return 0
    print("[FAIL] 超时未生效，查询跑完了")
    return 1


def main() -> int:
    failed = check_rules() + check_limit() + check_timeout()
    total = len(CASES) + len(LIMIT_CASES) + 1
    print(f"\n通过 {total - failed}/{total}" + ("" if failed == 0 else f"，失败 {failed}"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())