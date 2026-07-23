# Next-Open Basket Rebalance — Design-Only Proposal

> **状态**：DESIGN-ONLY，待独立 Review。不含任何代码实现。
> **创建日期**：2026-07-23
> **分支**：`engine-basket-design`（从 main `bdece95` 创建，独立于 pr6b2a-etf-rotation）
> **红线**：本文件只含设计文档、状态机、版本迁移方案和测试矩阵。不修改 BacktestEngine、PtradeAPI 或任何运行时代码。

---

## 1. 问题陈述

### 1.1 当前 next_open 语义（v0.2.0-next_open_pending）

在 `match_price_mode=next_open` 下，策略在 T 日通过 `order_target_value` 下单时，引擎（`backtest_engine.py:_create_pending_order`）执行以下操作：

1. **买单**：检查 `est_cost > self.account.cash`（L851）。若不足→rejected(insufficient_cash)。若通过→从 cash 预扣到 locked_cash。
2. **卖单**：检查 `avail < est_shares`（L861）。若不足→rejected(insufficient_sellable)。若通过→增加 pending_sell_shares。
3. 所有 pending order 在 T+1 开盘时由 `_drain_pending_orders` 逐一执行（L978-1060）。

### 1.2 核心缺陷

当策略在 T 日执行"卖 A → 买 B"的轮动时：

- T 日卖单只预扣 `pending_sell_shares`，**不增加 cash**。
- T 日买单检查 cash 时，卖出所得尚未到账（要等 T+1 drain 卖单成交后才释放）。
- 结果：买单被 `insufficient_cash` 拒绝。
- 76 个交易日的 ETF 轮动策略只有 3 笔首日买入（初始 100K 现金，零持仓），后续全部轮动买单被拒。

### 1.3 为什么不能简单预释放 cash

用户裁决明确禁止"T 日预释放卖出现金"，因为：

- T+1 开盘成交价未知——跳空可能导致卖出所得不足预期。
- 卖单可能被停牌/跌停/拒绝——若已预释放 cash，买单会占用虚假资金。
- 可能导致负现金或隐含杠杆。
- NAV 和订单状态失真。

---

## 2. 设计目标

在 next_open 模式下支持 **basket rebalance**：策略在 T 日可以同时下单卖出一组标的、买入另一组标的，引擎在 T+1 开盘按确定性顺序处理 basket 内的卖单和买单，用真实卖出所得支持真实买入。

### 2.1 不可违反的约束

1. **不允许负现金**——任何时刻 cash >= 0。
2. **不允许超卖**——pending_sell_shares <= can_sell。
3. **不允许重复锁定**——同一标的同一方向的 pending order 不重复预扣。
4. **不允许静默缩单**——买单要么全成要么全拒（与现有 `_execute_buy` 语义一致）。
5. **close/open 路径零触达**——basket 逻辑只在 next_open 模式激活。
6. **现有 next_open 非 basket 路径不回归**——不含 basket 标记的 pending order 按原逻辑处理。

---

## 3. 版本方案

| 语义版本 | 模式 | 描述 |
|---|---|---|
| `0.1.0-legacy` | close/open | 同步成交，无 pending queue（不变） |
| `0.2.0-next_open_pending` | next_open | PR2 pending order queue（现有，不变） |
| **`0.4.0-next_open_basket`** | **next_open + basket** | **本设计：basket-aware T+1 drain** |

版本号 `0.4.0`（跳过 0.3.x——0.3.0 已被 minute-bar 占用）。旧引擎遇到 basket-aware pending order 仍能处理（向下兼容：basket 元数据被忽略，退化到逐单 drain）。

---

## 4. Basket 数据结构

### 4.1 RebalanceBasket（新增）

```python
@dataclass
class RebalanceBasket:
    """T 日策略创建的关联订单组。T+1 drain 时作为一个原子单元处理。"""
    basket_id: str               # 唯一标识
    created_dt: str              # T 日日期
    scheduled_dt: str            # T+1 日期
    sell_orders: list[PendingOrder]   # 卖单（T+1 优先处理）
    buy_orders: list[PendingOrder]    # 买单（卖单成交后用真实所得处理）
    status: str = "pending"      # pending → draining → completed / partial / rejected
    # T+1 drain 结果记录
    realized_sell_proceeds: float = 0.0   # 卖单实际成交所得（扣成本后）
    buy_rejection_reason: str = ""        # 若买单被拒的原因
```

