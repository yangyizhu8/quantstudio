# F2-A 方案：QS_FILL_AUDIT 增计 `delta_below_one_lot`（框架层最小改进）

> 状态：**方案稿（待审）** — 六步流水线第 1 步
> 提出：2026-09-23；提出方：ou_reversal_csi300_10 策略线；审核方：总调度/审核席
> 关联铁律：框架层改动六步流水线；框架问题立即解决；性能优化不得改变引擎行为（本次为**诊断可观测性**改进，非性能优化）

## 1. 问题定义（已实测复现）

**现象**：2025-07-07 实测 QS_FILL_AUDIT 行
```
QS_REBALANCE_AUDIT ... selected=10 tradable=10 sell_submitted=0 buy_submitted=10
QS_FILL_AUDIT date=2025-07-07 sell_filled=0 buy_filled=5 sell_rejected=0 buy_rejected=0
```
**submitted=10 与 filled=5 / rejected=0 不闭合，5 单无从对账。**

**根因（已定位到行）**：`order_target_value` 在本地为**接线层包装**（P-D12，`ptrade_api.py:2675-2718`）。
当 `_qs_split_order` 返回空列表（`ptrade_api.py:2965-2998`：`value/px < 100` 股或 `cash_avail/px < 100` 股）时，
走 `_qs_noop_target`（`ptrade_api.py:2666-2672`）返回 no-op Order（`status='rejected', reason='delta_below_one_lot'`），
**不经引擎 `_finalize_immediate`**（`backtest_engine.py:775-791`，拒单集中采集唯一出口）→ 不进入 `_day_rejections` → 不计入 QS_FILL_AUDIT。

**影响面**：
- 「计划 vs 实际」机械对齐失效（审计行自述用途，`backtest_engine.py:796-803`）；
- 后续所有策略的 R5 诊断可能漏判「下单未成交」成因，归因指向错误方向（本策 F1 发现即因该盲区被掩盖，靠单次下单探针才暴露）。

**已文档化边界**：P-D12 设计文档（`docs/pd12-target-value-semantics-design.md` §2.3 / §T6）把该路径记为 B3
「0 股委托显式告警（`QS_ZERO_ORDER` log.warning）」——**信息未丢失，但未进入标准化计数器**。
本方案不改变 B3 的告警，只补计数上报，使对账闭合。

## 2. 改动范围（最小化）

| 项 | 内容 |
| --- | --- |
| 改动文件 | `quantstudio/backtest/ptrade_api.py`（**单文件**） |
| 改动点 | `_qs_wire_order_target_value` 中 `if not orders:` 分支（约 2711-2714） |
| 改动内容 | 在 `_qs_warn_zero_order(...)` 之后、`return _qs_noop_target(...)` 之前，把 `(code, direction, 'delta_below_one_lot')` 追加到引擎的 `_day_rejections`（与 `_finalize_immediate` 同构），失败静默跳过 |
| 明确不改 | 引擎 `_immediate_execute` / `_finalize_immediate` / `_emit_fill_audit`（零改动）；`_qs_split_order`（零改动）；订单/资金/持仓/撮合语义（零改动）；P-D12 B3 的 `QS_ZERO_ORDER` 告警（保留） |

**语义论证（行为等价性）**：`_day_rejections` 仅被 `_emit_fill_audit` 消费，用于生成日志行的计数与明细；
不参与任何订单、资金、持仓、估值或信号计算。故本改动**只增加日志可观测性，不改变任何可观察回测结果**。

## 3. 影响面

- 正向：QS_FILL_AUDIT 对账闭合（`submitted == filled + rejected`）；R5 归因可区分「太贵买不起 / 现金不足 / 涨跌停 / 停牌」。
- 风险：接线层在引擎未 attach（构造期/单测）时引用 `_api._engine` 可能为 None → 以 try/except 静默跳过，行为与改动前等价。
- 兼容：`_day_rejections` 元素格式 `(code, direction, reason)` 不变；`rejected_detail` 明细可能新增条目（预期变化，非回归）。

## 4. 验收标准

| # | 判据 |
| --- | --- |
| V1 | 对账闭合等式：代表性场景下 `buy_submitted == buy_filled + buy_rejected`，且 `rejected_detail` 含 `delta_below_one_lot` |
| V2 | 6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量（三层止损））重转 + 回测结果**逐项一致**（净值/成交/持仓/指标） |
| V3 | 相关测试套件全绿（含 `tests/test_pd12_target_semantics.py`、`tests/test_ptrade_contract_compliance.py`） |
| V4 | 矩阵 reverify：**无需**（不触 `_QS_*` wrapper 模板串）；CI contract-gate 不受影响 |
| V5 | 回归证据：改动前后同一策略同一窗口的 `daily_stats.csv` / `trades.csv` SHA-256 逐位一致 |

## 5. 回退条件

- 任何 V2/V5 出现可观察差异 → 立即回退（`git revert` 单文件改动）；
- 接线层上报引发异常或性能退化（每单一次 list append，量级可忽略）→ 回退。

## 6. 六步流水线排程

1. **方案**（本文）→ 2. **审计**（总调度/审核席）→ 3. **实施**（单文件）→ 4. **验收**（V1-V5 证据落 `docs/evidence/`）→ 5. **用户确认** → 6. **双仓库推送**（`git push origin` + 双远程 HEAD 一致核验）。

**与 F1-B 并行**：F1-B（策略侧候选递补）落地后，递补循环中的 no-op 尝试同样被本改进计入，对账保持闭合。
