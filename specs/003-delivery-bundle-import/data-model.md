# Data Model: 交付包 manifest / journal / 配置 / 结果对象

**Feature**: 003-delivery-bundle-import | **Date**: 2026-10-31 | **关联**: `plan.md` §2/§4/§5、`contracts/manifest-v1.schema.json`

> 本文是**字段级真源**：`manifest-v1.schema.json` 必须与本文同步（tasks 里有一条「schema 与本文一致性」测试）。所有路径字段一律 **UTF-8 + 相对包根**，禁绝对路径（FR-006）。

---

## 1 实体总览

| 实体 | 落盘位置 | 生命周期 | 权威定义处 |
|---|---|---|---|
| `Manifest`（v1） | `<bundle>/manifest.json` | 随包分发；只读消费 | `quantstudio/delivery/manifest.py` |
| `BundleFile` | manifest 内 `files[]` | 同上 | 同上 |
| `TableStat` | manifest 内 `tables[]`；复验时同构生成 | 同上 | `quantstudio/delivery/stats.py` |
| `ImportJournal` | `data/import_journal.json` | 导入开始 → 成功/回滚完成后删除 | `quantstudio/delivery/journal.py` |
| `DeliveryConfig` | `config/delivery_config.json` | 长期（用户设置） | `quantstudio/delivery/config.py` |
| `ExportResult` / `VerifyReport` / `ImportResult` / `RecoveryResult` | 不落盘（返回对象 + 可选 `--json` 输出） | 单次调用 | `quantstudio/delivery/__init__.py` |

---

## 2 `Manifest` v1（字段级）

### 2.1 顶层

| 字段 | 类型 | 必填 | 语义 | 校验规则（违反 ⇒ 失败类） |
|---|---|---|---|---|
| `manifest_schema_version` | `int` | ✅ | manifest 格式版本；当前 `1` | 缺失/非 int ⇒ ①包问题；`> supported` ⇒ ④包格式代差；`< min` ⇒ ②程序版本不匹配 |
| `generated_at` | `str`（ISO8601 带时区偏移） | ✅ | 导出完成时刻 | 不可解析 ⇒ ① |
| `producer` | `object` | ✅ | 生产方环境（诊断用） | 缺字段 ⇒ ① |
| `producer.app` | `str` | ✅ | 固定 `"QuantStudio"` | 不等于该值 ⇒ ①（不是本项目的包） |
| `producer.version` | `str` | ✅ | 程序版本（`quantstudio/__init__.py` 版本或 git describe） | 空 ⇒ ① |
| `producer.python` / `.duckdb` / `.sqlite` / `.platform` | `str` | ✅ | 运行环境快照（**仅诊断**，不参与放行判断） | 空 ⇒ ① |
| `files` | `array<BundleFile>` | ✅ | 白名单文件清单（长度必须 == 白名单长度） | 长度不符/含白名单外路径 ⇒ ① |
| `main_db` | `object` | ✅ | 主库标识与 schema 实测 | — |
| `qfq` | `object` | ✅ | 世代与路由信息 | — |
| `table_set` | `object` | ✅ | 表集边界声明（Q2 裁定的落点） | — |
| `tables` | `array<TableStat>` | ✅ | 逐表统计与状态 | 空数组 ⇒ ①（主集不可能为空） |
| `row_count_degraded` | `bool` | ✅ | 本次是否有表用了 `estimate`（②审 A3 强化①：只写逐表 `method` 不够，消费者要能一眼看出"这一包不是全量精确统计"） | `true` 而 `row_count_degraded_tables` 为空 ⇒ ① |
| `row_count_degraded_tables` | `array<str>` | ✅ | 用了 `estimate` 的表名清单；无降级时为空数组 | 与 `tables[].row_count_method` 不一致 ⇒ warning |
| `self_check` | `object` | ✅ | 红线自检结果 | 任一红线非零 ⇒ **产出不合格**（导出侧告警；导入侧按①拒绝） |
| `export` | `object` | ✅ | 锁窗口与耗时读数（诊断） | — |
| `bundle_id` | `str` | ⭕ | `sha256(规范化 manifest 去掉 bundle_id 与 export.*)` | 存在但与重算不符 ⇒ warning（不拒绝：允许人工后加工） |
| `unknown_fields` | — | — | **不写入**；读侧遇到未知字段记入报告 warning | — |

