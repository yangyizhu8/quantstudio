# 治理方案实施第 3 步 — 数据快照版本机制设计 v3（A 线，二轮修订版）

- 状态：**待复审**（v2 有条件通过后 4 项阻塞修订；未实施任何代码）
- 修订依据：DSH 快照设计复审（S3-B1 写路径准入 / B2 三重 hash / B3 键唯一性实证+RSS / B4 编码定案）


- v1：见 git 历史（v1 的 §1-§9 结构保留，本版按阻塞项逐条修订，附对照表）

---

## 0. 三决策点定案（DSH 裁定，已固化）

1. **副本方式 = 完整复制**（否决硬链接）；
2. **snapshot_meta.json 自动写入 = 走微流水线**，过渡期仅使用显式 `bind` 子命令（S3-B7）；
3. **基线引用快照默认不可删除**；仅基线退役/迁移后由**用户明确批准**，以 `--unprotect <ID> --reason <text>` 解除并写审计记录；**禁止普通 --force**。

## 1. 写锁：权威互斥锁（S3-B1）

- 新增 `data/snapshots/.write_lock`（filelock 协议：内容 = 持有者 PID + 任务 ID + 心跳时间戳）；
- **契约**：一切 DuckDB 写任务（daemon/修复脚本/唯一写入会话）与 `snapshot create` **共用此锁**——写任务开写前获取、任务结束释放；create 获取不到锁即 **fail-closed 拒绝**（退出码 2），不做"进程名/lock 文件扫描"式的弱探测；
- **写路径覆盖准入（DSH 复审 S3-B1 强化）**：create 启动时读取 `data/snapshots/write_path_registry.json`（写路径覆盖清单，见 §1.1），**任一 MAIN/AUX 写路径未标记 `locked: true` 即拒绝创建**（退出码 5）——不做"manifest 记录后继续"的弱处置；清单随新增写脚本同步更新，清单本身纳入登记表管理；
- 清单 v2（DSH 三审补全）：`data/snapshots/write_path_registry.json`，**MAIN 写路径 10 项 + AUX 写路径 5 项**（glob 已全部展开为具体脚本），另列排除项 5 类（写入对象非快照源：exporter 只写导出副本且源库 read_only 实测 L77、daemon 状态库/审计库、export 缓存清理、治理 json——qfq_invariant 黄金行 json 已从 sqlite 分类移出单列 GOV 类）；当前 MAIN/AUX 15 项全部 locked=false → create 处于拒绝状态（正确），**禁止手工放行**；
- 注意：文件锁是协作锁的本质不可消除，故 B1 的最终保证 = 锁协议 + 覆盖清单准入 + B2 三重 hash 的事后检测（锁外写入必然导致 source_hash_pre 不等 source_hash_post，快照失败），三层防御；
- 心跳超时（>10 分钟无更新）视为陈锁 → create 仍拒绝但报告陈锁告警（不自动清除，人工确认）。

## 2. 冻结窗口与竞态防护（S3-B2）

create 全流程在持有写锁期间原子完成，四阶段任一失败即整体失败：

```
[获取写锁] → ①源库校验（完整性探针+基准行数）
           → ②文件复制（主库+aux+配置 → <SNAP_ID>.tmp/）
           → ③副本校验（read_only 打开 + 逻辑 hash 重算对照 ①记录的源基准）
           → ④manifest 原子提交（见 S3-B6）→ [释放写锁]
```

- **源变化检测（DSH 复审 S3-B2 强化：三重逻辑内容 hash）**：
  - 阶段① source_hash_pre：源库全部回测必需表的逻辑内容 hash（流式）
  - 阶段③ copy_hash（副本同规范重算）+ source_hash_post（源库复算）
  - 判定：source_hash_pre == source_hash_post == copy_hash，三者任一不等 → 失败
  - 行数/mtime/size 仅作快速探针（阶段①前置短路），**不作为一致性依据**；
  - 三重 hash 使锁协议被绕过时的写入必然被检测（与 §1 三层防御互补）；
- 失败路径统一：tmp 目录删除 + index 不写入 + 非零退出 + 失败原因落 `data/snapshots/failed/` 日志。

## 3. 逻辑 hash 可执行性（S3-B3）

- **流式分块**：逐表 `con.execute("SELECT ... ORDER BY <键>").fetch_record_batch(65536)` 流式读取，行内序列化后增量喂入 sha256（`hashlib.update`），**禁止 pandas 全量加载**；
- **内存上限**：单表峰值 ≤ 512MB（record batch 流式保证），实现中用 `tracemalloc` 自检（超限告警）；
- **18 张表稳定排序键**（按主键/唯一键，已在主库实测列序；机器配置 data/snapshots/sort_keys.json）：

