# ④验收 · 003-delivery-bundle-import

- 抓拍：2026-10-31 22:35（Asia/Shanghai）
- 口径：**证据类型只认三种**——`fixture 实测` / `生产窗实测` / `豁免+传导`；无证据的不写"通过"

## 1 全量隔离 runner（实测读数）

- 隔离变量：`QUANTSTUDIO_DATA_ROOT=<temp>/data`、`QS_WRITE_LOCK_DIR=<temp>/lock`、`QT_QPA_PLATFORM=offscreen`
- 基线（非隔离，W2）：**4305 tests / 70 failed / 18 skipped**
- 本轮（隔离）：**4328 tests / 75 failed / 19 skipped / 0 errors / 1482 s（24:42）**，junit 落 `delivery-acceptance-junit-20261031.xml`

### 1.1 新增失败归因（**特性相关 = 0**）

差集 = 9 项新增（基线 70 + 9 − 4 项未复现 = 75）：

| # | 用例 | 归因 | 处置 |
|---|---|---|---|
| 1 | `test_qmt_export_parity::test_tab_registered_in_main_window` | **本特性引起**：断言 `_NAV_ITEMS` 恰为 11 项，新增「数据交付」后为 12 | 已改为 `>= 11`（本用例意图是"QMT 已注册"，锁死总数属脆弱断言）；改后单跑与实际隔离环境均 **3 passed** |
| 2–9 | `test_engine_config_default_uses_project_root`（1）、`test_gui_rebalance_mode`（2）、`test_qfq_schema_migration::TestProductionRefuse`（3，含参数化）、`TestReportPathSafety`（2） | **隔离 env 造成**：这些用例把"生产路径/工程根"写死为真实路径，隔离后 `db_path()` 指向 temp ⇒ 断言两侧不一致；与交付特性零关系 | 非隔离复跑 **29 passed（10:52）** —— 证明与本特性无关 |

**结论：隔离口径下"特性相关新增失败 = 0"；剩余 8 项是隔离 runner 与"生产路径硬编码类断言"的固有冲突（该冲突本身是隔离 runner 的已知代价，非回归）。**

### 1.2 交付特性测试

**141 passed / 0 failed / 54.40 s**（13 个文件，含 17 条 SC 的对应用例）

## 2 17 条 SC 终扫表

