# Component Plan: 恐慌抄底事件驱动逆向策略

- Strategy ID: `panic_bottom_fishing`
- Targets: quantstudio
- Universe mode: `portable_public_api`
- Backtest validation owner: `agent_managed`
- Engine profile: `daily-bar-v1` / `1d`
- Match price: `close`
- Signal cutoff: `信号：T-1、T 两日上证指数跌幅（get_index_day_bar('000001.SS', count=2, fields=['pctChg'])，T 日读数为当日已完成日线——daily-bar-v1 close 模式下 14:55≈收盘确认，R0 S1 客户确认近似）；选股/市值/流动性/状态：T-1 快照（get_fundamentals valuation date=T-1 PIT + get_index_stocks date=T-1）；执行/涨跌停过滤：T 日 raw bar（仅执行检查）。`
- Signal price adjustment: `pre` (`fq='pre'` required)
- Execution price basis: `raw_trade_price`

## Confirmed semantics

- Universe: 中证500（000905）成分股 PIT 动态池（get_index_stocks('000905') 注入当前回测日 as-of 快照，严格 PIT 无未来函数）。在此基础上：剔除 ST（isST）、停牌（suspendFlag）、流动性不足（T-1 成交额 < 3000 万）、科创板（688/689）、北交所（920/43/83/87）；保留流通市值最大的 50 只。
- Holding: 锁仓 20 交易日持有（T 日含起第 20 个交易日收盘清仓），T+1 引擎强制；买卖同日 T close 撮合，卖出所得同批可用。
- Entry rules:
  - 恐慌信号：上证指数（000001）连续两个交易日 pctChg 均 < -1.5%（两日跌幅均 > 1.5%）
  - 第二个信号日 T 为入场日：R0 确认采用 close 近似（14:55 尾盘 ≈ T 日收盘价成交）
  - 入场选股：T-1 中证500 成分 PIT → 流通市值 float_value 降序前 50（剔除后）等权满仓买入
  - 涨停无法成交（T 日 raw bar close >= high_limit）→ 跳过该标的、资金留现金，不递补
  - 入场日为 T 日收盘（close 模式），T+1 引擎强制
- Exit rules:
  - 锁仓 20 个交易日：T 日（含）起第 20 个交易日收盘全部清仓（R0 S5 确认）
  - 锁仓期内不进行任何止损操作（客户硬约束，不因浮亏提前退出）
  - 到期清仓日跌停无法卖出 → 顺延至下一可卖交易日，次日卖出（R0 S9 默认）
  - 无信号期间全程空仓（无持仓、不交易）
- Portfolio rules:
  - 初始本金 100 万，纯多头、无杠杆、不融资不做空
  - 满仓入场：目标 50 只等权（每只约 2%，runtime_total_value 派生：目标 = 组合总值/50）；实际可买 <50 时按实际数 n 等权（1/n），多余资金留现金
  - 卖先买后同批执行（close 模式即时成交，卖出所得立即可用）
  - 现金缓冲带 buffer=0.03（覆盖费用/整手取整）
  - 单票目标仓位 ≤ 2%（等权 50 只天然满足，无额外上限约束）
- Risk rules:
  - 纯多头、无杠杆、不融资、不做空
  - 无止损：锁仓期内浮亏风险完全暴露，R5 报告必须呈现锁仓期浮亏与回撤表现（客户校验重点②）
  - 涨停不买（跳过留现金）；跌停到期日不卖（顺延）
  - 严格规避未来函数：信号用 T-1、T 两日指数收盘 pctChg；选股市值/流动性/状态全部 T-1 快照；执行/涨跌停过滤用 T 日 raw bar（仅执行检查，不进信号）
  - 低频触发：2026 窗口内信号触发仅 1 次（2026-07-16/17，R1 实测）——样本有效性为校验重点①，如实呈现不做虚构

## Selected components

- Lifecycle hooks: initialize, handle_data, after_trading_end
- API groups: benchmark, universe, status_filter, fundamentals, history, portfolio, logging
- Required APIs: get_index_day_bar, set_benchmark, set_commission, set_slippage, get_index_stocks, get_fundamentals, get_history, get_stock_status, get_positions, get_position, order_target_value, order_target, log

## Hard constraints

