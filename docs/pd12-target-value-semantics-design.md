# P-D12 设计：order_target_value 目标市值语义修复（WP-B / B1+B2+B3）

- **流水线状态**：Step 1 ✅ → Step 2 ✅ 审计通过（2026-08-26，附两处细化）→ **Step 3 实施**（stash 回退点 + slippage 线协调照案）
- **审计细化（并入实施）**：
  - ①D1 补等价断言：**T13 三面测试**——wiring-delta == engine-native-delta 逐笔等价（引擎原生绿 / 旧接线红 / 新接线绿且等于原生），防"接线包装"与"直调引擎"两路径在同策略下分叉；
  - ②D5 fail-open 继承语义在 evidence **显式登记**（现值查询异常→视为空仓→全额买入 = 继承现状而非新设计，防合并基线重验误判为未解释差异）。
- 关联：`docs/dual-end-alignment-master-plan.md` WP-B；根因证据 `docs/evidence/b2-target-value-semantics-20260826.md`（E-2 代码级 + B2 复现 2 红 3 绿）；复现测试 `tests/test_b2_target_value_semantics_repro.py`（**已推送至 f0c0bd7**，2 红 = 修复目标，3 绿 = 对照保底）
- 依赖：WP-A（已关闭 f0c0bd7）：平台侧 `get_position(code).market_value` 现值入口可用（P-POS F3/F5 实证）；`_QSPositionView` 透传平台 `market_value`
- 改动侧：**本地 ptrade_api 接线层（B2）+ 转换注入模板（B1）+ 接线层 0 股告警（B3）**——双端同构修复；引擎 `_immediate_execute` 零改动（对照组已证其 delta 正确）

---

## 1. 问题定义

### 1.1 现象（tech_etf 双端实证，2026-08-25 trades.csv）

| 日期 | 事件 | 正确语义（引擎原生 delta） | 实际行为（接线层降级） |
|---|---|---|---|
| 本地 07-27 | 持仓 515050 40,600 股（现值 42,955），`order_target_value(515050, 43,562)` | delta = 1,904 → 补差 ≤1,900 股（或微调跳过） | **全额买 42,400 股**（金字塔） |
| ptrade 07-06 | 持仓 515050 33,900 股（现值 40,795），`order_target_value(515050, 45,333)` | delta ≈ 4,538 → 补差 | **全额买 36,400 股**（金字塔，仓位 88.5%） |
| 任意 | `order_target_value(code, target < 现值)`（减仓） | 卖出差额 | **全额买入**（更严重：减仓变加仓，B2 复现② 实证 50,000→78,300 股） |

### 1.2 根因（E-2，代码级闭环）

**双端同一 bug 的两份逐字同构拷贝**（2026-08-22 A1 拆单接线设计副作用）：

```python
# 本地 ptrade_api.py L2310-2318（转换侧 source_import._QS_ORDER_SPLIT_EXT 同构）
def _qs_wire_order_target_value(security, value, *args, **kwargs):
    px = _qs_last_close_lookup(security)
    orders, _tot = _qs_split_order(security, value, px)   # n = value/px 全额折股！
    if not orders:
        return _QSOrderWiringState.target_orig(security, value, ...)
    for code, amt in orders:
        ids.append(_qs_wire_order(code, amt))              # order(amt) shares 语义！
```

`_qs_split_order(security, value, px)` 把**目标市值全额**折算为股数（`n = value / px`），从不查询现持仓 → `delta = value − current_value` 从未计算 → 引擎原生 target 语义（`_immediate_execute` L695-711，对照组测试证明正确）被完全绕过。

**附带发现**：接线同时绕过了 `min_rebalance_pct` 微调跳过语义（引擎原生：|delta|/current < 阈值 → 拒单 `below_rebalance_threshold`）——修复须评估是否保留。

### 1.3 目标

1. 双端 `order_target_value` 恢复完整目标市值语义：加仓补差、减仓卖出、清仓保底、微调跳过；
2. 拆单逻辑（>49,000 股分板限制）保留并作用于 **delta 股数**而非全额股数；
3. 0 股委托（delta 折股 <100 股）显式告警（B3），不吞单不静默；
4. 双端订单序列在同构场景下逐笔一致（延续 A1 原始目标）。

## 2. 改动范围

### 2.1 本地侧（B2）：`quantstudio/backtest/ptrade_api.py` 接线层

**修改 `_qs_wire_order_target_value`**（唯一函数级改动）：

