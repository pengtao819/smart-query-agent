"""图表选型：纯规则，不调模型。

为什么不用模型：
图表类型只有五六种，规则可以做到 100% 可控、零成本、零延迟；
让模型选图属于"用大炮打蚊子"，还会引入不确定性——同一份数据两次跑出两种图。

三个容易被忽略的细节：
1. 结果里常有"排名"这种展示列，它是数字但**不是指标**，判图前要先剔掉
2. 时间列可能是数字（month_num = 1,2,3），不能当成指标
3. 分类太多时柱状图会糊成一片，要退回表格
"""

from __future__ import annotations

from dataclasses import dataclass

# 列名里出现这些词，就当作时间维度
TIME_HINTS = (
    "date", "day", "week", "month", "quarter", "year",
    "日期", "时间", "月份", "年", "季度", "周",
)

# 这些是展示用列（排序辅助），不参与选图
RANK_HINTS = ("排名", "名次", "序号", "rank", "row_num", "rn")

MAX_CATEGORIES = 30  # 分类超过这个数，柱状图就看不清了


@dataclass
class ChartSpec:
    """图表建议。type 取值：metric / line / bar / multi_line / scatter / table。"""

    type: str
    x: str | None = None
    y: str | None = None
    series: str | None = None
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "series": self.series,
            "reason": self.reason,
        }


def _is_time_column(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in TIME_HINTS)


def _is_rank_column(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in RANK_HINTS)


def _is_numeric(values: list) -> bool:
    """整列是不是数字。Decimal 的 as_tuple 用来识别它。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return False
        if not isinstance(value, (int, float)) and not hasattr(value, "as_tuple"):
            return False
    return True


def select_chart(columns: list[str], rows: list[tuple]) -> ChartSpec:
    """根据结果集的形状给出图表建议。"""
    if not rows:
        return ChartSpec("table", reason="没有数据，展示空结果")

    # 先剔掉排名这类展示列
    keep = [i for i, name in enumerate(columns) if not _is_rank_column(name)]
    cols = [columns[i] for i in keep]
    if not cols:
        return ChartSpec("table", reason="只有展示列，无可绘制的数据")

    n_rows = len(rows)
    if len(cols) == 1 and n_rows == 1:
        return ChartSpec("metric", y=cols[0], reason="单行单列，适合指标卡片")

    numeric = [_is_numeric([row[i] for row in rows]) for i in range(len(keep))]
    is_time = [_is_time_column(name) for name in cols]

    # 关键：时间列即使是数字（month_num）也算维度，不算指标
    value_idx = [i for i in range(len(cols)) if numeric[i] and not is_time[i]]
    dim_idx = [i for i in range(len(cols)) if i not in value_idx]

    # 一维 + 一指标
    if len(value_idx) == 1 and len(dim_idx) == 1:
        dim_i, val_i = dim_idx[0], value_idx[0]
        if is_time[dim_i]:
            return ChartSpec("line", x=cols[dim_i], y=cols[val_i], reason="时间 × 指标，看趋势用折线")
        if n_rows > MAX_CATEGORIES:
            return ChartSpec("table", reason=f"分类过多（{n_rows} 行），柱状会糊，退回表格")
        return ChartSpec("bar", x=cols[dim_i], y=cols[val_i], reason="分类 × 指标，比大小用柱状")

    # 时间 + 分类 + 指标：多系列折线
    if len(value_idx) == 1 and len(dim_idx) == 2:
        time_dims = [i for i in dim_idx if is_time[i]]
        if time_dims:
            x_i = time_dims[0]
            series_i = next(i for i in dim_idx if i != x_i)
            return ChartSpec(
                "multi_line",
                x=cols[x_i],
                y=cols[value_idx[0]],
                series=cols[series_i],
                reason="时间 × 分类 × 指标，多系列折线",
            )

    # 两列都是数值：看相关性
    if len(value_idx) == 2 and not dim_idx:
        return ChartSpec(
            "scatter", x=cols[value_idx[0]], y=cols[value_idx[1]], reason="两列都是数值，看相关性用散点"
        )

    return ChartSpec("table", reason="形状不适合画图，直接展示表格")