# 智能问数 Agent（Text-to-SQL）

用自然语言查询业务数据仓库：**自动完成 Schema 理解 → SQL 生成 → 安全校验 → 执行自修复 → 图表与结论**，让不写 SQL 的业务方直接拿到数据答案，且每条回答都能追溯到具体 SQL。

> 当前进度：已完成业务库、评测集与 baseline NL2SQL。**执行准确率 46.2%（26 题）**，正在做语义层优化。

## 为什么值得做

业务方想问「上个月华东区 GMV 是多少」，传统路径是提需求给数据同学、等排期。难点从来不是「让模型写 SQL」，而是：

| 难点 | 本项目的解法 |
|---|---|
| 表多列多，schema 塞不进 prompt | Schema 链接（选表选列） |
| 业务词（GMV/客单价）和物理列名对不上 | 指标语义层，口径显式定义 |
| 模型会写错、会写危险 SQL | sqlglot AST 护栏 + 只读执行 + 超时 |
| 一次写不对 | 执行报错回灌，自修复（最多 2 轮） |
| 效果好不好不能靠感觉 | 150 题评测集 + 结果集比对，可回归 |

**差异化**：多数同类项目只做「让回答看起来像对的」，本项目用**执行结果集比对**判对错，指标可复现、可回归。

## 架构

```
用户提问
   │
   ├─ 意图识别 / 澄清追问 ────────────── 信息不足时反问而非硬猜
   │
   ├─ Schema 链接 ──────────────────── 从 12 张表里选相关的表和列
   │
   ├─ 指标语义层 ────────────────────── GMV / 客单价 / 净GMV 的口径定义
   │
   ├─ SQL 生成 ─────────────────────── LLM，DuckDB 方言
   │
   ├─ AST 安全校验 ──────────────────── 仅 SELECT、表白名单、强制 LIMIT
   │
   ├─ 执行 ─────────────────────────── 只读连接 + 超时中断
   │
   ├─ 失败自修复（≤2 轮）────────────── 把数据库报错回灌给模型
   │
   └─ 图表选型 + 结论摘要
```

图例：`已完成` / `开发中`

当前状态：`SQL 生成`、`执行`、`评测` 已完成；Schema 链接与语义层正在开发。

## 评测结果

### Baseline（全 schema 塞 prompt，zero-shot）

| 指标 | 数值 |
|---|---|
| 执行准确率 | **46.2%**（12/26） |
| SQL 合法率 | 96.2%（25/26） |
| 平均耗时 | 2.7s |
| P95 耗时 | 4.4s |

按题型：

| 题型 | 准确率 | 说明 |
|---|---|---|
| 单表聚合 | 8/8 = 100% | 计数、求和、极值这类直接可答 |
| 多表 JOIN | 1/6 = 17% | 品类上卷、渠道维度关联易错 |
| 时间计算 | 2/6 = 33% | 环比、跨天支付、季度口径 |
| 口径陷阱 | 1/6 = 17% | GMV 是否含退款/取消单 |

**错误归因**：14 道错题里，绝大多数不是「不会写 SQL」，而是不知道业务口径。例如模型把 GMV 写成 `order_status = 'paid'`，漏掉已发货/已完成/已退款订单；或直接不过滤状态，把取消单也算进去。这正是引入语义层的直接依据。

### 评测方法

- 26 道可执行题（另 5 道澄清/拒答题待接入），5 类题型，覆盖单表、JOIN、时间计算、口径陷阱
- 判分用**结果集比对**而非字符串相似度：
  - 行序无关、列序相关
  - 数值容差 0.01（金额 DECIMAL 量化到两位）
  - NULL 等价、行数一致
- 数据固定种子 `seed=42` 生成，任何人重跑结果一致

## 模型选型

同一套 prompt、4 道代表题实测（速度 vs 准确率）：

| 模型 / 模式 | 正确 | 平均耗时 | 最慢 | 平均输出 token |
|---|---|---|---|---|
| qwen3.8-max（开思考） | 2/4 | 37.4s | 93.0s | 1470 |
| **qwen3.8-max（关思考）** | **2/4** | **3.1s** | **5.2s** | **137** |
| qwen3.8-flash | 2/4 | 20.1s | 40.7s | 1148 |
| qwen3.7-max | 2/4 | 16.6s | 25.4s | 1144 |
| qwen3-coder-flash | 0/4 | 2.1s | 2.5s | 145 |
| qwen-max | 1/4 | 4.3s | 6.6s | 137 |

