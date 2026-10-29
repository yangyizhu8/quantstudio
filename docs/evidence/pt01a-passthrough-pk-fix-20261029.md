# PT-01a 覆盖链主键保护 · 实施验收证据

- **日期**：2026-10-29 21:15–21:50（Asia/Shanghai）
- **归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade · FR-021/FR-022/FR-025 · 任务 T025–T032、T034
- **判型**：**纯恢复型**（回到「覆盖链不损坏主键」的应有语义；改道前后写入内容逐位一致 ⇒ 无行为漂移）

## 一、改动清单（本轮）

| 文件 | 改动 | 行数 |
|---|---|---|
| `quantstudio/pipeline/writers.py` | `_write_passthrough` 改 **F2**（TRUNCATE+INSERT，事务包覆）；新增 F1 备选（列集不一致 ⇒ 按 df 列集重建 + 显式重建同构主键，命中指纹表即拒绝）；`write_passthrough_chunked` 同步 F2（保留 staging+ledger 记账）；两处 docstring 更正 | +167 / −12 |
| `quantstudio/pipeline/config_lint.py` | `_lint_one_task` 新增 **ERROR 级前瞻守卫**：passthrough 指向带主键表 ⇒ 报错并点名 task 与表（判据同源于 `_WRITER_PK_REFERENCE` + schema `primary_key`，**不建第二份主键字典**） | +11 / −0 |
| `tests/test_passthrough_pk_preservation.py` | **新增** 7 项验收测试（P1–P6） | 新文件 |
| `docs/evidence/version_gate_entrypoint_check.py` | 新增：门禁三入口替身触发自检（T016/T017/T019） | 新文件 |
| `docs/evidence/gate_matrix_check.py` | 新增：三判定面一致性 + 声明面核对（T020/T021） | 新文件 |
| `docs/evidence/smoke_s1_s5.py` | 新增：S1–S5 冒烟（T018） | 新文件 |
| `docs/handoff/duckdb-pt01-passthrough-runbook-20261029.md` | 新增：runbook（备而不用）（T034） | 新文件 |

**铁律 10 自检**：每次 `edit` 后即时 `git diff` —— `writers.py` 改动段逐行核对，
`git diff --numstat` = +167/−12 与 +11/−0；**writers.py 改前 `git status` 干净、mtime=2026-10-26 01:24:39**（无他线并发改动叠加）。

## 二、实测读数

### 2.1 真实 writer 路径（P1/P2/P3/P4/P5）

测试文件：`tests/test_passthrough_pk_preservation.py`（构造 `DuckDBWriter({"type":"duckdb","path":…})`，
建带主键表 `demo(code,time,v, PRIMARY KEY(code,time))`，调用真实 `_write_passthrough`）

| 用例 | 判据 | 读数 | 结论 |
|---|---|---|---|
| P1 | F2 覆盖后 `duckdb_constraints()` PK 计数 = 1；`ON CONFLICT` 重复键被拦下 | PK=**1**、行数 4、重复键插入后仍 4 行 | PASS |
| P2 | 对照：旧语义（CTAS+DROP+RENAME）PK = 0 | PK=**0** | PASS（缺陷客观存在） |
| P3 | 双写逐位一致：旧路径库 vs 新路径库，列集/行数/内容 sha256 | 列集同、行数 4=4、**sha256 相同** | PASS |
| P4 | 列集不一致 ⇒ F1 重建：列集按 df 扩展且 PK 显式恢复 | 列集 `code,time,v,extra`、PK=**1**、行数 4 | PASS |
| P5 | 命中指纹表 ⇒ 拒绝列集变化；且拒绝后原表与 PK 完好 | 抛 `RuntimeError`（含「契约变更」）、原 PK=1、列集未变 | PASS |
| P6 | lint 负向：passthrough 指向带主键表 ⇒ ERROR | 命中即报（点名 task 与表） | PASS |
| P6 | lint 现状：真实配置 passthrough 任务集零告警 | 零 passthrough 错误；现状 passthrough 任务数 > 0 | PASS |

**7 passed in 3.60s**（pytest 实测）。

### 2.2 门禁三入口替身触发（T016/T017/T019）

`python docs/evidence/version_gate_entrypoint_check.py` →
**检查组合数 = 30（3 入口 × 3 放行 + 6 拒绝 + 逃生阀）；PASS**。
断言：集合内放行 rc=0；集合外拒启 **rc=3** 且 stderr 含「拒绝启动」+ 三类出路
（「升级到目标版本」/「改用已合规的解释器」/「临时放行」）；逃生阀 `QS_DUCKDB_VERSION_GATE=0` 放行且留「显式放行」告警。

### 2.3 三判定面一致性 + 声明面（T020/T021）

`python docs/evidence/gate_matrix_check.py` → **PASS**：
9 个版本串 × 三面（判定源单文件 CLI / 门禁模块 CLI / 进程内 `is_allowed`）**逐项一致**；
`requirements.txt:1 = duckdb==1.5.6`、`pyproject.toml` pin 行号 `[20, 50]`、注释均载明放行集合；
单源同一性（`gate.ALLOWED_RANGES is policy.ALLOWED_RANGES` 等）= **True**。

### 2.4 S1–S5 冒烟（T018）

