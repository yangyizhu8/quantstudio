# Next-Open Basket Rebalance — Design-Only Proposal (v2 corrective)

> **状态**：DESIGN-ONLY v2（corrective），待独立 Review。不含任何代码实现。
> **创建日期**：2026-07-23
> **分支**：`engine-basket-design`（从 main `bdece95`，独立于 pr6b2a-etf-rotation）
> **红线**：本文件只含设计文档、状态机、版本迁移方案和测试矩阵。不修改 BacktestEngine、PtradeAPI 或任何运行时代码。
> **修正**：v1（be909fc）复审 FAIL，13 项设计阻断。本 v2 逐项修正。

---

## 1. 问题陈述

### 1.1 当前 next_open 语义（v0.2.0-next_open_pending）

在 `match_price_mode=next_open` 下，策略在 T 日通过 `order_target_value` 下单时，引擎（`backtest_engine.py:_create_pending_order`）执行：

1. **买单**：检查 `est_cost > self.account.cash`（L851）。若不足→rejected(insufficient_cash)。若通过→从 cash 预扣到 locked_cash。
2. **卖单**：检查 `avail < est_shares`（L861）。若不足→rejected(insufficient_sellable)。若通过→增加 pending_sell_shares。
3. 所有 pending order 在 T+1 开盘时由 `_drain_pending_orders` 逐一执行（L978-1060）。

### 1.2 核心缺陷

当策略在 T 日执行"卖 A → 买 B"的轮动时：卖单只预扣 `pending_sell_shares`，不增加 cash。买单检查 cash 时，卖出所得尚未到账。结果：买单被 insufficient_cash 拒绝。

### 1.3 为什么不能简单预释放 cash

T+1 开盘成交价未知（跳空可能导致所得不足）；卖单可能被停牌/跌停/拒绝；可能导致负现金或隐含杠杆；NAV 和订单状态失真。

---

## 2. 设计目标

在 next_open 模式下支持 basket rebalance：策略在 T 日同时下单卖出一组标的、买入另一组标的，引擎在 T+1 开盘按确定性顺序处理，用真实卖出所得支持真实买入。

### 2.1 不可违反的约束

1. 不允许负现金——任何时刻 cash >= 0。
2. 不允许超卖——pending_sell_shares <= can_sell。
3. 不允许重复锁定——同一标的同一方向不重复预扣。
4. 不允许卖出所得双计。
5. close/open 路径零触达。
6. 现有 next_open legacy 行为不变（显式开关隔离）。

---

## 3. 版本方案与激活开关（修正阻断1/12）

### 3.1 版本

| 语义版本 | 模式 | 描述 |
|---|---|---|
| `0.1.0-legacy` | close/open | 同步成交（不变） |
| `0.2.0-next_open_pending` | next_open (legacy) | PR2 pending queue（现有，不变） |
| **`0.4.0-next_open_basket`** | **next_open + callback_basket** | **本设计** |

### 3.2 显式激活开关（默认关闭）

```python
# EngineConfig 新增字段
rebalance_mode: str = "legacy"  # "legacy" | "callback_basket"
```

**只有同时满足以下条件才启用 basket 语义**：
- `engine_profile == "daily-bar-v1"`
- `match_price_mode == "next_open"`
- `rebalance_mode == "callback_basket"`

此时 `engine_semantics_version = "0.4.0-next_open_basket"`。

**其余所有组合（包括 next_open + legacy）继续使用 `0.2.0-next_open_pending`**，行为完全不变。

### 3.3 minute-bar-v1 BLOCK

minute-bar-v1 + basket 必须**显式 BLOCK**（raise），不是静默退化。

### 3.4 旧引擎版本门禁

旧引擎（不认识 0.4.0）遇到 Spec/run_card 声明 `0.4.0-next_open_basket` 时，**必须拒绝**（unsupported semantics），不能静默按 0.2 执行。

### 3.5 Spec / manifest 透传

