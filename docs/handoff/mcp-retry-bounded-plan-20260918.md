# client.py:362 重试有界方案（六步①·方案先行，**禁改码**）

**提出**：数据拉取线 ｜ 2026-09-18 ｜ **状态**：待总调度审计后实施 ｜ **本件未改任何代码**
**来源裁定**：日历 §一二〇 L1179③「MCP client 重试无上界——方案派数据拉取线（有界总时长+超时退出+任务失败标记+审计行；不动成功路径语义；回退=上限可配等效旧行为），方案先行禁直接改码」
**证据来源**：日历 §一一七 L1172（D6 晨窗停滞发现）+ §一二〇 L1178（py-spy 定谳 + 总调度核验在卷）

---

## 一、问题定义（含真锚点：两处，非一处）

### 1.1 现象（实测台账）

| 项 | 实测 |
|---|---|
| 任务 | `mcp_stock_float_share`（source=mcp） |
| 起点 | 03:35:12 START 后静默 |
| 持锁时长 | 任务历时 **3h12m**，写锁全程持有（锁持续持有 9h+ 含等待） |
| 采样 | 12/12 采样 BUSY（非死锁，是**活着但不返回**） |
| 定性 | 非死锁 = 超长任务；主线程 join 等子线程，子线程 fetch 不返回 |

### 1.2 锚点 A（有界但被乘数放大 + 无谓等待）

`quantstudio/pipeline/mcp/client.py` 默认参数（L138-141）：`call_timeout=90.0` / `retry_max=5` / `backoff_sec=(30,60,120,240,480)` / `rate_per_min=200`。
`_call_with_retry`（L345-391）逐次尝试：`_acquire_rate()`（≤60 s）→ 起子线程 → **L362 `th.join(self.call_timeout)`** → 超时抛 `TimeoutError`（**子线程不可取消，继续在后台跑**）→ L373 取退避 → L389 `_sleep_with_heartbeat(wait)`。

**本段最坏 ≈ 5×90（join）+ (30+60+120+240+480)（退避）= 1,380 s ≈ 23 min** —— 有界，但：
- **L389 在最后一次失败后仍睡 480 s 才抛错**（L381 的 `if attempt+1 < retry_max` 只守 reset，不守 sleep）⇒ 纯浪费；
- 每次超时**泄漏一个仍在跑的 daemon 子线程**（Python 线程不可杀），连接/线程逐次累积。

### 1.3 锚点 B（**真正无上界**，3h12m 的实锚 —— 本方案的核心）

L371-389 的异常分支里，**L385 `self._reset_connection()` 不在任何超时包装内**：

    _call_with_retry (L385) → _reset_connection (L394-417) → L417 handshake()
      → handshake (L420-446) → L424 _post_rpc("initialize") 
      → _post_rpc (L238-310) → L258 requests.post(..., timeout=99, stream=True)
      → L287 text = resp.content.decode(...)   ← 一次性读全响应体

`_post_rpc` 用 `stream=True` 却以 `resp.content` 整体读取（L287）。**requests 的 `timeout` 是「连接 + 两次读之间的间隔」，不是总时长**：若服务端以 SSE keep-alive/慢速滴流持续送字节，`resp.content` **永不超时**、无限期阻塞。
而 `handshake()` 是**直接调用** `_post_rpc`（不经 `_call_with_retry` 的 join 包装）⇒ **主线程可被无限期卡在这一步**，重试循环连"下一次尝试"都进不去，任务与写锁一起被无限期持有。

**第二个同源入口**：`quantstudio/pipeline/sources/mcp_adapter.py:401` `self._client.handshake()`（首次取数建会话）——同样不经任何超时包装，首次调用即可挂死。

### 1.4 危害链（为何是"加重因子"）

锚点 B 卡死 ⇒ 任务不返回 ⇒ **写锁不释放** ⇒ ①当日导出/消费者被挡（9/16 追加导出受阻于锁）②daemon 周期停滞 ③D6 晨窗"停滞"告警。
日历口径：「③落地后运行期**长持锁最坏时长显著下降**，是①机制可靠性的前置」。

