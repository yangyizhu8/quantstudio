# Implementation Plan: 数据交付包导出与导入校验（CLI + GUI 双端）

**Date**: 2026-10-31 | **Spec**: [./spec.md](./spec.md)（v4+，FR-041/SC-013 已按 Q1 裁定改为基线相对口径） | **Status**: **待②审**（未过审不得写 tasks、不得改码）
**设计制品**: [data-model.md](./data-model.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md) · [research.md](./research.md)

---

## Summary

在 QuantStudio 内新增一条**纯增益**的「交付包」能力：本机在**同一写锁窗口**内导出一组硬编码白名单的三件套（`quantstudio.db` + `qfq_aux.db` + `qfq_aux_mcp_gen1.db`）并写出可自证的 `manifest.json`；客户侧按**九步**流程导入（只读校验 → 持锁 → 备份 → 可分步恢复覆盖 → 复验），带**三模式**（更新/修复/安装）与 **journal 崩溃恢复**。

技术路线一句话：**单一内核 + 双薄壳**——内核是新增独立包 `quantstudio/delivery/`（复用既有 `snapshot_lock` 写锁、`qfq_schema_status.detect_schema_status`、`qfq_aux_router.resolve_runtime_aux_path`、`_paths.DATA_ROOT`、`questdb_table_map.watermark_basis` 五个既有原语，不新造并行机制）；CLI（`qs-deliver`）与 GUI（「数据交付」页签）只做参数收集与展示，两端调用同一内核函数。

**零改动面**（硬约束）：`writers.py` / upsert 路径 / QFQ 引擎文件不落一行改动；`source_watermark` 表结构、`config/` 下既有配置、`batch_audit.db` / `quarantine.db` / `task_resume/` / 日志均不触碰。

---

## Technical Context

**Language/Version**: Python 3.11（仓库实测解释器 `Python311`，`data/daemon_status.json` 的 `exe` 字段）
**Primary Dependencies**: 标准库（`shutil`/`os`/`hashlib`/`json`/`pathlib`/`argparse`/`msvcrt`）+ 既有依赖 `duckdb==1.5.6`、`PyQt6` + `PyQt6-Fluent-Widgets`（GUI 壳）、`psutil`（进程存活判定，经既有 `snapshot_lock` 间接使用）。**不新增任何第三方依赖**
**Storage**: 主库 DuckDB `data/quantstudio.db`（58.03 GB 实测）；两代因子库 SQLite `data/qfq_aux.db`（10.81 GB）/ `data/qfq_aux_mcp_gen1.db`（53,248 B）；协作件为文件（`data/snapshots/.write_lock`、`data/import_journal.json`、`data/backup_import_*/`）
**Testing**: pytest（仓库现有 332 个 `tests/test_*.py`；本特性新增 `tests/test_delivery_*.py` + `tests/fixtures/delivery/` 小库构造器）
**Target Platform**: Windows 10/11（客户与实施机同平台；代码对 POSIX 保持可导入但不作为验收面）
**Project Type**: 单仓 Python 应用（CLI + PyQt GUI 双端，单一内核）
**Performance Goals**: 导出 68.9 GB 三件套在实施机磁盘上以字节级进度推进；校验**逐文件 fail-fast**（坏包数十秒内报错，而不是跑完 69 GB 哈希）；导入复验同校验口径
**Constraints**: 写锁窗口内完成「取锁 → checkpoint → WAL 归零断言 → 复制 → manifest」；**不得承诺跨文件原子性**（NTFS 无此语义）；duckdb 版本不得升级/降级（放行集合 `{1.4.5≤x<1.5} ∪ {x==1.5.6}`，判定源 `quantstudio/pipeline/duckdb_version_policy.py`）
**Scale/Scope**: 41 FR / 17 SC / 6 US；新增约 9 个内核模块 + 1 个 GUI 页签 + 1 个 CLI 入口 + 1 个审计脚本；主库单表最大行数量级未知（manifest 逐表统计口径见 §5）

---

## Constitution Check

对照 `.specify/memory/constitution.md`（v1.0，2026-10-27）逐条核对。**结论：全部通过，无未正当化违反**（设计完成后的复评见文末「复评」）。

| 原则 | 要求 | 本设计如何满足 | 判定 |
|---|---|---|---|
| 一 代码质量·最小可逆·落所有者 | 落点必须是所有者层；改动可单独回滚；禁无关重构 | 全部新增文件（`quantstudio/delivery/**`、`gui/tabs/delivery_tab.py`、`tests/test_delivery_*`、`scripts/delivery_*.py`）；对既有文件的改动只有 4 处**追加型**（`main_window.py` 页签注册、`gui/workers.py` 三个 worker、`pyproject.toml` 一条 script、`.gitignore` 两行）+ 1 处文档（`data/README.md`）。任一处均可单独回退 | ✅ |
| 二 测试先行 | 改动前先有可失败验证；验收可机检；区分新增能力型/行为漂移；证据落盘 | 每波先写测试再写实现（tasks 里测试任务排在实现前）；SC 全部落到 `tests/test_delivery_*.py` 或窗口内读数（后者在 plan §测试策略逐条标注）；本特性定性**新增能力型**（新入口开始报告真相），判据 = SC-013 基线相对 diff + 既有 332 个测试零新增失败 | ✅ |
| 三 可维护性·单一内核双薄壳 | 一份内核；双端对等可机检 | `quantstudio/delivery/` 为唯一内核；CLI 与 GUI worker 均为薄调用；对等性由 SC-009 机检（小库 fixture 上双壳跑同一包、比对 manifest 与哈希；生产库窗口内只读复测） | ✅ |
| 四 纯增益·可观察行为不漂移 | 三审计 + 显式分型 | 分型 = **新增能力型**；三审计：不改回测引擎取数/撮合/生命周期（零触碰）；不改写入通道（受保护路径 diff 基线相对为零）；不改 QFQ 世代机制（只读路由解析） | ✅ |
| 五 逐位一致与可回滚 | 结果正确性逐位一致；危险操作前建回退点与占用窗 | 导出件 SHA256 逐文件写入 manifest 并可复算；SC-009 双端逐位比对；停 daemon 前按铁律 19 声明占用窗（plan §测试环境与窗口）；写前快照按铁律 03 四步配方 | ✅ |
| 附加·duckdb 版本治理 | 不得改判定源/放行集合 | 本特性不触碰 `duckdb_version_policy.py`；manifest 只**记录**运行版本作为诊断 | ✅ |
| 附加·数据一致性（DDL 即契约变更） | 不改指纹表结构 | 只做行级只读查询（`source_watermark` 只 SELECT）；**零 DDL** | ✅ |
| 附加·并发边界 | 不做存储层结构性改造 | 复用既有应用级写锁；不引入存储层并发改造 | ✅ |
| 附加·文档同步 | 代码改动须同步文档 | FR-032 强制更新 `data/README.md` 分发说明（tasks 内含，不另立） | ✅ |

**无 constitution 违反 ⇒ Complexity Tracking 只登记「需辩护的例外」，见文末。**

---

## Project Structure

### 交付物树（本功能制品）

```
specs/003-delivery-bundle-import/
├── spec.md                    # 需求真源（不改）
├── plan.md                    # 本文件
├── research.md                # Phase 0 决策记录（10 项，无残留 NEEDS CLARIFICATION）
├── data-model.md              # manifest v1 / journal / 配置 / 结果对象 的字段级定义与校验规则
├── contracts/
│   ├── export_kernel.md       # export_bundle 行为契约（输入/输出/拒绝码/不变式/取消语义）
│   ├── import_kernel.md       # verify_bundle / import_bundle / recover_journal / detect_pending_journal 契约
│   ├── cli.md                 # qs-deliver 子命令、参数、退出码表、机读输出
│   └── manifest-v1.schema.json# manifest v1 的 JSON Schema（机检用，与 data-model 同源）
└── quickstart.md              # 两条操作路径 + 窗口开关步骤 + 验证命令
```

### 源码树（真实路径，非占位）

```
quantstudio/delivery/                  # ★ 新增：唯一内核
├── __init__.py                        # 公开内核 API（export_bundle / verify_bundle / import_bundle / recover_journal / detect_pending_journal）
├── constants.py                        # 白名单、排除模式、manifest 版本、状态枚举、退出码常量（单一权威定义处）
├── manifest.py                         # manifest v1 构建/解析/严格校验/规范化 JSON/相对路径编码
├── probe.py                            # 前置探测：schema 状态、写锁持有者、daemon 存活、磁盘空间、三路径互不包含、Windows 两项探针
├── stats.py                            # date_basis_col 解析 + 表集裁剪 + 逐表统计 + 六标签判定
├── export_kernel.py                    # 导出六步状态机（持锁窗口内 checkpoint→WAL 断言→复制→自证）
├── verify.py                           # 只读校验四要素 + 四类失败分派 + 世代路由一致性断言
├── import_kernel.py                    # 导入九步状态机 + 三模式 + 备份/覆盖/回滚 + 复验
├── journal.py                          # journal 原子读写 + 恢复决策（continue/rollback）+ 相位机
├── config.py                           # config/delivery_config.json 原子读写（缺失/空值 = 未选择，不报错）
└── cli.py                              # CLI 薄壳（argparse + 退出码 + --json 机读输出）

quantstudio/gui/tabs/delivery_tab.py    # ★ 新增：GUI 薄壳页签「数据交付」
quantstudio/gui/workers.py              # ＋追加 3 个薄 worker（ExportBundleWorker / VerifyBundleWorker / ImportBundleWorker）
quantstudio/gui/main_window.py          # ＋注册页签 1 处（紧随既有 216 行附近 qmt_export_tab 之后）

config/delivery_config.json             # ★ 新增（config_version/export_dir/import_dir；空值即未选择）
scripts/delivery_pure_gain_audit.py     # ★ 新增：SC-013 基线快照 + 收口比对（保护路径清单在此单一维护）
scripts/delivery_wave_gate.py           # ★ 新增：波次门禁（机检 evidence 文件必填读数，缺项即阻断下一波）
tests/fixtures/delivery/                # ★ 新增：小库构造器（tmp_path 内造 DuckDB 主库 + 两代 SQLite 因子库 + 造包/造坏包）
tests/test_delivery_export.py           # 导出：SC-001/002/003/012 + FR-001..008/034/035
tests/test_delivery_verify.py           # 只读校验：FR-009/010/011/019/038 + 四类失败分派 + 版本代差
tests/test_delivery_import.py           # 导入主路径 + 三模式 + 幂等（FR-013..018/033/039/040 + SC-004/007/015/016）
tests/test_delivery_journal.py          # journal 相位机 + 崩溃恢复二选一 + 禁重推模式（FR-014/036 + SC-014）
tests/test_delivery_rollback.py         # 程序内失败回滚（SC-006）+ ENOSPC（SC-017）
tests/test_delivery_precheck.py         # 前置拦截五场景（SC-001 兜底）+ 空间/路径重叠/句柄探针
tests/test_delivery_parity.py           # 双端对等（SC-009，小库 fixture + offscreen）+ 退出码
tests/test_delivery_pure_gain.py        # SC-013 审计脚本自测（基线一致/不一致分类）
docs/evidence/                          # 每波读数归档（delivery-pure-gain-baseline-*.json、delivery-wave*.md）
.gitignore                              # ＋ data/backup_import_*/ 与 data/import_journal.json（FR-031）
data/README.md                          # 分发说明改指向 GUI「数据交付」页签（FR-032）
pyproject.toml                          # ＋ [project.scripts] qs-deliver = "quantstudio.delivery.cli:main"
```