- `strategy_spec.json` → `execution.rebalance_mode`（新增可选字段，默认 "legacy"）。
- `generation_manifest.json` → `execution.rebalance_mode`（透传）。
- `run_card.json` → `reproducibility` 记录实际 `engine_semantics_version`。

### 3.6 Basket context 边界（冻结，原 §15 未决项）

- **每次 `handle_data` 调用形成一个 basket**（daily-bar-v1 only）。
- `before_trading_start` 和 `run_daily` 中的订单**不并入 basket**——继续走 legacy pending 逻辑。
- 引擎在 `_run_ptrade_strategy` 调用 `handle_data` 前 push basket context，调用后 pop 并提交 basket。

---

## 4. Basket 数据结构（修正阻断8/10）

### 4.1 RebalanceBasket

```python
@dataclass
class RebalanceBasket:
    basket_id: str               # "basket_{created_dt}_{seq}"
    created_dt: str              # T 日日期
    scheduled_dt: str            # T+1 日期
    sell_orders: list[PendingOrder]
    buy_orders: list[PendingOrder]
    status: str = "pending"      # 见 §10 状态真值表
    realized_sell_proceeds: float = 0.0  # 审计元数据（不重复加到 cash）
```

### 4.2 PendingOrder 扩展

```python
basket_id: str | None = None    # None = 独立订单（legacy）
```

### 4.3 同一 bare code 唯一性（冻结）

Basket 内**按 bare code 唯一**：
- 同一 bare code + 同方向重复订单：**拒绝后者**（reason=`basket_duplicate_order`）。
- 同一 bare code 买卖方向冲突：**拒绝后者**（reason=`basket_conflicting_order`）。
- `.SH`/`.SS` 不同后缀的同一 bare code 视为同一证券。

第一版 MVP 限制（控制范围）：
- 对已持仓标的只接受 `target_value=0` 的 sell-all。
- 对未持仓标的只接受 `target_value>0` 的 buy。
- 其他增减仓 target 暂时 BLOCK（reason=`basket_unsupported_target`）。

---

## 5. T 日资源预留规则（修正阻断9）

### 5.1 卖单预留

- 卖单仍预扣 `pending_sell_shares`（与 legacy 一致）。
- **不预释放 cash**。

### 5.2 买单预留（变更）

- 买单**不在 T 日检查 cash 或预扣 locked_cash**。
- 买单的 `est_cost` 记录在 basket 内供审计，cash 不变。

### 5.3 T 日方向锁定（冻结）

- T 日按估算价判定方向（buy/sell），写入 PendingOrder.direction。
- **T+1 drain 时若跳空导致方向翻转**（如 T 日判定 buy，但 T+1 重算后 target_value 已超出现有持仓市值→应卖），订单 **rejected(direction_changed_at_drain)**。
- 不在 drain 阶段动态重构 basket leg。

### 5.4 T 日 cash 不变性

- T 日结束时：`cash` 只受独立（非 basket）订单影响。
- Basket 内卖单/买单不影响 T 日 cash。
- Basket 内未成交的买单不计入资产。

---

## 6. T+1 开盘 Basket Drain 状态机（修正阻断2/3/4/6/7）

### 6.1 处理顺序

