# 现场处置流程的隔离验证证据（备份 / 重建 / 恢复 / 合并）· 2026-11-14

> 目的：为《修复完成通知 N-20261027-01》附件《现场处置流程》提供**可复核的执行依据**：①哪些 DuckDB 命令在目标版本上真实可用 ②各手段的对象（数据/约束/索引/视图/序列）覆盖度 ③备份与回退点是否可实证 ④备份后新增数据如何保全/合并 ⑤`git merge-base --is-ancestor` 的退出码语义与浅克隆坑。
> 边界：**全程只在临时目录的合成库上执行**；**未接触生产库 `data/quantstudio.db`**（由 daemon 持锁）；**未改任何生产代码**。
> 验证固件：`.specify/bugs/firefly-engine-defects-20261025/_iso_backup_rebuild_validate.py`（一次性验证脚本，非交付代码）
> 原始日志：`_iso_validation_1_5_6.txt`（目标版本）／`_iso_validation_1_4_5.txt`（回退位），同目录。

---

## 一、版本事实（目标版本的判定源，不是印象）

| 事实 | 读数 | 来源 |
|---|---|---|
| 客户/本仓**目标版本** | `duckdb==1.5.6` | 根 `requirements.txt` L1；根 `pyproject.toml` L20/L50 |
| 放行集合 | `1.4.5 ≤ x < 1.5` 或 `x == 1.5.6`（**唯一判定源**） | `quantstudio/pipeline/duckdb_version_policy.py`（`ALLOWED_RANGES`） |
| **回退位** | `1.4.5`（显式 `pip install "duckdb==1.4.5"`） | 根 `requirements.txt` L6 |
| **1.4 线 EOL** | **2026-11-17** | 根 `requirements.txt` L6 |
| 本机安装版 | `duckdb 1.5.6`（`python -c "import duckdb"`） | 实测 |
| 隔离安装的回退位 | `duckdb 1.4.5`（`pip install --target <临时目录>`，**未改本机环境**） | 实测 |

> ⚠ 相关条目 `knowledge/qmt/QD-2026-0013.md` 仍以「duckdb **1.4.x** 平台事实」为题，与当前放行版本不一致——**建议另案更新**（本轮未改已闭条目）。

## 二、文档核证（DuckDB 官方文档）

| 文档结论 | 出处 |
|---|---|
| **回收空间**用「整库复制」：`ATTACH 'db1.db' AS db1; ATTACH 'db2.db' AS db2; COPY FROM DATABASE db1 TO db2;`——原文说明这是删除行后回收空间的方式，**`CHECKPOINT` 与 `VACUUM` 都不回收空间** | DuckDB 官方文档 · *Reclaiming space*（footprint_of_duckdb） |
| `EXPORT DATABASE 'dir'` / `IMPORT DATABASE 'dir'`：保存/搬迁整个库状态（schema + 数据），可用于跨存储格式迁移 | DuckDB 官方文档 · *Internals/Storage*、*Migration* |
| `CHECKPOINT` 自 v1.5.1 起是 **Delta / Unity Catalog 表的 compaction**，**不是** WAL checkpoint | DuckDB 官方文档 · *CHECKPOINT*（core_extensions/delta、unity_catalog） |

## 三、隔离实测读数（1.5.6 与 1.4.5 **两版本结果一致**）

> 合成库含：带 PK/NOT NULL 的表、带 CHECK 的表、UNIQUE 索引、视图、序列、一张 20 万行后删半的表（制造「删除后空间不回收」）。

| 段 | 读数 | 结论 |
|---|---|---|
| **指纹鉴别力自检** | 改一行 ⇒ 指纹 `dc6aff99…` → `eb615943…`；改回 ⇒ 复原为 `dc6aff99…`（`selfcheck_has_power=True`） | 指纹判据**具备鉴别力**，「相等」不是恒等假信号（v1 因 SQL 未绑别名产生的 ERR 串相等结论**已作废**） |
| **WAL 生命周期** | 连接打开时 `.wal` **存在**；正常关闭后 `.wal` **消失**、`.db` 由 1,585,152 B 增至 1,847,296 B | 干净关闭即把 WAL 归并进主库 ⇒ **停机后只拷 `.db` 是一致快照**（WAL 一致保全的机制依据） |
| **备份内容校验** | 副本 sha256 = 源 sha256（`sha256_equal=True`） | **逐文件内容校验**可行（总量相等不足，见 §四） |
| **隔离恢复验证** | 只读打开副本，逐表行数与指纹**与源库一致**（`restore_matches_source=True`） | 回退点可用性**可实证**，不必只靠字节数 |
| **逐表 CTAS 重建** | 执行成功但 **constraints=[]、indexes=[]**（`object_classes_changed_or_lost=["constraints","indexes"]`）；库体 **2,895,872 B > 源 1,847,296 B** | **CTAS 不可用**：约束与索引全丢、且**不回收空间反而变大**；另：把视图当表处理会直接报 `Catalog Error: Existing object v_sw is of type View` |
| **COPY FROM DATABASE** | OK；对象清单 **零差异**（`[]`）；数据指纹一致；目标库 **1,585,152 B < 源 1,847,296 B** | **首选路线**：保对象且回收空间 |
| **EXPORT + IMPORT** | OK；导出产物＝`schema.sql` + `load.sql` + 各表 CSV；导入后对象清单 **零差异**（`[]`）、数据指纹一致；库体 1,585,152 B | **备选路线**：保对象且回收空间（中间目录占 ≈1× 空间，计入预算） |
| **备份后新增数据** | 备份态 `stock_daily` 2 行 / `sw_daily` 0 行；备份后新增后源库 3 行 / 1 行 ⇒ **新增行不在备份内** | 合并必要性由「是否彻底停机」决定 |
| **幂等重放保全** | 重放新增行后 **指纹与源库完全一致**（`replay_equals_live=True`）；再重放一次**行数与指纹不变**（`idempotent_unchanged=True`） | 「幂等 upsert 重放」是可行的保全路径；**手工行级合并不做** |
| **PRAGMA database_size** | 恢复副本读数可用（`restored / 1.7 MiB / 262,144 B …`） | 体积读数可取，用于预算与前后对比 |

## 四、字节数相等 ≠ 回退点可用（判据改写依据）

- v1 流程件把「源目录与备份目录**总字节数相等**」当作备份合格判据——**本轮已撤销该判据**；
- 现行判据（阶段 2.2–2.4）：**逐文件 sha256 清单一致** ＋ **隔离目录只读打开后逐表行数与指纹一致**（`RESTORE_PROBE PASS`）⇒ 才称回退点可用；
- 支撑：字节数相同的两个文件也可能一份是拷贝中断的半份；只有「真的打开并逐表比对」才排除该情形。

