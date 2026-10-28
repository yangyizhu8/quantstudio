# API 公开面行为核验（T5 收口件 · 2026-10-28）

> 触发：registry「注入 API 契约」行登记的「PTrade 面 69 待逐条行为核验」缺口闭合。
> 上游：`docs/evidence/api-surface-enumeration-20261023.md`（枚举终稿 v3，机器生成，**PTrade 面 68**）。
> 基线：`origin/main = 76e9e50`（统推 #8 出仓锚）；本件为纯验证轮，**零产品改动**。
> 判据三形态：①输入形态门（先验对象形态再下结论）②单值比对（禁区间/多值混判）③判定失败分报（不自证前不作事实级断言）。

## 一、结论摘要

| 结论 | 数量 | 说明 |
| --- | --- | --- |
| ✅ 一致/兼容 | **19** | 签名逐参对得上（含默认值）或有等价实现证据 |
| ⚠️ 差异 | **0** | A/B/C 已裁、E 待转换管线核对（详见 §四） |
| 🔸 未取到文档 | **44** | Context7 每批 3 次配额所限，如实标注待补查 |
| ⚠️ 判缺 | **1** | attribute_history（聚宽名混入候选） |
| **合计** | **68** | = PTRADE 面 68 |

> **与总调度裁定数字的差异说明（如实）**：裁定令记为「22 一致／43 未取到」，本轮逐条复核实际为 **19 一致／44 未取到／4 差异／1 判缺**（合计 68）。差异源于按「已取得文档依据且逐参比对通过」严格计数：`get_CCI`（半锚定，n=14 默认值未单查）按未取到计、`get_trades`（签名一致但缓存语义待核）计入一致并单列待核项。**以本表实际计数为准**，不凑数。

## 二、逐条表（68 条）