```
独立订单（basket_id=None）先 drain（legacy 逻辑不变）。
然后对每个 scheduled_dt == T+1 的 basket（按 basket_id 排序）：

  Phase 1: 卖单优先（按 bare code 字典序排序）
    cash_before_sells = account.cash
    for sell_order in basket.sell_orders:
      1. 释放 pending_sell_shares
      2. 检查 T+1 状态（停牌/跌停/无价格）
      3. 若可执行 → _execute_sell at T+1 open（此函数内部已 cash += net_proceeds）
      4. 若不可执行 → 记录拒绝，basket 标记 has_sell_failure=True
    cash_after_sells = account.cash
    realized_sell_proceeds = cash_after_sells - cash_before_sells  # 审计元数据

  Phase 2: mandatory sell 失败检查
    if has_sell_failure:
      → 整个 buy leg 全部 rejected（reason="mandatory_sell_failed"）
      → basket status = "partial"（sells 部分成交，buys 全拒）
      → 跳到 Phase 5

  Phase 3A: buy-leg 原子预检（不执行任何买单）
    for buy_order in basket.buy_orders:
      1. 获取 T+1 实际开盘价
      2. 检查停牌/涨停/无价格 → 若不可执行，标记 buy_preflight_failed
      3. 按 T+1 价格计算 actual_shares（整手向下取整）
      4. 计算 actual_commission + actual_transfer_fee
      5. 得到 actual_required_cash[i]
    total_required_cash = sum(actual_required_cash)

  Phase 3B: buy-leg 执行判定
    if buy_preflight_failed 或 total_required_cash > account.cash:
      → 整个 buy leg 全部 rejected
      → basket status = "partial"（若有 sells 成交）或 "rejected"（无 sells 成交）
      → 跳到 Phase 5
    else:
      → 依次执行全部买单（按 bare code 字典序）
      → 每笔从 account.cash 扣除 actual_required_cash

  Phase 5: basket 状态更新（见 §10 真值表）
```

### 6.2 卖出所得不双计（修正阻断2）

`_execute_sell` 内部已执行 `self.account.cash += net_proceeds`。因此：
- Phase 1 执行 sell 后，`account.cash` 已包含卖出所得。
- `realized_sell_proceeds` **只作为审计元数据**记录，**不再次加到 cash**。
- Phase 3B 的 `account.cash` 已是卖出后的真实可用现金。

### 6.3 确定性排序（修正阻断7）

- sell orders 和 buy orders 均按 **bare code 字典序** 排序。
- 多个 basket 按 **basket_id** 排序（含 created_dt + seq，自然有序）。
- 不使用 ETF pool index（引擎不可见）。

---

## 7. 卖单失败 → buy leg 全拒（修正阻断3/4）

### 7.1 冻结策略

- 所有 sell orders 属于 **mandatory sells**。
- 任意 mandatory sell 失败（停牌/跌停/无价格）→ 整个 buy leg 全拒。
- 已成交的 sell 不回滚。
- basket status = "partial"。
- 策略下一交易日重新计算并重试。

### 7.2 buy-leg 原子预检

Phase 3A 对每只买单用 T+1 **实际**开盘价计算 actual_shares + actual_cost。**禁止使用 T 日 est_cost 作为最终资金判断**。全部预检通过后才执行（Phase 3B）。

---

## 8. 停牌、涨跌停、跳空、整手和成本（修正阻断6）

| 场景 | 卖单 | 买单 |
|---|---|---|
| 停牌 | rejected(halted) | rejected(halted) |
| **涨停** | **允许卖出**（涨停买 blocked） | rejected(limit_up_blocked) |
| **跌停** | rejected(limit_down_blocked) | **允许买入**（跌停卖 blocked） |
| 跳空 | T+1 open 实际价成交；方向翻转→rejected(direction_changed_at_drain) | T+1 open 重算 shares；不足整手→rejected(insufficient_cash_or_rounding) |
| 整手 | 按持仓量处理 | 重算到 100 股整数倍 |
| 成本 | commission + stamp_tax(ETF=0) + transfer_fee(ETF=0) | commission + transfer_fee(ETF=0) |

与现有 `is_price_limit_blocked` 一致：`direction=buy 且涨停→blocked`；`direction=sell 且跌停→blocked`。

---

## 9. Cancel / Expire / 归还预留（修正阻断5）

### 9.1 Cancel basket order

- 若 order 在 basket 内且 basket 未 drain：
  - sell order → 精确减少 `position.pending_sell_shares`（by est_shares）。
  - buy order → 无需归还（未预扣 cash）。
  - 从 basket 移除该 order。
  - basket 变空 → status = "cancelled"。