## 五、`git merge-base --is-ancestor` 退出码语义与浅克隆坑（实测）

| 场景 | 退出码 | 输出 | 判读 |
|---|---|---|---|
| 是祖先（`5655fb4` vs `origin/main`） | **0** | 无 | 是祖先 |
| 不是祖先（本地未推提交 vs `origin/main`） | **1** | **静默（输出长度 0）** | **不是祖先**——git 对此不打印任何内容，易被误读为「命令没跑」 |
| 对象不存在（无效 SHA） | **128** | `fatal: Not a valid commit name …` | **出错**，非「非祖先」 |
| **浅克隆**（`--depth 1`）中对老提交执行同一命令 | **128** | `fatal: Not a valid object name …`；`git rev-parse --is-shallow-repository` = **true** | 与「对象真的不存在」**同形** ⇒ **不能据报错断言远端没有该提交**；处置＝`git fetch --unshallow origin` 后重验 |

- 本店 `remote.origin.fetch` refspec＝`+refs/heads/*:refs/remotes/origin/*`（**全分支**，未收窄）；客户侧可能收窄 ⇒ 需 `git config --get-all remote.origin.fetch` 核对。

## 六、祖先关系分列实测（与「被引用包含」分开）

方法：`git fetch` 刷新引用后，对各远端**默认分支**逐笔 `git merge-base --is-ancestor <sha> <ref>`。

| 远端 | 目标分支 | tip | 七笔判定 |
|---|---|---|---|
| `origin`（quantstudio-plus · 客户仓） | `origin/main` | `fac8221` | **ANCESTOR ×7** |
| `eco`（主仓镜像） | `eco/main` | `fac8221` | **ANCESTOR ×7** |
| `hybrid`（`C:/QuantStudioHybrid`） | `feat/rust-hybrid`（默认分支） | — | **NOT_ANCESTOR ×7** |
| `hybrid` | `main` | `366596d` | **NOT_ANCESTOR ×7** |

- 七笔＝`5655fb4` / `2707414` / `f515263` / `62196c3` / `76e9e50` / `111552d` / `16cc5b7`；
- 「被四个远端引用包含」（`git branch -r --contains`）＝**引用可达**；上表＝**是否在该远端目标分支的历史上**，两者不同、不可互相代替；
- `hybrid` 的事实**只是引用关系**：铁律 14 的同步门针对**主仓→trading（QuantStudio-trading）** 回测部分同步（与 `hybrid` 不是同一对象），铁律 20 的三端对齐**有激活条款**（副本 Rust 段实施启动之日起生效）⇒ **不能仅凭 `hybrid` 未包含即宣布同步/改写义务已触发**，须由相应线确认激活状态。

## 七、结论对流程件的约束（摘要）

1. 备份判据＝**停机后拷贝 + 逐文件 sha256 + 隔离恢复验证**，不是总字节数；
2. 重建手段＝**`COPY FROM DATABASE`（首选）/ `EXPORT+IMPORT`（备选）**，**禁用逐表 CTAS**；
3. 重建验收＝**七类对象逐类 diff 为空**（数据/约束/索引/视图/序列/设置/依赖）；
4. 备份后新增数据＝彻底停机则无需合并；否则**幂等 upsert 重放**（已实证逐值一致且幂等），手工合并不做；
5. 空间预算＝**峰值 ≈3×（COPY）～4×（EXPORT）**；
6. 命令适用版本＝**1.5.6 / 1.4.5 已实测**；**其他版本须先在副本重测**（1.4 线 **2026-11-17 EOL**）；
7. 客户终验批数判据＝**按行数**（≈1785 万行；1 万/批 ≈ 893 批），不按 357 批。

---

## 八、补测（2026-11-15 · R03 审核三条）：两条路径的依赖 · 无写者 · 回放恢复条件

**脚本（仓内一次性固件，非交付代码）**：`_iso_copy_export_dep_replay_validate.py`（v4）· `_iso_copy_clean_check.py`（v5）
**日志**：`_iso_v4_1_5_6.txt` · `_iso_v4_1_4_5.txt` · `_iso_v5_1_5_6.txt` · `_iso_v5_1_4_5.txt`
**环境**：临时目录合成库（表含 PK 表／唯一索引表／无键表／视图／序列／含删除历史的大表），**不触生产库**；**duckdb 1.5.6 与 1.4.5 两版读数完全一致**。

### 8.1 依赖与前置条件

| 项 | 实测读数（两版一致） | 说明 |
|---|---|---|
| `COPY FROM DATABASE` | `copy_from_database OK` | 源库须以**具名 catalog ATTACH** 后执行；数据指纹一致（干净比对见 8.2） |
| 同一文件重复 ATTACH | `Binder Error: Unique file handle conflict: Cannot attach "src" - the database file "..." is already attached by database "w"` | **同一 .db 文件在一个进程里只能 attach 一次**；v3 曾因此整轮失败（见 §十） |
| 目标库已存在且非空 | `copy_into_nonempty_target OK_no_error` | **不报错**，但旧内容不在复制范围 ⇒ **须用新建空文件**，不得复用旧目标 |
| `EXPORT DATABASE` | `export_database OK`，产物＝`schema.sql` + `load.sql` + 各表 CSV | 导入后对象零丢失、数据指纹一致 |
| **已 ATTACH 的次库内容** | `secondary_table_included False`（导出目录中**无**次库表的 CSV） | ⚠ **`EXPORT` 只导出当前主库**；数据若在附库里**须逐库分别导出** |
| 中文路径 | `export_to_cn_path OK` / `import_from_cn_path OK` / `cn_path_data_fingerprints_equal True` | Windows 中文目录**不是障碍** |
| 库体大小 | 两次 fixture 方向相反（一增一减） | ⇒ **库体大小不可作判据**，以**指纹 + 对象清单**为准 |
| 重建目标可写 | `rebuilt_target_writable OK` | 重建产物可继续作为生产库使用 |

### 8.2 干净数据比对（修正 v4 的探针污染）

- v4 的 `copy_data_fingerprints_equal False` **不是 COPY 丢数据**：该轮在比对**之前**先往目标库插入了一行可写性探针，比对必然不等。
- v5 干净重测（先比对、后探针）：`copy_clean_data_fingerprints_equal True`；`copy_objects_changed_or_lost []`。
- 与 v2 的 `COPY FROM DATABASE` 段（数据指纹一致、对象零丢失、目标体更小）结论一致。

### 8.3 无写者判定（跨进程文件锁）