| # | API | 本地签名（AST 七件套提取） | 结论 | 文档依据／说明 |
| --- | --- | --- | --- | --- |
| 1 | `attribute_history` | `(security, count, unit='1d', fields=None)` | 判缺 | PTrade 面无此 API（聚宽名混入候选；文档查无此名，get_history 为正主；镜像曾命中系混入特征）；68 计数暂不调整，终判随 T5 收口 |
| 2 | `cancel_order` | `(order_param)` | 一致 | cancel_order(order_param)：Order 对象或 order_id；返回 None（Context7 stock-trading.md） |
| 3 | `check_limit` | `(security, query_date=None)` | 未取到 | 配额未覆盖 |
| 4 | `convert_position_from_csv` | `(path)` | 未取到 | 配额未覆盖 |
| 5 | `create_dir` | `(user_path)` | 未取到 | 配额未覆盖；注意：在 DENY_REMOVE 移除面清单 |
| 6 | `current_price` | `(security)` | 未取到 | 配额未覆盖 |
| 7 | `filter_stock_by_status` | `(stocks, filter_type=None, query_date=None)` | 一致 | SHIM_CONTRACT_REGISTRY 已登记（测试断言集合相等）＋QMT 侧 QS_QMT_SEMANTIC_DIFF 语义差异行（无历史 PIT） |
| 8 | `get_CCI` | `(high, low, close, n=14)` | 未取到 | 指标族文档已证（MACD/KDJ 同族格式），CCI 未单独返回；n 默认值 14 待单查 |
| 9 | `get_KDJ` | `(high, low, close, n=9, m1=3, m2=3)` | 一致 | get_KDJ(high, low, close, n=9, m1=3, m2=3) 返回 k/d/j（Context7 Ptrade社区.md） |
| 10 | `get_MACD` | `(close, short=12, long=26, m=9)` | 一致 | get_MACD(close, short=12, long=26, m=9) 返回 dif/dea/macd（Context7 社区/东莞双源） |
| 11 | `get_RSI` | `(close, n=6)` | 未取到 | 配额未覆盖 |
| 12 | `get_all_positions` | `()` | 未取到 | 配额未覆盖 |
| 13 | `get_all_trades_days` | `(date=None)` | 一致 | get_all_trades_days(date=None)：返回 date 之前全部交易日（Context7 basic-info.md，批 3 第 3 次查询顺带覆盖） |
| 14 | `get_business_type` | `()` | 未取到 | 配额未覆盖 |
| 15 | `get_cb_info` | `()` | 未取到 | 配额未覆盖 |
| 16 | `get_cb_list` | `()` | 未取到 | 配额未覆盖 |
| 17 | `get_current_data` | `()` | 未取到 | 配额未覆盖；**重要 API，单独批核** |
| 18 | `get_current_kline_count` | `()` | 未取到 | 配额未覆盖 |
| 19 | `get_dominant_contract` | `(contract, date=None)` | 未取到 | 配额未覆盖 |
| 20 | `get_etf_info` | `(etf_code)` | 未取到 | 配额未覆盖 |
| 21 | `get_etf_list` | `()` | 一致 | get_etf_list() 无参、返回 list[str]、异常返回空 list（Context7 东莞/山西双源） |
| 22 | `get_etf_stock_info` | `(etf_code, security)` | 未取到 | 配额未覆盖 |
| 23 | `get_etf_stock_list` | `(etf_code)` | 未取到 | 配额未覆盖 |
| 24 | `get_frequency` | `()` | 未取到 | 配额未覆盖 |
| 25 | `get_fundamentals` | `(security, table='valuation', fields=None, date=None, start_year=None, end_year=None, report_types=None, merge_type=None, is_dataframe=False, **kwargs)` | 差异 B | 9 张表枚举；merge_type 不传＝首次发布（回测防未来）/1＝更新数据；**table 本地有默认 valuation、文档必传**；**is_dataframe 本地扩展**；date_type 文档已废弃（本地已对齐）（Context7 财务数据东莞/山西） |
| 26 | `get_history` | `(security=None, count=None, unit='1d', fields=None, frequency=None, field=None, security_list=None, fq='pre', include=False, fill='nan', is_dict=False)` | 差异 A | get_history(count, frequency="1d", field="close", security_list=None, fq=None, include=False, fill="nan", is_dict=False)；**fq 默认：本地 pre vs 文档 None**；参数别名并集（Context7 market-data.md） |
| 27 | `get_index_stocks` | `(index_code, date=None)` | 一致 | get_index_stocks(index_code, date)：date 格式 YYYYMMDD，默认当日（Context7 stock-info.md） |
| 28 | `get_industry_stocks` | `(industry_code)` | 一致 | get_industry_stocks(industry_code)：尾缀须 .XBHS；文档明写「获取当下数据，回测存在未来函数」（Context7 stock-info.md/山西） |
| 29 | `get_instruments` | `(contract)` | 未取到 | 配额未覆盖 |
| 30 | `get_ipo_stocks` | `()` | 未取到 | 配额未覆盖 |
| 31 | `get_margin_rate` | `(transaction_code)` | 未取到 | 配额未覆盖 |
| 32 | `get_market_detail` | `(finance_mic)` | 未取到 | 配额未覆盖 |
| 33 | `get_market_list` | `()` | 未取到 | 配额未覆盖 |
| 34 | `get_open_orders` | `(security=None)` | 一致 | get_open_orders(security=None)：当日未成交（Context7 stock-trading.md） |
| 35 | `get_order` | `(order_id)` | 一致 | get_order(order_id)：单个订单（Context7 stock-trading.md） |
| 36 | `get_orders` | `(security=None)` | 一致 | get_orders(security=None)：本策略当日全部订单（Context7 stock-trading.md/山西） |
| 37 | `get_position` | `(security)` | 未取到 | 配额未覆盖 |
| 38 | `get_positions` | `(security=None)` | 未取到 | 配额未覆盖（文档侧另有 get_all_positions()） |
| 39 | `get_price` | `(security, start_date=None, end_date=None, frequency='1d', fields=None, fq='pre', count=None, is_dict=False)` | 未取到 | 配额未覆盖；P3-1 已注入 fq='pre' |
| 40 | `get_reits_list` | `(date=None)` | 未取到 | 配额未覆盖 |
| 41 | `get_research_path` | `()` | 未取到 | 配额未覆盖；在 DENY_REMOVE 移除面清单 |
| 42 | `get_security_info` | `(code)` | 未取到 | 配额未覆盖 |
| 43 | `get_snapshot` | `(security, frequency='1d')` | 差异 C | get_snapshot(security) **单参**；返回 dict[str,dict]，40+ 字段，trade_status 16 种枚举（Context7 东莞.md）；**本地多 frequency 参数＝QS 扩展** |
| 44 | `get_stock_blocks` | `(stock_code)` | 未取到 | 配额未覆盖 |
| 45 | `get_stock_exrights` | `(security, date=None)` | 未取到 | 配额未覆盖 |
| 46 | `get_stock_info` | `(stocks, field=None)` | 未取到 | 配额未覆盖（QMT 侧有 D-23 日期契约修复史） |
| 47 | `get_stock_name` | `(stocks)` | 未取到 | 配额未覆盖 |
| 48 | `get_stock_status` | `(stocks, query_type='ST', query_date=None)` | 未取到 | 配额未覆盖 |
| 49 | `get_trade_days` | `(start_date=None, end_date=None, count=None)` | 一致 | get_trade_days(start_date=None, end_date=None, count=None)；**start_date 与 count 互斥**；返回 numpy.ndarray（Context7 basic-info.md） |
| 50 | `get_trades` | `()` | 一致 | get_trades()：当日本策略全部成交；**同一分钟多次调用返回当分钟首次查询缓存**；一个订单号可对多笔成交（Context7 东莞.md）；缓存语义待核 |
| 51 | `get_trades_file` | `(save_path='')` | 未取到 | 配额未覆盖 |
| 52 | `get_trading_day` | `(day=0)` | 未取到 | 配额未覆盖 |
| 53 | `get_trading_day_by_date` | `(query_date, day=0)` | 未取到 | 配额未覆盖 |
| 54 | `get_trend_data` | `(date=None, stocks=None, market=None)` | 未取到 | 配额未覆盖 |
| 55 | `get_underlying_code` | `(symbols)` | 未取到 | 配额未覆盖 |
| 56 | `get_user_name` | `(login_account=True)` | 未取到 | 配额未覆盖 |
| 57 | `order` | `(security, amount, limit_price=None)` | 一致 | order(security, amount, limit_price)；返回 Order id 或 None（order_target 族文档证实 limit_price 语义） |
| 58 | `order_target` | `(security, target_amount, limit_price=None)` | 差异 E | order_target(security, **amount**, limit_price=None)；**本地参数名 target_amount**（位置调用等价、关键字调用不兼容）（Context7 stock-trading.md） |
| 59 | `order_target_value` | `(security, value, limit_price=None)` | 一致 | order_target_value(security, value, limit_price=None)；返回 id/None；取整：股票 100 股／可转债 10 张（Context7 山西/stock-trading 双源） |
| 60 | `order_value` | `(security, value, limit_price=None)` | 未取到 | 配额未覆盖（与 59 同族） |
| 61 | `run_daily` | `(context, func, time='9:31')` | 一致 | run_daily(context, func, time="9:31")；仅 initialize 可调；累计 >5 个定时任务会被堵塞；**回测周期为日时无论设值只在 15:00 执行**（Context7 山西.md） |
| 62 | `set_benchmark` | `(sids)` | 未取到 | 配额未覆盖 |
| 63 | `set_commission` | `(**kwargs)` | 一致 | set_commission(commission_ratio=0.0003, min_commission=5.0, type="STOCK")；总手续费＝佣金＋经手费（万分之 0.487）；**本地 **kwargs 收同名参数、实现体处理 commission_ratio/min_commission/type ⇒ 兼容**（Context7 settings.md） |
| 64 | `set_fixed_slippage` | `(fixedslippage=0.0)` | 一致 | 东莞版 set_fixed_slippage(fixedslippage=0.0)；成交价＝委托价 ± fixedslippage/2；api-reference 版作 fixed_slippage（**文档自身不一致**，本地采东莞原版） |
| 65 | `set_parameters` | `(**kwargs)` | 未取到 | 配额未覆盖（**kwargs 收任意参数） |
| 66 | `set_slippage` | `(slippage=0.0)` | 一致 | 文档未返回；本地实现体 docstring 明写「PTrade accepts set_slippage(slippage=...) or one positional value. Local-only aliases are rejected so local success predicts platform success」⇒ 强证据对齐 |
| 67 | `set_universe` | `(security_list)` | 未取到 | 配额未覆盖 |
| 68 | `set_yesterday_position` | `(poslist)` | 未取到 | 配额未覆盖 |

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