**Structure Decision**：新增独立包 `quantstudio/delivery/`（而非塞进 `pipeline/`）——理由是交付包是**面向外部分发**的能力，与采集管线的生命周期/依赖方向相反（它必须在 daemon 停止时运行、且不得 import 采集写入路径之外的耦合模块）；独立包使「内核零改写入路径」在结构上可保证（`delivery` 不 import `writers`）。GUI 沿用既有 `gui/tabs/*` + `gui/workers.py` 模式（与 `export_tab`/`ExportWorker` 同构）。

---

## §1 模块划分与边界（七点①⑦）

### 1.1 依赖方向（单向，可机检）

```
cli.py ─┐
        ├→ __init__.py（内核 API）→ export_kernel / verify / import_kernel / journal
delivery_tab.py ─┘                      ↓
                                    probe / stats / manifest / config → 既有原语
```

**允许 import 的既有原语（白名单，5 个）**：
| 原语 | 来源 | 用途 |
|---|---|---|
| `WriteLock` / `acquire_write_lock` / `read_holder` | `quantstudio/pipeline/snapshot_lock.py:172,739,272` | 写锁持有与持有者诊断 |
| `detect_schema_status` | `quantstudio/pipeline/qfq_schema_status.py:138` | 主库 schema 实测（只读 introspection） |
| `resolve_runtime_aux_path` | `quantstudio/pipeline/qfq_aux_router.py:178` | 世代路由一致性断言 |
| `DATA_ROOT` / `db_path` | `quantstudio/_paths.py:50,53` | 数据根唯一真相源 |
| `questdb_table_map.json`（文件读） | `config/profiles/mcp_only/questdb_table_map.json` | `watermark_basis` 逐表基准列 |

**禁止 import（机检项）**：`quantstudio.pipeline.writers`、`quantstudio.pipeline.daemon`、任何 `qfq_*` 的**写入**函数、`quantstudio.backtest.*`。测试 `test_delivery_pure_gain.py` 用静态 AST 扫描 `quantstudio/delivery/**` 的 import 集，命中禁止项即 FAIL（这是一条「结构性」纯增益证据，与 SC-013 互补）。

### 1.2 与既有模块的边界（七点⑦）

| 既有模块 | 边界规则 | 机检手段 |
|---|---|---|
| `writers.py` / upsert 路径 / QFQ 引擎 | **零改动**；`delivery` 不 import 它们；不调用任何写入 API | SC-013 基线相对 diff + AST import 扫描 |
| `config/` 既有配置 | 只读（`questdb_table_map.json` / `qfq_aux_paths.json` / `collector_tasks.json` / `data_config.json` / `quality_baseline.json`）；**新配置只写 `config/delivery_config.json`** | 测试断言写入调用集合 ⊆ {`delivery_config.json`, `import_journal.json`, `backup_import_*`, 导出目录}（用 monkeypatch 包 `open`/`os.replace` 记录落点） |
| `daemon` / 计划任务 | 不启不停；只做**存活检测**（`daemon_status.json` + PID/create_time 双验） | 代码评审 + 测试断言无 `subprocess` 调用 daemon |
| `batch_audit.db` / `quarantine.db` / `task_resume/` / 日志 | 不读写；不在白名单、不在排除动作范围 | 白名单常量测试（`WHITELIST` 恰为 3 项，`EXCLUDE_PATTERNS` 覆盖 FR-002 全项） |
| `snapshot_lock` | 只**取/放**锁，不改锁协议 | 契约测试：取锁失败必须抛 `WriteLockHeld` 且 UI 明示持有者 |

### 1.3 单一权威定义处（避免语义分叉）

- 白名单 / 排除项 / manifest 版本 / 状态枚举 / 退出码 → 只在 `quantstudio/delivery/constants.py` 定义一次；
- 保护路径清单（SC-013）→ 只在 `scripts/delivery_pure_gain_audit.py` 定义一次；
- 「进水位体系表集」规则 → 只在 `stats.py` 实现一次，导出侧与复验侧共用（FR-018 明确要求同口径）。

---

## §2 manifest.json 完整 schema（七点②）

字段级定义、类型、必填性、校验规则、以及「缺字段 → 哪一类失败」的映射，**全文在 [data-model.md](./data-model.md) §2**；此处只给结构与三条不变式。

```jsonc
{
  "manifest_schema_version": 1,                  // 独立于主库 schema 状态，二者禁止同名混用（FR-006）
  "generated_at": "2026-10-31T03:20:11+08:00",   // 导出完成时刻（本地时区带偏移）
  "producer": { "app": "QuantStudio", "version": "…", "python": "3.11.x",
                "duckdb": "1.5.6", "sqlite": "3.x", "platform": "Windows-10-…" },
  "files": [ { "path": "quantstudio.db",          // 相对包根，UTF-8，禁绝对路径（FR-006）
               "kind": "duckdb", "bytes": 62306660352, "sha256": "…",
               "sqlite_header": null } ],          // SQLite 件写入头 16B 文本，作为"有效库"旁证
  "main_db": { "path": "quantstudio.db", "schema_status": "complete_2_1" },
  "qfq": { "released": false,                     // 客户配置口径下的 released（导出机视角，供比对）
           "qfq_active_cutover": { "cutover_id": "…", "source_generation": "…",
                                   "price_source": "mcp" },
           "aux_route_step6_expect": { "legacy": "qfq_aux.db", "gen1": "qfq_aux_mcp_gen1.db" },
           "aux_paths_config_sha256": "…" },
  "table_set": { "rule": "(应推进水位的表 ∪ questdb_table_map 覆盖的表) ∩ 库内实际存在",
                 "count": 64, "empty_count": 0, "unknown_basis": [] },
  "tables": [ { "table": "stock_daily", "source": "questdb", "freq": "daily",
                "date_basis_col": "trade_date",    // 本次实际所用列（自证口径）
                "basis_source": "questdb_table_map.json",
                "row_count": 12345678, "row_count_method": "exact",
                "max_date": "2026-10-30",
                "watermark_last_date": "2026-10-30", "state": "OK" } ],
  "self_check": { "red_line": { "outside_whitelist": 0, "env_hits": 0,
                                "shm_hits": 0, "log_hits": 0, "zero_leak": true },
                  "db_open": [ { "path": "quantstudio.db", "select1": "ok" } ],
                  "wal_zero": [ { "path": "quantstudio.db.wal", "present": false, "bytes": 0 } ] },
  "row_count_degraded": false,                    // ②审 A3 强化①：整包一眼可见是否含估算值
  "row_count_degraded_tables": [],                // 用了 estimate 的表名清单；无降级为空数组
  "export": { "lock_task_id": "delivery_export", "lock_acquired_at": "…",
              "lock_released_at": "…", "duration_sec": 412.5,
              "checkpoint": { "duckdb": "ok",
                              "sqlite": [ { "path": "qfq_aux.db", "busy": 0, "log": 0 } ] },
              "copy_bytes_total": 73914680448, "copy_sha256_pass": true }
}
```

**三条不变式**
1. **manifest 最后写**：复制与统计全部完成后才落 `manifest.json`（先写 `manifest.json.tmp<pid>` 再 `os.replace`）。因此「目录内无 manifest」= 包不完整，校验端第一类失败即可发现，不需要额外的「完成标记」。
2. **路径一律相对**：`files[].path` / `main_db.path` 相对包根；校验端做 `PurePosixPath` 归一化并拒绝 `..`、盘符、UNC、绝对路径（`test_delivery_verify::test_reject_absolute_path`）。
3. **自证可复算**：`files[].sha256` 必须与包内实际文件逐字节一致；`tables[].row_count/max_date` 取自**产出副本**（而非源库），故 manifest 描述的是「客户将拿到的这个文件」，见 research.md R4。

---

## §3 导出状态机（六步，七点③）

内核函数 `export_bundle()` 的相位机（`quantstudio/delivery/export_kernel.py`）。**Phase 名进日志与进度回调，也被测试用作断言点。**

