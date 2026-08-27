# QuestDB 数据补充任务书（ETF 除权/分红 + 股票分红全历史）

> 背景：QuantStudio 本地回测引擎 raw 撮合下，ETF 除权日送股缺失（stock_dividend 无 ETF 记录、净值虚假腰斩）。
> 修复方案：撮合引擎 preClose 反推兜底（近期必须）+ 数据侧补充（本任务）。
> 执行：Trae 在 trading-battle-back 补充本地 QuestDB → 同步推送云端 → MCP 侧登记映射。
> 日期：2026-08

## 〇、本地 QuestDB 现状核查结论（2026-08 实测）

| 表 | 行数/覆盖 | 结论 |
|---|---|---|
| ws_exdiv（分红除权，westock 源） | 2181 行 / 2181 只 / **仅 2026-03-05~07-09** / 仅现金 dividend_per_share / **0 只 ETF** | ❌ 需补充 |
| etf_adj_factor（tushare fund_adj） | 1,573,469 行 / 2227 只 / 2005-02-23~2026-08-13 | ✅ 完整（159995 2026-07-07 因子 1.0→1.9993 已确认） |
| etf_share_size（tushare fund_share） | 1,919,416 行 / 2062 只 / 2010~2026-08-13 | ✅ 完整 |
| etf_daily / etf_basic / etf_minutes | 2,436,194 行 / 2228 只 / 2010~2026-08-13；etf_basic 1635 只 | ✅ 完整 |
| ws_etf_nav（westock 基金净值） | **0 行（空表）** | ⚠️ 非必需（可选补充） |