### 4.2 PendingOrder 扩展

现有 `PendingOrder` 新增可选字段：

```python
basket_id: str | None = None    # 所属 basket（None = 独立订单，走原逻辑）
```

### 4.3 创建时机

策略在 T 日调用 `order_target_value` 时，引擎不立即创建独立 pending order。而是：
- 检测当前是否处于一个 "basket context"（由 handle_data 调用边界界定）。
- 若是：将订单加入当前 basket。
- 若否：创建独立 pending order（原逻辑）。

Basket context 的边界由引擎在 `_run_ptrade_strategy` 调用前后自动管理（策略无需感知）。

---

## 5. T 日资源预留规则

### 5.1 卖单预留（不变）

- 卖单仍预扣 `pending_sell_shares`（与现有逻辑一致）。
- **不预释放 cash**——这是核心约束。

### 5.2 买单预留（变更）

- 买单 **不再在 T 日检查 cash 或预扣 locked_cash**。
- 买单的 `est_cost` 记录在 basket 内，但 cash 不变。
- 现有 locked_cash 机制仍用于独立（非 basket）买单。

### 5.3 T 日 cash 不变性

- T 日结束时：`cash` 只受独立（非 basket）订单影响。
- Basket 内的卖单/买单不影响 T 日 cash。
- NAV 计算时：basket 内未成交的买单不计入资产（与现有 pending 语义一致——`__bool__ False`）。

---

## 6. T+1 开盘 Basket Drain 状态机

### 6.1 处理顺序（冻结）

```
对每个 scheduled_dt == T+1 的 basket：
  Phase 1: 卖单优先
    for sell_order in basket.sell_orders（按 ETF pool 顺序排序）:
      1. 释放 pending_sell_shares
      2. 检查 T+1 状态（停牌/涨跌停/价格）
      3. 若可执行 → _execute_sell at T+1 open → 累加 realized_sell_proceeds
      4. 若不可执行 → 记录拒绝原因，realized_sell_proceeds 不增加

  Phase 2: 计算可用现金
    available_cash = account.cash + realized_sell_proceeds

  Phase 3: 买单后置
    for buy_order in basket.buy_orders（按 ETF pool 顺序排序）:
      1. 检查 available_cash >= buy_order.est_cost
      2. 若不足 → ALL buy_orders rejected (reason="insufficient_cash_after_sells")
         → **全拒策略（见 §7）**
      3. 若足够 → 从 available_cash 扣除 → _execute_buy at T+1 open
      4. 成交后更新 available_cash

  Phase 4: basket 状态更新
    if all sells + all buys filled → status="completed"
    elif some sells failed but buys succeeded → status="partial"
    elif all buys rejected → status="rejected"
```

### 6.2 确定性排序规则

Basket 内卖单和买单均按以下规则排序（冻结，跨进程稳定）：
1. 按 canonical ETF pool 中的位置排序（pool index 升序）。
2. 不在 pool 中的标的按 bare code 字典序排在最后。

这确保同一 basket 的 drain 顺序跨进程一致（解决 set 迭代顺序不确定性）。

---

## 7. 卖单失败时买单处理

### 7.1 冻结策略：全拒（all-or-nothing）

当 Phase 3 发现 `available_cash < 任意 buy_order.est_cost` 时：

- **该 basket 内所有未执行的买单全部拒绝**（reason=`insufficient_cash_after_sells`）。
- 已执行的买单不回滚（卖单和先执行的买单已成交）。
- 不执行确定性缩单（与现有 `_execute_buy` 的不缩单语义一致）。

### 7.2 为什么选全拒而非缩单

