# Q2 实施与验收：`get_artifact` 载荷级 error 分类与留痕（2026-09-25）

- 方案：`docs/error-q2-get-artifact-error-payload-design.md`（过审 `cb04775`）｜归属：客户运维会话 1
- 案件：jabberwock 取数周期 hold（`get_artifact 缺少 Parquet base64 字段: keys=['error','artifact_id']`）
- 状态：**实施 + 验收完成（当轮）**；第二笔（调用侧降级不永久 hold）单列，待启动

## 1. 改动清单（精确路径）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/mcp/client.py` | **+73 / −4**：① 模块级 `_ArtifactPayloadError`（内部哨兵）+ `_payload_error_retryable()`（瞬时/确定性关键词分类，**未知默认可重试**）；② `get_artifact` 重写取数段：`_fetch()` 内**显式识别载荷级 error** → 瞬时类抛哨兵（由既有 `_call_with_retry` 按预算/退避重试）→ 确定性类原样返回后**外层立即显式失败**；③ **留痕**：异常消息含 `error 原文（截断 400）` + `artifact_id` + `job_id`（重试耗尽再补 `attempts`）；④ `MCPProtocolError` **只留给真协议违例**（无 error 载荷却缺 base64），并补上下文 |
| `tests/test_mcp_get_artifact_error_payload.py` | 新增 6 用例（V1–V5 + V2b） |

## 2. 验收（V1–V6）

| # | 判据 | 结果 |
|---|---|---|
| V1 | 载荷级 error（含 artifact_id）→ `MCPToolError`（**非** `MCPProtocolError`）+ 留痕齐全 | **PASS**（断言：类型、`载荷级 error`、`not found` 原文、`artifact_id=`/`job_id=`、`tool`/`is_error`、**调用恰 1 次**） |
| V2 | 瞬时类 → 走既有重试；重试成功则返回值一致 | **PASS**（`retry_max=3` 时调用 3 次后抛 `MCPToolError` 且含原文；V2b：先失败后成功 → 正常返回 bytes，调用 2 次） |
| V3 | 确定性类 → **不重试**、显式失败 | **PASS**（4 组消息 × 调用恰 1 次：`no such` / `expired` / `缺失` / `非法`） |
| V4 | **成功路径零变化** | **PASS**（正常载荷 bytes/sha256/size 一致；`sha256` 不匹配仍抛 `MCPChecksumError`——原行为不变） |
| V5 | MCP 相关套件回归全绿 | **PASS**：`test_mcp_fetch_routing` / `client_budget` / `export_cache` / `streaming` / `wide_text_routing` / `etf_latest_anchor` = **61 passed**；本件 6 passed |
| V6 | 证据入档 | **PASS**（本文件） |

**三型判定复核**：**纯恢复型**（成功路径零变化，V4 实证）＋ **新增检测型**（对原先未处理的 error 载荷新增识别与分类）—— 依铁律第 4 型单列，不与纯恢复型混办。

## 3. 测试驱动发现的两项实现要点（如实记录）

1. **中文确定性关键词需补全**：初版关键词表含 `invalid/无效` 但漏 `非法` → `非法 artifact_id` 被判为「未知 → 可重试」→ V3 失败。
   已补 `非法 / 不正确 / 格式错 / malformed`（未知仍默认可重试，避免「一次性失败即永久 hold」）。
2. **重试耗尽的包装语义（既有框架行为）**：`_call_with_retry` 在耗尽时把最后一次异常包成
   **`MCPTransportError`**（`client.py:524`：`raise MCPTransportError(f"MCP 重试 {retry_max} 次仍失败: {last_err}") from last_err`）
   ⇒ 若只捕哨兵类型，**耗尽路径会漏接**（V2 初版即因此失败）。
   **处置**：新增 `except MCPTransportError` 分支，沿 **`__cause__` 链**回溯哨兵 → 转 `MCPToolError` 并补 `attempts`，
   使耗尽路径同样以**工具级语义 + 留痕**呈现（而非笼统传输层失败）。

## 4. 回退

- 触发：任一验收项 FAIL 或成功路径漂移 → 立即回退；
- 方式：**精确 hunk 反向补丁 / 新建反向提交**（**禁整文件 `git checkout`**，CASE-009 教训）；
- 回退点：实施前已建（见提交记录）。

## 5. 第二笔（单列，已获准）

**调用侧降级**：使该类失败**不永久 hold 水位**（对齐既有 `dead_letter_max` 死信机制，`qfq_resident_orchestrator:759`）。
启动前需**先定位 hold 的确切触发点**（方案内取证项）——即「export/消费侧捕获该异常后的流转路径」。
启动条件：本笔验收完成（已满足）；建议紧随本笔进入取证 + 六步轻量。