### 2.2 `BundleFile`

| 字段 | 类型 | 必填 | 语义 | 校验 |
|---|---|---|---|---|
| `path` | `str` | ✅ | 相对包根的文件名（如 `quantstudio.db`） | 必须 ∈ `WHITELIST`；不得为绝对路径/含 `..`/含盘符/UNC（正则 + `PurePosixPath`） |
| `kind` | `enum("duckdb","sqlite")` | ✅ | 引擎类型 | `quantstudio.db` ⇒ duckdb；两个 aux ⇒ sqlite |
| `bytes` | `int` | ✅ | 字节数（`>=0`） | 与包内实际文件 `st_size` 不符 ⇒ ① |
| `sha256` | `str`（64 hex，小写） | ✅ | 文件内容哈希 | 与实算不符 ⇒ ① |
| `sqlite_header` | `str\|null` | ⭕ | SQLite 件写入头 16B 文本（`"SQLite format 3\u0000"`），作"有效库"旁证 | 非 null 且与实读不符 ⇒ warning |

### 2.3 `main_db` / `qfq`

| 字段 | 类型 | 必填 | 语义 | 校验 |
|---|---|---|---|---|
| `main_db.path` | `str` | ✅ | 通常 `quantstudio.db` | ∈ 白名单 |
| `main_db.schema_status` | `enum` | ✅ | 导出时实测：`complete_2_1`/`complete_2_2` | 不在放行集 ⇒ ①（包本身不合格） |
| `qfq.released` | `bool` | ✅ | 导出机 `qfq_aux_paths.json` 的 `released`（诊断/比对用） | 缺失 ⇒ ① |
| `qfq.qfq_active_cutover` | `object\|null` | ✅ | `{cutover_id, source_generation, price_source}`；无记录为 `null` | 有对象但缺键 ⇒ ① |
| `qfq.aux_route_step6_expect` | `object` | ✅ | `{legacy: "qfq_aux.db", gen1: "qfq_aux_mcp_gen1.db"}`（世代→文件名映射的期望） | 与实际包内文件不符 ⇒ ① |
| `qfq.aux_paths_config_sha256` | `str` | ⭕ | 导出机该配置文件的哈希（仅诊断"包是按哪份配置出的"） | — |

> **注意**：导入端**不得**用 `qfq.released` / `qfq_active_cutover` 直接决定路由，路由一律经 `resolve_runtime_aux_path`（plan §6）。manifest 里的这两个字段只用于「包与客户配置是否朝向同一世代」的对照与报告。

### 2.4 `table_set`（Q2 裁定的字段化落点）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `rule` | `str` | ✅ | 固定 `"(应推进水位的表 ∪ questdb_table_map 覆盖的表) ∩ 库内实际存在"`（机检：字符串必须逐字等于该常量） |
| `count` | `int` | ✅ | 主集大小（`tables.length`，冗余便于快速读） |
| `empty_count` | `int` | ✅ | 其中 `state==EMPTY` 的数量 |
| `unknown_basis` | `array<str>` | ✅ | `date_basis_col=="unknown"` 的表名清单（不猜基准列的证据） |
| `out_of_map` | `array<str>` | ✅ | 有 `source_watermark` 行但不在 map 内的表（配置漂移警示；不进 `tables[]`） |

### 2.5 `TableStat`

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `table` | `str` | ✅ | 表名（== map 的 key） |
| `source` | `str` | ✅ | map 的 `source`（如 `questdb`） |
| `freq` | `str` | ✅ | map 的 `freq`（`daily`/`minute`…） |
| `date_basis_col` | `str` | ✅ | 本次实际所用列名；`"unknown"` 表示 map 缺 `watermark_basis` |
| `basis_source` | `enum("questdb_table_map.json","none")` | ✅ | 基准列来源自证 |
| `row_count` | `int` | ⭕ | `date_basis_col=="unknown"` 时仍输出（行数不依赖基准列） |
| `row_count_method` | `enum("exact","estimate")` | ✅ | 默认 `exact`；降级时必须在 evidence 留读数与理由（plan §5.3）。**导入侧义务（A3 强化②）**：复验报告遇到 `estimate` 必须给该表附「该表行数为估算值」，禁止与 exact 行静默混排 |
| `max_date` | `str\|null` | ⭕ | `date_basis_col=="unknown"` 时**必须为 null**（不猜）；否则为本地日期 `YYYY-MM-DD` |
| `watermark_last_date` | `str\|null` | ⭕ | `source_watermark` 行的 `last_date`（epoch ms → 本地日期）；无行 ⇒ `null` |
| `state` | `enum` | ✅ | 见 2.6 |

