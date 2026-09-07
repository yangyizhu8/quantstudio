# Component Plan: 低流动性溢价换手尾部极值多头

- Strategy ID: `low_turnover_tail_premium`
- Targets: quantstudio
- Universe mode: `portable_public_api`
- Backtest validation owner: `agent_managed`
- Engine profile: `daily-bar-v1` / `1d`
- Match price: `close`
- Signal cutoff: `T-1 收盘：换手/估值 get_fundamentals(valuation, date=T-1 PIT)；ROE get_fundamentals(profit_ability, ann_date<=T-1 最近披露)；Amihud get_history_batch(include=False, fq='pre', 21 根) 止于前一交易日；状态过滤 T-1 快照；执行/涨跌停过滤用 T 日 raw bar（仅执行检查，不进信号）`
- Signal price adjustment: `pre` (`fq='pre'` required)
- Execution price basis: `raw_trade_price`

## Confirmed semantics

- Universe: 全A PIT 快照（get_Ashares 注入当前回测日 as-of 快照；注：get_Ashares 默认已排除北交所，L2 剔除作双保险无害——R2.5 补充确认）。L0 剔除基础数据不完整标的；L1 剔除上市不满 60 自然日次新（listed_date=首根K线日口径，T-1 判定）；L2 剔除科创板(688/689)与北交所(920/43/83/87)；L3 剔除 ST/停牌/退市（filter_stock_by_status，T-1 快照）
- Holding: 月度调仓持有，T+1 引擎强制；买卖同日 T close 撮合，卖出所得同批可用
- Entry rules:
  - L4 流动性门槛：T-1 单日成交额 amount ≥ 100 万元（10万资金单票≈8300元，成交占比<1%，杜绝无法成交小票）
  - L5 尾部定位（核心逻辑）：T-1 换手率 turnover_ratio 全A 升序，仅取前 10% 低换手尾部；中间/高换手区间明确舍弃（非线性：仅尾部有 Alpha）。换手 NaN/缺失剔除不参与排序
  - L6 辅助因子交叉（4个交集）：PE_TTM>0 ∧ PB≤L5后候选池中位数 ∧ 最新ROE>0（profit_ability，ann_date<=T-1）∧ 流通市值 float_value 升序
  - L7 Amihud 共振：候选池内 20日均 Amihud（|r|/amount，T-1 截止）升序前 50%，最终排序断 tie（换手升序→Amihud升序→市值升序→代码升序）取前 12
  - 空池规则：L7 后候选 ≥8 只按实际数量等权（target n=实际数）；<8 只本次调仓持币不动（审计行标记），禁止放宽筛选凑数
  - 涨跌停无法成交：T 日 raw bar 预过滤——涨停（close>=high_limit）不买、跌停（close<=low_limit）不卖；未成交买单放弃不递补
- Exit rules:
  - 月度首个交易日收盘（T 日 close）重新计算名单，原持仓不在新名单即卖出
  - 跌停不下卖单，持仓保留至下一可卖调仓日；停牌/无当日 bar 不下卖单（引擎亦 no_price 拒单，双层防护）
- Portfolio rules:
  - 目标 12 只等权（约 8.33%/只）；候选 ≥8 只时按实际数 n 等权（1/n）；sizing=runtime_total_value：买入目标价值 = min(组合总值/n, 可用现金/待买空位数)，禁止硬编码金额
  - 留任持仓不动，仅换出换入（不另行 rebalance 留任仓位）
  - 卖先买后同批执行（close 模式即时成交，卖出所得立即可用）；现金缓冲带 buffer=0.03
  - 单票仓位上限 20%（客户硬约束；等权 12 只天然满足，留任漂移保护）
- Risk rules:
  - 纯多头、不加杠杆、不融资、不做空
  - 涨停不买/跌停不卖；未成交买单不递补，接受实际仓位偏离（R2.5 补充钉死）
  - 候选 <8 只 fail-soft 持币：本次调仓不交易、持仓（如有）保留、审计行标记，不强制降格买入
  - 严格规避未来函数：全部信号因子 T-1 快照（fq='pre' include=False + ann_date<=T-1 + T-1 状态/估值快照）

## Selected components

