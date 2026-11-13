# test_08 / test_08b 归因（只读定位结论）

> 状态：**归因已定位**；断言修订**未执行**（R-20261113-04 裁乙：随 A-1 契约变更批次走六步②审）。

## 一、结论

`test_08_remove_banding` 与 `test_08b_rewrite_literal_inline` 的失败
**归因于本批 A-1 改动**：`quantstudio/strategy_compiler/portability_rules.py` 中
`is_trade` 由 **DENY_REMOVE（档 2 改写为字面量 False）** 迁入
**PTRADE_REGISTERED_WARN（KEEP，保留原调用）**。

## 二、证据链

### 基线（`7826aed`）——`portability_rules.py`

```
L20:  "is_trade",              # 本地回测模式标记（ptrade_api.py:2194），档 2 等价字面量 False
L212: "set_fixed_slippage", "set_commission", "set_volume_ratio",
```

⇒ `is_trade()` 被改写为 `False` ⇒ 产物中无 `is_trade(` ⇒ `test_08` 的
`assert 'is_trade(' not in out` **通过**；同时改写路径产生 `g.mode = False`
⇒ `test_08b` 的 `assert 'g.mode = False' in out` **通过**。

### 当前（HEAD）——`portability_rules.py`

```
+    "is_trade",              # 依据：api-reference-en/utilities.md（逐字引文见 DENY_REMOVE 处 A-1 注释）
+    "get_research_path",     # 依据：docs/original/Ptrade国盛.md（逐字引文见 A-2 注释）
```
（该块属于 `PTRADE_REGISTERED_WARN`，语义为 **KEEP**）

⇒ `is_trade()` **保留原调用** ⇒ 产物中出现 `is_trade(` ⇒ `test_08` 失败；
且不再生成 `g.mode = False` ⇒ `test_08b` 失败。

### 排除性证据（source_import.py 不涉案）

```
git diff -U0 7826aed HEAD -- quantstudio/strategy_compiler/source_import.py
hunk = 12, +242 行, -3 行
```
hunk 落点全部为 `_QSOrderRefState` / `_QS_CASH_WARNED_STATE` /
`_qs_reserve_cash` / `order_target_value` / `order` / `order_value` /
`SourceConverter`，**新增与删除行中无任何一行含 `banding` / `literal` /
`g.mode` / `is_trade`** ⇒ 失败**不由** `source_import.py` 本批改动引起。
原始 diff 见同目录 `batch1-source-diff-for-test08.txt`。

## 三、性质判定（关键）

这是**有意的契约变更**，**不是缺陷**：

- `is_trade()` 是 **PTrade 公有 API**（依据 Context7 `/kay-ou/ptradeapi`：
  `is_trade()` 用于区分**交易环境与回测环境**，**不是**本地自创函数）；
- 基线把它当「本地回测模式标记」改写为 `False`，属于**把公有 API 误当本地实现**；
- A-1 修正后保留原调用，是**回归正确语义**；
- 因此 **`test_08` / `test_08b` 的断言已过时**（断言的是修正前的旧契约）。

⇒ 结论：**测试须随契约同步（D6）**，而非回退 A-1。

## 四、处置路径（裁乙，不静默改、不永久红灯）

**随 A-1 契约变更批次走六步流水线②审**，送审材料须列明：

1. **`is_trade` 保留语义**：公有 API 逐字文档依据（Context7 `/kay-ou/ptradeapi`
   ＋ `api-reference-en/utilities.md`），说明为何不能改写为 `False`；
2. **两断言 diff**：

| 用例 | 现断言（过时） | 拟改断言 |
|---|---|---|
| `test_08_remove_banding` | `assert 'is_trade(' not in out` | `assert 'is_trade(' in out`（保留原调用）＋ 断言**不再**被改写为 `False` |
| `test_08b_rewrite_literal_inline` | `assert 'g.mode = False' in out` | 断言**不再**生成该改写（旧改写路径已随 DENY 移除而消失） |

3. **不削业务断言**：仅把「断言旧改写行为」改为「断言新契约行为」，
   **不删除**该用例，也不放宽为无条件通过。

> 在②审通过前，**这两项维持红灯**（不静默改、不临时跳过）。

## 五、与「基线既有 7 项」的区别（不得混同）

| 组 | 基线 | 当前 | 归因 | 根因 |
|---|---|---|---|---|
| `test_22/23/27/32/33/34/35`（7 项） | FAILED | FAILED | **基线既有** | **未定位**（≠已证根因相同） |
| `test_08` / `test_08b`（2 项） | PASSED | FAILED | **已证本批新增** | **已定位**：A-1 `is_trade` DENY→KEEP |

> 7 项基线同失败**不等于**根因相同，**不免除**当前修复义务；
> 第一批 50 passed 中**不含** 135 清单内任何项 ⇒ **不等于 135 项里解决了 50 项**。