| 场景 | 实测读数 |
|---|---|
| 写者持锁时**打开只读连接** | `ERR IO Error: Cannot open file "...": 另一个程序正在使用此文件，进程无法访问。` |
| 写者持锁时**第二个写连接** | `ERR`（同一条错误）——**写入不发生**，无静默并发写 |
| 写者进程退出后再写 | `OK`（锁已释放） |

⇒ **停机判据＝写者进程已退出且文件锁已释放**；「读连接能打开」即锁释放的直接证据。⇒ 备份与重建期间库上不得有任何其他写者。

### 8.4 回放恢复条件

| 场景 | 实测读数 | 结论 |
|---|---|---|
| 有 PRIMARY KEY 的表 upsert | `replay_on_pk_table OK`，指纹与 live 一致 | ✅ 可重放 |
| **有 UNIQUE 索引（非主键）的表 upsert** | `replay_on_unique_index_table OK`，指纹与 live 一致 | ✅ **唯一索引同样可用** |
| **无任何键的表 upsert** | `ERR Binder Error: The specified columns as conflict target are not referenced by a UNIQUE/PRIMARY KEY CONSTRAINT or INDEX` | ❌ **不能回放**（须整表重灌或排除） |
| delta 物化为 parquet 后回放 | `replay_via_parquet_delta OK`，指纹与 live 一致 | ✅ 可复核的搬运路径 |
| 同一 delta 重放第二次 | `replay_idempotent OK`，指纹不变 | ✅ 幂等 ⇒ 中断可安全重跑 |
| 逐表最终比对 | 有键表 `equal=true`；**无键表 `equal=false`**（其新增行未回放） | ⇒ **「未回放表」必须单列清单** |

---

## 九、范围与未测面（不得外推）

- **未测**：内存峰值；447 万行 × 42 列 / 60GB 级超大数据量（流程件 2.3 的空间预算仍是**估算**）。
- **未测**：复制/导出**过程中**持锁进程继续写入的行为（只测到锁层面读写均被拒；客户侧应靠阶段 1 停机规避）。
- **未测**：多库同时导出的脚本化做法（只测到「次库不进导出」这一事实）。
- **未测**：其他 DuckDB 版本（仅 1.5.6 与 1.4.5）；**1.4 线 2026-11-17 EOL**。
- **已测范围**：临时目录合成库，覆盖 PK 表／唯一索引表／无键表／视图／序列／含删除历史的大表／中文路径／跨进程锁。

---

## 十、本轮自我修正记录（2026-11-15）

| # | 缺陷 | 现象 | 处置 |
|---|---|---|---|
| 1 | **v3 脚本在同一进程里对同一 .db 先 ATTACH 后重复 ATTACH** | `Unique file handle conflict` ⇒ COPY/EXPORT 段整轮失真 | v4 改为**每个子测用独立文件副本**、连接全部显式 close、每个子测 try/except 并打印 |
| 2 | v3 **同文件先 read-write 后 read_only** | `Connection Error: Can't open a connection to same database file with a different configuration than existing connections` | v4 子测分离；需要跨配置时**先复制再打开** |
| 3 | v3 用**相对路径** `EXPORT DATABASE 'nope'` | 在仓库根留下垃圾目录 `./nope`（schema.sql/load.sql/各表 CSV） | **已确认为本次误建并删除**；v4 起 EXPORT 一律绝对路径 |
| 4 | v4 `copy_data_fingerprints_equal False` | 被自身可写性探针污染（比对前先插了一行） | v5 **干净重测＝True**；该 False **作废**，不作为任何结论依据 |
| 5 | v3 未打印 `SEC_Z` 即退出 | 脚本中途异常终止、读数不完整 | v4 起最外层 try/except 必打印 `FATAL`/`SEC_Z` |

> **教训沿用**：凡以「相等/一致」作判据的函数，先做**鉴别力自检**（改一个输入 ⇒ 判据必须变化），否则「恒等/被污染的真」会被读成「通过」。

---

## 十一、写者证据补测（2026-11-15 晚 · R04 第一条）：锁互斥矩阵 + 进程身份 + 句柄独占探针

**脚本**：`_iso_writer_probe_attached_db_validate.py`（跨进程锁矩阵）· `_lock_holder.py`（持锁辅助进程）
**日志**：`_iso_v6_1_5_6.txt` · `_iso_v6_1_4_5.txt`（两版读数一致）＋ 本机 Windows 句柄探针实测（PowerShell）

### 11.1 跨进程锁互斥矩阵（holder 持锁方式 × opener 打开方式）

| holder | opener | 结果 |
|---|---|---|
| 读写 | 读写 | ❌ `IO Error: Cannot open file "...": 另一个程序正在使用此文件，进程无法访问。` |
| 读写 | 只读 | ❌ 同一条错误 |
| **只读** | **读写** | ❌ **被拒**（只读持有者**也**阻断读写打开） |
| **只读** | **只读** | ✅ **通过**（两个只读可并存） |

> **结论**：**只读探针只能证明「此刻没有进程以读写方式持有该库」**；**只读持有者不阻断只读探针** ⇒ **「只读能打开」不足以证明没有进程持有该库**（前稿表述过强，已撤回）。
> **附带结论**：**只读持有者会阻断读写打开** ⇒ 备份/重建窗口内该库文件**不得被任何进程打开（含只读）**。

### 11.2 两种探针在同一现场状态下的读数（**鉴别力自检**）

| 现场状态 | 只读探针（DuckDB） | 句柄独占探针（`FileShare.None`） | 进程身份清点（CIM） |
|---|---|---|---|
| 无持有者 | OK | **OK**（无进程持有句柄） | — |
| **只读持有者**（PID 18596，自报 `HOLDING … ro`） | **OK（漏检）** | **FAIL**（`The process cannot access the file '…' because it is being used by another process.`） | 命中 PID/映像名/启动时间/命令行 |
| 读写持有者（PID 53924，自报 `HOLDING … rw`） | **FAIL** | **FAIL** | 命中 PID/映像名/启动时间/命令行 |
| 持有者退出后 | OK | **OK** | 命中数归零 |

⇒ 两个探针**都有鉴别力**，但**覆盖范围不同**：只读探针只覆盖读写持有者；**句柄独占探针覆盖全部持有者（读/写皆算）**，且失败信息直接指出「文件正被另一进程使用」。

### 11.3 进程身份证据（可执行方法与实测）

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match 'quantstudio\.pipeline\.daemon|main_gui|quantstudio\.db') } |
  Select-Object ProcessId, Name, CreationDate, CommandLine | Format-List | Out-String
