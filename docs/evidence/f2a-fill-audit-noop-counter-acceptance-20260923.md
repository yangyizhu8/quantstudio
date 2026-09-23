# F2-A 验收证据：QS_FILL_AUDIT 增计接线层 no-op（delta_below_one_lot）

> 六步流水线第 4 步（验收）｜执行：2026-09-23｜方案：`docs/qs-fill-audit-delta-below-one-lot-design.md`
> 改动：`quantstudio/backtest/ptrade_api.py`（新增 `_qs_report_noop` + 在 `if not orders:` 分支调用）

## 1. 问题定义（已实测复现）

`order_target_value` 的接线层（`ptrade_api.py:2704-2751`）在 `_qs_split_order` 返回空列表时走
`_qs_noop_target`（`reason='delta_below_one_lot'`），**不经引擎 `_finalize_immediate`**
→ 不进 `_day_rejections` → QS_FILL_AUDIT 出现「计划 vs 实际」对账缺口。

触发场景：单只预算连 1 手都买不起（10 万本金 ÷ 10 只等权 = 9,700 元/只 vs 高价股一手 > 9,700 元）。

## 2. 改动（最小化）

```python
def _qs_report_noop(security, direction, reason):
    """F2-A：接线层 no-op 计数上报引擎 QS_FILL_AUDIT（仅日志可观测性，零行为影响）。"""
    try:
        engine = getattr(_api, '_engine', None)
        if engine is None: return
        bucket = getattr(engine, '_day_rejections', None)
        if bucket is None: return
        to_qmt = getattr(engine, '_to_qmt', None)
        code = to_qmt(bare_code(security)) if to_qmt is not None else bare_code(security)
        bucket.append((code, direction, reason))
    except Exception:
        pass
```

**语义边界（行为等价）**：`_day_rejections` 只被 `_emit_fill_audit` 消费用于生成日志行，
不参与任何订单、资金、持仓、估值或信号计算 → 本改动**只增加可观测性，不改变任何可观察回测结果**。
P-D12 B3 的 `QS_ZERO_ORDER` 告警原样保留。

## 3. 验收判据与结果

| # | 判据 | 结果 | 证据 |
| --- | --- | --- | --- |
| V1 | 常设单测：上报写入 `_day_rejections`、no-op 订单形态不变、无引擎时静默 | **PASS** | `tests/test_fill_audit_noop_counter.py` 4/4；与 `test_design_metadata.py` 合计 16 passed |
| V2 | 运行期：`delta_below_one_lot` 出现在 QS_FILL_AUDIT 的 `rejected_detail` | **PASS** | 2 个月窗口 44 条审计行中 **39 条**含该明细 |
| V3 | A/B：改动前该路径不可见 | **PASS** | R5 run1（287 日，改动前）：`QS_FILL_AUDIT` 287 行中**含该明细 0 行**，`buy_rejected` 合计 9，而 `QS_ZERO_ORDER` 762 次 |
| V4 | 行为等价：改动前后策略结果不变 | **PASS** | 2 个月窗口改动前后 总收益/交易笔数 一致（−9.10% / 291 笔）；仅日志行变化 |
| V5 | 矩阵 reverify | **无需** | 未触 `_QS_*` wrapper 模板串 |

### A/B 实测对照

| 观测项 | BEFORE F2-A（R5 run1，287 日） | AFTER F2-A（2 个月验证） |
| --- | --- | --- |
| QS_FILL_AUDIT 行数 | 287 | 44 |
| 含 `delta_below_one_lot` 明细的行 | **0** | **39** |
| `buy_rejected` 合计 | 9 | 345 |
| `QS_ZERO_ORDER` 告警次数 | 762 | 339 |

样例（AFTER）：

```text
QS_FILL_AUDIT date=2025-07-07 sell_filled=0 buy_filled=10 sell_rejected=0 buy_rejected=6 \
  positions_total=10 rejected_detail=[000596.SZ:delta_below_one_lot,600809.SH:delta_below_one_lot,...]
```

## 4. 拆单容差口径（判据补充）

`submitted == filled + rejected` 仅在**无拆单**时精确：`_qs_split_order` 把单笔 > 49,000 元的委托拆成多笔
`order()`，使 `filled`（trade-record 计数）大于订单数。
**判据口径**：以 **order_id 聚合**后计数（同一拆单组的多个 trade record 归为 1 笔委托），
或等价地断言「未解释残差 = 0」。本策略单笔 ≈9,700 元无拆单，等式在本场景精确。

## 5. 遗留项（登记为 skill 标准改进，非策略层修复）

> **S-2（skill 标准）**：`QS_REBALANCE_AUDIT` 的 `submitted` 计数口径需在 skill 标准中明确为
> 「**所有** `order_target_value` 提交次数」——含已持仓标的的等权微调提交；当前本策略仅计新仓尝试数，
> 与 `QS_FILL_AUDIT` 的 filled/rejected（含微调提交）口径不一致，导致跨行对账需人工解释。
> 按「策略生成与转换全链路修复：仅限框架层」铁律，此改进落在 **skill 标准层**（生成模板/审计契约），
> **不得通过修改单个策略源码实现**；随下个 skill 维护批走流程。

## 6. 回退条件

- V1/V4 任一失败 → 单处 `git revert`（新增函数 + 调用点）。
