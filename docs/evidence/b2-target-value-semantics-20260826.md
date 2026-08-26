# B2 根因证据：order_target_value 目标市值语义双端失效（E-2 / P-D12 / 2026-08-26）

- 关联：master-plan WP-B（B1/B2）；复现测试 `tests/test_b2_target_value_semantics_repro.py`（2 红 3 绿）
- 结论强度：**代码行级 + 最小复现 + 生产实证三方闭环**，缺陷层级已隔离

## 1. 现象（生产实证）

tech_etf_mvo_rotation 本地回测（20260825_220017，2026-07-01~07-31）trades.csv：

| 日期 | 事件 | 股数 | 价格 |
|---|---|---|---|
| 07-20 | 买入 515050 | 40,600 | 1.018 |
| 07-27 | 策略 `order_target_value('515050.SS', ≈44,859)`（目标 ≈ 权益×0.475） | **42,400（全额再买）** | 1.058 |

07-27 时已持 40,600 股、现值 42,954.8 —— 原生 target 语义应补差 delta≈1,900 股（或落入微调阈值跳过），实际全额买入 → 期末持仓 82,xxx 股（金字塔）。ptrade 端同构（7-06 加仓 36,400 股 = 目标全额，见对齐分析报告 E-1）。

## 2. 根因（代码行级）

**缺陷在接线层，不在引擎层**：

- 引擎原生路径正确：`backtest_engine.py::_immediate_execute` target_value 分支（L695-711）有完整 delta 逻辑（`delta = target_value − current_value`，含 `min_rebalance_pct` 微调跳过、delta>0 买 / delta<0 卖）。对照组测试 `test_control_engine_native_delta_works` **绿**。
- 接线层破坏语义：`ptrade_api.py`（2026-08-22 A1 本地拆单接线）L2310-2318：

```python
def _qs_wire_order_target_value(security, value, *args, **kwargs):
    px = _qs_last_close_lookup(security)
    orders, _tot = _qs_split_order(security, value, px)   # n = value/px 全额折股，不扣现持
    if not orders:
        return _QSOrderWiringState.target_orig(security, value, ...)  # 仅 value<=0 时走到
    for code, amt in orders:
        ids.append(_qs_wire_order(code, amt))             # shares 语义 order()，绕过 delta
```

- 模块级重绑定（L2355）：`order_target_value = _qs_wire_order_target_value` —— 策略注入名即此包装，原生方法仅存于 `_QSOrderWiringState.target_orig`。
- `_qs_split_order`（L2553-2581）：`n = value / px`；n≤49,000 时 `amount = int(n/100)*100` 返回**全额目标市值折算股数**，从不查询现持仓。
- 转换侧注入模板（source_import `_QS_ORDER_SPLIT_EXT`）逐字同构（转换产物 L364-372）——**双端同一 bug，订单序列"一致地错"**（2026-08-22 接线后双端逐笔一致但均失去 target 语义）。

## 3. 影响面（两个方向都坏）

| 场景 | 原生语义 | 接线后实际 | 复现测试 |
|---|---|---|---|
| target > 现值（小幅加仓） | 补差 delta 股 | **全额买入**（金字塔） | ① 红：40,600 存量 + 目标 44,859 → 实际 82,900 股 |
| target < 现值（减仓） | 卖出差额 | **全额买入**（更严重：减仓变加仓） | ② 红：50,000 股、目标 30,000 → 实际 78,300 股 |
| value = 0（清仓） | 全卖 | 原生路径保留（`_qs_split_order` value≤0 返回空 → 走 target_orig） | 绿（保底未破坏） |
| 空仓 target 建仓 | 全额买 | 全额买（巧合等价） | 绿 |

受影响策略（凡 2026-08-22 之后双端回测、策略含 `order_target_value` 存量/减仓调用者）：tech_etf（实证）；CANSLIM/fall_reversal/weekly/周频 的 `order_target_value(code, 0)` 清仓不受影响、空仓建仓不受影响，**存量调仓与减仓路径全部受损**。

## 4. 复现与隔离（tests/test_b2_target_value_semantics_repro.py）

```
test_repro_existing_position_full_value_buy  FAILED（82,900 > 42,500 —— 复现）
test_repro_reduce_target_becomes_buy        FAILED（78,300 ≥ 50,000 —— 减仓变加仓）
test_control_engine_native_delta_works      PASSED（引擎原生 delta 正确 → 缺陷在接线层）
test_control_value_zero_clears              PASSED（清仓保底未破坏）
test_control_empty_position_full_buy_equivalent PASSED（空仓建仓等价）
```

断言按**原生 target 语义**书写 —— B2 修复后两红转绿，本测试即转为永久回归（master-plan §6）。

## 5. 修复方向（WP-B 实施依据，先方案后改码）

1. **本地侧（B2）**：`_qs_wire_order_target_value` 先查现持市值（`get_position`/引擎 account），`delta = value − 现值`；仅对 delta 走拆单路径（`_qs_split_order(security, delta, px)`）；value=0 维持原生清仓。min_rebalance_pct 微调跳过语义须保留（接线层绕过它同样改变了行为——存量微调被放大为全额）。
2. **转换侧（B1，依赖 WP-A1 持仓包装）**：模板同构修复——delta 路由 `order()`；平台侧现值读取依赖 A1 的 `get_position().value/market_value`（P-POS 探针钉死字段名后落）。
3. **回归判据**：两红转绿 + 保底/对照仍绿；golden 差异逐笔归因 delta 修正（合并基线重验，master-plan §5）。
4. **附带发现**：接线同时绕过了 `min_rebalance_pct` 微调跳过与涨跌停前的方向判定上移（Order.reason 分类）——修复时需对照 `_immediate_execute` 原生行为逐项核对，防止接线层再次引入语义差。

## 6. 时间线备注

- 2026-08-22 A1 接线入库（动机：分板市价单 50,000 股上限拆单，双端订单序列一致）；**持久锚点：提交 `6f263c3`（fix(ptrade-align): A1 order-split wiring）——行号引用以该提交为准，工作树行号随并行会话改动漂移**；
- 2026-08-25 六策略双端回测暴露金字塔（tech_etf 最显著）；
- 2026-08-26 本证据 + 复现测试（Phase 0）。
