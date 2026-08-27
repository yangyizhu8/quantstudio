# 框架修复方案 v3（终稿）：P-D12 接线层拆单换价缺陷修复

- 作者：dsh（broken_board_reversal R5 取证）
- 日期：2026-08-27
- 状态：**已批准实施**（ZCode 两轮审计通过：第二轮 R1/R2 必改 + R3 补充单测已并入；预批 cash 接口）
- 六步流水线：方案 ✅ 审计 ✅（本文件=方案 v3 终稿）→ 实施（下一步）→ 验收 → 用户确认 → 双仓库推送
- 关联：D4-S6（issue_registry）、docs/pd12-target-value-semantics-design.md（P-D12 原设计）、docs/bbrev-pd12-split-px-design.md（v1 源）

## 0. 审计结论链

| 轮次 | 结论 | 并入 |
|---|---|---|
| 第一轮（ZCode） | 方案 C 有条件通过 | 必改1（②层取价源）/ 必改2（order_value 双落点）/ 必改3（cash_avail 接通+buffer 声明）→ v2 |
| 第二轮（ZCode） | 有条件通过 | **R1**（buffer 裁定改 1.0）/ **R2**（§3 对比口径修正）/ **R3**（新增第 3 单测）→ v3 终稿 |
| 预批 | cash 接口 | `_api._engine.account.cash` 只读（免实施期 mini-audit，禁其他私有路径） |

## 1. 问题定义（两轮审计核验通过，v1 保留）

`order_target_value`/`order_value` → 接线层 `_qs_wire_order_target_value`/`_qs_wire_order_value` → `_qs_split_order`
用**统一链①层前收 px（T-1 前复权收盘）**换算股数 → 引擎按**当日收盘**成交。相邻日误差小；**春节 11 天缺口
跳空 +6.5%~8.4%** 时股数按低价核算高价成交 → 超支/拒单（实证：600821 按 6.20 核 8000 股 @6.72 成交 53,760
超目标 7.5%；000767 13600股@3.60=48,960 > 余现金 46,220 → insufficient_cash 拒单）。

错误**不在引擎**（插桩实证 `_immediate_execute` 当日价 6.72/3.60 正确），在 **P-D12 接线层拆单换算价**。

## 2. 改动范围（仅两文件，引擎零触碰）

- `quantstudio/backtest/ptrade_api.py`（接线层：`_qs_wire_order_target_value` / `_qs_wire_order_value` / `_qs_split_order` / 常量）
- `quantstudio/strategy_compiler/source_import.py`（`_QS_ORDER_SPLIT_EXT` 同构模板：`order_target_value` / `order_value` / `_qs_split_order` / 常量）
- **引擎 backtest_engine.py 零改动**；策略文件零改动（断板反包策略的显式股数自保版保持现状，修复落定后回归验证）

## 3. 实施要点（含两轮审计全部必改）

### 3.1 换算价源改 ② 层原语义（必改1）
`_qs_split_order` 的换算 `px` 由 `_qs_last_close_lookup`（① 层 T-1 前收）改为：
```python
px_exec = _QSPriceState.orig(security)   # = _api.current_price 原语义（日线 close=当日收盘；分钟=当前 bar）
```
- `order_target_value`（ptrade_api L2408）与 `order_value`（L2450）同步传 `px_exec`
- `px_exec<=0` → 回退原生 `target_orig`/`value_orig`（fail-open 兼容，不新增拒绝面）
- **delta 保持 ① 层**（`delta = value − amount×①层px`，P-D12 目标市值语义，`_qs_current_value` L2357 注释 PIT 理由在案）——正文显式声明该正交边界（最小改动）；新仓 amount=0 → delta=value 不受影响
- 禁止新写取价路径（不得直访 `_api._prices`/`_get_daily_data`）

### 3.2 order_value 双落点（必改2）
`_qs_wire_order_value`（ptrade_api L2450-2452）+ 模板 `order_value`（source_import L530-533）同步改 ② 层，不留豁免。

### 3.3 cash_avail 接通 + buffer 裁定 1.0（必改3 + 第二轮 R1）
- 接线层调用 `_qs_split_order(security, value, px_exec, cash_avail=_api._engine.account.cash)`（预批接口，L402 `Account(cash=capital)` 实证；禁其他私有路径）
- **`_QS_SPLIT_PX_BUFFER` = 0.95 → 1.0**（双端常量同步改，同构保持）。裁定理由（第二轮 R1）：cash_avail 接通后 use_buffer 首次真生效，0.95 会在多笔拆单引入 5% 系统性低配（超出修复范围的新行为）；换算价=成交价后成本核算精确，可负担性由引擎 `_execute_buy` 既有单手回退兜底 → buffer 无存在必要 → 1.0（纯增益）
- 注释声明：价差已消除，buffer 1.0 仅兜多笔竞对边角