```python
def _qs_current_value(security, px):
    """接线层现值：amount × 统一链 ① 层前收（不依赖 _get_ptrade_positions 的
    成本回退价格——prices={} 时 current_price=avg_cost 会高估减仓 delta）。"""
    try:
        pos = _api.get_position(security)
        amt = getattr(pos, 'amount', 0) or 0
        if amt <= 0 or px <= 0:
            return 0.0
        return float(amt) * float(px)
    except Exception:
        return 0.0    # 查询失败 → 视为空仓 → delta=全额（fail-open 到旧行为的买入分支）


def _qs_wire_order_target_value(security, value, *args, **kwargs):
    if value is None:
        return _QSOrderWiringState.target_orig(security, value, *args, **kwargs)
    value = float(value)
    if value == 0:
        return _QSOrderWiringState.target_orig(security, 0, *args, **kwargs)  # 清仓保底（B2 复现③绿）
    px = _qs_last_close_lookup(security)
    if px <= 0:
        return _QSOrderWiringState.target_orig(security, value, *args, **kwargs)  # px 缺失回退原 API（原语义）
    current = _qs_current_value(security, px)
    delta = value - current
    # ① 微调跳过（引擎原生语义恢复——阈值与 _immediate_execute 一致）
    if current > 0 and abs(delta) / current < _QS_MIN_REBALANCE_PCT:
        return _make_noop_order_target(security, delta, 'below_rebalance_threshold')
    if delta <= 0:
        # ② 减仓方向：卖出 |delta| 市值（走引擎原生命令——无拆单需求，卖出无 49k 上限）
        if current > 0:
            return _QSOrderWiringState.target_orig(security, value, *args, **kwargs)
        return _make_noop_order_target(security, 0.0, 'already_flat')     # 无仓减仓 → no-op
    # ③ 加仓补差：delta 走拆单链路（B1 同构）
    orders, _tot = _qs_split_order(security, delta, px)
    if not orders:
        # delta < 1 手价值（100×px）→ 0 股委托告警（B3），不静默
        _qs_warn_zero_order('order_target_value', security, delta, px,
                            reason='delta_below_one_lot')
        return _make_noop_order_target(security, delta, 'delta_below_one_lot')
    ids = []
    for code, amt in orders:
        ids.append(_qs_wire_order(code, amt))
    return ids[-1] if ids else None
```

**新增常量与辅助**（同文件）：

```python
_QS_MIN_REBALANCE_PCT = 0.05   # 与 backtest_engine min_rebalance_pct 默认值一致（实施时核对）

def _make_noop_order_target(security, delta, reason):
    """接线层 no-op Order（对齐引擎原生 below_rebalance_threshold 返回形态；
    status='rejected' reason=<why>，bool(filled)==False——策略可感知跳过）。"""

def _qs_warn_zero_order(api, security, value, px, reason):
    """B3：0 股委托显式告警（log.warning，含代码/目标金额/参考价/最小 1 手价值）。"""
```

### 2.2 转换侧（B1）：`source_import.py` `_QS_ORDER_SPLIT_EXT` 模板内 `order_target_value` 包装

同构修复（依赖 WP-A 的 `get_position` 包装已注入——**注入顺序**：P-D11 position view 块在 P-D10 块之后、shim 之前 → P-D12 修改的 order 包装在 helpers 块（更早）——需要调整装配顺序或使 B1 包装惰性调用 position view）：

**装配顺序问题**：`_QS_ORDER_SPLIT_EXT` 在 blocks 中位于 `_QS_POSITION_VIEW_EXT` **之前**（L2166 vs L2195），order 包装执行时 `get_position` 还是原生——**但包装体内的调用发生在策略运行时**（回测阶段），届时模块级所有重绑定已完成。所以运行时 `get_position(...)` 已是 P-D11 包装版 ✓。只需在模板内直接调用 `get_position`（模块级名字，运行时解析）——**无需调整装配顺序**。

模板修改（`order_target_value` 包装函数体替换为 B2 同构逻辑；`_qs_norm_code`/现值入口用 P-D11 已注入的同名函数）：

```python
def _qs_target_value_delta(security, value, *args, **kwargs):
    """P-D12：target 语义恢复——delta = value − get_position().amount × px。"""
    # 与 B2 同构：清仓保底 / px 缺失回退 / 微调跳过 / 减仓原生命令 / 加仓 delta 拆单
```

**门控**：`_QS_ORDER_SPLIT_EXT` 注入条件已有（`_source_uses_order_api`）；P-D12 修改在模板内部，不新增门控。**但 position view 依赖**：策略调用 `order_target_value` 而不调 `get_positions/get_position` 时，position view 不注入 → 模板内 `get_position` 为平台原生 → 键不匹配风险。**处置**：P-D12 的 delta 计算不直接调 `get_position`，改调 P-D11 已有的 `_qs_norm_code` + 平台原生 `get_position(_qs_norm_code(security))`（.SS 归一后查，绕开键双体系——探针 F5 实证 .SS 可查）。若 position view 已注入则 `get_position` 已是包装版，`.SS` 归一输入同样安全（幂等）。

