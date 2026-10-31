# API 公开面行为核验（T5 收口件 · 2026-10-28）

> 触发：registry「注入 API 契约」行登记的「PTrade 面 69 待逐条行为核验」缺口闭合。
> 上游：`docs/evidence/api-surface-enumeration-20261023.md`（枚举终稿 v3，机器生成，**PTrade 面 68**）。
> 基线：`origin/main = 76e9e50`（统推 #8 出仓锚）；本件为纯验证轮，**零产品改动**。
> 判据三形态：①输入形态门（先验对象形态再下结论）②单值比对（禁区间/多值混判）③判定失败分报（不自证前不作事实级断言）。

## 一、结论摘要

| 结论 | 数量 | 说明 |
| --- | --- | --- |
| ✅ 一致/兼容 | **36** | 签名逐参对得上（含默认值）或有等价实现证据 |
| ⚠️ 差异 | **16** | A/B/C/E 已裁（E 已降级）＋ **F/G/H/I（批 1）／J/K/L（批 2＋W4 核）／M/N（批 3）为 2026-10-30 新发现**（详见 §四） |
| 🔸 未取到文档 | **15** | Context7 每批 3 次配额所限，如实标注待补查（补查批 1 清 7（一致 3／差异 4）、批 2 清 4（一致 2／差异 2）、批 3 清 6（一致 4／差异 2）、批 4 清 6（全一致）、批 5 清 3（一致 2／差异 1）、批 6 清 2（差异 2）、批 7 清 1（差异 1）；另 5 条取得**存在性**证据但签名未取到；`get_current_data` 3 次配额用尽仍未命中） |
| ⚠️ 判缺 | **1** | attribute_history（聚宽名混入候选） |
| **合计** | **68** | = PTRADE 面 68 |

> **与总调度裁定数字的差异说明（如实）**：裁定令记为「22 一致／43 未取到」，本轮逐条复核实际为 **19 一致／44 未取到／4 差异／1 判缺**（合计 68）。差异源于按「已取得文档依据且逐参比对通过」严格计数：`get_CCI`（半锚定，n=14 默认值未单查）按未取到计、`get_trades`（签名一致但缓存语义待核）计入一致并单列待核项。**以本表实际计数为准**，不凑数。

## 二、逐条表（68 条）

