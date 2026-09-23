# 错误一 T3 实施与验收（注入点修订检测 → revision_alert outbox）

- 方案：`docs/error1-t3-revision-alert-outbox-design.md` **v1.3**（已过审 = 六步②）
- 实施日：D+1（2026-09-24）｜归属：客户运维会话（错误一案案主）
- 形态：**收敛为「情形 B（同 `(code,time)` 值变化）」**——情形 A（锚前移）已由既有 `factor_new` 通道覆盖（实证 2,320 条），不重复造

## 1. 改动清单（精确路径）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/sources/mcp_adapter.py` | **+65 行**：新增 `_detect_and_record_revisions()`（覆盖前读同 time 旧值 → 容差外变化 → `record_observations(conn=<同一连接>)` 同事务写 revision+alert）；在 `_inject_adjfactor` 的 `INSERT OR REPLACE` **之前**调用，**fail-soft** 包裹（异常只告警，不影响快照写入） |
| `tests/test_t3_revision_detect.py` | 新增 6 用例（C1–C6） |
| `agent_workspace/t3_obs_probe.py`、`t3_factor_new_coverage_probe.py` | 实施前只读取证脚本（未决项 1 / 5） |

**实现要点**
- **只查本批键**：临时表 `_t3_new`（主键 `(code,time)`，`INSERT OR REPLACE` 与快照同语义）→ `JOIN {target}` 走 PK 索引取旧值；不扫全表。
- **容差同口径**：复用 `qfq_observation._tol_eq` + `DEFAULT_EPSILON_ABS/REL`，防「检测说变、写入器说没变」分叉。
- **同事务原子**：`record_observations(conn=conn)` 不自行 BEGIN/commit → 与快照 `INSERT OR REPLACE` 一并提交（复用连接路径并入调用方事务）。
- **run_id**：未决项 2 核对结果——4 个调用点所属方法（`_streaming_export` / `_sync_factor_snapshot` / `_coldstart_adj_factors` / `_inject_qfq_inputs`）内**均无 run/batch 标识**（全文件 0 命中）⇒ 采用**确定性缺省 id** `adjfactor-inject:{target}`（确定性 → 幂等可重放；不参与 `alert_id` 生成）。
- **冷启动开关**：`QS_T3_REVISION_DETECT`，**默认开**（审核裁定）。

## 2. 验收（V1–V9）

| # | 判据 | 结果 |
|---|---|---|
| **V1** | **outbox 0 → 非 0**（触发场景 = 情形 B） | **PASS**：`test_c1_same_time_change_produces_revision_and_alert` —— 同 time 值 1.0→2.0 → 检出 1 条修订、observation 最新 `revision_no=2`、**outbox 新增 1 条 `pending`** |
| V2 | 幂等（同值重注无新增 / revision 不跳号） | **PASS**：`test_c6_idempotent_repeat`（重复注入同值 → `rev=2`、`alerts=1` 不重复）；`test_c2_unchanged_value_no_revision`（值未变 → 无告警） |
| V3 | 容差内不记修订 | **PASS**：容差与写入器同口径（`_tol_eq`）；C2 覆盖「同值」分支 |
| V4 | **fail-soft**（留痕失败 → 快照仍写入、批次不失败） | **PASS**：`test_c4_fail_soft_snapshot_still_written`（模拟留痕抛错 → `_inject_adjfactor` 返回 1、快照已写新值 9.9） |
| V5 | 消费闭环（`consume_revision_alerts` → trigger + acknowledged） | **PASS（既有链路回归）**：`test_qfq_event_discovery` 等消费侧套件全绿（见 V6） |
| **V6** | 回归全绿 | **984 passed / 3 skipped**（QFQ+MCP+锁 全套 52 文件）；**8 failed 经 A/B 归因证实为基线即红、与本改动无关**（见 §3） |
| V7 | 性能量化 | **待补**：本批键 JOIN 走 PK 索引（临时表主键去重）；真实 minutes 批（5220 码/日）增量耗时待生产窗实测后钉 |
| V8 | 生产规模评估 | **待补**：真实采集一轮后 outbox 条数与 revision 分布（防激增护栏） |
| **V9** | 9-06 窗口重放对照表不回归 | **待执行**：该对照表定义在 `docs/qfq-invariant-anchor-window-fix-design.md`（非本项可自跑的一次性重放）；本改动**未触** invariant/anchor-window/价格链逻辑（仅在快照写入前加修订检测）→ 需按该文档口径由指定执行方在同一窗口重放（见 §4 请示） |

## 3. A/B 归因（8 例失败 = 基线即红）

| 侧 | 内容 | 结果 |
|---|---|---|
| **A（对照）** | 临时回退 `mcp_adapter.py` 至 HEAD（备份后 checkout，跑完即恢复） | **8 failed / 161 passed**（与 B 侧**完全相同的 8 例**） |
| **B（T3 版）** | 含本改动 | 8 failed（同 8 例）/ 984 passed |

失败清单（两侧一致）：`test_audit_qfq_staleness`（3，TestEtfUniverse：真实 dict 配置/provider）、
`test_qfq_range_r1a`（2，golden 300750：`AssertionError`/`KeyError`）、
`test_qfq_resident_orchestrator::test_require_bootstrap_fail_closed`（1）、
`test_qfq_schema_migration`（2：`test_production_db_invariant`、`test_report_equals_production_refused`；现场见 `PermissionError` → 环境/生产库依赖）。

⇒ **定谳：与本改动无关（基线即红）**；恢复核对：T3 版 diff = **+65 行**（与回退前一致）✓

## 4. 待办与请示

1. **V9**：请指定 9-06 重放对照表的执行方与口径（我可按 `docs/qfq-invariant-anchor-window-fix-design.md` 执行，需窗口数据 + daemon 停止窗）。
2. **V7/V8**：生产窗实测（增量耗时 + outbox 规模分布）——建议在下一轮真实采集后取样（只读）。
3. 推送：本实施笔 + 前序 11 笔随下批协调；触及 `quantstudio/` → trading 同步门**不豁免**。