### 3.4 对比口径修正（第二轮 R2）
换算基准从 T-1 前收改为当日收盘，作用于**所有**经接线层的成交日（几乎每个成交日），非仅长假缺口日。验收对比口径改为：
- **不经接线层** order_target_value/order_value 的路径：**零漂移**（等价性铁律）
- **经接线层者**：差异 = 股数按真实成交价核算的校正，**逐笔归因列出**（含春节缺口样本 600821/000767）
- 禁止"仅跳空日切换 ② 层"的条件逻辑（会诱导错误实现）

## 4. 验收标准（§4 + R3 第三单测）

1. 单测新增 3 例：
   - T1：春节缺口日 order_target_value 换算=当日收盘、成本 ≤ 目标×1.005
   - T2：多笔拆单现金竞对不拒单（cash_avail 生效）
   - **T3（R3 新增）**：存量仓加减仓样本——跳空日加仓，验证 `delta(①层) ÷ px_exec(②层)` 换算后总市值 ≈ 目标（含费容差）；减仓路径不受影响（走原生）回归确认
2. 现有测试全绿 + P-D12 验收集（target 语义/微调跳过/拆单同构/49k 上限/delta 减仓/清仓保底/T10 差分）
3. source_import 模板：`_QS_ORDER_SPLIT_EXT` 段逐行 diff 静态核对 + 拆单同构用例重跑（双端订单序列一致）
4. broken_board_reversal 全量重跑：拒单=0、600821/000767 成本 ≤ 目标×1.005、G3.5 双跑一致（回归 order_target_value 后）
5. §5 十八策略逐个重跑对比归因（经接线层=股数校正逐笔归因；不经=零漂移）
6. 双端冒烟：春节缺口日订单序列一致

## 5. 受影响策略清单（18 个，逐笔归因）

fall_reversal / vol_regime_mom_rev / ETF轮动 / ETF动量 / etf_theme_rotation / ashare_manual_pool_2d_momentum_top2 /
CANSLIM突破成长选股策略 / ETF平滑动量轮动 / weekly_smallcap_growth_momentum_10 / smallcap_overnight_scalp_7 /
tech_etf_mvo_rotation / bbi_etf_rotation / 小市值策略2 / 小市值策略ptrade / 周频小市值成长动量（三层止损） /
二八轮动策略 / 双均线策略 / 首次覆盖事件驱动策略

（`order_target_value(code,0)` 清仓不拆单不经换算 → 不在差异范围，仅列入清单保险核验）

## 6. 回退条件

- 任一验收失败/回归无法归因 → `git checkout 8e543fd -- quantstudio/backtest/ptrade_api.py quantstudio/strategy_compiler/source_import.py`
  （P-D12 提交 = 本改动基线，已核验存在）
- 策略侧断板反包策略显式股数自保版（已发布 SHA c51e4cfd）保持可用直至修复落定重跑

## 7. 实施后登记（跨任务调度三件套）

- D4-S6 issue_registry：方案已产出待审计 → repairing → closed（修复+回归证据）
- AGENTS.md 新铁律：三件套（总日历/闭环清单/技术债清单）随实施推进入档
- HANDOFF_SCHEDULE.md 更新 D4-S6 状态

## 8. 已核验事实清单（审计+实施前置）

- L2394/L2408：`order_target_value` 接线用 ① 层 ✅
- L2450-2452：`order_value` 同缺陷第二落点 ✅
- L2464/2466：模块级绑定经接线层 ✅
- L2575-2588：`_QSPriceState.orig = _api.current_price`（②层）✅
- L2641：`_QS_SPLIT_PX_BUFFER = 0.95`；source_import L369 同构副本 ✅
- L2662-2690：`_qs_split_order(security, value, px, cash_avail=None)` cash_avail 死参数（L2419/2452 未传）✅
- backtest_engine L402：`self.account = Account(cash=capital)` → 预批接口 `_api._engine.account.cash` ✅
- source_import L389/418/483/532：模板同构落点 ✅
- 回退点 `8e543fd`（P-D12 提交）✅
- 取证产物：r5_probe_debug_stderr.log（ORDER版 6.72 实证）/ r5_probe_clamp_stderr.log（CLAMP版 DIAG+XPROBE 根因实证）/ strategy_clamp_probe.py ✅