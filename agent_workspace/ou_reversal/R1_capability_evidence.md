# R1 能力核验证据 — ou_reversal_csi300_10

> 阶段：R1（Program-specific capability inspection）
> 结论：**BLOCKED**（1 项 DATA_BLOCKED + 1 项 PROFILE_REMAP_REQUIRED）
> 核验时间：2026-09-22
> 工作区：`agent_workspace/ou_reversal/`；能力报告：`capability_report.r1.json`

## 0. 本轮核验对应的策略语义（R0 定稿）

流程图（唯一事实源）：`agent_workspace/ou_reversal_r0/flowchart.html`（17 节点）
客户裁定原文：`C1-A、C2-A、C3-B、C4-A、C5-B、C6-A、C7-A、C8-A、C9-A、C10-A、C11-B、C12-A`

流水线要点：沪深300 成分 PIT 池 → 5 层过滤（上市不足100天/北交所/科创板/停牌/涨跌停/近3日跌幅>8%；60日波动率<30%；股价>2元；60日均额>1000万；个股 ADX(14)>20）→ OU 因子 (MA60−Close)/Close 降序 → 前10只等权 → 大盘择时（市场 ADX(14)>25 且 沪深300 收盘站上布林20中轨且中轨向上）→ 次日开盘建仓/调仓/清仓。

## 1. 本地数据源解析（R1 首条硬规则）