---

## 二、最小改动方案（4 要素，逐条落到确切位点）

### 设计原则（把「不触碰成功路径与取数语义」变成**可验证**的定义）

> **成功路径** = 任一"在预算内正常返回"的调用：其**返回值、重试次数、退避序列、请求参数、session 复用行为**必须逐项不变。
> **取数语义** = 请求内容（endpoint/headers/params/method 与 SSE 解析结果）不得有任何变化。
> 本方案**只新增一条"总时长上限"，且仅在超出时改变行为**；不新增/不删除任何请求，不改变任何成功返回。

### 要素① 有界总时长

在 `_call_with_retry`（L345）入口取一次**单调时钟预算**：

    deadline = time.monotonic() + budget_sec        # budget_sec 由新参数/环境变量给出

- 每次尝试前算 `remaining = deadline - now`；`remaining <= 0` ⇒ 立即进入要素②（不再起线程）；
- L362 的 join 改为 `th.join(min(self.call_timeout, remaining))`；
- L389 的退避 `wait` 改为 `min(wait, remaining)`（**不睡过头**）；
- `_acquire_rate()` 计入同一预算。

**关键补充（锚点 B）**：把 L385 的 `self._reset_connection()` 与 `mcp_adapter.py:401` 的首次 `handshake()` 一并纳入同一预算包装 —— 抽一个**单一小助手** `_run_bounded(fn, timeout, label)`（线程 + join + 剩余预算），三处复用（attempt 调用 / reset / 首握手）。**不修改 `_post_rpc` 本身**（不动成功路径的那一行 `resp.content`），因此取数语义零变更。

> ⚠️ **待裁项 1**：`mcp_adapter.py:401` 的首次握手是否纳入本件。纳入＝首次调用也被有界（覆盖面完整）；不纳入＝改动面更小但留一个同源无界入口。**本方案建议纳入**（同预算、同助手、零额外语义），请总调度裁定。

### 要素② 超时退出

预算耗尽时抛**专用异常**（建议 `MCPRetryBudgetExhausted`，继承 `MCPTransportError` 以免上层 `except` 语义变化），消息含 `budget_sec/attempts_used/last_err`。
**不改变**"预算内正常成功"与"预算内正常重试后成功"的行为；也**不擅自改变确定性错误（`MCPAuthError`/`MCPProtocolError`，L368-370）的立刻上抛语义**。

> **待裁项 2**：L389「最后一次失败仍睡 480 s 才抛错」是否顺手修为"最后一次不睡"（仅失败路径时序，成功路径无关）。**本方案建议一并修**（1 行），请裁定。

### 要素③ 任务失败标记

**复用既有机制，不新造**：daemon 的 `batch_audit.record(..., "failed", str(e), started_at)`（`daemon.py` L1028 / L1234 / **L1285 全零行路径**）。
预算耗尽异常从 adapter 取数路径上抛 ⇒ 由上述既有路径落一条 `batch_audit` 失败行（含 `error` 摘要）。
⚠️ 实施时**必须实测确认**该异常确实走到落账点（不得只凭推断）——见验收④。

### 要素④ 审计行

- **结构化日志锚点**（沿用既有 `[MCP retry]` 前缀，L374-376 同族）：
  `[MCP retry] BUDGET_EXHAUSTED budget=Ns attempts=k/N last_err=<type>: <msg>`
- **持久面**：daemon 日志（`data/logs/daemon.log`）+ `batch_audit` 失败行（③）。
- **不新增审计文件**（沿用既有落盘面，避免扩面）。

### 新参数与默认值（供要素①与回退）

| 参数 | 建议名 | 默认 | 语义 |
|---|---|---|---|
| 总预算 | `QS_MCP_RETRY_BUDGET_SEC`（env，沿用本仓 `QS_*` 惯例） | **900 s** | `0` 或负值 = **不限（等效旧行为）** |