- 缩单会改变策略的目标权重（如 Top3 等权变成 Top2），使结果不可复现。
- 全拒更接近实盘：如果资金不足以完成整个 rebalance，实盘经纪人也会拒绝部分委托。
- 策略可以在后续交易日重试（cash 已从卖单成交中释放）。

### 7.3 替代方案（记录但未采纳）

- **按比例缩单**：每只买单按 `available_cash / total_buy_cost` 比例缩减。否决原因：破坏等权语义。
- **按优先级缩单**：按 momentum score 排序，优先填充高分标的。否决原因：引入隐含策略逻辑到引擎层。

---

## 8. 多只买单的现金分配

### 8.1 顺序分配（冻结）

买单按 §6.2 的确定性排序逐一处理：
- 每只买单检查当前 `available_cash >= est_cost`。
- 若通过：扣除 est_cost，执行买入。
- 若不通过：该 basket 所有剩余买单全部拒绝。

### 8.2 为什么不平分

- 平分需要预先知道所有买单的 est_cost 总和，但卖单所得在 Phase 1 之前未知。
- 顺序分配更简单且确定性强。
- 策略层应确保等权金额（`total_value / max_positions`）在大多数情况下都能被满足。

---

## 9. 停牌、涨跌停、跳空、整手和成本处理

| 场景 | 卖单 | 买单 |
|---|---|---|
| 停牌 | rejected(halted)，proceeds=0 | rejected(halted) |
| 涨跌停 | 涨停卖→rejected(limit_up_blocked)；跌停卖→可执行 | 涨停买→rejected(limit_up_blocked)；跌停买→可执行 |
| 跳空（T+1 open ≠ T close） | 用 T+1 open 实际价成交；shares 不变（卖整仓时） | 用 T+1 open 重算 shares；若不足整手→rejected(insufficient_cash_or_rounding) |
| 整手 | 卖单按持仓量整手处理 | 买单重算到 100 股整数倍；不足→rejected |
| 成本 | 卖单扣除 commission + stamp_tax（ETF=0）+ transfer_fee（ETF=0） | 买单扣除 commission + transfer_fee |

跳空导致的 `realized_sell_proceeds` 变化直接影响 Phase 2 的 `available_cash`，从而可能触发 Phase 3 的全拒。

---

## 10. Cancel / Expire / End-of-Backtest

| 事件 | Basket 行为 |
|---|---|
| **cancel_order** | 若 order 在 basket 内且 basket 未 drain → 从 basket 移除该 order。若 basket 变空→basket 标记 cancelled。 |
| **expire（末日）** | 末日仍有 pending basket → 所有 orders 标记 expired，basket 标记 expired。无预扣需归还（basket 买单未预扣 cash）。 |
| **end-of-backtest** | 同 expire。 |

---

## 11. Get Open Orders / Get Order 状态可见性

### 11.1 get_open_orders

- 返回所有 status=pending 的 orders（含 basket 内的）。
- basket 内 order 增加 `basket_id` 字段供查询。

### 11.2 get_order / get_orders

- 返回当日已处理的 orders（含 basket drain 结果）。
- basket 内 order 的 status 为 filled/rejected，reason 区分：
  - `insufficient_cash_after_sells`（basket 专用）
  - `halted` / `limit_up_blocked` / `limit_down_blocked`（通用）
  - `insufficient_cash_or_rounding`（跳空导致）

---

## 12. 不回归证明

### 12.1 close/open 模式

- Basket 逻辑只在 `match_price_mode == "next_open"` 时激活。
- close/open 模式：`_create_pending_order` 不被调用（走 `_immediate_execute`），basket 数据结构不存在。
- **零触达保证**。

### 12.2 现有 next_open 非 basket 订单

- PendingOrder 的 `basket_id` 默认 None。
- `_drain_pending_orders` 先处理独立 order（basket_id=None，原逻辑），再处理 basket。
- 独立 order 的 drain 逻辑不变（释放 locked_cash → 检查 cash → 执行）。
- **行为不变保证**。

### 12.3 测试矩阵