| # | API | 本地签名（AST 七件套提取） | 结论 | 文档依据／说明 |
| --- | --- | --- | --- | --- |
| 1 | `attribute_history` | `(security, count, unit='1d', fields=None)` | 判缺 | PTrade 面无此 API（聚宽名混入候选；文档查无此名，get_history 为正主；镜像曾命中系混入特征）；68 计数暂不调整，终判随 T5 收口 |
| 2 | `cancel_order` | `(order_param)` | 一致 | cancel_order(order_param)：Order 对象或 order_id；返回 None（Context7 stock-trading.md） |
| 3 | `check_limit` | `(security, query_date=None)` | 未取到 | 文档**存在性**已证（Context7 山西.md check_strategy 示例含 `check_limit(g.security)`）⇒ **但签名未取到**，不可逐参比对 ⇒ 仍计未取到 |
| 4 | `convert_position_from_csv` | `(path)` | 一致 | convert_position_from_csv(path)：**仅回测模块可用**；返回 list[dict]，元素键 sid/amount/enable_amount/cost_basis（东莞.md）⇒ 与 set_yesterday_position 配套使用 |
| 5 | `create_dir` | `(user_path)` | 未取到 | 配额未覆盖；注意：在 DENY_REMOVE 移除面清单 |
| 6 | `current_price` | `(security)` | 未取到 | 配额未覆盖 |
| 7 | `filter_stock_by_status` | `(stocks, filter_type=None, query_date=None)` | 一致 | SHIM_CONTRACT_REGISTRY 已登记（测试断言集合相等）＋QMT 侧 QS_QMT_SEMANTIC_DIFF 语义差异行（无历史 PIT） |
| 8 | `get_CCI` | `(high, low, close, n=14)` | 一致 | get_CCI(high, low, close, n=14)：返回 cci 时间序列，适用回测/交易（Context7 `api-reference/technical-indicators.md` ＋ 社区.md 示例）⇒ 逐参一致（含 n 默认 14） |
| 9 | `get_KDJ` | `(high, low, close, n=9, m1=3, m2=3)` | 一致 | get_KDJ(high, low, close, n=9, m1=3, m2=3) 返回 k/d/j（Context7 Ptrade社区.md） |
| 10 | `get_MACD` | `(close, short=12, long=26, m=9)` | 一致 | get_MACD(close, short=12, long=26, m=9) 返回 dif/dea/macd（Context7 社区/东莞双源） |
| 11 | `get_RSI` | `(close, n=6)` | 一致 | get_RSI(close, n=6)（Context7 `api-reference/technical-indicators.md`）⇒ 逐参一致（含 n 默认 6） |
| 12 | `get_all_positions` | `()` | 差异 Q | get_all_positions()：获取全部持仓信息，**适用环境：仅交易**；Parameters N/A（Context7 山西.md）⇒ 参数面一致（双方无参）；**环境面本地更宽松**（本地回测亦可调，与差异 H 同族） |
| 13 | `get_all_trades_days` | `(date=None)` | 一致 | get_all_trades_days(date=None)：返回 date 之前全部交易日（Context7 basic-info.md，批 3 第 3 次查询顺带覆盖） |
| 14 | `get_business_type` | `()` | 未取到 | 配额未覆盖 |
| 15 | `get_cb_info` | `()` | 一致 | get_cb_info() 无参；返回 DataFrame（Context7 东莞.md 示例） |
| 16 | `get_cb_list` | `()` | 未取到 | 配额未覆盖 |
| 17 | `get_current_data` | `()` | 未取到 | **🔸 W6 单独立批：3 次 Context7 查询配额用尽仍未命中该名**（三查分别返回 is_trade/get_snapshot/order 示例、get_fundamentals、get_stock_status/get_stock_info）⇒ 按三形态「查不到如实标注」处理，**不凭记忆作答** |
| 18 | `get_current_kline_count` | `()` | 一致 | get_current_kline_count()：**无参**，取当前时间分钟 bar 数量（Context7 社区.md） |
| 19 | `get_dominant_contract` | `(contract, date=None)` | 一致 | get_dominant_contract(contract, date=None)：返回 {'合约': {'corr_month_code','trade_date','month_contract_name'}}；date 支持 datetime/'YYYY-mm-dd'/'YYYYmmdd'，默认当前日期；**无主力合约数据权限时返回空 dict**（Context7 山西.md）⇒ 参数面逐参一致 |
| 20 | `get_etf_info` | `(etf_code)` | 差异 S | get_etf_info(**etf_code**)：获取 ETF 信息，返回 dict；**适用环境：交易**（Context7 国盛.md 参数表）⇒ 参数面逐参一致（etf_code）；**环境面本地更宽松**（本地回测亦可调，与差异 H/Q 同族） |
| 21 | `get_etf_list` | `()` | 一致 | get_etf_list() 无参、返回 list[str]、异常返回空 list（Context7 东莞/山西双源） |
| 22 | `get_etf_stock_info` | `(etf_code, security)` | 未取到 | 配额未覆盖 |
| 23 | `get_etf_stock_list` | `(etf_code)` | 差异 H | get_etf_stock_list(etf_code) → list 成分股；**适用环境：仅交易**（Context7 国盛.md）⇒ 本地回测亦可用＝**环境面本地更宽松** |
| 24 | `get_frequency` | `()` | 未取到 | 配额未覆盖 |
| 25 | `get_fundamentals` | `(security, table='valuation', fields=None, date=None, start_year=None, end_year=None, report_types=None, merge_type=None, is_dataframe=False, **kwargs)` | 差异 B | 9 张表枚举；merge_type 不传＝首次发布（回测防未来）/1＝更新数据；**table 本地有默认 valuation、文档必传**；**is_dataframe 本地扩展**；date_type 文档已废弃（本地已对齐）（Context7 财务数据东莞/山西） |
| 26 | `get_history` | `(security=None, count=None, unit='1d', fields=None, frequency=None, field=None, security_list=None, fq='pre', include=False, fill='nan', is_dict=False)` | 差异 A | get_history(count, frequency="1d", field="close", security_list=None, fq=None, include=False, fill="nan", is_dict=False)；**fq 默认：本地 pre vs 文档 None**；参数别名并集（Context7 market-data.md） |
| 27 | `get_index_stocks` | `(index_code, date=None)` | 一致 | get_index_stocks(index_code, date)：date 格式 YYYYMMDD，默认当日（Context7 stock-info.md） |
| 28 | `get_industry_stocks` | `(industry_code)` | 一致 | get_industry_stocks(industry_code)：尾缀须 .XBHS；文档明写「获取当下数据，回测存在未来函数」（Context7 stock-info.md/山西） |
| 29 | `get_instruments` | `(contract)` | 差异 I | get_instruments(contract) → 合约信息（FutureParams）；**实盘模块涨跌幅平台暂无法提供**（Context7 东莞.md）⇒ 本地返回基础证券元信息、**期货不支持**（本地 docstring）＝返回面差异 |
| 30 | `get_ipo_stocks` | `()` | 一致 | get_ipo_stocks() 无参；返回 dict（key=分类市场：上证普通/上证科创板/深证普通/深证创业板/可转债，value=申购代码列表）；异常返回 {}（Context7 东莞.md） |
| 31 | `get_margin_rate` | `(transaction_code)` | 一致 | get_margin_rate("IF")：**仅回测模块可用**（东莞.md 示例）⇒ 首参为交易代码字符串，本地 (transaction_code) **位置等价**；文档另见设置侧 set_margin_rate("IF", 0.08)（不在 PTrade 面 68 名单，未核） |
| 32 | `get_market_detail` | `(finance_mic)` | 未取到 | 配额未覆盖 |
| 33 | `get_market_list` | `()` | 未取到 | 配额未覆盖 |
| 34 | `get_open_orders` | `(security=None)` | 一致 | get_open_orders(security=None)：当日未成交（Context7 stock-trading.md） |
| 35 | `get_order` | `(order_id)` | 一致 | get_order(order_id)：单个订单（Context7 stock-trading.md） |
| 36 | `get_orders` | `(security=None)` | 一致 | get_orders(security=None)：本策略当日全部订单（Context7 stock-trading.md/山西） |
| 37 | `get_position` | `(security)` | 一致 | get_position(security).amount > 0（国盛.md 双均线示例）⇒ 返回 Position 含 amount 字段；本地空仓返回 amount=0 的 Position（非 None），与示例判据一致 |
| 38 | `get_positions` | `(security=None)` | 一致 | get_positions(security) **security 可选**（可传单只/列表/不传）；适用环境 回测/交易；返回 list 持仓列表（Context7 国盛.md 参数表＋东莞.md 示例 get_positions()/('600570.SS')/(list)） |
| 39 | `get_price` | `(security, start_date=None, end_date=None, frequency='1d', fields=None, fq='pre', count=None, is_dict=False)` | 差异 J | get_price(security, **start_date 必填**, **end_date 必填**, **frequency 必填**, fields 可选)；返回 dict/OrderedDict（多股多字段返 pandas.DataFrame，Py3.5 返 Panel），异常 None（Context7 山西.md）⇒ **本地三项均可省略（放宽）＋ 本地多 fq/count/is_dict（QS 扩展，fq='pre' 为 E1 口径）**；**fq 迁移面已闭合**（P3-1 对 get_history/get_price 显式注入 fq='pre'，同 §四 A） |
| 40 | `get_reits_list` | `(date=None)` | 差异 P | get_reits_list()：获取 REITs 列表，适用研究/回测/交易；**文档 Parameters: N/A（未列参数）**（Context7 山西.md）⇒ 本地多 `date` 可选参数＝本地更宽松（**弱判据**：文档未列参数，未能逐参比对） |
| 41 | `get_research_path` | `()` | 一致 | get_research_path()：返回**云端研究根路径**（/home/fly/notebook/，策略唯一可读写目录）（Context7 社区.md）⇒ 无参一致；在 DENY_REMOVE 移除面清单 |
| 42 | `get_security_info` | `(code)` | 未取到 | 配额未覆盖 |
| 43 | `get_snapshot` | `(security, frequency='1d')` | 差异 C | get_snapshot(security) **单参**；返回 dict[str,dict]，40+ 字段，trade_status 16 种枚举（Context7 东莞.md）；**本地多 frequency 参数＝QS 扩展** |
| 44 | `get_stock_blocks` | `(stock_code)` | 差异 M | get_stock_blocks(stock_code) → dict[str:list[list[str,str]]]（键 HGT/HY/DY/ZJHHY/GN，值为板块代码+名称）；失败/已退市股返回 None；**文档明载「获取的是当下的数据，回测时注意未来函数」**（Context7 东莞.md）⇒ 参数面一致；**未来函数面本地 PIT 关键词核 = 无（本地未做 PIT 取证）** |
| 45 | `get_stock_exrights` | `(security, date=None)` | 差异 N | get_stock_exrights(**stock_code**, date=None)：date 可选（'20180228'/20180228/datetime.date），默认全部历史（Context7 东莞.md）⇒ **首参名平台为 stock_code、本地为 security**＝关键字调用不兼容（同 §四 E 族·本地命名差异） |
| 46 | `get_stock_info` | `(stocks, field=None)` | 差异 F | get_stock_info(stocks, field=None)：**field 可选**、默认只返回 stock_name；返回嵌套 dict（Context7 社区.md）⇒ **本地 field 无默认值＝必填** ⇒ 只传 stocks 时平台可用、**本地 TypeError**＝调用面差异；QMT 侧另有 D-23 日期契约修复史 |
| 47 | `get_stock_name` | `(stocks)` | 差异 G | get_stock_name(stocks) → dict[str:str]，查不到时 value=None（Context7 东莞.md）⇒ **本地以代码充当名称**（本地 docstring「DuckDB 无 name 字段」）＝返回值语义差异 |
| 48 | `get_stock_status` | `(stocks, query_type='ST', query_date=None)` | 一致 | get_stock_status(stocks, query_type='ST', query_date=None)：返回 dict，值 True/False，**未查到返回 None**；query_date 格式 YYYYmmdd（Context7 社区.md） |
| 49 | `get_trade_days` | `(start_date=None, end_date=None, count=None)` | 一致 | get_trade_days(start_date=None, end_date=None, count=None)；**start_date 与 count 互斥**；返回 numpy.ndarray（Context7 basic-info.md） |
| 50 | `get_trades` | `()` | 一致 | get_trades()：当日本策略全部成交；**同一分钟多次调用返回当分钟首次查询缓存**；一个订单号可对多笔成交（Context7 东莞.md）；缓存语义待核 |
| 51 | `get_trades_file` | `(save_path='')` | 一致 | get_trades_file(save_path='')：**仅回测模块可用**；成功返回导出路径、失败 None；csv 表头 10 字段 [order_id,trading_id,entrust_id,security_code,order_type,volume,price,total_money,trading_fee,trade_time]（Context7 东莞.md）⇒ 本地实现表头逐字一致 |
| 52 | `get_trading_day` | `(day=0)` | 差异 K | get_trading_day(**date**)：**date 格式 'YYYY-MM-DD'，返回 bool 是否交易日**；适用环境 回测/交易（Context7 国盛.md）⇒ **本地为 day 偏移量语义、参数名与语义均不同**＝调用面阻断（传日期字符串会错/报错） |
| 53 | `get_trading_day_by_date` | `(query_date, day=0)` | 未取到 | 配额未覆盖 |
| 54 | `get_trend_data` | `(date=None, stocks=None, market=None)` | 一致 | get_trend_data(date=None, stocks=None, market=None)：集中竞价期间代码数据；**不传参默认返回当日 XSHE/XSHG 全部代码**；**stocks 与 market 不能同时入参**；失败返回空 dict {}；date 格式 YYYYmmdd（Context7 社区.md）⇒ 参数面逐参一致（互斥约束本地实现核 = 未显式校验（本地宽松）） |
| 55 | `get_underlying_code` | `(symbols)` | 未取到 | 配额未覆盖 |
| 56 | `get_user_name` | `(login_account=True)` | 未取到 | 文档**存在性**已证（山西.md 示例含 `get_user_name(False)`）⇒ 签名未取到 ⇒ 仍计未取到 |
| 57 | `order` | `(security, amount, limit_price=None)` | 一致 | order(security, amount, limit_price)；返回 Order id 或 None（order_target 族文档证实 limit_price 语义） |
| 58 | `order_target` | `(security, target_amount, limit_price=None)` | 差异 E | order_target(security, **amount**, limit_price=None)；**本地参数名 target_amount**（位置调用等价、关键字调用不兼容）（Context7 stock-trading.md） |
| 59 | `order_target_value` | `(security, value, limit_price=None)` | 一致 | order_target_value(security, value, limit_price=None)；返回 id/None；取整：股票 100 股／可转债 10 张（Context7 山西/stock-trading 双源） |
| 60 | `order_value` | `(security, value, limit_price=None)` | 一致 | order_value(security, cash)（国盛.md 双均线示例：用全部现金买入）⇒ 前二参一致；本地多 limit_price 可选默认 None（更宽松） |
| 61 | `run_daily` | `(context, func, time='9:31')` | 一致 | run_daily(context, func, time="9:31")；仅 initialize 可调；累计 >5 个定时任务会被堵塞；**回测周期为日时无论设值只在 15:00 执行**（Context7 山西.md） |
| 62 | `set_benchmark` | `(sids)` | 差异 R | set_benchmark(**security**)：设置业绩比较基准（回测报告 Alpha/Beta/夏普基于此计算）；**仅 initialize() 可用**；不设置默认沪深300 `000300.SS`；返回 None（Context7 `api-reference/settings.md` 英中双源）⇒ **首参名平台 security、本地 sids**（位置调用等价、关键字调用不兼容）＝差异 E/N 族 |
| 63 | `set_commission` | `(**kwargs)` | 一致 | set_commission(commission_ratio=0.0003, min_commission=5.0, type="STOCK")；总手续费＝佣金＋经手费（万分之 0.487）；**本地 **kwargs 收同名参数、实现体处理 commission_ratio/min_commission/type ⇒ 兼容**（Context7 settings.md） |
| 64 | `set_fixed_slippage` | `(fixedslippage=0.0)` | 一致 | 东莞版 set_fixed_slippage(fixedslippage=0.0)；成交价＝委托价 ± fixedslippage/2；api-reference 版作 fixed_slippage（**文档自身不一致**，本地采东莞原版） |
| 65 | `set_parameters` | `(**kwargs)` | 一致 | set_parameters(holiday_not_do_before/tick_data_no_l2/receive_other_response/receive_cancel_response/**not_restart_trade**/**server_restart_not_do_before**)：平台为具名关键字入参（社区.md）；**山西.md 版本变动：PTrade1.0-QTV202401.01.000 新增 not_restart_trade、server_restart_not_do_before** ⇒ 本地 **kwargs 可吸收全部具名参数 ⇒ 逐参兼容（更宽松） |
| 66 | `set_slippage` | `(slippage=0.0)` | 一致 | 文档未返回；本地实现体 docstring 明写「PTrade accepts set_slippage(slippage=...) or one positional value. Local-only aliases are rejected so local success predicts platform success」⇒ 强证据对齐 |
| 67 | `set_universe` | `(security_list)` | 未取到 | 配额未覆盖 |
| 68 | `set_yesterday_position` | `(poslist)` | 一致 | set_yesterday_position(poslist)：东莞.md 示例 `poslist = convert_position_from_csv("Poslist.csv"); set_yesterday_position(poslist)` ⇒ 单参、参数名 poslist、元素含 sid/amount/enable_amount/cost_basis ⇒ 与本地逐参一致 |

