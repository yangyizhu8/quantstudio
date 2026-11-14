# 六步①方案：区分「预算不可得」与「真实零预算」（订单侧金额 API 拒单判据修正）

> 状态：**①方案**（未实施、未改码）。过②审后方可进入③实施。
> 触发：B 桶 4 项订单语义回归（基线 PASS／当前 FAIL）根因已确证（见
> `docs/evidence/pd0020-wt-baseline-20261113/bucket-b-attribution.md`）。

## 知识反哺检索留痕

- 知识反哺：[命中] PD-2026-0020（PD 族 / ptrade-deploy / `knowledge/ptrade-deploy/PD-2026-0020.md`，2026-11-12）
  —— 采纳要点：§12 期1「甲方案·order 包装」与 D3/D4/D5 框架约束；本期新增 D6「测试须随契约同步」。
- 知识反哺：[命中] PD-0018（PD 族 / ptrade-deploy / `knowledge/ptrade-deploy/PD-0018*.md`，2026-11-11）
  —— 采纳要点：F3(a) 预算口径 `_qs_cash_budget`（跨标的累计扣减）与 **M2 fail-open** 约定。
- 知识反哺：[无命中] 其他族（QD / 工具链等）无涉及「现金预算不可得 vs 零预算」判据的条目，2026-11-15。

## 一、问题定义

`source_import.py`（共享核心文件，hop-1 模板）的两个金额 API 在本批 A-2 中新增了
「零/不可得预算 ⇒ 显式拒单」分支，其判据把**两种不同语义**混为一谈：

| 位置 | 函数 | 本批新增分支 | 基线 `7826aed` |
|---|---|---|---|
| **L1498** | `order_target_value` | `if _cash is None or _cash <= 0:` → 拒单 | **无此分支** |
| **L1706** | `order_value` | `if _qs_ov_buy and (_cash is None or _cash <= 0):` → 拒单 | **无此分支** |

两种语义必须分开：

| `_cash` 取值 | 真实含义 | 正确行为 | 当前行为 |
|---|---|---|---|
| `None` | **预算取不到**（不可观测） | **fail-open**：钳制不生效，保持平台现状全额下单 | **拒单** ✘ |
| `<= 0`（且非 None） | **确实没有预算** | 拒单（显式可见、可计数） | 拒单 ✔ |

### 1.1 违反的既有契约（逐字）

**（a）`_qs_cash_avail()` docstring**（`source_import.py` L1124-1128）：
> 「全不可得 → 返回 **None**（**钳制不生效**，保持平台现状全额下单）＋ 一次性告警 `QS_CASH_AVAIL_UNAVAILABLE`。
> **禁止：取不到禁下单** / 静默逐笔告警 / 引用未登记平台 API（portability BLOCK）。」

**（b）`order_target_value` 同段注释**（L1487-1489，与新增分支**紧邻**）：
> 「fail-open（**M2**）：cash_avail 取数不可得 → **None → 钳制不生效（平台现状全额下单）**＋ 一次性告警。」

**（c）`order_value` 注释**（L1688 附近）：「现金钳制（与 order_target_value 同链，M1 系数 1.0 / **M2 fail-open**）」

⇒ 新增分支与**同一函数内既有注释**直接矛盾，属**自相矛盾**，而非仅外部契约冲突。

### 1.2 实证后果（B 桶 4 项回归）

| nodeid | 失败断言 |
|---|---|
| `test_ptrade_contract_compliance::test_split_wrapper_target_value_calls_split_order` | 「应拆单走 order()」→ `assert []` |
| `test_ptrade_contract_compliance::test_wire_local_vs_template_homology` | 「双端拆单序列必须逐笔一致」→ `assert [] == [('300029.SZ', 44400)]` |
| `test_ptrade_contract_compliance::test_wire_template_order_value_wrapper` | 「px 命中应拆单走 order()」→ `assert []` |
| `test_pd12_target_semantics::test_t9_dual_end_homology` | 「B1 模板应产出订单」→ `assert []` |

机制：`_qs_split_order` 基线↔当前**逐字相同**，其 `cash_avail=None` 语义为
「`budget = value`、照常拆单（fail-open）」；测试环境无引擎 context ⇒
`_qs_cash_avail()` 三路不可得 ⇒ 返回 **None** ⇒ 基线正常拆单（PASS），
当前命中拒单分支（FAIL）。

## 二、改动范围

**仅 `quantstudio/strategy_compiler/source_import.py` 两处判据**，**不改**：
- `_qs_cash_avail`（取数与告警契约不动）；
- `_qs_cash_budget`（预算口径不动）；
- `_qs_split_order`（拆单预算语义不动）；
- `order` 包装（甲方案 order 钳制不动）；
- 隔离层与持久化层不动。

### 2.1 拟改内容（**②审通过后**才实施）

