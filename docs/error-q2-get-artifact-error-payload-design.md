# Q2 修复设计（六步轻量）：`get_artifact` 载荷级 error 显式识别与降级留痕

- 状态：**方案（呈快审）**｜日期：2026-09-25｜归属：客户运维会话 1
- 案件：jabberwock 取数周期 hold（`get_artifact 缺少 Parquet base64 字段: keys=['error','artifact_id']`）
- 分析依据：`docs/jabberwock-incremental-12h-three-question-analysis.md` §Q2
- **修复前置纯增益审计（三型判定）= 纯恢复型（正常路径零变化）＋ 新增检测型（对原先未处理的 error 载荷形态新增能力）**——按铁律第 4 型**单列**，不按纯恢复型混办

## 1. 问题定义（取证）

| 步 | 事实（`quantstudio/pipeline/mcp/client.py`） |
|---|---|
| ① | 工具级错误通道**已存在**：`:359-370` —— `result.isError` → `MCPToolError`；`result["error"]`（dict）→ `MCPToolError` |
| ② | 客户错误**在载荷内**：工具**成功返回**结构化载荷，其中含 `error` 键（另含 `artifact_id`）→ **未走**上述两通道 |
| ③ | `get_artifact`（`:796-810`）四个 base64 字段全空 → **`raise MCPProtocolError("…缺少 Parquet base64 字段: keys=[…]")`** |
| ④ | 后果：`MCPProtocolError` 被上层视为**不可重试的协议违例** → 无重试、无降级、无结构化留痕 ⇒ **周期 hold、水位可永久卡死** |

**定性**：**框架缺陷成立**——「服务端把错误放进 artifact 载荷」属协议内可预期形态，客户端必须显式分类处理。

## 2. 改动范围（最小、纯增量；单文件为主）

| # | 落点 | 改动 |
|---|---|---|
| ① | `mcp/client.py::get_artifact` | 取回 `d` 后**先判载荷级 error**：`if d.get("error"):` → 提取 message（截断 400）+ `artifact_id`/`job_id` → 走 ② 分类 |
| ② | 同文件（新增异常或复用） | **可重试分类**：瞬时类（timeout / not ready / busy / 429 / 5xx 语义）→ `retryable=True`；其余（artifact 过期 / 不存在 / 参数错）→ `retryable=False` |
| ③ | 同文件 | **留痕**：结构化字段（`artifact_id` / `job_id` / `error 原文截断` / `retryable`）随异常与日志落盘（复用既有审计行通道，不新增机制） |
| ④ | 同文件（文案） | 保留 `keys=[…]` 提示（现价值高）**并补 error 原文**；`MCPProtocolError` 仅保留给**真正的协议违例**（如无 `result`） |

**第二处（属方案内取证项，暂不落码）**：调用侧（export / 消费侧）如何把该类失败**降级**而非永久 hold
（对齐既有 `dead_letter_max` 死信机制，`qfq_resident_orchestrator:759`）——须先定位 hold 的确切触发点，
在方案审计通过后作为**第二笔**实施（不与本笔混合）。

## 3. 影响面（铁律前置审计三问）

| 问 | 结论 |
|---|---|
| ① 是否影响项目其他功能 | **否**：仅新增分支处理「原先必然抛协议错误」的形态；无 error 载荷时**逐字走原路径** |
| ② 是否影响回测性能 | **否**：不在回测热路径；仅当服务端返回 error 载荷时行为改变（由「上抛」变「分类 + 可重试」） |
| ③ 是否影响回测精度 | **否**：不触碰任何数据/口径/取数语义；异常分类不改变成功路径返回值 |

**类型**：纯恢复型（成功路径零变化）＋ 新增检测型（新增对 error 载荷的识别与分类）。

## 4. 验收标准

| # | 判据 |
|---|---|
| V1 | **载荷级 error**（构造 `{"error": {"message": "..."}, "artifact_id": "job/shard"}`）→ 断言：抛**专用分类异常**、`retryable` 取值正确、留痕字段齐全（artifact_id/job_id/error 原文） |
| V2 | **瞬时类** → 断言走重试路径（重试计数 + 审计行可见）；重试成功后**返回值与正常路径一致** |
| V3 | **不重试类** → 断言显式失败（不静默），且错误文案含 error 原文与 keys 提示 |
| V4 | **成功路径 golden 逐项一致**：正常 artifact（含 `verify_sha256` 两条分支）返回值/字段/异常行为与改前逐项一致 |
| V5 | 回归全绿：MCP 相关套件（`test_mcp_*`）+ 既有 `get_artifact` 相关用例 |
| V6 | 证据入 `docs/evidence/`（含 V1–V4 断言与 V5 结果） |

## 5. 回退条件

- 触发：任一验收项 FAIL 或出现「成功路径行为漂移」→ **立即回退**；
- 方式：**精确 hunk 反向补丁**或新建反向提交（**禁用整文件 `git checkout`**，CASE-009 教训）；
- 回退点：实施前建 `git stash create -u` + `store`（零副作用）。

## 6. 期限与派单

| 项 | 内容 |
|---|---|
| 归属 | 客户运维会话 1（案主） |
| 期限 | 方案呈审（本件）→ 审计通过后**当轮实施 + 验收**（快审通道）；第二处（调用侧降级）随第一笔验收后单独立笔 |
| 提交纪律 | 精确清单 + edit 后即时 `git diff` 自检；单文件为主，无共享核心文件叠加 |
