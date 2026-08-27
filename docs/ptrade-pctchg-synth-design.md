# 框架修复方案 v2（终稿）：PTrade 转换管线双端对齐（pctChg 合成 / 裁剪防御 / 列名双形态 / 可负担钳制）

- 作者：dsh（断板反包策略平台冒烟取证，2026-08-27）
- 状态：方案 v2（六步流水线第 1 步，ZCode 审计 M1-M3 已修入）→ 待审计通过后实施
- 关联：issue_registry D4-S7、output/generated_strategies/broken_board_reversal/PTRADE_SMOKE_ISSUES.md（问题 #1-#5）
- 平台实证来源：断板反包策略转换产物 `断板反包策略_ptrade_fixed.py` 三轮平台运行（2026-08-27 10:39 / 10:58），平台导出明细（交易详情 20260827112511.csv、持仓明细 20260827112505.csv）

## 0. 审计修入记录（ZCode 2026-08-27）

| 项 | 审计要求 | 方案 v2 落实 |
|---|---|---|
| M1 | #5 钳制系数 0.995 → 1.0（与本地 D4-S6 同构，ptrade_api.py:2694 buffer=1.0）| §2.4 改 `budget=min(value, cash_avail)`（无折扣）；双端同 1.0；费用余量日后需改则双端同步+回归，禁止单侧 |
| M2 | #5 平台现金取数 fail-open 语义显式声明 | §2.4 补：取数失败/接口不存在 → 钳制不生效（保持平台现状全额下单）+ 一次性告警日志（`QS_CASH_AVAIL_UNAVAILABLE` 只打一次），禁止取不到就禁下单 / 静默逐笔告警 |
| M3 | #4 现在裁定模板侧（不保留"二选一"）| §2.4/#4：模板侧透传/合成（产物自包含，与平台端行为同构）；**引擎零触碰**，不开"改本地引擎 get_history 补偿"口子 |
| 补充 | T1C：合成 pctChg 与本地 DuckDB pctChg 列逐值一致（容差 1e-6）| §4 验收新增 T1C |
| 补充 | 回退条件写具体基线 commit | §5 回退：source_import 基线 = **d421cef**（D4-S6 推送后状态，已核验干净）；平台产物 SHA 8b08b8e1 在案 |

## 1. 问题定义（五个缺陷，均框架层转换管线/接线层）

| # | 缺陷 | 现象（平台实证） | 根因（代码级） |
|---|---|---|---|
| 1 | pctChg 平台无字段 | get_history `invalid field ['pctChg']`，handle_data 每日失败 | 策略 FIELDS 含 pctChg（本地列）；模板 `_QS_FIELD_TO_PTRADE` 未映射；平台合法字段无 pctChg |
| 2 | percent API 裁剪未注入 | 产物加载 `NameError: order_target_percent` | `_QS_ORDER_SPLIT_EXT` 无条件赋值引用被裁剪未注入的 API |
| 3 | 平台 DataFrame 列名未映射 | arr_pct/arr_prec/arr_amt 空 → IndexError | `_qs_to_dataframe` DataFrame 分支缺失 `_QS_COL_TO_LOCAL` rename |
| 4 | 产物×本地引擎 mkt trade_date | 本地 smoke 0 信号（mkt_td 空早退） | 转换产物请求剔除 trade_date 后本地 close-only DataFrame 无合成源（平台端有 DateIndex 不受影响） |
| 5 | 平台可透支成交 vs 本地现金硬约束 | 07-15 平台成交 1200×41.6=49,920 > 本地 cash 48,823；07-31 成交 49,932 > 本地 cash 45,439 | 平台 order_target_value 按目标市值全额下单不钳制现金；本地接线层 D4-S6 cash_avail 钳制 + 引擎单手回退 → 语义分叉 |

## 2. 改动范围（仅框架层，策略源码零改动）

### 落点 A：`quantstudio/strategy_compiler/source_import.py` 模板（重转产物生效）

1. **#1 pctChg 合成**（与 trade_date 合成同机制）：
   - `_QS_SYNTHETIC_FIELDS` 增 `'pctChg'`（请求剔除）
   - `_qs_synthesize_pct_chg(df)`：用 close/preClose 合成 `pctChg=(close/preClose−1)×100`；仅当两基列可用且请求含 pctChg 时合成（fail-soft）
   - `_qs_to_dataframe` 挂接（与 `_qs_synthesize_trade_date` 并列）
2. **#3 列名双形态映射**：`_qs_to_dataframe` 的 ndarray 与 DataFrame **两分支统一** rename（money→amount、preclose→preClose、datetime→time）+ 合成（当前 DataFrame 分支缺失）
3. **#2 percent 裁剪防御**：`_QSOrderRefState` 的 target_percent_orig/percent_orig 赋值改防御式（存在才绑定，否则 None）
4. **#5 可负担钳制**（ZCode 定谳分支 + M1/M2 修入）：模板 `order_target_value`/`order_value` 接入 cash_avail 语义——`budget=min(value, cash_avail)`（**无折扣系数，与本地 D4-S6 buffer=1.0 同构**；费用余量日后需改则双端同步+回归）；**fail-open**：平台现金取数接口不可得/失败 → 钳制不生效（保持平台现状全额下单）+ **一次性**告警日志（`QS_CASH_AVAIL_UNAVAILABLE`，只打一次，禁止静默逐笔告警/禁止取不到禁下单）
5. **#4 本地 mkt 形态（M3 裁定模板侧）**：`_qs_synthesize_trade_date` 对无 time/datetime 列且无 DateIndex 的 DataFrame —— 请求侧含 trade_date（本地引擎场景）时**请求侧不剔除**（保留 trade_date 透传，平台端无该字段则剔除+返回侧合成）——即"透传优先、合成兜底"，模板侧自包含实现；**引擎零触碰**（不开本地 get_history 补偿口子）

