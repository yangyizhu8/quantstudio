# B 桶归因（9 项，逐 nodeid 调用链对照）

> 依据：2026-11-14 审核 —— 「疑似本批」**不是**已证根因；订单语义 5 项与另两项**分开**处理。
> 方法：①测试文件在 `7826aed..HEAD` 的 diff（**5 个文件全部无变化** ⇒ 差异在被测源码）；
> ②逐 nodeid 取当前 junit 的断言与失败信息；③对被测源码做基线↔当前 diff；④查被测函数的既有契约。

## 一、分组

| 组 | 项数 | nodeid |
|---|---|---|
| **A 组：订单语义（根因已确证）** | **4** | `test_ptrade_contract_compliance::test_split_wrapper_target_value_calls_split_order`<br>`test_ptrade_contract_compliance::test_wire_local_vs_template_homology`<br>`test_ptrade_contract_compliance::test_wire_template_order_value_wrapper`<br>`test_pd12_target_semantics::test_t9_dual_end_homology` |
| **B 组：订单语义之外（归因未完成）** | **3** | `test_pd12_target_semantics::test_t11_compliance_stock_cases_still_green`<br>`test_qfq_factor_cache::test_lru_capacity_evicts`<br>`test_daemon_identity_macos::test_v5_debounce_requires_three_consecutive` |
| **C 组：已定位（A-1 `is_trade`）** | **2** | `test_source_import::test_08_remove_banding` / `test_08b_rewrite_literal_inline`（见 `test08-08b-attribution.md`） |

## 二、A 组：根因确证（本批引入，且违反框架既有契约）

### 2.1 失败现象

四项的断言**全部是「应产出订单，实际空」**：

| nodeid | 失败断言 |
|---|---|
| `test_split_wrapper_target_value_calls_split_order` | `应拆单走 order()` → `assert []` |
| `test_wire_local_vs_template_homology` | `双端拆单序列必须逐笔一致` → `assert [] == [('300029.SZ', 44400)]`（模板侧 3 笔、本地侧 0 笔） |
| `test_wire_template_order_value_wrapper` | `px 命中应拆单走 order()` → `assert []` |
| `test_t9_dual_end_homology` | `B1 模板应产出订单` → `assert []` |

### 2.2 差异点（`source_import.py` 的 `order_value`，基线 vs 当前）

基线 `7826aed`（**无**拒单分支）：
```python
_cash = _qs_cash_budget(security)
orders, _tot = _qs_split_order(security, value, _px_exec, cash_avail=_cash)
```

当前 HEAD（**新增**拒单分支，A-2「PD-2026-0020 §12 期1 a-2（②审·P0）」）：
```python
_cash = _qs_cash_budget(security)
_qs_ov_buy = (value is not None and float(value) > 0)
if _qs_ov_buy and (_cash is None or _cash <= 0):
    log.error('QS_CASH_BUDGET_ZERO api=%s ... reason=%s'
              % ('order_value', security, _cash,
                 'unavailable' if _cash is None else 'zero_budget'))
    _QS_CASH_REJECT_COUNT[0] += 1
    return None          # ← 显式拒单
orders, _tot = _qs_split_order(security, value, _px_exec, cash_avail=_cash)
```

### 2.3 为什么基线 PASS、当前 FAIL

`_qs_split_order` 在**基线与当前逐字相同**，其预算语义为：
```python
_budget = min(cash_avail, value) if cash_avail is not None else value
budget  = min(cash_avail, value) if cash_avail is not None else value
use_buffer = cash_avail is not None
```
⇒ **`cash_avail=None` ⇒ 以 value 为预算上界、照常拆单（fail-open）**。

测试环境无引擎 context ⇒ `_qs_cash_avail()` 三条取值路径全部不可得 ⇒ 返回 **None**：

- **基线**：`cash_avail=None` ⇒ `budget = value` ⇒ **正常拆单** ⇒ 断言 PASS；
- **当前**：`_cash is None` ⇒ **命中拒单分支 `return None`** ⇒ 订单列表为 `[]` ⇒ 断言 FAIL。

⇒ **差异点唯一**：A-2 新增分支把 **`None`（预算不可得）** 与 **`0`（真零预算）** 混为一谈。

### 2.4 违反的框架既有契约（逐字）

`source_import.py` 的 `_qs_cash_avail()` docstring：
> 「全不可得 → 返回 **None**（**钳制不生效**，保持平台现状全额下单）＋ 一次性告警 `QS_CASH_AVAIL_UNAVAILABLE`。
> **禁止：取不到禁下单** / 静默逐笔告警 / 引用未登记平台 API（portability BLOCK）。」

⇒ 「**禁止：取不到禁下单**」被 `_cash is None ⇒ return None` **直接违反**。

同时 `order_value` 自身注释亦写明该链路的既有原则：
> 「D4-S7 #5：现金钳制（与 order_target_value 同链，**M1 系数 1.0 / M2 fail-open**）」

