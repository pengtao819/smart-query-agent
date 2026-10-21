# 数据模型

> 由 `scripts/init_db.py` 自动生成，请勿手改。

## 表清单

| 表名 | 粒度 | 行数 | 说明 |
| --- | --- | ---: | --- |
| `dim_date` | 一天一行 | 730 | 日期维表，一行一天。按日/月/季度/节假日聚合都从这里取。 |
| `dim_region` | 一个城市一行 | 35 | 区域维表：大区-省-市三层，一行一个城市。 |
| `dim_customer` | 一个客户一行 | 3,000 | 客户维表：注册信息、会员等级、所在城市。 |
| `dim_category` | 一个品类一行 | 24 | 商品品类维表，两级结构：parent_id 为空表示一级品类。 |
| `dim_product` | 一个 SKU 一行 | 500 | 商品维表：价格、成本、所属品类、上下架状态。 |
| `dim_channel` | 一个渠道一行 | 5 | 销售渠道维表：线上渠道与线下门店。 |
| `dim_campaign` | 一个活动一行 | 14 | 营销活动维表：活动时间窗、类型、预算。 |
| `fact_order` | 一个订单一行 | 30,000 | 订单主表：一行一个订单，金额是订单级汇总。 |
| `fact_order_item` | 订单 × 商品一行 | 52,600 | 订单明细表：一行一个商品，一个订单可能有多行。 |
| `fact_payment` | 一笔支付一行 | 27,925 | 支付流水表：一个订单可能有一笔成功支付；取消订单没有支付记录。 |
| `fact_refund` | 一笔退款一行 | 3,045 | 退款表：整单退款与部分退款都在这里。 |
| `fact_inventory_snapshot` | 商品 × 日期一行 | 45,000 | 库存快照表：每个商品每天一行，记录当日库存。 |

## `dim_date`

日期维表，一行一天。按日/月/季度/节假日聚合都从这里取。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `date_key` | DATE | 日期主键，例如 2026-09-01 |
| `year_num` | INTEGER | 年份 |
| `quarter_num` | INTEGER | 季度 1-4 |
| `month_num` | INTEGER | 月份 1-12 |
| `day_num` | INTEGER | 几号 1-31 |
| `week_day` | INTEGER | 星期，1=周一 7=周日 |
| `is_weekend` | BOOLEAN | 是否周末 |
| `is_holiday` | BOOLEAN | 是否法定节假日 |
| `holiday_name` | VARCHAR | 节假日名称，非节假日为空 |
| `year_month` | VARCHAR | 年月，例如 2026-09，方便按月分组 |

## `dim_region`

区域维表：大区-省-市三层，一行一个城市。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `region_id` | INTEGER | 区域主键 |
| `region_name` | VARCHAR | 大区：华东/华北/华南/华中/西南/东北/西北 |
| `province` | VARCHAR | 省份 |
| `city` | VARCHAR | 城市 |

## `dim_customer`

客户维表：注册信息、会员等级、所在城市。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `customer_id` | INTEGER | 客户主键 |
| `customer_name` | VARCHAR | 客户姓名（存在重名） |
| `gender` | VARCHAR | 性别：男/女 |
| `age` | INTEGER | 年龄 |
| `register_date` | DATE | 注册日期 |
| `city` | VARCHAR | 所在城市 |
| `region_id` | INTEGER | 所在城市对应的区域 ID |
| `member_level` | VARCHAR | 会员等级：普通/银卡/金卡/钻石 |
| `is_active` | BOOLEAN | 是否活跃 |

## `dim_category`

商品品类维表，两级结构：parent_id 为空表示一级品类。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `category_id` | INTEGER | 品类主键 |
| `category_name` | VARCHAR | 品类名称 |
| `parent_id` | INTEGER | 父品类 ID，一级品类为空 |
| `level` | INTEGER | 层级：1=一级 2=二级 |

## `dim_product`

商品维表：价格、成本、所属品类、上下架状态。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `product_id` | INTEGER | 商品主键 |
| `product_name` | VARCHAR | 商品名称 |
| `category_id` | INTEGER | 所属二级品类 ID |
| `brand` | VARCHAR | 品牌 |
| `price` | DECIMAL(12,2) | 标价（元） |
| `cost` | DECIMAL(12,2) | 成本（元） |
| `launch_date` | DATE | 上架日期 |
| `status` | VARCHAR | 状态：on_sale 在售 / off_shelf 已下架 |

## `dim_channel`