**结论**：`qwen3.8-max` + `enable_thinking=False` —— 准确率持平，速度快约 12 倍，token 省 10 倍。

附带发现：代码专精模型（qwen3-coder-flash）写 SQL 语法正确但**业务口径全错**，说明取数任务的核心不是「会写 SQL」。

## 数据模型

12 张表、约 16 万行，星型模型。金额统一 `DECIMAL(12,2)`。

维度表：`dim_date`(730) · `dim_region`(35) · `dim_customer`(3000) · `dim_category`(24) · `dim_product`(500) · `dim_channel`(5) · `dim_campaign`(14)

事实表：`fact_order`(30000) · `fact_order_item`(52600) · `fact_payment`(27925) · `fact_refund`(3045) · `fact_inventory_snapshot`(45000)

数据刻意埋了业务陷阱：跨天支付（1,707 笔）、下架商品仍在历史订单（49 个）、收货地与客户所在地不一致（15%）、活动为空（约 70%）。

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言/包管理 | Python 3.13 + uv | 依赖可复现 |
| OLAP 存储 | DuckDB 1.5 | 嵌入式、列式、单文件、零运维 |
| LLM | qwen3.8-max（OpenAI 兼容） | 中文与 SQL 能力均衡，关思考后延迟可接受 |
| SQL 解析 | sqlglot | AST 级安全校验、跨方言 |
| Web | FastAPI | 后续 API 层 |
| 评测 | 自研结果集比对 | 不依赖 LLM 裁判，客观可复现 |

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置模型
cp .env.example .env        # 填入 LLM_API_KEY
uv run python scripts/check_llm.py   # 验证连通性与可用模型

# 3. 构建数据库（幂等，固定种子）
uv run python scripts/init_db.py

# 4. 查询
uv run python scripts/query.py --tables
uv run python scripts/query.py "SELECT COUNT(*) FROM fact_order"

# 5. 评测
uv run python scripts/eval.py --self-check      # 自检标准答案
uv run python scripts/run_baseline.py           # 跑 baseline
uv run python scripts/bench_models.py           # 复现模型选型对比
```

## 目录结构

```
app/
  api/          HTTP 接口（规划中）
  core/         config.py 配置、llm.py 模型客户端
  db/           executor.py 只读执行器
  schemas/      Pydantic 模型（规划中）
  services/     nl2sql.py SQL 生成
data/
  analytics.duckdb      业务库（不进 Git）
  schema_meta.json      表/列/外键元数据
  eval/questions.jsonl  评测集
scripts/
  init_db.py        建库 + 造数 + 生成元数据
  query.py          命令行查库
  eval.py           评测（自检 + 结果集比对）
  run_baseline.py   baseline 评测
  bench_models.py   模型选型对比
  check_llm.py      连通性检查
docs/schema.md      数据字典（自动生成）
tests/              单测（规划中）
web/                前端（规划中）
```

## 路线图

- [x] 业务库（12 张表）+ 元数据
- [x] 评测集（26 道可执行题 + 5 道澄清题）+ 结果集比对评测脚本
- [x] Baseline NL2SQL，拿到 46.2% 起点数字
- [x] 模型选型定版（qwen3.8-max 关思考）
- [ ] 评测集扩充到 150 题
- [ ] Schema 链接 + 指标语义层
- [ ] SQL 护栏（AST 只读 / 表白名单 / 强制 LIMIT / 超时中断）
- [ ] 执行反馈自修复（最多 2 轮）
- [ ] 澄清与拒答能力接入评测
- [ ] 图表选型 + 结论摘要
- [ ] FastAPI 接口 + 前端页面
- [ ] 部署 + 演示视频

## 设计取舍

- **先做尺子再做模型**：评测集先于 baseline，没有起点数字的优化无法归因
- **执行结果比对优于字符串相似度**：避免「SQL 长得像但结果是错的」
- **模型定版一次**：避免因换模型导致历史指标不可比
- **一份声明生成三样东西**：一张 `TABLES` 定义同时产出 DDL、`schema_meta.json`、`docs/schema.md`，杜绝文档与库不一致
- **DuckDB 而非 MySQL**：本项目是 OLAP 分析场景；生产可替换为 MySQL/ClickHouse，仅需在执行器层做方言适配