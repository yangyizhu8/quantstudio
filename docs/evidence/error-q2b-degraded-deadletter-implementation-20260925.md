# Q2b ①③ (+②) 实施与验收：degraded 分类留痕 + 死信通道（2026-09-25）

- 方案/取证：`docs/error-q2b-hold-trigger-forensics-and-plan.md`（`d734e37`）｜归属：客户运维会话 1
- 裁定：**①③ 本次实施**（死信默认仅告警 + N=3 可配）；**② 收窄兜底批准**（附已知异常清单论证，快审）；
  **④ 按表隔离 hold 呈用户裁定**（与 Q1 窗口化候选一并）
- 状态：**实施 + 验收完成（当轮）**

## 1. 改动清单（精确路径）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/daemon.py` | **+91/−7**：① 模块级 `_QFQ_KNOWN_ERROR_NAMES` 清单 + `_classify_qfq_factor_error()`（按**类名**匹配，避免顶层导入 MCP 包引发环）+ `_qfq_degraded_deadletter_limit()`（env `QS_QFQ_DEGRADED_DEADLETTER_N`，默认 3）；② 实例方法 `_qfq_degraded_reset()` / `_note_qfq_degraded(kind, detail)`（连续**同因**计数；达阈值 ERROR 死信锚点，未达不告警；异因分开计数）；③ `_qfq_refresh_factors` 三处接入：MCP 短路→`reset`；`res.degraded`→分类 + 计数；`except Exception`→分类 + 计数 + **保留 degraded 保守语义**；成功→`reset`；④ `run_post_ingest` 调用点透传 `detector_degraded_kind` |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | **+9/−7**：`run_post_ingest` 新增**可选** kwarg `detector_degraded_kind: str = ""`；`detector_degraded` 分支的 `reasons` 文案在前缀后追加 `(kind=…)`（**未传时与旧版逐字一致**）；WARNING 同步带 kind |
| `tests/test_qfq_factor_degraded_deadletter.py` | 新增 7 用例（C1–C6 + 异因分开计数） |

## 2. 验收（C1–C6）

| # | 判据 | 结果 |
|---|---|---|
| C1 | 分类：已知因子链异常 → `known:<类型>`；未知 → `unknown:<类型>` | **PASS**（`MCPToolError`/`MCPProtocolError`/`ValueError`/`OSError` → known；自定义 `WeirdError` → unknown） |
| C2 | 死信阈值 env 覆盖生效、默认 3、非法值回落 | **PASS** |
| C3 | 连续**同因**达阈值 → 死信锚点 ERROR（含 kind 与 detail）；**未达阈值不告警** | **PASS**（第 3 次触发，恰 1 条 ERROR，含 `known:MCPToolError` 与 `N=3`） |
| C3b | **异因分开计数**（不误合并） | **PASS** |
| C4 | 成功 → 计数清零（再次失败从 1 起算） | **PASS** |
| C5 | **不改变 hold 决策**：失败/异常路径仍返回 `True`(degraded) | **PASS** |
| C6 | 编排器：传 kind → `hold_reason` 含 `(kind=…)`；**未传 → 旧文案逐字一致** | **PASS**（签名默认 `""` + 文案分支静态断言） |

**回归**：`qfq_factor_refresh` / `factor_split_storage` / `factor_new_date_trigger` / `event_discovery` /
`resident_orchestrator` / `daemon_lifecycle` / `daemon_once_exit_contract` / `reanchor_batch1` / 本件 = **182 passed / 1 failed**；
唯一失败 `test_qfq_resident_orchestrator::test_require_bootstrap_fail_closed` 经 **A/B 归因证实为基线即红**
（回退本批两文件改动后同用例**同样失败且信息一致**）——与本批无关。

## 3. ② 收窄兜底：已知因子链异常清单论证（快审要求）

`_QFQ_KNOWN_ERROR_NAMES` 四组，逐组给**链上来源**（按类名匹配，不建依赖）：

| 组 | 类名 | 链上来源（取证） |
|---|---|---|
| **MCP 客户端家族** | `MCPClientError` 及子类（`MCPAuthError` / `MCPTransportError` / `MCPRetryBudgetExhausted` / `MCPProtocolError` / `MCPToolError` / `MCPChecksumError` / `MCPExportBudgetError`） | 因子/行情取数经 `mcp_adapter.fetch_table` → `client`（`export_dataset`/`create_export_job`/逐 shard `get_artifact`）；本线 Q2 第一笔修复的载荷级 error 亦落此家族 |
| **数据访问层** | `IOException`（duckdb）/ `OperationalError` / `DatabaseError` / `IntegrityError` / `ProgrammingError`（sqlite3） | 主库 duckdb（`writer.shared_conn`）与 `qfq_aux.db`（`adj_factor`/`fund_adj` 读写、`ObservationStore` 事务） |
| **数据形态** | `ValueError` / `KeyError` / `TypeError` / `IndexError` | `normalize_mcp_adj_factor_df` 归一化、`ObservationStore._preprocess` 校验（同批冲突/非法输入）、列/键缺失 |
| **文件系统** | `OSError` 及子类（`FileNotFoundError` / `PermissionError` / `IOError`） | Parquet 分片落盘/读取（Landing）、aux 库文件访问 |

**语义边界（裁定要点）**：
- 上述清单**只用于分类与死信计数**——命中与否**都不改变** `degraded` 决策；
- **未命中（unknown）一律保留 `degraded=True`**（fail-closed 以防漏，避免"未知异常被放过"）；
- 故本批对**水位/门禁行为零变更**（C5 + C6「未传 kind 逐字一致」双证）。

## 4. 类型判定（铁律「修复前置纯增益审计」）

| 案 | 类型 |
|---|---|
| ① 分类留痕 | **纯恢复型**（决策不变）＋ **新增检测型**（新增分类与留痕能力） |
| ③ 死信通道 | **新增检测型**（默认仅告警，不改行为）＋ 配置化（N 可配） |
| ② 收窄兜底 | **纯恢复型 + 新增检测型**（决策不变；仅告警更细、不再静默） |

**合并判定：本批对可观察行为零变更**（水位推进/持有决策逐项一致），属**新增检测/留痕**能力。

## 5. 回退

- 触发：任一判据 FAIL 或出现行为漂移 → 立即回退；
- 方式：**精确 hunk 反向补丁 / 新建反向提交**（**禁整文件 `git checkout`**，CASE-009 教训）；
- 回退点：实施前已建（见提交记录）。

## 6. 待办

- **④ 按表隔离 hold**：呈**用户裁定**（与 Q1「窗口化」候选一并呈批）——牵动一致性门禁语义，须附论证；
- Q3 等客户三件取证；Q1 三项先取证待窗。