## 三、方法与环境

- 本地签名：`quantstudio/backtest/ptrade_api.py` **AST 提取**（含 `posonlyargs/args/defaults/vararg/kwonlyargs/kw_defaults/kwarg` 七件套）。
- 文档依据：Context7 `/kay-ou/ptradeapi`（铁律 16），每批 3 次查询上限、单主题查询、禁含密钥。
- 入库核对：`knowledge/registry.md` 六态、`SHIM_CONTRACT_REGISTRY`（9 键）、`_QMT_API_WHITELIST`（44）、`_QMT_CONTEXT_METHODS`（6）、`INJECTED_WRAPPER_NAMES`（6）。

## 四、四处差异与裁定

### A. `get_history` 的 `fq` 默认（已裁：维持本地 + 登记已知分歧）

| 端 | 默认值 |
| --- | --- |
| 本地 | `fq='pre'`（前复权） |
| PTrade | `fq=None`（不复权） |

裁定：维持本地默认（E1 铁律「把正确口径设为默认、防漏传」）；转换管线核对项 → **已闭合**：`source_import.py:4095-4152` 有 P3-1 注入机制（`get_history/get_price` 显式补 `fq='pre'`），注释明写「PTrade 默认 fq=不复权，注入 fq='pre' 确保信号前复权一致」；`source_import_qmt.py` 另有 6 处显式。