### 2.6 `state` 枚举与判定（plan §5.2 的规范形式）

```
EMPTY          row_count == 0 且 watermark_last_date is null           （非缺陷）
MISSING        row_count  > 0 且 watermark_last_date is null           （缺陷：增量退化为全历史）
UNKNOWN-BASIS  date_basis_col == "unknown"                            （非缺陷：未猜）
UNKNOWN-SOURCE 有水位行，但客户配置无对应采集任务/凭据                （非缺陷：信息性）
OK             Δ == 0
BEHIND         Δ  > 0   （N = Δ 天：水位落后数据，下次采集会重拉 N 天）
AHEAD          Δ  < 0   （N = |Δ| 天：水位领先数据，数据可能截断或水位虚高）
```

判定顺序（优先级即上表顺序；**先判空、再判缺、再判基准列、再判源、最后判前后**）。`Δ = (max_date − watermark_last_date).days`，两者都按**本地日期**取整（时间部分丢弃）。

> **口径说明（②审 A2 裁定后更新）**：spec FR-018 已**正式纳入** `EMPTY` 与 `UNKNOWN-BASIS` 两个**排除性标签**（明确"不参与四态判定，仅为可显示的事实"）。因此本文与代码里它们是**标签**而非"第五/第六态"：四态判定（`OK`/`BEHIND`/`AHEAD`/`MISSING`）+ `UNKNOWN-SOURCE` 之外的结论性判断只看四态；两个排除性标签只用于解释"为什么这行没有结论"。

### 2.7 `self_check` / `export`

| 字段 | 类型 | 必填 | 语义 | 红线判据 |
|---|---|---|---|---|
| `self_check.red_line.outside_whitelist` | `int` | ✅ | 产出目录中白名单外文件数 | 必须 `== 0`（FR-007） |
| `self_check.red_line.env_hits` | `int` | ✅ | `*.env` 命中数 | `== 0` |
| `self_check.red_line.shm_hits` | `int` | ✅ | `-shm` 命中数 | `== 0` |
| `self_check.red_line.log_hits` | `int` | ✅ | 日志/缓存类命中数 | `== 0` |
| `self_check.red_line.zero_leak` | `bool` | ✅ | 上述三项全零的合取（便于 UI 单点判断，避免 UI 自己重组判据） | `true` |
| `self_check.db_open[]` | `array{path,select1}` | ✅ | 三件逐一只读打开 + `SELECT 1` | 长度 == 白名单长度且全 `ok` |
| `self_check.wal_zero[]` | `array{path,present,bytes}` | ✅ | checkpoint 后的 WAL 探测 | `present==false` 或 `bytes==0` |
| `export.lock_task_id` / `.lock_acquired_at` / `.lock_released_at` | `str` | ✅/⭕ | 锁窗口证据（`lock_released_at` 缺失 ⇒ warning，不拒绝） | — |
| `export.duration_sec` | `float` | ✅ | Phase 1→5 总耗时 | — |
| `export.checkpoint` | `object` | ✅ | DuckDB 结果 + 每个 SQLite 件的 `(busy,log)` | `busy != 0` ⇒ 包不合格 |
| `export.copy_bytes_total` | `int` | ✅ | 复制总字节（== `sum(files[].bytes)`） | 不等 ⇒ ① |
| `export.copy_sha256_pass` | `bool` | ✅ | 流式哈希与写入字节一致性 | `false` ⇒ 包不合格 |

### 2.8 交付语义注释（供后来者，勿再质疑）