```
order_target_value（L1498）：
-  if _cash is None or _cash <= 0:
+  if _cash is not None and _cash <= 0:
     → 仅「真实零预算」拒单；None 交回 _qs_split_order(cash_avail=None) ⇒ fail-open

order_value（L1706）：
-  if _qs_ov_buy and (_cash is None or _cash <= 0):
+  if _qs_ov_buy and _cash is not None and _cash <= 0:
     → 同上；卖出（value<=0）本就不受该分支影响，保持不变
```

### 2.2 告警口径

`QS_CASH_BUDGET_ZERO` 的 `reason` 当前用
`'unavailable' if _cash is None else 'zero_budget'`；改后 `None` 不再进入该分支
⇒ `reason` 只剩 `zero_budget`。**「预算不可得」的一次性告警仍由 `_qs_cash_avail` 的
`QS_CASH_AVAIL_UNAVAILABLE` 承担**（既有契约，不新增、不静默）。

## 三、影响面

| 面 | 评估 |
|---|---|
| 买入路径（`order_value` / `order_target_value`） | **行为变更**：预算不可得时由「拒单」改回「fail-open 全额下单」——恢复基线与契约语义 |
| 卖出路径 | **不变**（`order_value` 由 `_qs_ov_buy` 守卫；`order_target_value` 上方 `_delta <= 0` 已 return） |
| 真实零预算 | **不变**（仍拒单、仍计数 `_QS_CASH_REJECT_COUNT`、仍打 `QS_CASH_BUDGET_ZERO`） |
| 隔离层 | 不涉及（隔离层用 `_QSClampHooks`，与 `_qs_cash_budget` 路径不同） |
| 可移植性 | 不涉及（无新增/移除 API 调用） |
| 策略源码 | **零改动**（框架层修复，经重转生效） |

## 四、验收标准（**双端差分判据**）

### 4.1 三态行为表（新增判据，禁止只断言文本）

在受控 stub 下，对 `order_value` / `order_target_value` **各测三态**：

| 态 | `_qs_cash_budget` 返回 | 期望 | 判据 |
|---|---|---|---|
| **不可得** | `None` | **fail-open**：产出订单（与基线一致） | 订单序列**非空** ＋ 双端逐笔一致 |
| **真零预算** | `0` | **拒单**：返回 `None` ＋ `_QS_CASH_REJECT_COUNT` 递增 ＋ 记 `QS_CASH_BUDGET_ZERO` | 三项同时成立 |
| **正常** | `> 0` | 按 `min(cash, value)` 正常拆单 | 金额勾稽 ≤ 预算×缓冲 |

### 4.2 双端差分（本地 ↔ PTrade 模板）逐笔一致

- **本地侧**（QuantStudio ② 层）与**模板侧**（`source_import` 产物）在同一输入下的
  **拆单序列逐笔一致**（code、amount、顺序全等）；
- 判据：`test_wire_local_vs_template_homology` 的**逐笔相等**语义，
  **不是**「两边都空」也算通过；
- 覆盖三态：不可得 / 真零预算 / 正常，各测一遍。

### 4.3 回归判据

| 项 | 判据 |
|---|---|
| B 桶 4 项 | 4 个 nodeid 复跑**全绿** |
| 卖出不钳 | 4 态 × 2 API 全测：`value<0` 时 `applied=False`、数量不钳 |
| 真实零预算仍拒 | 不可被 fail-open 吞掉（否则本修复＝放宽超买） |
| 框架回归 | `tests/test_source_import.py` 全文件 ＋ `tests/test_api_portability_rules.py` ＋ 隔离/部署相关套件**全绿** |
| 六策略重转 | 重生后 `api_portability` **6/6 PASS** ＋ 产物 hash 重新登记 |

## 五、回退条件

1. 三态判据中「真零预算仍拒单」不成立 ⇒ **立即回退**（否则等于把超买闸门整体拆掉）；
2. 双端逐笔一致在「不可得」态下**仍**不一致 ⇒ 回退并重查 `_qs_split_order` 口径；
3. 出现 `LOCAL-API-WHITELIST` BLOCK 或 `P4_equiv` FAIL ⇒ 回退；
4. 任何策略源码改动需求 ⇒ 停止本方案（修框架层，不改策略）。

## 六、请②审裁

1. §二 的两处判据收窄是否正确（`None` fail-open、`0` 拒单）；
2. §4.1 三态表与 §4.2 双端逐笔差分**是否足够作为验收判据**（不接受「两边都空也算过」）；
3. §三 影响面判断是否完整（尤其卖出路径与隔离层）；
4. 是否批准进入③实施。

> 未过审前**不改码**；D5（`test_fund_statement_acceptance`）另按「先证明全部入口避开生产库」推进，
> **保持 unknown**。