### B. `get_fundamentals` 的 `table` 默认与 `is_dataframe`（已裁）

- `table='valuation'` 默认：**维持**（纯增益，不改变显式调用行为）。
- `is_dataframe=False`：**移 QS_ONLY 标注**（本地扩展，PTrade 面无此参）。
- `date_type`：文档已废弃，本地未实现 ⇒ **已对齐废弃态**。
- `**kwargs`：本地吸收额外具名参数（兼容层）。

### C. `get_snapshot` 的 `frequency` 参数（已裁：QS 扩展）

| 端 | 签名 |
| --- | --- |
| 本地 | `get_snapshot(security, frequency='1d')` |
| PTrade | `get_snapshot(security)` 单参 |

裁定：记 QS 扩展参数——PTrade 快照仅交易模块实时可用、回测无快照，本地为回测场景扩展；**不传参行为不变 ＝ 纯增益**。

### E. `order_target` 参数名（已裁：已知分歧·参数名）

| 端 | 签名 |
| --- | --- |
| 本地 | `order_target(security, **target_amount**, limit_price=None)` |
| PTrade | `order_target(security, **amount**, limit_price=None)` |

裁定：位置调用 `order_target(sec, 100)` 两端等价；**仅关键字调用不兼容**（本地 `target_amount=`／平台 `amount=`）。转换管线核对项：核 `source_import` 是否已有 order_target 参数名改写（P3-1 同族），无则生成 PTrade 代码时确保位置传参或改写 `amount=`。