- **`qfq_aux_mcp_gen1.db` 当前≈空库**（2026-10-31 实测 53,248 B，头 `SQLite format 3`，是**有效** SQLite 而非空壳/损坏文件）。它随包交付的目的**不是数据体量**，而是：①**世代路由一致性**——`qfq_aux_paths.json` 的 `released` 一旦置 true 且主库有 active cutover，运行时就会读 gen1；缺文件 ⇒ 客户切世代即断链；②**未来切换的可预期性**——两代并存是既定机制，包内两代齐备才能让"切换只改配置"的承诺成立。manifest 中该文件的 `bytes`/`sha256` 如实反映其实际大小，不因"小"而省略。
- **`files[].sqlite_header` 的存在理由**：正是为了让上面这条判断**可复算**——只看体积容易把 52 KB 误判成损坏，头 16 字节是"这是不是一个 SQLite 库"的最廉价判真手段。

---

## 3 `ImportJournal`（`data/import_journal.json`）

> 与 manifest 不同：journal 是**本机运行时**状态，**允许绝对路径**（它必须跨 CWD 变化存活），但同样禁止承载凭据。

### 3.1 字段

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `journal_version` | `int` | ✅ | 当前 `1`；未知版本 ⇒ recovery 拒绝自动恢复并提示人工（fail-closed） |
| `started_at` / `updated_at` | `str` | ✅ | ISO8601 |
| `phase` | `enum` | ✅ | 见 3.2 |
| `mode` | `enum("update","repair","install")` | ✅ | 进入时判定，之后**不再重推**（T6） |
| `data_root` | `str`（绝对） | ✅ | 变更区根；恢复时校验「当前 `DATA_ROOT` == 本字段」，不符 ⇒ 拒绝自动恢复 |
| `package_dir` | `str`（绝对） | ✅ | 包目录 |
| `package_manifest_sha256` | `str` | ✅ | 包标识（manifest 文件哈希） |
| `manifest_schema_version` | `int` | ✅ | 便于诊断"恢复时程序已换版" |
| `backup_dir` | `str\|null` | ⭕ | 安装模式为 `null` |
| `lock_task_id` | `str` | ✅ | `"delivery_import"`（恢复时用于诊断残留锁归属） |
| `files` | `array<JournalFile>` | ✅ | 待处理/已处理清单 |
| `created_files` | `array<str>` | ✅ | 安装模式下本次创建的目标文件名（回滚 = 删除这些；FR-014/017 的边界） |
| `forensic` | `object\|null` | ⭕ | 修复模式取证摘要：`{bytes, sha256|null, open_error, schema_status}` |
| `host` | `object` | ✅ | `{hostname, pid, create_time}`（诊断 PID 复用） |

### 3.2 `phase` 相位机

```
lock_acquired → backup_started → backup_done → overwrite_started → overwrite_done
              → reverify_started → done
rolling_back → rolled_back
```
**单调前进**；回滚时切 `rolling_back`；成功或回滚完成后删除 journal。`phase` 只能前进（恢复流程也遵守），跳变视为 journal 损坏 ⇒ 拒绝自动恢复。

### 3.3 recovery 决策表（机检逐行）

| `phase` | 「继续完成」 | 「回滚到导入前」 |
|---|---|---|
| `lock_acquired` / `backup_started` | 重跑备份（尚未替换，幂等安全） | 无需还原；删 journal |
| `backup_done` / `overwrite_started` | 从首个 `state=="pending"` 继续替换 | 按 `backup_name` 还原 `state=="done"` 项；删 `created_files` |
| `overwrite_done` / `reverify_started` | 只做复验并收尾（不再替换） | 全量还原 `state=="done"` 项；删 `created_files` |
| `rolling_back` | **拒绝**（回滚未完成不得继续）→ 提示重跑回滚 | 重跑回滚（幂等） |
| `rolled_back` | 拒绝（终态） | 删 journal（清理终态） |

**不变式**：恢复流程**不得**调用 `detect_mode()`（禁止按文件存在性重推模式）；恢复流程取锁后必须重算 journal 指针，不得信任内存中的旧值。

---

## 4 `DeliveryConfig`（`config/delivery_config.json`）

| 字段 | 类型 | 必填 | 语义 | 校验 |
|---|---|---|---|---|
| `config_version` | `int` | ✅ | 当前 `1` | 未知版本 ⇒ 视为未选择 + 告警（不崩） |
| `export_dir` | `str` | ✅ | 导出根目录（可空串 = 未选择） | 空 ⇒ 按钮禁用；非空但不存在 ⇒ 提示不可用（不崩） |
| `import_dir` | `str` | ✅ | 交付包扫描根（可空串 = 未选择） | 同上 |

