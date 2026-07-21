# 数据管线契约（Data Pipeline Contract）

> 版本: v1.0 | 日期: 2026-07-18
> 适用对象: **数据管线维护者**（新增数据源、扩展字段、调整清洗规则的人）
> 配套: `docs/interface-contract.md`（回测层契约）

本文档定义 QuantStudio 数据管线的**核心原则与扩展规范**。任何对 adapter/aligner/validator/writers 的改动都应遵循本契约。

---

## 一、核心原则（不可违反）

### 1. 所有清洗/补算在 aligner 层统一完成
- 字段重命名、单位换算、派生计算（如市值补算）**全部在 aligner 层**
- **回测层只读现成数据，不在回测阶段临时计算**
- 各数据源（tushare/baostock/akshare/xtquant）的数据，经 aligner 统一清洗后入库，DuckDB 里的数据格式/字段/单位完全一致
- 回测的契约对象只有 DuckDB，DuckDB 里的数据从哪来、哪个源、怎么算的，回测完全不知道

### 2. 各源数据清洗成一致格式入库
- 同一张 DuckDB 表，无论数据来自哪个源，字段名/单位/语义必须一致
- 源特有的字段（如 xtquant 有但 tushare 无的）由 aligner 补 NULL 或补算
- 单位统一：股=股、元=元、时间=毫秒戳、比率=小数或%

### 3. 数据项应包含所需全部，不甩给回测临时算
- 凡是回测/策略可能用到的数据项（如 circ_mv、pe_ttm），DuckDB 表里就应该有
- 不存在"回测时用 free_share×close 现算 circ_mv"——应该在 aligner 层提前算好入库
- 新增数据需求时，优先扩展 DuckDB schema + aligner 补算，而非改回测层

---

## 二、两表两层市值口径（D10 修复后的设计）

流通市值（circ_mv）有两个层次的需求，分两张表存：

| 表 | 口径 | 颗粒度 | 字段 | 数据源 | circ_mv 计算 |
|---|---|---|---|---|---|
| `stock_float_share` | 报告期股本+报告期末市值 | 每季一条 | code/end_date/ann_date/free_share/total_share/circ_mv/total_mv | **xtquant Capital**（历史 PIT） | aligner 补算 = free_share × end_date 最近交易日 close |
| `stock_daily_valuation` | 每日估值 | 每日一条 | code/time/circ_mv/total_mv/pe_ttm/pb/turnover_rate | **tushare daily_basic** | 直接取（daily_basic 本就提供每日值） |

### 为什么分两张表
- `free_share`（流通股本）只在财报/限售解禁/增发/送转时变化（每季几次），属报告期数据
- `circ_mv`（流通市值）每天随股价变化，属交易日数据
- 把两者塞进一张表会混两种时间语义，且数据量膨胀（940万行 vs 40万行）
- 分表后：stock_float_share 按报告期 PIT（ann_date），stock_daily_valuation 按交易日 PIT（time）

---

## 三、xtquant 源的 PIT 约束（关键）

### 禁止用 get_instrument_detail 取历史数据
- `get_instrument_detail(code)` 只返回**当前最新**的 FloatVolume/TotalVolume
- 用它取历史 = "用今天的股本算过去的市值" = **未来函数**
- **xtquant 源必须用 `get_financial_data`（Capital 报表）**，按 m_timetag/m_anntime 取历史报告期值

### Capital 报表字段映射
| xtquant Capital | DuckDB stock_float_share | 说明 |
|---|---|---|
| m_timetag | end_date | 报告期末日（YYYYMMDD→ms） |
| m_anntime | ann_date | 公告日（PIT 关键） |
| circulating_capital | free_share | 流通股本（股） |
| total_capital | total_share | 总股本（股） |
| （无） | circ_mv/total_mv | aligner 用 free_share×close 补算 |

### 已下架票自动剔除
- xtquant API 对已退市/下架的票（如 002231）返回空数据
- 这从根上解决了 tushare 保留死票导致候选池污染的问题

---

## 四、aligner 补算规范（derive_fields 层）

### 触发条件
- 仅当 schema 字段存在 + df 缺失（NULL 或 ≤0）时触发
- 源已提供值时保留源值（不覆盖），仅补缺失

### 非交易日处理（merge_asof backward）
- 报告期末日（end_date）可能是周末/假期，当天无收盘价
- 用 `pd.merge_asof(direction="backward")` 取 ≤ end_date 的最近交易日 close
- 不要用"找最近交易日"的复杂逻辑，merge_asof 是标准方案

### 源值比对告警
- 若源已提供 circ_mv 且补算值与源值差异 >5%，logger.warning（不拒绝，记录归因）
- 用于发现源口径错误（如单位换算没做）

---

## 五、新增数据源/字段的扩展规范（发现即扩展）

### 新增数据源支持某表
1. adapter 实现 `fetch_table` 对应分支（返回原始字段）
2. `alignment_rules.json` → `source_mappings.<新源>.<表>` 加 column_map（字段重命名）
3. `SOURCE_CAPABILITY`（config_editor_tab.py + task_tab.py）加该源
4. 若 adapter 已预重命名字段（如 xtquant 财务表），用 `{"identity": true}` 空映射

### 新增字段到已有表
1. `alignment_rules.json` → `schemas.<表>.columns` 加字段定义（type/unit/required）
2. `writers.py` DDL 加列 + dedup键/冲突子句/upsert列（4处）
3. 若字段需补算（非源直供），aligner derive_fields 加补算逻辑
4. DuckDB 既有表需 ALTER TABLE 加列（CREATE IF NOT EXISTS 不会改已有表）

### 发现新字段立即扩展
- 不要拖。发现 xtquant/tushare 有新可用字段，立即补映射 + schema
- 避免"临时在回测层现算"的诱惑——那违背解耦原则

---

## 六、管线唯一性（所有入库路径的咽喉）

```
adapter.fetch_table → aligner.align → validator.validate → writer.write
```

- **validator.validate 是所有入库的唯一咽喉**（daemon 的 4 处 writer.write 全部在其后）
- 增量写入、常驻进程、全量拉取，全部走同一条管线
- PIT 校验（AnnDateLogic）、正值校验（PositiveNumeric）、inf 校验（InfCheck）自动覆盖所有路径

---

## 七、相关文件

| 用途 | 路径 |
|---|---|
| 字段对齐规则（schema + source_mappings） | `config/alignment_rules.json` |
| 对齐器（清洗/补算） | `quantstudio/pipeline/aligner.py` |
| 入库校验（13 条规则） | `quantstudio/pipeline/validator.py` |
| DuckDB 写入（DDL + upsert） | `quantstudio/pipeline/writers.py` |
| 采集任务配置 | `config/collector_tasks.json` |
| 数据源适配器 | `quantstudio/pipeline/sources/*.py` |
| 采集 daemon（管线编排） | `quantstudio/pipeline/daemon.py` |

---

## 变更记录

| 版本 | 日期 | 改动 |
|---|---|---|
| v1.0 | 2026-07-18 | 初版：两表两层市值口径、xtquant PIT 约束、derive_fields 补算规范、扩展规范 |