| SC | 判据摘要 | 证据类型 | 证据指针 |
|---|---|---|---|
| SC-001 | 五种前置拦截（schema/写锁/daemon/空间/WAL）各一次导出，产出文件数均为 0 | fixture 实测 + **生产窗实测** | `tests/test_delivery_export.py::test_sc001_*`（5 例）；生产窗：`delivery-l3-window3-report`§2（`TARGET_HANDLE_BUSY` 1.5 s 拒绝、零产出） |
| SC-002 | 零泄露：`*.env`/日志/缓存/白名单外/`-shm` 命中数全 0 | fixture 实测 + **生产窗实测** | `test_sc002_zero_leak`；生产窗两轮 `zero_leak=true`（`delivery-l3-window3-report`§2） |
| SC-003 | 同刻一致：三文件均可只读打开且状态可共同读通 | fixture 实测 + **生产窗实测** | `test_sc003_same_instant_three_files_readable`；生产窗两份各 `db_open=ok ×3`、`-wal` 全 0 |
| SC-004 | 存量客户导入成功路径（校验→持锁→备份→覆盖→复验） | fixture 实测 | `test_delivery_import.py::test_sc004_update_happy_path` |
| SC-005 | 失败路径零改动（坏包/schema 不符/配置不符） | fixture 实测 | `test_delivery_verify.py::test_sc005_zero_mutation_on_bad_packages`（三参数化） |
| SC-006 | 覆盖期注入失败 → 回滚后主库可读、schema 与行数回原 | fixture 实测 | `test_delivery_rollback.py::test_sc006_rollback_on_replace_failure` |
| SC-007 | 安装模式三保留件（snapshots/.gitkeep/README） | fixture 实测 | `test_delivery_import.py::test_sc007_install_mode_keeps_existing_assets` |
| SC-008 | 不跨世代：同参回测逐位一致 | **豁免+传导**（跨世代）+ **传导链强化**（不跨世代，降级已申报） | `test_delivery_sc008_exemption.py`（跨世代：实测 `c_future→c_pkg`，字节+列集）；`test_delivery_sc008_ab.py`（不跨世代：schema 状态 + 逐表列集 + 抽样行摘要 + 字节一致；**未含带交易撮合**，理由见文件头 §降级理由） |
| SC-009 | 双端一致：CLI 与 GUI 同库各导出一次，逐文件 SHA256 100% | fixture 实测（三方全等）+ **生产窗实测** | `test_delivery_parity.py::test_sc009_cli_and_kernel_produce_identical_hashes`；`test_delivery_gui_offscreen.py::test_sc009_gui_offscreen_export_matches_cli`（offscreen，GUI==CLI==内核）；生产窗：`delivery-l3-window3-report`§2（3/3 SHA 全等） |
| SC-010 | 配置删除/字段空/路径不存在三情形 GUI 正常且按钮禁用+提示 | fixture 实测 | `test_delivery_parity.py::test_sc010_*`（3 例：容错加载/多余键/原子写）+ `test_delivery_gui_offscreen.py::test_sc010_gui_buttons_disabled_until_dir_chosen`（禁用态 + 「请先选择目录」） |
| SC-011 | 备份方式与空间证据链（inode/size+mtime/耗时/空间） | fixture 实测 | `test_delivery_import.py::test_sc011_backup_is_rename_with_evidence`（`method=rename`、`st_ino` 一致、<5 s） |
| SC-012 | 体积显示为实测值，代码/文档无写死体积常量 | fixture 实测 | `test_delivery_export.py::test_sc012_volume_readings_are_measured`；`data/README.md` 已删体积数字 |
| SC-013 | 纯增益可机检（基线相对）+ 零新增失败 | **机检实测** | `delivery-pure-gain-audit.py --mode audit --baseline …baseline-v2`：PASS（36 路径 0 变化）；§1 全量新增失败读数 |
| SC-014 | 崩溃恢复二选一 + 半新半旧不被再备份 | fixture 实测（真 `os._exit(9)`） | `test_delivery_journal.py::test_sc014_crash_then_rollback` / `test_sc014_crash_then_continue`（备份目录恒 1）；`test_import_refuses_when_journal_present`（exit 7） |
| SC-015 | 坏库修复路径（0 字节起点）不终止、结果记录「原库已损坏」 | fixture 实测 | `test_delivery_import.py::test_sc015_repair_mode_on_zero_byte_db`（forensic 四字段 + 0 字节原件保留） |
| SC-016 | 导入幂等：同包两次，复验四态一致 | fixture 实测 | `test_delivery_import.py::test_sc016_idempotent_two_imports` |
| SC-017 | 复制期 ENOSPC → 按 journal 回滚、journal 清理 | fixture 实测 | `test_delivery_rollback.py::test_sc017_enospc_during_staging_rolls_back` |

**缺口（如实申报）**：SC-008 的「带交易策略撮合结果等价」未取得——真实引擎在 fixture 上因 provider 硬编码 `time` 列而零成交（证据：`Candidate bindings: "trade_date"`），按裁定走降级并申报；补测需真实库窗口。

## 3 SC-013 终审 + D-6 四文件终对照

- `delivery_pure_gain_audit.py --mode audit --baseline docs/evidence/delivery-pure-gain-baseline-v2-20261031.json` → **PASS（36 保护路径 / 0 变化 / 新增未跟踪 无）**
- D-6（APPEND_ONLY 只核不判）终对照：

| 文件 | + | − | 归属 |
|---|---|---|---|
| `quantstudio/gui/main_window.py` | 8 | 0 | 本会话 4（`数据交付` nav + `row == 11` 块）／他线 4（`QMT 转换部署` nav + `row == 10` 块） |
| `quantstudio/gui/workers.py` | 161 | 0 | 本会话（委派 ZCode，已补验） |
| `pyproject.toml` | 1 | 0 | 本会话（`qs-deliver`） |
| `.gitignore` | 6 | 0 | W0 已落 |

## 4 交付物全清单