- exclude_st (T-1 isST)
- exclude_halt (T-1 suspendFlag / 无当日 bar)
- exclude_liquidity (T-1 成交额 < 3000 万)
- exclude_star_market (688/689 科创板)
- exclude_bse (920/43/83/87 北交所)
- top50_float_value (中证500 内流通市值降序前 50)
- block_limit_up_buy (T 日涨停不买，跳过留现金，不递补)
- block_limit_down_sell (到期日跌停不卖，顺延下一可卖日)
- enforce_t1 (引擎)
- round_lot (引擎)
- halt_no_price (引擎)
- lock_20d_no_stop (锁仓 20 交易日无止损，客户硬约束)
- empty_when_no_signal (无信号全空仓)
- No direct DuckDB/provider/file access.
- Backtest dates are never hardcoded in strategy source; use the confirmed window contract.
- Agent-managed mode: R5 is executed by the agent only with the customer-confirmed backtest window.
- QuantStudio-only target: registered local APIs are allowed; PTrade portability must not be claimed.
- Every signal-price get_history/get_history_batch/get_price call uses literal fq='pre'.
- attribute_history is forbidden because its price adjustment cannot be proven.
- Raw bar/snapshot OHLC is execution-only and must not enter indicator or signal series.
- No strategy-pattern branch may be added to Compiler or templates.

## Calling-agent implementation notes

- pure helpers: _extract_history_field(history_item, field, dtype) 硬门禁（rule 17）/ _ensure_runtime_state() 幂等状态守卫 / _lock_remaining_days() 锁仓剩余交易日计数（get_trade_days 未来交易日）
- 信号读数（A-11 框架修复后）：get_index_day_bar 为 QuantStudio 本地注入 API（local_only_symbols 已登记，PTrade 转换 fail-closed BLOCK；重写映射+平台探针为后续项 D4 编号另立）；count 越界 ValueError、无数据空 DataFrame → 策略 fail-soft 不触发信号并审计行记录
- r5_deployment_invariants fail-soft 一致性（终审⑥）：入场日候选全部涨停时低 gross exposure 为合法状态（QS_REBALANCE_AUDIT note=limit_up_skip_all_low_exposure_legal），复核不判 deployment_invariant_failed
- 信号源实现（A-11）：T 日跌幅 = data['510760.SS'].close / data['510760.SS'].preclose - 1（上证综指ETF 当日快照，框架快照含 etf_daily）；T-1 跌幅 = get_history('000001.SS', 2, '1d', fields=['pctChg'], fq='pre', include=False)[-1]（指数日线，合规无循环）。R1 实测两口径 2026 年判定 0 分歧 0 误报
- 信号源指数：get_history('000001.SS', ..., fq='pre', include=False) 取 index_daily（数据层 stock→etf→index fallback 支持）；指数代码归一化为 000001.SS
- 选股：get_index_stocks('000905.SS', date=T-1) PIT 成分 → get_fundamentals(['...'], 'valuation', fields=['float_value'], date=T-1) 流通市值降序前 50（T-1 快照）
- 涨停判断：T 日 raw bar close vs high/low_limit（get_history fq 原始列或引擎注入）；get_stock_status 查 ST/HALT/DELISTING
- 锁仓计数：入场日 T 记 lock_start；get_trade_days(T, count=20) 推第 20 交易日；当日 == 到期日 → 清仓
- 禁止 set()/frozenset() 依赖哈希序决策；确定性破 tie：市值降序 → 代码升序
- 所有信号价格调用字面 fq='pre' + include=False；代码后缀统一 .SS/.SZ/.BJ
- 参数冻结声明（REQ-1）：SIGNAL_DROP=-1.5 / TOP_N=50 / LOCK_DAYS=20 / MIN_AMOUNT=3000万 / BUFFER=0.03 / COMMISSION=0.0003 / SLIPPAGE=0.001，全部来自客户提示词或 R0 确认，未做回测驱动寻优
- 成本契约：set_commission(0.0003) 双边万3最低5元 + 引擎默认印花税 + 过户费万0.1 + set_slippage(0.001)
- 低频校验（客户强制，R0 未否决）：R5 必须呈现触发次数、每次触发入场-锁仓-清仓全周期、锁仓期浮亏峰值与最大回撤；R5.5 因样本不足（2026 窗口 1 次触发）WF/MC 门控必然 INSUFFICIENT_SAMPLES——需 R2.5 客户确认 robustness_gates.enabled=false 通道
