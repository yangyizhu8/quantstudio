# Component Plan: 沪深300均值回归超跌反弹

- Strategy ID: `ou_reversal_csi300_10`
- Targets: quantstudio
- Universe mode: `portable_public_api`
- Backtest validation owner: `agent_managed`
- Engine profile: `daily-bar-v1` / `1d`
- Match price: `open`
- Signal cutoff: `信号日 S = 执行日 D 的前一交易日。全部信号（择时门、五层过滤、OU 排序）经通用历史 API 以 include=False 读取 S 日及更早的已完成前复权日线；大盘择时门的指数读数经 get_index_day_bar 显式消费 trade_date ≤ S 的行（丢弃含 D 的末行）。**执行日 D 不参与任何信号计算。**`
- Signal price adjustment: `pre` (`fq='pre'` required)
- Execution price basis: `raw_trade_price`

## Confirmed semantics

- Universe: get_index_stocks('000300', date=S)（严格 as-of，最近不晚于 S 的 complete 快照；区间 2025-07-01..2026-09-01 全部命中真实月度快照）
- Holding: D 日开盘按目标建仓/调仓，持仓跨日至下一交易日；每个交易日 D 重算一次目标（信号来自 S=D-1）。T+1 规则由引擎强制（当日买入不可当日卖出）。
- Entry rules:
  - D 日 handle_data（唯一决策回调）：以 include=False 读取 S 日及更早前复权日线
  - 计算大盘择时门（指数行消费至 S）→ 五层过滤 → OU 因子降序取前 10（不足按实际数量）
  - 目标集合非空 → 按目标市值 order_target_value 建仓/调仓（撮合价 = D 日开盘价）
  - 输出 QS_SIGNAL（每决策日）与 QS_REBALANCE_AUDIT/QS_PORTFOLIO_AUDIT（发生下单日）
- Exit rules:
  - 择时门关闭 → 目标集合为空 → 全部卖出 order_target_value(code, 0)（清仓信号）
  - 持仓不在新目标集合 → 卖出
  - 无个股止损（C12-A）
- Portfolio rules:
  - 等权持有（C3-B）：单只目标市值 = 运行时总资产 × (1 − 0.03) ÷ N，N = 当日实际候选数（≤10）
  - 满仓运行，无固定现金比例；3% 缓冲仅覆盖费用/整手取整/价格漂移
  - 先卖后买，open 模式即时撮合（execution-funding-matrix 的 open 行：同批卖出所得可用于买入）
  - 总资金 100,000 元；不融资、不做空、无杠杆
- Risk rules:
  - 无个股止损（C12-A）
  - 拒单处理走框架默认（涨跌停/停牌/资金/整手在引擎层拦截，不强制平仓、不递补，C10-A）
  - 指数成分 PIT + ST 显式剔除（C11-B）：先取指数成分，再按 is_st_reliable 剔除

## Selected components

- Lifecycle hooks: initialize, handle_data, after_trading_end
- API groups: benchmark, commission, universe, status_filter, history, index_bar, portfolio, logging
- Required APIs: set_benchmark, set_commission, get_index_stocks, get_stock_info, get_stock_status, get_history, get_index_day_bar, get_positions, get_position, order_target_value, log.info

## Hard constraints

- exclude_st (is_st_reliable)
- listed_over_100_calendar_days
- exclude_star_market (688/689)
- exclude_bse (920/430/83x/87x)
- exclude_halt
- exclude_limit_up_down
- three_day_drop_over_8pct
- vol60_over_30pct
- price_over_2
- amount60_over_10m
- adx14_over_20
- block_limit_up_buy
- block_limit_down_sell
- enforce_t1
- round_lot
- fixed_capital_100k
- No direct DuckDB/provider/file access.
- Backtest dates are never hardcoded in strategy source; use the confirmed window contract.
- Agent-managed mode: R5 is executed by the agent only with the customer-confirmed backtest window.
- QuantStudio-only target: registered local APIs are allowed; PTrade portability must not be claimed.
- Every signal-price get_history/get_history_batch/get_price call uses literal fq='pre'.
- attribute_history is forbidden because its price adjustment cannot be proven.
- Raw bar/snapshot OHLC is execution-only and must not enter indicator or signal series.
- No strategy-pattern branch may be added to Compiler or templates.

## Calling-agent implementation notes

- 【E1 合规声明】信号取数一律 get_history(..., fq='pre', include=False)；源码零 include=True 日线调用；成交价模式显式 match_price_mode='open'（E1-1/E1-2）。本策略不使用 data[code] 任何字段（不读 close/high/low/open/volume/preclose），杜绝 raw 与前复权混基（E1-3）。
- 【专门 API 消费规则】get_index_day_bar 在 daily-bar-v1 下末行为 D 日（daily_incl_T）；本策略成交于 D 日开盘，必须丢弃 trade_date >= D 的行后消费（E1-4）；取法：count=need+1 → 过滤 trade_date < D → 断言剩余 ≥ need → 取末 need 行。
- 【ST 过滤依赖声明】ST 判定绑定 get_stock_status(query_type='ST')（数据源 stock_daily.is_st_reliable，由 stock_namechange PIT 推导，is_st_reliable_source='namechange'；窗口内 True 44,818 行实测）；stock_daily.isST 全 NULL（xtquant 不可靠）**不可用**，禁止以 isST 作 ST 判定依据。停牌经 get_stock_status(query_type='HALT')；两者均以无 query_date 形式调用 → 引擎 _prev_day_data = S 日原始快照（PIT 安全）。
- 【单回调时序面】不使用 run_daily（daily-bar-v1 下 run_daily 仅允许 time='15:00' 或不给 time，09:31 会被 PROFILE-SCHEDULE-MISMATCH BLOCK；本策略亦无 intraday 调度需求）。全部逻辑置于 handle_data 单一回调，时序依赖面最小。
- 【参数冻结声明】上市天数=100自然日 / 3日跌幅阈值=8% / 波动率阈值=30% / 年化因子=√250(ddof=1) / 价格下限=2元 / 均额下限=1000万元 / 个股 ADX 阈值=20 / 市场 ADX 阈值=25 / 布林周期=20 / MA60=60 / 持仓数=10 / 成本往返等效 0.00324 —— 全部来自客户提示词或 R0/R1/E1 裁定，未做任何寻优（R5.4 NOT_APPLICABLE）。
- 【取数批量与窗口统一】三个不同 count（2/3/60/61）统一以 count=61 一次性多代码批量取数（get_history(security_list=[...300], count=61, is_dict=True)），本地切片；字段 ['close','high','low','pctChg','money','trade_date']。字段一律经 np.asarray(...) 归一（rule 17）。
- 【审计行】每决策日输出 QS_SIGNAL date=... targets=... changed=0/1（轻量，不入正式审计对）；发生实际下单日成对输出 QS_REBALANCE_AUDIT + QS_PORTFOLIO_AUDIT（同一 rebalance_id）。
- 【无 numpy/pandas 以外依赖】仅显式 import numpy/pandas；不引用 MyTT 等本地注入指标名。
- 【R5.5 预登记】287 交易日 5 折 ≈ 57 交易日/折；样本充分性由门控判定，INSUFFICIENT_SAMPLES 如实呈现、不豁免。
