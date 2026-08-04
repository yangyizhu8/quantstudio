# 全量表采集配置任务书（CodeBuddy 执行）

> **版本**：v1（2026-08-02）
> **前置**：完整清单已落盘(docs/mcp_migration/full_table_inventory.json)，scope=['*']实测通过

---

## 0. 总览

| 类别 | 表数 | 处理方式 |
|---|---|---|
| A（映射到canonical） | 19张 | _CANONICAL_TO_QUESTDB映射 + mcp alignment映射 + 正常管线(align→validate→write) |
| B（passthrough同名） | 67张 | 直接同名建DuckDB表，不映射，不走aligner |
| 排除（测试/内部/空表） | 23张 | 不同步 |
| **总计** | **86张** | |

完整清单：`docs/mcp_migration/full_table_inventory.json`

---

## 1. 类别A：19张映射表（6张新增 + 13张已有）

### 已有（已配置的13张）
stock_daily/stock_minutes/etf_daily/etf_minutes/stock_basic/etf_basic/
stock_dividend(ws_exdiv)/balance_statement(stock_balancesheet)/
income_statement(stock_income)/cashflow_statement(stock_cashflow)/
fin_indicator(stock_fina_indicator)/stock_suspend_d/etf_adj_factor

### 新增6张（本次回填）
| DuckDB canonical | QuestDB源表 | 行数 |
|---|---|---|
| stock_daily_valuation | stock_daily_basic | 1411万 |
| index_constituents | index_weight | 24.6万 |
| sw_industry | sw_daily | 150万 |
| industry_classification | sw_classify | 7885 |
| industry_membership | sw_weight | 417万 |
| trade_calendar | trade_cal | 1.3万 |

每张表需：
1. _CANONICAL_TO_QUESTDB加映射
2. _MCP_SUPPORTED加(table,daily)
3. mcp_only/alignment_rules.json加mcp.<table>映射（需探测QuestDB源表列名→DuckDB canonical列名）
4. mcp_only/collector_tasks.json加task

### stock_daily_valuation映射（重要，需注意）
QuestDB stock_daily_basic列：ts_code/trade_date/close/turnover_rate/turnover_rate_f/volume/pe/pe_ttm/pb/ps/ps_ttm/dv_ratio/dv_ttm/total_share/float_share/free_share/total_mv/circ_mv
DuckDB stock_daily_valuation列：code/time/circ_mv/total_mv/pe_ttm/pb
映射：ts_code→code, trade_date→time, vol不涉及（估值表无OHLCV，UnitCheck不拦）
注意：stock_daily_basic的vol/amount不是OHLCV的vol/amount，是volume字段，不需要×100/×1000

### 3张停更/新建表决策（用户已确认）

**stock_float_share** → 停更。数据在stock_daily_basic(total_share/float_share列)，不建独立表。

**stock_delist** → 停更。stock_basic含delist_date/list_status，不建独立表。

**stock_namechange** → **必须补建（影响ST过滤+PTrade保真度）**。
方案A：在trading-battle-back的ETL里把akshare的`stock_info_sz_change_name`写入本地QuestDB（新建`stock_namechange`表），同步管线自动同步到云端QuestDB，MCP模式从云端拉取。
- namechange是is_st_reliable的唯一权威数据源（aligner._derive_st_status PIT JOIN）
- 缺失后果：is_st_reliable恒False → ST股不被过滤 → 策略可能买入真实ST股 → 回测偏离实盘
- 数据量小（~7445行），仅深市有（akshare限制），沪市靠is_delisting_risk兜底（已知结构性局限）
- QuestDB建表后，MCPAdapter按类别A映射（_CANONICAL_TO_QUESTDB: stock_namechange→stock_namechange同名）
- DuckDB canonical schema: code/change_date/status_after/name_before/name_after（来自alignment_rules已有schema）
- **前置依赖**：trading-battle-back先建ETL写QuestDB → 同步到云端 → 然后MCP模式才能拉取

---

## 2. 类别B：67张passthrough同名表

### 写库语义（P0，必须严格遵循）

**小表（53张，<100万行）**：
- 路径：query_snapshot全量拉取
- 写库：**CREATE OR REPLACE TABLE**（每次全量覆盖，简单可靠）
- 不走aligner/validator（无schema=无column_map/无UnitCheck）
- 不走source_watermark（全量覆盖无需增量水位）
- DuckDB表名=QuestDB表名，列名=QuestDB列名（ts_code/trade_date等原样保留）