```

- 实测可定位到持有者：PID / 映像名（python.exe）/ **启动时间** / **完整命令行**（含库路径与模式）。
- ⚠ **假阳性**：按脚本文本匹配时，**包装进程**（`powershell.exe`、`node.exe`，其命令行含整段脚本文本）也会被命中——本机实测 `identity_matches=3`（2 包装 + 1 真持有者）⇒ **匹配面必须双向验证**，并剔除本会话自身与包装进程。
- ⚠ **漏匹配**：命令行有模块形态与脚本路径形态两种，**两种都要查**。
- 本机**无 Sysinternals `handle.exe`**（`handle_exe_present => []`）⇒ 句柄级**进程清单**路径本轮未实测，流程件已按「句柄独占探针 + 进程身份清点」双证降级口径写。

---

## 十二、附库逐库保全补测（2026-11-15 晚 · R04 第三条）

| 项 | 实测读数（两版一致） |
|---|---|
| 枚举 | `PRAGMA database_list;` → `[["20637","master","…\\master.db"],["22076","sec","…\\attached.db"]]` ⇒ **每个库＝独立 `.db` 文件** |
| 主库导出 | `export_main_files => ["load.sql","m_.csv","schema.sql"]`；`export_main_contains_sec_table => False` ⇒ **附库内容不进主库导出** |
| 逐库导出 | `USE sec; EXPORT DATABASE '…'` → `["load.sql","s_.csv","s__1.csv","schema.sql"]` ⇒ **产物含该库的表** |
| 逐库导出后还原 | `IMPORT DATABASE '…'` → 表清单 `["s1","s2"]`，指纹与源一致 ⇒ **可还原** |
| 逐库复制（COPY 路线） | `COPY FROM DATABASE sec TO dstcat` → **OK**，目标库含 `s1`/`s2`，指纹一致 |
| ⚠ 命名陷阱 | 导出 CSV 文件名被规范化（`s1`→`s_.csv`、`s2`→`s__1.csv`）⇒ **表名以 IMPORT 后的清单为准，不得拿 CSV 文件名当表名** |

---

## 十三、本轮（2026-11-15 晚）自我修正记录

| # | 缺陷 | 现象 | 处置 |
|---|---|---|---|
| 1 | pwsh 拉持锁进程时 `Start-Process python -ArgumentList '-c', "…"` **引号被吃掉** | 持有者**从未真正打开库** ⇒ 据此得到的「句柄独占探针通过」读数**无效** | 改用持锁辅助脚本 `_lock_holder.py`，并**核对其自报 `HOLDING <pid> <mode>`** 后重测；前一组读数**作废** |
| 2 | v6 脚本用 `s1.csv` 判断「附库表是否进导出」 | **判据写错**（导出文件名被规范化成 `s_.csv`）⇒ 该布尔读数**作废** | 以 `export_sec_files` 列表 + IMPORT 还原成功两项替代，并在流程件 A.6.2 写入命名陷阱警示 |
| 3 | 进程身份匹配按脚本文本匹配 | 命中**包装进程**（node.exe/powershell.exe）⇒ `identity_matches=3` | 写入流程件 A.5.2「匹配面双向验证 + 剔除包装进程」警示 |
| 4 | 前稿「读连接能打开＝锁已释放（直接证据）」 | **过强**（只读持有者不阻断只读探针） | **撤回**，改为**句柄独占探针 + 进程身份清点**双证 + 鉴别力自检（A.2 / A.5） |

---

## 十四、终审对照索引（2026-11-15 · R05「四件与对照证据齐后终审」）

> **索引件**：`docs/evidence/firefly-site-procedure-review-index-20261115.md` —— 登记四件的路径/版本/**内容 sha256**、对照证据清单（脚本＋原始日志）、**判据→证据位置映射**与未测面清单。**sha256 一律内容寻址（不用 mtime）**。

### 14.1 四件（本轮交付 · 全部工作树未提交）

| 件 | 路径 | 版本 |
|---|---|---|
| 客户通知稿 | `docs/handoff/customer-notice-firefly-defect-batch-20261027.md` | **rev3.3** |
| 现场处置流程 | `docs/handoff/firefly-site-recovery-procedure-20261114.md` | **v5** |
| 变更依据 | `docs/handoff/firefly-notice-revision-rationale-20261114.md` | **v4** |
| 隔离实测证据（本件） | `docs/evidence/firefly-site-procedure-isolation-validation-20261114.md` | **v4** |

### 14.2 对照证据（脚本＋原始日志 · 一次性固件，非交付代码）

| 脚本 | 覆盖内容 | 原始日志 |
|---|---|---|
| `_iso_backup_rebuild_validate.py` | v2：备份/WAL/隔离恢复/CTAS 对照/EXPORT+IMPORT/COPY FROM DATABASE/备份后新增数据 | `_iso_validation_1_5_6.txt` · `_iso_validation_1_4_5.txt` |
| `_iso_copy_export_dep_replay_validate.py` | v4：两条路径依赖 · 无写者（跨进程）· 回放恢复条件 | `_iso_v4_1_5_6.txt` · `_iso_v4_1_4_5.txt` |
| `_iso_copy_clean_check.py` | v5：COPY 干净比对（修正 v4 探针污染）· 次库不进导出 · 中文路径 | `_iso_v5_1_5_6.txt` · `_iso_v5_1_4_5.txt` |
| `_iso_writer_probe_attached_db_validate.py` | v6：2×2 锁互斥矩阵 · 探针鉴别力自检 · 附库逐库保全 | `_iso_v6_1_5_6.txt` · `_iso_v6_1_4_5.txt` |
| `_lock_holder.py` | 持锁辅助进程（自报 `HOLDING <pid> <mode>`，用于核对持有者确已打开） | 见 §十一·11.2 的本机 Windows 实测记录 |

> v3 轮（`_iso_v3_*.txt`）与 `_iso_validation_output.txt` 属**已撤回轮次**的日志，仅作留痕，不得据以引用结论。

### 14.3 判据 → 证据位置映射（终审用）

| 判据 | 证据位置 | 读数 |
|---|---|---|
| 备份总字节数相等不足 | §二 · 流程件 2.2/2.4 | 逐文件 sha256 + `RESTORE_PROBE` |
| 重建不能靠逐表 CTAS | §三 · 流程件阶段 3 导语 | CTAS：constraints=[] indexes=[]，库体 2,895,872 B > 源 1,847,296 B |
| 两条路径对象零丢失 | §八.1 · 流程件 A.1 | `copy_objects_changed_or_lost []`；`import_objects_changed_or_lost []` |
| 附库逐库保全 | §十二 · 流程件 A.6 | `PRAGMA database_list` 两库；`copy_from_attached_database OK`；`import_from_sec_export OK` |
| 只读探针不足 | §十一.1 · 流程件 A.2 | 2×2 矩阵 ro×ro ✅ / ro×rw ❌ |
| 独占探针必失败 | §十一.2 · 流程件 A.5.1/A.5.3 | ro 持有者 FAIL · rw 持有者 FAIL · 释放后 OK |
| 进程身份可取证 | §十一.3 · 流程件 A.5.2 | 命中 PID 18596 / 53924（含启动时间与命令行原文）；包装进程假阳性 `identity_matches=3` |
| 四证合取 | 流程件 A.7（**流程性规定**） | 证①✔实测 · 证②单次探针✔实测、**观察窗未实测** · 证③✔实测 · 证④✔实测 |
| 无键表只整表重灌或明确排除 | §八.4 · 流程件 A.3-6 | `replay_on_keyless_table ERR`；无键表 `equal=false` |
| exit 码三态 · 浅克隆 | §五 · 通知件 §三第 3 步 | exit 0/1/128；浅克隆同形 128 ⇒ 缺对象≠远端没有 |

### 14.4 未测面（终审请一并看）

- **观察窗时间参数**（流程性规定，未实测）；**内存峰值/超大数据量**（未测）；**复制中并发写**（未测）；**多库同时导出**（未测）；**`handle.exe` 句柄级清单路径**（本机无该工具，未测）；**其他 duckdb 版本**（仅 1.5.6 / 1.4.5；1.4 线 2026-11-17 EOL）。

---

## 十五、v7 补测（2026-11-15 晚 · R06 剩余项）：竞态 · 冻结 · 逐库三核

**脚本**：`_iso_race_freeze_perdb_validate.py`　**日志**：`_iso_v7_1_5_6.txt` · `_iso_v7_1_4_5.txt`（两版读数**完全一致**）

### 15.1 探针关闭后的再打开竞态（TOCTOU）

| 情形 | 实测读数 | 含义 |
|---|---|---|
| 写者持续持锁时做文件级拷贝 | `SEC_F1 copy_while_rw_holder => ERR PermissionError: [Errno 13] Permission denied: '…\\f1_src.db'` | 竞态窗口表现为**响亮失败**（系统直接拒绝拷贝），**不会静默产出错误副本** ⇒ 失败即停，**不要重试** |
| 写者「写完即退」（探针之后、拷贝之前进入又退出） | `SEC_F2 src_sha256_eq_backup_sha256 => True`；`SEC_F2 backup_matches_stop_time_fingerprint => False`（停机指纹 rows=2/fp=07f9f004…；副本 rows=3/fp=400ce729…） | ⚠ **源与副本 sha256 相等检不出静默推进**；**只有与「停机时冻结指纹」比对才能检出** ⇒ 阶段 1.4 冻结指纹是必需项 |
| 在**同一条只读连接**内完成导出/拷贝 | `SEC_G writer_while_ro_connection_open => ERR IO Error: …另一个程序正在使用此文件…`；`SEC_I export_from_ro_connection_source_unchanged => True`；`SEC_I export_created_wal_next_to_source => False` | 只读连接存续期间外部写者被拒 ⇒ **同连接导出/拷贝可消除该竞态窗口**；且只读导出**不改源库**、**不产生 .wal** |

### 15.2 恢复验证的冻结（只读 vs 读写对照）

| 项 | 实测读数 |
|---|---|
| 只读打开 + 全表扫描前后原备份 sha256 | `SEC_H readonly_scan_sha256_unchanged => True` |
| 只读扫描是否在原备份旁产生 `.wal` | `SEC_H readonly_scan_created_wal => False` |
| 对照：读写打开并写入后字节是否变化 | `SEC_H rw_open_write_sha256_changed => True` |
| 对照组事后能否看到 `.wal` | `SEC_H rw_open_write_created_wal => False`（**`.wal` 在正常关闭时已归并消失** ⇒ 事后检查 `.wal` 存在性**不能**证明「未发生写入」，须用**字节 sha256** 判据） |

### 15.3 逐库三核（数据 · 对象 · 增量）原型

`SEC_J per_db_three_checks`（两版一致）：

| 表 | 备份态指纹 | live 指纹 | 增量 | 处置 |
|---|---|---|---|---|
| `keyed`（有主键） | rows=2 · fp=07f9f004… | rows=3 · fp=99f5f40b… | **新增 2 行 + 改值 1 行** | **可幂等重放**（唯一/主键覆盖） |
| `keyless`（无键） | rows=2 · fp=7b43f775… | rows=3 · fp=37afd141… | **新增 1 行（不可行级回放）** | **排除 ⇒ 恢复不完整**，损失行数须列明并由**客户明确接受**（**量化口径见 §16.1**：`count(live EXCEPT backup)` 是集合语义且方向为「新增」，**不得当损失行数**；须用多重集 `rows_lost = sum(greatest(bc-lc,0))`，并同时给 `rows_added` 与受影响行数） |

### 15.4 本轮自我修正（v7）

| # | 缺陷 | 处置 |
|---|---|---|
| 1 | v7 首轮在拷贝处抛 `PermissionError` 中断 | **该异常本身即发现** ⇒ 改写 v7b，把三条竞态路径分开测，「拷贝失败」作为读数记录（SEC_F1） |
| 2 | 跨库差集用错连接（`livecat` 挂 A、查询发 B） | 1.5.6 报 `schema "livecat" does not exist`、1.4.5 报 `Table with name keyed does not exist`（同根因）⇒ 改同连接内 ATTACH+差集，两版读数一致 |
| 3 | 对照组事后未见 `.wal` | 如实记录：`.wal` 关闭时即归并 ⇒ **事后 `.wal` 检查不能作未写入证明**，须用字节 sha256 |

## 十六、v8 补测（2026-11-15 · R07 退修）：冻结指纹消费 · 原件零打开 · 损失量多重集口径

**脚本**：`_iso_freeze_reference_multiset_validate.py`　**日志**：`_iso_v8_1_5_6.txt` / `_iso_v8_1_4_5.txt`（除 `SEC_Z workdir` 行外**逐行一致**，两版均 `SUMMARY OK`）

### 16.1 损失量：`count(live EXCEPT backup)` 到底量的是什么

| 场景（相对备份态） | `except_count` | `multiset_delta_rows` | `rows_only_in_live` | `rows_only_in_base` | 判读 |
|---|---|---|---|---|---|
| 副本新增 1 行**重复行**（与既有行逐位相同） | **0** | 1 | 1 | 0 | **完全看不见「多了」** |
| 1 行**改值** | **1** | 2 | 1 | 1 | 只见**新值侧**；旧值丢失要靠 `rows_only_in_base` |
| 1 行改值 **+** 1 行**删除**（累积） | **1** | 3 | 1 | **2** | **低估一半**：真实丢失 2 行，差集只报 1 |

**结论（写入流程件 A.3-8）**：`EXCEPT` 是**集合**语义且**单向**（live 减 base）⇒ 它度量的是「live 独有行（去重）＝**新增/改后的新值**」，**不是损失**；且**给不出方向**。损失必须用多重集 `sum(greatest(bc-lc,0))`，并同时报 `rows_added` 与受影响行数；无键表无法区分「改值」与「删+增」。

### 16.2 冻结指纹消费式验证（原备份零打开）

| 项 | 读数 |
|---|---|
| 主路径是否用 duckdb 打开原件 | `source_opened_by_duckdb = False` |
| 副本指纹与冻结指纹比对 | `{"keyed": {"frozen_rows": 3, "copy_rows": 3, "fp_match": true}, "kless": {... "fp_match": true}}` |
| 验证期间原件 sha256 | `source_sha256_unchanged = True` |
| 验证期间目录条目（含 `.wal`） | `dir_unchanged_during_verify = True` |
| 备选路径（参照复制品 vs 待验副本） | `{"keyed": {"fp_equal": true}, "kless": {"fp_equal": true}}`，`source_sha256_unchanged = True` |
| 负向对照（改副本一行） | `negative_control_fp_match = False`（判据非恒真，具备鉴别力） |

### 16.3 为什么「只读打开」不能算「不打开」

| 观测项 | 读数 |
|---|---|
| `size_unchanged` | True |
| `mtime_unchanged` | True |
| `wal_exists` | False |
| **`atime_changed`** | **True** |

⇒ 字节与 `.wal` 可以完全不变而**打开确实发生过**；`atime` 又受系统「上次访问更新」策略控制、不可依赖。**唯一可复核判据＝原备份路径从未进入任何 `connect`/`ATTACH`**（流程件 §2.4.3 五项 + 脚本原文回传）。

### 16.4 本轮自我修正（v8）

| # | 缺陷 | 现象 | 处置 |
|---|---|---|---|
| 1 | 限定名整体加引号 | `Table with name b.kless does not exist` | 改按段加引号 `q()` |
| 2 | 模板自带引号 + `q()` 再加一层 | `Parser Error: zero-length delimited identifier` | 模板去引号，只由 `q()` 负责 |
| 3 | 判读文字先写成「改值 EXCEPT=2」 | 实测为 **1** | 按实测改正，并把结论升级为「度量的是新增侧，不是损失侧」 |
| 4 | 负向对照先改副本再做参照比对 | `SEC_G` 出现 `fp_equal=false`（自查污染） | 调换顺序：先 G 后 F |

## 十七、v9 补测（2026-11-15 · R08）：整行指纹的语义边界（断言式）

**脚本**：`_iso_varchar_boundary_validate.py`　**日志**：`_iso_v9_1_5_6.txt` / `_iso_v9_1_4_5.txt`
**方法**：每个子测先给**预期**再算**实测**，不符记 FAIL ⇒ 「预期表」本身是被实测确认过的，不是我口头写的。**15 项断言全部 PASS，`SUMMARY OK`，两版语义读数一致**（唯一差异是 `SEC_T9` 里探针库文件自身的 sha256 前缀——不同版本写出的字节本就不同，关键结论 `equal=true` 相同）。

### 17.1 类型 / 列名 / 列序

`SEC_T1 type_rendering_raw`（1.5.6 原文）：

`{'d': 1.5000, 'f': 1.0, 'r': 1.0, 'bi': 9223372036854775807, 'hi': 170141183460469231731687303715884105727, 'bo': true, 'dt': 2026-11-15, 'tm': '13:45:07', 'ts': '2026-11-15 13:45:07.123456', 'bl': \xDE\xAD\xBE\xEF, 's': txt, 'u': 00000000-0000-0000-0000-000000000001}`

| 断言 | 结果 |
|---|---|
| DECIMAL 保留标度（`1.5000`） | PASS |
| BOOLEAN 渲染 `true` | PASS |
| 文本**含列名**（`'d':`） | PASS |
| 列序变化 ⇒ 指纹变化（`o1` vs `o2`） | PASS（`8b393ea77035f69a…` ≠ `bc4cceeb267cfdbb…`） |
| 行序变化 ⇒ 指纹不变 | PASS（两侧同为 `[3, "d8191359…"]`） |

### 17.2 NULL / 转义 / 浮点特殊值

| 断言 | 读数 |
|---|---|
| NULL vs `''` vs `'NULL'` 三者互异 | `["{'id': 1, 's': NULL}", "{'id': 2, 's': ''}", "{'id': 3, 's': 'NULL'}"]`，3 个不同指纹 ⇒ PASS |
| 7 类易混淆字符串两两不碰撞（含 `('x,y','z')` vs `('x','y,z')`、引号、换行、制表、反斜杠） | `SEC_T4 escape_raw` 7 行文本互异 ⇒ PASS |
| `nan` / `inf` / `-inf` 互异 | `SEC_T3` 5 行互异 ⇒ PASS |

### 17.3 ⚠ 浮点边界（本轮最硬的一条）

```
SEC_T3b float_row_text ["{'tag': a, 'v': 0.3}", "{'tag': a, 'v': 0.3}"]
SEC_T3b distinct_row_texts 1
SEC_T3b fingerprint [2, "527b59b5650387e6692457a231ab4955"]
```

⇒ `0.1+0.2`（实为 `0.30000000000000004`）与 `0.3` 的 **DOUBLE→VARCHAR 文本相同**；两行除该列外完全相同时，**整行指纹无法区分**。
⇒ **含 DOUBLE/REAL 列的表，`md5(CAST(t AS VARCHAR))` 不具单射性**；逐位证据须改用 `format('{:.17g}', v)` 之类全精度渲染并声明口径。

### 17.4 lost/added 预期表（四场景，实测打勾）

| 场景 | 预期 `rows_lost` / `rows_added` | 实测 | 判定 |
|---|---|---|---|
| 副本多出 1 行**重复行** | 0 / 1 | `{0, 1}` | PASS |
| 1 行**改值** | 1 / 1 | `{1, 1}` | PASS |
| 1 行**删除** | 1 / 0 | `{1, 0}` | PASS |
| 1 行改值 **+** 1 行删除 | 2 / 1 | `{2, 1}` | PASS |

### 17.5 无键表：实体数无定义

```
SEC_T8 keyless_two_scenarios_same_readout
  {"update": {"rows_lost": 1, "rows_added": 1}, "delete+insert": {"rows_lost": 1, "rows_added": 1}}
