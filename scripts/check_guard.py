"""护栏自检：用一组攻击样本验证"该拦的拦住、该放的放行"。

用法：
    uv run python scripts/check_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.guard import check_sql  # noqa: E402

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
    # ---------- 应该放行 ----------
    ("SELECT COUNT(*) FROM fact_order", True, "普通聚合"),
    ("WITH a AS (SELECT 1 AS x) SELECT * FROM a", True, "CTE"),
    ("SELECT region_id, SUM(total_amount) FROM fact_order GROUP BY 1 ORDER BY 2 DESC", True, "分组排序"),
    ("SELECT * FROM fact_order UNION ALL SELECT * FROM fact_order", True, "UNION ALL"),
    ("SELECT * FROM fact_order WHERE total_amount > 100", True, "带过滤条件"),
]


def main() -> int:
    passed = 0
    failed = 0

    for sql, should_pass, note in CASES:
        result = check_sql(sql)
        ok = result.ok == should_pass
        passed += ok
        failed += not ok

        mark = "OK  " if ok else "FAIL"
        expect = "放行" if should_pass else "拒绝"
        actual = "放行" if result.ok else f"拒绝[{result.rule}]"
        shown = sql.replace("\n", " ")[:58] or "(空)"
        print(f"[{mark}] 期望{expect} 实际{actual}  {shown}")
        if not ok or not result.ok:
            reason = result.reason if not result.ok else "（已放行）"
            print(f"        {note} | {reason}")

    total = passed + failed
    print(f"\n通过 {passed}/{total}" + ("" if failed == 0 else f"，失败 {failed}"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())