### 2.5 定性与修正方向（**待②审，未实施**）

| 情形 | 语义 | 正确行为 | 当前行为 |
|---|---|---|---|
| `_cash <= 0`（**真零预算**） | 确实无预算 | 拒单（显式可见） | 拒单 ✔ |
| `_cash is None`（**预算不可得**） | 取不到，非「无预算」 | **fail-open：钳制不生效，照常拆单** | **拒单 ✘ 违反契约** |

⇒ **修正方向**：拒单条件收窄为「仅 `_cash is not None and _cash <= 0`」；
`None` 一律 fail-open，交回 `_qs_split_order(cash_avail=None)`（与基线一致）。

⚠️ `order_target_value` 存在**同形分支**（同批 A-2），须一并核。

⚠️ **本轮未改任何代码**：属框架层改动，须走六步流水线（①方案→②审→③实施→④验收→⑤用户确认→⑥双仓推送）。

## 三、B 组：归因未完成（如实登记，不作断言）

| nodeid | 失败信息（摘） | 待查 |
|---|---|---|
| `test_daemon_identity_macos::test_v5_debounce_requires_three_consecutive` | 断言 `tab._daemon_state == "running"` / `_daemon_death_streak == 1` / `"1/3" in status_label`；实际状态文本为「⏸ 采集已因回测停止（不会自动重启，需要时请手动开启）」 | **环境差异类（非本批）**：①测试文件与 `quantstudio/gui/tabs/task_tab.py` 在 `7826aed..HEAD` **均无变化**；②`task_tab.py` L930-937 显式区分「因回测停止」文案；③**主仓 `data/backtest_stop.flag` 存在=True，基线 worktree=False** ⇒ 主仓走「回测停止」分支、基线（无 `data/`）走「异常退出」分支 ⇒ 机械上基线 PASS／当前 FAIL，**语义上是环境差异** |
| `test_pd12_target_semantics::test_t11_compliance_stock_cases_still_green` | `compliance 套件红：..........F.F.....` | **本批 A-2 连带（非独立缺陷）**：该用例 L283-288 实为 `subprocess.run([... "pytest", "tests/test_ptrade_contract_compliance.py" ...])` 并断言 `returncode == 0` ⇒ 是 compliance 套件的**聚合门**；套件内 3 项红**正是 A 组的本批 A-2 失败项** ⇒ 修正 A-2 后应随之转绿 |
| `test_qfq_factor_cache::test_lru_capacity_evicts` | `assert 10 <= 8`，实测 `{'evict': 0, 'hit': 0, 'hit_rate': 0.0, 'miss': 10, ...}` | **他线引入（非本批）**：被测 `quantstudio/pipeline/daemon.py` 在 `7826aed..HEAD` **+107/−15**，容量改为 env 可配（`_QFQ_FACTOR_CACHE_MAX_ENV="QS_QFQ_FACTOR_CACHE_MAX"`、`_QFQ_FACTOR_CACHE_MAX_DEFAULT=512`、新增 `_qfq_factor_cache_max()`，淘汰循环改用 `_cache_max = self._qfq_factor_cache_max()`）；而测试仍按**旧常量 `_QFQ_FACTOR_CACHE_MAX=8`** 断言 ⇒ 插 10 条不触发淘汰 ⇒ `evict=0` ⇒ **他线契约变更未同步测试** |

### 3.1 B 桶人工归因汇总

| 性质 | 项数 | nodeid |
|---|---|---|
| **本批 A-2 连带** | **1** | `test_t11_compliance_stock_cases_still_green` |
| **环境差异类** | **1** | `test_v5_debounce_requires_three_consecutive` |
| **他线引入** | **1** | `test_lru_capacity_evicts` |

### 3.2 对映射台账的处理（不篡改机械结果）

机械映射仍按「基线状态」记录（B 桶＝基线 PASSED／当前 FAILED）；
**人工归因另列**，不因人工判断而改写机械分桶。
⇒ B 桶 9 项人工归因合计：**本批引入 4（A 组）＋ 1（连带）＝5**；**他线 1**；**环境差异 1**；**A-1 契约类 2**（C 组，另件）。

## 四、结论边界

- A 组 4 项：**根因已确证**（本批 A-2 的 `_cash is None` 判据，违反 `_qs_cash_avail` 的
  「禁止取不到禁下单」契约；方案见 `docs/pd0020-order-cash-none-vs-zero-design-20261115.md`）；
- B 组 3 项：**归因已完成**，分别为「本批连带 1 / 环境差异 1 / 他线引入 1」，三种性质**不得混为一类**；
- C 组 2 项：根因＝A-1（`is_trade` DENY→KEEP），见另件；
- **基线同 fail ≠ 当前已修**；A 桶 100 项根因仅 2 项已定位。

