# 影响面附表：持仓视图契约修复（18 文件逐个结论）

- 方法：`agent_workspace/dividend_defense_smallcap_5d/impact_scan.py` 机械提取
  （扫描 `quantstudio/backtest/strategies/*.py` 中 `portfolio.positions` 访问点，
  取访问点 + 后续 4 行窗口内的字段命中作为**作用域证据**；文件级字段命中仅作参考，不用于归类）。
- 归类（三分矩阵）：
  - **甲** 引擎属性读者：在 `portfolio.positions` 上读 `.volume`/`.can_sell`（引擎 dataclass 字段）
  - **乙** PTrade 契约字段读者：在 `portfolio.positions` 上读 `amount`/`enable_amount`/`cost_basis`/`last_sale_price`/`sid`/`market_value` → **修复前静默错**
  - **丙** 函数路径读者：经 `get_position()`/`get_positions()` → 类型本正确，但受 §四 两项变更影响者**连带作废**
  - **键** 仅用键（`in` / `.keys()` / 迭代 / `len()`）→ 键语义未变，**不受影响**
- 纪律：不默认安全；每行给出结论。

## 一、18 个引用 `portfolio.positions` 的文件

| # | 文件 | 访问点 | 作用域字段证据 | 归类 | 结论 |
|---|---|---|---|---|---|
| 1 | ETF动量.py | L86 / L95 / L100 | — | 键 | 不受影响 |
| 2 | ETF平滑动量轮动.py | L78 / L81 / L82 / L86 / L91 / L95 | —（L82 读 `.value`） | 键 + **独立缺陷** | 本修复不影响；**`.value` 在任一 Position 类型上都不存在** → 独立缺陷，另案登记 |
| 3 | ETF轮动.py | L50 | — | 键 | 不受影响 |
| 4 | SG-MS-PEG-HL防守型小市值成长交叉验证选股策略.py | L490 / L491 / L555 / L557 / L590 | L557 `volume` | 兼容读取者 | **不受影响**：`_pos_amount`(:573) 为 `amount → volume` 双形态兜底 |
| 5 | ashare_manual_pool_2d_momentum_top2_quantstudio.py | L66 / L91 | L91 `market_value` | **乙** | 修复前 `getattr(position,'market_value',0)` 恒 0（静默漏算持仓市值）→ 修复后取真值 = **正确性改善** |
| 6 | etf_theme_rotation_quantstudio.py | 见扫描 | `amount` | 键 | 不受影响 |
| 7 | fall_reversal_quantstudio.py | 见扫描 | — | 键 | 不受影响 |
| 8 | tech_etf_mvo_rotation_quantstudio.py | 见扫描 | `amount` | 键 | 不受影响 |
| 9 | vol_regime_mom_rev_quantstudio.py | L175（warning 分支） | — | 键（带 try/except） | 不受影响 |
| 10 | weekly_smallcap_growth_momentum_10_quantstudio.py | 见扫描 | —（函数路径） | 键 | 不受影响 |
| 11 | 二八轮动策略.py | L69 | — | 键 | 不受影响 |
| 12 | 低流动性溢价换手尾部极值多头.py | L375 / L443 | — | 键 | 不受影响 |
| 13 | 周频小市值成长动量（三层止损）.py | L434 / L442 / L496 / L510 | — | 键 | 不受影响 |
| 14 | 小市值策略2.py | L104 / L153 / L302 | L302 `amount, sid` | **乙** | 修复前 `pos.sid`/`pos.amount` MISSING→0 → 修复后正确 = **正确性改善**；**存量结论作废** |
| 15 | 小市值策略ptrade.py | L60 / L65 / L71 / L78 | L65 `amount, sid` | **乙** | 同上；**存量结论作废** |
| 16 | 恐慌抄底事件驱动逆向策略.py | L247 / L276 / L332 / L365 | —（仅取 dict 键） | 键 | 不受影响（R5 实证 41 回合一致佐证） |
| 17 | 断板反包策略.py | L113 | `market_value` | **乙 + 丙** | 修复前 `market_value` 恒 0（正确性改善）；并含内联 P-D11 wrapper（L1381-1488）→ `enable_amount` 变更连带作废 |
| 18 | 连板梯队龙头打板套利策略.py | L103 | `market_value` | **乙** | 修复前 `market_value` 恒 0 → 正确性改善 |

**甲类（引擎属性读者）**：本批 18 文件中**未发现**在 `portfolio.positions` 上读 `.volume`/`.can_sell` 的站点
（`SG-MS-PEG-HL` 的 `volume` 命中来自双形态兜底 helper，归「兼容读取者」）→ 甲类计数 = 0。

## 二、丙类扩展：函数路径读者（不在上述 18 内，但读 §四 变更字段）

| 文件 | 访问点 | 读取方式 | 受 §四 变更影响 | 结论 |
|---|---|---|---|---|
| smallcap_overnight_scalp_7_quantstudio.py | L426 / L462 / L717 | `get_position(...).enable_amount` / `.last_sale_price` | 是 | **连带作废**（且本身是乙类实锤失效者） |
| first_cover_event_daily_quantstudio.py | L2780 / L3097 | `_attr_number(pos, ("enable_amount","closeable_amount","can_sell"), amount)` | 是（兜底链首项由 0 变为真值） | **连带作废** |
| 双均线策略.py | L35 / L42 | `get_position(g.security).amount / .enable_amount` | 是（T+1 当日 enable_amount 由 volume 变 0） | **连带作废**（方向 = 收紧本地↔平台订单流差） |
| 断板反包策略.py | L897 + 内联 wrapper L1453 | `get_position(...).amount` / wrapper `enable_amount` 透传 | 是 | **连带作废**（已计入上表 #17） |

## 三、既有 workaround 文化（本修复的动机旁证）

同一缺陷在策略侧被三种不同方式绕过，说明契约从未真正成立：
- `SG-MS-PEG-HL`：`amount or volume` 双形态兜底；
- `first_cover`：`("enable_amount","closeable_amount","can_sell")` 三选一兜底；
- `ashare_manual_pool_2d` / `连板梯队` / `断板反包`：`getattr(position,'market_value',0)` 静默取 0；
- `smallcap_overnight`：只读 `amount`，无兜底 → **直接失效（只买不卖）**。

修复后契约唯一，四类写法全部回归同义。

## 四、Backlog（命名具体 · 禁用「待核验」收口）

1. **已失效/受影响发布策略的存量回测结论作废并重跑（6 文件）**：
   `smallcap_overnight_scalp_7_quantstudio.py` / `小市值策略ptrade.py` / `小市值策略2.py` /
   `断板反包策略.py` / `first_cover_event_daily_quantstudio.py` / `双均线策略.py`
2. **乙类正确性改善者（3 文件）的结论需重验**：
   `ashare_manual_pool_2d_momentum_top2_quantstudio.py` / `连板梯队龙头打板套利策略.py` / `断板反包策略.py`
3. **独立缺陷（另案）**：`ETF平滑动量轮动.py:82` 读 `context.portfolio.positions[...].value` —— `.value` 在
   引擎 `Position` 与 PTrade `Position` 上**均不存在**，与本次修复无关，按独立缺陷立项。
4. **文档缺口**：`check_fund_matrix.py` 覆盖范围与 AGENTS.md 铁律措辞的口径差。
5. **市值口径观察**：`Account.market_value`（成本口径）与 `Account.total_asset` 调用方审计；
   `Portfolio.market_value` 的 `except: pass` 静默回落成本口径快照。