SEC_T8 readout_identical PASS
```

⇒ 无键表在 `(lost, added)` 上**无法区分「改 1 行」与「删 1 行 + 增 1 行」** ⇒ **「受影响实体数」无定义**，只能报行级多重集。

### 17.6 hash 不变 ≠ 未打开

```
SEC_T9 open_then_hash {"before": "0660b31dbc518fe9", "after": "0660b31dbc518fe9", "equal": true}
```

⇒ 真的用 `connect(read_only=True)` 打开并查询过，**sha256 前后完全相等** ⇒ 「hash 没变」不能证明「没打开」。

### 17.7 本轮自我修正

| # | 缺陷 | 现象 | 处置 |
|---|---|---|---|
| 1 | 首版浮点判据「5 行指纹互异 ⇒ 浮点有区分度」 | 那 5 行的 `id` 不同，指纹必然不同 ⇒ **该断言测不到浮点（假阳性）** | 增 `SEC_T3b`（除浮点列外完全相同）→ 结论**反向**：无区分度 |
| 2 | 首版未打印断言预期值 | 无法自检判据 | v9 起每项 `check(expect, actual)` 并打印原文 |

## 十八、v10（2026-11-15 · R09）未证表范围自检 ＋ 定点 diff

> **性质声明**：本轮**不重复已有实验**。v10 只做「未证表范围」枚举判据的**语法自检**（含负向对照），不产生新的数据一致性判据。

### 18.1 未证表范围：枚举判据自检（两版一致，5 项断言全 PASS）

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'main' AND upper(data_type) IN ('DOUBLE','REAL','FLOAT')
ORDER BY table_name, column_name;
```