**约束**：不得含数据目录路径（`DATA_ROOT` 有唯一真相源，FR-020）；不得有 `backup_mode` 之类开关（v3 复审裁定删除）。
**写入**：`delivery_config.json.tmp<pid>` + `os.replace`（原子）；读失败/JSON 损坏 ⇒ 视为未选择并告警（FR-021）。
**路径可空**：整个文件缺失也必须能启动（SC-010）。

---

## 5 返回对象

### 5.1 `ExportResult`
`{bundle_dir, manifest_path, files:[{path,bytes,sha256}], total_bytes, duration_sec, self_check, warnings:[str], qualified: bool}`
`qualified=False` ⇒ 红线自检未过（FR-007：标记不合格并告警，但产物保留供取证）。

### 5.2 `VerifyReport`
`{ok, failure_kind: ①②③④|null, reasons:[str], package_dir, manifest_schema_version, files_checked, route: {resolved_path, reason, target_path, matched: bool}, tables:[TableStat], warnings:[str], elapsed_sec}`
`ok=False` 时 `failure_kind` 必填（FR-011 的分派依据）。

### 5.3 `ImportResult`
`{ok, mode, backup:{dir,bytes,method:"rename"|"copy"|"none"}, files:[{name,action:"replaced"|"created",bytes,sha256_ok}], reverify:{tables:[TableStat], summary:str, can_incremental:bool}, forensic:{...}|null, baseline:{stale:bool, note:str}, journal_finalized:bool, warnings:[str], elapsed_sec, exit_code}`
`mode=="repair"` ⇒ `forensic` 必填且界面必须展示释放提示（FR-040）。

### 5.4 `RecoveryResult`
`{journal_found, phase_before, action:"continue"|"rollback", completed_items, restored_files:[str], deleted_created_files:[str], reverify?:{...}, ok}`

---

## 6 校验矩阵（字段缺失/非法 → 失败类）

| 情形 | 失败类 | 用户可见 | 退出码 |
|---|---|---|---|
| `manifest.json` 不存在 / 不是合法 JSON | ① 包问题 | 交付包不完整或已损坏 | 4 |
| 缺 `manifest_schema_version` / 类型错 | ① | 同上 | 4 |
| `manifest_schema_version > 1` | ④ 包格式代差 | 包由更新版本的程序生成，请先更新程序 | 6 |
| `manifest_schema_version < 1` | ② 程序版本不匹配 | 程序版本与数据版本不匹配 | 6 |
| `files[]` 缺项 / 多出白名单外项 / 路径非法 | ① | 交付包不完整或已损坏 | 4 |
| 任一文件 SHA256 不符 / 字节数不符 | ① | 同上 | 4 |
| `main_db.schema_status` 不在程序放行集 | ② | 程序版本与数据版本不匹配 | 6 |
| `main_db.schema_status` 与实测不一致 | ① + warning「包可能被改动或导出时状态有误」 | 同上 | 4 |
| 路由路径 ≠ 导入目标 / `DATA_ROOT` 非法 | ③ 配置不匹配 | 配置不匹配 + 两路径对照 | 5 |
| `table_set.rule` 不等于约定常量 | ① | 包由不兼容的导出器生成 | 4 |
| 未知顶层字段（同版本内） | — | 通过 + warning 列出字段名 | 0 |

---

## 7 与 spec 的字段级追溯

| spec 条款 | 本文落点 |
|---|---|
| FR-006（manifest 字段 / 相对路径 / `date_basis_col`） | §2.1–2.5 |
| FR-007（红线自检） | §2.7 |
| FR-010（四要素校验） | §6 + `VerifyReport` |
| FR-011（四类失败分派） | §6 |
| FR-014（三模式 + 恢复优先） | §3.3 |
| FR-015/016（备份与替换语义） | §3.1 `files[]` / `backup_dir` / `created_files` |
| FR-018（复验四态同口径） | §2.5–2.6（导出与复验共用 `TableStat`） |
| FR-020/021（配置两项 + 原子写） | §4 |
| FR-033（基线过时提示） | §5.3 `baseline` |
| FR-036（journal 字段与二选一） | §3 |
| FR-038（恰一个包） | `VerifyReport.package_dir` + `locate_package` |
| FR-040（结果页字段） | §5.3 |
