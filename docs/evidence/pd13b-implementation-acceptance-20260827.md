# P-D13b 实施与验收证据：C4a 停牌撤单保真 + C4b 退市强平保真（2026-08-27）

- 流水线：Step 1 实施版设计（引用 WP-C 原 §2.4）→ Step 2 审计通过（两验收条件并入 T9/T11）→ Step 3 实施 → **Step 4 验收**
- 解锁前置：backtest_engine 他线 11 hunk 已 d41c0ed 收敛（工作树零 M）；fidelity_config 已 tracked（P-D13c 连带解除）
- 回退点：`ed03bf9b8264cd3e3eb9d516cf98876f54deca3a`

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `quantstudio/backtest/fidelity_config.py` | +`fidelity_halt_reject` / `fidelity_delist_force_close`（默认 False，opt-in） |
| `quantstudio/backtest/backtest_engine.py` | C4a：`_immediate_execute` L657 价格检查后插入 halt 检查（`_api._fidelity` 防护式读，volume==0/suspendFlag==1 → `reason='halted'`）；C4b：run 日循环 L544 后插入 `_apply_delist_force_close`（无行情持仓 → 复用 `_execute_sell` + `fidelity_delist` 审计行）；+`_lookup_curr_row` helper |
| `tests/test_pd13b_c4_fidelity.py`（新） | T9-T12 |

## 2. 验收结果

| 项 | 判据 | 实测 |
|---|---|---|
| T9（验收①） | halt 开 → 停牌拒单 `reason='halted'`，**区分于 no_price/limit** | ✅ PASS（reason=='halted' 且 != no_price） |
| T10 | 默认关不拒单 | ✅ PASS |
| T11（验收②） | delist 开 → 无行情持仓强平 + **审计行 fidelity_delist 标记**（区分普通卖出） | ✅ PASS（最后价 9.5 强平 + `fidelity_delist` in log） |
| T12 | 默认关不强平 | ✅ PASS |
| 回归 | 默认关零行为变化（engine 改动黄金等价） | ✅ **135/135**（fill_audit/d3/pd12/compliance/pd14b 全绿） |
| 6 策略重转 | engine 改动不触转换 | ✅ api_portability 全 PASS |

## 3. 关键决策落实

- D1 `_api._fidelity` 防护式读取（run 已 import `_api`；异常/None 默认关）；
- D2 默认 False（opt-in，P-D9）；
- D3 halt 检查位次 = 价格检查后、无指令保护前；
- D4 强平复用 `_execute_sell`（不新造卖出路径）；
- D5 开关进 tracked fidelity_config。

## 4. 平台对拍

opt-in 开关暂缓（WP-D 后统一窗口）——平台侧对拍待后续安排（不是本 WP 阻塞项）。

## 5. 回退

- engine 两插入 + helper 定向 restore（回退点 `ed03bf9`）；
- fidelity 两字段回滚；
- 默认关 → 回退无行为残留。