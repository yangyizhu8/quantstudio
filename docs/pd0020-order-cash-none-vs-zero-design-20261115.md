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

### 2.2 注释重写（**必须同 commit**，独立②审生效条件①）

现注释逐字写着「零/**不可得**预算 ⇒ 显式拒单」「原 fail-open 按目标全额放行 = 超买根因」
⇒ 判据收窄后即成**假陈述**。这正是本次缺陷的成因类别（**码-注自相矛盾**），不改等于原地复刻病灶。

| 位置 | 现注释（**收窄后失真**） | 重写要求 |
|---|---|---|
| `order_target_value` L1495-1497 | 「零/不可得预算 ⇒ 显式拒单，不回落原生命令（原 fail-open 按目标全额放行 = 超买根因）」 | 改为：「**仅真实零预算（`budget ≤ 0`）⇒ 显式拒单**；**预算不可得（None）⇒ fail-open**（沿用 M2：钳制不生效，保持平台现状全额下单）」 |
| `order_value` L1699-1701 | 「零/不可得预算 ⇒ 显式拒单——仅买入路径生效」 | 同上口径（区分 two 态），并保留「卖出不受影响」的既有说明 |

⚠️ 注释必须与条件表达式**同 commit** 重写；
⚠️ 禁止只改代码不改注释（本缺陷的复发模式即由此而来）。

### 2.3 告警口径

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
| **⚠️ R1 残余通道（既有契约限制·非本方案引入，但须补记）** | `_qs_cash_avail()` 三路探测**全部以 `> 0` 为返回条件** ⇒ context 在位但 `portfolio.cash == 0.0`（满仓/资金耗尽）时**不返回 0**，继续探测后归入 `None` ⇒ fail-open ⇒ **全额下单** | ⇒ 三态表中「`_cash == 0` ⇒ 真零拒单」**实际只覆盖 `cash − reserved == 0`**（现金>0 但跨标的预留耗尽）这一条可达路径；「**账户真零现金**」这条残余通道**仍会被 fail-open 吞掉**。属**既有契约的已知限制**（D4-S7 #5 平台可透支背景），**非本方案引入**，本方案**严格优于现状**（现状连预留耗尽态都判不对）。**后续独立立项**（方向示例：`context` 在位即返回 `float(cash)` 含 0）——**改变 M2 契约语义，禁止夹带本轮** |
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

> ⚠️ **措辞修正（2026-11-15 依独立②审结论）**：**真零态双端合法地产出零订单**
> （模板侧：拒单三件套；本地侧：`cash_avail=0.0` ⇒ `budget=0` ⇒ `([],0)` ⇒ `_qs_noop_target` 返 `None`）。
> 故「不接受两边都空」**不能**无条件套用于真零态，否则该态永不可满足（或被弃测即被 game）。

| 态 | 双端判据 | 「序列非空」是否适用 |
|---|---|---|
| **不可得（None）** | 双端**逐笔一致**（code/amount/顺序全等）**且序列非空** | ✅ 适用 |
| **真零（≤0）** | 双端序列**皆空** ＋ **模板侧拒单三件套**（`QS_CASH_BUDGET_ZERO` ＋ 计数递增 ＋ 返回 `None`）＋ **本地侧 no-op 证据**（`delta_below_one_lot` 等 no-op 路径） | ❌ **不适用**（合法皆空） |
| **正预算（>0）** | 双端**逐笔一致**且序列非空 **且** 金额勾稽 ≤ 预算×缓冲 | ✅ 适用 |

⇒ **「不接受两边都空」仅约束不可得态与正预算态**；
真零态以「皆空 ＋ 模板三件套 ＋ 本地 no-op」**三元组**判定，不以非空判定。

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

### 5.3 R2：知识条目措辞随批修订（独立②审生效条件③）

`knowledge/ptrade-deploy/PD-2026-0020.md` frontmatter 的 `fix_target` 逐字含：

> 「… `_qs_cash_budget`/`_qs_cash_avail`（预算口径 + ε 缓冲 + **None fail-closed**）」

⇒ 与在码契约（`_qs_cash_avail` docstring「**禁止：取不到禁单价**」、`order_target_value` L1487-1489
「M2 fail-open」、基线同文）**正面冲突**；且该条目讨论节 D1-D5 **均未**把
「None ⇒ fail-closed 拒单」立为已裁定语义（`fix_target` 短语无讨论节背书）。

⇒ **a-2 缺陷极可能正源于此**：按 KB 实施者会照抄「None fail-closed」而复刻同缺陷。

**处置（随③实施同 commit，属知识面动作，不违最小差分——那只约束代码面）**：

| 项 | 修订 |
|---|---|
| `fix_target` | 「…（预算口径 + ε 缓冲 + **None fail-open（钳制不生效）＋真零(≤0) 拒单**）」 |
| 讨论节 | 补记本次判据：**不可得 ≠ 零预算**，两者必须分判；`None` fail-open 沿用 M2 |
| `recurrence` | 依本次复发情况更新（原 `recurrence: 0`） |

## 六、②审范围（2026-11-15 审核指定，四项须逐项覆盖）

> 以下四项为**②审必查项**，缺一即不得进入③实施；每项须给出「通过/不通过＋证据」。

### 6.1 卖出路径

| 检查点 | 判据 |
|---|---|
| `order_value` 卖出（`value <= 0`） | 不进入拒单分支（`_qs_ov_buy` 为 False）⇒ 行为与基线逐位一致 |
| `order_target_value` 卖出（`_delta <= 0`） | 上游已 return ⇒ 不触该分支；`b-1/B3` 的额度释放语义**不受影响** |
| 卖出 + 预算不可得 | **不得**因 fail-open 修正而被误钳 |
| 卖出 + 真零预算 | **不得**因 fail-open 修正而被误拒（卖出本不受该分支约束） |

### 6.2 真零预算拒单

| 检查点 | 判据 |
|---|---|
| `_cash == 0` | **必须拒单**：返回 `None` ＋ `_QS_CASH_REJECT_COUNT` 递增 ＋ 记 `QS_CASH_BUDGET_ZERO(reason=zero_budget)` |
| 三项同时成立 | 任缺其一即判不通过 |
| 不得被 fail-open 吞掉 | 若真零预算也放行 ⇒ **等于拆掉超买闸门** ⇒ **立即回退** |

### 6.3 最小差分

| 检查点 | 判据 |
|---|---|
| 改动文件数 | **仅** `quantstudio/strategy_compiler/source_import.py`（两处条件表达式） |
| 改动行数 | **仅条件表达式本身 ＋ 随附注释**；不夹带其他逻辑改动 |
| 模板渲染等价 | `source_import.py` 是 `.format()` 模板 ⇒ 改动不得引入花括号字面量（D3）；`_QS_` 前缀不得新增（D4） |
| 白名单 | 不改 `_qs_cash_avail`／`_qs_cash_budget`／`_qs_split_order`／`order` 包装／隔离层／持久化层／策略源码 |
| 变量持函数引用 | 不得新增 `globals().get(...)` 持引用后调用（D5-A：`LOCAL-API-WHITELIST` 会 BLOCK） |
| 可移植性 | 不新增/移除任何平台 API 调用；`api_portability` 须仍 PASS |

### 6.4 六策略回归

| 检查点 | 判据 |
|---|---|
| 六策略第一跳**重生** | 逐个 `qs-compile import`（**不沿用既有产物**——D5-B 假绿纪律） |
| `api_portability` | **6/6 PASS** |
| 产物 hash | 重新登记（代码/配置/产物三张 sha256） |
| 隔离层/持久化层 | 隔离三态（不可得/零/正常）与 C 探针行为**不受本次改动影响** |

## 七、请②审裁

1. §二 的两处判据收窄是否正确（`None` fail-open、`0` 拒单）；
2. §4.1 三态表与 §4.2 双端逐笔差分**是否足够作为验收判据**（不接受「两边都空也算过」）；
3. §三 影响面判断是否完整（尤其卖出路径与隔离层）；
4. 是否批准进入③实施。

> 未过审前**不改码**；D5（`test_fund_statement_acceptance`）另按「先证明全部入口避开生产库」推进，
> **保持 unknown**。