| # | Phase | 动作 | 失败语义 |
|---|---|---|---|
| 0 | `PRECHECK` | ①`detect_schema_status(main_db)` ∈ {`COMPLETE_2_1`,`COMPLETE_2_2`}；②三件套均可只读打开（DuckDB `SELECT 1` / SQLite `PRAGMA quick_check(1)`）；③`read_holder()` 为空；④daemon 未运行（status 文件 + PID/create_time 双验）；⑤`export_dir`/`DATA_ROOT` 互不包含（FR-035）；⑥磁盘空间 ≥ 三件套 × 1.1 + manifest 余量；⑦Windows 探针甲（目标目录 create+delete 临时文件）、探针乙（源文件 `msvcrt.locking(LK_NBLCK)` 试探）；⑧同步盘特征检测（命中 ⇒ 需显式确认，不阻断） | **零产出**，抛 `ExportRefused(code)`；`EMPTY_OR_NEW` 亦拒绝（无空包路径） |
| 1 | `ACQUIRE` | `WriteLock(task_id="delivery_export")`；写入 `lock_acquired_at` | `WriteLockHeld` ⇒ 拒绝并打印持有者诊断（禁止静默） |
| 2 | `CHECKPOINT` | DuckDB：以读写连接执行 `CHECKPOINT` 后**立即关闭连接**；SQLite（两代因子库）：`PRAGMA wal_checkpoint(TRUNCATE)`，记录 `(busy, log, checkpointed)` | `busy != 0` ⇒ fail-closed（有并发读者/写者未退） |
| 3 | `WAL_ZERO` | 断言三件套的 `-wal` 均**不存在或 0 字节**；把探测结果写 `self_check.wal_zero` | 任一非零/不可判定 ⇒ fail-closed，报实测字节数（**不得**把非空 WAL 装入包） |
| 4 | `COPY` | 新建 `<export_dir>/<YYYYMMDD_HHMMSS>/`；逐文件按 **1 MiB 块** `shutil.copyfileobj` 式复制并**同时**流式 sha256（单次读盘），字节级进度 + 可取消；每完成一个文件记 `(bytes, sha256)` | 磁盘耗尽/句柄失败 ⇒ 中止并**清理本会话新建的时间戳子目录**（旧包永不触碰） |
| 5 | `ATTEST` | 对**产出副本**做只读统计（§5）与自检：白名单外文件数、`*.env`、`-shm`、日志类命中数；三件套逐一 `SELECT 1`；哈希与复制时一致；写 `manifest.json`（最后写） | 自检任一红 ⇒ 标记产出不合格并**告警但保留**（供取证），退出码非 0（与 FR-007「标记不合格并告警」一致） |

**finally 块**：无论成败 `WriteLock.release()`；释放失败 → 记 `lock_released_at` 缺失 + 告警（不静默）。

**关键语义（易被做错的四点）**
- 复制与统计**都在锁内**：若统计在锁外做，复制完成→统计之间发生写入，manifest 描述的就不是交付件内容（违反 FR-004 的"同刻"）。
- 复制采用**单次读盘 + 流式哈希**：不二次读 68.9 GB 复算（research.md R3 给出取舍与残余风险：哈希描述的是"我写出的字节"，落盘完整性由随后的只读打开 + `SELECT 1` 兜底）。
- 取消（FR-019）在 Phase 1–4 语义不同：Phase 1–2 取消 = 干净退出（无产出）；Phase 4 取消 = 清理时间戳子目录后退出（**不留下半包**）。
- **复制中断 ⇒ 本次产出整体作废（②审补充）**：IO 异常、磁盘耗尽、用户取消三种情形一律**删除本会话新建的时间戳子目录**，不保留"部分文件 + 无 manifest"的目录——半包比不出包更危险（客户会以为拿到了完整的）。红线自检不过属另一类：**保留产物供取证**并置 `qualified=False`（FR-007 要求"标记不合格并告警"，不是删除）。

---

## §4 导入状态机（九步 + 三模式 + journal，七点③）

### 4.1 九步（FR-013 严格顺序）

| # | Phase | 动作 | 失败语义 |
|---|---|---|---|
| 1 | `LOAD_CONFIG` | 读 `delivery_config.json`（缺失/空值 = 未选择，**不报错**）；解析 `DATA_ROOT`（只读显示，FR-012）；三路径互不包含校验（FR-035） | 配置不匹配 ⇒ 第③类失败，零改动 |
| 2 | `PRECHECK` | daemon 未运行；`read_holder()` 为空；`DATA_ROOT` 可写（create+delete）；磁盘空间满足 FR-015 门槛（同卷 1.1× 包体积；跨卷 备份+1.1×）；**与 FR-003 同款两项 Windows 探针**；`import_dir` 内恰有一个包（FR-038） | 零改动终止；空间不足必须在**备份前**拒绝并报所需/可用 |
| 3 | `VERIFY_READONLY` | FR-010 四要素：①manifest 存在 + 逐文件 SHA256（**逐文件 fail-fast、小文件优先**）②主库 schema 实测 vs 程序期望 vs manifest 记录 ③世代与路径一致性断言（§6）④白名单完整性 | 按**四类原因**分派（§4.4）；`DATA_ROOT` 下零文件修改 |
| 4 | `TERMINATE_IF_FAILED` | 失败即返回；本相位**不落任何文件**（含日志与 journal） | — |
| 5 | `ACQUIRE_AND_JOURNAL` | `WriteLock(task_id="delivery_import")`；写 `data/import_journal.json`（相位 `lock_acquired`，含模式、包 id、待替换清单、临时名/正式名） | 取锁失败 ⇒ 提示「请先停止采集」并终止 |
| 6 | `BACKUP` | 按 FR-015：同卷 `os.replace` 移入 `DATA_ROOT/backup_import_<ts>/`（O(1)、零额外空间）；跨卷退回复制；写 `RESTORE_README.txt`（FR-039）；备份回验：源文件**导入前可读** ⇒ 回验失败=真失败并回滚；**导入前不可读（修复模式）** ⇒ 降级为取证保留（原件 rename 入备份、记取证摘要、继续） | 真失败 ⇒ 回滚到 `backup_done` 前状态（无替换发生，等价于零改动） |
| 7 | `OVERWRITE` | 全部临时文件先落 `*.incoming`（目标目录内）→ **逐个** `os.replace` 到正式名，每完成一个更新 journal（`files[i].state=done` + `updated_at`） | 任一失败 ⇒ 按 journal **反向回滚**已替换项；禁止留「主库新、因子库旧」混合态 |
| 8 | `REVERIFY` | 再跑 FR-010 四要素 + 逐表六标签（§5）+ 基线过时判定（FR-033，只提示不改写） | 复验失败 ⇒ 按 journal 回滚（视为第⑦相位失败） |
| 9 | `RELEASE_AND_PASS` | 释放锁；删 journal；输出结果对象（模式/备份路径与体积/逐文件结果/复验表/取证摘要/警告） | 释放失败 ⇒ 告警但结果仍返回（journal 已删） |

### 4.2 三模式 + 恢复优先级（FR-014）

```
detect_pending_journal() 非空 ──→ RECOVERY（二选一：继续完成 / 回滚到导入前）
        │（不按文件存在性重推模式 —— T6 的唯一解）
        ↓ 无残留 journal
DATA_ROOT/quantstudio.db 不存在 ─────────────→ INSTALL（跳过备份，明示"无文件可备"）
存在且可只读打开 ───────────────────────────→ UPDATE（备份 → 覆盖）
存在但无法只读打开（0 字节/打不开/UNKNOWN）──→ REPAIR（取证备份 → 覆盖）
```

- **恢复流程必须在 Phase 1 之前**：GUI 启动或进入导入入口即检测（FR-036），展示「已完成 N/M 项」。
- **INSTALL 的回滚语义**：无备份可还原，故 journal 额外记 `created_files`（导入前不存在的目标文件）；回滚 = 删除本次创建的这些文件（**只删自己创建的**，不碰白名单外与既有文件）。

### 4.3 journal 相位机（`quantstudio/delivery/journal.py`）

相位枚举（单调前进，回滚时切到 `rolling_back`）：
```
lock_acquired → backup_started → backup_done → overwrite_started → overwrite_done
             → reverify_started → done            （成功路径删 journal）
rolling_back → rolled_back                        （回滚路径保留 journal 供审计，终态删除）
```
恢复决策表（机检项，逐行有测试）：

| journal.phase | 「继续」语义 | 「回滚」语义 |
|---|---|---|
| `lock_acquired` / `backup_started` | 重跑备份（尚未替换，安全） | 无需还原，直接清 journal |
| `backup_done` / `overwrite_started` | 从第一个 `state=pending` 项继续替换 | 把 `state=done` 项按 `backup_name` 还原；`created_files` 删除 |
| `overwrite_done` / `reverify_started` | 只做复验并收尾 | 全量还原 |

**原子写**：journal 一律写 `import_journal.json.tmp<pid>` + `os.replace`（与 `backtest_window._atomic_write_json` 同构）；写失败即中止并回滚（journal 不可信时不得继续替换）。

### 4.4 四类失败分派（FR-011，含 v4 新增第四类）

| 类 | 触发 | 用户可见话术 | 退出码 |
|---|---|---|---|
| ① 包问题 | manifest 缺失/字段缺失/SHA256 不符/白名单文件缺失/路径不合法 | 「交付包不完整或已损坏，请重新获取」 | 4 |
| ② 程序版本不匹配 | 主库 schema 实测与**程序期望**不一致（包的 schema 高于或低于程序） | 「程序版本与数据版本不匹配，请更新程序」 | 6 |
| ③ 配置不匹配 | 路由解析出的运行时因子库路径 ≠ 导入目标；`DATA_ROOT` 非有效数据目录；`released` 指向的库不在包内 | 「配置不匹配，请检查」+ 两路径对照 | 5 |
| ④ 包格式代差 | `manifest_schema_version > MANIFEST_SCHEMA_VERSION_SUPPORTED` | 「包由更新版本的程序生成，请先更新程序」并终止（引导归②） | 6 |

**约束**：④ MUST NOT 抛异常崩溃、MUST NOT 按旧格式猜测解析、MUST NOT 忽略未知字段继续执行（同版本内的**额外字段**允许忽略但必须记 warning，二者区别由测试锁定）。

---

## §5 `date_basis_col` 解析算法与表集边界（七点④ + Q2 裁定落点）

### 5.1 表集（按审核方 Q2 裁定：**①∩库内实际存在** 为主集）