| 断言 | 读数 |
|---|---|
| 命中表集合 | `["t_double", "t_mixed", "t_real"]` ✔ |
| `DECIMAL` 表**不进**清单 | `dec_not_in_scope = False` ✔ |
| 纯文本/整数表**不进**清单 | `text_not_in_scope = False` ✔ |
| 一表多浮点列时**逐列列出** | `t_mixed` 列出 `DOUBLE`+`FLOAT` 两列 ✔ |
| 报告形状（回传四栏） | `{"total_tables":5,"text_digest_equal_tables":["t_dec","t_text"],"unproven_tables":["t_double","t_mixed","t_real"],"unproven_columns":{"t_double":["v"],"t_mixed":["v","w"],"t_real":["r"]}}` ✔ |

**结论**：「未证表范围」可由 `information_schema.columns` **稳定枚举**；清单内每表结论一律记「**未证**」，除非采用**另经审定**的比较方法（流程件 §2.4.6 B1）。

### 18.2 定点 diff

本轮五件的实际改动以 unified diff 落盘：`docs/evidence/firefly-r10-targeted-diff-20261115.patch`
（生成：改动前快照 `_pre_r10_snapshot/` → `git diff --no-index` 逐文件导出后拼接；**只含本轮改动**，不含 R08 及更早内容。）

