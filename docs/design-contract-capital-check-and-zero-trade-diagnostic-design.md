# 方案：设计契约本金校验（A）+ 零成交诊断（B）—— 框架层两件

> 六步流水线**第 1 步（方案）**｜提出：策略线会话｜2026-10-06
> ②审计：**PASS**（总调度 2026-10-05）｜③实施：**已获加速令执行**（2026-10-06 晨）
> 触发：GUI 回测「沪深300慢KD超卖均值修复策略」**161 交易日零成交**（用户实测日志）
> **铁律约束**：「策略生成与转换全链路修复：仅限框架层」——两件**均落框架层，策略源码零改动**
> **分类＝新增检测型**（铁律要求方案阶段单列该形态——本方案据此单列，不与纯恢复型混办）

## 1. 问题定义
用户那次 `config.csv` 的 `init_capital = 100000`，而策略设计契约 `portfolio_contract.required_initial_cash = 1000000`
（`sizing_mode=fixed_notional`，10 仓 × 单只 10 万）。**最小复现**（同策略同窗口，仅改本金）：

| 初始资金 | 成交笔数 |
| --- | --- |
| **100,000** | **0** |
| **1,000,000** | **34** |

→ 本金为设计要求 1/10 时连一仓都足额建不起，在客户裁定的 S-2-A（要么足额、要么不建仓）下必然零成交。
**策略行为正确、配置错配，但系统对此完全静默。**

**四层默认均 10 万的陷阱链**：`gui/tabs/backtest_tab.py:76-78` `setValue(100000)` → `gui/workers.py:424` →
`backtest/run_ptrade_strategy.py:29-33` `capital=100_000` → `backtest/backtest_engine.py:320-321` `capital=100_000`。

**静默两缺口**：① 设计契约 `required_initial_cash` 在回测期**从未被消费**；
② 引擎 **已有**空跑告警（`:636-643`）但判据为 `零成交 AND _lifecycle_errors>0`，本次无报错 → 不触发。

## 2. 改动范围
| 件 | 落点 | 规模 |
| --- | --- | --- |
| A | `quantstudio/backtest/run_ptrade_strategy.py`：新增 `_check_design_capital_contract()` + 在 `engine.run()` 前调用 | **+44 行**（纯新增） |
| B | `quantstudio/backtest/backtest_engine.py:636-643`：既有空跑告警**补全**新增 `elif` 分支 | **+10 行**（纯新增） |

**A 做法**：惰性导入 `find_design_for_strategy` → **仅 `RESOLVED`** 才读 design JSON 取 `portfolio_contract.required_initial_cash` →
**非空且 `capital < required`** 时 `logger.warning`（含 required/capital/sizing_mode/design 路径；明示「可能无法足额建仓甚至零成交；仍按给定资金执行，不中断」）；整块 `try/except Exception: pass`。
**B 做法**：既有分支**逐字不动**，仅新增 `elif len(self.result.trade_records) == 0:` 输出诊断（零成交、执行错误 0、三条可能原因、提示核对 `QS_SIGNAL` 与设计元数据）。

## 3. 复用既有机制（不新建平行机制）
| 机制 | 位置 |
| --- | --- |
| 设计元数据可信解析链 | `strategy_compiler/design_metadata.py:151 find_design_for_strategy`（只读、ledger 生命周期 + SHA 绑定 + schema 2.3 校验） |
| 契约字段 | `agent_strategy_design.schema.json:444-450` `portfolio_contract.required_initial_cash` |
| 唯一共用入口 | `run_backtest`（CLI `:269` + GUI `workers.py:419` 都走它） |
| 零成交判据 | `result.trade_records`（引擎 `:639` 自述口径） |
| 「无条件诊断」原则 | 引擎 `:649-662`「防『空数据静默出回测』事故模式」 |

## 4. 影响面与三型判定
| 三问 | A | B |
| --- | --- | --- |
| 影响其他功能 | 否（仅 RESOLVED＋本金不足时加一条；其余零输出） | 否（既有分支不动；仅零成交且无错误时加一条） |
| 影响性能 | 否（启动期一次） | 否（收尾一次 `len()`） |
| 影响精度 | 否（不触撮合/费用/持仓/净值/信号） | 否 |

**类型＝新增检测型**；**红线**：diff 只能归因于新增诊断输出，出现任何数值/产物差异即验收失败回退。

## 5. 验收标准（V-A1~A5 / V-B1~B4 / V-C1~C4）
见 `docs/evidence/capital-contract-check-and-zero-trade-diagnostic-acceptance-20261006.md`。

## 6. 回退条件
单点回退（A/B 两块互不依赖）；V-C4 出现数值差异或 V-C1~C3 失败即回退；回退后须重新登记为「未修」。

## 7. 明确不做
不改任何策略源码；不改 GUI/引擎默认本金（平台口径）；不触撮合/费用/持仓/净值/信号；不做策略侧 `skip_notes`（属 FIX-5）；不处理 FIX-2/FIX-3；不新增配置/schema/CLI 旗标。