### F. `get_stock_info` 的 `field` 是否必填（2026-10-30 新发现）

| 端 | 签名 |
| --- | --- |
| 本地 | `get_stock_info(stocks, field)`（**field 无默认值＝必填**） |
| PTrade | `get_stock_info(stocks, field=None)`（field 可选，默认只返回 stock_name） |

影响：只传 `stocks` 的调用**平台可用、本地 TypeError** ⇒ 调用面阻断级（P1）。**处置：仅登记 ISSUES，下轮统一**（B3 裁定）。

### G. `get_stock_name` 返回值语义（2026-10-30 新发现）

| 端 | 返回 |
| --- | --- |
| 本地 | **以证券代码充当名称**（本地 docstring「DuckDB 无 name 字段」） |
| PTrade | dict[str:str] 真实名称，查不到 value=None（东莞.md） |

影响：依赖名称的展示/选股逻辑语义不等价（P2）。**处置：仅登记 ISSUES，下轮统一**。

### H. `get_etf_stock_list` 适用环境（2026-10-30 新发现）

文档明载「**适用环境：交易**」（国盛.md）⇒ 本地回测亦可调用＝**环境面本地更宽松**（平台在回测中不可用）。

### I. `get_instruments` 返回面（2026-10-30 新发现）

文档返回合约信息（FutureParams），且**实盘模块涨跌幅平台暂无法提供**（东莞.md）⇒ 本地仅返回基础证券元信息、**期货不支持**（本地 docstring）＝返回面差异（P2）。