**默认值依据**：单次 `call_timeout`=90 s、既有退避合计 930 s；正常 MCP 调用为秒级。900 s ≈ 既有"5 次尝试 + 退避"的同一量级且**远低于** 3h12m 实测；如需更大可改。**默认值请总调度核定**。

---

## 三、验收标准（4 条，均为可执行判据）

| # | 判据 | 方法 |
|---|---|---|
| ① | **有界前逐项等价** | 在同一组"成功路径"调用上，`git show HEAD:quantstudio/pipeline/mcp/client.py` 版与新版**返回值逐位相同、请求序列相同**（复用 `agent_workspace/golden_compare.py` 的 HEAD-版双加载技法） |
| ② | **超时路径可测** | 新增单测：以**桩 fn**（sleep 超过预算 / 桩 server 滴流响应）+ **极小预算**（如 2 s）断言：抛出 `MCPRetryBudgetExhausted`、耗时 ≤ 预算 + ε、尝试次数与剩余预算一致、**reset/首握手也被计入预算** |
| ③ | **全量回归零退化** | `pytest` 现有全量（含 ci-smoke 共享层段）零退化；本件只动 `mcp/client.py`（+可选 `adapter:401`） |
| ④ | **黄金对比 + 失败落账实证** | ①在**影子库/隔离环境**跑一次真实 MCP 取数（小表如 `etf_basic`）→ 前后逐值等价；②**制造一次预算耗尽**并实证 `batch_audit` 落 `failed` 行 + 日志含 `BUDGET_EXHAUSTED` |

**测试基建缺口（须先补）**：本仓**无 `tests/test_mcp_client*.py`**（11 个 `test_mcp_*` 命中均为路由/契约类，无 client 直测）⇒ ②需要**新建** client 单测文件 + 桩 transport（不做真实网络调用）。

**当前最坏持锁时长的可量化预期**：由"无限期"→ **≤ budget_sec +（最后一次 `call_timeout` 的上界）＋ 释放开销**；预算 900 s 时最坏约 15–20 min 量级，**较 3h12m 下降一个数量级**。

---

## 四、回退条件（上限可配等效旧行为）

- `QS_MCP_RETRY_BUDGET_SEC=0`（或不设任何新参数时的**显式关闭路径**）⇒ **完全退回今日行为**：无预算判定、无新异常、join/退避/reset 与现状逐行一致。
- **回退有效性必须实证**（不得只声明）：仿 Part A 的 `write_lock_rollback_probe` 形态，出一次性探针脚本，跑 A（预算关闭=旧行为）/B（预算开启=有界）两态并断言差异仅在于"超时后是否退出"。
- 代码回退点：实施前建 `git stash create -u` + `store` 回退点并登记哈希（沿用共享核心文件纪律）。

---

## 五、六步实施序（本件＝①；②-⑥待本件审计通过后启动）

| 步 | 内容 | 状态 |
|---|---|---|
| **①** | **方案（本件）** | **待审计** |
| ② | 建回退点 + 建 client 单测基建（桩 transport） | 待批 |
| ③ | 改 `client.py`：预算 + `_run_bounded` + 专用异常 + 日志锚点（+ 待裁项 1/2） | 待批 |
| ④ | 自测：判据①②③ + 回退探针（实施侧证据，**非验收结论**） | 待批 |
| ⑤ | 黄金对比 + 失败落账实证（判据④，**隔离环境**） | 待批 |
| ⑥ | 交独立验收（独采）+ 单一目的 commit（推送待用户确认） | 待批 |

---

## 六、边界与红线（本件遵守）

