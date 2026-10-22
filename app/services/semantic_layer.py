"""指标语义层：加载 → 检索 → 渲染成 prompt 片段。

为什么需要"检索"而不是把所有指标全塞进 prompt：
1. 成本：10 个指标全量约 1,500 token，每题都要付
2. 准确率：GMV / 净GMV / 按支付日期的GMV 三个定义相似，同时出现会互相干扰
3. 可扩展：指标会越加越多，全量注入迟早塞不下

检索算法：最长匹配优先（类似中文分词）
- 中文没有空格，`GMV` 包含在 `净GMV` 里
- 所以先按别名长度从长到短排序，长的先占位置，短的若与已占区间重叠就丢弃
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SEMANTIC_PATH = ROOT / "data" / "semantic_layer.yml"


@dataclass
class Match:
    kind: str  # "metric" | "dimension"
    name: str
    alias: str  # 实际命中的别名，便于排查误命中
    score: int  # 别名长度，越长越具体
    obj: dict


@lru_cache
def load_semantic_layer() -> dict:
    if not SEMANTIC_PATH.exists():
        raise FileNotFoundError(f"语义层配置不存在: {SEMANTIC_PATH}")
    return yaml.safe_load(SEMANTIC_PATH.read_text(encoding="utf-8"))


def _candidates(text: str, entries: list[dict]) -> list[tuple]:
    """收集所有别名的所有出现位置：(start, end, length, obj, alias)。"""
    found = []
    for obj in entries:
        for alias in obj.get("aliases", []):
            key = str(alias).lower().strip()
            if not key:
                continue
            start = 0
            while True:
                idx = text.find(key, start)
                if idx < 0:
                    break
                found.append((idx, idx + len(key), len(key), obj, str(alias)))
                start = idx + 1
    return found


def _longest_first(found: list[tuple], kind: str) -> list[Match]:
    """最长匹配优先，重叠的短别名丢弃。"""
    found.sort(key=lambda c: (-c[2], c[0]))
    taken: list[tuple[int, int]] = []
    out: list[Match] = []
    for start, end, length, obj, alias in found:
        if any(not (end <= t_start or start >= t_end) for t_start, t_end in taken):
            continue
        taken.append((start, end))
        out.append(Match(kind=kind, name=obj["name"], alias=alias, score=length, obj=obj))
    return out


def match_semantics(question: str) -> tuple[list[Match], list[Match]]:
    """问题 → (命中的指标, 命中的维度)。"""
    layer = load_semantic_layer()
    text = question.lower()

    metrics = _longest_first(_candidates(text, layer.get("metrics", [])), "metric")
    dims = _longest_first(_candidates(text, layer.get("dimensions", [])), "dimension")

    # 同一指标可能被多个别名命中，去重保留最高分
    def dedupe(items: list[Match]) -> list[Match]:
        best: dict[str, Match] = {}
        for m in items:
            if m.name not in best or m.score > best[m.name].score:
                best[m.name] = m
        return sorted(best.values(), key=lambda m: -m.score)

    metrics = dedupe(metrics)
    dims = dedupe(dims)

    # 补齐依赖：客单价依赖 GMV 的定义（过滤条件），否则模型不知道 GMV 怎么算
    by_name = {m["name"]: m for m in layer.get("metrics", [])}
    matched_names = {m.name for m in metrics}
    for m in list(metrics):
        for dep in m.obj.get("depends_on", []) or []:
            if dep in by_name and dep not in matched_names:
                metrics.append(Match("metric", dep, "(依赖)", 0, by_name[dep]))
                matched_names.add(dep)

    return metrics, dims


PRODUCT_LEVEL_DIMENSIONS = {"品类", "品牌", "商品"}


def _apply_dimension_rules(
    metrics: list[Match], dims: list[Match], layer: dict
) -> tuple[list[Match], list[str]]:
    """口径不只取决于指标名，还取决于聚合维度。

    例：问"各品类的销售额"时，不能对订单金额求和（一个订单含多个商品会重复计），
    必须走 fact_order_item 明细口径，即"品类销售额"指标。
    """
    applied: list[str] = []
    by_name = {m["name"]: m for m in layer.get("metrics", [])}
    dim_names = {d.name for d in dims}
    metric_names = {m.name for m in metrics}

    if (
        dim_names & PRODUCT_LEVEL_DIMENSIONS
        and "GMV" in metric_names
        and "品类销售额" in by_name
    ):
        metrics = [m for m in metrics if m.name != "GMV"]
        metrics.append(Match("metric", "品类销售额", "(维度规则)", 0, by_name["品类销售额"]))
        applied.append("按商品/品类/品牌拆分金额时，改用明细口径 item_amount，不能对订单金额求和")

    return metrics, applied


def _render_metric(m: Match) -> str:
    obj = m.obj
    head = f"- {obj['name']}"
    if m.alias not in ("", "(依赖)", "(维度规则)") and m.alias != obj["name"]:
        head += f"（问题里出现的是「{m.alias}」）"
    lines = [head]
    lines.append(f"  计算：{obj['expression']}")
    if obj.get("filters"):
        lines.append(f"  过滤：{obj['filters']}")
    if obj.get("time_field"):
        lines.append(f"  时间字段：{obj['time_field']}")
    if obj.get("note"):
        lines.append(f"  注意：{obj['note']}")
    return "\n".join(lines)


def render_semantic_context(question: str) -> tuple[str, dict]:
    """渲染成可直接拼进 prompt 的文本，并返回命中信息（便于调试）。"""
    layer = load_semantic_layer()
    metrics, dims = match_semantics(question)
    metrics, applied_rules = _apply_dimension_rules(metrics, dims, layer)

    parts: list[str] = ["【业务口径（必须遵守）】"]

    rules = layer.get("rules", [])
    if rules:
        parts.append("通用规则：")
        for rule in rules:
            parts.append(f"- {rule['name']}：{rule['text'].strip()}")

    if metrics:
        parts.append("\n涉及指标的统一定义（不要自行发明口径）：")
        for m in metrics:
            parts.append(_render_metric(m))
    else:
        parts.append(
            "\n未命中预定义指标。请严格按表结构和通用规则自行判断；"
            "若关键口径不明确，应输出空 sql 并在 reason 中说明需要澄清什么。"
        )

    if applied_rules:
        parts.append("\n【口径修正（优先于上面的指标定义）】" + "；".join(applied_rules))

    if dims:
        parts.append("\n涉及维度的字段映射：")
        for d in dims:
            obj = d.obj
            line = f"- {obj['name']} → {obj['field']}"
            if obj.get("join"):
                line += f"（关联：{obj['join']}）"
            if obj.get("note"):
                line += f"｜注意：{obj['note'].strip()}"
            parts.append(line)

    info = {
        "metrics": [{"name": m.name, "alias": m.alias} for m in metrics],
        "dimensions": [{"name": d.name, "alias": d.alias} for d in dims],
        "rules_applied": applied_rules,
    }
    return "\n".join(parts), info