```python
main = {t.name for t in duckdb_tables(main_db)}          # introspection，只读
mp   = load_json("config/profiles/mcp_only/questdb_table_map.json")["tables"]   # 64 张
M    = [t for t in mp if t in main]                      # 主集（顺序按 mp 声明序，稳定可复现）
```
- 主集成员**逐表**输出状态（含 `MISSING`）——这是 Q2 裁定的核心：以 `source_watermark` 行集为边界会**在定义上消灭 MISSING**，而复验报告最需要人看见的恰是它（预告「点增量会退化为全历史重拉」）。
- `source_watermark` 行是**每表状态的数据来源**，不参与集合边界。
- `collector_tasks.json`（实测 88 个任务）只做**交叉核对**：`UNKNOWN-SOURCE` 判定要看「map 有表 + 有水印行 + 但客户配置无对应任务/凭据」。
- 主集之外、但在 `source_watermark` 有行的表 → 记入 `table_set.out_of_map: [...]`（警示配置漂移），不进 `tables[]`。

> **任务数差异注明（②审补充）**：`collector_tasks.json` 实测 **88** 个任务，与其 `_note` 声明的 86 差 2；疑为计数口径差异（`_note` 可能排除 disabled 大表与 hidden 任务这两类）。**以实测 88 为准**；W0 实测时若查明具体是哪两个任务，在本节追加一行名单（任务 T006 的副产物）。该差异只影响 `UNKNOWN-SOURCE` 判定时「客户是否有对应采集任务」的宽严，**不影响表集边界**（边界只由 map ∩ 库内实际表决定）。

### 5.2 逐表算法（`stats.py`，导出与复验共用同一实现）

```
basis   = mp[t].watermark_basis                       # 逐表真源，不在数据库内
if basis missing: date_basis_col="unknown"; 不输出 max_date; 状态 = UNKNOWN-BASIS
else:             date_basis_col=basis
row_count = SELECT count(*) FROM t                    # row_count_method="exact"（默认）
max_date  = SELECT max(basis) FROM t → 归一化到本地日期（date 粒度）
wm_date   = source_watermark(source=mp[t].source, table_name=mp[t].target_table, freq=mp[t].freq).last_date
            # 库内为 epoch 毫秒 → 本地日期
```

**状态判定（顺序即优先级，避免歧义）**
| 序 | 条件 | 标签 | 是否缺陷 |
|---|---|---|---|
| 1 | `row_count == 0` 且无 `wm` 行 | `EMPTY` | 否（Q2 裁定：空表不算 MISSING） |
| 2 | `row_count > 0` 且无 `wm` 行 | `MISSING` | **是**（增量会退化为全历史重拉） |
| 3 | `basis` 缺失 | `UNKNOWN-BASIS` | 否（不猜，标 `date_basis_col:"unknown"`） |
| 4 | 有 `wm`，但客户配置无对应采集任务/凭据 | `UNKNOWN-SOURCE` | 否（信息性，FR-018/C-4） |
| 5 | `Δ = (max_date − wm_date).days`，`Δ == 0` | `OK` | 否 |
| 6 | `Δ > 0`（水位落后数据） | `BEHIND`（N=Δ 天） | 是（下次采集会重拉这 N 天） |
| 7 | `Δ < 0`（水位领先数据） | `AHEAD`（N=|Δ| 天） | 是（数据被截断或水位虚高） |

> **口径说明（②审 A2 裁定后更新）**：`EMPTY` 与 `UNKNOWN-BASIS` 已由审核方**正式纳入** spec FR-018，定性为**排除性标签**（"不参与四态判定，仅为可显示的事实"）。因此它们不是第六/第七态：结论性判断只看四态（`OK`/`BEHIND`/`AHEAD`/`MISSING`）+ `UNKNOWN-SOURCE`；两个排除性标签只解释"这行为什么没有结论"。

### 5.3 窗口时长控制（不牺牲诊断力）

- 统计对象是**产出副本**（锁内、只读连接），逐表 `count(*)` + `max(basis)`；
- 默认 `row_count_method="exact"`；仅在窗口实测「主集逐表总计 > 8 分钟」时，启用 `--row-count=estimate`（写 `duckdb_tables().estimated_size`，manifest 记录 `row_count_method="estimate"` 并在结果页标注），**该降级必须当场留读数与理由**；
- **降级的两条强化（②审 A3 裁定）**：①manifest 顶层同时记 `row_count_degraded: true` 与 `row_count_degraded_tables: [...]`（只写 `tables[]` 里逐表的 method 不够——消费者要能一眼看出"这一包不是全量精确统计"）；②导入侧复验读到 `row_count_method=="estimate"` 时，报告必须给该表附「该表行数为估算值」，**禁止与 exact 行静默混排**（否则客户会把估算值当实测值用于判断数据完整性）。
- 逐表统计进度上报（`stats: 37/64`），不静默卡住。

---

## §6 世代路由一致性断言（七点⑤）

复用 `resolve_runtime_aux_path`（`qfq_aux_router.py:178`），**不重写路由逻辑**。断言链（`verify.py::assert_generation_consistency`）：

```
1) 读客户配置 config/profiles/mcp_only/qfq_aux_paths.json（缺失/解析异常 ⇒ 走 fail-secure 分支，见 3）
2) duckdb_read 回调 = 对**包内主库副本**做只读查询（未来态），price_source 取 manifest.qfq.qfq_active_cutover.price_source
   （若 manifest 无该字段 ⇒ 回退 price_source="mcp"，并记 warning）
3) P, reason = resolve_runtime_aux_path(main_db=DATA_ROOT/"quantstudio.db",
                                       duckdb_read=<包内副本只读回调>,
                                       price_source=..., config_path=客户配置)
4) 断言 P 的 basename ∈ 包内交付的因子库清单，且 P.parent 解析后 == DATA_ROOT
   ├─ 成立 ⇒ 通过（reason 原样入报告，如 gen1:b6_formal_20260807_v2）
   └─ 不成立 ⇒ 第③类失败，输出两路径对照（运行时路径 P / 导入目标路径）
5) fail-secure 分支的显式输出（FR-010③ 硬要求）：
   reason 以 legacy: 开头且内容为 released=false / no_active_cutover / fail-secure:* ⇒
   报告必须写明「配置缺失或未释放，已按 legacy 判定；包内 qfq_aux.db 必须校验通过；如需 gen1 请修复 config」
```

**为什么用包内副本做主库参数来源**：客户导入后**实际会读到的是包内主库**（`qfq_active_cutover` 也在包内），故路由必须按「导入后的世界」解析；用客户当前（旧）主库解析会得出导入前的路由结论——这正是 T8 的深层形态。
**断言的可机检性**：`test_delivery_verify::test_route_mismatch_*` 用两个 fixture 配置（`released=false` / `released=true` + cutover 记录）分别断言 reason 前缀与失败分类；`test_route_config_missing` 断言 legacy fail-secure 文案。

---

## §7 Windows 环境风险与检测动作（补充 C：每项配检测动作）

