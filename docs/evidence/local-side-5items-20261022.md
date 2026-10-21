# 本地侧续推五件（LC-08／FEE-02·03·04／IDX-02 · 2026-10-22）

- **触发**：总调度令「续推 IDX/LC-08/FEE-04 公式/DAT 枚举自行面」
- **性质**：只读取证 ＋ 账本回填；**零代码改动**

## 一、LC-08 策略回调异常行为 —— **已锚定（6 处调用点全实勘）**

| 调用点 | 路径 | 异常处理行为 |
| --- | --- | --- |
| `backtest_engine.py:2271` | Ptrade 日线 `before_trading_start` | `except Exception as e:` → **`self._lifecycle_errors += 1`** ＋ `logger.error("[Ptrade] before_trading_start 错误: …")` |
| `:2283` | Ptrade 日线 `handle_data` | 同段结构捕获 → 记 error |
| `:2368` | DailyProxy `before_trading_start` | `except Exception as exc:` → `logger.error("[DailyProxy] …")` |
| `:2404` | DailyProxy `handle_data` | `except Exception as exc:` → `logger.error("[DailyProxy] handle_data error @ bar …")` |
| `:2553` | PR4 分钟 `before_trading_start` | `except Exception as e:` → `logger.error("[PR4] …")` |
| `:2605` | PR4 分钟 `handle_data` | `except Exception as e:` → `logger.error("[PR4] handle_data 错误 @ bar …")` |

**语义定谳**：策略回调抛异常 ⇒ **被捕获、不中断回测**（后续 bar 继续执行）、**记 error 日志**；日线 Ptrade 路径另**累加 `_lifecycle_errors` 计数**。
**⚠️ 已登记的不对称（观察项，非缺陷）**：`_lifecycle_errors` 计数**只在 :2271 一条路径**；DailyProxy／PR4 两条路径**仅记日志不计数** ⇒ 三路径异常可观测性不一致（若将来需要统一统计，"异常次数"不能只看 `_lifecycle_errors`）。

## 二、FEE 面：`TradeCost` 定义与三处公式（**全部定位**）

`TradeCost`（`backtest_engine.py:227` `@dataclass`）默认值：

| 字段 | 默认值 | 语义 |
| --- | --- | --- |
| `commission_rate` | **0.00035** | 佣金费率（万 3.5） |
| `min_commission` | **5.0** | 最低佣金（元） |
| `stamp_tax_rate` | **0.001** | 印花税（**卖出单向**，千 1，注释标"对齐 Ptrade"） |
| `transfer_fee_rate` | **0.00001** | 过户费（万 0.1） |

**计费公式（实勘）**：
- **买入**：`transfer_fee = cost_amount × transfer_fee_rate`；`total_cost = cost_amount + commission + transfer_fee`（`:1167-1168`）；另有 `one_lot_cost × transfer_fee_rate`（`:1177`）
- **卖出**：`stamp_tax = proceeds × _stamp_tax_rate(date)`；`transfer_fee = proceeds × transfer_fee_rate`；`net_proceeds = proceeds − commission − stamp_tax − transfer_fee`（`:1248-1250`）；`pnl = (fill − avg_cost)×qty − commission − stamp_tax − transfer_fee`（`:1252`）
- **🔴 印花税分期间（重要发现）**：`_stamp_tax_rate(date)`（`:885-889`）在特定日期后**封顶 0.0005**（`min(rate, 0.0005)`）⇒ **印花税不是常量，随日期切换**（疑对应 2023-08-28 印花税减半，待与平台口径核对）

**判定**：
| 条目 | 结论 |
| --- | --- |
| **FEE-02 佣金费率** | **已锚定**：默认 `commission_rate=0.00035` ＋ `min_commission=5.0` |
| **FEE-03 印花税** | **已锚定（升级）**：卖出单向 0.001，且**分期间封顶 0.0005** ⇒ 平台侧须核对同期间切换点 |
| **FEE-04 过户费** | **已锚定（升级）**：默认 0.00001，买卖**双向**按成交额计提（买入 :1167／卖出 :1249） |

## 三、IDX-02 年化 —— **已锚定（公式实名）**

| 公式 | 位置 | 说明 |
| --- | --- | --- |
| `strategy_annual_return = pow(1 + total_return, TRADING_DAYS_PER_YEAR / trade_days) − 1` | `ptrade_metrics.py:123` | **复利年化**（按交易日数年化）；基准/相对/超额同式（:124-126） |
| `annual_return = summary['annual_return_pct']`，回退 `(final/initial) ** (250/len(df)) − 1` | `backtest_engine.py:270` | 引擎摘要优先，回退 250 日年化 |
| 出参 | `ptrade_metrics.py:195` | `annual_return_pct = strategy_annual_return × 100` |

⇒ 原账本「同上（年化由日收益链推导）」为**推断**，现补**实名公式**＋行号，升级为有据。

## 四、附带：IDX 指标出参面（补强既有锚定）

`ptrade_metrics.py:192-197` 出参：`max_drawdown_pct`（:192）／`annual_return_pct`（:195）／`alpha_ratio`（:197）；
`sharpe`（:142 `(strategy_annual_return − RISK_FREE)/(volatility/√252)`）／`sortino`（:148 同构，用 downside_risk，:31/:46-47）。
⇒ IDX-04/05/06/09 的公式侧证据补强（原仅引 `test_dual_end_reconcile` 对账测试）。

## 五、DAT 枚举（11 项）— **本轮未做，需先定枚举口径**

`DAT-05..15` 当前是**一行聚合**（"日期边界/首末日/fallback/指数日线专门 API/停牌日 K 线/ETF 池 PIT/…"），要拆成 11 条**须先确定拆分口径**（按 API？按口径维度？按坑族？）——属**账本结构变更**，不宜顺手定。
**建议拆分口径（待裁）**：按**口径维度**拆 11 条 = ①日期边界（首/末日）②停牌日 K 线 ③fallback 链 ④复权口径 ⑤PIT 时点 ⑥指数日线专门 API ⑦ETF 池 PIT ⑧分钟/日线一致性 ⑨字段缺失表达（None/空串/0）⑩单位与精度 ⑪跨源一致性。选定后一轮可在 `docs/evidence/` 出枚举清单并回填。