### J. `get_price` 参数必填性与 QS 扩展（2026-10-30 批 2 新发现）

文档：security/start_date/end_date/frequency **均必填**，fields 可选；本地三项均可省略且多 `fq='pre'`/`count`/`is_dict`（QS 扩展）。
**fq 迁移面已闭合**（P3-1 对 get_history/get_price 显式注入 fq='pre'，同 §四 A）；余「必填性放宽」属本地宽松（P3）。

### K. `get_trading_day` 参数语义完全不同（2026-10-30 批 2 新发现）

文档 `get_trading_day(date)`：**date 格式 'YYYY-MM-DD'，返回 bool**；本地 `get_trading_day(day=0)`＝**偏移天数语义** ⇒ 参数名与语义均不同（调用面阻断级）。

### L. `get_industry_stocks` 本地恒返空列表（W4 本地实现侧核 · 2026-10-30）

| 端 | 行为 |
| --- | --- |
| 本地 | `ptrade_api.py:2081` → `self._reference.get_industry_stocks(...)`；docstring 明写「**DuckDB 暂无行业成分表 → 返回空 list**」⇒ **恒空** |
| PTrade | 返**当下**行业成分，文档明载「获取当下数据，**回测存在未来函数**」 |

结论：本地**不存在未来函数**（比平台保守），但**语义不同**——依赖行业池的策略在本地回测**恒得零样本**（静默失效面）。
对照：`get_index_stocks`（index 面）已严格 PIT（F3 as-of＋日期归一＋fail-closed，L825）⇒ **PIT 仅覆盖 index 面，行业面未覆盖**。

### M. `get_stock_blocks` 未来函数面（2026-10-30 批 3 新发现）

文档明载「获取的是**当下**的数据，**回测时注意未来函数**」（东莞.md）；参数面与本地一致。
本地实现 PIT 关键词核：**未命中 ⇒ 本地无 PIT 取证** ⇒ 行业/概念板块面**未做 PIT 取证**（与差异 L 同族的未来函数面家族）。

### N. `get_stock_exrights` 首参名（2026-10-30 批 3 新发现）

文档 `get_stock_exrights(stock_code, date=None)`；本地 `(security, date=None)` ⇒ **首参名不同**（位置调用等价、关键字调用不兼容）＝差异 E 同族的**本地命名差异**，无迁移阻断点（PTrade 产物不产 `get_stock_exrights`）。

### P. `get_reits_list` 参数面（2026-10-30 批 5 新发现·弱判据）

文档（山西.md）：获取 REITs 列表，适用研究/回测/交易；**Parameters / Request Example 均标 N/A（未列参数）**。
本地 `(date=None)`：多一个**可选** `date` 参数 ⇒ 若平台确为无参，则本地为**更宽松**（调用面不阻断）。
**判据强度：弱**（文档未列参数，无法逐参比对）⇒ 已登记 ISS-T5-P，留待后续文档版本再判。