### 落点 B：`quantstudio/backtest/ptrade_api.py` 本地接线层（#5 已有 D4-S6 cash_avail，无需改；#1-#3 本地不动——本地 DuckDB 有 pctChg 列、列名已是本地形态）

## 3. 受影响面（通用性验证）

- **模板落点通用**：所有经转换管线的策略产物（使用 get_history 取史 / order_target_value 下单者）——修复不改变本地语义（pctChg 本地照常、列名照常、钳制与本地同构）
- **方案 C 对应**：6 策略重转（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量）api_portability 全 PASS + 相关测试套件全绿
- **断板反包策略**：平台重转产物复跑（信号/股数/现金与本地对齐）

## 4. 验收标准

1. 单测新增（模板层）：
   - T1A：pctChg 请求剔除 + 合成（平台 DateIndex DataFrame + 小写列 → cols 含 pctChg/trade_date/amount/preClose）
   - T1B：close/preClose 合成数值正确（(6.72/6.20−1)×100=8.39%）
   - **T1C（审计补充）：合成 pctChg 与本地 DuckDB pctChg 列逐值一致（同日同码，容差 1e-6）——防 8.39 vs 0.0839 形态/符号错位**
   - T2A：percent API 未注入时 `_QSOrderRefState` 赋值不抛（None）
   - T3A：ndarray 与 DataFrame 双形态 rename 一致
   - **T4A：trade_date 透传优先/合成兜底（本地 close-only DataFrame 无时间列 → 透传请求列）**
   - **T5A（#5 钳制，M1 系数 1.0）**：cash_avail 竞对样本——目标 value=50,000、cash=48,823、px=41.6 → 双端订单量一致（本地 1100；平台钳制后同值）；budget=min(value, cash_avail) 无折扣
   - **T5B（M2 fail-open）**：cash_avail 取数不可得 → 钳制不生效（fallthrough 全额）+ 一次性 `QS_CASH_AVAIL_UNAVAILABLE` 告警（不逐笔）
2. 现有测试全绿 + P-D12 验收集（target 语义/微调跳过/拆单同构/49k 上限/delta 减仓/清仓保底/T10 差分）+ compliance 套件
3. 6 策略重转 api_portability 全 PASS（通用性铁律）
4. **断板反包平台冒烟复现**：重转产物平台跑 2025-07-31~2026-07-31——信号逐笔一致 + **07-15/07-31 现金竞对日双端订单量一致**（本地 1100/6900 = 平台钳制后 1100/6900）+ 账户现金非负（透支消除）
5. G3.5 双跑一致（本地产物）
6. 双端冒烟：平台缺口日订单序列一致

## 5. 回退条件

- 任一验收失败/回归无法归因 → source_import 恢复至基线 **d421cef**（D4-S6 推送后状态，2026-08-27 核验工作区干净一致）+ 平台产物回退至 `断板反包策略_ptrade_fixed.py`（SHA 8b08b8e1，含 #1/#2/#3 冒烟版，平台已验证可运行）
- 平台冒烟版产物保留为过渡（其行为=模板修复后行为子集，除 #5 钳制与 #4 透传外）

## 6. 实施后登记

- issue_registry D4-S7：方案已产出待审计 → repairing → closed（修复+回归证据）
- 三件套（总日历/闭环清单/技术债清单）随实施进档

## 7. 已核验事实清单（审计+实施前置）

- 平台合法字段集（日志实证）：{volume, high_limit, preclose, high, low, money, unlimited, low_limit, price, close, is_open, open}——无 pctChg ✅
- 模板 `_QS_FIELD_TO_PTRADE`（source_import L189-192）：仅 amount/preClose ✅
- `_QS_SYNTHETIC_FIELDS={'trade_date'}` + `_qs_synthesize_trade_date`（P 先例，请求剔除+返回合成）✅
- `_qs_to_dataframe` 两处（L159 主链/L264 扩展链）DataFrame 分支缺 rename（平台实证）✅
- `_QSOrderRefState` 无条件赋值（source_import L386-387）✅
- 平台三问定谳：②请求=成交（1200）✅ ③买卖闭环（1200↔1200 归零）✅ ①成交额 > 本地 cash（49,920>48,823；49,932>45,439）→ 平台可透支 ✅
- 本地接线层 D4-S6 cash_avail 钳制已接通（ptrade_api L2426/2465）→ #5 模板侧同构即可 ✅
- 产物冒烟版（SHA 8b08b8e1）三处修复平台实证通过（三轮无 ERROR + 信号一致）✅