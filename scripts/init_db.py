"""初始化电商业务库：建表 -> 造数 -> 导出元数据与文档。

三个设计原则：
1. 单一事实来源：TABLES 一份声明同时生成 DDL、schema_meta.json、docs/schema.md。
2. 可复现：固定随机种子，重跑得到同一份数据，评测数字才对得上。
3. 有陷阱：退款口径、跨天支付、下架商品、活动为空，都是后面评测要考的。
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "analytics.duckdb"
META_PATH = ROOT / "data" / "schema_meta.json"
DOC_PATH = ROOT / "docs" / "schema.md"

SEED = 42
START_DATE = date(2025, 1, 1)
END_DATE = date(2026, 9, 30)
DIM_DATE_END = date(2026, 12, 31)
N_ORDERS = 30_000
N_CUSTOMERS = 3_000
N_PRODUCTS = 500

rng = random.Random(SEED)


# ---------------------------------------------------------------------------
# 1. 表结构声明：改这里就够了，DDL / 元数据 / 文档都会跟着变
# ---------------------------------------------------------------------------
def col(name: str, type_: str, desc: str, sample: bool = False) -> dict:
    return {"name": name, "type": type_, "desc": desc, "sample": sample}


TABLES: dict[str, dict] = {
    "dim_date": {
        "desc": "日期维表，一行一天。按日/月/季度/节假日聚合都从这里取。",
        "grain": "一天一行",
        "pk": "date_key",
        "columns": [
            col("date_key", "DATE", "日期主键，例如 2026-09-01"),
            col("year_num", "INTEGER", "年份"),
            col("quarter_num", "INTEGER", "季度 1-4"),
            col("month_num", "INTEGER", "月份 1-12"),
            col("day_num", "INTEGER", "几号 1-31"),
            col("week_day", "INTEGER", "星期，1=周一 7=周日"),
            col("is_weekend", "BOOLEAN", "是否周末"),
            col("is_holiday", "BOOLEAN", "是否法定节假日"),
            col("holiday_name", "VARCHAR", "节假日名称，非节假日为空", sample=True),
            col("year_month", "VARCHAR", "年月，例如 2026-09，方便按月分组", sample=True),
        ],
    },
    "dim_region": {
        "desc": "区域维表：大区-省-市三层，一行一个城市。",
        "grain": "一个城市一行",
        "pk": "region_id",
        "columns": [
            col("region_id", "INTEGER", "区域主键"),
            col("region_name", "VARCHAR", "大区：华东/华北/华南/华中/西南/东北/西北", sample=True),
            col("province", "VARCHAR", "省份", sample=True),
            col("city", "VARCHAR", "城市", sample=True),
        ],
    },
    "dim_customer": {
        "desc": "客户维表：注册信息、会员等级、所在城市。",
        "grain": "一个客户一行",
        "pk": "customer_id",
        "columns": [
            col("customer_id", "INTEGER", "客户主键"),
            col("customer_name", "VARCHAR", "客户姓名（存在重名）"),
            col("gender", "VARCHAR", "性别：男/女", sample=True),
            col("age", "INTEGER", "年龄"),
            col("register_date", "DATE", "注册日期"),
            col("city", "VARCHAR", "所在城市", sample=True),
            col("region_id", "INTEGER", "所在城市对应的区域 ID"),
            col("member_level", "VARCHAR", "会员等级：普通/银卡/金卡/钻石", sample=True),
            col("is_active", "BOOLEAN", "是否活跃"),
        ],
    },
    "dim_category": {
        "desc": "商品品类维表，两级结构：parent_id 为空表示一级品类。",
        "grain": "一个品类一行",
        "pk": "category_id",
        "columns": [
            col("category_id", "INTEGER", "品类主键"),
            col("category_name", "VARCHAR", "品类名称", sample=True),
            col("parent_id", "INTEGER", "父品类 ID，一级品类为空"),
            col("level", "INTEGER", "层级：1=一级 2=二级"),
        ],
    },
    "dim_product": {
        "desc": "商品维表：价格、成本、所属品类、上下架状态。",
        "grain": "一个 SKU 一行",
        "pk": "product_id",
        "columns": [
            col("product_id", "INTEGER", "商品主键"),
            col("product_name", "VARCHAR", "商品名称"),
            col("category_id", "INTEGER", "所属二级品类 ID"),
            col("brand", "VARCHAR", "品牌", sample=True),
            col("price", "DECIMAL(12,2)", "标价（元）"),
            col("cost", "DECIMAL(12,2)", "成本（元）"),
            col("launch_date", "DATE", "上架日期"),
            col("status", "VARCHAR", "状态：on_sale 在售 / off_shelf 已下架", sample=True),
        ],
    },
    "dim_channel": {
        "desc": "销售渠道维表：线上渠道与线下门店。",
        "grain": "一个渠道一行",
        "pk": "channel_id",
        "columns": [
            col("channel_id", "INTEGER", "渠道主键"),
            col("channel_name", "VARCHAR", "渠道名称", sample=True),
            col("channel_type", "VARCHAR", "渠道类型：线上/线下", sample=True),
        ],
    },
    "dim_campaign": {
        "desc": "营销活动维表：活动时间窗、类型、预算。",
        "grain": "一个活动一行",
        "pk": "campaign_id",
        "columns": [
            col("campaign_id", "INTEGER", "活动主键"),
            col("campaign_name", "VARCHAR", "活动名称", sample=True),
            col("campaign_type", "VARCHAR", "活动类型：满减/折扣/直播/新品", sample=True),
            col("start_date", "DATE", "活动开始日期"),
            col("end_date", "DATE", "活动结束日期"),
            col("budget", "DECIMAL(12,2)", "活动预算（元）"),
        ],
    },
    "fact_order": {
        "desc": "订单主表：一行一个订单，金额是订单级汇总。",
        "grain": "一个订单一行",
        "pk": "order_id",
        "columns": [
            col("order_id", "BIGINT", "订单主键"),
            col("customer_id", "INTEGER", "下单客户 ID"),
            col("region_id", "INTEGER", "收货区域 ID（可能不等于客户所在城市）"),
            col("channel_id", "INTEGER", "下单渠道 ID"),
            col("campaign_id", "INTEGER", "参与的活动 ID，未参与为空"),
            col("order_date", "DATE", "下单日期"),
            col("order_status", "VARCHAR",
                "订单状态：paid 已支付 / shipped 已发货 / completed 已完成 / refunded 已退款 / cancelled 已取消",
                sample=True),
            col("total_amount", "DECIMAL(12,2)", "订单实付金额 = 商品金额 - 优惠 + 运费"),
            col("discount_amount", "DECIMAL(12,2)", "优惠金额"),
            col("shipping_fee", "DECIMAL(12,2)", "运费"),
            col("created_at", "TIMESTAMP", "下单时间（含时分秒）"),
        ],
    },
    "fact_order_item": {
        "desc": "订单明细表：一行一个商品，一个订单可能有多行。",
        "grain": "订单 × 商品一行",
        "pk": "item_id",
        "columns": [
            col("item_id", "BIGINT", "明细主键"),
            col("order_id", "BIGINT", "所属订单 ID"),
            col("product_id", "INTEGER", "商品 ID"),
            col("quantity", "INTEGER", "购买数量"),
            col("unit_price", "DECIMAL(12,2)", "成交单价"),
            col("item_amount", "DECIMAL(12,2)", "行金额 = 数量 × 单价"),
        ],
    },
    "fact_payment": {
        "desc": "支付流水表：一个订单可能有一笔成功支付；取消订单没有支付记录。",
        "grain": "一笔支付一行",
        "pk": "payment_id",
        "columns": [
            col("payment_id", "BIGINT", "支付流水主键"),
            col("order_id", "BIGINT", "订单 ID"),
            col("payment_method", "VARCHAR", "支付方式：支付宝/微信/银行卡/其他", sample=True),
            col("paid_amount", "DECIMAL(12,2)", "实付金额"),
            col("paid_at", "TIMESTAMP", "支付时间（可能晚于下单日期）"),
            col("payment_status", "VARCHAR", "支付状态", sample=True),
        ],
    },
    "fact_refund": {
        "desc": "退款表：整单退款与部分退款都在这里。",
        "grain": "一笔退款一行",
        "pk": "refund_id",
        "columns": [
            col("refund_id", "BIGINT", "退款主键"),
            col("order_id", "BIGINT", "订单 ID"),
            col("refund_amount", "DECIMAL(12,2)", "退款金额（可能小于订单金额）"),
            col("refund_reason", "VARCHAR", "退款原因", sample=True),
            col("refund_status", "VARCHAR", "退款状态：completed/pending/rejected", sample=True),
            col("requested_at", "TIMESTAMP", "申请时间"),
            col("completed_at", "TIMESTAMP", "完成时间，未完成则为空"),
        ],
    },
    "fact_inventory_snapshot": {
        "desc": "库存快照表：每个商品每天一行，记录当日库存。",
        "grain": "商品 × 日期一行",
        "pk": ["snapshot_date", "product_id"],
        "columns": [
            col("snapshot_date", "DATE", "快照日期"),
            col("product_id", "INTEGER", "商品 ID"),
            col("stock_qty", "INTEGER", "当日库存数量"),
            col("in_transit_qty", "INTEGER", "在途库存"),
            col("safety_stock", "INTEGER", "安全库存阈值"),
        ],
    },
}

RELATIONSHIPS = [
    ("fact_order", "customer_id", "dim_customer", "customer_id"),
    ("fact_order", "region_id", "dim_region", "region_id"),
    ("fact_order", "channel_id", "dim_channel", "channel_id"),
    ("fact_order", "campaign_id", "dim_campaign", "campaign_id"),
    ("fact_order", "order_date", "dim_date", "date_key"),
    ("fact_order_item", "order_id", "fact_order", "order_id"),
    ("fact_order_item", "product_id", "dim_product", "product_id"),
    ("fact_payment", "order_id", "fact_order", "order_id"),
    ("fact_refund", "order_id", "fact_order", "order_id"),
    ("dim_product", "category_id", "dim_category", "category_id"),
    ("dim_customer", "region_id", "dim_region", "region_id"),
    ("fact_inventory_snapshot", "product_id", "dim_product", "product_id"),
    ("fact_inventory_snapshot", "snapshot_date", "dim_date", "date_key"),
]


# ---------------------------------------------------------------------------
# 2. 生成 DDL
# ---------------------------------------------------------------------------
def ddl_for(table: str, spec: dict) -> str:
    lines = [f'  {c["name"]} {c["type"]}' for c in spec["columns"]]
    pk = spec.get("pk")
    if isinstance(pk, str):
        lines.append(f"  PRIMARY KEY ({pk})")
    elif isinstance(pk, list):
        lines.append("  PRIMARY KEY (" + ", ".join(pk) + ")")
    return f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);"


# ---------------------------------------------------------------------------
# 3. 造数
# ---------------------------------------------------------------------------
HOLIDAYS: dict[date, str] = {}


def mark_holidays(start: date, end: date, name: str) -> None:
    d = start
    while d <= end:
        HOLIDAYS[d] = name
        d += timedelta(days=1)


mark_holidays(date(2025, 1, 1), date(2025, 1, 1), "元旦")
mark_holidays(date(2025, 1, 28), date(2025, 2, 4), "春节")
mark_holidays(date(2025, 4, 4), date(2025, 4, 6), "清明节")
mark_holidays(date(2025, 5, 1), date(2025, 5, 5), "劳动节")
mark_holidays(date(2025, 5, 31), date(2025, 6, 2), "端午节")
mark_holidays(date(2025, 10, 1), date(2025, 10, 8), "国庆中秋")
mark_holidays(date(2026, 1, 1), date(2026, 1, 3), "元旦")
mark_holidays(date(2026, 2, 15), date(2026, 2, 21), "春节")
mark_holidays(date(2026, 4, 4), date(2026, 4, 6), "清明节")
mark_holidays(date(2026, 5, 1), date(2026, 5, 5), "劳动节")
mark_holidays(date(2026, 6, 19), date(2026, 6, 21), "端午节")
mark_holidays(date(2026, 9, 25), date(2026, 9, 27), "中秋节")
mark_holidays(date(2026, 10, 1), date(2026, 10, 7), "国庆节")

REGIONS = [
    ("华东", "上海市", "上海"), ("华东", "江苏省", "南京"), ("华东", "江苏省", "苏州"),
    ("华东", "浙江省", "杭州"), ("华东", "浙江省", "宁波"), ("华东", "安徽省", "合肥"),
    ("华东", "福建省", "福州"), ("华东", "福建省", "厦门"), ("华东", "山东省", "济南"),
    ("华东", "山东省", "青岛"),
    ("华北", "北京市", "北京"), ("华北", "天津市", "天津"), ("华北", "河北省", "石家庄"),
    ("华北", "山西省", "太原"), ("华北", "内蒙古", "呼和浩特"),
    ("华南", "广东省", "广州"), ("华南", "广东省", "深圳"), ("华南", "广东省", "东莞"),
    ("华南", "广西", "南宁"), ("华南", "海南省", "海口"),
    ("华中", "湖北省", "武汉"), ("华中", "湖南省", "长沙"), ("华中", "河南省", "郑州"),
    ("华中", "江西省", "南昌"),
    ("西南", "四川省", "成都"), ("西南", "重庆市", "重庆"), ("西南", "云南省", "昆明"),
    ("西南", "贵州省", "贵阳"),
    ("东北", "辽宁省", "沈阳"), ("东北", "辽宁省", "大连"), ("东北", "吉林省", "长春"),
    ("东北", "黑龙江省", "哈尔滨"),
    ("西北", "陕西省", "西安"), ("西北", "甘肃省", "兰州"), ("西北", "新疆", "乌鲁木齐"),
]

TOP_CATEGORIES = ["数码", "服饰", "家居", "美妆", "食品", "母婴"]
SUB_CATEGORIES = {
    "数码": ["手机通讯", "电脑办公", "智能穿戴"],
    "服饰": ["男装", "女装", "鞋靴"],
    "家居": ["家纺", "厨具", "收纳清洁"],
    "美妆": ["护肤", "彩妆", "洗护"],
    "食品": ["休闲零食", "咖啡冲调", "粮油调味"],
    "母婴": ["纸尿裤", "奶粉辅食", "童装玩具"],
}
PRODUCT_WORDS = {
    "手机通讯": ["智能手机", "蓝牙耳机", "充电宝", "数据线"],
    "电脑办公": ["轻薄笔记本", "机械键盘", "无线鼠标", "显示器"],
    "智能穿戴": ["智能手表", "运动手环", "智能眼镜"],
    "男装": ["纯棉T恤", "休闲衬衫", "连帽卫衣", "牛仔裤"],
    "女装": ["连衣裙", "针织开衫", "阔腿裤", "半身裙"],
    "鞋靴": ["运动鞋", "帆布鞋", "短靴", "拖鞋"],
    "家纺": ["四件套", "乳胶枕", "夏凉被", "毛巾"],
    "厨具": ["不粘炒锅", "保温杯", "刀具套装", "餐盘"],
    "收纳清洁": ["收纳箱", "衣架", "拖把", "垃圾桶"],
    "护肤": ["保湿面霜", "精华液", "洁面乳", "防晒霜"],
    "彩妆": ["口红", "粉底液", "眼影盘", "眉笔"],
    "洗护": ["洗发水", "护发素", "沐浴露", "身体乳"],
    "休闲零食": ["每日坚果", "薯片", "肉脯", "果干"],
    "咖啡冲调": ["挂耳咖啡", "速溶咖啡", "茶叶", "蜂蜜"],
    "粮油调味": ["食用油", "大米", "酱油", "调味料"],
    "纸尿裤": ["纸尿裤", "拉拉裤", "湿巾"],
    "奶粉辅食": ["婴幼儿奶粉", "米粉", "果泥"],
    "童装玩具": ["儿童T恤", "积木", "毛绒玩具", "婴儿车"],
}
BRANDS = ["星辰", "云栖", "山海", "初见", "拾光", "木白", "青禾", "简物", "乐活", "南风"]
PRICE_RANGE = {
    "数码": (199, 5999), "服饰": (59, 599), "家居": (29, 399),
    "美妆": (39, 699), "食品": (19, 199), "母婴": (29, 499),
}
CHANNELS = [("APP", "线上"), ("小程序", "线上"), ("天猫", "线上"), ("京东", "线上"), ("线下门店", "线下")]

CAMPAIGNS = [
    ("2025年货节", "满减", date(2025, 1, 10), date(2025, 1, 28)),
    ("2025春季上新", "新品", date(2025, 3, 1), date(2025, 3, 20)),
    ("2025 618大促", "满减", date(2025, 5, 25), date(2025, 6, 20)),
    ("2025暑期档", "折扣", date(2025, 7, 1), date(2025, 8, 10)),
    ("2025 99划算节", "满减", date(2025, 9, 1), date(2025, 9, 12)),
    ("2025双11", "满减", date(2025, 10, 20), date(2025, 11, 15)),
    ("2025黑五", "折扣", date(2025, 11, 20), date(2025, 11, 30)),
    ("2025双12", "满减", date(2025, 12, 1), date(2025, 12, 15)),
    ("2026年货节", "满减", date(2026, 1, 8), date(2026, 1, 26)),
    ("2026 38节", "折扣", date(2026, 3, 1), date(2026, 3, 12)),
    ("2026 618大促", "满减", date(2026, 5, 25), date(2026, 6, 20)),
    ("2026暑期档", "折扣", date(2026, 7, 1), date(2026, 8, 10)),
    ("2026 99划算节", "满减", date(2026, 9, 1), date(2026, 9, 12)),
    ("直播间专场", "直播", date(2025, 8, 15), date(2026, 9, 30)),
]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超霞平刚建华文博宇轩浩然子涵欣怡雨桐梓萱俊杰思远晨曦嘉怡")

REFUND_REASONS = ["七天无理由", "质量问题", "发错货", "不喜欢", "物流破损", "价格保护"]


def weighted_choice(pairs: list[tuple]) -> object:
    values, weights = zip(*pairs)
    return rng.choices(values, weights=weights, k=1)[0]


def chinese_name() -> str:
    return rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice([1, 2])))


def build_dim_date() -> list[tuple]:
    rows = []
    d = START_DATE
    while d <= DIM_DATE_END:
        rows.append((
            d, d.year, (d.month - 1) // 3 + 1, d.month, d.day,
            d.isoweekday(), d.isoweekday() >= 6,
            d in HOLIDAYS, HOLIDAYS.get(d), f"{d.year}-{d.month:02d}",
        ))
        d += timedelta(days=1)
    return rows


def build_dim_region() -> list[tuple]:
    return [(i, name, province, city) for i, (name, province, city) in enumerate(REGIONS, start=1)]


def build_dim_customer() -> list[tuple]:
    rows = []
    for cid in range(1, N_CUSTOMERS + 1):
        region_id = rng.randint(1, len(REGIONS))
        _, province, city = REGIONS[region_id - 1]
        register = date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
        rows.append((
            cid, chinese_name(), rng.choice(["男", "女"]), rng.randint(18, 65), register,
            city, region_id,
            weighted_choice([("普通", 60), ("银卡", 25), ("金卡", 12), ("钻石", 3)]),
            rng.random() < 0.75,
        ))
    return rows


def build_dim_category() -> list[tuple]:
    rows = []
    cid = 1
    sub_id = len(TOP_CATEGORIES) + 1
    for top in TOP_CATEGORIES:
        rows.append((cid, top, None, 1))
        cid += 1
    for top in TOP_CATEGORIES:
        for sub in SUB_CATEGORIES[top]:
            rows.append((sub_id, sub, TOP_CATEGORIES.index(top) + 1, 2))
            sub_id += 1
    return rows


def build_dim_product() -> list[tuple]:
    rows = []
    sub_rows = [(i, name) for i, (_, name, parent, level) in
                enumerate(build_dim_category(), start=1) if level == 2]
    sub_to_top = {}
    for top in TOP_CATEGORIES:
        for sub in SUB_CATEGORIES[top]:
            sub_to_top[sub] = top
    for pid in range(1, N_PRODUCTS + 1):
        sub_id, sub_name = rng.choice(sub_rows)
        top = sub_to_top[sub_name]
        low, high = PRICE_RANGE[top]
        price = round(rng.uniform(low, high), 2)
        cost = round(price * rng.uniform(0.35, 0.7), 2)
        launch = date(2023, 1, 1) + timedelta(days=rng.randint(0, 1275))
        rows.append((
            pid,
            f"{rng.choice(BRANDS)}{rng.choice(PRODUCT_WORDS[sub_name])}{rng.choice(['', ' Pro', ' Max', ' 2代'])}",
            sub_id, rng.choice(BRANDS), price, cost, launch,
            "off_shelf" if rng.random() < 0.1 else "on_sale",
        ))
    return rows


def build_dim_channel() -> list[tuple]:
    return [(i, name, typ) for i, (name, typ) in enumerate(CHANNELS, start=1)]


def build_dim_campaign() -> list[tuple]:
    return [
        (i, name, typ, start, end, round(rng.uniform(50_000, 2_000_000), 2))
        for i, (name, typ, start, end) in enumerate(CAMPAIGNS, start=1)
    ]


def build_orders_and_children(
    customers: list[tuple], products: list[tuple], channels: list[tuple],
    campaigns: list[tuple], regions: list[tuple],
) -> tuple[list, list, list, list]:
    date_pool = []
    d = START_DATE
    while d <= END_DATE:
        weight = 1.0
        if d.month == 11:
            weight = 2.2
        elif d.month == 6:
            weight = 1.8
        elif d.month == 12:
            weight = 1.4
        if d.isoweekday() >= 6:
            weight *= 1.25
        if d in HOLIDAYS:
            weight *= 1.5
        date_pool.append((d, weight))
        d += timedelta(days=1)
    dates, date_weights = zip(*date_pool)

    product_ids = [p[0] for p in products]
    order_rows, item_rows, payment_rows, refund_rows = [], [], [], []
    item_id = payment_id = refund_id = 0

    for order_id in range(1, N_ORDERS + 1):
        order_date = rng.choices(dates, weights=date_weights, k=1)[0]
        customer = rng.choice(customers)
        customer_id, customer_region = customer[0], customer[6]

        channel = weighted_choice([
            (channels[0], 45), (channels[1], 25), (channels[2], 15),
            (channels[3], 10), (channels[4], 5),
        ])

        active = [c for c in campaigns if c[3] <= order_date <= c[4]]
        campaign = rng.choice(active) if active and rng.random() < 0.3 else None

        age_days = (END_DATE - order_date).days
        if age_days > 30:
            status = weighted_choice([("completed", 78), ("refunded", 8), ("shipped", 4),
                                      ("paid", 3), ("cancelled", 7)])
        else:
            status = weighted_choice([("completed", 40), ("shipped", 20), ("paid", 25),
                                      ("refunded", 5), ("cancelled", 10)])

        # 收货区域 85% 与客户所在城市一致，15% 寄到别处
        region_id = customer_region if rng.random() < 0.85 else rng.randint(1, len(regions))

        n_items = rng.choices([1, 2, 3, 4], weights=[50, 30, 15, 5], k=1)[0]
        chosen = rng.sample(product_ids, k=n_items)
        subtotal = 0.0
        base_time = datetime.combine(
            order_date, time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        )
        for pid in chosen:
            product = products[pid - 1]
            qty = rng.choices([1, 2, 3], weights=[70, 20, 10], k=1)[0]
            unit_price = float(product[4])
            amount = round(unit_price * qty, 2)
            subtotal += amount
            item_id += 1
            item_rows.append((item_id, order_id, pid, qty, unit_price, amount))

        rate = rng.uniform(0.05, 0.20) if campaign else (rng.uniform(0.01, 0.05) if rng.random() < 0.5 else 0)
        discount = round(min(subtotal * rate, subtotal * 0.5), 2)
        shipping = 0.0 if subtotal - discount >= 99 else 8.0
        total = round(subtotal - discount + shipping, 2)

        order_rows.append((
            order_id, customer_id, region_id, channel[0],
            campaign[0] if campaign else None, order_date, status,
            total, discount, shipping, base_time,
        ))

        if status == "cancelled":
            continue

        paid_at = base_time + timedelta(minutes=rng.randint(1, 180))
        payment_id += 1
        payment_rows.append((
            payment_id, order_id,
            weighted_choice([("支付宝", 50), ("微信", 40), ("银行卡", 8), ("其他", 2)]),
            total, paid_at, "success",
        ))

        if status == "refunded":
            amount = total if rng.random() < 0.85 else round(total * rng.uniform(0.2, 0.8), 2)
            requested = paid_at + timedelta(days=rng.randint(1, 20), hours=rng.randint(0, 23))
            refund_status = weighted_choice([("completed", 85), ("rejected", 8), ("pending", 7)])
            completed = requested + timedelta(days=rng.randint(1, 7)) if refund_status == "completed" else None
            refund_id += 1
            refund_rows.append((
                refund_id, order_id, amount, rng.choice(REFUND_REASONS),
                refund_status, requested, completed,
            ))
        elif status == "completed" and rng.random() < 0.03:
            requested = paid_at + timedelta(days=rng.randint(5, 60))
            refund_id += 1
            refund_rows.append((
                refund_id, order_id, round(total * rng.uniform(0.1, 0.5), 2),
                rng.choice(REFUND_REASONS), "completed",
                requested, requested + timedelta(days=rng.randint(1, 5)),
            ))

    return order_rows, item_rows, payment_rows, refund_rows


def build_inventory(products: list[tuple]) -> list[tuple]:
    rows = []
    for offset in range(89, -1, -1):
        snapshot = END_DATE - timedelta(days=offset)
        for product in products:
            stock = 0 if rng.random() < 0.05 else rng.randint(20, 500)
            rows.append((snapshot, product[0], stock, rng.randint(0, 100), rng.randint(10, 50)))
    return rows


# ---------------------------------------------------------------------------
# 4. 写入与导出
# ---------------------------------------------------------------------------
def insert_rows(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    """批量写入：DataFrame -> DuckDB，比逐行 executemany 快一到两个数量级。"""
    cols = [c["name"] for c in TABLES[table]["columns"]]
    df = pd.DataFrame(rows, columns=cols)
    con.register("_tmp_df", df)
    con.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"SELECT {', '.join(cols)} FROM _tmp_df"
    )
    con.unregister("_tmp_df")


def jsonable(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def export_meta(con: duckdb.DuckDBPyConnection, counts: dict[str, int]) -> None:
    tables_meta = []
    for table, spec in TABLES.items():
        columns = []
        for c in spec["columns"]:
            item = {
                "name": c["name"], "type": c["type"], "description": c["desc"],
            }
            if c["sample"]:
                values = con.execute(
                    f'SELECT DISTINCT {c["name"]} FROM {table} '
                    f'WHERE {c["name"]} IS NOT NULL LIMIT 12'
                ).fetchall()
                item["sample_values"] = [jsonable(v[0]) for v in values]
            columns.append(item)
        tables_meta.append({
            "name": table, "description": spec["desc"], "grain": spec["grain"],
            "row_count": counts[table], "columns": columns,
        })

    meta = {
        "database": "电商业务库",
        "tables": tables_meta,
        "relationships": [
            {"from_table": f, "from_column": fc, "to_table": t, "to_column": tc}
            for f, fc, t, tc in RELATIONSHIPS
        ],
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def export_doc(counts: dict[str, int]) -> None:
    lines = [
        "# 数据模型",
        "",
        "> 由 `scripts/init_db.py` 自动生成，请勿手改。",
        "",
        "## 表清单",
        "",
        "| 表名 | 粒度 | 行数 | 说明 |",
        "| --- | --- | ---: | --- |",
    ]
    for table, spec in TABLES.items():
        lines.append(f"| `{table}` | {spec['grain']} | {counts[table]:,} | {spec['desc']} |")
    lines.append("")
    for table, spec in TABLES.items():
        lines += [
            f"## `{table}`", "", spec["desc"], "",
            "| 字段 | 类型 | 说明 |", "| --- | --- | --- |",
        ]
        for c in spec["columns"]:
            lines.append(f"| `{c['name']}` | {c['type']} | {c['desc']} |")
        lines.append("")
    lines += ["## 表间关系", "", "| 从 | 到 |", "| --- | --- |"]
    for f, fc, t, tc in RELATIONSHIPS:
        lines.append(f"| `{f}.{fc}` | `{t}.{tc}` |")
    lines.append("")
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 连 WAL 一起清掉，避免上次中断的残留被重放
    for suffix in ("", ".wal"):
        leftover = Path(str(DB_PATH) + suffix)
        if leftover.exists():
            leftover.unlink()

    print("[1/5] 建表 ...")
    con = duckdb.connect(str(DB_PATH))
    for table, spec in TABLES.items():
        con.execute(ddl_for(table, spec))

    print("[2/5] 生成维度数据 ...")
    dim_date = build_dim_date()
    dim_region = build_dim_region()
    dim_customer = build_dim_customer()
    dim_category = build_dim_category()
    dim_product = build_dim_product()
    dim_channel = build_dim_channel()
    dim_campaign = build_dim_campaign()

    print("[3/5] 生成订单与明细 ...")
    dims = {
        "dim_date": dim_date, "dim_region": dim_region, "dim_customer": dim_customer,
        "dim_category": dim_category, "dim_product": dim_product,
        "dim_channel": dim_channel, "dim_campaign": dim_campaign,
    }
    for table, rows in dims.items():
        insert_rows(con, table, rows)

    orders, items, payments, refunds = build_orders_and_children(
        dim_customer, dim_product, dim_channel, dim_campaign, dim_region
    )
    for table, rows in [("fact_order", orders), ("fact_order_item", items),
                        ("fact_payment", payments), ("fact_refund", refunds)]:
        insert_rows(con, table, rows)

    print("[4/5] 生成库存快照 ...")
    insert_rows(con, "fact_inventory_snapshot", build_inventory(dim_product))

    print("[5/5] 校验与导出 ...")
    counts = {
        table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES
    }
    checks = {
        "缺少明细的订单": (
            "SELECT COUNT(*) FROM fact_order o LEFT JOIN "
            "(SELECT DISTINCT order_id FROM fact_order_item) i USING (order_id) "
            "WHERE i.order_id IS NULL"
        ),
        "金额不一致的订单": (
            "SELECT COUNT(*) FROM fact_order o LEFT JOIN "
            "(SELECT order_id, SUM(item_amount) AS s FROM fact_order_item GROUP BY order_id) i "
            "USING (order_id) WHERE ABS(o.total_amount - (i.s - o.discount_amount + o.shipping_fee)) > 0.01"
        ),
        "非取消订单缺支付": (
            "SELECT COUNT(*) FROM fact_order o LEFT JOIN fact_payment p USING (order_id) "
            "WHERE o.order_status <> 'cancelled' AND p.order_id IS NULL"
        ),
        "支付金额不一致": (
            "SELECT COUNT(*) FROM fact_order o JOIN fact_payment p USING (order_id) "
            "WHERE ABS(o.total_amount - p.paid_amount) > 0.01"
        ),
        "退款超过订单金额": (
            "SELECT COUNT(*) FROM fact_refund r JOIN fact_order o USING (order_id) "
            "WHERE r.refund_amount > o.total_amount + 0.01"
        ),
        "跨天支付订单": (
            "SELECT COUNT(*) FROM fact_order o JOIN fact_payment p USING (order_id) "
            "WHERE CAST(p.paid_at AS DATE) <> o.order_date"
        ),
        "下架商品仍出现在订单": (
            "SELECT COUNT(DISTINCT i.product_id) FROM fact_order_item i "
            "JOIN dim_product p USING (product_id) WHERE p.status = 'off_shelf'"
        ),
    }
    check_results = {name: con.execute(sql).fetchone()[0] for name, sql in checks.items()}

    export_meta(con, counts)
    export_doc(counts)

    print("\n表行数：")
    for table, n in counts.items():
        print(f"  {table:<26} {n:>8,}")
    print("\n一致性校验：")
    for name, value in check_results.items():
        print(f"  {name:<20} {value:>8,}")
    print(f"\n数据库: {DB_PATH}")
    print(f"元数据: {META_PATH}")
    print(f"文档:   {DOC_PATH}")
    con.close()


if __name__ == "__main__":
    main()