### Q. `get_all_positions` 适用环境（2026-10-30 批 6 新发现）

文档（山西.md）：获取全部持仓信息，**适用环境：仅交易**；Parameters N/A。本地 `()` 无参一致；**环境面本地更宽松**（回测亦可调）——与差异 H（get_etf_stock_list 仅交易）同族。

### R. `set_benchmark` 首参名（2026-10-30 批 6 新发现）

| 端 | 签名 |
| --- | --- |
| 本地 | `set_benchmark(sids)` |
| PTrade | `set_benchmark(security)`（api-reference/settings.md 英中双源） |

位置调用等价、**关键字调用不兼容**＝差异 E（order_target）/N（get_stock_exrights）族的本地命名差异；另注：平台**仅 initialize() 可用**、不设默认沪深300 `000300.SS`——本地是否限制调用位置未核（与 W3 run_daily 同族行为面）。

### S. `get_etf_info` 适用环境（2026-10-30 批 7 新发现）

文档（国盛.md）：get_etf_info(etf_code) 返回 dict，**适用环境：交易** ⇒ 本地回测亦可调＝环境面本地更宽松（与差异 H/Q 同族）。

## 六·补 W5 收官建议（2026-10-30 批 7 后）

余 15 条经**补查批 1–7（共 21 次 Context7 查询）**均未命中任何独立签名条目（文档以示例/模板为主，未逐 API 列签名）：

　　`check_limit`／`create_dir`／`current_price`／`get_business_type`／`get_cb_list`／`get_current_data`／`get_etf_stock_info`／`get_frequency`／`get_market_detail`／`get_market_list`／`get_security_info`／`get_trading_day_by_date`／`get_underlying_code`／`get_user_name`／`set_universe`

**结论建议**：判为「**文档面系统性缺失**」（非查询配额问题）⇒ **保留「未取到文档」如实标注并结案**，不再无限续查；若后续需这些 API 的签名依据，改走他途（本地实现已按 PTrade 语义实现并经真实策略验证／必要时取官方 PDF 文档）。**待你方裁定是否收官**。

## 六·补二 W5 收官（用户 2026-10-31 照准）

**收官结论**：余 **15 条**判为「**文档面系统性缺失**」——补查批 1–7 共 **21 次 Context7 查询**均未命中任何独立签名条目（PTrade 文档以示例/模板为主，未逐 API 列签名）。

**处置**：保留「未取到文档」如实标注，**结案**，不再继续补查；如需这些 API 的签名依据，改走他途（官方 PDF 文档等）另行立项。**不凭记忆补写签名**（三形态纪律）。

结案清单（15 条）：`check_limit`／`create_dir`／`current_price`／`get_business_type`／`get_cb_list`／`get_current_data`／`get_etf_stock_info`／`get_frequency`／`get_market_detail`／`get_market_list`／`get_security_info`／`get_trading_day_by_date`／`get_underlying_code`／`get_user_name`／`set_universe`

**终态计数（68 条，机检复核）**：✅ 一致 **36** ／ ⚠️ 差异 **16**（A/B/C/E/F/G/H/I/J/K/L/M/N/P/Q/R/S）／ 🔸 未取到 **15**（已收官）／ ⚠️ 判缺 **1** = **68**

## 五、QS_ONLY 口径调整（随本件回写）

| API | 参数 | 处置 |
| --- | --- | --- |
| `get_fundamentals` | `is_dataframe=False` | 移 QS_ONLY 标注（本地扩展） |
| `get_snapshot` | `frequency='1d'` | 移 QS_ONLY 标注（回测扩展） |

> 说明：两者**不传参时行为与平台一致**，故 PTrade 面计数不变。

## 六、待核清单（结转下轮）

