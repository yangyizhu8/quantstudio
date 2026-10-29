# Tasks: duckdb 首跳升级（1.4.5 → 1.5.6）＋ PT-01 覆盖链 PK 保护

**Input**: 设计制品来自 `specs/002-duckdb-1.5-upgrade/`（spec / plan / research / data-model / contracts/version-gate-cli.md / quickstart）
**依据**: M-2 三件连审通过（2026-10-29，总调度）＋ 四项审核意见已回改 spec/plan/design/quickstart；analyze 发现 2×HIGH 已在本批回改
**格式**: `[ID] [P?] [Story?] 动作 + 文件路径`　**任务标签**: 〔A-xx〕/〔B-xx〕= 总调度任务清单编号，可追溯

## Format: `[ID] [P?] [Story?] Description`
- **[P]**: 可并行（不同文件、无未完成依赖）　**[Story]**: 归属用户故事（US1–US6）
- 每行含确切文件路径；Phase 1/2/10 任务不带故事标签，故事阶段任务必带

---

## Phase 1: Setup（共享基础设施）

- [X] T001 建回退点：`git stash create` + `git stash store`（铁律 03），hash 记入 `docs/evidence/duckdb-1.5-upgrade-window-evidence.md`（新建），并核 `git status --porcelain` 中本会话涉及路径清单
- [X] T002 〔A-10〕记录窗前实态基线（写同 T001 的 evidence 文件）：daemon 进程双形态 grep（`-m quantstudio.pipeline.daemon` 与 `quantstudio\pipeline\daemon.py`，**排除自身 $PID**）、`schtasks /Query /TN QS_DaemonForever /V /FO LIST`（确认未携带 `QS_DUCKDB_VERSION_GATE=0`）、主库尺寸与 mtime、WAL 体积、各解释器 duckdb 版本
- [X] T003 确认 `%TEMP%\venv_ddb156`（duckdb 1.5.6 隔离 venv）与 `%TEMP%\venv_ddb155` 仍可用，供 A-14 / F2 复测；不可用则登记并停（不得临时装包到生产解释器）
- [X] T004 核对 `scripts/check_passthrough_pk.py` 与 `docs/evidence/pk_baseline.json` 存在且 `--check` 输出 `OK`（F4 守卫基线，段 A 产物）

## Phase 2: Foundational（判定源内核 —— 完成前不得开始任何故事）