销售渠道维表：线上渠道与线下门店。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `channel_id` | INTEGER | 渠道主键 |
| `channel_name` | VARCHAR | 渠道名称 |
| `channel_type` | VARCHAR | 渠道类型：线上/线下 |

## `dim_campaign`

营销活动维表：活动时间窗、类型、预算。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `campaign_id` | INTEGER | 活动主键 |
| `campaign_name` | VARCHAR | 活动名称 |
| `campaign_type` | VARCHAR | 活动类型：满减/折扣/直播/新品 |
| `start_date` | DATE | 活动开始日期 |
| `end_date` | DATE | 活动结束日期 |
| `budget` | DECIMAL(12,2) | 活动预算（元） |

## `fact_order`

订单主表：一行一个订单，金额是订单级汇总。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `order_id` | BIGINT | 订单主键 |
| `customer_id` | INTEGER | 下单客户 ID |
| `region_id` | INTEGER | 收货区域 ID（可能不等于客户所在城市） |
| `channel_id` | INTEGER | 下单渠道 ID |
| `campaign_id` | INTEGER | 参与的活动 ID，未参与为空 |
| `order_date` | DATE | 下单日期 |
| `order_status` | VARCHAR | 订单状态：paid 已支付 / shipped 已发货 / completed 已完成 / refunded 已退款 / cancelled 已取消 |
| `total_amount` | DECIMAL(12,2) | 订单实付金额 = 商品金额 - 优惠 + 运费 |
| `discount_amount` | DECIMAL(12,2) | 优惠金额 |
| `shipping_fee` | DECIMAL(12,2) | 运费 |
| `created_at` | TIMESTAMP | 下单时间（含时分秒） |

## `fact_order_item`

订单明细表：一行一个商品，一个订单可能有多行。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `item_id` | BIGINT | 明细主键 |
| `order_id` | BIGINT | 所属订单 ID |
| `product_id` | INTEGER | 商品 ID |
| `quantity` | INTEGER | 购买数量 |
| `unit_price` | DECIMAL(12,2) | 成交单价 |
| `item_amount` | DECIMAL(12,2) | 行金额 = 数量 × 单价 |

## `fact_payment`

支付流水表：一个订单可能有一笔成功支付；取消订单没有支付记录。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `payment_id` | BIGINT | 支付流水主键 |
| `order_id` | BIGINT | 订单 ID |
| `payment_method` | VARCHAR | 支付方式：支付宝/微信/银行卡/其他 |
| `paid_amount` | DECIMAL(12,2) | 实付金额 |
| `paid_at` | TIMESTAMP | 支付时间（可能晚于下单日期） |
| `payment_status` | VARCHAR | 支付状态 |

## `fact_refund`

退款表：整单退款与部分退款都在这里。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `refund_id` | BIGINT | 退款主键 |
| `order_id` | BIGINT | 订单 ID |
| `refund_amount` | DECIMAL(12,2) | 退款金额（可能小于订单金额） |
| `refund_reason` | VARCHAR | 退款原因 |
| `refund_status` | VARCHAR | 退款状态：completed/pending/rejected |
| `requested_at` | TIMESTAMP | 申请时间 |
| `completed_at` | TIMESTAMP | 完成时间，未完成则为空 |

## `fact_inventory_snapshot`

库存快照表：每个商品每天一行，记录当日库存。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `snapshot_date` | DATE | 快照日期 |
| `product_id` | INTEGER | 商品 ID |
| `stock_qty` | INTEGER | 当日库存数量 |
| `in_transit_qty` | INTEGER | 在途库存 |
| `safety_stock` | INTEGER | 安全库存阈值 |

## 表间关系

| 从 | 到 |
| --- | --- |
| `fact_order.customer_id` | `dim_customer.customer_id` |
| `fact_order.region_id` | `dim_region.region_id` |
| `fact_order.channel_id` | `dim_channel.channel_id` |
| `fact_order.campaign_id` | `dim_campaign.campaign_id` |
| `fact_order.order_date` | `dim_date.date_key` |
| `fact_order_item.order_id` | `fact_order.order_id` |
| `fact_order_item.product_id` | `dim_product.product_id` |
| `fact_payment.order_id` | `fact_order.order_id` |
| `fact_refund.order_id` | `fact_order.order_id` |
| `dim_product.category_id` | `dim_category.category_id` |
| `dim_customer.region_id` | `dim_region.region_id` |
| `fact_inventory_snapshot.product_id` | `dim_product.product_id` |
| `fact_inventory_snapshot.snapshot_date` | `dim_date.date_key` |