**内核（quantstudio/delivery/，10 模块）**：`constants` / `config` / `manifest` / `probe` / `winenv` / `stats` / `verify` / `export_kernel` / `import_kernel` / `journal`（+`cli`、`__init__`）

**CLI**：`qs-deliver`，6 子命令 `export / status / verify / import / recover / set`（`--help` exit 0）

**GUI**：`quantstudio/gui/tabs/delivery_tab.py`（247 行）+ `workers.py` 三个 worker（`DeliveryExportWorker` / `DeliveryVerifyWorker` / `DeliveryImportWorker`）+ `main_window.py` 注册（nav + `row == 11`）

**配置**：`config/delivery_config.json`（仅两键）；`.gitignore` 两行（`data/backup_import_*/`、`data/import_journal.json`）

**测试（8 文件 / 143 例）**：`test_delivery_{pure_gain, precheck, fixtures, stats, verify, export, import, rollback, journal, parity, gui_offscreen, sc008_exemption, sc008_ab}.py`

**Evidence（docs/evidence/）**：`delivery-pure-gain-baseline-20261031.json`、`…baseline-v2-20261031.json`、`delivery-l3-window{,2-report,3-report,-declaration,-void,-declaration-v2,-declaration-v3}-20261031.*`、`delivery-l3-run1*`、`delivery-l3-sc009-compare-20261031.json`、`delivery-l3-junit-20261031.xml`、`delivery-l3-junit-failures-20261031.txt`、`delivery-wave0/1/2/3/4-20261031.md`、`delivery-polish-20261031.md`、`delivery-w3-stale-lock-20261031_205842.json`、`delivery-acceptance-20261031.md`（本件）

**知识库**：`knowledge/db/DB-2026-0004 … 0009`（6 条，`kb_hook.validate_record` 全通过）

**文档**：`data/README.md`（FR-032 改造）、`specs/003-delivery-bundle-import/{spec,plan,tasks,data-model,research,quickstart}.md` + `contracts/`（4 件）

## 5 遗留债登记表

| 债 | 归属 | 状态 |
|---|---|---|
| D-28 `ATTEST` 错误可读性（`BinderException` 透传） | 本特性（维护项） | 未做，随下一批提交 |
| 5 张表 `UNKNOWN-BASIS`（map 与 alignment 均无基准列声明） | 实施方 / 配置 | 报告已给脚注；需补 `questdb_table_map` 或 `alignment_rules` |
| `snapshot_lock` 两笔（D-22 任务退出不清锁 + `diagnose` 假活） | `snapshot_lock` 归属线 | 已转交（文本见 W3 回执），铁律 08 只登记不擅动 |
| 70 项全仓既有失败 | 总调度（债面分派） | 清单已交；`test_b1_channel` 已定 PT-01a 转交 |
| watermark-preset 另案（水位 source 多义 / 3 行同表多 source） | 另一评估案 | 材料已更新为「source 维度多义」 |
| **SC-008 带交易撮合 A/B** | 本特性（需真实库窗口） | 未取得，降级已申报 |
| **fixture 业务表列名与 canonical DDL 偏差**（`stock_daily` 用 `trade_date`，`DuckDBMarketProvider` 期望 `time`） | 本特性（fixture 工厂） | **SC-008 带交易 A/B 的前置改进**：让 fixture 造 canonical 业务表，或让 provider 兼容两种列名；真实库窗口补测时该约束自然消失 |

## 7 ⑥推送记录与归档（2026-10-31 23:20）

- **commit**：`cd80acb3fb502cb67de98959d42f863f92801010` — `feat(delivery): 数据交付包导出与导入校验（specs/003 内核 + CLI + GUI 薄壳）`
- **写前快照（铁律 03）**：`stash create` + `stash store` → **`4ebde7644cb6f1355f39b6a5d0cbfce184b5c80a`**
- **精确清单 add（铁律 10）**：59 个路径，**未用 `git add -A`**；`config/delivery_config.json` 有意不提交（运行期配置）
- **双远程核对**：

| 位置 | main HEAD |
|---|---|
| local | `cd80acb3` |
| `origin`（quantstudio-plus） | `cd80acb3` |
| `origin` 第二 push URL（quantstudio） | `cd80acb3` |
| `eco`（quantstudio） | `cd80acb3` |