## 五、QS_ONLY 口径调整（随本件回写）

| API | 参数 | 处置 |
| --- | --- | --- |
| `get_fundamentals` | `is_dataframe=False` | 移 QS_ONLY 标注（本地扩展） |
| `get_snapshot` | `frequency='1d'` | 移 QS_ONLY 标注（回测扩展） |

> 说明：两者**不传参时行为与平台一致**，故 PTrade 面计数不变。

## 六、待核清单（结转下轮）

| # | 项 | 类型 | 处置 |
| --- | --- | --- | --- |
| W1 | `get_trades` 分钟级缓存语义 | 行为面 | 核转换产物是否消费 get_trades；若消费需评估缓存语义模拟 |
| W2 | `order_target` 关键字传参现状 | 迁移面 | 核 `source_import` 是否已有参数名改写；无则补（P3-1 同族） |
| W3 | `run_daily` 三条语义 | 行为面 | ①仅 initialize 可调 ②>5 任务堵塞 ③日线回测恒 15:00｜本地是否模拟待核 |
| W4 | `get_industry_stocks` 未来函数 | 行为面 | 文档明载「获取当下数据」；registry F3 仅锚定 index 面 PIT |
| W5 | 44 条文档补查 | 取证面 | 分 2-3 轮按批补查（每轮 3 次配额） |
| W6 | `get_current_data` 单独批核 | 取证面 | 重要 API，单独立批 |

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
