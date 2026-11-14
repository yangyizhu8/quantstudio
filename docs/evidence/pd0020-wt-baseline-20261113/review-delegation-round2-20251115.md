# ②审委托包（两件独立审查 · 2026-11-15）

> 依据：六步流水线铁律 01 的②审须**独立审核**，未过禁实施。
> 本包为**审查输入**：材料、判据、争议点、待裁问题已列全；**审查结论由独立审查方给出**，
> 执行方不自审、不自放行。
> **边界（本轮已锁定）**：不改代码、不改断言、不运行 D5、不碰生产库。

---

# 第一件：订单修复 —— 预算不可得 vs 真实零预算

## 1. 送审材料（已在库，请直接调阅）

| 材料 | 路径 |
|---|---|
| ①方案（含§六②审范围四项） | `docs/pd0020-order-cash-none-vs-zero-design-20261115.md` |
| 根因证据链 | `docs/evidence/pd0020-wt-baseline-20261113/bucket-b-attribution.md` §二 |
| 基线对照 | `git show 7826aed:quantstudio/strategy_compiler/source_import.py`（基线锚 `7826aedbf9d634aabb48c2f7c0d988f490c78b07`） |
| 当前实现 | `quantstudio/strategy_compiler/source_import.py` L1494-1505（`order_target_value`）／L1698-1713（`order_value`） |

## 2. 待审问题（按审核指示，四项必答）

### 2.1 三态核验：**预算不可得 / 真零 / 正预算**

请独立核验并给出「通过/不通过＋证据」：

| 态 | `_qs_cash_budget` 返回 | 期望行为 | 关键证据点 |
|---|---|---|---|
| 不可得 | `None` | **fail-open**：照常下单，钳制不生效 | `_qs_cash_avail()` docstring 逐字「全不可得 → 返回 None（钳制不生效…）…**禁止：取不到禁下单**」；`_qs_split_order` 的 `cash_avail=None ⇒ budget=value` |
| 真零 | `0` | **拒单**：返回 `None` ＋ `_QS_CASH_REJECT_COUNT` 递增 ＋ 记 `QS_CASH_BUDGET_ZERO` | 三项须**同时**成立 |
| 正预算 | `> 0` | `min(cash, value)` 正常拆单 | 金额勾稽 ≤ 预算×缓冲 |

**请特别核**：拟改「拒单条件收窄为 `_cash is not None and _cash <= 0`」是否**恰好**覆盖
「真零拒单 ＋ 不可得 fail-open」，且**不引入第三种未定义态**。

### 2.2 卖出路径

- `order_value`：`value <= 0` 时不得进入拒单分支（`_qs_ov_buy` 守卫）；
- `order_target_value`：`_delta <= 0` 上游已 return，不触该分支；
- **卖出 + 预算不可得**：不得被 fail-open 修正误钳；
- **卖出 + 真零预算**：不得被 fail-open 修正误拒（卖出本不受该分支约束）；
- `b-1/B3` 的额度释放语义**不得**受本次改动影响。

### 2.3 本地与模板逐笔一致（双端差分）

- 本地 ② 层与 PTrade 模板的**拆单序列逐笔一致**（code / amount / 顺序全等）；
- **不接受「两边都空」也算通过**；
- 覆盖三态各测一遍。

### 2.4 最小差分

- 改动**仅** `quantstudio/strategy_compiler/source_import.py` 两处条件表达式（＋随附注释）；
- 不得夹带其他逻辑改动；
- `.format()` 模板**不得引入花括号字面量**（D3）；
- 不得新增 `_QS_` 前缀（D4）；
- 不得新增 `globals().get(...)` 持引用后调用（D5-A：会被 `LOCAL-API-WHITELIST` BLOCK）；
- 不新增/移除任何平台 API 调用。

## 3. 请裁

1. §2.1 三态判据是否正确、是否覆盖完全；
2. §2.2 卖出路径是否无遗漏；
3. §2.3 逐笔差分判据是否足够；
4. §2.4 最小差分是否满足；
5. **是否批准进入③实施**。

---

# 第二件：A-1 契约同步 —— `is_trade()` 公有化后的断言同步

## 1. 送审材料（已在库）

| 材料 | 路径 |
|---|---|
| 送审稿 | `docs/evidence/pd0020-wt-baseline-20261113/a1-contract-sync-review-request.md` |
| 根因证据 | `docs/evidence/pd0020-wt-baseline-20261113/test08-08b-attribution.md` |
| 公开 API 依据 | Context7 `/kay-ou/ptradeapi`；仓内 `portability_rules.py` A-1 注释（逐字引文） |

## 2. 待审问题（按审核指示：单核 initialize 上下文与两处断言）

### 2.1 `initialize` 调用上下文（**单核项**）

请独立核验并给出「通过/不通过＋证据」：

- 产物中 `is_trade()` 的**实际调用点**与上下文结构
  （`initialize` 内 `if not is_trade(): set_backtest()` 一侧是否完整保留）；
- 断言**上下文结构未被改写**（不是被折叠成常量）；
- 断言产物中**不存在**旧改写痕迹（如 `if not False:` 一类残留）；
- **执行级语义**：回测环境与交易环境两种假设下，该分支的**执行结果必须不同**
  —— 改写为 `False` 会使两者**相同**（这正是 A-1 要修的缺陷）。
  建议以受控 stub 注入两种 `is_trade()` 返回值、执行产物 `initialize` 体来取**执行级**证据，
  而非仅文本断言。

### 2.2 两处断言（**单核项**）

| 用例 | 现断言 | 拟改断言 | 请核 |
|---|---|---|---|
| `test_08_remove_banding` | `'is_trade(' not in out` | `'is_trade(' in out` ＋ 不存在旧改写形态 | 方向是否正确？强度是否**不降**？ |
| `test_08b_rewrite_literal_inline` | `'g.mode = False' in out` | 不再生成该旧改写形态 | 同上 |

**须核**：两用例**均保留**（不删、不跳、不放宽为无条件通过）；断言强度方向为
「由禁止公有调用 → 要求公有调用存在」＝**更严格**；每个新断言**成对**含
「必须有新形态」与「必须无旧形态」。

## 3. 请裁

1. 公开 API 依据是否成立（可复核 Context7 与仓内逐字引文）；
2. §2.1 `initialize` 上下文验证设计是否足以替代文本断言作为保真判据；
3. §2.2 两断言差分方向是否正确、是否满足「不削业务断言」；
4. **是否批准进入③实施**。

> 未获批前，**这两项维持红灯**（不静默改、不跳过）。