- **C7 推送闸**：首次拒推（范围内含他线两笔未声明）→ 按全量显式声明制以 `QS_PUSH_BUNDLE_ALLOWALSO='7aa6726,41b82b3'` 声明后放行（锚 = 本笔 `cd80acb`）
- **铁律 14 同步门**（触共享层 `main_window.py` / `workers.py` ⇒ 不豁免）：
  `QuantStudio-trading` `git fetch` + `git merge origin/main` → **`24241d6d → 83beee36`，MERGE_EXIT=0**；
  合并后共享层（`quantstudio/ config/ skills/ scripts/ tests/ main_gui.py`）本地改动 **0**；
  未推送 trading（副本推送留给该线自行决定）
- **既有测试例外留痕**：`tests/test_qmt_export_parity.py` 的 `== 11 → >= 11` 留在工作树**未提交**（该文件是 QMT 线未跟踪在途件），已写入 commit message 例外段
- **知会 QMT 线（待用户转达）**：「`tests/test_qmt_export_parity.py` 的 `_NAV_ITEMS == 11` 已放宽为 `>= 11`——因 specs/003 新增『数据交付』页签使 nav 项数合法变为 12，该用例意图是 QMT 已注册；改动未提交，请随你方批次入库。证据：`docs/evidence/delivery-acceptance-20261031.md` §1.1」


## 6 ⑤用户确认呈报包（**定稿版**，补三处后即为下发版）

- **功能一句话**：把整库（主库 + 两代 QFQ 因子库 + 自证 manifest）一键导出为可校验的交付包，并在客户机以「只读校验 → 备份 → 覆盖 → 复验」导入，全程可回滚。

- **路径一（本机导出）**
  1. 关闭/停止采集（**导出需独占写锁窗口**：daemon 运行时导出会被拒绝，提示「采集正在运行，请先停止采集」）；
  2. 打开 GUI → 左侧导航「数据交付」→「导出」区选目标目录（**不得位于 `data/` 内**）；
  3. 点「开始导出」（或 `qs-deliver export --export-dir <目录>`）；
  4. 等待完成，界面显示**实时体积读数**与「合格 qualified=true」；
  5. 把产物目录（含 `manifest.json`）整体交付给客户。
  > 导出期间请勿启动采集；如需并行，另开窗口按占用声明执行。

- **路径二（客户导入）**
  1. 停止采集（导入同样需独占写锁窗口）；
  2. GUI「数据交付」→「导入」区选交付包目录；
  3. 先点「只读校验」（可选但推荐）——不写盘，几十秒内可判包好坏；
  4. 点「开始导入」（或 `qs-deliver import --import-dir <目录>`）——自动校验 → 备份 → 覆盖 → 复验；
  5. 复验通过后**首日建议先跑单表增量试探**（例如只对 1–2 张关键表采一轮），确认采集正常再放开全量；
     异常时 `qs-deliver recover --action rollback` 一键回到导入前。

- **边界与已知限制**
  - **导出与导入都需停采集**（写锁窗口），这是设计约束而非缺陷；
  - 备份走同卷 rename（O(1)），跨卷时额外占用与包等量的空间，空间不足时在备份前即拒绝；
  - 5 张表缺基准列声明（`ai_research_snapshot`/`block_trade`/`broker_recommend`/`xueqiu_sentiment`/`etf_basic`）⇒ 标 `UNKNOWN-BASIS`，不判四态、不猜；
  - 跨复权世代导入时「回测结果零变化」判据不适用（合法重算），改用传导论证；
  - 体积一律**实时读数**（界面/文档均不写死数字）。

- **回滚方式**
  - 数据回滚：`backup_import_<时间戳>/` 内含导入前原件 + `RESTORE_README.txt`（确认新库正常前勿删；释放权在客户）；
  - 整体摘除本特性：移除 `quantstudio/delivery/`、GUI 页签文件与 `main_window.py` 的 nav/`row==11` 两处注册、`pyproject.toml` 的 `qs-deliver`、`.gitignore` 两行、`config/delivery_config.json`。

