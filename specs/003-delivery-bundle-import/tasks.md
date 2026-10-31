# Tasks: 数据交付包导出与导入校验（CLI + GUI 双端）

**Input**: 设计制品来自 `specs/003-delivery-bundle-import/`（spec.md v4+ 二次微调 / plan.md ②审通过版 / research.md / data-model.md / contracts/ / quickstart.md）
**Constitution**: `.specify/memory/constitution.md` v1.0（唯一内核 + 双薄壳；纯增益；可机检验收）
**阶段划分**：W0 Setup & Foundational → W1 内核导出 → W2 内核只读校验 → W3 导入变更区 → W4 壳与双端对等 → Polish（共 6 阶段，W0 内含阻塞性前置）

## Format: `[ID] [P?] [Story?] Description`
- **[P]**：可并行（不同文件、无未完成依赖）
- **[Story]**：归属用户故事（US1 导出 / US2 存量导入 / US3 全新安装 / US4 坏库修复 / US5 回滚与崩溃恢复 / US6 双端对等）
- 每行带确切文件路径；验收口径指向 SC 或 FR

---

## Phase 1: W0 Setup & Foundational（W0 基线 + 阻塞性前置）

**Goal**: 建立可量测的开工基线（SC-013 diff 基线 + 测试基线）、测试 fixture 工厂与内核骨架，使后续每一波都有"参照物"可比。
**独立验证**: `python scripts/delivery_pure_gain_audit.py --mode audit --baseline <刚生成的基线>` 返回一致；`pytest tests/test_delivery_pure_gain.py -q` 全绿；fixture 能造出 `detect_schema_status` 可识别的四种状态。