| 表 | ORDER BY |
|---|---|
| stock_daily / etf_daily / stock_minutes / etf_minutes | code, time |
| index_daily | code, time |
| index_constituents | index_code, code, time |
| etf_basic / stock_basic / sw_industry | code |
| etf_dividend / stock_dividend | code, ex_date |
| stock_float_share | code, end_date |
| stock_daily_valuation | code, time |
| fin_indicator | code, ann_date, end_date |
| index_constituents_snapshot_meta | index_code, time |
| industry_classification | classification_system, classification_version, industry_code, effective_from |
| industry_membership | classification_system, classification_version, code, effective_from |
| strategy_events | event_type, event_date, code |

- **排序键唯一性实证（DSH 复审 S3-B3，2026-08-17 主库只读实测）**：18/19 表键唯一（PASS）；**strategy_events (event_type, event_date, code) 存在 3 组重复**（同事件重复导入，差异在 imported_at/source_row_id；另有 1 组全列完全重复——数据发现，登记 D2-F5 重复导入清理项）。
  - strategy_events 采用**确定性退化方案**：全列 canonical 排序（ORDER BY 全部 12 列按 information_schema 列序）；
  - 实现快照脚本前重跑唯一性检查并归档结果（键集变更时先改本设计再实施）；
- **内存监控双轨（DSH 复审）**：tracemalloc 之外，create 与测试记录**进程 RSS/峰值工作集**（psutil 或 Get-Process PeakWorkingSet64），写入 manifest（`peak_rss_mb`）并纳入测试证据；512MB 上限双轨校验。

## 4. canonical 编码规范（S3-B4）

序列化规范（写入设计文档并在 fixture 测试中固化）：

| 类型 | 规范 |
|---|---|
| INTEGER/BIGINT | 十进制无填充 |
| DOUBLE | Python `repr(float)`；**NaN→`NaN`、Inf→`Inf`/`-Inf`、-0.0→`-0.0`**（区分 +0.0） |
| DECIMAL | **定案：保留定点字符串**（CAST(col AS VARCHAR) 原样输出，不转 DOUBLE，避免精度丢失） |
| VARCHAR | 原样 UTF-8，不转义（分隔符碰撞由 §分隔符规则处理） |
| TIMESTAMP/BIGINT ms | 一律按存储类型（BIGINT ms）十进制输出，不做时区转换 |
| DATE（整型 yyyymmdd 等） | 按存储类型原样 |
| BLOB | hex 编码，前缀 `0x` |
| NULL | 哨兵串 `\N` |
| 分隔符 | 列 0x1F，行 0x0A |
| VARCHAR 转义规则（**定案**） | 1) 普通 UTF-8 字符原样输出；2) 控制字符（U+0000-U+001F，含两个分隔符本身）统一转义为 反斜杠+u+两位小写hex；3) 转义前缀反斜杠自身重复输出（反斜杠反斜杠）；4) 该编码仅用于 hash 计算（单向），fixture 字节级断言；5) 反转义规则文档化（uXX 到控制字符、双反斜杠到单反斜杠）供 verify 工具核对 |
| schema/类型快照 | manifest 记录每表列名+类型+序（information_schema 导出，canonical JSON：键排序、无空白） |
| canonical JSON | `json.dumps(..., sort_keys=True, separators=(",",":"))` |

**fixtures**：`tests/fixtures/snapshot_encoding/` 覆盖 NaN/Inf/-0.0/NULL/BLOB/含分隔符字符串/DECIMAL 各一例，断言字节级输出。

## 5. qfq_aux（SQLite）一致性（S3-B5）

- 复制方式：**优先 SQLite `VACUUM INTO '<副本路径>'`**（官方一致性强拷贝，产物已去碎片）；不支持时回退 `sqlite3.Connection.backup()` API；**禁止裸文件复制**（WAL 模式下可能不一致）；
- 副本校验：`PRAGMA integrity_check`（必须返回 `ok`）+ §3 逻辑 hash（仅 `adj_factor`、`fund_adj` 两表，流式同规范）；
- 主库（DuckDB）：副本以 read_only 打开执行 hash（等价于 checkpoint 后一致读）。

## 6. 磁盘与原子性（S3-B6）