### 18.3 结论强度的口径（本轮唯一实质变更）

| 项 | R08 及以前 | R09 起 |
|---|---|---|
| 「整表一致」的表述 | 可写「整表逐行一致性」 | 只能写「**所选文本摘要**一致」 |
| 含 `DOUBLE/REAL` 的表 | 加免责声明即可 | **要么**经审定的替代比较（B1）、**要么**保持「未证」（B2）、**要么**客户书面接受（B3）；**仅加免责声明＝不通过** |
| 零打开结论 | 似为普适 | **只对已审入口与命令**成立，不外推 |

## 十九、R10 门间传播与四类负例（2026-11-15）

> **性质声明**：本轮**不新增实验**。四类负例的「预期读数」由配方推演给出（下称**预期**），**不声称已实测**；既有 v2–v10 证据原样保留。

### 19.1 四类负例的预期（推演，未实测）

| # | 负例 | 预期读数（应原样回传） | 旧配方为何会错判 |
|---|---|---|---|
| N1 | 冻结清单为空（0 行且无 `#EMPTY_DB`） | `RESTORE_PROBE FAIL(EMPTY_FROZEN_LIST)` | v9 脚本 `for t in frozen` **循环 0 次** ⇒ 直接 `PASS`（空真） |
| N2 | 清单有表、副本无该表 | `MISSING_IN_COPY: ['t3']` + `FAIL(MISMATCH missing=1 …)` | v9 脚本虽会抛异常进 `MISSING_IN_COPY`，但**未把缺表计入总判定**的路径存在退化风险 |
| N3 | 副本多表而清单未记 | `EXTRA_IN_COPY: ['t4','t5']` + `FAIL(MISMATCH … extra=2 …)` | v9 **完全不做**多表核对 ⇒ 清单不全也能 PASS |
| N4 | 双副本相等但均非停机态 | `fp_equal=True` 但冻结比对 `DIFF`/缺失 | 备选路径把「两副本相等」当成了可用性证据 |

### 19.2 门间传播（四处门）

| 门 | 读什么 | 断链条件 |
|---|---|---|
| G1 §2.4.1 | 冻结清单 + 副本 | `FAIL` ⇒ 终止；`PARTIAL_UNPROVEN` ⇒ 带未证清单下传 |
| G2 §2.4.4 | G1 状态 | 非 `PASS`/`PASS_EMPTY_DB` ⇒ 只可写「部分未证」 |
| G3 §3.0 进口门 | G1+G2 + 未证表范围 + 对象清单 | `FAIL` 或 **未证表范围未回传** ⇒ 不进入 |
| G4 §3.3 / A.7 | 三核 + 未证清单 + B1/B2/B3 | 缺未证清单 ⇒ 该库验收不成立；`PARTIAL_UNPROVEN` ⇒ 证④不齐备 |

### 19.3 diff

`docs/evidence/firefly-r11-fourgate-diff-20261115.patch`（只含 R10→R11 改动）。

## 二十、R11：空标记两种构造与 partial 入口真值表（2026-11-15）

> **性质声明**：本轮**静态闭合**，未新增实验（按 R11 边界：不跑备份、不跑重建、不跑客户现场）。下列读数均为**配方预期**，供后续隔离验证逐条比对。

### 20.1 空标记两种构造的预期（C1 表头前 / C2 表头后）

| 构造 | 文件形态 | 预期读数 | v10 行为 |
|---|---|---|---|
| C1 | `#EMPTY_DB` 在**表头前**（line 1） | `PASS_EMPTY_DB(marker_line=1, copy_tables=0)` | 通过（但未查副本 —— 缺陷 a） |
| C2 | `#EMPTY_DB` 在**表头后**（line 2） | `PASS_EMPTY_DB(marker_line=2, copy_tables=0)` | **`TypeError: int() argument must be … not 'NoneType'`**（缺陷 b） |