**大表（14张，>100万行）**：
- **默认enabled=false**（首拉一亿行量级，客户按需手动启用）
- 路径：export分片（需要row_limit）
- 写库：CREATE OR REPLACE TABLE（首次全量），后续按designated timestamp增量
- designated timestamp列：每张表不同（trade_date/ann_date/timestamp等），需逐表探测

### MCPAdapter passthrough模式实现
1. fetch_table对passthrough表：直接query_snapshot(或export)取raw_df，**不做column_map/不normalize_adj_factor**
2. 返回metadata标记 `passthrough: True`
3. daemon对passthrough表：**跳过aligner/validator**，直接writer.write
4. DuckDBWriter对passthrough表：表不存在时按DataFrame dtypes自动CREATE TABLE

### 大表清单（enabled=false，14张）
cyq_chips(9000万)/stk_limit(1676万)/stk_factor_pro(1447万)/margin_secs(680万)/idx_factor_pro(613万)/margin_detail(558万)/cyq_perf(318万)/report_rc(286万)/ths_daily(260万)/ths_member(244万)/stk_auction(201万)/moneyflow_ths(198万)/etf_share_size(191万)/etf_adj_factor(155万)

注意：stock_daily/stock_minutes/etf_daily/etf_minutes/stock_daily_basic/sw_weight/sw_daily也是大表，但它们在类别A（已映射，走正常管线），不受此影响。

---

## 3. 配置改动清单

### 3.1 mcp_adapter.py
- _CANONICAL_TO_QUESTDB：加6张类别A映射
- _MCP_SUPPORTED：加6张类别A + 67张类别B（全部daily或对应频率）
- 新增passthrough检测：表名在passthrough集合里→跳过column_map/normalize
- 新增_PASSTHROUGH_TABLES集合（67张表名）

### 3.2 mcp_only/collector_tasks.json
- 加6张类别A task（source=mcp, enabled=True）
- 加53张类别B小表task（source=mcp, enabled=True, mode=full_range）
- 加14张类别B大表task（source=mcp, **enabled=False**, mode=full_range）

### 3.3 mcp_only/alignment_rules.json + 默认config/alignment_rules.json
- 类别A 6张：加mcp.<table>映射（column_map/code_format/unit_conversions）
- 类别B 67张：**不加任何映射**（passthrough无需alignment_rules）

### 3.4 daemon.py（passthrough路由）
- _execute_task里检测passthrough表：跳过aligner/validator，直接writer.write
- 或在task配置里加`"passthrough": true`标记

### 3.5 DuckDBWriter（自动建表）
- write()时如果表不存在：按DataFrame dtypes自动CREATE TABLE IF NOT EXISTS
- passthrough表用CREATE OR REPLACE TABLE（全量覆盖）

### 3.6 config_lint.py
- passthrough表跳过schema检查（无alignment_rules schema=正常）

---

## 4. 首拉体量预期

| 分类 | 表数 | 总行数 | 预期 |
|---|---|---|---|
| 小表(已启用) | 53+6=59 | <500万 | 首拉几分钟内完成 |
| 大表(已启用，类别A) | ~8 | ~7亿 | 分钟线是大头，需较长时间 |
| 大表(禁用) | 14 | ~1.5亿 | 客户手动启用时才拉 |

建议：GUI提示首次拉取预期耗时，大表task默认禁用。

---

## 5. 验收标准

1. ConfigLint通过（86张task全部不报错）
2. 类别A 6张映射表：每张取数1行→align→validate→write→不崩
3. 类别B小表抽测5张（news_sentiment/cyq_perf/top_list/limit_list_d/moneyflow_ths）：取数→直接write→DuckDB表创建→行数>0
4. 类别B大表enabled=false确认（不自动拉）
5. passthrough表DuckDB列名=QuestDB列名（原样保留ts_code/trade_date等）
6. daemon完整一轮跑通（59张enabled task不崩）
7. MCP模式下GUI任务列表显示全部enabled task

---

## 6. 不改的东西

- aligner/validator的核心行为（passthrough表跳过它们，不改变它们的逻辑）
- 回测引擎
- 默认config/（只改mcp_only/）
- 不擅自commit/push

---

## 7. 分支
feat/full-table-config（独立分支）

## 8. Routing contract addendum (2026-08-04)

- `index_constituents` uses the `index_weight` export dataset and is not in the QFQ whitelist.
- Every non-export dataset uses complete `fetch_page` pagination; the 10,000-row `query_snapshot` result is never a production completeness signal.
- Passthrough datasets still skip aligner/validator and keep source columns unchanged during full replacement. Pagination fixes completeness only; it does not change the passthrough storage contract.
