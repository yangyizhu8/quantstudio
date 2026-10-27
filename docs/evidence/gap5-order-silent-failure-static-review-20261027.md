---
title: 5 笔「提交未成交」静态审读取证 —— order() 静默失败分支清单与根因锁定
date: 2026-10-27
task: A3（总调度 2026-10-27 批准：静态审读 order() 链路，替代/并行于 dev 的 QS_ORDER_RESULT 插桩）
knowledge_refill: [命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-27）—— 取证口径铁律（日志日期=T、策略 previous_date=T-1，须 −1 换算 + 双向校验）
---

# 一、结论摘要（先给结论）

| 笔 | T 日 | 根因 | 定谳 |
| --- | --- | --- | --- |
| **600452** | 2026-03-02 | 引擎 `is_price_limit_blocked` 判定 **涨停阻断** ⇒ `Order(status=rejected, reason=limit_up_blocked)` | ✅ **已定谳** |
| 600227 | 2026-03-16 | 等分预算后单笔金额不足 1 手 ⇒ `round_to_lot` 取整 0 ⇒ `_execute_buy` 返回 0（**完全静默**） | ⚠ 机制锁定，待验 |
| 002821 | 2026-04-01 | 同上 | ⚠ 机制锁定，待验 |
| 001696 | 2026-06-09 | 同上 | ⚠ 机制锁定，待验 |
| 600968 | 2026-03-03 | 同上（另叠加现金仅 56%） | ⚠ 机制锁定，待验 |

**静默事实**：引擎拒单/未成交分支**全部无 INFO 级日志** ⇒ 日志层不可见（实测 8 个关键词命中数全为 0）⇒ 此前归因「未确证」的根本原因是**框架层缺审计**，而非数据不足。

# 二、审读范围与依据（源码行号）

| 位置 | 内容 |
| --- | --- |
| `quantstudio/backtest/ptrade_api.py:1352` | `order()` → 委派 `self._engine._immediate_execute(...)` |
| `quantstudio/backtest/ptrade_api.py:1389` | `order_value()` → 同链路（**本策略实际走此路径**，策略 L237） |
| `quantstudio/backtest/backtest_engine.py:714` | `_immediate_execute` 主体 |
| `quantstudio/backtest/backtest_engine.py:1153` | `_execute_buy` 主体（资金/取整/扣款） |
| `quantstudio/backtest/backtest_engine.py:1296` | `_get_pct_chg`（优先 `pctChg` 列，回退 `(close-preClose)/preClose`） |
| `quantstudio/backtest/libs/shared_ashare_rules.py:25` | `is_price_limit_blocked`（买入：`pct_chg >= limit - 0.001`） |
| `output/generated_strategies/first_board_high_open_1to2/strategy.py:229/234/237` | 分配额与下单调用 |

# 三、失败分支清单（穷举，附静默性判定）

| # | 分支 | 触发条件 | 日志级别 | 静默 | 本次是否命中 |
| --- | --- | --- | --- | --- | --- |
| 1 | `no_price` | 取价 ≤ 0 | 无 | **是** | ✗ 排除（09:31 bar 存在且有价） |
| 2 | `halted` | bar 成交量=0 / `suspendFlag=1` | 无 | **是** | ✗ 排除（**默认关** opt-in `fidelity_halt_reject`） |
| 3 | `no_instruction` | 无指令 | 无 | **是** | ✗ 排除（策略传 value） |
| 4 | `limit_up_blocked` | `pct_chg ≥ 涨停比例−0.001` | `logger.debug` | **是** | ✅ **600452 命中** |
| 5 | `limit_down_blocked` | 卖出跌停 | `logger.debug` | **是** | ✗ 排除（本次均为买入） |
| 6 | `target_vol ≤ 0` | 取整后不足 1 手 | **无** | **是（最隐蔽）** | ⚠ **4 笔待验** |
| 7 | 资金不足 | `total_cost > cash` 且减一手仍不足 | `logger.warning`「下单失败」 | 否 | ✗ 排除（实测命中 **0**） |

> **判定**：7 个分支中 6 个在默认日志级别下**完全不可见** ⇒ 任何「提交了但没成交」的归因，**静态排查必然止步于此** ⇒ 必须有引擎级审计（`QS_ORDER_RESULT`）才能闭合。

# 四、600452 定谳链（决定性 ✓）

1. T=2026-03-02；09:31 bar：开 14.30 / 高 14.61 / **收 14.61**
2. 当日涨停价 = `round(preClose×1.1, 2)` = `round(12.94×1.1,2)` = **14.61** ⇒ **收 = 涨停价**
3. `_get_pct_chg` ⇒ `(close−preClose)/preClose` = **10.02%**（`pctChg` 列若存在亦同值，因 close 已等于涨停价 ⇒ 结论稳健）
4. `is_price_limit_blocked(code, 1, 0.1002)` ⇒ `0.1002 ≥ 0.10−0.001` ⇒ **True ⇒ 阻断**
5. ⇒ 引擎返回 `rejected/limit_up_blocked`，**仅 debug 日志** ⇒ 策略未读返回值 ⇒ 静默

**数据源**：`agent_workspace/r1_live_copy_20261011/quantstudio.db`（`stock_minutes` / `stock_daily`，只读）

# 五、4 笔待验：等分预算 → 取整为 0 的机制链

```
strategy.py:229  value = _available_cash(context) / len(qualified)   # 可用现金等分 N 只
strategy.py:234  if _available_cash(context) / price <= MIN_BUY_LOT_SHARES:  # 门槛用【全部现金】
strategy.py:237  order_value(code, value)                            # 实际用【1/N 分配额】
engine: _execute_buy  max_vol = int(buy_value / fill_price) → round_to_lot(·,100)
engine: L1162      if target_vol <= 0: return 0, fill_price           # 无日志 ⇒ 静默
```

**机制**：门槛检查与实际下单金额**不同源** ⇒ 策略判定「够买 1 手」用的是全部现金，实际每只只分得 `cash/N` ⇒ 当 `cash/N / price < 100` ⇒ 引擎取整为 0 ⇒ **静默无成交**。

**待验项（诚实申报）**：各该日 `len(qualified)`（合格标的数）与 `cash/N / price` 实际值尚未逐日取数核对 ⇒ 本条为**机制锁定**，非逐笔定谳。

# 六、处置建议（与 dev 立项衔接）

| 层 | 建议 | 归属 |
| --- | --- | --- |
| 框架层 | 补 `QS_ORDER_RESULT` 审计：拒单/未成交一律落 INFO 级行（含 `reason`、请求额、算得股数、可用资金）⇒ 消除静默 | **dev（已立项）** |
| 框架层 | `target_vol ≤ 0` 分支补 warning（「金额不足 1 手」）⇒ 该分支当前**连 debug 都没有** | dev（建议并入同一审计） |
| 策略层 | 门槛检查改用**实际分配额**（`value`）而非全部现金 | ⚠ **策略产物零改动**（铁律 07）⇒ 不在本线擅动；如需修须另立项 |
| 本线 | 本件作为 dev 侧实现依据回传（分支清单 + 判定式 + 静默性标注） | 本线 |

# 七、方法论沉淀


## 九、更正（2026-10-27 15:2x · 本轮逐日取数后**否证**「4 笔 = 取整为 0」）

### 9.1 逐日取数（`selected` / `tradable` / 池 N / 现金，取自 `_r5_fix.log` + 副本库）

| 码 | T 日 | 首板池 N | selected | tradable | cash_ratio | value=cash/selected | 09:30收 | 09:31开 | 09:31收 | 涨停价 | 09:31涨幅 | 算得股数(按09:31收) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 600452 | 03-02 | 58 | 1 | 1 | 1.0000 | 100,000 | 14.04 | 14.30 | **14.61** | 14.61 | **10.02%** | 6,844 |
| 600227 | 03-16 | 49 | 1 | 1 | 1.0000 | 100,000 | 4.49 | 4.47 | 4.66 | 4.81 | 6.64% | 21,459 |
| 002821 | 04-01 | 40 | 1 | 1 | 1.0000 | 100,000 | 114.60 | 114.59 | 113.00 | 121.85 | 2.01% | 884 |
| 001696 | 06-09 | 32 | 1 | 1 | 1.0000 | 100,000 | 16.41 | 16.39 | 16.66 | 17.55 | 4.45% | 6,002 |
| 600968 | 03-03 | 61 | 2 | 2 | 0.5636 | 28,180 | 5.36 | 5.34 | 5.43 | 5.54 | 7.74% | 5,189 |

> `selected` 即策略 `g.qualified_count`（三条件过滤后的合格数，**远小于首板池 N**）⇒ `value = 现金 / selected` ⇒ **每只分得几乎全部现金**。

### 9.2 否证结论（更正 §五）

| 假设 | 本轮实测 | 判定 |
| --- | --- | --- |
| 「等分预算 ⇒ 单笔不足 1 手 ⇒ `round_to_lot` 取整 0」 | 算得股数 **872 ~ 21,459 股**，全部 ≫ 100 | ❌ **否证** ✗ —— §五「机制锁定」**作废** |
| 「涨停阻断」 | 4 笔 09:31 涨幅 2.01%~7.74%，均 < 9.9% | ❌ 否证（**600452 除外**） |
| 「资金不足」 | `下单失败` warning 实测 0；且 cash_ratio 充足 | ❌ 否证 |
| 「集合竞价(09:30) bar 达涨停」 | 09:30 收全部低于涨停价（涨幅 2.75%~6.35%） | ❌ 否证 |
| 「无价 / 停牌 / 无指令」 | 09:30+09:31 bar 齐全且有价 | ❌ 否证 |

### 9.3 修訂后状态（诚实口径 ✓）

| 笔 | 状态 |
| --- | --- |
| **600452** | ✅ **定谳**：涨停阻断（09:31 收 14.61 = 涨停价 ⇒ 10.02% ≥ 9.9% ⇒ `limit_up_blocked`，仅 debug 级 ⇒ 静默） |
| 600227 / 002821 / 001696 / 600968 | ❓ **仍未定位** —— 上述 7 个失败分支（§三）**全部否证** ⇒ 拦截点**不在**已枚举的 7 个分支内 ⇒ **必须**依赖引擎级审计（`QS_ORDER_RESULT`）才能闭合 |

### 9.4 新增排除项（本轮引入）

| # | 分支 | 检验 | 判定 |
| --- | --- | --- | --- |
| 8 | 集合竞价 bar 达涨停（若撮合 curr_data 取 09:30 bar） | 5 笔 09:30 收 vs 涨停价 | ❌ 排除 |
| 9 | 门槛检查拦截（L234 `available_cash/price ≤ 100`） | 现金/价 ≫ 100 ⇒ 门槛从不触发（**与聚宽 L65 同构** ⇒ 聚宽原生语义） | ❌ 排除 |

> **教训**：§五曾以「池 N」当分母估值 ⇒ 实际分母是 `selected`（合格数）⇒ 差 1~2 个数量级 ⇒ **同类估算必须取引擎/策略真实变量**（本轮靠 `QS_REBALANCE_AUDIT selected=N` 纠正）。
> **静默失败面（Silent-Failure Surface）**：回测引擎的下单链路存在 7 个失败分支，其中 6 个在默认日志级别不可见 ⇒ 「提交 vs 成交」类归因的**可行上限**＝排除可观测分支（数据面）+ 审读源码（代码面）⇒ 剩余部分**只能**靠引擎级审计闭合。此结论已可复用（同类问题必现）。