### 2.3 B3：0 股委托告警（双端接线层内置）

上两节已含（`_qs_warn_zero_order` / 模板内同构 `log.warning`）。告警格式：

```
QS_ZERO_ORDER api=order_target_value code=515050.SS delta=83.0 px=1.058
  one_lot_value=105.8 reason=delta_below_one_lot
```

### 2.4 涉及文件

| 文件 | 改动 | tracked |
|---|---|---|
| `quantstudio/backtest/ptrade_api.py` | B2：`_qs_wire_order_target_value` 重写 + `_qs_current_value` + `_QS_MIN_REBALANCE_PCT` + no-op/告警辅助 | ✅（他线有 57 行未提交改动——**实施前协调**） |
| `quantstudio/strategy_compiler/source_import.py` | B1：`_QS_ORDER_SPLIT_EXT` 内 `order_target_value` 包装同构修改 | ✅ |
| `tests/test_b2_target_value_semantics_repro.py` | 修复后两红转绿（断言不变，产品修复） | ✅（已推送） |
| `tests/test_pd12_target_semantics.py`（新） | B1/B2/B3 专项测试矩阵（§5） | 新增 |

**不改**：`backtest_engine.py`（delta 引擎正确）；`order_target_percent`（本地无此 API，双端无分叉）；`order_value`（增量语义正确，无 delta 需求）；WP-A 全部产物。

## 3. 关键设计决策（审计要点）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 现值计算用 `amount × _qs_last_close_lookup(px)` 而非 `get_position().market_value` | 本地 `_get_ptrade_positions(prices={})` 的 `current_price` 回退 `avg_cost`（成本价），接线层拿到的是成本估值而非市价——减仓 delta 会被低估。统一链 ① 层前收（T-1 close）与决策时钟一致（PIT）。平台侧 `market_value` 是实时估值（含当日），T-1 px 更保守且双端一致 |
| D2 | 微调跳过（`min_rebalance_pct`）**恢复**引擎原生语义 | 接线层此前绕过它 = 语义降级的一部分；恢复是完整修复。代价：tech_etf 07-27 场景（delta 1.4% < 5%）从"全额买"变为"跳过"——行为变化由合并基线重验覆盖（master-plan §5 硬约束 3） |
| D3 | 减仓方向走 `target_orig` 原生命令而非拆单 | 卖出无 49k 单笔上限（买入分板限制）；引擎原生减仓（delta<0 → `_execute_sell(sell_value=|delta|)`）已含涨跌停/停牌防护 |
| D4 | px 缺失（缓存未命中）→ 回退 `target_orig` 原生 | 与现行 `if not orders` 回退一致（探针已证 px=0 回退语义）；原生路径有引擎自身价格 |
| D5 | `_qs_current_value` 异常 → 返回 0（视为空仓）→ delta=全额买入 | fail-open 到修复前的买入行为（不引入新阻断）；异常仅记 debug（接线层不刷屏） |
| D6 | B1 平台侧 delta 的 `get_position` 调用：`get_position(_qs_norm_code(security))` | position view 未注入时（策略不调 position API），`.SS` 归一输入绕开键双体系（F5 实证）；已注入时幂等安全 |
| D7 | `_QS_MIN_REBALANCE_PCT` 独立常量而非 import 引擎值 | 转换模板自包含（平台无 quantstudio 可导入）；与引擎默认值同步由 T-差分测试钉死 |
| D8 | B4（审计计数）**移至 WP-F** | 其主体是策略层埋点（skill 模板），接线层仅提供能力；避免 WP-B 范围蔓延 |

## 4. 影响面

### 4.1 受益策略（重转后）

| 策略 | 修复效果 |
|---|---|
| tech_etf_mvo_rotation | 金字塔消除：本地 07-27 补差（或微调跳过）、ptrade 07-06 补差数千股；"降仓 50%"风控恢复执行 |
| CANSLIM / 周频三层 / weekly | 清仓（value=0）不受影响（保底路径）；空仓建仓全额不受影响；**存量持仓调仓**受益（此前若有则全额） |
| vol_regime / fall_reversal | fall_reversal 的 `order_target_value(code, 0)` 清仓全走保底——零影响 |

### 4.2 行为变化与基线影响

- **本 WP 是行为变化**（恢复正确语义）：所有含存量调仓的策略结果会变——**合并基线重验**（master-plan §5 硬约束 3：P-A3 二期 + B2 + D3 合并为一次重验）；
- B2 复现测试两红转绿 = 修复的直接验证；
- 全量套件中 order 相关测试（compliance 94 项）需全绿——`_qs_wire_order_target_value` 的 5 个测试用例**需同步更新**（它们测的是全额拆单行为——修复后 delta 语义下这些用例的输入场景需重设：空仓时 delta=全额 → 原断言仍成立；存量时需新设）。