- **refund 幂等**：已 cancelled/filled/rejected 的 order 不得重复归还。

### 9.2 Expire / end-of-backtest

- 所有未 drain 的 basket → status = "expired"。
- 逐笔归还 sell order 的 `pending_sell_shares`。
- buy order 无需归还。
- **end-of-backtest 后所有 pending_sell_shares == 0**（不变式）。

---

## 10. Basket Status 真值表（修正阻断10）

| 场景 | status |
|---|---|
| 所有 sell + 所有 buy 均 filled | `completed` |
| sell-only basket，所有 sell filled | `completed` |
| buy-only basket，所有 buy filled | `completed` |
| 至少一笔 filled，但未完成全部预期订单 | `partial` |
| sells filled + buys 全拒 | `partial` |
| some sells filled + buy leg aborted | `partial` |
| 无任何订单 filled | `rejected` |
| all sells rejected + no buys filled | `rejected` |
| drain 前全部有效订单被取消 | `cancelled` |
| 回测结束仍未 drain | `expired` |

---

## 11. 独立订单与 Basket 优先级（修正阻断11）

### 11.1 处理顺序（冻结）

1. **独立订单先 drain**（basket_id=None，legacy 逻辑）。
2. **basket 后 drain**（按 basket_id 排序）。

### 11.2 影响

- 独立买单消耗 cash 后，basket buy preflight 使用**剩余真实 cash**。
- 独立卖单释放的 cash **可供** basket 使用（独立卖单在 basket 之前 drain）。
- 独立订单与 basket 对同一 bare code 冲突 → 独立订单先执行，basket order 检测到持仓/cash 已变 → 按 T+1 实际状态重算。

---

## 12. 不回归证明

### 12.1 close/open 模式

basket 逻辑只在 `match_price_mode == "next_open" AND rebalance_mode == "callback_basket"` 时激活。close/open 零触达。

### 12.2 现有 next_open legacy（rebalance_mode="legacy"）

- PendingOrder 的 `basket_id` 默认 None。
- `_create_pending_order` 行为不变（T 日 cash 检查 + locked_cash 预扣）。
- `_drain_pending_orders` 行为不变。
- **行为不变保证**（通过显式开关隔离，非"自动检测"）。

### 12.3 minute-bar-v1

`rebalance_mode == "callback_basket" AND engine_profile == "minute-bar-v1"` → **raise**（显式 BLOCK）。

---

## 13. Reference Signal/Order/NAV 接入

### 13.1 Basket 元数据进入 run_card

```json
{
  "basket_id": "basket_20260107_001",
  "created_dt": "2026-01-07",
  "drained_dt": "2026-01-08",
  "status": "completed",
  "sells": [
    {"code": "515880.SH", "status": "filled", "price": 3.12, "shares": 10700, "proceeds": 33287.70,
     "trigger_reason": "rotation_exit"}
  ],
  "buys": [
    {"code": "159870.SZ", "status": "filled", "price": 0.84, "shares": 39900, "cost": 33316.50,
     "trigger_reason": "rotation_buy"}
  ],
  "realized_sell_proceeds": 33287.70
}
```

### 13.2 字段分离

- `trigger_reason`（策略层：stop_loss / volume_surge / rotation_exit / rotation_buy / defensive_clear / defensive_buy）。
- `order_status`（引擎层：filled / rejected / pending）。
- `order_reason`（引擎层：insufficient_cash_after_sells / mandatory_sell_failed / halted / limit_down_blocked / direction_changed_at_drain）。

三者独立，不得混为一个字段。

### 13.3 证券代码修正

§13.1 示例中 `159870.SH` 修正为 `159870.SZ`（深圳 ETF）。

---

## 14. 完整测试矩阵（25 项，修正后）