| # | 项 | 类型 | 处置 |
| --- | --- | --- | --- |
| W1 | `get_trades` 分钟级缓存语义 | 行为面 | **✅ 2026-10-30 结案**：本地 `get_trades` 直返 `engine._today_trades`（`ptrade_api.py:2702`），**无分钟级缓存**；PTrade 文档为「同一分钟多次调用返回当分钟首次查询缓存」⇒ **已知差异（本地实时返回，更新鲜但不等价）** |
| W2 | `order_target` 关键字传参现状 | 迁移面 | **✅ 2026-10-30 结案（风险不成立）**：转换管线 `order_target` 独立命中 5 处**全部为 `order_target_percent`**；`_ORDER_APIS`（`source_import.py:3133`）仅含 `order_target_value`／`order_target_percent`／`order_value`；`target_amount` 命中 **0** ⇒ **PTrade 产物不生成 `order_target`** ⇒ 差异 E **降级为本地 API 命名差异，无迁移阻断点** |
| W3 | `run_daily` 三条语义 | 行为面 | **✅ 2026-10-30 结案**：本地实现仅注册 `_daily_tasks`（`ptrade_api.py:1924-1926`），三条语义**均未实现**——①不限仅 initialize 可调 ②无 5 任务上限 ③日线回测时点由引擎定（非恒 15:00）⇒ **行为差异（本地宽松）** |
| W2 | `order_target` 关键字传参现状 | 迁移面 | 核 `source_import` 是否已有参数名改写；无则补（P3-1 同族） |
| W3 | `run_daily` 三条语义 | 行为面 | ①仅 initialize 可调 ②>5 任务堵塞 ③日线回测恒 15:00｜本地是否模拟待核 |
| W4 | `get_industry_stocks` 未来函数 | 行为面 | **✅ 2026-10-30 结案（本地实现侧核）**：本地 `ptrade_api.py:2081` **恒返空 list**（DuckDB 无行业成分表）⇒ **无未来函数**（比平台保守），但**语义不同＝本地恒零样本**；`get_index_stocks`（index 面）已严格 PIT（L825 F3 as-of＋fail-closed）⇒ PIT 仅覆盖 index 面 ⇒ 登记差异 L 与 ISS-T5-L |
| W5 | 文档补查 | 取证面 | **补查批 1（2026-10-30）已完成**：清 7 条（一致 3：get_ipo_stocks/get_cb_info/get_stock_status；差异 4：F/G/H/I）⇒ 余 **16 条**，按批续查（每批 3 次配额）；批 2-6 另得存在性证据 |
| W6 | `get_current_data` 单独批核 | 取证面 | **✅ 2026-10-30 结案＝未取到**：独立立批 3 次 Context7 查询配额用尽仍无该名（三查返回 is_trade/get_snapshot/order、get_fundamentals、get_stock_status/get_stock_info）⇒ 保留「未取到文档」标注，**不凭记忆补写签名** |

## 七、判据自纠记录（我方缺陷，同轮发现并更正）

**缺陷**：首版 AST 签名提取只取 `args.args`，未处理 `vararg/kwonlyargs/kwarg` ⇒ 把 `set_commission(self, **kwargs)` 读成「零参数」，一度准备报「文档三参 vs 本地零参」的重大差异。

**发现**：按判据三形态「失败先自证」，在报重大差异前读实现体，见 `def set_commission(self, **kwargs)` 且体内处理 `commission_ratio/min_commission/type`。

**更正**：全 68 条重提签名 ⇒ 仅 3 条含可变参数（`get_fundamentals`／`set_commission`／`set_parameters`）；batch 1-3 结论不受影响（`get_fundamentals` 仅补注 `**kwargs`）。

**教训**：AST 签名提取必须处理七件套；**可变参数不是零参数**。

**知识反哺**：`[无命中]` knowledge/qmt qd 族 14 条无「AST 签名提取」类条目（检索日期 2026-10-28）——本条教训拟随下轮 R4 沉淀为 QD 条目。

## 八、R1／R4 留痕

```
知识反哺：[命中] QD-2026-0007／0008（API 契约域，qd 族，2026-10-28）被采纳要点：契约核验须双端标注依据
知识反哺：[无命中] knowledge/registry.md 无 API 逐条行为核验既有件，2026-10-28
本轮沉淀：N/A（理由：纯验证轮零产品改动；AST 七件套教训拟下轮随 R4 起草 QD 条目）
```

**判据自纠记录 2（2026-10-30 批 2 自查）**：
补查批 2 复核签名时，我方临时 AST 提取脚本用 `defaults[i-len(posonlyargs)]` 取默认值
⇒ **默认值与形参错位**（实测把 `get_price` 读成 `start_date='1d', frequency='pre', fq=False`），
该输出**不可信**。修正对齐式为 `d_idx = i-(npos-ndef)`（defaults 覆盖最后 ndef 个位置参数）后
重取 4 条，与表内签名**逐参比对全部一致**（`get_positions(security=None)`／
`get_price(security, start_date=None, end_date=None, frequency='1d', fields=None, fq='pre', count=None, is_dict=False)`／
`get_trades_file(save_path='')`／`get_trading_day(day=0)`）⇒ **差异 J/K 的判定依据成立，表内签名无需更正**。
**教训**：AST 提取的默认值必须按「位置参数总数 − defaults 个数」偏移对齐；
**临时脚本的输出在用于判定前必须与权威表自动比对**，不得凭肉眼认读（本次差异由自动比对而非人眼发现）。