### 4.3 风险

| 风险 | 防护 |
|---|---|
| `ptrade_api.py` 他线 57 行未提交改动（slippage） | **实施前协调**——stash create 回退点 + 与该线确认改动无交集（本 WP 改 L2310-2350 区，slippage 改签名区——不同函数，git 可自动合并） |
| 微调跳过引入新拒单（策略感知变化） | no-op Order 返回形态对齐引擎原生（`status='rejected'` + reason）；策略检查 `order.status` 可感知 |
| 双端微差（本地 T-1 px vs 平台实时 market_value） | D1 统一用 T-1 px 计算现值（双端同源）——平台侧也用 `_qs_last_close_lookup` 的 T-1 缓存而非实时 market_value |
| 平台 get_position 返回形态漂移 | P-D11 fail-loud 兜底；D6 归一输入已处理键体系 |

## 5. 测试矩阵（tests/test_pd12_target_semantics.py，新增）

| 用例 | 场景 | 断言 |
|---|---|---|
| T1 加仓补差 | 空仓 → target 20,000 | 全额买入（与修复前一致——空仓 delta=全额） |
| T2 存量加仓 | 持仓 40,600@1.058（现值 42,955）→ target 44,859 | **补差 ≤1,900 股**（B2 复现① 转绿） |
| T3 减仓 | 持仓 50,000@1.058（现值 52,900）→ target 30,000 | **卖出**（持仓减少）（B2 复现② 转绿） |
| T4 清仓保底 | 持仓 → target 0 | 原生清仓（B2 复现③ 仍绿） |
| T5 微调跳过 | 持仓现值 42,955 → target 43,562（delta 1.4% < 5%） | no-op rejected + reason=below_rebalance_threshold |
| T6 delta <1 手 | 持仓 → delta=83 元（px=1.058 → 78 股 <100） | **QS_ZERO_ORDER 告警**（B3）+ no-op |
| T7 px 缺失回退 | 缓存空 → target 20,000 | 走 target_orig 原生（captured 验证） |
| T8 拆单作用于 delta | 空仓 → target 490,000@10 元（delta 49,000 股恰等上限） | 单笔不拆；49,001 → 拆 2 段 |
| T9 双端同构 | 同场景 B1 模板 exec vs B2 本地接线 | 订单序列 (code, amount) 逐笔一致 |
| T10 微调阈值一致性 | `_QS_MIN_REBALANCE_PCT` vs 引擎 `min_rebalance_pct` 默认值 | 相等（防漂移） |
| T11 compliance 存量用例回归 | 5 个 `_qs_wire_order_target_value` 测试 | 更新后全绿（空仓场景断言不变，存量场景新增 delta 断言） |
| T12 6 策略重转 | 全部重转 | api_portability 全 PASS + B1 模板含 P-D12 标记 |
| T13 三面等价（审计细化①） | 同场景三路径：引擎原生（`target_orig` 直调）/ 新接线（`_qs_wire_order_target_value`）/ B2 复现对照 | 新接线产出 == 引擎原生产出（成交股数/方向逐笔等价）；与 B2 复现测试组成三面——原生绿、旧接线红、新接线绿且等原生 |

## 6. 验收标准

1. **单元**：T1~T12 全绿 + B2 复现 2 红转绿（3 绿保底仍绿）；
2. **回归**：compliance 94 项（含更新后 5 个接线用例）全绿；全量套件除已知存量红外零新增；
3. **平台验收**（用户执行，tech_etf 单策略）：金字塔消除——ptrade 07-06 加仓 ≤ delta 补差数千股（非 36,400 全额）；"降仓 50%"风控实际生效（vol_trigger 后仓位 ≤50%+缓冲）；
4. **合并基线重验**：P-A3 二期 + B2 + D3 全部落地后统一双跑归因（master-plan §5）。

## 7. 回退条件

- 实施前 `git stash create -u` 回退点（hash 回填）；
- 本 WP = 2 tracked 文件改动 + 1 新增测试——回退 = 定向 restore + 删除新测试；
- 微调跳过若引发不可归因 golden 漂移 → 单独回退 D2 决策（保留 delta 修复、去掉跳过），分步收敛。

## 8. 明确不做

- 不改引擎 `_immediate_execute`（对照组已证正确）；
- 不动 `order_target_percent`（本地无此 API，无分叉）；
- 不动 `order_value`（增量语义本身正确）；
- B4（审计计数）归 WP-F；
- 不在本 WP 处理平台"资金不足自动降量"（ptrade L26 原生行为，对齐方向属 D 系保真开关）。
