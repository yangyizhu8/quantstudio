# 五策略 OLD 产物指认（sha16 → 完整路径 · 所在域）— 供 ⑤ 复核（2026-11-10）

> **背景**：2026-11-09 本地扫描 `output/ptrade_export/**` 与 `Demo_account_Real_account/**`，
> 得 5 个**第一跳产物**不含 `_qs_cash_budget`（即**无 PD-2018 F3(a) 拆单预算钳制**）⇒ 若投用，
> 下单预算不受钳制，具备重演 2026-11-09「超额委托 → `[260200]` 部分失败」形态的条件。
> **指认来源**：本机扫描 `output/ptrade_export/<策略目录>/<产物名>_ptrade.py`（sha256 前 16 位）。
> **域归属判断依据**：目录名 ＋ 产物文件名 ＋ 既有知识条目；**各线请自认**（我方不代认）。

| # | sha16 | 产物完整路径（本机） | 所在域（产出链归属） | 备注 |
|---|---|---|---|---|
| 1 | `f37fe8d66cddd4da` | `D:\miniQMT策略实盘\QuantStudio\output\ptrade_export\沪深300慢KD超卖均值修复策略\沪深300慢KD超卖均值修复策略_ptrade.py` | 策略 skill 产物域（`quantstudio-strategy-compiler` skill 族） | 与本项目三策略同仓不同族 |
| 2 | `531dab841a0d9bc6` | `D:\miniQMT策略实盘\QuantStudio\output\ptrade_export\etf_hot_theme_rotation\strategy\strategy_ptrade.py` | ETF 主题轮动（skill 产物域，**嵌套 `strategy/` 目录**） | — |
| 3 | `6b1516fd7ddf3ae6` | `D:\miniQMT策略实盘\QuantStudio\output\ptrade_export\vol_regime_mom_rev\vol_regime_mom_rev_ptrade.py` | 简单量化工厂产物域（`simple-quant-factory` 系，`vol_regime_mom_rev` 属铁律 07 六策略之一） | — |
| 4 | `949679f2370890e4` | `D:\miniQMT策略实盘\QuantStudio\output\ptrade_export\CANSLIM突破成长选股策略\CANSLIM突破成长选股策略_ptrade.py` | 策略 skill 产物域（CANSLIM 族，属铁律 07 六策略之一） | — |
| 5 | `a884eac2c6909d90` | `D:\miniQMT策略实盘\QuantStudio\output\ptrade_export\tech_etf_mvo_rotation\tech_etf_mvo_rotation_ptrade.py` | ETF MVO（skill 产物域，属铁律 07 六策略之一） | ⚠️ 该策略 2026-07-26 已被真实 IQEngine 证伪（`set_backtest` NameError）⇒ 产物**已失效**，登记仅为完整性 |

## 复核要点（建议 ⑤ 按此逐项核）

1. **是否投用**：投用则须先重转（取现行第一跳 ⇒ 带 `_qs_cash_budget` 钳制）再部署；
2. **是否在铁律 07 六策略验收面**（第 3／4／5 项是）⇒ 重转后须过该门禁；
3. **第 5 项已失效** ⇒ 只作登记，不必重转；
4. 重转后请回填各策略新产物的 sha16 ＋ 确认含 `_qs_cash_budget`。

> **我方动作边界**：本表仅为**指认与登记**（铁律 07：修复落点须在通用框架层，但**清点与重转排期属各线自己的域**）⇒ 我方不代认、不擅改。