| # | 测试 | 验证 |
|---|---|---|
| 1 | 显式激活默认关闭 | rebalance_mode=legacy → 0.2.0 语义 |
| 2 | legacy next_open handle_data 行为不变 | 非 basket 订单行为与 0.2.0 一致 |
| 3 | daily basket version=0.4.0 | 三条件满足 → engine_semantics_version |
| 4 | minute basket BLOCK | minute-bar-v1 + callback_basket → raise |
| 5 | sell cash delta 不双计 | account.cash delta == net_proceeds（不重复加） |
| 6 | T+1 actual-cost preflight | 用 T+1 实际价计算，非 T 日 est_cost |
| 7 | buy leg 资金不足→0 笔 buy filled | 全拒非缩单 |
| 8 | mandatory sell 失败→buy leg 0 笔 | 任一 sell 失败→buys 全拒 |
| 9 | cancel sell reservation restore | pending_sell_shares 精确归还 |
| 10 | expire sell reservation restore | 末日归还，pending_sell_shares=0 |
| 11 | duplicate/conflicting same-code orders | 同 bare code 同方向→reject；买卖冲突→reject |
| 12 | .SH/.SS bare-code 冲突 | 不同后缀同一 bare code 视为同一证券 |
| 13 | target direction change at drain | 跳空翻转→rejected(direction_changed_at_drain) |
| 14 | mixed independent order + basket | 独立先 drain，basket 用剩余 cash |
| 15 | buy-only basket | 无 sells，buys 正常 |
| 16 | sell-only basket | 无 buys，sells 正常 |
| 17 | basket status truth table | 6 种场景各自正确 status |
| 18 | deterministic code/basket ordering | 跨进程插入顺序不同→drain 顺序一致 |
| 19 | 涨停买阻断、涨停卖允许 | direction=buy+涨停→blocked；direction=sell+涨停→OK |
| 20 | 跌停卖阻断、跌停买允许 | direction=sell+跌停→blocked；direction=buy+跌停→OK |
| 21 | close/open 零触达 | close/open 模式无 basket |
| 22 | legacy next_open 非 basket 不回归 | stock case1 next_open legacy：trades/NAV 不变 |
| 23 | cash >= 0 | 所有路径不变式 |
| 24 | pending_sell_shares <= can_sell | 所有路径不变式 |
| 25 | cancel/expire 后 reservation 归零 | pending_sell_shares==0 |

---

## 15. 已冻结的未决项（原 v1 §15 全部关闭）

| 项 | 冻结决议 |
|---|---|
| Basket context 边界 | 每次 handle_data 调用一个 basket（§3.6）；before_trading_start/run_daily 走 legacy |
| 多 basket 同日 drain | 按 basket_id 排序，各自独立 drain（§6.1/§11） |
| 策略层 basket API | 第一版不暴露，引擎自动管理，策略透明 |
| 卖单失败策略 | mandatory sell 全部成功才进入 buy leg（§7） |
| buy 失败策略 | 原子预检全过才执行，否则全拒（§7.2） |
| 排序规则 | bare code 字典序（§6.3） |
| 方向翻转 | T 日方向锁定，T+1 翻转→rejected（§5.3） |
| 重复/冲突订单 | bare code 唯一，冲突拒绝（§4.3） |

---

## 16. 实施路线图（审批后）

| Phase | 内容 | 依赖 |
|---|---|---|
| **A** | EngineConfig.rebalance_mode + basket context 管理 + 版本门禁 | 本设计审批 |
| **B** | RebalanceBasket 数据结构 + PendingOrder.basket_id + bare code 唯一性 | Phase A |
| **C** | T+1 basket drain 状态机（§6）+ sell 优先 + buy 原子预检 | Phase B |
| **D** | cancel/expire 归还（§9）+ status 真值表（§10） | Phase C |
| **E** | 不回归测试矩阵（§14，25 项）+ 不变式断言 | Phase D |
| **F** | run_card 接入（§13）+ CP3 Oracle 重跑 | Phase E |