**v11 的判别力**：两种构造必须给出**同一状态、不同 `marker_line`**；且 `copy_tables=0` 必须来自**实际打开副本**。若把副本换成含表的库，两者都必须变 `FAIL` —— 这是本判据的鉴别力自检。

### 20.2 partial 入口真值表（§3.0 A）

| G1 | 未证表范围 | 破坏性授权 | 进入阶段 3 | 证④ |
|---|---|---|---|---|
| `PASS` | 已回传 | 不需要 | 是 | 齐备 |
| `PASS_EMPTY_DB` | 「空」 | 不需要 | 是（NO-OP） | 齐备（`copy_tables=0`） |
| `PARTIAL_UNPROVEN` | 非空 | **必需** | **仅当有授权** | 不齐备 |
| `FAIL` | — | — | 否 | 不齐备 |

### 20.3 源锚（N4）

- 双副本相等 ⇒ `fp_equal=True`，但**源锚仍为「未证」**；**不能**用任何副本组合补出停机时点；
- 缺冻结指纹 ⇒ 回阶段 1.4 重取；`G1` 不成立 ⇒ 不进阶段 3。

### 20.4 diff

`docs/evidence/firefly-r12-partial-truth-diff-20261115.patch`（只含 R11→R12）。

## 二十一、R12：三项确定的静态闭合（2026-11-16）

> **性质声明**：静态闭合，未新增实验。下列为**配方预期**。

### 21.1 BOM 解析键（C7）

| 场景 | v11 行为 | v12 预期 |
|---|---|---|
| CSV 无 BOM | 正常 | 正常（不变） |
| CSV 带 UTF-8 BOM | 表头去 BOM 后校验通过，但 `DictReader` 读原行 ⇒ 字段名 `\ufefftable` ⇒ 数据行 `r["table"]` 为空 ⇒ **`BAD_ROW(line=2)`**（假失败） | `utf-8-sig` 读入 + `fieldnames=header` ⇒ 与无 BOM **同结果** |

### 21.2 空库范围（C5 / C5b）

| 场景 | 预期读数 | 判定 |
|---|---|---|
| 整库 0 对象（除系统 schema） | `PASS_EMPTY_DB(marker_line=n, scope=whole_db, objects=0)` | 可用（NO-OP），证④齐备 |
| 声明整库为空但副本有 `other.t2` 或视图 | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=n, e.g. [...]))` | 不可用 |
| `#EMPTY_DB_MAIN_ONLY`：main 基表 0、其它对象 >0 | `PARTIAL_EMPTY_MAIN_ONLY(main_base=0, other_objects=n, e.g. [...])` | **整库未证**：不可用、不进阶段 3、不计证④ |

### 21.3 入口真值表（v12）

| G1 | 进入阶段 3 | 备注 |
|---|---|---|
| `PASS` | 是 | — |
| `PASS_EMPTY_DB` | 是（NO-OP） | 须整库取证 |
| `PARTIAL_EMPTY_MAIN_ONLY` | **否** | 整库未证 |
| `PARTIAL_UNPROVEN` | **否（默认）** | 确需 ⇒ §3.6 用户例外申请（业主本人书面），例外不使证④齐备 |
| `FAIL` | 否 | — |

### 21.4 diff

`docs/evidence/firefly-r13-threefix-diff-20261116.patch`（只含 R12→R13）。

## 二十二、R13：对象范围与两个真实分支（2026-11-16）

> **性质声明**：静态闭合，未新增实验。以下为**配方预期**；五源系统函数的列名**未在两版实测**。

### 22.1 「整库为空」的对象范围（五源）

| # | 源 | 查询 | 覆盖的对象 |
|---|---|---|---|
| ① | tables_views | `information_schema.tables`（除系统 schema） | 基表 / 视图 |
| ② | sequences | `duckdb_sequences()`（除系统 schema） | **序列（v12 遗漏项）** |
| ③ | extra_schemas | `duckdb_schemas()`（除 main/系统） | 非默认 schema 本身 |
| ④ | user_types | `duckdb_types()`（除系统 schema，`NOT internal`） | 用户自定义类型 |
| ⑤ | macros | `duckdb_functions()`（`function_type='macro'`） | 宏 |

- `objects=0` 须**五源合计**为 0；任一源不可探测 ⇒ `FAIL(EMPTY_DB_SCOPE_UNVERIFIED(src))`。

### 22.2 两个真实分支（v12 缺陷 → v13 预期）

| 场景 | v12 行为 | v13 预期 |
|---|---|---|
| 只剩一个序列（C5c） | `PASS_EMPTY_DB`（**假空库**） | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=1, by_source={'sequences': 1}))` |
| `MAIN_ONLY` 但 main 基表非空（C5e） | 恒打印 `main_base=0`（**未核实**） | `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES(1, e.g. ['main.t1']))` |
| `whole_db` 且有对象 | 代码 FAIL、表格写「可降级」**（不一致）** | 表格与代码统一：**FAIL，不降级** |

### 22.3 待合成验证项（下一轮授权后执行）

1. 五源查询在 **1.4.5 与 1.5.6** 上是否均可执行（列名 `internal` / `function_type` 等）；
2. C5c / C5e 的实际输出行；
3. `whole_db` 有对象时的 `by_source` 明细格式。

### 22.4 diff

`docs/evidence/firefly-r14-scope-diff-20261116.patch`（只含 R13→R14）。

## 二十三、R14：宏分两类 · by_source 补齐 · 基线前提（2026-11-16）

> **性质声明**：本轮**未运行任何合成验证**（仅提交申请件）。以下为配方预期与审定前提。

### 23.1 宏源拆分

| 源 | 查询过滤 | v13 | v14 |
|---|---|---|---|
| scalar macro | `function_type='macro'` | ✔ | ✔（`macros_scalar`） |
| **table macro** | `function_type='table_macro'` | ✘（漏） | ✔（`macros_table`） |

### 23.2 基线前提（§2.4.1.2）

1. 最小空库六源过滤后计数必须 **全 0**；
2. internal / 默认 catalog 的排除须**可复核**（能打印被排除条目）；
3. **查询失败 ≠ 0**（`EMPTY_DB_SCOPE_UNVERIFIED`）；
4. 1.5.6 与 1.4.5 **独立环境、独立空库**，不混跑。

### 23.3 申请件

`docs/evidence/firefly-synthetic-validation-request-20261116.md` —— 命令、用例与预期输出，**待审定后另行授权运行**。