- **create 前置 fail-closed 磁盘检查**：预估空间（源文件字节数和 × 1.05）+ 滚动保留后快照总量 ≤ 磁盘公式预算（`5×main + 2×aux + max(10GiB, 20%×(main+aux))`），不足即拒绝（退出码 3）；
- `.tmp` 目录计入保护：prune 不触碰 `*.tmp`（由 create 失败路径自清理 + 启动时清扫孤儿 tmp）；
- **受保护快照**：manifest 增加 `protected: true` 字段（基线绑定即置位）；prune 遇保护项跳过并在输出中列出；
- **index.json 原子更新**：写 `<tmp文件>` → `os.fsync` → `os.replace` 原子替换；manifest.json 同协议。

## 7. 边界收缩：不触碰框架（S3-B7）

- **v1 的 result_exporter 自动写入 snapshot_meta.json 撤销**（属框架行为增量）——后续如需自动绑定，另走微流水线送审；
- 过渡期唯一绑定方式：`snapshot bind <SNAP_ID> <result_dir>`（在结果目录**外挂**生成 `snapshot_meta.json`，不修改 result_exporter 任何产物）；
- 回测绑定约定：运行时以 `db_path` 指向快照副本，运行者手动 bind；基线建立流程（第 4 步）把 bind 纳入基线档案步骤。

## 7.5 实施顺序重排（DSH 终审阻塞 2 闭合：消除循环依赖）

**3A 写锁收口（前置，独立微流水线 `docs/governance-3a-write-lock-design.md`）→ 3B 本快照 CLI 实现与首份修复前快照 → 唯一写入会话修复 → 写后快照 + D2 复检 → 第 4 步基线**。原第 5 步仅保留流程治理（队列/准入/并行度）；locked=true 翻转由 lock_adoption_log 证据驱动（扫描器校验），禁止手工。

## 8. CLI 与测试（更新）

```
create [--source-task <id>]          # 全流程 §2，fail-closed：锁(2)/磁盘(3)/一致性(4)
verify <SNAP_ID>                     # 重算逻辑 hash 对照 manifest（流式）
list / prune [--keep 3] / bind <ID> <dir>
unprotect <ID> --reason <text>       # 仅用户批准场景；写 audit 记录（data/snapshots/unprotect.log）
```

测试新增（在 v1 §7 基础上）：
- 锁：持锁时 create 拒绝（fail-closed）；陈锁告警不自动清除；
- 竞态：①③之间模拟源库变更 → 快照失败 + tmp 清理 + 告警；
- 磁盘：空间不足 fail-closed；
- 原子性：index.json 更新中途 kill → 旧 index 完好；
- 编码 fixtures（§4 全类型）；
- SQLite：WAL 模式源库 VACUUM INTO 副本 integrity_check ok。

## 9. 影响面（更新）

- 新增脚本 + 快照目录 + 写锁文件；**零框架/数据行为改动**；
- **存量写脚本接入写锁**（daemon、修复脚本、events.py 导入等）：属第 5 步读写隔离实施项，此处仅在 manifest 记录 `lock_protocol_version` 标注覆盖程度（诚实标注：过渡期未接入者为已知风险）；
- 回退：删除脚本/目录/锁文件即完全回退。

## 附录：S3-B1~B7 修订对照表

| # | 审计阻塞项 | v2 落实 |
|---|---|---|
| S3-B1 | 写锁需权威互斥、fail-closed | §1 共用文件锁 + 退出码 2 + 陈锁告警不自动清 |
| S3-B2 | 冻结窗口覆盖全流程、源变化必失败 | §2 四阶段 + ①③基准复检 + 统一失败路径 |
| S3-B3 | 流式 hash、内存上限、排序键清单 | §3 record_batch 流式 + 512MB + 20 表键表 |
| S3-B4 | canonical 编码全类型 + fixture | §4 规范表 + fixtures 目录 |
| S3-B5 | SQLite 一致性协议 | §5 VACUUM INTO/backup API + integrity_check |
| S3-B6 | 磁盘 fail-closed、tmp/保护、index 原子 | §6 公式预算 + os.replace+fsync |
| S3-B7 | result_exporter 增量须微流水线 | §7 撤销自动写入，过渡期显式 bind |

### 二轮复审修订对照（v3）

| # | 二轮阻塞项 | v3 落实 |
|---|---|---|
| S3-B1+ | 写路径覆盖清单准入，未接入即拒绝 | §1 write_path_registry.json + 退出码 5 + 三层防御 |
| S3-B2+ | mtime/size 非充分证据，须三重逻辑 hash | §2 pre==post==copy 全必需表 |
| S3-B3+ | 键唯一性须实证；RSS 监控 | §3 实测 18/19 唯一 + strategy_events 全列退化排序 + 双轨内存监控 |
| S3-B4+ | DECIMAL 与转义须定案 | §4 定点字符串 + 五条转义规则写死 |