| # | 风险 | 检测动作（可执行） | 命中后行为 |
|---|---|---|---|
| W-1 | 杀软实时扫描 / Explorer 预览窗格 / 同步客户端持句柄 | 甲：目标目录 create+delete 临时文件；乙：对每份源文件 `os.open(O_RDWR)` 后 `msvcrt.locking(fd, LK_NBLCK, 1)`（非阻塞字节锁），随即解锁关闭 | 报「文件被其他程序占用（杀软/预览/同步）」并终止（FR-003①） |
| W-2 | 目标在 OneDrive/Dropbox/网盘（同步风暴 + 句柄钩住） | 路径特征（`OneDrive`/`Dropbox`/`坚果云`/`百度网盘`/`Nutstore` 等）+ 环境变量 `OneDrive`/`OneDriveCommercial` 前缀比对 + 目录 reparse 属性 | **不阻断**，醒目警示并要求显式确认（CLI `--accept-sync-dir`；GUI 二次确认框） |
| W-3 | 路径长度 > MAX_PATH（260） | 计算「导出根 + 时间戳子目录 + 文件名」「DATA_ROOT + backup_import_ts + 文件名」的 `len()`，> 255 即警示 | 警示并建议缩短；内部复制用 `\\?\` 前缀（`os.path.abspath` + 前缀），但 manifest 仍写相对路径 |
| W-4 | 中文路径与编码（本机 `D:\miniQMT策略实盘\…`） | manifest 一律 UTF-8 + 相对路径 + `ensure_ascii=False` + `newline="\n"`；CLI 输出检测 `sys.stdout.encoding`，非 UTF-8 且非 cp936 时降级为 ASCII 安全串 | 禁止绝对路径入 manifest（测试锁定）；控制台不输出 emoji（沿用 `snapshot_lock` 的既有约定） |
| W-5 | `os.replace` 遇目标被打开（WinError 32） | 捕获 `PermissionError/OSError` 并区分 `winerror==32/5`，逐个文件记入 journal | 触发回滚（Phase 7 语义），错误原文进结果页 |
| W-6 | 跨文件无原子性 | 结构性规避：staged `*.incoming` + 逐项 `os.replace` + journal；**不承诺**任何跨文件原子 | 崩溃后由 recovery 二选一（SC-014） |
| W-7 | `DATA_ROOT` 是 junction/符号链接 ⇒ 同卷判定错 | `os.stat().st_dev` 比对备份目录与源文件；同时 `Path.resolve()` 记录真实根 | `st_dev` 不同 ⇒ 走跨卷（复制）路径并明示额外占用 |
| W-8 | 磁盘耗尽（包 68.9 GB，D: 剩余 147.1 GB） | 备份前 `shutil.disk_usage` 实时读数，门槛见 FR-015；复制期间每 256 MiB 复查剩余 | 门槛不满足 ⇒ 备份前拒绝并报所需/可用（SC-011 末项）；运行中耗尽 ⇒ 回滚（SC-017） |
| W-9 | 长任务被睡眠/休眠打断 | 记 `duration_sec` 与逐相位时间戳；导入期间结果页提示「请勿休眠/关机」 | 中断 ⇒ journal 恢复（不静默续跑） |
| W-10 | DuckDB 保留 0 字节 `-wal` 导致误判 | 判据取 `size > 0` 才算未归零（**存在但 0 字节视为已归零**），并把 `present/bytes` 双双写入 manifest | 见 Phase 3 |
| W-11 | 杀软拖慢大复制 ⇒ 看起来"卡死" | 字节级进度 + 「无进展 > 120 s」告警（不自动中止，只提示） | 用户可取消（Phase 4 取消语义） |
| W-12 | 文件名大小写/保留名（`CON`、尾随点） | 白名单是固定 3 个既有文件名，不引入用户命名；备份/临时名只 append 后缀 | 结构性规避，测试断言临时名后缀合法 |

---

## §8 manifest 版本化与前向兼容（补充 D：实现与测试方式）

**实现**
- `MANIFEST_SCHEMA_VERSION = 1`（`constants.py` 单一权威）；`MANIFEST_SCHEMA_VERSION_SUPPORTED = {1}`。
- 读侧三步（顺序固定）：①解析 JSON（失败 ⇒ 第一类，注明"manifest 不是合法 JSON"）②读 `manifest_schema_version`（缺失 ⇒ 第一类；非 int ⇒ 第一类）③版本裁决：`> max(supported)` ⇒ **第四类**（提示更新程序、终止、不解析其余字段）；`< min(supported)` ⇒ 第二类（程序比包新，引导同②）；`== 1` ⇒ 进入字段级严格校验。
- 字段级校验：`manifest-v1.schema.json`（JSON Schema，落在 `contracts/`）+ 手写必填/类型检查（不引入 `jsonschema` 依赖，避免新增第三方依赖）；**同版本内的未知字段**：忽略 + 记 `unknown_fields:[...]` warning（前向兼容：老程序读新包的新增字段不应崩，但也不得据此改变判断）。
- 写侧：`json.dump(..., ensure_ascii=False, sort_keys=True, separators=(",",":"))` + 尾部换行 → 规范化输出使「同一输入两次导出」除时间戳外逐字节可比（SC-009 的基础）。

**测试（`tests/test_delivery_verify.py`）**
| 用例 | 构造 | 期望 |
|---|---|---|
| `test_manifest_version_too_new` | 把包内 manifest 的版本改成 2 | 第四类文案 + 退出码 6 + `DATA_ROOT` 零改动 + **无异常栈** |
| `test_manifest_version_missing` | 删除版本字段 | 第一类（包问题），不是崩溃 |
| `test_manifest_version_mismatch_type` | 版本改为 `"1"` 字符串 | 第一类，且提示可读 |
| `test_manifest_version_old` | 版本改为 0 | 第二类（程序较新） |
| `test_manifest_unknown_field_tolerated` | v1 内加一个未知字段 | 通过 + warning 记录未知字段名 |
| `test_manifest_roundtrip_canonical` | 同一份输入构建两次 | 除 `generated_at`/`export.*` 时间字段外逐字节一致 |

---

## 测试策略（七点⑥）

### 6.1 分层

| 层 | 内容 | 运行条件 | 覆盖 |
|---|---|---|---|
| L1 单元 | manifest 编解码/校验、表集裁剪、状态判定、journal 相位机、退出码映射、白名单常量 | 无 IO 或 `tmp_path` | FR-001/002/006/011/014/036 的规则面 |
| L2 集成（小库 fixture） | 导出→校验→导入→复验 全链路；三模式；回滚；ENOSPC；幂等；双端对等 | `tmp_path` 内造库，**秒级**，无需 daemon 窗口 | 绝大多数 SC |
| L3 窗口（生产库，只读导出） | SC-001/002/003/009(生产侧)/011/012 的真实读数 | **需停 daemon + flag 窗口**；**只允许导出路径** | 见 §窗口章硬边界 |
| L4 传导（不跑库） | SC-008 的传导论证（字节一致 ⇒ 结构/契约一致）+ SC-013 基线审计 | 随时 | 纯增益 |

**fixture 构造器（`tests/fixtures/delivery/`）**
- `build_main_db(path, *, schema="complete_2_1"|"partial_or_mixed"|"empty"|"zero_bytes", tables=N)`：用**项目自身**的 DDL/写入路径初始化（保证指纹状态可被 `detect_schema_status` 识别），或造 `partial` 状态（缺一列）。
- `build_aux_db(path, rows=…)`：`CREATE TABLE` + 少量行 + `PRAGMA journal_mode=WAL`。
- `make_package(tmp, *, main=…, aux=…, gen1=…, corrupt=[...], manifest_overrides={...})`：走**真实导出内核**造包（保证 schema 同源），再按需注入损坏（改 SHA、删文件、改版本、改路径为绝对路径）。
- `make_client_data_root(tmp, mode)`：`update`/`repair`(0 字节)/`install`(空，含 `snapshots/`、`.gitkeep`、`README.md`)。

### 6.2 每个 SC 的可执行手段（不许「无法执行」或「必然通过」）

| SC | 手段 | 层 | 备注 |
|---|---|---|---|
| SC-001 | 五个场景各一个测试，断言**导出目录不存在新增子目录**（`list(export_dir)` 前后一致）+ 拒绝码正确 | L2（+L3 复测 1 次） | 五场景：schema 不合格 / 有锁 / daemon 在跑 / 空间不足 / WAL 未归零（用 fixture 造非零 `-wal`） |
| SC-002 | 产出目录全量枚举，正则命中 `*.env`/`.log`/`-shm`/`staging`/`backup`；断言白名单外文件数==0 | L2 + L3 | 计数写 evidence |
| SC-003 | 同一次只读会话内**连续打开三件**并执行关键查询（主库 `qfq_active_cutover`/`source_watermark` + 因子库 `SELECT count(*)`）；**不采 mtime 作证** | L2 + L3 | 断言三次打开均成功且查询有结果结构 |
| SC-004 | 更新模式全链路跑通，断言复验表存在且结论「可增量」 | L2 | — |
| SC-005 | 三种坏包各跑一次；前后对 `DATA_ROOT` 做指纹（路径+size+mtime_ns+sha256+目录清单哈希）逐位比对 | L2 | 「逐位一致」= 六元组全等 |
| SC-006 | monkeypatch `os.replace` 在第 2 次替换时抛异常 → 断言回滚后主库可只读打开、`schema_status` 与导入前一致、抽样行数一致 | L2 | 程序内失败 |
| SC-007 | 干净 `data/`（三个既有文件存在）安装模式；断言三文件仍在；随后用 fixture 触发采集侧建库逻辑（`quarantine.BatchAudit` 构造）断言自建 | L2 | 不启动真 daemon |
| SC-008 | (a) 传导论证文档 + SC-013 全绿；(b) 小库 fixture 上跑一次短回测（A/B：导入前后），断言黄金结果逐位一致（不跨世代） | L4 + L2 | **生产库不做导入**（硬边界）；fixture 的子集构造属测试专用（交付包仍禁 CTAS，见 research R9） |
| SC-009 | offscreen（`QT_QPA_PLATFORM=offscreen`）下双壳各跑一次 → 比对文件清单 + 逐文件 SHA256（manifest 时间字段除外）+ 校验结论；生产库窗口内复测（只读导出） | L2 + L3 | 前提不满足（有写入）时**标注不适用**，不硬凑 |
| SC-010 | 删配置/空字段/不存在路径三种情形构造 `DeliveryTab`，断言按钮 `isEnabled()==False` 且提示文案含「请先选择」 | L2 | offscreen |
| SC-011 | 同卷：`os.stat().st_ino` 前后比对（非零且一致）→ 否则 size+mtime_ns 一致且源路径消失 → 外加耗时 < 5 s 与 `disk_usage` 无显著下降 | L2 | 三选一证据链，读数入 evidence |
| SC-012 | 界面体积字符串来自 `os.stat().st_size` 格式化；另加**回扫测试**：`grep` 交付模块与文档中的 `\d+(\.\d+)?\s*GB` 常量，命中即 FAIL（背景数字允许只在 spec 背景章） | L2 | DB-2026-0003 的教训（判据会静默失准） |
| SC-013 | `scripts/delivery_pure_gain_audit.py --mode baseline|audit`：保护路径逐文件 diff 哈希 + 未跟踪文件扫描；`pytest` 全量零新增失败 | L4 | 基线相对口径（见 §纯增益） |
| SC-014 | 子进程真强杀：`QS_DELIVERY_TEST_HOOKS=1` + `QS_DELIVERY_TEST_KILL_AFTER_REPLACES=1` ⇒ 替换 1 项后 `os._exit(9)`；父进程再跑 `detect_pending_journal` → 断言二选一；选「回滚」后逐位还原；选「继续」后完成并复验通过；**断言未发生二次备份**（备份目录数仍为 1） | L2 | 钩子默认惰性（有测试断言未设 env 时完全无行为） |
| SC-015 | 0 字节主库 → 修复模式；断言不因回验失败终止、结果页含「原库已损坏」+ 取证摘要四字段 | L2 | — |
| SC-016 | 同包导入两次；断言两次复验表逐字段相等 | L2 | — |
| SC-017 | monkeypatch 写盘函数在累计 N 字节后抛 `OSError(errno.ENOSPC)`；断言回滚到备份状态 + journal 终态正确（清理或保留可恢复） | L2 | 真 ENOSPC（VHD）列为**人工可选**，不作为自动判据（诚实标注） |

### 6.3 崩溃注入与 ENOSPC 的诚实边界

- 崩溃注入用**受控测试钩子**（env 双重开关 + 单测断言惰性），不用 `taskkill` 打随机 PID（避免铁律 5 的误杀面）；
- ENOSPC 自动测试用**确定性注入**（改写在阈值处抛 `errno.ENOSPC`），因为真实磁盘填满在 CI/本机会污染机器；**真 ENOSPC 只做一次人工演练**并在 evidence 中标注「人工，非自动」。

---

## 实施波次与门控（补充 A）

### 波次定义与 DoD

| 波 | 范围 | 出口 DoD（缺一即停点） |
|---|---|---|
| **W0 Setup** | 基线快照（SC-013）、`tests/fixtures/delivery/` 构造器、`constants.py`、`scripts/delivery_pure_gain_audit.py` + `delivery_wave_gate.py`、契约测试骨架（先失败） | ①基线 json 落 `docs/evidence/`；②构造器能造出 `detect_schema_status` 可识别的四种 fixture 状态；③审计脚本自测通过（一致/不一致两情形）；④**零源码改动**（只加测试与脚本） |
| **W1 内核导出** | `manifest.py` / `probe.py` / `stats.py` / `export_kernel.py`（六步）+ 导出侧测试 | ①导出测试全绿；②SC-001/002/003/012 读数落 evidence（L2）；③SC-013 基线审计一致；④红线自检红/绿两向用例都在 |
| **W2 内核导入校验（纯只读）** | `verify.py` / `config.py` / 只读部分 + 校验测试 | ①校验测试全绿（含四类失败与版本代差）；②**零改动**证明：SC-005 三种坏包前后 `DATA_ROOT` 指纹逐位一致；③SC-013 一致 |
| **W3 导入变更区** | `journal.py` / `import_kernel.py`（备份/覆盖/回滚/复验）+ 导入测试 | ①SC-004/006/007/011/014/015/016/017 读数落 evidence；②journal 相位机测试全绿（含「禁重推模式」）；③SC-013 一致 |
| **W4 壳与双端对等** | `cli.py` / `delivery_tab.py` / `gui/workers.py` / `main_window.py` / `pyproject.toml` / `.gitignore` / `data/README.md` + 对等与文档测试 | ①SC-009/SC-010 读数（L2）+ 窗口内 L3 复测；②CLI 退出码表逐项测试；③`data/README.md` 文案已改（FR-032）；④全量 pytest 零新增失败；⑤SC-013 一致 + 最终收口审计；⑥**CLI smoke：`qs-deliver --help` 退出码 0**（D-3 裁定——入口失灵是可观察行为，diff 审计看不见它） |
| **Polish** | 结果页文案、进度与取消体验、evidence 汇总、R4 沉淀条目 | 文档与证据链完整，无待办遗漏 |

### 门控的「非形式主义」做法

1. **机检门**：`scripts/delivery_wave_gate.py --wave N` 解析 `docs/evidence/delivery-waveN-*.md`，要求存在且含固定字段：`测试: passed=<n> failed=<n>`、`SC-013: <一致|不一致+来源>`、`SC-xxx: <读数>`（该波声明覆盖的 SC 列表在脚本内硬编码，缺项即 FAIL 退出码非 0）。
2. **波次开工前置**：下一波第一个任务必须是「跑 `delivery_wave_gate.py --wave <N-1>` 并贴输出」；不通过则本波不得开工（tasks.md 里以依赖关系表达）。
3. **红线即停**：任一波出现①测试红②SC-013 不一致且未申报③发现需改受保护路径 ⇒ **立即停并上报**，不得"先往前推再回来补"。
4. **门控的独立性**：gate 脚本只读 evidence 与 git，不改任何状态；它不能证明"测试真的跑过"，因此每份 evidence 必须附**命令原文 + 尾部原始输出片段**（供人工核），gate 只负责"字段齐全 + 数值非空"。

---

## 测试环境与窗口（补充 B）

### 验收库来源：**选 B（生产库锁窗口直测）**，理由与硬边界

| 选项 | 判断 | 理由 |
|---|---|---|
| A 生产库只读副本 | 弃 | 三件套 68.9 GB，D: 剩余 147.1 GB ⇒ 副本占掉可用空间 47%，且多一次 68.9 GB 复制时间；副本还会**掩盖真实句柄/杀软行为**（副本在小目录里，与真实 `data/` 环境不同） |
| **B 锁窗口直测** | **选** | 省空间；且 SC-001/002/003/009/011/012 本来就要求「daemon 停止且零写入」窗口，锁窗口天然满足；能在**真实目录形态**（中文路径、25,267 个同级路径、真实句柄环境）下取读数 |

**硬边界（审核方裁定①，违反即打回）**
- 生产库上**只允许「导出路径的只读验证」**：`export_bundle()` 的 Phase 0–5（其中 Phase 2 的 `CHECKPOINT` 是 spec FR-004 明文要求的写动作，除此之外零写入）；
- **导入 / 覆盖 / 回滚 / 崩溃恢复 / ENOSPC 一律在小库 fixture 上做**，禁止在生产 `DATA_ROOT` 上演练；
- 导出测试完成后必须**移除产出目录**或明确标注「保留为交付候选」，不得留在生产 `data/` 旁造成混淆。

### 窗口开启/恢复步骤（唯一高危点在 flag）

```
声明占用（铁律 19）：时段 + 操作 + 预期恢复时点，落会话群/信箱
1) 精确 PID 停 daemon：候选集 = daemon_status.json.pid（并排除 $PID）；先打印候选清单核对归属；
   禁宽关键词匹配；禁 -Force 群杀