- Lifecycle hooks: initialize, before_trading_start, handle_data, after_trading_end
- API groups: benchmark, universe, status_filter, fundamentals, history, portfolio, ordering, logging
- Required APIs: set_benchmark, set_commission, set_slippage, get_Ashares, get_stock_info, filter_stock_by_status, get_fundamentals, get_fundamentals_batch, get_history_batch, get_positions, order_target_value, log

## Hard constraints

- exclude_missing_basics (L0)
- exclude_listed_lt_60d (L1, 次新,T-1判定)
- exclude_star_688_689 (L2)
- exclude_bj_920_43_83_87 (L2b, 与 get_Ashares 默认排除重复作双保险)
- exclude_st (L3)
- exclude_delisting (L3)
- min_amount_100w (L4, 先于L5)
- tail_turnover_p10 (L5, 尾部定位)
- turnover_nan_excluded (换手NaN/缺失剔除不排序)
- pe_positive_pb_le_cand_median_roe_positive (L6, 辅助因子交集,PB分母=候选池)
- amihud_resonance_p50 (L7)
- min_hold_8_or_hold_cash (空池: ≥8按实际n等权/<8持币)
- block_limit_up_buy (执行层,不递补)
- block_limit_down_sell (执行层)
- enforce_t1 (引擎)
- round_lot (引擎)
- halt_no_price (引擎)
- No direct DuckDB/provider/file access.
- Backtest dates are never hardcoded in strategy source; use the confirmed window contract.
- Agent-managed mode: R5 is executed by the agent only with the customer-confirmed backtest window.
- QuantStudio-only target: registered local APIs are allowed; PTrade portability must not be claimed.
- Every signal-price get_history/get_history_batch/get_price call uses literal fq='pre'.
- attribute_history is forbidden because its price adjustment cannot be proven.
- Raw bar/snapshot OHLC is execution-only and must not enter indicator or signal series.
- No strategy-pattern branch may be added to Compiler or templates.

## Calling-agent implementation notes

- pure helpers: _extract_history_field(history_item, field, dtype) 硬门禁（rule 17）/ _latest_by_code(df, field) ROE 最新报告期 / _amihud20(closes, amounts) / _ensure_runtime_state() 幂等状态守卫
- 字段名契约（R2.5⑤④）：valuation 返回 turnover_ratio/pe_ttm/pb_ratio/float_value/a_floats（本地适配层 query_valuation_daily_pit 实测映射）；ROE 走 profit_ability 表 roe 字段
- PB 中位数分母 = L5 尾部候选池（T-1 当期），禁止用全市场中位数
- 禁止 set()/frozenset() 依赖哈希序决策；确定性破 tie：换手升序→Amihud升序→市值升序→代码升序
- 所有信号价格调用字面 fq='pre' + include=False；代码后缀统一 .SS/.SZ
- 参数冻结声明（REQ-1）：TAIL_PCT=0.10 / MIN_AMOUNT=1e6 / LISTED_DAYS=60 / TARGET_HOLDINGS=12 / MIN_HOLD=8 / AMIHUD_DAYS=20 / AMIHUD_KEEP=0.5 / BUFFER=0.03 / MAX_SINGLE=0.20 / COMMISSION=0.0003 / SLIPPAGE=0.001，全部来自客户提示词或 R2.5 确认，未做回测驱动寻优
- 成本契约（R2.5⑧）：set_commission(0.0003) 双边万3最低5元 + 引擎默认印花税（卖出单向千1→2023-08-28后万5，日期自动切换）+ 过户费万0.1 + set_slippage(0.001)；滑点灵敏度 A/B：0.1%（主）与 0.2%（对照），同一源码 hash 驱动层注入
- 核心校验（客户强制，R2.5⑨）：分段对照研究 [0,10%) 尾部组 / [45%,55%) 中间组 / [90%,100%] 高换手组——同算法 L0-L5(对应分位段)+L6（剔除 L7 Amihud 防同源污染），独立组合回测；MC 块自助法 p 值（与 R5.5 G6 同方法）；证明仅尾部正向显著、中间无 Alpha。研究附件不进发布策略源码
- R5.5（WF 5折+MC+G1-G6）为强制门，分段研究是附件不可替代；19 个月窗口下 G4 可能 INSUFFICIENT_SAMPLES——非阻塞，如实呈现
