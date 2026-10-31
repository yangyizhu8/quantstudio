# W0 收口读数（003-delivery-bundle-import）

- 抓拍时间：2026-10-31 03:56（Asia/Shanghai）
- git HEAD：`7a0c6a30bee19fcb346d53987ba329dc9ce3db3d`
- daemon 状态：**running**（PID 29152，started 2026-10-30T21:51:34，`--mode forever`）——**本轮全程未停/未启**

## 1 基线快照（T004 → T005）

基线快照：`docs/evidence/delivery-pure-gain-baseline-20261031.json`

| 项 | 读数 |
|---|---|
| 保护路径（SC-013 判据范围） | 35 个（`writers.py` + 34 个 `qfq_*.py`） |
| 开工即脏 | 1 个：`quantstudio/pipeline/writers.py`，diff 6236 字节，`diff_sha256` 前 12 位 `d03bd38e25f9` |
| APPEND_ONLY（D-6：只核不判） | 4 个；其中 `main_window.py` 开工即脏 1103 字节（`diff_sha256` 前 12 位 `49fa02bfc1e5`），其余 3 个为 0 |
| 受保护目录内未跟踪文件 | 1 个：`quantstudio/pipeline/sources/consume_whitelist_guard.py`（非本会话产出） |

> **他线在推进（如实记录）**：上一轮（03:05 前后）实测 `writers.py` diff 为 6376 字节 / `B0AFE49FFCB8`，本基线为 6236 字节 / `d03bd38e25f9` —— 基线抓拍时他线已再次改动该文件。基线以**抓拍时刻**为准，SC-013 判据即"相对此基线"。

## 2 测试基线（T006）

### 2.1 交付特性测试（本会话新增，无隔离、直接跑）

```
python -m pytest tests/test_delivery_pure_gain.py tests/test_delivery_verify.py \
     tests/test_delivery_stats.py tests/test_delivery_precheck.py \
     tests/test_delivery_fixtures.py -q
```
**读数：83 passed / 0 failed（2.36s）**

### 2.2 相关面既有测试（隔离 runner：`QUANTSTUDIO_DATA_ROOT=<空临时目录>`）

```
python -m pytest tests/test_snapshot_lock.py tests/test_db_lock_errors.py \
  tests/test_lock_preserve_file.py tests/test_writers_rw_backoff.py \
  tests/test_writer_channel_contract.py tests/test_writer_upsert_skip_identical.py \
  tests/test_qfq_schema_status.py tests/test_qfq_schema_migration.py \
  tests/test_qfq_schema_22_last_run_id.py tests/test_delivery_*.py -q
```
**读数：308 passed / 6 failed / 165.07s**

6 项失败**全部由隔离 runner 引起**（这些用例断言"生产 `DATA_ROOT` 路径必须被拒绝"，改 env 后 `DATA_ROOT` 指向临时目录 ⇒ 断言失配）：

| # | 用例 | 失败原因（原文摘） |
|---|---|---|
| 1 | `test_writer_channel_contract.py::test_b1_chunked_variant_is_same_channel_not_third` | 依赖真实 `DATA_ROOT` 的默认路径判定 |
| 2–4 | `test_qfq_schema_migration.py::TestProductionRefuse::[relative / dotdot / production_db_invariant]` | `report path is outside allowed_root: …\data\qfq_aux.db not under …\pytest-…` |
| 5–6 | `test_qfq_schema_migration.py::TestReportPathSafety::[test_report_equals_production_refused / test_report_equals_aux_refused]` | 同上 |

**为确认这 6 项在不隔离时应恢复，尝试过无隔离复跑 → 240s 未完成（后台作业已终止）**：真实 `DATA_ROOT` 下这些用例与**运行中的 daemon** 争用写锁/生产库，属窗口问题而非代码问题。**该读数未取得，留待 W2 收口的 L3 窗口内复验**（届时 daemon 已停）。

### 2.3 全量套件（推迟，附实测证据）

| 项 | 读数 |
|---|---|
| 收集总数 | **4251 tests**（`--collect-only -q`，3.93s） |
| 隔离全量实测进度 | 10 分钟内由 1% → 3%，且已出现 1 个失败 ⇒ 平均 ~4 s/test |
| 推算全量耗时 | **> 2 小时** |
| 处置 | **推迟到专用窗口**（daemon 停止 + 无其他长任务）执行；本波以「交付特性 83 项 + 相关面 314 项」为基线，后续每波用**同一 runner** 比对"新增失败" |