| 项 | 值 |
| --- | --- |
| 选中路径 | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db` |
| 解析理由 | 项目本地库存在且可只读打开 → 按 R1 规则首选；**未**使用外部库/覆盖 |
| 体积/表数 | 38.08 GB / 101 表（`probe_db_access.py`） |
| 读取模式 | `duckdb.connect(..., read_only=True)`（不写生产库） |

对照候选（未选用，仅记录）：`agent_workspace/backtest_readonly/quantstudio.db`（35.31 GB，2026-09-12 备份硬链接）、`data/quantstudio_backup_20260912.db`。

## 2. 行情数据覆盖（`probe_cov3.py` / `probe_coverage.py` / `probe_gap.py`）

| 表/列 | 覆盖 | 判定 |
| --- | --- | --- |
| `stock_daily` | 2018-01-01 .. 2026-09-20，9,775,971 行，5,811 代码，2,117 交易日 | READY |
| `stock_daily.close_front` 非空 | 2024/2025/2026 均 100%（1,293,797 / 1,313,806 / 962,904 行全非空） | READY（`fq='pre'` 可用） |
| 窗口内 `amount>0` / `volume>0` | 2,198,956 / 2,199,013 行；`suspendFlag=1` 仅 57 行 | READY |
| `index_daily` 000300 | 2018-01-01 .. 2026-09-20，2,116 行；窗口内 403 日 | READY（除下条缺口） |
| `trade_calendar` | 2016-12-31 .. 2026-12-30，3,652 行，2,428 开市日；窗口内 404 开市日 | READY |

**缺口（证据 `probe_gap.py`）**：`index_daily` 缺 **2026-08-03** 一个交易日（`stock_daily` 同日有数据）。
影响：①基准净值曲线该日回落 `first_bench` 兜底（`backtest_engine.py:2303`）；②择时门在 2026-08-03 15:00 调用 `get_index_day_bar` 时最近一根为 2026-07-31（无穿越，仅 1 日陈旧）。
判定：**APPROXIMATION（1 日，需客户知悉）**。

## 3. 沪深300 PIT 成分（核心阻断项）

数据面（`probe_constituents.py`）：`index_constituents` + `index_constituents_snapshot_meta`；000300 共 48 个快照日期，其中 `status='complete'` **35 个**（n=300/expected=300）。

快照列表（complete，北京时间口径）：2018-01-31 … **2021-03-31**，**2025-07-01**、2025-08-01、2025-09-01、2025-10-09、2025-11-03、2025-12-01、2026-01-05、2026-02-02、2026-03-02、2026-04-01、2026-05-06、2026-06-01、2026-07-01、2026-07-31、2026-08-31。
即 **2021-04 至 2025-06 连续 51 个月无任何 complete 快照**。

Provider as-of 契约（`duckdb_data_access.py:1864-1898`）：只取「不晚于 as_of 的最近 complete 快照，无则空，绝不向未来 fallback」。

**实测（`probe_pit_live.py` / `probe_pit_window.py`，经真实 Provider 调用）**：
窗口 2025-01-02 .. 2026-09-01 共 404 个交易日，逐日 as-of 结果：

| 生效快照 | 交易日数 | 区间 |
| --- | --- | --- |
| **2021-03-31（陈旧 ≈4 年）** | **117** | 2025-01-02 .. 2025-06-30 |
| 2025-07-01 | 23 | 2025-07-01 .. 2025-07-31 |
| 2025-08-01 | 21 | 2025-08-01 .. 2025-08-29 |
| 2025-09-01 | 22 | 2025-09-01 .. 2025-09-30 |
| 2025-10-09 | 17 | 2025-10-09 .. 2025-10-31 |
| 2025-11-03 | 20 | 2025-11-03 .. 2025-11-28 |
| 2025-12-01 | 23 | 2025-12-01 .. 2025-12-31 |
| 2026-01-05 | 20 | 2026-01-05 .. 2026-01-30 |
| 2026-02-02 | 14 | 2026-02-02 .. 2026-02-27 |
| 2026-03-02 | 22 | 2026-03-02 .. 2026-03-31 |
| 2026-04-01 | 21 | 2026-04-01 .. 2026-04-30 |
| 2026-05-06 | 18 | 2026-05-06 .. 2026-05-29 |
| 2026-06-01 | 21 | 2026-06-01 .. 2026-06-30 |
| 2026-07-01 | 22 | 2026-07-01 .. 2026-07-30 |
| 2026-07-31 | 21 | 2026-07-31 .. 2026-08-28 |
| 2026-08-31 | 2 | 2026-08-31 .. 2026-09-01 |

附带验证（同一实测）：确定性 True（同日两次调用同结果）、跨日期结果不同（非 history-union）、无未来快照泄漏（任何查询日均未返回晚于查询日的快照）。

判定：`index_constituents_pit` 在 **2025-01-02..2025-06-30（117 日，占窗口 29.0%）为 DATA_BLOCKED**；2025-07-01 起 **READY**。

> 注：官方 `inspect_capabilities.py` 对 000300 报 `index_constituents_pit=READY`、`index_constituents_history_coverage=READY`（按其"起点/终点覆盖"口径，48 个快照），**未识别中间 51 个月空档**；本阻断项即为该隐藏缺口。

## 4. 预热窗口（`probe_st_warmup.py`）

- 机制：`get_history(count=N, frequency='1d', fq='pre', include=False)` → `get_bars_by_count` → `query_bars_by_count_batch(codes, count, _end_ms(end_date), use_qfq)`（`duckdb_provider.py:82`）——**只有上界、无下界**，可自然读到回测起始日之前的历史 K 线，无需额外预热调度。
- 实测（以 2025-07-01 生效快照的 300 只成员为样本，窗口 `< 2025-07-01`）：
  - 2025-01-02..2025-06-30 区间每只成员 K 线数 min=108 / max=118，**300/300 ≥ 61 根**；
  - 窗口起始前的全量历史：299/300 ≥ 120 根（1 只为新上市，仅 2 根）。
- 指标需求：MA60/60日波动率/60日均额需 60 根；Wilder ADX(14) 最少 28 根（14+14），稳定性建议 ≥60 根；布林20 需 20 根。
- 判定：**WARM-UP READY**（两种窗口起点的预热均满足）。

## 5. 状态类过滤能力（C11-B / 停牌 / 退市）

| 来源 | 事实 | 判定 |
| --- | --- | --- |
| `stock_daily.isST` | 窗口内 **非空行数 = 0**（全 NULL，xtquant 不可靠，仅作 HALT 列保留） | **不可用于 ST 过滤** |
| `stock_daily.is_st_reliable` | `is_st_reliable_source='namechange'` 且 True 44,818 行（1.97%）；`'none'` False 2,231,892 行 → PIT 推导链路实际生效 | READY（C11-B 数据源） |
| `stock_daily.is_delisting_risk` | True 838 行 / False 2,275,872 行 | READY |
| `get_stock_status(stocks, query_type∈{ST,HALT,DELISTING}, query_date)` | `ptrade_api.py:2400-2437`，返回 `{security: bool}`；ST = is_st_reliable OR is_delisting_risk；HALT = suspendFlag==1 OR volume==0 | READY |
| `filter_stock_by_status(stocks, filter_type, query_date)` | `ptrade_api.py:926`；签名注册表限定 **仅 before_trading_start 可调用** | 可用但受回调位置约束 |
| 上市日期 | `stock_basic.list_date` 5,239/5,239 非空；`get_stock_info(stocks, field=['listed_date'])` | READY |
| 涨跌停 | 回测上下文无公开 `check_limit`（skill 禁用）；需以 T 日 raw `preClose`/`close` 自行判定 | APPROXIMATION（设计期须记录，R4 触发 `HARDFILTER-LIMIT` WARN） |

## 6. 引擎 Profile 与时序（核心重映射项）

- 引擎支持 `match_price_mode ∈ {close, open, next_open}`（`backtest_engine.py:375`）与 `engine_profile ∈ {daily-bar-v1, minute-bar-v1, daily-open-close-proxy-v1}`（同文件 :379）。
- **skill 自 2026-08-13 起废弃 `next_open`**：
  - `SKILL.md`：「[DEPRECATED 2026-08-13: `next_open` itself is deprecated — it introduces T+1 data into the T-day time slice, violating strict PIT semantics; all strategies must use `close` mode.]」
  - `references/execution-funding-matrix.md` 顶部 DEPRECATION NOTICE 同款表述。
- **承载 T+1 开盘执行语义的合规 Profile = `daily-open-close-proxy-v1`**（`backtest_engine.py:2206-2313`）：
  - 每个交易日两次因果快照：**09:31**（OHLC 全等于当日开盘价）与 **15:00**（当日完整 OHLC）；`handle_data` 被调用两次，`before_trading_start` 一次（只见 T-1）；
  - 09:31 期间下单 → 撮合价 = 当前合成 bar 收盘价 = **当日开盘价**；15:00 下单 → 撮合价 = 当日收盘价；
  - 语义版本 `0.5.0-daily-open-close-proxy`（`:452-453`）；`match_price_mode` 须为 `close`（proxy + next_open 直接 ValueError，`:382`）；
  - 历史产物先例：`output/backtest_results/*/config.csv` 中 `...,close,0.5.0-daily-open-close-proxy,0`。
  - 设计元数据解析器已把它列为合法 profile（`design_metadata.py:42,124`）。
- `get_index_day_bar(security, count, fields)`（本地扩展，`ptrade_api.py:1882`）：profile-aware「已完成」上界——**proxy 下 09:31 不含 T、15:00 含 T**（`:1945-1965`），指数走独占 index_daily 路由、不触发 ETF 代理替换。校验器：`before_trading_start` 调用 BLOCK（`PREOPEN-INDEX-BAR`）。
- `get_history`：`include=False` 锚定 `prev_date`（`ptrade_api.py:1327-1328`）；校验器对 `include=True` 除"已确认分钟回调"外一律 BLOCK（`validate_agent_strategy.py:1135-1153`）。
- `run_daily`：需字面 `time='HH:MM'`；09:30 在 proxy/minute 下 BLOCK（`AUCTION-BAR-UNAVAILABLE`，`:1174`）；daily-bar-v1 的非 15:00 分钟时刻 BLOCK（`PROFILE-SCHEDULE-MISMATCH`，`:1177-1181`）。

**结论**：客户 C2-A「T 日收盘信号 → T+1 开盘执行」的**语义完全可实现**，但落点不是 `next_open`，而是
`engine_profile=daily-open-close-proxy-v1` + `match_price_mode=close`：**T 日 15:00 回调算信号并存入 `g`，T+1 日 09:31 回调下单，撮合价 = T+1 开盘价**。因该改动变更 R0 记录的引擎口径，须客户确认后方可进入 R2。

## 7. API 签名核验（`ptrade-api-signatures.json`，profile `ptrade-backtest-public-v1` v1.10.0 @ 2026-07-27）

| 计划使用的 API | 注册表事实 | 判定 |
| --- | --- | --- |
| `set_benchmark(sids)` | 仅 initialize 可调；`:625` bare_code 化，引擎经 `index_daily` 读基准（`duckdb_provider.py:170`） | READY |
| `set_commission(commission_ratio=, min_commission=, type=)` | allowed_keywords 恰为这三项；**0 位置参数** | READY（见 §8 成本可表达性） |
| `set_universe(security_list)` | 本地为 no-op（DuckDB 模式无需订阅，`ptrade_api.py:633`） | READY |
| `get_index_stocks(index_code, date=)` | 严格 as-of；date 用 YYYYmmdd | READY（数据面见 §3） |
| `get_history(count, frequency, field, security_list, fq, include, fill, is_dict)` | canonical `fq=['pre']`；须显式 import numpy/pandas | READY |
| `get_index_day_bar(security, count, fields)` | contexts=`quantstudio_local_backtest`，`unsupported_on_ptrade=true`；count∈[1,250]；fields 白名单 open/high/low/close/pctChg/volume/amount/trade_date | READY（本地专属；本地-only 目标不涉 PTrade 转换） |
| `get_stock_status(stocks, query_type, query_date)` | query_type∈{ST,HALT,DELISTING}；返回 `{security: bool}` | READY |
| `filter_stock_by_status(stocks, filter_type, query_date)` | 仅 before_trading_start | READY（受约束） |
| `get_stock_info(stocks, field=['listed_date'])` | 统一证券元数据层；未知证券保留空记录 | READY |
| `get_position(security)` / `get_positions(security=None)` | 返回 Position / dict | READY |
| `order_target_value / order_target / order_value / order` | 均含 `limit_price` 关键字 | READY |
| `get_open_orders(security=None)` | 回测上下文可用 | READY |
| `log.{debug,info,warning,error,critical}` | `log.warn` 被 block（平台无此方法） | READY |
| `is_trade()` | 登记于 `local_only_symbols`（本地专属扩展） | 可用，但**本设计不使用**（引擎仅在交易日回调，无需判日） |

## 8. 成本可表达性（C1-A 细化）

引擎成本模型（`backtest_engine.py:229-232`）：
- 买入：`amount·(1+commission_rate+transfer_fee_rate)`，佣金 `max(amount·rate, min_commission)`
- 卖出：`proceeds·(1−commission_rate−stamp_tax_rate(date)−transfer_fee_rate)`，印花税自 2023-08-28 起上限于 0.0005（`:829-833`）
- 默认：commission 0.00035 / min 5 元 / stamp 0.001（卖出单向，2023-08-28 后实际 0.0005）/ transfer 0.00001（双边）

策略层可调仅 `set_commission`（commission_ratio / min_commission / type）；`type='ETF'` 会**清零**印花税与过户费，非 ETF 无其他副作用；引擎层 `TradeCost` 的 stamp/transfer 无公开策略 API，runner 仅额外暴露 `--slippage`。

因此「买 0.3% / 卖 0.4%」**无法逐边精确表达**，可选映射（须客户确认）：

| 方案 | 设置 | 实际买 | 实际卖 | 单次往返 |
| --- | --- | --- | --- | --- |
| A 对齐买入 | `commission_ratio=0.003, min_commission=5` | 0.301% | 0.351% | 0.652% |
| B 对齐卖出 | `commission_ratio=0.0035, min_commission=5` | 0.351% | 0.401% | 0.752% |
| C 往返等效（推荐） | `commission_ratio=0.00324, min_commission=5` | 0.325% | 0.375% | **0.700%** |

## 9. 未使用/不适用项

- PTrade backtest/trade context：**NOT_APPLICABLE**（本地-only skill，转换由 PyQt tab / `qs-compile import` 承接）。
- SW 行业分类 / 行业指数日线（F4/F5）：本策略不需要 → 未参与判定（`inspect_capabilities` 报 BLOCKED 与本策略无关）。
- `get_etf_list_local`、ETF 相关能力：本策略为股票池，不适用。

## 10. R1 结论

| 维度 | 结果 |
| --- | --- |
| API 就绪性 | READY（计划使用的全部 API 均有注册签名与本地实现；无 MISSING_REUSABLE_API） |
| 数据可用性 | **BLOCKED**（沪深300 PIT 成分在 2025-01-02..2025-06-30 缺失；基准 1 日缺口） |
| 预热窗口 | READY（无下界 count 查询，实证 ≥108 根前序 K 线） |
| 引擎时序 | **PROFILE_REMAP_REQUIRED**（next_open 已废弃 → daily-open-close-proxy-v1） |
| 成本 | APPROXIMATION_REQUIRES_CONFIRMATION（三选一） |

**R1 出口闸门未通过 → 停留 R1，等待客户就以下三项裁定后重跑/放行。**