tushare 接口实测（真实 token，2026-08）：
- `fund_div`（基金分红，[doc_id=120](https://tushare.pro/wctapi/documents/120.md)）：**存在且可用**；510500 的 div_cash（0.087/0.091/0.062/0.149）与 etf_daily preClose 差额（prev_close − preClose）逐分精确吻合。⚠️ 覆盖不全：518880、159915 有真实分红（本地 preClose 确认）但 fund_div 返回 0 条；同事件有重复行（net_ex_date 有无两行）。
- `dividend`（股票分红送转，[doc_id=103](https://tushare.pro/document/2?doc_id=103)）：**存在且可用**，字段全（stk_div/stk_bo_rate/stk_co_rate/cash_div/cash_div_tax/record_date/ex_date/pay_date/ann_date/div_proc），纯 A 股（ETF 代码返回 0 行），支持按 ts_code 拉全历史（实测 600000.SH 76 行）。
- 份额折算/拆分/合并：**无任何结构化接口**（fund_split/fund_convert/fund_scale 均不存在）→ 不补充，维持 fund_adj/preClose 信号 + 撮合引擎反推吸附。

---

## 一、任务 A：新增 ETF 基金分红表 `etf_dividend`（核心）

**数据源**：tushare `fund_div`（已实测可用，基础积分接口）。

**QuestDB DDL 建议**（遵循 ws_exdiv 风格，ex_date 为时间戳 + 去重键）：

```sql
CREATE TABLE IF NOT EXISTS etf_dividend (
  ts_code STRING,        -- 基金代码，如 510500.SH
  ann_date STRING,       -- 公告日期
  imp_anndate STRING,    -- 分红实施公告日
  base_date STRING,      -- 分配收益基准日
  div_proc STRING,       -- 方案进度（预案/实施）
  record_date STRING,    -- 权益登记日
  ex_date TIMESTAMP,     -- 除息日（designated timestamp）
  pay_date STRING,       -- 派息日
  earpay_date STRING,    -- 收益支付日
  net_ex_date STRING,    -- 净值除权日
  div_cash DOUBLE,       -- 每股派息（元）★ 精确现金金额
  base_unit DOUBLE,      -- 基准基金份额（万份）
  ear_distr DOUBLE,      -- 可分配收益（元）
  ear_amount DOUBLE,     -- 收益分配金额（元）
  account_date STRING,   -- 红利再投资到账日
  base_year STRING       -- 份额基准年度
) timestamp(ex_date) PARTITION BY YEAR WAL
DEDUP UPSERT KEYS(ts_code, ex_date)
```

**拉取范围**：etf_basic（market='E'）全部场内 ETF（1635 只），全历史（2005 起）。

**拉取方式**（二选一，推荐 1）：
1. 按 ts_code 逐只（1635 次调用，每次全历史）——复用现有 `load_etf_adj_factor`/`load_etf_share_size` 的 per-code 模式；
2. 按 ex_date 循环交易日历（fund_div 实测支持 ex_date 参数批量拉，~5000 交易日）。

**处理规则**：
- 只保留 `div_proc='实施'` 且 `ex_date` 非空；
- **去重**：同事件重复行（net_ex_date 有无两行）按 (ts_code, ex_date) 去重，优先保留 div_cash 非空行；
- 落库用 QuestDB DEDUP UPSERT（幂等重推安全）。

**预期产出**：ETF 现金分红精确记录（每只 ETF 的 ex_date + div_cash），供回测引擎除息日现金入账（TD-ETF-DIV 解决）。

**已知限制（必须输出核查清单）**：fund_div 覆盖不全——518880（黄金ETF）、159915（创业板ETF）等有真实分红但 fund_div 无记录；159995/513100/515880 属份额折算（非分红，无记录属正常）。回填完成后输出"有记录/无记录"清单，无记录 ETF 的现金分红仍需 fund_adj/preClose 检测兜底。

---

## 二、任务 B：股票分红全历史回填（含送转字段）

**现状**：ws_exdiv 仅 2026 年 2181 条、仅现金字段、0 ETF；无法满足回测引擎"股票精确路径"（cash + stk）。

**数据源**：tushare `dividend`（doc_id=103），实测字段完整。

**方案（推荐方案 1，不破坏现有 ws_exdiv/云端 schema）**：

新增表 `stock_dividend_full`：

```sql
CREATE TABLE IF NOT EXISTS stock_dividend_full (
  ts_code STRING,
  end_date STRING,        -- 报告期
  ann_date STRING,        -- 公告日
  div_proc STRING,        -- 预案/实施
  stk_div DOUBLE,         -- 每股送股
  stk_bo_rate DOUBLE,     -- 每股送股比例（送）
  stk_co_rate DOUBLE,     -- 每股转增比例（转）
  cash_div DOUBLE,        -- 每股分红（税后）
  cash_div_tax DOUBLE,    -- 每股分红（税前）
  record_date STRING,     -- 权益登记日
  ex_date TIMESTAMP,      -- 除权除息日（designated timestamp）
  pay_date STRING,        -- 派息日
  div_listdate STRING,
  imp_ann_date STRING     -- 实施公告日
) timestamp(ex_date) PARTITION BY YEAR WAL
DEDUP UPSERT KEYS(ts_code, ex_date)
```

**拉取范围**：全部 A 股（stock_basic，~5400 只），全历史（上市以来，2005 起；或按回测需求 2018-01-01 起）。

**处理规则**：保留 `div_proc='实施'` 且 `ex_date` 非空；`stk_div+stk_bo_rate+stk_co_rate` 为送转精确值（QuantStudio DuckDB `stock_dividend` 表 schema 已含全部对应字段：cash_div_before_tax/cash_div_after_tax/cash_div/stk_div/stk_bo_rate/stk_co_rate/div_rat/div_proc）。

**替代方案（不推荐）**：扩展现有 ws_exdiv 加送转列——需改 westock loader + 云端 schema + MCP 映射，影响面大。

---

## 三、同步推送云端（新表登记）

1. 本地回填完成后，在 `data/sync_to_cloud/config.yaml` 的 `tables:` 增加：
   ```yaml
   - {table: etf_dividend, ts_col: ex_date, group: slow}
   - {table: stock_dividend_full, ts_col: ex_date, group: slow}
   ```
   并在 `full_sync.windows."365"` 列表追加两表（历史修订窗口）；
2. 重新运行 `gen_config.py` 生成配置；执行 `run_sync_now.py --tables etf_dividend stock_dividend_full`（或 run_sync_full_now.py）推送云端（云端 124.223.159.234:8812）；
3. 云端表登记：`docs/mcp_migration/full_table_inventory.json` 追加 `etf_dividend`（canonical=etf_dividend，A_mapped）与 `stock_dividend_full`（canonical=stock_dividend_full）；
4. MCP 侧映射（QuantStudio 配置）：etf_dividend → DuckDB 新表（供回测引擎现金入账）；stock_dividend_full → 替换/补充 stock_dividend 映射（全字段注入，含 stk_div 等）——涉及 alignment_rules/column_map，按 MCP 线2 既有模式落地。

---

## 四、验证清单（回填后必做）

1. **510500 现金分红精确核对**：etf_dividend 中 4 次事件 div_cash（0.087 / 0.091 / 0.062 / 0.149）与 etf_daily preClose 差额逐分一致；
2. **159995 份额折算确认**：fund_div 无记录（正常，折算非分红）；etf_adj_factor 2026-07-07 因子 1.0→1.9993（已有）；
3. **股票送转字段核对**：任取 2026 年送转案例（如 600000.SH 2026-07-16 现金 0.42）确认 stock_dividend_full 与 tushare 一致；抽 3~5 只历史送转（stk_div>0）确认字段非空；
4. **覆盖缺口清单**：fund_div 无记录 ETF 列表（已知 518880/159915），登记 TD-ETF-DIV 兜底；
5. **QFQ 协调**：stock_dividend_full 回填后，qfq_event_discovery 全表扫描会生成历史 trigger（B-5 baseline 冻结 2181 行）——回填与 MCP B-5/B-6 节奏协调，避免前复权因子全历史修订洪水。

---

## 六、QuantStudio 侧管线接入清单（Trae 同步推送云端后，由 QuantStudio 侧执行）

**结论：核心框架（拉取调度/水位/标准化/入库/质检主链路 + CLI/PyQt 手动增量 + 常驻增量）无需修改——管线配置驱动，新增表扩展点已文档化（mcp_adapter.py:50-52）。需要的是"注册类"改动：**

### 必须改动（按文件）

| # | 文件 | 改动 | 类型 |
|---|---|---|---|
| 1 | `quantstudio/pipeline/sources/mcp_adapter.py` | `_MCP_SUPPORTED` 加 `("etf_dividend","daily"):"etf_dividend"`、`("stock_dividend_full","daily"):"stock_dividend_full"`；`_CANONICAL_TO_QUESTDB` 加 2 行 | 注册（小） |
| 2 | `quantstudio/pipeline/sources/mcp_adapter.py` | `_fetch_small_table` 日期列候选 `("date","trade_date","cal_date")` 增加 `"ex_date"`（etf_dividend/stock_dividend_full 的时间戳列是 ex_date，不在候选里增量窗口过滤会失效、退化为全表重拉；对存量表无影响，因它们无 ex_date 列） | 通用小扩展 |
| 3 | `quantstudio/pipeline/writers.py` | 新 canonical 表 DDL（etf_dividend 全字段、stock_dividend_full 全字段，日期字符串列声明 VARCHAR）；`pk_for_dedup`/`pk_cols` 注册 `"etf_dividend": ["code","ex_date"]`、`"stock_dividend_full": ["code","ex_date"]` | 注册（小） |
| 4 | `config/collector_tasks.json` + `config/profiles/mcp_only/collector_tasks.json` | 新增 2 个 task：etf_dividend / stock_dividend_full，source=mcp，首拉 mode=full_range（start_date=2005-01-01），之后 incremental | 配置 |
| 5 | `config/alignment_rules.json` + `config/profiles/mcp_only/alignment_rules.json` | 2 张表 column_map（ts_code→code、ex_date→ex_date 毫秒、div_cash→div_cash、stk_div/stk_bo_rate/stk_co_rate/cash_div/cash_div_tax/div_proc/record_date/ann_date 等）、date_format、PK | 配置 |
| 6 | `config/sources_config.json` | authority 规则：etf_dividend 权威源=mcp；**决策：stock_dividend canonical 源从 ws_exdiv 切换到 stock_dividend_full**（全字段），ws_exdiv 任务停更 | 配置 + 决策 |
| 7 | `quantstudio/pipeline/config_lint.py` | 新表必填列检查登记（如 `"etf_dividend": ["code","ex_date"]`） | 注册（小） |
| 8 | `quantstudio/pipeline/source_capabilities.py` | 新表 (table, "daily") 登记 | 注册（小） |
| 9 | `quantstudio/gui/tabs/config_editor_tab.py` | 新表来源/标签登记（否则 PyQt 配置编辑器不显示） | 注册（小） |
| 10 | `docs/mcp_migration/full_table_inventory.json` | 登记 etf_dividend（canonical=etf_dividend）与 stock_dividend_full（canonical=stock_dividend_full） | 文档 |

### 可选项/清理项

- `mcp_adapter.py:578-583` ws_exdiv 的 div_proc 派生 hack：切换到 stock_dividend_full 后（自带 div_proc）可删除；
- `quality_audit.py`：新表默认检查即可覆盖，可选加 etf_dividend 专项（div_cash 非负、按 (code,ex_date) 去重计数）。

### 无需修改（明确列出）

- daemon 生命周期/全量增量/水位（watermark 通用自动建行）、A4 变更检测、aligner 主链路、validator、quality_audit 主链路、qfq_event_discovery（扫描 stock_dividend 逻辑不变）；
- CLI（qs-collect 等）、PyQt 手动增量、常驻增量：全部读 collector_tasks.json 驱动，新 task 登记后**重启常驻 collector 即生效**（README 已注明 etf_basic 同模式）；
- `daemon.py:560` per_stock 白名单：不需要加（MCP 源跳过 per_stock；若 stock_dividend 改走 tushare 直拉才需要）；
- MCP fetch 通用路径：云端表存在即拉（server scope=['*'] 已放行）。

### 接入前置步骤

1. `scripts/mcp_probe_tables.py` 探测云端 etf_dividend / stock_dividend_full 真实列名（Trae 推送后）；
2. 上述注册清单落地 → `ConfigLint` 通过（未探测确认的表不激活）；
3. stock_dividend 源切换与 QFQ B-5 discovery-baseline（冻结 2181 行）协调，避免历史 trigger 洪水；
4. 常驻/CLI/PyQt 增量重启验证。

---

## 五、无需补充（已确认完整）

- etf_adj_factor（fund_adj 复权因子）✓
- etf_share_size（fund_share 份额）✓
- etf_daily / etf_basic / etf_minutes ✓
- 份额折算比例：无结构化源，维持 preClose 反推 + 吸附（撮合引擎方案）
- ws_etf_nav（空表）：非必需，如需基金净值辅助检测可后续用 tushare fund_nav 补充（可选）