`python docs/evidence/smoke_s1_s5.py` → **PASS**（零生产接触）：

```
S1 读写：独立临时库写入 2 行，读回 2 行 → PASS
S2 时区：写入 2026-10-29T09:30:00+08:00 → 读回同值 → PASS
S3 重叠批 upsert：写入 3 行（含 1 重叠）→ 行数 3、A.v=99.0 → PASS
S4 主库只读点查：未执行（daemon 运行时库被 DuckDB 单写者独占）→ 留窗口内
S5 storage_version=64（上限 68）创建串='v1.4.5' magic=b'DUCK' → PASS
```

### 2.5 局部回归

`pytest tests/test_write_integrity.py tests/test_3a_equivalence.py tests/test_batch_checkpoint.py
tests/test_passthrough_pk_preservation.py tests/test_duckdb_version_gate.py -q` → **61 passed in 16.01s**。

### 2.6 全量回归对比（T033）

| | 失败 | 通过 | 跳过 | xfail | 耗时 | 日志 |
|---|---|---|---|---|---|---|
| **改前基线**（writers.py 改动前采集） | **68** | 3977 | 4 | 8 | 39:28 | `docs/evidence/_pytest_baseline_20261029.log` |
| **改后**（writers.py + config_lint.py 改道后） | **68** | **3984**（+7） | 4 | 8 | 39:44 | `docs/evidence/_pytest_after_20261029.log` |

**逐项 diff 结果：新增失败 = 0；消失失败 = 0；失败集合逐项相同（68 项）。**
`passed +7` 恰为本轮新增的 PT-01a 验收测试 7 项 ⇒ **PT-01a 零新增失败**。

失败归因（基线即存在，**非 PT-01a 引入**）：按文件分布 —— `test_fin_growth_dividend_staging_tool.py` 10、
`test_source_import.py` 7、`test_phase4_minute_memory_slice.py` 6、`test_phase4_minute_batch_provider.py` 6、
`test_pr6b1_orchestrator.py` 3、`test_gui_rebalance_mode.py` 3、`test_audit_qfq_staleness.py` 3、
`test_batch_apis.py` 3，其余零散 25 项。与 writer 相关的 4 项
（`test_write_integrity_r2_serial.py::test_registry_same_lock_object`、
`::test_instance_method_delegates_to_module_registry`、
`test_writer_channel_contract.py::test_b1_chunked_variant_is_same_channel_not_third`、
`test_strategy_name_chinese_contract.py::test_publish_writes_chinese_filename_and_front_blocks_collision`）
**在改前基线中同样失败** ⇒ 属他线在途 / 既有失败，非本次改道引入。

## 三、未闭环项（如实申报）

| 项 | 状态 | 原因 / 闭环时点 |
|---|---|---|
| **T033 全量回归对比** | **已完成** | 基线 68 failed/3977 passed vs 改后 68 failed/3984 passed ⇒ 新增失败 0、失败集合逐项相同、passed +7（新增 PT-01a 测试 7 项）；详见 §2.6 |
| **T035 哨兵挂载** | **未完成** | 仓库内**无统一的「每日哨兵」编排器**（已 grep `scripts/`、`docs/`、`quantstudio/`：`三判据`/`每日哨兵`/`daily_check` 零命中；`scripts/check_daemon_alive.ps1` 的「三判据」是 daemon 存活判据，非 grep 巡检链）。守卫脚本 `scripts/check_passthrough_pk.py` 已就绪（只读、零源码改动），**挂载点待总调度裁定**；接入说明落 runbook §6 |
| 真实 daemon 上的 passthrough 任务验证 | 未做 | 改道路径已在**真实 writer 实例**上验证（P1–P5），但真实采集链上的 passthrough 任务需 daemon 实跑 ⇒ 窗口内（S6） |
| `check_wal.ps1` 端到端 / 三入口真实进程触发 / F4 `--check` 取 `OK` | 维持 M-3 窗内 | 本轮命令②既定口径不变 |

## 四、设计澄清留痕（实施期）

- **F1 分支的指纹断言**：列集变化时若目标表属指纹校验表（`qfq_schema_contracts` 内 `*FINGERPRINT` 字典的键）⇒ 直接抛错拒绝，
  **不做自动 `ALTER ADD COLUMN`**（加列＝契约变更六件套，本期无六件套；铁律 24）。此为对 T029 字面「ALTER ADD COLUMN 或回退 DROP+RENAME」二选一的**收紧**：
  实测「回退 DROP+RENAME」无法解决列集变化（旧语义同样依赖列集一致），故取「按 df 列集重建 + 显式重建同构主键」，并对指纹表硬拒绝。
- **事务包覆**：F2 的 TRUNCATE 与 INSERT 必须在同一事务内（否则中途失败留下空表）。实现用 `_pt_begin()` 探测「连接是否已在事务中」，
  已在事务中则不重复 BEGIN（交由外层提交），失败路径 ROLLBACK。
- **chunked 路径**：staging + ledger 记账语义**完整保留**（续写、staging/ledger 一致性校验、`rebuilt` 标志均未动），
  仅把「全部片完成后 DROP+RENAME」换成「TRUNCATE+INSERT 拷入原表」（列集一致）或 F1 重建（列集不一致）。