1. **本件禁改码**：全文只读取证（`read/grep`）+ 出方案，未编辑任何 .py。
2. 不改 `_post_rpc` 的 `resp.content` 行、不改 SSE 解析、不改请求参数 ⇒ **取数语义零变更**。
3. 不改成功路径的返回、重试次数、退避序列 ⇒ **逐项等价可验**。
4. 不新造审计面/失败落账机制，一律复用既有（`batch_audit` + `[MCP retry]` 日志）。
5. 不重启 daemon、不触碰主库、不动 `config/`。
6. 泄漏线程的**根本治理**（无线程化传输 / filelock 家族迁移）**不属本件**，归 T2 锁链设计；本件只保证"任务与锁在预算内退出"。

## 七、待总调度裁定项（3 条）

1. **首握手是否纳入预算**（`mcp_adapter.py:401`）——建议**纳入**。
2. **最后一次失败后的 480 s 无谓等待**是否顺手修（1 行）——建议**修**。
3. **默认预算取值**（建议 900 s）与环境变量名（建议 `QS_MCP_RETRY_BUDGET_SEC`）。

## 八、证据引用（复取即用）

- 裁定：`docs/handoff/dispatch-calendar-20260911.md` §一二〇（L1176-1182）、§一一七（L1172）
- 代码位点：`quantstudio/pipeline/mcp/client.py` L138-141（默认）/ L238-310（`_post_rpc`，阻塞读 L287）/ L345-391（`_call_with_retry`，join L362、reset L385、退避 L389）/ L394-417（`_reset_connection`）/ L420-446（`handshake`）；`quantstudio/pipeline/sources/mcp_adapter.py:401`；`quantstudio/pipeline/daemon.py` L1028/L1234/L1285（失败落账）
- 黄金对比技法：`agent_workspace/golden_compare.py`（HEAD 版双加载 + 逐项等价）
- 回退探针技法：`agent_workspace/write_lock_rollback_probe.py`（A/B 两态）
---

## 九、实施记录（六步③，2026-09-18 · 审计通过件已落地）

**三裁定全部纳入**：①首握手纳入预算（`mcp_adapter.py:401` → `handshake_bounded()`）；②末次失败不再空等退避；③默认预算 900 s + 变量名 `QS_MCP_RETRY_BUDGET_SEC`。

| 文件 | 改动 | 说明 |
|---|---|---|
| `quantstudio/pipeline/mcp/errors.py` | +23 | 新增 `MCPRetryBudgetExhausted(MCPTransportError)`（继承保上层语义），携带 budget/elapsed/attempts/last_err |
| `quantstudio/pipeline/mcp/client.py` | +135 / −26 | 预算常量与解析、构造参数 `retry_budget_sec`、`_run_bounded`、`_raise_budget_exhausted`、重写 `_call_with_retry`、拆分 `handshake(timeout)/_handshake_impl` + `handshake_bounded`、`_reset_connection(timeout)` |
| `quantstudio/pipeline/sources/mcp_adapter.py` | +2 / −1 | 首握手改走有界入口（裁定1） |
| `tests/test_mcp_client_budget.py` | 新增 11 用例 | 桩函数/桩 `_post_rpc`，零网络 |

**落点（与方案 §二 逐条对应）**：预算取 `time.monotonic()` deadline；join 收窄为 `min(call_timeout, remaining)`；退避收窄为 `min(wait, remaining)`；`_reset_connection(timeout=_remaining())` ⇒ **真锚点纳入预算**；预算耗尽抛专用异常并记 `[MCP retry] BUDGET_EXHAUSTED` 审计行。

**回退**：`QS_MCP_RETRY_BUDGET_SEC=0` ⇒ 无预算判定、重握手 `timeout=None`（旧无界语义）、末次退避恢复旧行为 —— 探针 A/B 两态实证 PASS。

**实施侧自测（非验收结论，验收归六步④独采）**：