- [ ] T001 [P] 建内核包骨架：`quantstudio/delivery/__init__.py`（公开 API 占位 + `__all__`）、`constants.py`/`manifest.py`/`probe.py`/`stats.py`/`export_kernel.py`/`verify.py`/`import_kernel.py`/`journal.py`/`config.py`/`cli.py`/`winenv.py` 空模块（仅 docstring）
- [ ] T002 [P] 建测试骨架：`tests/fixtures/delivery/__init__.py` + `tests/test_delivery_export.py`/`test_delivery_verify.py`/`test_delivery_import.py`/`test_delivery_journal.py`/`test_delivery_rollback.py`/`test_delivery_precheck.py`/`test_delivery_parity.py`/`test_delivery_pure_gain.py`
- [ ] T003 [P] `quantstudio/delivery/constants.py`：`WHITELIST`（恰 3 项）、`EXCLUDE_PATTERNS`（FR-002 全项）、`MANIFEST_SCHEMA_VERSION=1` 与 `MANIFEST_SCHEMA_VERSION_SUPPORTED`、`TableState`（四态 + `UNKNOWN-SOURCE` + 排除性标签 `EMPTY`/`UNKNOWN-BASIS`）、`FailureKind`、退出码常量；单测 `tests/test_delivery_pure_gain.py::test_constants_*`
- [ ] T004 [P] `scripts/delivery_pure_gain_audit.py`：`--mode baseline|audit`、保护路径清单（`writers.py` + `git ls-files 'quantstudio/pipeline/qfq_*.py'` + `*upsert*.py`）、逐文件 `worktree_sha256`/`diff_sha256`/`diff_bytes`、未跟踪文件扫描、`SELF_CHANGED`/`OTHER_LINE` 分类；自测 `tests/test_delivery_pure_gain.py::test_audit_*`
- [ ] T005 落 **SC-013 基线快照** → `docs/evidence/delivery-pure-gain-baseline-20261031.json`（依赖 T004；**必须在动第一行实现代码之前**；含开工时既存的 `writers.py` 他线 diff 读数）
- [ ] T006 落 **测试基线** → `docs/evidence/delivery-test-baseline-20261031.md`：`python -m pytest tests -q` 的 `passed/failed` 读数 + `collector_tasks.json` 实测任务数（88）与 `_note` 声明（86）的差异核对（副产物回填 plan §5 差异名单）
- [ ] T007 [P] `scripts/delivery_wave_gate.py`：解析 `docs/evidence/delivery-wave<N>-*.md` 的固定字段（`测试:` / `SC-013:` / `SC-xxx:` / `停止申报:`），缺项即非 0 退出；自测 `tests/test_delivery_pure_gain.py::test_wave_gate_*`
- [ ] T008 [P] `quantstudio/delivery/config.py` + `config/delivery_config.json`（`config_version/export_dir/import_dir`，空串=未选择）：原子写（tmp+`os.replace`）、缺失/损坏容忍、字段集恰两项；单测 `tests/test_delivery_precheck.py::test_config_*`
- [ ] T009 [P] `.gitignore` 增 `data/backup_import_*/` 与 `data/import_journal.json`（FR-031）；用例 `tests/test_delivery_pure_gain.py::test_gitignore_ignores_backup_and_journal`（`git check-ignore`）
- [ ] T010 [P] `tests/fixtures/delivery/main_db.py::build_main_db(path, schema=...)`：造 `complete_2_1` / `partial_or_mixed`（缺一列）/ `empty_or_new` / `zero_bytes` 四种状态，用项目自身 DDL 使 `detect_schema_status` 可识别
- [ ] T011 [P] `tests/fixtures/delivery/bundle.py::build_aux_db` / `make_package`（走真实导出内核造包）/ `corrupt_package`（改 SHA/删文件/改版本/绝对路径）/ `make_client_data_root(mode=update|repair|install)`
- [ ] T012 [P] `quantstudio/delivery/manifest.py`：`build_manifest`/`parse_manifest`（版本裁决顺序）/`validate_v1`（必填+类型+路径守卫）/`canonical_dumps`（`sort_keys`+`ensure_ascii=False`+`\n`）；单测 `tests/test_delivery_verify.py::test_manifest_*`
- [ ] T013 [P] `quantstudio/delivery/probe.py`：`schema_status`/`assert_no_write_lock`/`assert_no_daemon`（status+PID/create_time 双验）/`assert_disk_space`/`assert_paths_disjoint`（6 种嵌套）/`assert_single_bundle` 统一前置
- [ ] T014 [P] `quantstudio/delivery/winenv.py`：`probe_dir_writable`（create+delete）、`probe_file_handle`（`msvcrt.locking(LK_NBLCK)`，非 Windows 降级为只读尝试）、`detect_sync_dir`（路径特征+env 前缀+reparse）、`check_path_length`（>255 警示 + `\\?\` 前缀封装）
- [ ] T015 [P] `quantstudio/delivery/stats.py`：`resolve_basis(table_map)`、`select_table_set(main_db, table_map)`（map ∩ 库内实际表）、`collect_table_stats`（六标签判定顺序 + `row_count_method` + `row_count_degraded*` 汇总）、`format_states`；单测覆盖 `trade_date` 陷阱表（分钟表取 `ingest_time`）
- [ ] T016 依赖守卫与文档一致性测试（`tests/test_delivery_pure_gain.py`）：AST 扫描 `quantstudio/delivery/**` 的 import 集（禁 `writers`/`daemon`/`backtest.*`）、禁 `shutil.copytree`/`COPY…TO`/`EXPORT DATABASE`/CTAS 调用；`contracts/manifest-v1.schema.json` 的 required 字段集 == `data-model.md` §2 标注 ✅ 的字段集

**W0 DoD**：①基线 json 落盘；②测试基线落盘；③fixture 四状态可识别；④wave_gate 自测通过；⑤gate 证据：`docs/evidence/delivery-wave0-20261031.md`；⑥**零实现改动**（只有测试/脚本/常量/骨架）

---

## Phase 2: W1 内核导出 (US1, P1)

**Goal**: 一句话导出三件套 + 可自证 manifest，前置校验全 fail-closed。
**Independent Test**: 在 fixture 与生产窗口各跑一次导出，检查产出清单、manifest 字段、白名单外文件数=0、非零 WAL 被拒。

- [ ] T017 [US1] `quantstudio/delivery/export_kernel.py::export_bundle`：六相位（`PRECHECK/ACQUIRE/CHECKPOINT/WAL_ZERO/COPY/ATTEST`）、`finally` 释放锁、取消与失败清理时间戳子目录（FR-003/004/005/008/034）
- [ ] T018 [P] [US1] `quantstudio/delivery/verify.py::self_check_bundle`：白名单外计数、`*.env`/`-shm`/日志命中、`db_open`（`SELECT 1`）、`zero_leak` 合取（FR-007/A5）
- [ ] T019 [P] [US1] `export_kernel.py::_attest`：对**产出副本**跑 `collect_table_stats` 组装 `tables[]`/`table_set`/`row_count_degraded*`，最后写 `manifest.json`（tmp+replace）
- [ ] T020 [US1] 测试 `tests/test_delivery_precheck.py::test_sc001_*`：五场景（schema 不合格 / 有锁 / daemon 在跑 / 空间不足 / 非零 WAL）各一次 ⇒ 导出目录无新增子目录 + 拒绝码正确
- [ ] T021 [P] [US1] 测试 `tests/test_delivery_export.py::test_sc002_zero_leak`：产出目录枚举，`*.env`/`-shm`/日志类命中=0、白名单外=0
- [ ] T022 [P] [US1] 测试 `tests/test_delivery_export.py::test_sc003_same_instant`：一次只读会话内连续打开三件 + 关键查询（禁 mtime 作证）
- [ ] T023 [P] [US1] 测试 `tests/test_delivery_export.py::test_sc012_*`：界面体积格式化来源 == `os.stat().st_size`；回扫交付模块与文档中 `\d+(\.\d+)?\s*GB` 字面量（命中即 FAIL）
- [ ] T024 [US1] 测试 `tests/test_delivery_export.py::test_fr034_old_bundle_untouched` + `test_fr035_nested_rejected`（6 种嵌套关系全拒）
- [ ] T025 [US1] 测试 `tests/test_delivery_export.py::test_progress_monotonic` / `test_cancel_*` / `test_interrupt_voids_bundle`（中断后子目录不存在）
- [ ] T026 W1 收口：`docs/evidence/delivery-wave1-20261031.md`（测试读数 + SC-001/002/003/012 读数 + SC-013 审计）+ `python -m pytest tests -q -k "not delivery"` 回归 + `delivery_wave_gate.py --wave 1`

**W1 DoD**：①导出测试全绿；②SC-001/002/003/012 读数入 evidence；③SC-013 基线审计一致；④红线自检红/绿两向用例都在

---

## Phase 3: W2 内核只读校验 (US2 前置半程, P1)

**Goal**: 客户可"只验不导"——四要素校验 + 四类失败分派 + 世代路由断言，全程零改动。
**Independent Test**: 三种坏包各跑一次，`DATA_ROOT` 六元组指纹逐位不变；版本代差不崩不猜。

- [ ] T027 [US2] `quantstudio/delivery/verify.py::verify_bundle` + `locate_package`：版本裁决顺序（①/②/④）、v1 字段级校验、逐文件存在性（FR-010/011/038）
- [ ] T028 [P] [US2] `verify.py::verify_files`：**体积升序**逐文件流式 SHA256、首错即返（FR-019 主序）、字节级进度
- [ ] T029 [P] [US2] `verify.py::classify_failure`：四类 `FailureKind` + `reasons` 按类聚合 + 退出码映射表（`contracts/cli.md` §2）
- [ ] T030 [US2] `verify.py::assert_generation_consistency`：以**包内主库**做 `duckdb_read` 回调调 `resolve_runtime_aux_path`，断言 `P.parent==DATA_ROOT` 且 basename ∈ 包内因子库；legacy fail-secure 三文案（FR-010③/plan §6）
- [ ] T031 [US2] 测试 `tests/test_delivery_verify.py::test_sc005_zero_mutation`：三种坏包 × `DATA_ROOT` 六元组指纹（路径/size/mtime_ns/sha256/目录清单哈希）
- [ ] T032 [P] [US2] 测试版本表 6 用例（too_new/missing/type/old/unknown_field/canonical_roundtrip）`test_manifest_version_*`
- [ ] T033 [P] [US2] 测试路由 3 用例（`released=false` / gen1 cutover / 配置缺失 fail-secure）
- [ ] T034 [P] [US2] 测试 `test_fr038_*`：`import_dir` 下 0 / 1 / 2 个 manifest
- [ ] T035 [P] [US2] 测试 `test_estimate_labeled`（A3 强化②）：`row_count_method=="estimate"` 的表在报告中带「该表行数为估算值」且不与 exact 混排
- [ ] T036 W2 收口：`docs/evidence/delivery-wave2-20261031.md`（SC-005 指纹读数 + 版本表 + 路由用例 + SC-013）

**W2 DoD**：①校验测试全绿；②SC-005 三种坏包前后指纹逐位一致；③SC-013 一致

---

## Phase 4: W3 导入变更区 (US2/US3/US4/US5, P1)

**Goal**: 持锁备份→可分步恢复覆盖→复验，三模式 + journal 崩溃恢复，失败可回滚。
**Independent Test**: 小库 fixture 上跑成功路径 / 三模式 / 强杀恢复 / ENOSPC / 幂等，全部只碰 `tmp_path`。

- [ ] T037 [US5] `quantstudio/delivery/journal.py`：相位机（`lock_acquired…done` + `rolling_back/rolled_back`）、原子写、`load/save/delete`、恢复决策表（`journal.md` §3.3 逐行）、`journal_version`/`data_root` 校验（FR-036/FR-014）
- [ ] T038 [US2] `quantstudio/delivery/import_kernel.py::import_bundle` 九步骨架 + `detect_mode()`（`RecoveryRequiredError` / INSTALL / UPDATE / REPAIR）+ `dry_run`（FR-013/014）
- [ ] T039 [P] [US5] `import_kernel.py::_backup`：同卷 `os.replace` / 跨卷 copy（`st_dev` 判定）/ 修复模式取证降级 + `RESTORE_README.txt`（FR-015/039）
- [ ] T040 [P] [US5] `import_kernel.py::_overwrite`：全部 `*.incoming` 就绪 → 逐项 `os.replace` → 每项更新 journal → 失败反向回滚；删除动作仅限 `created_files`（FR-016/017）
- [ ] T041 [P] [US2] `import_kernel.py::_reverify`：复用 `verify_bundle` 四要素 + 四态 + 排除性标签 + `FR-033` 基线过时条件提示（只读）
- [ ] T042 [US2] 测试 `tests/test_delivery_import.py::test_sc004_happy_path`
- [ ] T043 [P] [US5] 测试 `tests/test_delivery_rollback.py::test_sc006_rollback_on_replace_failure`
- [ ] T044 [P] [US5] 测试 `tests/test_delivery_journal.py::test_sc014_*`：子进程强杀（`QS_DELIVERY_TEST_HOOKS` + `KILL_AFTER_REPLACES`）→ 二选一 → 回滚逐位还原 / 继续完成；**断言备份目录数仍为 1（无二次备份）**；另断言钩子未设 env 时完全惰性
- [ ] T045 [P] [US5] 测试 `tests/test_delivery_rollback.py::test_sc017_enospc`：写盘注入 `OSError(ENOSPC)` → 回滚 + journal 终态正确
- [ ] T046 [P] [US3] 测试 `tests/test_delivery_import.py::test_sc007_install_*`：安装模式保留 `snapshots/`/`.gitkeep`/`README.md`；`created_files` 回滚；首启自建 `quarantine.db`/`batch_audit.db`
- [ ] T047 [P] [US4] 测试 `tests/test_delivery_import.py::test_sc015_repair_*`：0 字节主库不因回验失败终止 + 取证摘要四字段 + 「备份自身失败=真失败」用例
- [ ] T048 [P] [US2] 测试 `tests/test_delivery_import.py::test_sc016_idempotent`
- [ ] T049 [P] [US2] 测试 `tests/test_delivery_import.py::test_sc011_backup_evidence_chain`（`st_ino` → size+mtime_ns+源消失 → 耗时<5s + 空间无显著下降）
- [ ] T050 W3 收口：`docs/evidence/delivery-wave3-20261031.md`（SC-004/006/007/011/014/015/016/017 + SC-013）

**W3 DoD**：①上列 SC 读数入 evidence；②journal 相位机测试全绿（含"禁重推模式"）；③SC-013 一致

---

## Phase 5: W4 壳与双端对等 (US6, P2；同时补齐 US1/US2 的 GUI 面)

**Goal**: CLI 与 GUI 两个薄壳调用同一内核，产物逐位一致；配置健壮、文案合规。
**Independent Test**: offscreen 下双壳各跑一次比对 manifest（剔时间字段）；删配置/空字段/错路径三情形按钮禁用。

- [ ] T051 [US6] `quantstudio/delivery/cli.py`：`export/verify/import/recover/status/set` 六子命令、`--json`、进度走 stderr、`KeyboardInterrupt`→回滚（`contracts/cli.md`）
- [ ] T052 [P] [US6] 测试 `tests/test_delivery_parity.py::test_cli_exit_codes`（11 个码逐项）+ `test_cli_help_smoke`（`qs-deliver --help` 退出码 0，D-3）
- [ ] T053 [P] [US1] `quantstudio/gui/tabs/delivery_tab.py`：「数据交付」页签（目录选择+回显、按钮禁用与提示、影响范围标注、字节级进度、结果页、journal 二选一弹窗）（FR-022~025/040）
- [ ] T054 [P] [US6] `quantstudio/gui/workers.py` 追加 `ExportBundleWorker`/`VerifyBundleWorker`/`ImportBundleWorker`（`BaseWorker` 子类，只调内核）
- [ ] T055 [US6] `quantstudio/gui/main_window.py` 注册页签（紧随 `qmt_export_tab`，FR-023）
- [ ] T056 [P] `pyproject.toml` `[project.scripts]` 增 `qs-deliver = "quantstudio.delivery.cli:main"`
- [ ] T057 [US6] 测试 `tests/test_delivery_parity.py::test_sc009_dual_shell_identical`（`QT_QPA_PLATFORM=offscreen`，CLI vs GUI worker，比对清单+SHA256+校验结论）
- [ ] T058 [P] [US6] 测试 `tests/test_delivery_precheck.py::test_sc010_config_robust`（删配置/空字段/不存在目录 ⇒ 按钮禁用+提示）
- [ ] T059 [P] [US1] 改 `data/README.md` 分发说明指向 GUI「数据交付」页签 + 删体积数字（FR-032）；用例断言文案含页签名、无 `\d+\s*GB`；同时断言 `DATA_ROOT` 在 GUI 只读显示、无"选择数据目录"入口（FR-012）
- [ ] T060 W4 DoD 收口：**窗口内 L3 只读导出复测**（只允许导出路径，导入类一律 fixture）+ `python -m pytest tests -q` 全量 + `qs-deliver --help` smoke + `delivery_pure_gain_audit.py --mode audit` + `docs/evidence/delivery-wave4-20261031.md`

**W4 DoD**：①SC-009/010 读数（L2）+ 窗口 L3 复测；②CLI 退出码逐项 + help smoke；③`data/README.md` 已改；④全量 pytest 零新增失败；⑤SC-013 一致 + 最终收口审计

---

## Phase 6: Polish

- [ ] T061 [P] [US2] 测试 `tests/test_delivery_import.py::test_sc008_backtest_ab`：`tmp_path` 内用 `ATTACH+INSERT SELECT` 造有界子集主库（**测试专用，交付包仍禁 CTAS/EXPORT**），导入前后各跑一次短回测，逐位比对（不跨复权世代）
- [ ] T062 [P] 写 `docs/evidence/delivery-sc008-conduction-20261031.md`：SC-008 豁免条款下的传导论证（字节一致 ⇒ 结构/约束一致 ⇒ 取数契约不变 ⇒ 行为等价）+ 指向 SC-013 读数
- [ ] T063 [P] 修复模式结果页文案与释放提示（`delivery_tab.py` + `ImportResult.forensic` 展示，「释放权在客户」）（FR-040）
- [ ] T064 [P] R4 知识沉淀：`python scripts/kb_draft.py --kb knowledge/db --symptom … --domain D5`（交付包导入的「半包/世代脱节」教训），过 `kb_hook.validate_record` 后落盘
- [ ] T065 文档同步核查：`README.md` / `docs/` 中受影响的引用、`quickstart.md` 按实测读数回填（无写死体积）、`plan.md` §5 的 88 vs 86 差异名单回填
- [ ] T066 `speckit-analyze`：spec ↔ plan ↔ tasks 只读交叉分析（覆盖缺口/矛盾/歧义），产出报告
- [ ] T067 **检查点呈报**：三件套摘要（spec/plan/tasks）+ analyze 结论 + 全部 SC 读数索引，停等用户批准（未批准不得提交/推送，铁律 01 第⑤步）

---

## Dependencies

```
W0(T001–T016) ──→ W1(T017–T026) ──→ W2(T027–T036) ──→ W3(T037–T050) ──→ W4(T051–T060) ──→ Polish(T061–T067)
```
- **阶段内硬依赖**：T005 依赖 T004；T006 依赖 T001（可先跑）；T017 依赖 T010/T011（fixture）+ T012/T013/T015；T019 依赖 T015；T027 依赖 T012/T013；T030 依赖 T012；T037 依赖 T008；T038 依赖 T027/T037；T039/T040/T041 依赖 T038；T051 依赖 T017/T027/T038；T053 依赖 T051；T057 依赖 T051/T053/T054；T060 依赖 T051–T059。
- **跨阶段门控**：每阶段第一个动作 = 跑 `scripts/delivery_wave_gate.py --wave <上一波>` 并贴输出；不通过则本阶段不得开工（W1 起适用）。
- **⛔ W2 收口即硬停点（②审裁定，流程硬门）**：W2 完成后**必须停**，呈三段读数供审核方放行——①W0–W2 全部 SC 的 fixture 读数；②**一次生产库 L3 只读窗口实测**（只许导出路径：SC-001 复测 + SC-002/003/011/012 真实读数 + SC-009 生产侧），窗口按 plan「测试环境与窗口」八步开闭；③获审核方**明确放行**后才可开 W3。理由：W3 是唯一会写客户 `DATA_ROOT` 的阶段，放行前必须有真实库证据；未放行即开 W3 = 违返本门。
- **W2 之前不得触碰任何 `DATA_ROOT` 写入**（W1/W2 只读或只写导出目录）。

## Parallel Opportunities

- W0：T001/T002/T003/T004/T007/T008/T009/T010/T011/T012/T013/T014/T015 全可并行（文件互不重叠）；T005/T006 串行收口。
- W1：T018/T019 可并行；T020–T025 各测试文件独立可并行。
- W2：T028/T029 并行；T032–T035 测试并行。
- W3：T039/T040/T041 并行（三块逻辑不同文件段，但同在 `import_kernel.py` ⇒ 建议按函数分段提交，避免同文件并发写冲突）；T043–T049 测试并行。
- W4：T052/T053/T054/T056/T058/T059 并行；T055 需 T053 存在后一行注册。
- Polish：T061–T064 并行。

## Implementation Strategy

- **MVP 优先级**：**W0+W1+W2** 即构成"本机能出包 + 客户能验包"的可用闭环（客户可先"只验不导"，风险为零）。建议先交付这三阶段并实测一轮，再进 W3 动写入面。
- **增量交付**：每阶段收口即产出 evidence 并停等审核；**红线即停**（测试红 / SC-013 不一致未申报 / 需改受保护路径）。
- **写入面后置**：W3 是唯一会改客户 `DATA_ROOT` 的阶段，必须在其开始前确认 W2 的 SC-005（零改动）读数已落盘。
- **编码委派（铁律）**：T001/T003/T004/T007/T008/T010/T011/T012/T013/T014/T015（骨架与纯函数）为机械实现，适合委派 `zcode_code`；T017/T027/T038/T039/T040/T041（状态机与事务语义）与本会话上下文强耦合，建议**本会话内实现**并在汇报中显式声明「未委派 + 原因」；T020–T025/T031–T035/T042–T049/T057–T059（测试）若委派须先给定 SC 判据原文与 fixture 契约。

## 需求覆盖索引（机检用）

| FR 组 | 任务 |
|---|---|
| FR-001/002（白名单/排除） | T003, T018, T021 |
| FR-003（前置+Windows 探针） | T013, T014, T020 |
| FR-004/005/008（锁窗口/文件级复制/零写） | T017, T020 |
| FR-006/007（manifest/红线） | T012, T019, T018 |
| FR-009~011（校验/四类失败） | T027, T028, T029, T032 |
| FR-012（DATA_ROOT 只读） | T059 |
| FR-013~017（九步/三模式/备份/覆盖） | T037, T038, T039, T040, T042, T046, T047 |
| FR-018/019（复验口径/进度与 fail-fast） | T028, T041, T035 |
| FR-020~025（配置与界面） | T008, T053, T058 |
| FR-026~028（内核/CLI/对等） | T001, T051, T054, T057 |
| FR-029/030/041（纯增益面） | T004, T005, T016, T026/T036/T050/T060 收口 |
| FR-031/032（gitignore/README） | T009, T059 |
| FR-033（基线过时提示） | T041 |
| FR-034/035（时间戳子目录/路径互斥） | T024 |
| FR-036~040（journal/持锁/单包/README/结果页） | T037, T038, T034, T039, T049, T063 |

| SC | 任务 |
|---|---|
| SC-001 / SC-002 / SC-003 / SC-012 | T020 / T021 / T022 / T023 |
| SC-004 / SC-005 / SC-006 / SC-007 | T042 / T031 / T043 / T046 |
| SC-008 | T061, T062 |
| SC-009 / SC-010 / SC-011 | T057 / T058 / T049 |
| SC-013 | T004, T005, 各波 T026/T036/T050/T060 |
| SC-014 / SC-015 / SC-016 / SC-017 | T044 / T047 / T048 / T045 |

**故事任务数（机检读数）**：US1 = 11 / US2 = 15 / US3 = 1 / US4 = 1 / US5 = 6 / US6 = 6（带标签共 40）；无故事标签 = 27（W0 16 + 四个波次收口 T026/T036/T050/T060 + T056 + Polish 6）。合计 **67 个任务**，ID `T001–T067` 连续，`[P]` 标记 44 处。