2) 写 data/backtest_stop.flag（JSON：{"reason":"delivery_window","at":…,"owner_pid":$$}）
3) 复核窗口就绪三件：daemon 进程不存在 + daemon_status.json 无 running + flag 存在（三读数入 evidence）
4) 跑测试/导出，全程记字节级进度
5) 恢复：删除 flag → 复核 flag 不存在 → （按需）由用户手动启 daemon；若用户要求本会话启动，
   走 start_daemon_guarded.ps1（不带 -Force），并核对 daemon_status.json
6) 回执必须含：flag 删除确认 + daemon 当前状态一行（停 or 运行 + PID）
```

**⛔ flag 静默解除（本轮实测发现，强制条款）**
`quantstudio/pipeline/daemon_lifecycle.py:585-586` 在 `run_forever()` 起始调用 `clear_stop_flag()`；`quantstudio/gui/tabs/task_tab.py:817` 与 `backtest_tab.py:393` 只在**读** flag 做提示。因此：
> **窗口内禁止任何手动启 daemon（GUI 点「启动采集」/ CLI 启动）。一旦发现 daemon 起来（`daemon_status.json` 变 running 或 PID 存在），即判「窗口作废」，本轮所有读数作废，须重写 flag 后重测；回执必须写明是否发生该事件。**

另外：`README.md:123` 的「存在即跳过」只作用于 `scripts/start_daemon_guarded.ps1:31-34`（计划任务拉起路径）——正是我们需要的保护面；Boot 触发同样经该脚本，故 flag 有效。

### 既有测试回归也在窗口内（D-13，W0 实测所得）

**实测（2026-10-31 W0）**：在 daemon 运行状态下用**真实 `DATA_ROOT`** 跑相关面既有测试，`tests/test_qfq_schema_migration.py` 等与写锁争用，**240 s 未完成**（两次后台作业被迫终止）；改用隔离 `DATA_ROOT` 才取得读数（308 passed / 6 failed，6 项均为隔离所致）。

**纪律**：
- 既有测试回归（相关面与全量）**MUST 在 daemon 停止窗口内**执行；窗口外只允许跑不触库的纯单元测试（如 `quantstudio/delivery/**` 的 manifest/stats 用例）。
- 隔离 `DATA_ROOT`（`QUANTSTUDIO_DATA_ROOT=<空临时目录>`）只能作为**降级 runner**，且其读数必须标注「隔离口径」；隔离会引起"生产路径拒绝"类断言失配（W0 已实测 6 项），不得据此判回归。
- 全量基线（4251 项）在 W2 收口的 L3 窗口内一次跑完，与"6 项隔离失败的无隔离复验"同窗完成。

### 06:00 边界

`collector_tasks.json.daemon_schedule.daily_time="06:00"`：窗口若跨 06:00，daemon 已停 ⇒ 无采集轮次；**但若机器在窗口内重启**，Boot 触发会经 `start_daemon_guarded.ps1` 拉起 —— flag 正是为此存在（且重启后首次 `run_forever()` 会清 flag，所以重启后必须**重新确认 flag 并重写**，此点写入 quickstart）。

---

## 纯增益可机检（七点⑦ + SC-013 基线相对口径）

### 保护路径清单（单一权威：`scripts/delivery_pure_gain_audit.py`）

```python
PROTECTED = [
  "quantstudio/pipeline/writers.py",
] + git_ls_files("quantstudio/pipeline/qfq_*.py") \
  + git_ls_files("quantstudio/pipeline/*upsert*.py")

# 追加型改动文件（②审 D-6 裁定：只核不判，不并入 SC-013 判据）
APPEND_ONLY = [
  "quantstudio/gui/main_window.py",
  "quantstudio/gui/workers.py",
  "pyproject.toml",
  ".gitignore",
]
```
（实测 35 个文件命中 `PROTECTED`：`writers.py` + 34 个 `qfq_*.py`。）

> **D-6（②审裁定采纳）**：`APPEND_ONLY` 的 4 个文件**一并记入基线快照**（逐文件 `worktree_sha256`/`diff_sha256`），每波收口出**对照读数**，但**不参与** SC-013 的通过/失败判定（SC-013 范围仍只含 `PROTECTED`）。理由：`main_window.py` 开工即已有他线未提交 diff，W4 在本文件追加页签注册后，若不核基线就无法自证「哪几行是我加的」——这是诚实性加固，不是判据扩张。

### 基线快照（W0 第一步，**必须在动第一行代码之前**）

```
python scripts/delivery_pure_gain_audit.py --mode baseline \
    --out docs/evidence/delivery-pure-gain-baseline-20261031.json
```
产物结构：`{taken_at, git_head, files:[{path, worktree_sha256, diff_sha256, diff_bytes, tracked}], untracked_in_protected_dirs:[…], session_note}`。
**基线读数是既存事实**：本轮开工实测 `writers.py` 已有 6376 字符未提交 diff（`diff_sha256` 前 12 位 `B0AFE49FFCB8`，源自他线在途），基线文件如实记录，不掩饰。

### 收口审计（每波 + 最终）

```
python scripts/delivery_pure_gain_audit.py --mode audit \
    --baseline docs/evidence/delivery-pure-gain-baseline-20261031.json \
    --out docs/evidence/delivery-wave<N>-<date>.md
```
比对规则：
1. 逐文件 `diff_sha256` 与基线**逐字一致** ⇒ PASS；
2. 不一致 ⇒ 必须分类并留证：`SELF_CHANGED`（本会话改动清单里有该文件 ⇒ **违规**，立即停）vs `OTHER_LINE`（本会话改动清单无该文件，且能看到他线的证据：文件 mtime 变化时间落在本会话无写操作时段 or `git log --since` 有他人 commit）⇒ 记为**例外并留证**，不判违规；
3. 受保护目录内出现**新增未跟踪文件** ⇒ 视为 `SELF_CHANGED` 违规（除非在基线里已存在）。

**「本会话改动清单」的定义**：本会话对工作区的全部写操作在 `docs/evidence/delivery-session-touched-files.txt` 里逐条 append（人工维护，每波收口核对），作为分类的唯一输入。这是诚实性设计：分类不靠猜，靠清单 + 时间证据；分类存疑时默认按 `SELF_CHANGED` 上报（fail-closed）。

**基线吸收例外（②审 W0 裁定，唯一允许重取基线的情形）**：默认**不重取基线**（保证跨波可比）。但若某波收口出现「某保护路径 diff 归零 **且** `git HEAD` 已越过基线 HEAD」——说明他线的在途改动被 commit 吸收了，"与基线一致"此时是**假象**（基线记的是脏工作树，现在变成干净工作树 + 新提交）⇒ **MUST 留证申报并重取基线**，新基线文件记新 HEAD 与新 diff 读数，并在 evidence 写明「为何判定为吸收而非我方改动」。判据由 `delivery_pure_gain_audit.py` 输出：`diff_bytes==0 && git_head != baseline.git_head`。

### 既有测试零衰减

```
python -m pytest tests -q            # 全量（W4 收口必跑）
python -m pytest tests -q -k "not delivery"   # 既有测试子集（每波可跑，快）
```
判据：`failed == 0` 且基线 `failed == 0`（开工时若已有失败，先记录基线，收口时**新增失败必须为 0**）。

### 入口可用性（D-3 裁定）

`pyproject.toml` **不纳入** SC-013 保护清单（它不影响写入路径语义，把它算进"零改动"会让审计变成形式主义）。替代手段是功能验证：W4 DoD 必须含 `qs-deliver --help` 退出码 0 的 smoke。理由：**入口失灵是可观察行为问题，diff 审计看不见它**（典型形态是 script entry 写错模块名——diff 干净但命令跑不起来）。

---

## FR → plan 落点矩阵（41 条，逐条）

| FR | 落点（文件/符号） | 波次 | 验证 |
|---|---|---|---|
| FR-001 硬编码白名单 3 件 | `constants.WHITELIST`（tuple）+ `probe.assert_whitelist_invariants()` | W0/W1 | 单测：恰 3 项、不含 `-wal/-shm`、不可配置 |
| FR-002 排除清单 | `constants.EXCLUDE_PATTERNS` + 自检 `red_line` | W1 | 单测：12 类模式逐条命中样例；导出件命中数==0 |
| FR-003 前置校验 fail-closed + Windows 两项探针 | `probe.py::precheck_export()` / `winenv_probe()` | W1 | SC-001 五场景 + W-1/W-2 用例 |
| FR-004 同锁窗口 checkpoint→WAL 归零→复制 | `export_kernel.py` Phase 1–4 | W1 | 单测：非零 `-wal` ⇒ 拒绝；`busy!=0` ⇒ 拒绝 |
| FR-005 文件级字节复制，禁 CTAS/EXPORT | `export_kernel.py::_copy_file` | W1 | 单测 AST 扫描仓库内无 `COPY … TO`/`EXPORT DATABASE`/CTAS 调用；契约测试（复制后 SHA 一致） |
| FR-006 manifest 字段 + 路径编码 | `manifest.py::build_manifest` + `data-model.md §2` | W1 | Schema 校验测试 + 绝对路径拒绝测试 + `date_basis_col` 断言 |
| FR-007 红线自检 | `verify.py::self_check_bundle`（导出侧复用） | W1 | 红/绿两向用例（造一个含 `.env` 的假包） |
| FR-008 导出零写主库（除 checkpoint） | `export_kernel.py`（无 DDL/DML 路径）+ monkeypatch 记录写调用 | W1 | 单测：导出全程捕获的写调用 ⊆ {checkpoint, journal? 无} |
| FR-009 校验先于覆盖、校验期零修改 | `import_kernel.py::phase3` + 指纹守护 | W2 | SC-005 |
| FR-010 四要素校验 + legacy 显式输出 | `verify.py::verify_bundle` | W2 | 四要素各一用例 + `test_route_config_missing` |
| FR-011 四类失败分派（含版本代差） | `verify.py::classify_failure` + `constants.FAILURE_KIND` | W2 | 四类各一用例 + 版本表（§8） |
| FR-012 `DATA_ROOT` 只读显示 | `config.py::resolve_paths` + `delivery_tab.py`（只读 QLabel） | W4 | UI 测试：无「选择目录」入口；`DATA_ROOT` 文本 == `_paths.DATA_ROOT` |
| FR-013 九步顺序 | `import_kernel.py::run`（Phase 枚举） | W2/W3 | 单测：相位序列断言（监听进度回调） |
| FR-014 三模式 + 恢复优先 | `import_kernel.py::detect_mode` + `journal.detect_pending_journal` | W3 | 四分支各一用例 + 「有残留 journal 不得重推模式」用例 |
| FR-015 备份同卷/跨卷 + 回验两分 | `import_kernel.py::phase6` | W3 | SC-011 + 修复模式降级用例 + 真失败用例 |
| FR-016 staged 改名 + 逐项 journal + 反向回滚 | `import_kernel.py::phase7` | W3 | SC-006 + 混合态禁止用例 |
| FR-017 只增改不删 | `import_kernel.py`（删除动作白名单：仅 `created_files`） | W3 | 用例：安装模式后 `snapshots/`/`.gitkeep`/`README.md` 仍在 |
| FR-018 复验四态同口径 + 两个排除性标签 + 不触发采集 | `verify.py` + `stats.py`（共用） | W3 | 用例：四态判定与 `EMPTY`/`UNKNOWN-BASIS` 排除性标签（spec FR-018 已定，**不参与四态判定**）；无 `subprocess`/daemon 调用；`date_basis_col` 与 FR-006 同源断言 |
| FR-019 字节级进度 + 取消 + 逐文件 fail-fast（**体积升序**） | `export_kernel.py`/`verify.py` 的 `ProgressCB` | W1/W2 | 用例：进度单调、取消后无残留；**体积升序**顺序断言（spec FR-019 已定为主序） |
| FR-020 独立配置两项、无 `backup_mode` | `config.py` + `config/delivery_config.json` | W2 | 单测：字段集恰两项；无 `backup_mode` 键 |
| FR-021 原子写 + 缺失不报错 | `config.py::save`（tmp+replace） | W2 | 用例：删配置 GUI/CLI 正常；损坏 JSON 视为未选择并告警 |
| FR-022 按钮禁用 + 提示 | `delivery_tab.py::_refresh_actions` | W4 | SC-010 |
| FR-023 独立页签「数据交付」 | `gui/main_window.py` 注册 + `delivery_tab.py` | W4 | UI 测试：页签标题存在且与 K 线导出页签分离 |
| FR-024 目录选择 + 启动回显 | `delivery_tab.py`（QFileDialog + 回显） | W4 | 用例：mock 选择后配置落盘、重启回显 |
| FR-025 影响范围标注 + 体积实测 | `delivery_tab.py` + `format_bytes(os.stat)` | W4 | SC-012 + 文案断言（含「暂停采集」/「备份并覆盖」） |
| FR-026 单一内核双薄壳 | `delivery/__init__.py` 导出 API；两壳只调它 | W4 | AST/调用图断言：壳模块内无业务分支（只允许 UI 交互） |
| FR-027 CLI 等价入口 + `--dry-run` | `cli.py` | W4 | `contracts/cli.md` 逐项测试 |
| FR-028 双端逐位一致 | `tests/test_delivery_parity.py` | W4 | SC-009 |
| FR-029 纯增益面 | 设计约束（本文件 §1.2） | 全线 | SC-013 + AST import 扫描 |
| FR-030 不触碰既有配置/台账/日志 | 写落点白名单测试 | W1–W4 | 用例：捕获写调用集合断言 |
| FR-031 `.gitignore` 两条 | `.gitignore` | W0 | 用例：`git check-ignore` 断言两路径被忽略 |
| FR-032 `data/README.md` 分发说明 | `data/README.md` | W4 | 文案断言：含「数据交付」页签、无具体 GB 数字 |
| FR-033 基线过时条件提示 | `import_kernel.py::phase8` + `quality_baseline.json` 只读 | W3 | 用例：水位新于基线 ⇒ 醒目；否则普通提示；断言文件未被改写 |
| FR-034 强制时间戳子目录、旧包不触碰 | `export_kernel.py::_new_bundle_dir` | W1 | 用例：已有旧包时新目录产生且旧包哈希不变 |
| FR-035 三路径互不包含 | `probe.assert_paths_disjoint` | W1/W2 | 用例：6 种嵌套关系全拒 |
| FR-036 journal 完整字段 + 原子写 + 二选一 | `journal.py` + `data-model.md §3` | W3 | SC-014 + 字段完整性单测 |
| FR-037 变更区持锁 | `import_kernel.py` Phase 5–9 | W3 | 用例：持锁期间第二次导入被拒（`WriteLockHeld`） |
| FR-038 `import_dir` 恰一个包 | `verify.py::locate_package` | W2 | 三用例（0/1/2 个 manifest） |
| FR-039 `RESTORE_README.txt` | `import_kernel.py::phase6` | W3 | 断言文件存在且含「勿删除/恢复方法」 |
| FR-040 结果页字段 + 修复模式额外展示 | `delivery_tab.py` + `ImportResult` | W3/W4 | 用例：结果对象字段断言 + 文案含「释放权在客户」 |
| FR-041 基线相对 diff 可机检 | `scripts/delivery_pure_gain_audit.py` | W0/W1–W4 | SC-013 |

**覆盖率自检**：上表 41 行 ↔ spec 41 条 FR，一一对应，无遗漏项（编号核对：001-008,009-019,020-025,026-033,034-041 全在）。

---

## SC → 可执行手段（17 条，摘要；详见 §6.2）

| SC | 判据可执行性 | 层 | 波次 |
|---|---|---|---|
| SC-001 五场景零产出 | 五测试 + 目录前后枚举 | L2(+L3) | W1 |
| SC-002 零泄露 | 正则枚举计数 | L2+L3 | W1 |
| SC-003 同刻一致 | 三件连续只读打开 + 查询 | L2+L3 | W1 |
| SC-004 成功路径 | 全链路 | L2 | W3 |
| SC-005 失败零改动 | 六元组指纹逐位 | L2 | W2 |
| SC-006 回滚有效 | 注入替换失败 | L2 | W3 |
| SC-007 安装模式 | 干净 data/ | L2 | W3 |
| SC-008 功能零变化 | 传导 + fixture 回测 A/B | L4+L2 | W4 |
| SC-009 双端一致 | offscreen 双壳 + 窗口复测 | L2+L3 | W4 |
| SC-010 配置健壮 | 三情形按钮禁用 | L2 | W4 |
| SC-011 备份方式 | 证据链三选一 | L2 | W3 |
| SC-012 体积实测 + 无写死 | 格式化来源 + 常量回扫 | L2 | W1/W4 |
| SC-013 纯增益 | 基线审计 + 全量 pytest | L4 | 每波 |
| SC-014 崩溃恢复 | 子进程强杀 + 二选一 + 禁二次备份 | L2 | W3 |
| SC-015 坏库修复 | 0 字节起点 | L2 | W3 |
| SC-016 幂等 | 连导两次比表 | L2 | W3 |
| SC-017 磁盘耗尽 | 注入 ENOSPC + 回滚 | L2 | W3 |

---

## 已知陷阱 T1–T12 应对矩阵

| # | 陷阱 | 应对（落点） | 验证 |
|---|---|---|---|
| T1 | CTAS/EXPORT 丢 PK/NOT NULL/CHECK ⇒ 写入者拒启 | 只做文件级字节复制（`_copy_file`）；禁止逻辑导出（AST 扫描 + 代码评审）；先例：`knowledge/db/DB-2026-0002.md` | 用例：导出件 `detect_schema_status` == 源库状态（逐字一致） |
| T2 | checkpoint 后 `-wal` 非零 ⇒ 同刻一致未达成 | Phase 3 硬门 fail-closed；`present/bytes` 双写 manifest | SC-001 第五场景 |
| T3 | `-shm` 携带无意义且有过期页映射 | `-shm` 不入白名单；自检 `shm_hits==0` 且产出目录无 `-shm` | SC-002 |
| T4 | Windows 无跨文件原子；`os.replace` 对打开句柄失败 | staged + 逐项 replace + journal + 回滚（FR-016）；WinError 32 专门归因 | SC-006/W-5 用例 |
| T5 | 变更区不持锁 ⇒ daemon 写正在被替换的库 | FR-037：Phase 5 持锁至 Phase 9；持锁失败即拒 | 用例：持锁期间第二实例被拒 |
| T6 | 崩溃重入按文件存在性重判模式 ⇒ 污染唯一回退资产 | FR-014：`detect_pending_journal()` **优先**，有 journal 一律进恢复流程 | SC-014 + 「禁重推模式」单测 |
| T7 | 坏库（最需导入）被"备份必须可回验"卡死 | FR-015 回验降级分支（取证保留 + 继续）；区分「导入前不可读」vs「备份自身失败」 | SC-015 + 真失败用例 |
| T8 | `qfq_aux_paths.json` 相对 config 且可编辑 ⇒ 包内文件对≠运行时读对 | §6 路由断言（用**包内主库**解析 + 路径相等断言 + legacy 显式输出） | `test_route_*` 三用例 |
| T9 | 每表水位基准列真源在 config 不在库 | §5：`questdb_table_map.json.watermark_basis` 逐表解析；缺失标 `unknown` 不猜 | 单测：`trade_date` 陷阱表（分钟表）取到正确列 |
| T10 | `data/` 25,267 路径含 `secrets.env` ⇒ 绝不整目录打包 | 硬编码白名单 + 红线自检 + 不使用 `copytree`（AST 扫描禁 `copytree`） | SC-002 + AST 用例 |
| T11 | 导出目录落在 `data/` 内 ⇒ rename/临时复制递归冲突 | FR-035 三路径互不包含（导出前 + 导入前各一次） | 6 种嵌套全拒用例 |
| T12 | 备份目录污染客户 `git status` | FR-031 两条 `.gitignore` | `git check-ignore` 断言 |

---

## 实现级取舍（②审裁定后回填，2026-10-31）

| # | 申报项 | 裁定 | 回填落点 |
|---|---|---|---|
| A1 | FR-019 排序规则冲突（「按 manifest 顺序」vs「小文件优先」） | **采纳「按体积升序（小文件优先）」**；spec FR-019 已由审核方改为该口径 | [contracts/import_kernel.md](./contracts/import_kernel.md) §2.1 + 任务 T028 |
| A2 | `EMPTY` / `UNKNOWN-BASIS` 两个状态标签 | **已正式入 spec**（FR-018 新增两个**排除性标签**，不参与四态判定） | data-model §2.6（去掉"待入 spec"的申报口径）；FR-018 落点行改引 spec |
| A3 | `row_count` 的 `estimate` 逃生门 | **保留 + 两条强化**：①manifest 顶层记 `row_count_degraded` 与涉及表清单；②导入侧复验读到 estimate 值时报告标「该表行数为估算值」，禁静默混排 | data-model §2.1/§2.5、manifest-v1.schema.json、§5.3 + 任务 T035 |
| A4 | `files[].sqlite_header`（SQLite 头 16B） | **采纳**；另补一条 gen1 交付语义注释（当前≈空库，交付目的 = 世代路由一致性 + 未来切换，非数据体量） | data-model §2.2 + §2.8 |
| A5 | `self_check.red_line.zero_leak`（三项全零合取） | **采纳**（防 CLI/GUI 壳各自重组红线判据） | data-model §2.7 + 任务 T018 |
| D-3 | `pyproject.toml` 是否纳入 SC-013 保护清单 | **不纳入**（不影响写入路径语义）；**但 W4 DoD 增一条 CLI smoke：`qs-deliver --help` 退出码 0** | 本文件 W4 DoD ⑥ + 纯增益章末条 + 任务 T052 |

## Complexity Tracking（仅登记需辩护的例外）

| 例外 | 为何需要 | 为何拒绝更简替代 |
|---|---|---|
| 新增独立包 `quantstudio/delivery/`（9 模块） | 交付能力生命周期与采集管线相反；独立包可用 AST 证明「不 import 写入路径」，这是纯增益的**结构性**证据 | 塞进 `pipeline/` 会让 `delivery` 与写入路径同目录共享 import 便利，弱化"零改动"的可证明性 |
| 对 4 个既有文件做追加型改动（`main_window.py` / `workers.py` / `pyproject.toml` / `.gitignore`） | 双端对等与项目脚本入口的既有约定（`[project.scripts]` 已有 3 条） | 不改则 GUI 无法注册页签/CLI 无入口；替代方案（另建启动脚本）会绕开既有入口约定 |
| Phase 2 对主库执行 `CHECKPOINT`（写动作） | spec FR-004 明文要求；不 checkpoint 则 WAL 归零断言无意义 | 无替代：只读连接无法 checkpoint；关闭连接自动 checkpoint 不保证 WAL 归零 |
| 生产库用于导出路径的只读验证（含一次 checkpoint） | 见 §测试环境（B 方案理由） | A 方案需 68.9 GB 额外空间且掩盖真实句柄环境 |
| 测试专用子集库（SC-008 的 A/B 回测）用 `ATTACH+INSERT SELECT` 造 | 回测需要真实数据；生产库禁止导入 | 不造子集则 SC-008 只能靠传导论证，诊断力下降；已注明「交付包仍禁 CTAS，此处仅测试 fixture」（research R9） |
| 受控崩溃注入钩子（env 双开关） | SC-014 需真强杀；随机 `taskkill` 触犯铁律 5 的误杀面 | 不用钩子只能人工演练，无法进门禁 |

**复评（设计完成后）**：上述 6 项均不违反原则一~五（无行为漂移面、无并行机制、改动可单点回退）；附加约束（duckdb 版本/DDL/并发/文档）逐条已核。**结论：Constitution Check 复评通过。**

---

## 风险与回退

| 风险 | 触发信号 | 回退动作 |
|---|---|---|
| 生产窗口读数期间发生写入（daemon/他线） | `daemon_status.json` 变 running；导出件 SHA 与统计时段不一致 | 判窗口作废，重写 flag 重测；读数不采用 |
| 逐表统计拖长窗口 | `stats` 进度停滞 > 8 分钟 | 启用 `--row-count=estimate`（manifest 标 `row_count_method`）+ 留读数与理由 |
| `os.replace` 在客户机被占用反复失败 | 连续两次 WinError 32 | 回滚并提示关闭杀软/预览窗格，不重试覆盖 |
| 他线在窗口内修改受保护路径 | SC-013 审计 `OTHER_LINE` | 记录证据、不判违规，但**在本波收口申报**并请审核方裁定是否重取基线 |
| spec 与实现出现解释分歧 | 编码时发现某 FR 无法同时满足 | **停**，按任务书「认为需求有问题 ⇒ 提出并停等裁定」处理 |

---

## 交付物与下一步

- 本轮产出：`plan.md`（本文件）、`research.md`、`data-model.md`、`contracts/`（3 md + 1 json schema）、`quickstart.md`；`.specify/feature.json` 已切到 `specs/003-delivery-bundle-import`（D-3 已获准）。
- **本阶段零源码改动**（仅文档 + 1 个 58 字节内务文件）。
- 下一步：**停在此处等②审**。过审后进入 `speckit-tasks` 产出 `tasks.md`（阶段化 + `T001 [P] [US1]` 格式 + 波次依赖），再按 W0→W4 逐波实施。