- 新件：`tests/test_mcp_client_budget.py` **11 passed / 3.30 s**
- 回归子集（13 文件，含 T7 交接包全套）：**207 passed / 40.29 s**，零退化
- 回退探针 `agent_workspace/mcp_retry_budget_probe.py`：**RESULT: PASS**（A1-A5 旧行为等价；B1-B4 有界；**B2 真锚点：_post_rpc 挂死时重握手 0.52 s 受界返回**）
- 黄金对比 `agent_workspace/mcp_retry_golden_probe.py`（HEAD 树 vs 当前，zip 解包避 tar 的 CJK 缺陷）：**PASS（差异集 = 已批准差异集）**——10 项中 8 项逐位相同（成功路径/尝试次数/最终错误类型与文案/不可重试路径），仅 2 项已申报差异：①`_reset_connection` 新增 `timeout` 形参（预算=0 时值=None=旧语义）；②退避次数 3→2（裁定2）。
- 回退点：`git stash` `eb731a570c884cb62e7d7c5d18739abd19ffb544`（实施前工作树快照）
---

## 十、六步④独采发现与修复（2026-09-18，追加提交，不改写已审历史）

**独采结论**：有条件通过——九项通过，揪出一处**回退等价缺陷**。

### 缺陷（独采发现，本槽已独立复现确认）

`QS_MCP_RETRY_BUDGET_SEC=0`（回退模式）下 `_call_with_retry` 的 `wait_s=None` → `_run_bounded` 走**无界 join**；而真旧代码恒有 `th.join(call_timeout)`（每 attempt 硬界 90s）。
⇒ 回退开关**丢掉了旧行为的第二道防线**：滴流挂死时无限阻塞，**比真旧行为更糟**，直接违反「0=逐行等效旧行为」——而回退开关恰是应急时唯一敢按的钮。

**本槽独立复现（before/after 物理证据）**：同一复现脚本（budget=0 + 挂死 fn + call_timeout=0.5 + retry_max=1，外层 6s 超时判界）

- `c8c1b75`（修复前）：**6.04s 内未退出**（被强制 Kill）⇒ 无界成立
- 修复后：**0.50s 受界退出**，`MCPTransportError: MCP 重试 1 次仍失败: <lambda> 超过 0.5s 未返回`

**探针未暴露的原因（独采指出，采信）**：回退探针 A 组桩 fn 从不挂死，只测次数/文案/reset/退避。已补 A6 组堵住该测试面。

### 修复（三处，均在批准边界内）

1. **1 行修复**：`wait_s = float(self.call_timeout) if rem is None else min(float(self.call_timeout), max(0.0, rem))` —— 回退模式恢复每次尝试的 `call_timeout` 硬界。
2. **补 1 例测试**（独采指定）：`test_rollback_mode_keeps_per_attempt_hard_timeout` —— budget=0 + 挂死 fn + 小 call_timeout ⇒ 断言尝试满 retry_max、总耗时 ≤ call_timeout×retry_max+ε、文案逐字一致。
3. **补文档**（独采附加条件）：类 docstring 增「预算粒度」声明——**每次调用点独立计算，非整任务/整日累计**；「最坏持锁上界 = 调用次数 × 预算」。

**本槽自查追加一处（同类缺口，一并修）**：`_run_bounded` 的 TimeoutError 文案原为「…未返回（预算/超时收窄）」，与旧代码「…超过 {call_timeout}s 未返回」**不逐字一致** ⇒ 已改为 `f"{label} 超过 {float(timeout)}s 未返回"（收窄时数值自然不同）。

### 修复后验证（实施侧，非验收）

- 新件：**12 passed**（11 + 独采指定用例）
- 邻域回归：13 文件 **207 passed** 零退化
- 回退探针：**PASS**（A1-A6 + B1-B4 + B2 真锚点；A6.2 总耗时 0.94s 受界、A6.3 文案一致）
- 黄金对比：**PASS（差异集 = 已批准差异集）**，基线由 HEAD 改为 `c8c1b75^`（= f81436f 真旧代码；独采指出 HEAD 已含新码致比对无信息量），并**新增 timeout 路径用例**——该路径修复后与真旧代码**逐位相同**（12 项 10 同 / 2 项已申报差异）