- [X] T005 [P] 在 `quantstudio/pipeline/duckdb_version_policy.py`（新增，**无包内依赖** ⇒ 可被 ps1/sh 单文件直调）定义放行集合：`ALLOWED_RANGES = [1.4.5, 1.5) ∪ {1.5.6}` + `parse_version()`（≥2 段、缺省 patch=0、不可解析返回 None）+ `is_allowed()`（fail-closed）〔A-01〕
- [X] T006 同文件加 CLI 入口（`--check [版本串]` / `--json` / `--describe`，stdout `PASS|FAIL|ERROR <v>`，退出码 0/1/2），契约照 `specs/002-duckdb-1.5-upgrade/contracts/version-gate-cli.md` §2–§3（**与 T005 同文件，不并行**）
- [X] T007 `quantstudio/pipeline/duckdb_version_gate.py` 降为薄封装：删除 `REQUIRED_SERIES`（外部引用点 = 0）、4 处硬编码文案改集合描述、保留 `GATE_ENV` 逃生阀与 `SystemExit(3)`、新增 `-m` 入口转发同一 CLI〔A-02〕
- [X] T008 〔A-02〕`requirements.txt:1` 与 `pyproject.toml:20`、`pyproject.toml:50` 改为**单版本精确锁定 `duckdb==1.5.6`**（依赖清单语法无法表达「区间∪精确点」并集），并以注释承载放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`
- [X] T009 〔A-05〕更正 `requirements.txt:2` 注释：1.5.x 全线（含 1.5.6）该 ART index-delete 缺陷仍在，仅由静默变为 FATAL，1.5.6 未修复
- [X] T010 〔A-02〕**全仓调用点兼容核查**（M-2 意见②）：grep 全仓 `REQUIRED_SERIES` / `duckdb_version_gate` / `require_duckdb_version` / `check_duckdb_version` / `GATE_ENV` / 版本字面量，产核查表落 `docs/evidence/duckdb-version-gate-compat-audit-20261029.md`；判据 = 外部引用点为零（已读数确认：仅 `main_gui.py:19` 与 `quantstudio/pipeline/daemon.py:4077` 调用 `require_duckdb_version`，测试文件不引用 `REQUIRED_SERIES`），任何直接引用常量处须同批适配
- [X] T011 〔A-02〕`check_wal.ps1:66-72` 改为调用判定源单文件 `quantstudio\pipeline\duckdb_version_policy.py --check $ver` 并取 `$LASTEXITCODE`，删除本地版本正则（文案改集合描述；**实施期形态调整**：不取 `-m` 形态，避免依赖可导入的项目包）
- [X] T012 [P] 〔A-02〕`check_wal.sh:41-48` 同样改调用判定源单文件取退出码（**保留 `${VAR}` 花括号纪律**，铁律 23）
- [X] T013 [P] 同步 `scripts/check_wal_regression.sh`（版本门伪造 shim 拆 1.5.5/1.5.6 双臂 + 判定源随行复制），跑全绿（实测 **PASS=13 FAIL=0**，禁触生产库）
- [X] T014 〔A-03〕重写 `tests/test_duckdb_version_gate.py`：**9 个契约点**（1.4.5✓ / 1.4.9✓ / 1.5.6✓ / 1.4.4✗ / 1.5.0✗ / 1.5.5✗ / 1.6.0✗ / 2.0.1✗ / 2.1✗）＋C3 逃生阀＋C4 导入失败＋C5 不可解析 fail-closed＋**C6 单源同一性机检**（`is` 断言）＋CLI↔进程内逐点比对；实测 **27 passed**
- [X] T015 跑 `pytest tests/test_duckdb_version_gate.py -q` 与 CLI 退出码矩阵自检，输出落 `docs/evidence/duckdb-version-gate-matrix-20261029.md`（声明面 pin 落于集合内 + 三判定面逐项一致）

## Phase 3: User Story 1 - 升级后进程能正常启动并读写 (P1) 🎯 MVP

**Goal**: 三入口在放行集合内放行、集合外拒启（固定退出码 3），逃生阀语义不变
**Independent Test**: 以集合内/外两种版本分别触发门禁判定，看放行与拒启

- [X] T016 [P] [US1] 门禁三入口自检脚本（`main_gui.py:19`、`quantstudio/pipeline/daemon.py:4077`、GUI 拉起子进程形态）落 `docs/evidence/` 下临时脚本，正反向各跑一次并留读数
- [X] T017 [US1] 拒启文案三出路核对（换合规解释器 / 降版 / 显式放行）＋ `QS_DUCKDB_VERSION_GATE=0` 放行时必留醒目告警，证据落 `docs/evidence/duckdb-version-gate-matrix-20261029.md`
- [X] T018 [P] [US1] 〔A-06〕准备 S1–S5 冒烟脚本：**S1 读写 / S2 `Asia/Shanghai` 时区往返 / S3 重叠批 upsert 冲突探测 —— 写入只落独立临时库或独立文件**（不碰主库、不经 `create_writer` 入口、不改既有表与 schema）；**S4 分片与主键点查 —— 主库只读**；**S5 `storage_version` 回显 —— 文件头只读**（`uint32 LE @12`，magic `DUCK`@8，创建版本串 @52；实测 64 ≤68）；脚本落 `docs/evidence/` 下可复跑
- [X] T019 [P] [US1] 〔A-06〕准备 S6 门禁三入口正反向脚本（可窗外执行，以替身调用触发，不连主库）

## Phase 4: User Story 2 - 判定口径三面一致 (P1)

**Goal**: 门禁 / 双脚本 / 契约三判定面对同组版本串判定逐项一致；声明面单版本 pin 落于集合内
**Independent Test**: 对同组版本串分别跑三面，比较结果矩阵

- [X] T020 [P] [US2] 三判定面一致性机检脚本（CLI 退出码 vs 契约断言 vs 双脚本退出码），逐项比对并落 `docs/evidence/duckdb-version-gate-matrix-20261029.md`
- [X] T021 [US2] 声明面静态核对：grep `duckdb==1.5.6` 命中 `requirements.txt:1` 与 `pyproject.toml:20`、`:50`，且注释载明放行集合

## Phase 5: User Story 3 - 出问题能在窗口内回退 (P2)

**Goal**: 降版可执行、旧版可打开现库、门禁同步回滚、库异常走既有收敛入口
**Independent Test**: 演练环境执行一次降版 + 旧版打开现库 + 抽样读 + 门禁复核

- [X] T022 [US3] 〔A-07〕回滚步骤成文落 `specs/002-duckdb-1.5-upgrade/quickstart.md` §2：降版命令用**显式版本** `pip install "duckdb==1.4.5"`（**禁用 `pip install -r requirements.txt`**——声明面已锁 1.5.6），并补「旧版打开现库 + 抽样读取 + 门禁复核」判据
- [X] T023 [US3] 〔A-07〕库异常路径核对：`check_wal.ps1 -Force` / `check_wal.sh --force` 处置 + **禁手工删除 WAL**（铁律 23），演练记录落 `docs/evidence/duckdb-1.5-upgrade-window-evidence.md`
- [X] T024 [US3] 〔A-07〕回滚触发条件清单成文（窗内判据不达标 / daemon 无法以计划任务恢复 / 旧版打不开已升级库 / 窗口超 60 分钟）→ 同步 `docs/duckdb-1.5-upgrade-design.md` §5

## Phase 6: User Story 4 - 覆盖链不再静默销毁主键 (P2) · PT-01 并行轨

**Goal**: 覆盖后主键 1→1、改道前后双写逐位一致、静态守卫前瞻拦截
**Independent Test**: 合成库覆盖一次比主键存在性 + 一份故意指向带主键表的配置跑静态检查

- [X] T025 [P] [US4] 〔B-03′〕`quantstudio/pipeline/config_lint.py` 的 `_lint_one_task` 新增 **ERROR 级**规则：`task["passthrough"]` 为真且 `task["table"]` 命中 writer 主键字典 ⇒ 报错并点名 task 与表（复用 `writers.py:1434` 表→主键列字典，与既有 `config_lint._lint_schema_pk_consistency` 同源，**不建第二份**）
- [X] T026 [P] [US4] lint 负向用例 + 现状零告警：构造指向带主键表的配置必拦；现 67 个 passthrough 任务（53 启用 / 14 禁用）跑 lint 零告警
- [X] T027 [US4] 〔B-01′〕`quantstudio/pipeline/writers.py:1575-1628`（`_write_passthrough`）改 F2：`TRUNCATE TABLE` + `INSERT`（**同一事务包覆**），列集一致走 F2
- [X] T028 [US4] 〔B-01′〕`quantstudio/pipeline/writers.py:1672-1762`（`write_passthrough_chunked`）同步 F2，**保留 staging + ledger 记账语义**（不得只换 SQL）
- [X] T029 [US4] 〔B-01′〕列集不一致（上游新增列）→ DDL 路径分支实现（`ALTER TABLE ADD COLUMN` 或回退 `DROP+RENAME`），并在实现前**断言该表不在指纹校验名单内**（`verify_fingerprint(reject_extra=True)` 定义在 `quantstudio/pipeline/qfq_schema_contracts.py:422`，指纹库为该模块的 `TARGET_MAIN_DB_*_FINGERPRINT`；passthrough 类别B 表不在其中，implement 时以实际断言为准）；**若命中指纹表 ⇒ 停手并报总调度**（契约变更六件套，铁律 24）
- [X] T030 [US4] 双写逐位一致测试：同一 df 经旧路径与新路径分别写入，比对列集 / 行数 / 内容哈希
- [X] T031 [US4] 覆盖后主键保持验证：合成库带主键表覆盖后 `duckdb_constraints()` 计数 1→1，且 `ON CONFLICT` 事后仍可用（照 `docs/evidence/pt01-f2-truncate-20261029.md` 判据）
- [X] T032 [US4] 〔B-01′〕更正 `quantstudio/pipeline/writers.py:1573-1581` docstring（声称 `CREATE OR REPLACE`、实为 `DROP+RENAME`），并核对 `write_passthrough_chunked` 同类描述
- [X] T033 [US4] 全量回归（`pytest` 全库）零新增失败；失败须归因并区分「PT-01a 引入」与「他线在途」（**已完成**：基线 68 failed/3977 passed（39:28）vs 改后 68 failed/3984 passed（39:44）⇒ **新增失败 0**、失败集合逐项相同、passed +7 = 新增 PT-01a 测试；日志 `docs/evidence/_pytest_baseline_20261029.log` 与 `_pytest_after_20261029.log`）
- [X] T034 [P] [US4] 〔B-02′〕runbook 文本（**备而不用，不要求演练**）成文：含段 A 条件补齐后解释 C 的四条剩余可能、丢主键两种后果（带 `ON CONFLICT` 目标 ⇒ BinderException 拒绝；普通 INSERT ⇒ 可能插重复）
- [X] T035 [P] [US4] F4 巡检守卫接入每日哨兵（④已先行完成）：`scripts/check_passthrough_pk.py`（**只读、零源码改动**，新增检测型能力）（**已由④先行完成**（2026-10-29 02:11）：接入总运维每日哨兵**第 4 步** + 首轮全绿 —— **我方此前误判「尚未接入」，特此更正**；本会话使命收窄为两处补丁：①哨兵第 4 步文本补 runbook §3 处置引用 +「禁用 --snapshot」；②接入说明 §五「daemon 运行期多为 SKIP」句按④三次实测（read_only 与 daemon 写连接共存 ⇒ exit 0）修订。修订版随④闭环回执）

## Phase 7: User Story 5 - 治理条文与知识库不再自相矛盾 (P3)

**Goal**: 条文含三要素且消解原冲突；活文档旧口径零残留
**Independent Test**: 三要素可检索定位；「必须 1.4.x」类表述在活文档内零命中

- [X] T036 [P] [US5] 〔A-04〕**已落码**（commit `76106cf`，2026-10-30，②审通过后）`.specify/memory/constitution.md:38` 条文**替换**稿（①放行集合 ②90/60/30 升级判据 ③到期复核装置：EOL 日历守护 + 缺陷单关闭或新补丁发布后自动落复核提醒），替换稿已成文（`docs/handoff/duckdb-1.5-upgrade-governance-approval-20261029.md` §一，含①放行集合 ②90/60/30 判据 ③到期复核装置；**承载方＝总调度 EOL 日历守护 + 仓库内守护条目**，落地由 **T056** 承接）；**措辞随②审呈批后落码**
- [X] T037 [P] [US5] 〔A-12〕**已落码**（commit `76106cf`）`AGENTS.md:207`（23 号铁律摘要）与 `knowledge/iron-rules/23-WAL残留处置.md:6` 的「版本门必须 1.4.x」→ 改为「须落在放行集合内且以判定源为唯一实现」，**只出 diff 与提交信息草稿**（跨域治理，随 ②审呈批后落码）
- [X] T038 [P] [US5] 〔A-12〕**已落码**（commit `e941944`：customer-notice-4 / gui-startup-nonblocking-design / writers-export-three-requests-design / case008 追加节 / environment-map §8）界定「活文档」并出同步 diff：`docs/handoff/customer-notice-4-duckdb-component-warning-20260921.md:64`、`docs/case008-duckdb-version-mixing-unification-incident.md:5,49`、`docs/gui-startup-nonblocking-design.md:61`、`docs/writers-export-three-requests-design.md:37,195`；**历史卷宗不回改**（`.specify/assessments/**`、`.specify/bugs/**`、`docs/handoff/dispatch-calendar-*.md` 记的是当时事实），登记为历史事实并列入证据说明
- [X] T039 [P] [US5] 〔A-13〕`docs/evidence/duckdb-version-environment-map-20260923.md` 追加「只读消费者」节：`trading-battle-back\venv_miniQMT`（duckdb 1.5.3）不升级、仅只读消费、其开启写者入口将被门禁拒绝（总调度 2026-10-29 裁定）

## Phase 8: User Story 6 - 客户能一次自检 (P3)

**Goal**: 一条命令输出版本 + PASS/FAIL；通知含同批升级要求与量化提示
**Independent Test**: 干净环境跑自检命令，看输出与退出码

- [X] T040 [P] [US6] 〔A-08〕客户通知稿成文（`docs/handoff/` 下新件）：代码与依赖**同批升级**（否则门禁 `SystemExit(3)` 拒启）＋ 自检命令 `python -m quantstudio.pipeline.duckdb_version_gate --check` ＋ **A-14 量化句**「批量写路径在 1.5.6 上约 4–6 倍：300 行 3.9s、1000 行 13.4s、5000 行 >90s（超时）」；下发不晚于 11-17
- [ ] T041 [US6] 〔A-08〕通知送审路径执行：出稿 → 总调度审 → 呈用户 → **用户闸门后下发**（不得由执行会话代下发）—— **进行中**：稿已出并呈总调度；等总调度审 → 转呈用户 → 用户闸门后方可下发（本会话不下发）—— **总调度初审已过（2026-10-30）**；终审与用户闸门随 M-5 下发前同轮

## Phase 9: M-3 窗口执行（11-07/11-08 周末凌晨，≤60 分钟）

- [ ] T042 〔A-11〕窗口**占用声明**（按铁律 19：时段 + 操作类型 + 预期恢复时点），并**与 dev 线协调**（11 月已入实盘准备期，停 daemon 须避交易日盘前链），协调回执附证据；未取得 dev 线确认前不得开窗
- [ ] T043 [US1] 窗口 W1–W3：关 GUI → 停 daemon（精确 PID，**排除自身 $PID**，禁宽关键词匹配）→ 记录 t0/库尺寸/WAL
- [ ] T044 [US1] 〔A-13〕窗口 W4–W5：装 `duckdb==1.5.6` 到运行主库的解释器（**显式版本命令**）→ 门禁自检 `PASS 1.5.6`
- [ ] T045 [US1] 〔A-06〕窗口 W6：执行 S1–S6 冒烟（脚本见 T018/T019；**写入只落独立临时库，主库仅只读点查与文件头读数**），逐项读数落 `docs/evidence/duckdb-1.5-upgrade-window-evidence.md`
- [ ] T046 [US3] 窗口 W7：E7 升级后基线一次（与升级前成对）
- [ ] T047 [US1] 窗口 W8：`schtasks /Run /TN QS_DaemonForever` 拉起 daemon（**禁 Start-Process**），核 `data/daemon_status.json` 的 `started_at` 更新且 cmdline 为 `-m …daemon`
- [ ] T048 [US1] 窗口 W9–W10：记录 t1（窗口时长 ≤60 分钟）、WAL 体积、daemon 首轮耗时，拆除占用声明，窗口报告入 evidence
- [ ] T057 〔A-07〕**回滚演练 1 次**（兑现 SC-004 · M6 补任务）：在停服窗内或演练环境执行一次完整回滚——降版 1.4.5 → 旧版只读打开现库 + 抽样读取（`stock_daily` / `trade_calendar` / `index_constituents_snapshot_meta`）→ 门禁复核 `PASS 1.4.5` → 复升 1.5.6 → 门禁复核 `PASS 1.5.6`；读数（库尺寸 / 各步耗时 / 退出码 / 抽样行数）落 `docs/evidence/duckdb-1.5-upgrade-window-evidence.md`

## Phase 10: Polish（观测、证据与收口）

- [ ] T049 [P] 〔A-09〕观测面采集：窗口实测分钟 / S1–S6 通过数 / `Failed to delete all rows from index` 计数（须 = 0）/ WAL 体积 / daemon 首轮耗时 / 升级前后各一次 E7 基线，逐项注来源
- [ ] T050 [P] 〔A-09〕升级后首周哨兵关键词接入（缺陷 FATAL 文本 + PK 巡检），落每日哨兵配置
- [X] T051 〔A-14〕**已完成（2026-10-30）**：①**结论落通知** —— `docs/handoff/customer-notice-5-duckdb-1.5.6-upgrade-20261029.md` §六量化表（300 行 3.9s / 1000 行 13.4s / 5000 行 >90s）+ §七 FAQ 读法；②**风险登记** —— `docs/evidence/a14-risk-register-20261030.md`（`RSK-DUCKDB-001`，等级中，影响面仅辅助表刷新时延，处置口径/解除条件/复审触发齐备，局限如实申报）；③**改写入形态单独立项** —— `docs/handoff/duckdb-executemany-write-path-refactor-20261030.md`（落点 `index_constituents_meta.py:125`，判型纯性能型，验收含同数据集 sha256 逐位一致 + ≤2× 且 ≤5s）
- [X] T052 两条 KB 沉淀起草（M-4）—— **已完成（2026-10-30）**：db 族 schema 已由⑥登记（`knowledge/kb-info.yaml`，案件 7a78e15）；三条经 `scripts/kb_draft.py` 过 `kb_hook.validate_record` 机检落盘 `knowledge/db/DB-2026-0001..0003.md`（①executemany 劣化 D2/write_path+upgrade ②DROP+RENAME 清 PK vs TRUNCATE 保留 D3/write_path+constraint+lint_guard ③制品判据回扫 D5/artifact_rule），随 commit `8149565` 入库；**第四条候选（暂存区混入他线文件范式）经评估不入 db 族（族域＝数据库引擎与数据完整性域，域不符）**，结论落 `docs/handoff/duckdb-kb-candidate4-assessment-20261030.md`（铁律 10 完整版增补待授权）—— **前置依赖**：⑥会话先在 `knowledge/kb-info.yaml` families 段登记 `db` 族（数据库引擎与数据完整性域，总调度 2026-10-29 裁定）；本会话已备三条候选文案 `docs/handoff/duckdb-kb-candidates-20261029.md`（含 kb_draft 参数），族登记后经 `scripts/kb_draft.py` 入族：①1.5.x executemany 劣化 + 本项目唯一引擎落点 ②DROP+RENAME 静默清空 PK / TRUNCATE 保留（含两版本读数）——用 `scripts/kb_draft.py` 按族起草、过 `kb_hook.validate_record` 机检后落 `knowledge/`
- [ ] T053 证据归档核对：`docs/evidence/` 下 A-14、F2、门禁矩阵、调用点核查、窗口记录、回滚记录、PK 基线七件齐
- [X] T054 跑 `speckit-analyze` 交叉分析（只读）→ 无 CRITICAL/HIGH 后呈报**检查点 A**（spec/plan/tasks 三件套摘要 + analyze 结论）→ **批准方为总调度**（口径更正 2026-10-29：六步流水线的**⑤用户确认闸在推送前**，不在改码前）⇒ 总调度批准后方可改码 —— **已提前执行（改道后复跑）**：首轮结论 = **0 CRITICAL / 3 HIGH / 6 MEDIUM / 11 LOW**（2026-10-29）⇒ 按规则回修：3 HIGH 已按总调度裁定修毕（H1 严格三段口径 + 四层形态判定，判定源/契约/契约测试/CLI 契约四处同步；H2 SC-006 转风险接受口径；H3 治理替换稿以 FR-010 原文为唯一口径）、6 MEDIUM 全修、11 LOW 全修；**重跑 analyze 结论（2026-10-30）**：**新增 0 CRITICAL / 0 HIGH / 0 MEDIUM / 0 LOW**；D-1（已登记 · 非新增）**已于 2026-10-30 commit `76106cf` 落码关闭**（constitution:38 替换稿生效 ⇒ 宪法 MUST 不再与实施冲突；**未靠 analyze 稀释**）⇒ 本任务可勾，D-1 挂账至 Phase 7 关闭。回修落点：判定源严格三段 + 四层形态（`duckdb_version_policy.py`）、契约测试 +9 例（38 passed）、CLI 契约 §2/§3/§6、spec（Edge Case / Assumptions / FR-002 / FR-006 / SC-006 / SC-007 / Status / 库尺寸 / A-13）、plan（结构决策 / 契约点数 / 行数 / Complexity② / Phase D / 库尺寸）、tasks（依赖 / 并行表 / T035 / T057）、治理呈批件 §一（H3：90/60/30 以 FR-010 原文为唯一口径）
- [ ] T055 提交与推送纪律执行（仅在检查点 A 通过且用户明确确认后）：精确路径 `git add`（**禁 `git add -A`**）、commit message 用 uuid 唯一文件名（`io.open(..., encoding="utf-8", newline="\n")`）、提交后核 `git log -1 --format="%s" HEAD`、双仓推送并核对两远程一致 —— **本地提交已完成（2026-10-30，用户放行；⑤随 #12 组批）**：笔③实现批 `fdca384`（9 文件：判定源+门禁+声明面+双脚本+回归脚本+writers.py+config_lint.py）／笔④制品批 `8149565`（29 文件：两测试+SDD 制品 8 件+证据与脚本 10 件+handoff 6 件+KB 三条）；／笔⑤批后增量 `53a6afd`（2 文件：tasks 状态 + 第四条候选评估件）。**⑤组批口径（#12 更正，2026-10-30）：以我方 3 笔为准**（`fdca384` / `8149565` / `53a6afd`）；Phase 7 治理两笔 `76106cf` / `e941944` **已出仓+追认，不再列入⑤表**。／笔④**第四笔**（T051/T056 制品批，9 文件：T051 风险登记+立项件、T056 EOL 日历×2+守护脚本+自测、铁律 10 增补草案、哨兵接入说明、tasks 状态）已提交（SHA 见本轮上报）。**本轮共 4 笔（fdca384 / 8149565 / 53a6afd / 第四笔）；**三笔均：精确路径 add + `git commit --only` + uuid 唯一 message 文件 + 提交后核标题与文件清单逐项相同**；**双仓推送待⑤用户确认闸门**（尚未 push）
- [ ] T056 〔A-04〕**到期复核装置落地**（承载方按 T036 裁定）—— **实现部分已完成（2026-10-30）**：EOL 日历条目（机读 `docs/duckdb-version-eol-calendar.json` + 人读 `docs/duckdb-version-eol-calendar.md`）+ 守护脚本 `scripts/check_duckdb_eol_guard.py`（只读判定，退出码 0 无提醒 / 1 有提醒 / 2 错误，支持 `--as-of` 模拟、`--patch-note` 事件触发、`--emit` 落提醒）+ **实现自测 8/8 通过**（读数 `docs/evidence/duckdb-eol-guard-selftest-20261030.md`：6 档里程碑分层 + 事件触发 + 2 条错误路径 fail-closed）；**「验证能被真实触发一次」尚未发生**（当前为模拟日期自测）⇒ 本任务**不勾**；**接入已排定**：④每日哨兵**第 6 步**接入说明 `docs/handoff/duckdb-eol-guard-sentinel-onboarding-20261030.md`（**真新增检查，与 T035 F4 第 4 步非重复项**；退出码 0/1/2 语义 + **exit 1 = 1.4 线 T−30 属实态非故障**）；**首跑 2026-10-30 20:30 由④执行 = 真实触发验证**（当前实态 T−30 ⇒ 预期 exit 1 + `close_or_accept` + 提醒件 `docs/evidence/eol-review-reminder.md` 落盘）；**勾选条件 = 首跑读数 + 提醒件落盘回执**（两项齐备即勾）

## 任务映射（总调度清单 → T 编号）

| 清单项 | 任务 | 清单项 | 任务 |
|---|---|---|---|
| A-01 放行口径 | T005 | B-01′ PT-01a PK 保护 | T027–T033 |
| A-02 六落点同批改 | T006–T013 | B-02′ PT-01b 消费（runbook） | T034 |
| A-03 契约重写 | T014 | B-03′ 白名单守卫 | T025、T026 |
| A-04 条文三要素 | T036、T056 | A-06 冒烟 S1–S6 | T018、T019、T045 |
| A-05 注释更正 | T009 | A-07 回滚预案 | T022–T024、T057（回滚演练 1 次 · 兑现 SC-004） |
| A-08 客户通知 | T040、T041 | A-09 观测指标 | T049、T050 |
| A-10 实施前门（实态） | T002 | A-11 排期与停服窗 | T042 |
| A-12 文档与知识同步 | T037、T038 | A-13 装包落点 | T039、T044 |
| A-14 风险计量 | T051（结论登记；改码另立项） | 门禁/声明/脚本三面一致 | T015、T020、T021 |

## Dependencies

- **T005–T015（Foundational）阻塞所有故事**：判定源与声明面不动，门禁与脚本不可改。
- **T016/T017（US1）依赖 T005–T007**；**T020/T021（US2）依赖 T011–T015**。
- **T018/T019 → T045**：冒烟脚本须先备妥，窗口内只跑不改。
- **T025/T026（lint）独立于 T027–T029**，可与 PT-01 实现并行；两者共同构成 B-01′/B-03′ 验收。
- **T027–T032 → T033（全量回归）**；**T029（DDL 分支）依赖其前置断言**（命中指纹表即停手报总调度）。
- **T036–T039（治理）不阻塞代码**，但 T037/T038 产出为 diff，须随 ②审呈批后落码；**T036 → T056**（承载方裁定后落地）。
- **T042–T048（窗口）依赖 T011–T015（门禁就绪）、T018/T019（冒烟脚本就绪）、T027–T032（PT-01a 已实施且回归通过）**；窗口未过即按 T024 回滚。
- **T040 依赖 A-14 风险计量读数**（量化句来源，已齐；**不依赖 T049** —— T049 是窗口观测，与量化句无关）；**T052 依赖 T045/T049 证据齐备 + ⑥会话完成 `db` 族登记**（`knowledge/kb-info.yaml` families 段）。

## Parallel Opportunities

- Phase 2 内：`T008`/`T011`/`T012`/`T013` 可并行（不同文件）；`T011`（ps1）与 `T012`（sh）互不依赖；**`T005` 与 `T006` 同文件（判定源单文件）⇒ 不可并行**（实施期已按此串行执行）。
- Phase 6 内：`T025`/`T026`（lint 线）与 `T027`/`T028`（写入路径线）**两条线并行**，但**不得同批改 `writers.py` 与 `config_lint.py` 后再合并提交**（双链同改风险）——建议分两笔提交、分别验收。
- Phase 7 内：`T036`/`T037`/`T038`/`T039` 四件互不依赖，可并行起草（全部只出 diff/稿件）。
- Phase 9 内：`T044`（装包）与 W4–W5 序列不可并行，其余按 W 序列执行。

## Implementation Strategy

- **MVP = Phase 2 + Phase 3 + Phase 4**：判定源就位 + 三入口行为正确 + 三面口径一致 ⇒ 升级前置条件全部满足，可独立交付并单独评审。
- **增量 1 = Phase 6（US4）**：PT-01a 修复与升级主线**代码路径完全分离**（`writers.py` 两分支 vs 判定源/脚本），可与升级窗口**解耦排期**；但两者不得同 commit。
- **增量 2 = Phase 5 + Phase 7**：回滚能力与治理条文，前者可随窗口演练验证，后者随 ②审呈批落码。
- **增量 3 = Phase 8 + Phase 10**：客户通知（M-5 ≤11-17）与观测/证据/KB 沉淀（M-4 ≤11-15）。
- **窗口 = Phase 9**：一次性跨切面动作，≤60 分钟，失败即按 T024 回滚。