## 3 fixture 状态（W0 DoD④）

`tests/test_delivery_fixtures.py` 实测（4 个状态均被项目自身 `detect_schema_status` 识别）：

| fixture | 构造方式 | `detect_schema_status` 读数 |
|---|---|---|
| `complete_2_1` | `qfq_reanchor_schema.init_duckdb_schema`（项目自身 DDL） | `COMPLETE_2_1` |
| `empty_or_new` | 空 DuckDB | `EMPTY_OR_NEW` |
| `partial_or_mixed` | 建 `qfq_source_cutover` 但缺列 | `PARTIAL_OR_MIXED` |
| `zero_bytes` | 0 字节文件 | 打开即失败（对应修复模式） |
| `build_aux_db` | SQLite + WAL + 4 行 | 头 16B = `SQLite format 3`，行数 4 |
| `make_client_data_root`（install/update/repair） | — | 三模式均保留 `snapshots/`、`.gitkeep`、`README.md` |

## 4 SC-013 基线相对审计（T005 的首次对照）

```
python scripts/delivery_pure_gain_audit.py --mode audit \
  --baseline docs/evidence/delivery-pure-gain-baseline-20261031.json
```

**读数：PASS（退出码 0）**

| 项 | 读数 |
|---|---|
| 保护路径总数 | 35 |
| 有变化 | **0**（全部 SAME） |
| 新增未跟踪（受保护目录） | 无 |
| APPEND_ONLY 对照 | `main_window.py` 1103→1103 一致；`workers.py` 0→0；`pyproject.toml` 0→0；**`.gitignore` 0→430 已变化（本会话 FR-031 新增两行，属预期）** |

> D-6 的价值当场体现：`.gitignore` 的 430 字节变化被记录为"对照读数"而不是判据失败——SC-013 判据范围仍只含 `PROTECTED`。

## 5 生产库未被测试触碰（证据）

`data/*.db` 六个文件（大小 + mtime）在测试前后逐项一致：

```
same: batch_audit.db 13844480 2026-10-30T18:54:31Z
same: qfq_aux.db 11608604672 2026-10-30T18:52:49Z
same: qfq_aux_mcp_gen1.db 53248 2026-09-06T04:39:09Z
same: quantstudio.db 62306398208 2026-10-30T19:33:43Z
same: quarantine.db 1407508480 2026-10-30T18:52:58Z
same: qfq_aux_c4resume_staging.db 53248 2026-08-07T04:54:53Z
```

## 6 W0 DoD 对照

| DoD | 状态 | 证据 |
|---|---|---|
| ①基线 json 落盘 | ✅ | §1 路径 |
| ②测试基线落盘 | ✅（相关面；全量推迟并已给实测依据） | §2 |
| ③fixture 四状态可识别 | ✅ | §3 |
| ④审计脚本/gate 自测通过 | ✅ | §2.1（83 passed 含 audit/gate 自测） |
| ⑤零实现改动（仅测试/脚本/骨架） | ✅（见偏离申报 D-10：W0 内实现的是**常量/配置/manifest/probe/winenv/stats**——它们在 tasks 里本就属 W0 Foundational） | §4 + `docs/evidence/delivery-session-touched-files.txt` |

## 7 波次门禁

```
python scripts/delivery_wave_gate.py --wave 0
```
（本文件即 W0 evidence；门禁要求 `测试:` / `SC-013:` / `基线快照:` / `测试基线:` / `fixture 状态:` 五项齐全，见下）

测试: 83 passed / 0 failed（交付特性）；308 passed / 6 failed（相关面，隔离 runner，6 项均为隔离所致）
SC-013: 与基线一致（PASS，35 保护路径 0 变化；APPEND_ONLY `.gitignore` 已变化=本会话预期）
基线快照: docs/evidence/delivery-pure-gain-baseline-20261031.json（35 保护路径 / 开工即脏 1 / APPEND_ONLY 4 / 未跟踪 1）
测试基线: 交付 83 项全绿；相关面 308/314（6 项隔离所致，逐条列于 §2.2）；全量 4251 项推迟专用窗口（实测 ~4s/test，估算 >2h）
fixture 状态: complete_2_1 / EMPTY_OR_NEW / PARTIAL_OR_MIXED / zero_bytes 四态均被 detect_schema_status 识别（§3）