| 测试组 | 覆盖 |
|---|---|
| **close-mode 不回归** | stock case1 close 模式： trades/NAV 与现有完全一致 |
| **open-mode 不回归** | stock case1 open 模式： 同上 |
| **next_open 非 basket 不回归** | next_open 模式下无 basket 标记的独立订单：行为与 0.2.0 一致 |
| **basket 完整轮动** | sell A + buy B in basket → 两单均成交 at T+1 open |
| **basket 卖单失败** | sell A halted → buy B rejected(insufficient_cash_after_sells) |
| **basket 跳空不足** | sell A gap down → proceeds 不足 → buy B rejected |
| **basket 全拒** | all sells fail → all buys rejected |
| **basket 部分成交** | 2 sells + 2 buys: 1 sell filled + 1 rejected → buys 用部分 proceeds |
| **basket 确定性排序** | 同一 basket 多标的，跨进程插入顺序不同但 drain 顺序一致 |
| **basket cancel** | cancel basket 内 order → 其余 order 正常 drain |
| **basket expire** | 末日 basket → expired，无负现金 |
| **负现金防护** | 所有路径 cash >= 0（不变式断言） |
| **超卖防护** | pending_sell_shares <= can_sell（不变式断言） |
| **重复锁定防护** | 同标的同方向不重复预扣（不变式断言） |

---

## 13. Reference Signal/Order/NAV 接入

### 13.1 Basket 元数据进入 run_card

Basket drain 结果记录到 run_card 的 `smoke_backtest` 或独立 `basket_rebalance` 字段：

```json
{
  "basket_id": "basket_20260107_001",
  "created_dt": "2026-01-07",
  "drained_dt": "2026-01-08",
  "status": "completed",
  "sells": [
    {"code": "515880.SH", "status": "filled", "price": 3.12, "shares": 10700, "proceeds": 33287.70}
  ],
  "buys": [
    {"code": "159870.SH", "status": "filled", "price": 0.84, "shares": 39900, "cost": 33316.50}
  ],
  "realized_sell_proceeds": 33287.70,
  "available_cash_at_buy_phase": 66504.20
}
```

### 13.2 Reference order 序列

CP3 reference package 的 `expected_orders.json` 需包含 basket 元数据：
- 每个 order 记录 `trigger_reason`（策略层：stop_loss/volume_surge/rotation/defensive）。
- 每个 order 记录 `basket_id`（引擎层：关联卖买组）。
- `order_status`（引擎层：filled/rejected）和 `order_reason`（引擎层：insufficient_cash_after_sells 等）分离。

### 13.3 与 CP3 Oracle 的对齐

- Oracle 的 `handle_data` 不需修改——basket 创建/管理是引擎层自动的。
- Oracle 的 `_place_order` 返回的 Order 对象会携带 basket_id（如果引擎注入了 basket context）。
- Oracle 日志已区分 filled/pending/rejected，basket drain 结果会自然反映。

---

## 14. 实施路线图（审批后）

| Phase | 内容 | 依赖 |
|---|---|---|
| **A** | Basket 数据结构 + PendingOrder 扩展 + basket context 管理 | 本设计审批 |
| **B** | T+1 basket drain 状态机（§6）+ 全拒策略（§7） | Phase A |
| **C** | 确定性排序（§6.2）+ cancel/expire（§10） | Phase B |
| **D** | 不回归测试矩阵（§12.3）+ 负现金/超卖防护断言 | Phase C |
| **E** | engine_semantics_version 切换 + run_card 接入（§13） | Phase D |
| **F** | CP3 Oracle 重跑 + reference package 冻结 | Phase E + CP3 reference package |

---

## 15. 未决项

| 项 | 说明 |
|---|---|
| Basket context 边界检测 | 如何界定"一次 handle_data 调用"内的所有订单属于同一 basket？方案：在 `_run_ptrade_strategy` 前后 push/pop basket context。 |
| 多 basket 同日 drain | 若 T 日创建了多个 basket（如 stop_loss basket + rotation basket），T+1 drain 顺序？方案：按 basket 创建时间排序，各自独立 drain。 |
| 策略层 basket API | 是否暴露 `create_basket()` / `submit_basket()` 给策略？方案：第一版不暴露，引擎自动管理；策略透明。 |
