# firefly 案 · 现场处置流程（客户侧执行）· **v14**

> **适用**：客户收到《修复完成通知 N-20261027-01》后，**自愿**为排查 #1（大表写入卡死）现场卡顿而执行「回收死元组 / 全量重拉」。
> **执行方＝客户**；本方只出稿并核读数，**不代客户执行任何一步**，也不代客户判断成败。
> **v2 修订（2026-11-14，依据总调度 R02 审核八条）**：备份章重写（使用者确认 · WAL 一致保全 · 逐文件清单与内容校验 · 空间预算 · **隔离恢复验证**）；重建章重写（**禁用 CTAS** · 改用 `COPY FROM DATABASE` / `EXPORT+IMPORT` · **覆盖清单逐类比对** · **备份后新增数据的保全/合并**）；批数判据改行数口径；T2 措辞改为「该次场景实测值」。
> **v3 修订（2026-11-15，依据总调度 R03 审核三条）**：新增**附录 A**（A.1 依赖 · A.2 无写者判定 · A.3 回放恢复条件 · A.4 未测面）。
> **v4 修订（2026-11-15 晚，依据总调度 R04 审核三条）**：①**写者证据改为双证**（A.5 进程身份＋句柄独占探针；**撤回**「只读连接能打开即锁已释放」）；②**无键表不做任何行级回放**；③**附库逐库保全**（A.6）。
> **v5 修订（2026-11-15，依据总调度 R05 审核判据）**：新增 **A.7 四证合取判据**（进程身份 · 锁观察 · 备份 SHA256 · 隔离恢复，**四者齐备**才算数）；探针鉴别力自检升为**硬判据**；观察窗具体化。
> **v6 修订（2026-11-15，依据总调度 R06 审核剩余项）**：①**观察窗仅证明采样时点**（A.5.5 明确：S1/S2/S3 只覆盖各采样时点，采样间隙仍有竞态；观察窗为**必要非充分**条件）；②新增 **A.5.7「探针关闭后的再打开竞态」**（TOCTOU：探针一关闭即释放；三种实测表现与缓解手段：**停机时冻结指纹** · 与冻结指纹比对而非只比 sha256 · **优先在同一只读连接内完成导出/拷贝** · 拷贝失败即停不重试）；③阶段 1 新增 **1.4 停机时冻结指纹**（逐表 rows+fp、每库文件 sha256、WAL 指纹）；④阶段 2.4 强化：恢复验证**只在备份的隔离复制品上执行**，**原备份与 WAL 指纹冻结**（验证前后 sha256 必须一致，不得对原备份做任何写操作）；⑤3.3/3.4 改为**逐库三核**（**数据 · 对象 · 增量**）；⑥无键表**排除**须**明确损失并由客户明确接受**，且**不得视为完整恢复**。
> **v7 修订（2026-11-15，依据总调度 R07 终审退修两条）**：①**§2.4 自相矛盾修正**——旧文字要求「只验证隔离复制品、原备份零打开」，旧代码却仍 `connect(src_db, read_only=True)`（**打开了原备份**）；v7 改为**消费停机时冻结指纹**（主路径 §2.4.1）或**另备参照复制品**（备选 §2.4.2），**原备份全程零打开**，并给出可自查的「零打开清单」（§2.4.3）；②**损失量口径修正**——旧口径 `count(live EXCEPT backup)` 是**集合**语义（去重）且**方向是「live 独有」＝新增**，**度量的根本不是损失**：实测重复行增加时它=**0**（完全看不见）、改值时只看见新值一侧、**改值+删除时它=1 而真实丢失=2（低估一半）**；v7 改为**多重集 + 方向**（`rows_only_in_base`）并附三类反例读数（A.3-7）。
> **v8 修订（2026-11-15，依据总调度 R08 判据）**：①新增 **§2.4.5 整行指纹的语义边界**——逐项给出**类型 / NULL / 浮点 / 转义 / 列序**政策与其实测文本形态，并明确该判据**能区分什么、不能区分什么**（含 `DOUBLE/REAL` 渲染**有损**、`0.1+0.2` 与 `0.3` 文本相同的实测碰撞）；②新增 **A.3-9 lost/added 预期表**（重复/改值/删除/改值+删除四场景，作判据自检用）；③新增 **A.3-10 无键表不得报告「受影响实体数」**（该数无定义，只报行级多重集）；④**§2.4.3 第 4 项降级为必要条件**——实测「只读打开后 sha256 前后相等」，故 **hash 不变不能证明「从未打开原件」**，零打开的充分证据只有路径约束与脚本原文回传。
> **v9 修订（2026-11-15，依据总调度 R09 判据）**：①**收窄结论强度**——「同 schema 同版本整表一致」改为「**所选文本摘要一致**」：本判据只能证明文本摘要相同，**在已证 `DOUBLE/REAL` 渲染有损的前提下，不得据相等确认「精确恢复」**；②新增 **§2.4.6 含 `DOUBLE/REAL` 的表**——须采用**另经审定**的比较方法，**或保持「未证」**；**仅加免责声明不算通过**（三档处置 B1/B2/B3 与措辞红线）；③**零打开结论限定到本流程已审入口与命令**（§2.4.1/2.4.2 及其变体），不外推为「任何方式都未打开」；④新增 **A.3-11「未证表范围」**（枚举 SQL + 三档结论 + 回传义务）。
> **v10 修订（2026-11-15，依据总调度 R10 判据「浮点未证及降级方向收，旧成功出口未受约束」）**：**四处门接入未证表范围与完整性核对**——①**§2.4.1** 脚本重写：冻结清单**不可解析/为空即 FAIL**、**缺表 MISSING / 多表 EXTRA 双向核对**、**结论分级 `PASS` / `PARTIAL_UNPROVEN` / `FAIL`**（`PASS` 只在未证表范围为空时成立）、**真实空库须显式 `#EMPTY_DB` 标记**才可 `PASS_EMPTY_DB`；②**§2.4.4** 判据改四态并把「回退点可用」限定为 `PASS`；③**§3.0 进口门**新增：未证表范围未定 ⇒ **不得进入阶段 3**，且 PARTIAL 进入后的结论上限为「部分未证」；④**§3.3/A.7 证④** 同步：证④齐备要求 §2.4.1 为 `PASS`，**`PARTIAL_UNPROVEN` 不计作证④齐备**；⑤新增 **§2.4.7 四类负例的预期与门间传播表**、**A.3-12 判定矩阵**；⑥**客户 B3 接受不提升为技术 PASS**（仅进「客户接受」栏）。
> **v11 修订（2026-11-15，依据总调度 R11 判据「局部收四门设计，v10 全文终审仍不通过」）**：①**§2.4.1 解析顺序重排**——先**摘出 `#EMPTY_DB` 标记行**（无论在表头前/后/末尾）→ 校验**列名/重复表/对象范围**→ 再**独立打开副本确认「真的 0 张表」**；修掉两处硬缺陷：**(a) 旧版仅凭标记就在 `connect` 之前报 `PASS_EMPTY_DB`（未查副本）**、**(b) 标记写在表头之后会被当数据行 ⇒ `int(None)` 崩溃**；新增 **§2.4.1.1 空标记的两种合法构造与预期读数**；②**统一 §2.4.4 / §3.0 / A.7**：`PARTIAL_UNPROVEN` 使证④不齐 ⇒ **不得自动进入破坏性阶段 3**，须**用户/总调度书面授权**（⚠ **该口径已被 v12 取代**：不得以任何授权入口绕过证④与用户闸；确需者只能走 §3.6 用户例外申请）；**「降级结论」不等于「批准开始重建」**（新增 §3.0 真值表）；③**同备份双副本相等不能补源锚**——任何组合都不行，冻结对象不可得须回 1.4 重取。
> **v12 修订（2026-11-16，依据总调度 R12 判据「局部收，终审未过 · 仍有三个确定问题」）**：①**BOM 解析键统一**——表头去 BOM 后必须把**同一份 `fieldnames` 交给 `csv.DictReader`**（v11 只在表头去掉 BOM、却把原行喂给 DictReader ⇒ 带 BOM 时数据行取不到 `table`，误报 `BAD_ROW`）；②**空库范围补全**——`information_schema.tables` 须查**整库（排除系统 schema）**；**仅 main 基表为空不得报 `PASS_EMPTY_DB`**，一律降级为新态 **`PARTIAL_EMPTY_MAIN_ONLY`（整库未证）**，并新增 `#EMPTY_DB_MAIN_ONLY` 显式声明写法；③**PARTIAL 不得绕过证④与用户闸**——删除「用户/总调度任一书面」入口：§3.0 默认**不进入阶段 3**；确需部分未证重建，**另立 §3.6 用户例外申请**（业主本人书面 + 七项要素），**不预置为已生效配方**，批准前停在阶段 2。
> **v13 修订（2026-11-16，依据总调度 R13 判据「局部收，静态终审未过」）**：①**「整库为空」的对象范围补齐为五源**——`information_schema.tables` **不枚举 sequence**，故仅查表/视图会在「只剩一个 sequence」时误报空库；v13 起要求**表与视图 · 序列 · 非默认 schema · 用户类型 · 宏** 五源合计为 0，且**任一源不可探测即 `FAIL(EMPTY_DB_SCOPE_UNVERIFIED)`（拒绝该未覆盖状态）**；②**`MAIN_ONLY` 分支先核后报**——必须先确认 `main` 基表**确实为 0** 才可打印 `main_base=0`，否则 `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES)`；③**表格与代码统一**——`scope=whole_db` 只要有任何对象即 **`FAIL`（不降级）**；`PARTIAL_EMPTY_MAIN_ONLY` 只在**声明本身就是 `main_only`** 时出现。
> **v14 修订（2026-11-16，依据总调度 R14 判据「收 sequence、MAIN_ONLY 及 whole_db 修正，可进入合成验证命令审定，未授权运行」）**：①**宏源拆为两类**——`function_type='macro'`（scalar macro）**不完整**，须与 **`table_macro`** 分别成源（`macros_scalar` / `macros_table`），二者任一非空即「非空库」；②**`PASS_EMPTY_DB` 输出补齐 `by_source`**（表里要求、代码此前未输出）；③明确**索引/约束依附于表，不单列为独立来源**（不靠凑来源数）；④新增 **§2.4.1.2 空库判据的基线前提**——两版默认 catalog 与 internal 对象须**先由合成空库确认基线为 0**，否则会出现「空库恒 FAIL」；**查询失败不得当 0**（fail-closed 不变）。
> **版本与命令适配**：本文所有 DuckDB 命令已在 **duckdb 1.5.6（当前目标版本）** 与 **1.4.5（回退位）** 上**隔离实测通过**（合成库，不触生产库）——证据见 `docs/evidence/firefly-site-procedure-isolation-validation-20261114.md`。**若您的 DuckDB 版本与上述不同，请先在副本上重测本文命令再执行**。
> ⛔ 标记的步骤为**不可逆操作**（会改写或丢弃数据）。**未完成阶段 2 备份并通过「隔离恢复验证」前，禁止进入阶段 3。**
> 每阶段含「做什么 / 怎么验证 / 失败怎么办」。任何阶段失败：**保留现场**（命令输出、日志、截图），回传本方后再继续，不要「试一下看」。

---

## 阶段 0 · 前置确认（只读，不改任何数据）

| 步 | 做什么 | 怎么验证 / 取读数 |
|---|---|---|
| 0.1 | 记录当前版本基线 | `git log -1 --oneline`；再执行通知件第三节第 3 步的两条祖先验证（**按 exit 码判读**，两行应各为 `exit=0`）。**输出原样保留** |
| 0.2 | 记录库文件基线 | Windows：`Get-Item .\data\quantstudio.db \| Select-Object Length,LastWriteTime`；Linux/macOS：`ls -l data/quantstudio.db`。**若有附库（独立 .db 文件），逐个记录**——枚举见 A.6 |
| 0.3 | 记录 WAL 有无 | `Get-Item .\data\quantstudio.db.wal -ErrorAction SilentlyContinue`（存在则记大小。**这是决定备份必须在停机后做的原因**） |
| 0.4 | 记录现役进程与**数据库使用者** | 任务管理器看有无采集/界面进程；记 PID 与启动时间；**再确认除引擎外没有别的程序在连这个库**（自研脚本、BI/报表工具、备份软件、Excel 插件、杀毒软件扫描）。**有其他使用者时先停用它们**。⚠ **「只读连接能打开」不足以证明没有写者** ⇒ 须做 **A.5 的进程身份清点与句柄独占探针** |
| 0.5 | 确认磁盘空间 | 峰值需求 ≈ 库体积 × 3～4（预算表见阶段 2.3） |
| 0.6 | 确认 DuckDB 版本 | `python -c "import duckdb; print(duckdb.__version__)"`。本文命令实测于 **1.5.6 / 1.4.5**；**其他版本请先在副本上重测** |
| 0.7 | 回传 0.1–0.6 | 本方核对后回「可以进入阶段 1」或「先别动」 |

---

## 阶段 1 · 停机（可逆，但会让采集暂停）

| 步 | 做什么 | 验证 | 失败怎么办 |
|---|---|---|---|
| 1.1 | GUI「进程常驻增量拉取」→ **停止**；界面进程也一并退出 | `data\daemon_status.json` 消失，或 GUI 按钮显示「已停止」 | 按钮无效时：确认没有第二个界面实例在跑（阶段 0.4 的 PID）——**不要直接结束不确定归属的进程** |
| 1.2 | 确认无残留写入 | 阶段 0.4 的 PID 均已退出；**等待 1 分钟**，再观察 `data\quantstudio.db.wal` 是否**不再增长或消失**；⚠ 再跑 **A.5 的句柄独占探针**（主判据）与**进程身份清点** | 若 WAL 仍增长 ⇒ 还有进程在写：**不要强杀**，把该进程的命令行截图回传本方；若**句柄独占探针失败** ⇒ 仍有进程持有该库文件（**含只读打开者**），按 A.5 处理后再继续 |
| 1.3 | 确认 WAL 已归并（**一致保全的前提**） | 干净关闭后 **`.wal` 会消失**（其内容已被归并进 `.db`）——我方隔离实测：连接打开时 `.wal` 存在（1.5 MiB 级库），正常关闭后 `.wal` 不存在、`.db` 体积略增。若关闭后 `.wal` 仍在且不缩小 ⇒ 未干净关闭 | 回到 1.1/1.2；仍不干净 ⇒ 回传本方 |
| 1.4 | ⛔ **停机时冻结指纹**（**「停机时点」的锚**，v6 新增） | ①对**每个库文件**（含附库）记录 `sha256`；②记录是否存在 `.wal` 及其 `sha256`/字节数；③对**每个库**记录**逐表行数 + 逐行指纹**（用 2.4.1 的 `fp()` 函数，**只读**打开**生产库**计算），并**落成 `stop_time_fingerprints.csv`（列 `table,rows,fp`，每库一份）**——这是 2.4.1 唯一的比对基准 | 计算过程**只读**（不得对生产库写入）；若只读打不开 ⇒ 有持有者 ⇒ 回 1.2。**冻结记录是后续所有比对的基准**（见 A.5.7：源与副本 sha256 相等**检不出**静默推进，只有与本步冻结指纹比对才能检出） |

> 停机后采集暂停；请安排在您方便的时间窗内完成阶段 2–4。

---

## 阶段 2 · ⛔ 备份（**不可逆操作的唯一权威回退点**）

> **关键原则**：**「目录总字节数相等」不能证明回退点可用**——字节数相同也可能是拷贝中断的半份文件。回退点可用必须**在隔离目录里真的把它打开并逐表比对**（2.4）。
> **出口闸**：进入阶段 3 之前，**四证（证①进程身份 · 证②锁观察 · 证③备份 SHA256＋停机时冻结指纹 · 证④隔离恢复）必须齐备**，见 **A.7**。缺任一证 ⇒ **不得进入阶段 3**。

### 2.1 停机后拷贝（**必须在停机后**）

| 步 | 做什么 | 验证 | 失败怎么办 |
|---|---|---|---|
| 2.1.1 | 在**已停机**状态下拷贝整个 `data` 目录（**主库 `.db` 与所有附库 `.db` 文件**；`.wal` 若仍存在则一并拷并记录） | 见 2.2 | 拷贝报「文件被占用」/`Permission denied` ⇒ **有进程仍持锁**（我方实测：持锁期间拷贝会被 Windows **直接拒绝**，是**响亮失败**而非静默错误副本）⇒ **不要重试**，回阶段 1.2 查残留进程（A.5.7） |
| 2.1.2 | 记录备份路径与时间戳 | 例如 `D:\backup\quantstudio-data-20261115-1530` | 备份盘与项目盘不同盘时，确认剩余空间足够（预算见 2.3） |

### 2.2 逐文件清单与内容校验（**逐文件 sha256，不是总量**）

```powershell
# Windows：逐文件 sha256 + 清单落盘
$src = '.\data'; $dst = 'D:\backup\quantstudio-data-<时间戳>\data'
$out = @()
Get-ChildItem $src -Recurse -File | ForEach-Object {
  $rel = $_.FullName.Substring((Resolve-Path $src).Path.Length)
  $out += [pscustomobject]@{
    file   = $rel
    bytes  = $_.Length
    sha256 = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash
    copy   = (Get-FileHash -Algorithm SHA256 (Join-Path $dst $rel)).Hash
    frozen = (Get-Content "D:\backup\stop_time_fingerprints.csv" -ErrorAction SilentlyContinue) -match [regex]::Escape($rel) | Out-Null
  }
}
$out | Format-Table -AutoSize | Out-String
$out | Export-Csv "D:\backup\quantstudio-data-<时间戳>\manifest.csv" -NoTypeInformation -Encoding UTF8
$bad = $out | Where-Object { $_.sha256 -ne $_.copy }
if ($bad) { "MISMATCH: $($bad.file -join ', ')" } else { "ALL_SHA256_MATCH" }
```

```bash
# Linux/macOS
cd <项目目录>
find data -type f -print0 | sort -z | xargs -0 sha256sum > /backup/manifest_src.txt
(cd /backup/<备份目录> && find data -type f -print0 | sort -z | xargs -0 sha256sum > /backup/manifest_copy.txt)
diff /backup/manifest_src.txt /backup/manifest_copy.txt && echo ALL_SHA256_MATCH
```

> **附库提醒**：附库是独立的 `.db` 文件，**同样必须出现在逐文件清单里**；清单里有几个 `.db` 就是现场有几个库，重建与验收都按**逐库**做（A.6）。
> **本节＝四证之证③**：必须出现 `ALL_SHA256_MATCH`，并把 `manifest.csv` 与**阶段 1.4 的停机时冻结指纹**一并留存。
> ⚠ **sha256 相等不等于「停机时点」的快照**：我方隔离实测——若在探针通过之后、拷贝之前有写者「写完即退」，拷贝会成功且**源与副本 sha256 相等**，但内容**已前进**（与停机时冻结指纹不一致）。⇒ **必须再与 1.4 的冻结指纹逐表比对**（A.5.7）。

### 2.3 空间预算（**峰值占用**）

| 项目 | 占用 | 说明 |
|---|---|---|
| 源库（原地不动） | 1× | 重建期间保留 |
| 备份副本 | 1× | 阶段 2 产出 |
| 重建目标库（`COPY FROM DATABASE` 路线） | 1× | 阶段 3 产出 |
| EXPORT 中间目录（若走 EXPORT+IMPORT 路线） | ≈1× | CSV/Parquet 中间件，可与重建目标并行存在 |
| **峰值合计** | **≈3×（COPY 路线）～4×（EXPORT 路线）** | 另留 10% 余量 |

### 2.4 ⛔ 隔离恢复验证（**回退点可用的实证**，不可省）

> **v7 换法（R07 退修）**：旧版本文说「只验证隔离复制品、原备份零打开」，旧代码却 `connect(src_db, read_only=True)`——**文字与代码自相矛盾**，且「只读打开」**仍是一次打开**（实测：字节与 `.wal` 可以不变、`atime` 会变；`atime` 又受系统「上次访问更新」策略控制，不可依赖 ⇒ **两者都不能作为「没打开」的证据**）。v7 起**原备份全程零打开**：验证基准改为**停机时冻结指纹**（主路径）或**参照复制品**（备选）。

#### 2.4.1 主路径：消费停机时冻结指纹（推荐）

前置：阶段 **1.4** 已产出 `stop_time_fingerprints.csv`（表头固定 `table,rows,fp`；真实空库须另写一行 `#EMPTY_DB` —— **写在哪一行都合法**，由脚本按标记识别并剔除，见 §2.4.1.1）。

```python
# 隔离恢复验证（主路径 v12）——只打开「备份的隔离复制品」；原备份路径**不传入任何 connect**
# 用法：python restore_probe.py <probe_copy.db> <stop_time_fingerprints.csv>
import csv, os, re, sys, duckdb

probe, frozen_csv = sys.argv[1], sys.argv[2]
SYS_SCHEMAS = ("information_schema", "pg_catalog")

def die(msg):
    print(f"RESTORE_PROBE FAIL({msg})"); sys.exit(2)

def fp(con, t):
    q = ("SELECT count(*)::BIGINT, coalesce(md5(string_agg(h,'' ORDER BY h)),'EMPTY') "
         f'FROM (SELECT md5(CAST(t AS VARCHAR)) AS h FROM "{t}" AS t)')
    n, h = con.execute(q).fetchone()
    return int(n), h

# ============ 阶段 A：文字层解析（先摘标记；BOM 只去一次并**复用同一份 header**）============
try:
    raw = open(frozen_csv, encoding="utf-8-sig").read()   # ← utf-8-sig：读入即去 BOM（无 BOM 也安全）
except FileNotFoundError:
    die("FROZEN_CSV_NOT_FOUND")
lines = raw.splitlines()

marker_lines = [i + 1 for i, ln in enumerate(lines)
                if ln.strip().startswith("#") and "EMPTY_DB" in ln]
if len(marker_lines) > 1:
    die(f"MULTIPLE_EMPTY_DB_MARKERS{marker_lines}")
empty_declared = bool(marker_lines)
scope = "whole_db"
if empty_declared and "MAIN_ONLY" in lines[marker_lines[0] - 1]:
    scope = "main_base_only"
if empty_declared and re.search(r"scope\s*=\s*main", lines[marker_lines[0] - 1]):
    scope = "main_base_only"

body = [ln for i, ln in enumerate(lines) if (i + 1) not in marker_lines]
if not body or not body[0].strip():
    die("BAD_HEADER(empty)")

header = [c.strip().lstrip("\ufeff") for c in body[0].strip().split(",")]
if header != ["table", "rows", "fp"]:
    die(f"BAD_HEADER({header})")

# ⚠ 关键：DictReader 必须显式拿**同一份** header 作 fieldnames，不能再让它去读原始首行
frozen = {}
for ln_no, r in enumerate(csv.DictReader(body[1:], fieldnames=header), start=2):
    t = (r.get("table") or "").strip().lstrip("\ufeff")
    n = (r.get("rows") or "").strip()
    h = (r.get("fp") or "").strip()
    if not t or not n or not h:
        die(f"BAD_ROW(line={ln_no})")
    if not re.fullmatch(r"\d+", n):
        die(f"BAD_ROWS(line={ln_no}, value={n!r})")
    if not (re.fullmatch(r"[0-9a-f]{32}", h) or h == "EMPTY"):
        die(f"BAD_FP(line={ln_no}, value={h[:16]!r})")
    if t in frozen:
        die(f"DUPLICATE_TABLE({t})")
    frozen[t] = (int(n), h)

# ============ 阶段 B：对象范围（标记与数据行互斥；空清单≠空库）============
if empty_declared and frozen:
    die(f"EMPTY_DB_WITH_ROWS({len(frozen)})")
if not empty_declared and not frozen:
    die("EMPTY_FROZEN_LIST")

# ============ 阶段 C：副本必须存在且可开（空库分支也不得跳过）============
if not os.path.exists(probe):
    die("PROBE_NOT_FOUND")
try:
    con = duckdb.connect(probe, read_only=True)
except Exception as e:
    die(f"PROBE_OPEN_FAILED({type(e).__name__})")

# ============ 阶段 D：空库判定——**五源全对象面**取证 ============
# 声明范围（缺一不可；information_schema.tables **不含 sequence**）：
#   ①表与视图  ②序列  ③非默认 schema  ④用户类型  ⑤scalar macro  ⑥table macro
#   （索引/约束依附于表，不单列为来源；表为空则它们不存在）
# 任取一源探测失败 ⇒ EMPTY_DB_SCOPE_UNVERIFIED（**拒绝未覆盖状态**，不猜「大概没有」）
def probe_surface(con):
    surf = {}
    def q(src, sql):
        try:
            surf[src] = [tuple(str(x) for x in r) for r in con.execute(sql).fetchall()]
        except Exception as e:
            die(f"EMPTY_DB_SCOPE_UNVERIFIED({src}: {type(e).__name__})")
    q("tables_views",
      "SELECT table_schema, table_name, table_type FROM information_schema.tables "
      f"WHERE table_schema NOT IN {SYS_SCHEMAS}")
    q("sequences",
      "SELECT schema_name, sequence_name FROM duckdb_sequences() "
      f"WHERE schema_name NOT IN {SYS_SCHEMAS}")
    q("extra_schemas",
      "SELECT schema_name FROM duckdb_schemas() "
      "WHERE schema_name NOT IN ('main','information_schema','pg_catalog')")
    q("user_types",
      "SELECT schema_name, type_name FROM duckdb_types() "
      f"WHERE schema_name NOT IN {SYS_SCHEMAS} AND NOT internal")
    q("macros_scalar",      # scalar macro
      "SELECT schema_name, function_name FROM duckdb_functions() "
      "WHERE function_type = 'macro' AND schema_name NOT IN ('information_schema','pg_catalog')")
    q("macros_table",       # table macro（v13 只筛 'macro'，漏掉这一类）
      "SELECT schema_name, function_name FROM duckdb_functions() "
      "WHERE function_type = 'table_macro' AND schema_name NOT IN ('information_schema','pg_catalog')")
    return surf

surf = probe_surface(con)
by_source = {k: len(v) for k, v in surf.items()}
total_objects = sum(by_source.values())
main_base = [o for o in surf["tables_views"] if o[0] == "main" and o[2].upper() == "BASE TABLE"]

if empty_declared:
    # ① 整库声明：五源合计必须为 0 —— **有任何对象即 FAIL，不降级**
    if scope == "whole_db":
        if total_objects:
            nonempty = {k: v for k, v in by_source.items() if v}
            die(f"EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects={total_objects}, by_source={nonempty})")
        con.close()
        print(f"RESTORE_PROBE PASS_EMPTY_DB(marker_line={marker_lines[0]}, scope=whole_db, "
              f"objects=0, by_source={by_source})")
        sys.exit(0)

    # ② main_only 声明：**先核 main 基表确实为空**，再报 main_base=0（不得恒打印）
    if main_base:
        die(f"EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES({len(main_base)}, "
            f"e.g. {[f'{s}.{t}' for s, t, _ in main_base[:8]]})")
    others = total_objects - len(main_base)          # 其它源 + main 的非基表对象
    if others == 0:
        con.close()
        print(f"RESTORE_PROBE PASS_EMPTY_DB(marker_line={marker_lines[0]}, "
              f"scope=main_base_only, objects=0, by_source={by_source})")
        sys.exit(0)
    con.close()
    print(f"RESTORE_PROBE PARTIAL_EMPTY_MAIN_ONLY(main_base=0, other_objects={others}, "
          f"by_source={ {k: v for k, v in by_source.items() if v} })")
    sys.exit(3)

# ============ 阶段 E：逐表核对与分级（清单范围 = main 基表，与阶段 1.4 一致）============
copy_tabs = {o[1] for o in main_base}
unproven = sorted({r[0] for r in con.execute(
    "SELECT table_name FROM information_schema.columns "
    "WHERE table_schema='main' AND upper(data_type) IN ('DOUBLE','REAL','FLOAT')").fetchall()})
missing = sorted(set(frozen) - copy_tabs)
extra = sorted(copy_tabs - set(frozen))
if missing: print(f"MISSING_IN_COPY: {missing}")
if extra:   print(f"EXTRA_IN_COPY: {extra}")
bad = []
for t, (n0, h0) in frozen.items():
    if t not in copy_tabs:
        bad.append(t); continue
    n1, h1 = fp(con, t)
    ok = (n1 == n0 and h1 == h0)
    print(f"{t}: rows {n0}->{n1} | fp {'MATCH' if ok else 'DIFF'} | frozen {h0[:12]} vs copy {h1[:12]}")
    if not ok: bad.append(t)
con.close()

print(f"UNPROVEN_TABLES: {unproven}")
if missing or extra or bad:
    die(f"MISMATCH missing={len(missing)} extra={len(extra)} bad={len(bad)}")
if unproven:
    print(f"RESTORE_PROBE PARTIAL_UNPROVEN({len(unproven)}: {','.join(unproven)})")
else:
    print("RESTORE_PROBE PASS")
```

**结论分级（五态，唯一定义）**

| 状态 | 判定条件（全满足才成立） | 回退点可用性 | 可否进入阶段 3 | 可否计入证④ |
|---|---|---|---|---|
| `PASS` | 清单可解析 · 列/行/重复表校验通过 · 无缺表/多表 · 逐表指纹一致 · **未证表范围为空** | **可用**（仅「文本摘要一致」级） | 可 | 可 |
| `PASS_EMPTY_DB` | 标记行被摘出且唯一 · 标记与数据行互斥 · 副本已实际打开 · **五源全对象面**合计为 0（`scope=whole_db`）；或 `scope=main_base_only` 且**实测 main 基表为 0 且其它对象为 0** | 可用（空库） | 可（**NO-OP**，无表可重建） | 可（须附 `marker_line` + `scope` + `objects=0` + `by_source`） |
| `PARTIAL_EMPTY_MAIN_ONLY` | **声明即 `main_base_only`** · 已核实 **main 基表为 0** · 但五源中仍有其它对象（序列/非默认 schema/用户类型/宏/视图） ⇒ **仅 main 基表为空、整库未证** | **不可用**（整库未验） | **不可** | **不可** |
| `PARTIAL_UNPROVEN` | 逐表全一致，但**存在未证表**（含 `DOUBLE/REAL/FLOAT` 列） | **部分未证**（须列名） | **默认不可**；如需 ⇒ **§3.6 用户例外申请**（业主本人书面批准后方可，且不使证④齐备） | **不可** |
| `FAIL` | 清单缺失/不可解析/列名不符/重复表/行字段非法 · 空清单无标记 · 标记与数据行并存 · 副本不存在/打不开 · **`scope=whole_db` 而副本有任一对象（不降级）** · **`main_only` 而 main 基表非空** · **五源任一不可探测** · 缺表/多表/任一指纹 DIFF | **不可用** | **不可** | 不可 |

> **「整库为空」到底指的是什么（v13 · 五源声明范围）**：`information_schema.tables` **只枚举表与视图，不枚举 sequence**——只查它会在「库里只剩一个序列」时误报空库。因此 v13 把声明范围固定为五源，**缺一不可**：
>
> | # | 对象源 | 查询 |
> |---|---|---|
> | ① | 表与视图 | `information_schema.tables`（除系统 schema） |
> | ② | **序列** | `duckdb_sequences()`（除系统 schema） |
> | ③ | 非默认 schema | `duckdb_schemas()`（除 `main` / 系统） |
> | ④ | 用户类型 | `duckdb_types()`（除系统 schema，且 `NOT internal`） |
> | ⑤ | **scalar macro** | `duckdb_functions()` 中 `function_type = 'macro'`（除系统 schema） |
> | ⑥ | **table macro** | `duckdb_functions()` 中 `function_type = 'table_macro'`（除系统 schema） |
>
> **索引与约束不单列**：它们依附于表——表为空则索引/约束不可能存在，故**不新增来源**（不以来源数量凑覆盖度）。
>
> - **`objects=0` 必须由上述全来源合计为 0 给出**，并回传 `by_source` 明细；
> - **任一源不可探测**（函数/列名不适用当前版本）⇒ `FAIL(EMPTY_DB_SCOPE_UNVERIFIED(src))`——**拒绝该未覆盖状态**；**查询失败绝不当 0**（fail-closed）；
> - `#EMPTY_DB` = 整库为空：**有任何对象即 FAIL**（v12 表格里「whole_db 有对象可降级」的说法与代码不符，v13 已删除，统一为 FAIL）；
> - 只能确认「main 基表为空」时必须写 `#EMPTY_DB_MAIN_ONLY`，脚本**先核实 main 基表确实为 0** 再据实输出；若 main 基表非空 ⇒ `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES)`。此时若其它源仍有对象 ⇒ `PARTIAL_EMPTY_MAIN_ONLY`（**整库未证**，不得按空库放行、不得计入证④）。
>
> ⚠ **未实测项（如实声明）**：上述四/五源所用系统函数的**列名**（`duckdb_types().internal`、`duckdb_functions().function_type` 等）**未在 1.4.5 / 1.5.6 上实测**（本轮按边界不跑实验）——故一律走「探测失败即 fail-closed」，并列入合成验证命令的待验项。

#### 2.4.1.1 空标记的两种合法构造与预期读数（v11 · R11 判据）

> **为什么单列**：v10 把「标记是否存在」写成了 `raw.strip().endswith("#EMPTY_DB")`，于是同时踩两个坑：**(a)** 标记存在就**在 `connect` 之前**报 `PASS_EMPTY_DB`——**副本根本没被检查**，声明可伪造/过期；**(b)** 标记若写在**表头之后**，会被 `csv.DictReader` 当数据行读入，随后 `int(None)` **抛异常**（不是 FAIL，是栈）。

| 构造 | 文件内容（示意） | **预期读数** | 说明 |
|---|---|---|---|
| **C1 标记在表头前** | 第 1 行 `#EMPTY_DB`；第 2 行 `table,rows,fp` | `RESTORE_PROBE PASS_EMPTY_DB(marker_line=1, scope=whole_db, objects=0)` | 标记先被摘出 → 表头仍能正确识别 |
| **C2 标记在表头后** | 第 1 行 `table,rows,fp`；第 2 行 `#EMPTY_DB` | `RESTORE_PROBE PASS_EMPTY_DB(marker_line=2, scope=whole_db, objects=0)` | **v10 在此崩溃（`int(None)`）**；v11 因先摘标记而与 C1 同结果 |
| **C7 CSV 带 UTF-8 BOM** | 文件首字节 `EF BB BF` ＋ 上述任一构造 | **与同构造的无 BOM 结果完全一致**（`PASS_EMPTY_DB(… objects=0)`） | **v11 在此误报 `BAD_ROW(line=2)`**：表头去了 BOM、DictReader 却读原行 ⇒ 字段名成 `\ufefftable`，`r["table"]` 取不到 |
| **C3 标记 + 数据行并存** | 标记 + 至少一行表数据 | `FAIL(EMPTY_DB_WITH_ROWS(n))` | 空库声明与「有表」自相矛盾 |
| **C4 标记多行** | 两行 `#EMPTY_DB` | `FAIL(MULTIPLE_EMPTY_DB_MARKERS[1, 3])` | 多标记不可判定来源 |
| **C5 声明整库为空但副本有对象** | `#EMPTY_DB` + 副本含 `main.t1`（或 `other.t2`、视图、**序列**、宏、用户类型、非默认 schema 之一） | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=n, by_source={...}))` | **「不许自证」的核心**：必须实测**五源合计**为 0 |
| **C5c 只剩一个序列** | `#EMPTY_DB` + 仅 `main.seq1` | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=1, by_source={'sequences': 1}))` | **v12 在此误报 `PASS_EMPTY_DB`**（`information_schema.tables` 不含 sequence） |
| **C5f 只有 scalar macro** | `#EMPTY_DB` + `CREATE MACRO m1(x) AS x+1` | `FAIL(… objects=1, by_source={'macros_scalar': 1})` | v13 只筛 `'macro'` 能抓到它，但**抓不到 table macro** |
| **C5g 只有 table macro** | `#EMPTY_DB` + `CREATE MACRO tm1(x) AS TABLE SELECT x AS c` | `FAIL(… objects=1, by_source={'macros_table': 1})` | **v13 在此误报空库**（`function_type='macro'` 不含 `table_macro`） |
| **C5d 五源之一不可探测** | 版本不支持某系统函数/列名 | `FAIL(EMPTY_DB_SCOPE_UNVERIFIED(<src>: <ErrType>))` | **拒绝未覆盖状态**（fail-closed），不得当作 0 |
| **C5b 只声明 main 基表为空（且确实为空）** | `#EMPTY_DB_MAIN_ONLY` + main 无基表、但含视图/其他 schema 的表/序列 | `RESTORE_PROBE PARTIAL_EMPTY_MAIN_ONLY(main_base=0, other_objects=n, by_source={...})` | **整库未证**：不得按空库放行、不得计入证④、不得进入阶段 3 |
| **C5e main_only 但 main 基表非空** | `#EMPTY_DB_MAIN_ONLY` + `main.t1` 存在 | `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES(1, e.g. ['main.t1']))` | **v12 在此恒打印 `main_base=0`**（没核实就报） |
| **C6 副本不存在/打不开** | 标记 + 路径错 | `FAIL(PROBE_NOT_FOUND)` / `FAIL(PROBE_OPEN_FAILED(...))` | 空库分支**不得**跳过打开 |

**三条口径**：①`PASS_EMPTY_DB` 的每一次出现都必须带 `marker_line` + `scope` + `objects=0` + `by_source`（**全来源实测**，含 `macros_scalar` / `macros_table` 分列），仅凭标记一律不合格；②**带 BOM 与不带 BOM 必须同结果**——解析键只能有一份；③**声明即判据**：`whole_db` 有对象 ⇒ `FAIL`（不降级）；`main_only` 必须**先核实 main 基表为 0**（C5e）——「先核后报」，不得恒打印。

#### 2.4.1.2 空库判据的基线前提（v14 · R14 判据）

> **风险**：两版（1.5.6 / 1.4.5）的**默认 catalog 名**与 **internal 对象**（内置类型、internal 宏、系统 schema 下的对象）如果被过滤器漏掉，就会让**空库也报「有对象」⇒ `空库恒 FAIL`**；反过来，如果为了「能过」而放宽过滤器，又会把真实对象一起排掉 ⇒ **假空库**。两者都是判据失效。

**硬前提（写入判据，不是建议）**

| # | 前提 | 判定方式 |
|---|---|---|
| 1 | **最小空库基线必须为 0** | 用**合成空库**（新建、无任何用户对象）跑六源过滤后的计数，必须 **全 0**；否则先修过滤器，**不得**改判据去接受非 0 |
| 2 | **internal / 默认 catalog 必须显式排除，且排除方式可复核** | 过滤器要能打印「被排除的条目清单」，供审阅确认排除的是 internal 而非用户对象 |
| 3 | **查询失败 ≠ 0** | 任一源抛错 ⇒ `EMPTY_DB_SCOPE_UNVERIFIED`（v13 起已如此，此处再确认口径） |
| 4 | **两版各自独立测** | 1.5.6 与 1.4.5 分处独立环境、独立空库路径；**不得**在同一进程内混跑或互相比对空库基线 |

**若基线非 0 怎么办**：记录被计入的条目（`by_source` + 明细行）→ 改进过滤器（增加 `internal`/系统 schema/默认 catalog 排除）→ 重跑基线 → **直到为 0**；**任何情况下不得**改成「允许 N 个以内的残留」这种宽松判据。

> **执行状态**：本节为**审定前提**；本轮**未运行**任何合成验证。具体命令与用例见 `docs/evidence/firefly-synthetic-validation-request-20261116.md`（待审定）。

#### 2.4.2 备选：参照复制品（阶段 1.4 未做时）

把原备份**再复制一份**当「参照复制品」——被打开的两个对象**都是复制品**，原件仍零打开：

```powershell
Copy-Item D:\backup\...\data\quantstudio.db D:\work\ref_copy.db
Copy-Item D:\backup\...\data\quantstudio.db D:\work\probe_copy.db
```

```python
# 用法：python restore_probe_ref.py <ref_copy.db> <probe_copy.db>
import duckdb, sys
ref = duckdb.connect(sys.argv[1], read_only=True)     # 参照复制品
prb = duckdb.connect(sys.argv[2], read_only=True)     # 待验复制品
def fp(con, t):
    q = ("SELECT count(*)::BIGINT, coalesce(md5(string_agg(h,'' ORDER BY h)),'EMPTY') "
         f'FROM (SELECT md5(CAST(t AS VARCHAR)) AS h FROM "{t}" AS t)')
    n, h = con.execute(q).fetchone()
    return int(n), h
tabs = [r[0] for r in ref.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY 1").fetchall()]
bad = [t for t in tabs if fp(ref, t) != fp(prb, t)]
print("RESTORE_PROBE", "PASS" if not bad else "FAIL(" + ",".join(bad) + ")")
# ⚠ v11：本备选**无法补出源锚**——两个副本相等只证明它们彼此一致（同一次拷贝/同一时刻的非停机态
#        也会相等）。**任何组合都不行**：ref vs probe、probe vs probe、多次拷贝互比皆然。
#        ⇒ 冻结指纹不可得时**回阶段 1.4 重取**，不得以副本相等充当源锚；缺源锚则 G1 不成立。
```

#### 2.4.3 「原备份零打开」自查清单（**对本流程已审入口与命令**·硬判据，逐项打勾回传）

> **结论范围**：本节结论只覆盖**已审入口与命令**——即 §2.4.1 / §2.4.2 给出的脚本与其变体。**不得外推**为「任何方式都未曾打开原备份」（本流程未审计其他工具、其他脚本、他人的操作）。

| # | 检查点 | 通过判据 |
|---|---|---|
| 1 | 验证脚本的**命令行参数**（限本流程已审入口 §2.4.1/2.4.2） | **不含**原备份路径；只有副本路径与冻结指纹 CSV |
| 2 | 脚本内 `duckdb.connect(` / `ATTACH` 的**目标** | **全部**指向副本或参照复制品，**无一处**指向原备份 |
| 3 | 允许对原备份做的动作 | **仅**文件级拷贝（`Copy-Item` / `shutil.copy2`）与 `Get-FileHash` |
| 4 | 验证前后的原备份与所在目录 | 每文件 `sha256` 一致、**目录无新增**（含 `.wal`）——⚠ 本项**只是必要条件**：实测只读打开后 sha256 也可完全不变（§2.4.5 C），**不能据此证明「未打开」** |
| 5 | 回传 | 把①②的**脚本原文与命令行**原样回传（口径可复核，不靠声明） |

> ⚠ **不得用「只读打开」代替「零打开」**：实测只读打开后字节与 `.wal` 均可不变、`atime` 会变，而 `atime` 受系统策略控制 ⇒ 两个方向都不可作证据。判据只有一条：**原备份路径从未进入任何 `connect` / `ATTACH`**。

#### 2.4.4 判据 · 鉴别力自检 · 逐库

- **判据（v12：两个问题拆开问，且**部分未证不得就地开闸**）**——以 §2.4.1 的分级状态为准：
  - **问题一「回退点可用吗？」**：`PASS` ⇒ 可用（「文本摘要一致」级）；`PARTIAL_UNPROVEN` ⇒ **部分未证**（须列名）；`PASS_EMPTY_DB` ⇒ 可用（空库，**须整库取证**）；`PARTIAL_EMPTY_MAIN_ONLY` / `FAIL` ⇒ 不可用。
  - **问题二「可以开始破坏性重建吗？」**：仅 `PASS` / `PASS_EMPTY_DB` 可进入（后者为 NO-OP）。**`PARTIAL_UNPROVEN` 与 `PARTIAL_EMPTY_MAIN_ONLY` 默认一律不得进入**——它们使证④不齐。**本流程不设「就地授权」入口**：确需部分未证重建，只能走 **§3.6 用户例外申请**（业主本人书面批准 + 七项要素），**批准前停在阶段 2**。
  - **红线一**：**「降级结论」不等于「批准开始重建 / 批准部分未证重建」**——把 `PARTIAL_*` 读成「已通过、可以开工」属越级。
  - **红线二**：**客户 B3 书面接受 ≠ 技术通过，也 ≠ 操作授权**——B3 只进「客户接受」栏。
  - **红线三**：**总调度（或任何执行方）无代批权**——例外只能由业主本人作出；总调度仅转呈与留痕。
  - **红线四**：任何状态下都不得把「文本摘要一致」表述为「精确恢复 / 恢复完整 / 重建成功」。
- **鉴别力自检（必做）**：在**副本**上改一行再算 ⇒ 必须 `DIFF`；改回后必须 `MATCH`（实测：改副本一行 ⇒ `fp_match=False`，判据非恒真）。
- **逐库**：有附库时每个库各跑一次；**每个库各一份冻结指纹 CSV**。
- **冻结核对**：验证**前后**记录原备份每文件 `sha256` 与 `.wal` 存在性/大小 ⇒ 必须一致、目录无新增（＝2.4.3 第 4 项）。
- **本节＝四证之证④**：输出必须含 `RESTORE_PROBE PASS` **且** 2.4.3 五项**全部通过**。

#### 2.4.5 整行指纹的语义边界（**类型 / NULL / 浮点 / 转义 / 列序** · v8 新增，实测）

> 本流程的整行指纹是 `md5(CAST(t AS VARCHAR))`（`t` 为整行）——它是**结构体文本的哈希**，不是逐位比较，因此必须写明它**能区分什么、不能区分什么**。以下为隔离实测（1.5.6 与 1.4.5 **语义读数一致**）。

**A. 文本形态（1.5.6 实测原文）**

```
{'d': 1.5000, 'f': 1.0, 'r': 1.0, 'bi': 9223372036854775807, 'hi': 1701…727,
 'bo': true, 'dt': 2026-11-15, 'tm': '13:45:07', 'ts': '2026-11-15 13:45:07.123456',
 'bl': \xDE\xAD\xBE\xEF, 's': txt, 'u': 00000000-0000-0000-0000-000000000001}
```

| 维度 | 政策（实测） | 对判据的影响 |
|---|---|---|
| **列名** | 文本**含列名**（`'d':`） | 列**改名** ⇒ 指纹变；跨表比对须「列名 + 列序」同时一致 |
| **列序** | 按表定义列序排列；同一数据换列序 ⇒ 指纹**不同**（实测 `8b393ea7…` vs `bc4cceeb…`） | 重建前后**列序必须一致**；列序变化不得当数据变化，反之亦然 |
| **DECIMAL** | **保留标度**：`1.5000` | 标度变更（`D(18,4)`→`D(18,2)`）⇒ 指纹变（属 schema 契约变更） |
| **BOOLEAN** | `true` / `false` | 稳定 |
| **DATE / TIME / TIMESTAMP** | `2026-11-15` / `'13:45:07'` / `'2026-11-15 13:45:07.123456'`（**微秒保留**） | 稳定；会话时区/格式设置改动会影响文本 |
| **BLOB** | `\xDE\xAD\xBE\xEF` 形态 | 稳定 |
| **NULL** | 字面 `NULL` | 与「空串 `''`」「字符串 `'NULL'`」**三者可区分**（实测三行指纹互不相同） |
| **字符串转义** | 含 `,` `'` `"` `\`、换行、制表 时**均被引号/转义包裹** | 实测 7 类易混淆数据（含 `('x,y','z')` vs `('x','y,z')`）**两两不碰撞** |
| **浮点 NaN/Inf** | `nan` / `inf` / `-inf` | 三者互不相同 |
| **⚠ DOUBLE / REAL 精度** | **最短表示渲染、有损**：`0.1+0.2` 与 `0.3` 渲染**都是 `0.3`** | ⇒ 两行若**只在该列不同**，整行指纹**无法区分**（实测 `distinct_row_texts=1`、指纹相同）。**含 DOUBLE/REAL 列的表，本判据不具单射性**；需逐位证据时改用 `format('{:.17g}', v)` 之类全精度渲染并注明口径 |

**B. 适用 / 不适用（写进回传要求）**

| | 内容 |
|---|---|
| **可作** | ①同一 schema 同一版本下「备份 vs 重建」「备份 vs 副本」的**所选文本摘要**一致性判据（**不等于数据逐位一致**；含 `DOUBLE/REAL` 列的表另见 §2.4.6）；②重复/改值/删除的**行级多重集**量化（A.3-9） |
| **不可作** | ①**跨 schema 版本**比对（列名/列序/类型标度任一变化都会变）；②**逐位**数值等价证明（DOUBLE/REAL 有损）；③**实体级**归因（无键表，见 A.3-10） |
| **报告义务** | 必须写明所用判据（`md5(CAST(t AS VARCHAR))`）与该表**是否含 DOUBLE/REAL 列**；含则声明「数值列非逐位判据」 |

**C. hash 不变 ≠ 没打开（R08 明确）**

- 实测：对库文件 `connect(..., read_only=True)` 并查询后，其 `sha256` **前后完全相等**（`SEC_T9 open_then_hash.equal = true`）；v8 另测 `size`/`mtime` 不变、`.wal` 不出现，但 **`atime` 变化**。
- ⇒ **§2.4.3 第 4 项（sha256 一致）只是必要条件**，不能证明「从未打开原件」。零打开**唯一充分的证据**是第 1、2 项（**原备份路径未进入任何 `connect`/`ATTACH`**）＋第 5 项（**脚本原文与命令行回传**）。**不得以「hash 没变」代替「零打开」。**

#### 2.4.6 含 `DOUBLE/REAL` 的表：替代比较（**须另经审定**）或「未证」（v9 · R09 判据）

> **为什么必须单列**：§2.4.5 已实测「`DOUBLE/REAL` 渲染有损」——`0.1+0.2` 与 `0.3` 的**文本相同**。因此对**含这类列的表**，「文本摘要一致」**不能**推出「数据精确一致」。
> **仅加一句免责声明不算通过**：这类表必须**要么有另经审定的比较方法，要么结论保持「未证」**，二者都不做则该库验收**不成立**。

**A. 先定范围（必须随回传）**

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'main' AND upper(data_type) IN ('DOUBLE','REAL','FLOAT')
ORDER BY table_name, column_name;
```

- 实测（合成库语法自检，1.5.6 与 1.4.5 读数一致）：含 `DOUBLE`/`FLOAT` 的表被**稳定枚举**；`DECIMAL` **不在**清单内（渲染保留标度，无同类有损问题）；一张表有多个浮点列时**逐列列出**。
- 该结果即「**未证表范围**」：清单内每一张表，结论一律记「**未证**」，**不得计入「重建通过」**。

**B. 三种处置（只能选其一，且必须写清）**

| 处置 | 含义 | 允许的表述 | 禁止的表述 |
|---|---|---|---|
| **B1 经审定的替代比较** | 采用**另行审定**的比较方法（**候选**：`format('{:.17g}', v)` 全精度渲染逐行比对；或按二进制位比 `md5(v::BLOB)`）。⚠ **候选未经审定前一律不得当判据** | 「该表按《方法 X（版本 / 审定号）》比对一致」 | 「数据精确一致」（除该法本身即逐位且已审定） |
| **B2 保持未证**（默认） | 不做替代比较，或做了但方法未审定 | 「该表**文本摘要**一致；**精确性未证**」 | 「重建成功」「恢复完整」「该表一致」 |
| **B3 客户书面接受放弃该表精确性** | 客户明确书面接受「该表只到文本摘要级」 | 「客户已书面接受该表仅文本摘要级」 | 任何暗示精确性的词 |

**C. 结论口径（与 §2.4.4 一致）**

- 整体只允许三档：**①全部表无未证项** → 「文本摘要全一致」；**②存在未证表** → 「**部分未证**」＋**列名**；**③有 DIFF／MISSING** → `FAIL`。
- **措辞红线**：任何情况下**不得**把「文本摘要一致」表述为「数据精确恢复」「恢复完整」「重建成功」。

#### 2.4.7 四类负例的预期与门间传播（v10 · R10 判据）

> 目的：**杜绝「零次循环报成功」与「新节声明未证、旧门仍凭 PASS 放行」**。四类负例都必须被**下游门**拦住，而不是只在某一节写一句免责。

**A. 四类负例的预期读数**

| # | 负例 | 构造 | **预期读数**（原样回传） | 预期判定 |
|---|---|---|---|---|
| **N1** | **空 CSV**（只有表头 / 0 行） | 冻结文件被清空或生成失败 | `RESTORE_PROBE FAIL(EMPTY_FROZEN_LIST)` | `FAIL`（**不得**当作「零表即通过」） |
| **N2** | **缺表**（清单有、副本无） | 副本少了某张表 | `MISSING_IN_COPY: ['t3']` ＋ `RESTORE_PROBE FAIL(MISMATCH missing=1 …)` | `FAIL` |
| **N3** | **清单不全**（副本多表） | 冻结清单漏记了副本里的表 | `EXTRA_IN_COPY: ['t4','t5']` ＋ `FAIL(MISMATCH … extra=2 …)` | `FAIL`（清单不全**不得**静默通过） |
| **N4** | **双副本相等但源锚未证** | 参照复制品与待验副本均来自「非停机态」的同一次拷贝 | 备选路径 `fp_equal=True`，但冻结指纹**缺失** | **源锚未证 ⇒ G1 不成立**：双副本相等**不能补源锚**（任何组合都不行）⇒ **不得进入阶段 3**，回阶段 1.4 重取冻结指纹 |

> **N1–N4 的共同点**：旧配方都会「循环 0 次 / 或只看相等」就得出成功；v10 起由**清单可解析性 + 双向集合核对 + 冻结比对**三重拦住。

**B. 真实空库 vs 空清单（必须区分）**

| 情形 | 特征 | 判定 |
|---|---|---|
| **真实空库** | 冻结文件含 `#EMPTY_DB`（位置不限，先被摘出）· 标记与数据行**互斥** · **副本已打开且 `information_schema.tables` 返回 0 行** | `PASS_EMPTY_DB(marker_line=n, copy_tables=0)`（**仅凭标记不成立**） |
| **空清单** | 冻结文件 0 行**且无** `#EMPTY_DB`；或文件不存在；或表头/列名不符 | `FAIL(EMPTY_FROZEN_LIST / FROZEN_CSV_NOT_FOUND)` |

**C. 门间传播设计（缺一即断链）**

| 门 | 输入 | 输出 | **传播规则** |
|---|---|---|---|
| **G1 §2.4.1 冻结消费** | `stop_time_fingerprints.csv` ＋ 隔离副本 | `PASS` / `PARTIAL_UNPROVEN` / `PASS_EMPTY_DB` / `FAIL` ＋ `UNPROVEN_TABLES` | `FAIL` ⇒ **链条终止**（不得进入 G2 之后的任何判定）；`PARTIAL_UNPROVEN` ⇒ **必须把未证表清单带到 G2/G3/G4** |
| **G2 §2.4.4 判据** | G1 状态 ＋ 未证表清单 | 回退点可用性 | 「**可用**」**仅当** G1=`PASS`（或 `PASS_EMPTY_DB`）；G1=`PARTIAL_UNPROVEN` ⇒ 只可写「**部分未证**」 |
| **G3 §3.0 进口门（阶段 3）** | G1+G2 状态 ＋ 未证表清单 ＋ 七类对象清单 | 进入 / 不进入 | G1=`FAIL` ⇒ **不进入**；未证表范围**未定** ⇒ **不进入**；**`PARTIAL_UNPROVEN` / `PARTIAL_EMPTY_MAIN_ONLY` 默认一律不进入**——确需时只能走 **§3.6 用户例外申请**（业主本人书面），且例外**不使证④齐备** |
| **G4 §3.3 / A.7 验收** | 逐库三核 ＋ 未证表清单 ＋ B1/B2/B3 | 库级结论 | 缺未证表清单 ⇒ **该库验收不成立**；`PARTIAL_UNPROVEN` 库 ⇒ **证④不齐备**；B3 只写「客户接受」栏，**技术栏仍为「未证」** |

**D. 客户 B3 的定位（不得升级为技术 PASS）**

- B3＝**客户书面接受**「该表只到文本摘要级」，属**业务接受**；
- **技术结论栏永远保持「未证」**，验收表须并列两栏：`技术状态` / `客户接受`；B3 只填后者；
- 任何汇总口径（如"重建完成"）**不得**因 B3 而成立；
- **B3 不构成任何操作授权**——「客户接受该表精度」与「客户同意开始重建」是两件事；后者只能走 **§3.6 用户例外申请**，且**执行方与总调度均无代批权**。

### 2.5 保留期

备份与 `manifest.csv`、**停机时冻结指纹记录**保留到**本案关闭**为止；期间不得删除、不得原地覆盖、**不得对原备份做任何写操作**。

---

## 阶段 3 · ⛔（可选）回收历史死元组：整库重建

> **目的**：回收历史全量拉取遗留的「删除行占位」。DuckDB 官方文档说明：**`CHECKPOINT` 与 `VACUUM` 都不回收空间**，有效手段是「整库复制到新库」（`COPY FROM DATABASE`）或「EXPORT + IMPORT」。
> **我方隔离实测（1.5.6 与 1.4.5 均得同一结论）**：
> - ❌ **逐表 `CREATE TABLE … AS SELECT` 重建：不可用**——实测**全部约束与索引丢失**，且**库体反而变大**（2,895,872 B > 源 1,847,296 B）。**请勿采用。**
> - ✅ **`COPY FROM DATABASE`（首选）** 与 **`EXPORT DATABASE` + `IMPORT DATABASE`（备选）**：实测**对象零丢失**、数据指纹一致、**库体变小**。
> - ⚠ **两者都只覆盖「当前库」**：**附库内容不会自动进入重建结果**——须**逐库保全**（A.6）。

### 3.0 进口门（**前置判据**，缺一不得开始重建）

**A. 入口真值表（唯一判据；缺任一列即「不得进入」）**

| G1 状态 | 未证表范围已回传 | **可进入阶段 3？** | 依据 | 结论上限 | 证④ |
|---|---|---|---|---|---|
| `PASS` | 是（可为「空」） | **是** | 本表（阶段 3 内替换生产库仍走用户闸，见 3.5） | 文本摘要一致 | 齐备 |
| `PASS_EMPTY_DB` | 是（须为「空」） | **是（NO-OP，无表可重建）** | 整库取证 `objects=0` | 空库 | 齐备 |
| `PARTIAL_EMPTY_MAIN_ONLY` | 是 | **否** | 整库未证 | — | 不齐备 |
| `PARTIAL_UNPROVEN` | 是（非空清单） | **否（默认）**；确需 ⇒ **§3.6 用户例外申请**（业主本人书面批准后另行执行，不在本流程内预置） | 证④不齐 | 部分未证（须列名） | 不齐备 |
| `FAIL` | — | **否** | — | — | 不齐备 |
| 任意状态 | **否** | **否** | — | — | — |

**B. 逐项前置**

| # | 前置条件 | 通过判据 | 不通过怎么办 |
|---|---|---|---|
| 1 | §2.4.1 状态 ∈ 真值表首列 | 见上表（`FAIL` 一律不得进入） | 回 2.2 重拷 + 重验 |
| 2 | **未证表范围已定** | 已回传 `UNPROVEN_TABLES`（含表名与涉及列）；**为空也须显式回传「空」** | 先跑 §2.4.6 A 的查询，未回传**不得进入** |
| 3 | 七类对象清单已取（逐库） | 表/约束/索引/视图/序列/设置/依赖清单齐备 | 回 3.1 取清单 |
| 4 | `PARTIAL_*` 时**不得就地开闸** | 本流程**没有**「在 §3.0 里签一句授权即可进入」的通道；确需时另走 **§3.6 用户例外申请** | **未获业主书面例外 ⇒ 停在阶段 2**（不是「先做再说」，也不是「总调度同意即可」） |
| 5 | 结论上限已声明 | `PARTIAL_UNPROVEN` 须预先声明「**阶段 3 结论上限＝部分未证**」 | 不得开始（否则会产出越级结论） |

> **B4 的语义（R12 判据）**：`PARTIAL_UNPROVEN` / `PARTIAL_EMPTY_MAIN_ONLY` 使 A.7 证④不齐——**证④不齐 ⇒ 不得自动开始破坏性动作**。
> **且不得通过「新增一个授权入口」把它放行**：v11 曾写「用户/总调度任一书面授权即可进入」，这等于**绕过原证④与用户闸**，R12 已删除。
> 正确路径只有两条：**(i)** 补齐证④（把未证项消除）后再进；**(ii)** 另立 **§3.6 用户例外申请**，业主本人书面批准后**另行执行**——例外**不预置**在本流程里，也**不使证④齐备**。

### 3.6 用户例外申请（**另行立项 · 不属本流程默认配方**）

> 仅在「确有业务必要、且未能补齐证④」时使用。**不得**据此认为阶段 3 已可开始——批准前一律停在阶段 2。

| # | 申请要素 | 要求 |
|---|---|---|
| 1 | 未证对象清单 | 未证表/列（来自 §2.4.6 A 与 §2.4.1 的 `UNPROVEN_TABLES`），或整库未证的具体缺口（如非 main schema 对象） |
| 2 | 为何必须现在做 | 业务理由；「反正要重建」不构成理由 |
| 3 | 影响面与最坏后果 | 明写：这些对象的精确性**无法证明**，重建后**不能**与停机态逐位比较 |
| 4 | 回退方案 | 原备份仍冻结可回退；回退演练记录 |
| 5 | 结论措辞承诺 | 交付文档只写「部分未证」，**禁止**「重建成功 / 恢复完整」 |
| 6 | **业主本人逐字批准** | 用户书面原文；总调度只转呈与留痕，**无代批权** |
| 7 | 期限与复核点 | 到期未复核即失效，需重新申请 |

**效力边界**：例外批准**不改变技术结论**（技术栏仍「未证」）、**不使证④齐备**、**不构成对未证对象的精度承认**，也不得被引用为后续同类场景的既成先例。

> **为什么要有这道门**：未证表范围若只写在 §2.4.6、而阶段 3 仍凭 `PASS` 放行，就等于「新节声明未证、旧门照旧通过」——**R10 判据明确禁止**。

### 3.1 取表清单与对象基线（客户回传，本方出脚本）

```sql
-- 在客户库上执行（只读），把输出回传本方
SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY 1;
SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints() WHERE schema_name='main' ORDER BY 1,2,3;
SELECT table_name, index_name, is_unique FROM duckdb_indexes() WHERE schema_name='main' ORDER BY 1,2;
SELECT view_name FROM duckdb_views() WHERE schema_name='main' AND NOT internal ORDER BY 1;
SELECT sequence_name FROM duckdb_sequences() WHERE schema_name='main' ORDER BY 1;
```

> **附库也要取一遍**：`PRAGMA database_list;` 列出全部库后，对**每个库**各跑上面五条。**在拿到清单前不执行任何重建动作。**

### 3.2 执行重建（首选路线）

```sql
-- 路线 A（首选）：整库复制
ATTACH 'D:/backup/.../prod_like.db' AS src;   -- 源库
ATTACH 'D:/work/rebuilt.db' AS dst;            -- 目标库（新建、空）
COPY FROM DATABASE src TO dst;
-- 完成后：对比 3.3 的对象清单；确认无误再原子替换生产库
```

```bash
# 路线 B（备选）：EXPORT + IMPORT
duckdb "data/quantstudio.db" -c "EXPORT DATABASE 'D:/work/exported'"
duckdb "D:/work/rebuilt.db" -c "IMPORT DATABASE 'D:/work/exported'"
```

> **附库**：上面两条只作用于主库。**附库请按 A.6 逐库保全**（`COPY FROM DATABASE <附库> TO …` 或 `USE <附库>; EXPORT DATABASE …`）。
> ⚠ **竞态提示**：我方隔离实测——**只读连接存续期间，外部写者会被拒绝**（`另一个程序正在使用此文件`）⇒ **在同一条只读连接内完成「导出＋拷贝＋算指纹」可消除探针关闭后的再打开窗口**（A.5.7）；若用文件级拷贝，则**拷贝失败即停**，不要重试。

### 3.3 ⛔ 覆盖清单核对（**重建前后必须逐类一致**）

| 类别 | 核对方式 | 通过判据 |
|---|---|---|
| **数据** | 逐表行数 + 逐行指纹（阶段 2.4 的 `fp()` 函数） | 每表 `rows` 与 `fp` 完全一致 |
| **约束** | `duckdb_constraints()` 前后 diff | diff 为空（PK / NOT NULL / CHECK / UNIQUE 全部保留） |
| **索引** | `duckdb_indexes()` 前后 diff | diff 为空（含唯一索引） |
| **视图** | `duckdb_views()`（排除 internal）前后 diff | diff 为空 |
| **序列** | `duckdb_sequences()` 前后 diff | diff 为空 |
| **设置** | `duckdb_settings()` 中与本库相关的项 | 与原库一致或已明确记录差异原因 |
| **依赖** | 视图/序列/外键的依赖关系 | 重建后视图可正常查询（逐个 `SELECT … LIMIT 1` 试跑）；无孤儿依赖 |
| **未证表范围**（v10 新增，**必填**） | 该库含 `DOUBLE/REAL/FLOAT` 列的表清单（§2.4.6 A） | **逐表列出**；这些表本行结论只能写「文本摘要一致、**精确性未证**」，**不得**并入「重建通过」；缺本行 ⇒ 该库验收不成立 |

> 任一类 diff 非空 ⇒ **不得替换生产库**；保留新旧两库并回传 diff。
> ⚠ **逐库核验**：本清单必须**对每个库（含附库）各做一遍**——**缺任一库的证据即视为未完成**（A.6）。

### 3.4 备份后新增数据的保全 / 合并（**逐库**）

| 情形 | 判定 | 处置 |
|---|---|---|
| **备份时已彻底停机**（推荐） | 备份与源库是同一时点的一致快照（阶段 1.3 + 1.4 冻结指纹一致） | **无新增数据，无需合并**；直接以重建库替换 |
| 备份后仍有写入（停机不彻底或期间又跑了采集） | 新增行**不在备份内**（我方隔离实测：备份态 2 行 vs 之后 3 行，差异可测） | 二选一：①**丢弃新增数据**——以备份/重建库为准，按水位重拉该窗口（推荐，状态可预期）；②**保全新增数据**——用**幂等 upsert 重放**该窗口：重放后与源库逐行指纹一致，且**重复重放不改变行数**（我方实测：`replay_fp == live_fp`，二次重放 `idempotent_unchanged=True`）。⚠ 该方式**只适用于具备 UNIQUE/PRIMARY KEY 约束或索引的表**；**无键表不做任何行级回放**，**只整表重灌或明确排除**（A.3-6） |
| 不确定是否停机彻底 | 无法判定 | **按情形①处理**（回到备份态重拉），把不确定性回传本方 |

> **逐库三核（v6 新增，验收表按库逐行填）**：每个库都要给出 **①数据**（指纹一致？）· **②对象**（七类 diff 为空？）· **③增量**（该库备份后新增行的处置：**已幂等重放** / **已丢弃并重拉** / **不可回放→排除**（须填**损失行数**——按 A.3-8 的多重集 `rows_lost` 口径，附 `rows_added` 与受影响行数——及时间范围））。**三核缺一 ⇒ 该库视为未完成。**

### 3.5 失败处理

任一步报错 ⇒ **立即停手**：保留**新旧两个库**（不要删除旧库），回传报错原文与 3.3 的 diff；回退方式＝阶段 5.3（从备份恢复）。

---

## 阶段 4 · 全量重拉（串行 + 降批 + 记录读数）

| 步 | 做什么 | 验证 / 记录 | 异常怎么办 |
|---|---|---|---|
| 4.1 | 设置开关：把 `QS_UPSERT_SKIP_IDENTICAL=1` 加到**采集进程实际读到的环境**，**并重启采集** | 记录设置位置与时间；**设置后必须重启才生效** | 加错位置 ⇒ 采集仍按旧行为；用阶段 0.4 的进程命令行确认启动方式 |
| 4.2 | 设为**单任务串行** | 记录本轮启动的任务名 | 有并发 ⇒ 停掉其他任务再重跑 |
| 4.3 | 批次降到 **1 万**行 | 记录实际批次大小 | 不支持改批次的入口 ⇒ 保留默认并注明 |
| 4.4 | 逐批记录：**批号 / 累计行数 / 单批耗时 / 是否推进** | 见下方记录表 | 单批耗时持续上升 ⇒ 记录到第几批停止增长，回传 |
| 4.5 | 观察目标：**按行数判定，不按批数** | 现场原判据「**357 批 × 每批 5 万行 ≈ 1785 万行**」；按 1 万/批则**同等工作量约需 893 批**。**请按累计写入行数回报** | 冻结时不要强杀：记批号与日志，等 5 分钟看是否仍在推进 |
| 4.6 | 记录 T2 的真实收益（若可行） | 同一批次**开/关**各跑一次比较单批耗时 | 属生产变更；不便做两次就只回传「开了 T2」的读数 |

**重拉记录表（请复制填写回传）**

| 批号 | 累计行数 | 单批耗时(s) | 是否推进 | 备注 |
|---|---|---|---|---|
| 1 | | | | |
| … | | | | |
| 末批 | | | | 判据按**累计行数 ≈ 1785 万**（对应 1 万/批时约 893 批） |

> **T2 收益口径提醒**：开关只跳过「已存在且逐位一致」的行；**数据实际发生变化的行必须写入**。我方实测的「约 20 倍」是**该次场景的实测值**，**不是普遍上限，也不是收益承诺**。

---

## 阶段 5 · 恢复与收尾

| 步 | 做什么 | 验证 | 失败怎么办 |
|---|---|---|---|
| 5.1 | 重新启动常驻采集 | GUI 按钮「🟢 运行中」；`data\daemon_status.json` 出现 | 启不来 ⇒ 不要反复点；查日志并回传 |
| 5.2 | 走通知件 §4.1 的 7 项常规自查 | 逐项打勾 | 任一项异常 ⇒ 记录该项与日志回传 |
| 5.3 | （仅在阶段 3/4 出错时用）从备份恢复 | ①停机 ②把当前 `data` 目录**改名留存**（不要直接删）③拷贝备份回 `data` ④重启 ⑤跑阶段 2.4 的隔离恢复验证 | 恢复后仍异常 ⇒ 立即回传，本方接手 |
| 5.4 | 收尾 | 记录：实际执行的阶段、每阶段耗时、最终结论（**累计写入行数**、是否再冻结）；**备份与冻结指纹记录保留**至本案关闭 | — |

---

## 失败处理速查表

| 现象 | 可能原因（待排假设） | 动作 | 可否回退 |
|---|---|---|---|
| 拷贝备份报文件占用 / `Permission denied` | 仍有进程持锁（我方实测：持锁期间拷贝被系统**直接拒绝**） | **不要重试**；回阶段 1.2 查残留；**不要强杀** | 未进阶段 3 ⇒ 无需回退 |
| **句柄独占探针失败**（`being used by another process`） | 仍有进程持有该库文件句柄（**含只读打开者**） | 按 **A.5** 清点进程身份并停用它；回传 PID 与命令行 | 未进阶段 3 ⇒ 无需回退 |
| **只读探针通过、拷贝仍报占用** | 存在**只读**持有者：它**不阻断**只读探针，但**会阻断**读写打开 | **以句柄独占探针为准**；关闭 BI/查询/预览/同步盘后再试 | 未进阶段 3 ⇒ 无需回退 |
| **拷贝成功但与停机时冻结指纹不一致** | 探针关闭到拷贝之间有写者「写完即退」（**sha256 相等检不出**） | **备份作废**，回阶段 1 重做；把冻结指纹与副本指纹一并回传 | 未进阶段 3 ⇒ 无需回退 |
| **观察窗中途新增命中 / 任一次探针 FAIL** | 窗口期内有新进程打开了该库 | **观察窗不通过** ⇒ 备份作废，回阶段 1 | 未进阶段 3 ⇒ 无需回退 |
| **四证缺任一证** | 只做了部分举证 | **不得判定「停机成立」或「回退点可用」**，**不得进入阶段 3** | 未进阶段 3 ⇒ 无需回退 |
| 备份逐文件 sha256 不一致 | 拷贝中断/源在变 | 重做备份（2.1→2.2） | 未进阶段 3 ⇒ 无需回退 |
| 隔离恢复验证 FAIL | 备份不完整或源库在拷贝期间被写 | 重做备份并重验（2.2→2.4） | 未进阶段 3 ⇒ 无需回退 |
| **原备份验证前后 sha256 不一致 / 旁边多出 .wal** | 有人对原备份做了写操作 | **原备份已被污染**，重做备份；不得继续用它当回退点 | 未进阶段 3 ⇒ 无需回退 |
| `COPY FROM DATABASE` / `IMPORT` 报错 | 磁盘不足、路径含中文未转义、目标文件已存在 | 保留新旧两库；回传报错原文 | 是（5.3） |
| 重建后对象清单 diff 非空 | 约束/索引/视图/序列未全量保留 | **不得替换生产库**；回传 diff | 是（5.3） |
| **重建库里缺了附库的表** | `EXPORT`/`COPY` 只覆盖主库 | 按 **A.6** 逐库保全并**逐库核验**；**不得替换生产库** | 是（5.3） |
| 重建后库体没变小甚至变大 | 走的是 CTAS 路线（禁止）或死空间确实未回收 | 确认走 COPY/EXPORT 路线；回传体积读数 | 是（5.3） |
| 重放后某表指纹仍不一致 | 该表**无 UNIQUE/PK 约束**（行级回放不适用）或 delta 边界圈错 | 该表**只整表重灌或明确排除**（排除须列明损失并由客户确认）；**不将就** | 是（5.3） |
| 重拉单批耗时持续上升 | 死元组累积 / 磁盘或内存压力 | 记录到第几批，回传 | 是（5.3） |
| 重拉再次冻结 | 与首次现场现象一致（根因未消除） | **不要强杀**；记批号与日志，回传 | 是（5.3） |
| 开了 T2 但耗时无改善 | 变更行占比高／开关未被进程读到 | 核对进程是否读到该 env；记录单批耗时 | — |
| 祖先验证 exit=128 | 本地缺提交对象（浅克隆或 refspec 收窄） | 见通知件 §三第 3 步的三分支处理 | — |
| 恢复后采集起不来 | 库或 WAL 状态异常 | **不要反复启动**；回传日志 | 是（5.3） |

---

## 客户回传清单（一次问清）

1. 阶段 0 的全部读数（版本 / 祖先验证 exit 码 / 库体积 / WAL / 进程与使用者 / DuckDB 版本）；
2. **阶段 1.4 的停机时冻结指纹**（每库文件 sha256 · `.wal` 指纹 · 逐表 rows+fp）；
3. 阶段 2.2 的**逐文件 sha256 清单**（含 `manifest.csv`）+ 2.4 的 `RESTORE_PROBE` 输出 + **原备份验证前后 sha256 对照**；
4. **A.5 的进程身份清单**（PID / 启动时间 / 命令行原文）与**句柄独占探针**输出（含**鉴别力自检**与**观察窗** S1/S2/S3 留痕）；
5. **附库清单**（`PRAGMA database_list` 输出）与**逐库**保全结果、**逐库**七类 diff；
6. **§2.4.3 零打开自查五项**（脚本原文与命令行原样回传）；
7. **逐库三核表**（数据 / 对象 / 增量；增量栏须写清：已重放 / 已丢弃重拉 / 排除（含**损失行数与时间范围**））；
8. **A.7 的四证合取表**（每个库一行，四证勾填；缺证即不通过）；
9. 若有**排除项**：**损失清单**（表名 / **损失行数（多重集 `rows_lost` 口径，须同时给 `rows_added` 与受影响行数；不得用 `EXCEPT` 差集计数）** / 时间范围）+ **客户明确接受该损失的书面确认**（未获确认 ⇒ 不得替换生产库）；
10. 若执行阶段 4：重拉记录表（批号/**累计行数**/耗时/是否推进）+ 是否冻结 + 冻结时的批号与日志；
11. 阶段 5.2 的 7 项自查结果；
12. 任何失败步骤的**报错原文与截图**（不要改写、不要摘要）；
13. **未证表范围**（§2.4.6 A 的查询输出）：总表数 / 文本摘要一致表数 / **未证表数（含表名与涉及列）**，以及每表处置（B1 / B2 / B3）；**并须回传 §2.4.1 的分级状态原文**（`PASS` / `PARTIAL_UNPROVEN` / `PASS_EMPTY_DB` / `FAIL(…)`）；
14. **四类负例自检**（§2.4.7 A）：若是 N1 空清单 / N2 缺表 / N3 清单不全 / N4 双副本相等，须分别回传其**预期读数原文**（这些情形**不得**报「重建成功」）；
15. **空库构造与例外凭证**（§2.4.1.1 / §3.0 / §3.6）：若为真实空库，须回传 `PASS_EMPTY_DB` 的**完整输出行**（含 `marker_line`、`scope`、`objects=0`、`by_source` 五源明细）；若只能确认 main 基表为空，须回传 `PARTIAL_EMPTY_MAIN_ONLY` 原文；若 §2.4.1 为 `PARTIAL_UNPROVEN`/`PARTIAL_EMPTY_MAIN_ONLY`，**须附 §3.6 用户例外申请的业主书面原文**——**无例外文本不得开始阶段 3**，且例外**不使证④齐备**。

> 回传口径：读数**带来源**（哪台机、哪个库、哪次运行、哪条命令），避免用记忆值代替。

---

## 附录 A（v6 · 2026-11-15）· 依赖 · 写者证据双证 · 回放恢复条件 · 附库逐库保全 · 四证合取 · 竞态与冻结

> 本附录内容全部来自 2026-11-15 的**隔离实测**（duckdb **1.5.6** 与 **1.4.5** 两版读数**完全一致**；写者证据另在本机 Windows 上实测），证据件：`docs/evidence/firefly-site-procedure-isolation-validation-20261114.md` §八/§十一/§十二/§十四/§十五。
> **范围声明**：实测在**临时目录合成库**上完成，**不触生产库**；**未测面见 A.4**。

### A.1 两条路径的依赖与前置条件（实测）

| 路径 | 前置条件 | 实测读数 | 失败信息（原文） |
|---|---|---|---|
| `COPY FROM DATABASE` | 源库须**以具名 catalog ATTACH**（`ATTACH 'src.db' AS src`）后执行；**同一文件不可在同一进程重复 ATTACH** | OK；**数据指纹与源一致**；**对象零丢失**；重建后可继续写入 | `Unique file handle conflict: Cannot attach "src" - the database file "..." is already attached by database "w"` |
| `COPY FROM DATABASE` | 目标库**须用新建空文件** | 目标**已存在且非空时也不报错**——但旧内容不在复制范围，**不得复用旧目标文件** | — |
| `EXPORT DATABASE` + `IMPORT DATABASE` | 导出**当前库**；产物＝`schema.sql` + `load.sql` + 各表 CSV | OK；**导入后对象零丢失、数据指纹与源一致** | — |
| `EXPORT DATABASE` | ⚠ **只导出当前库**：已 ATTACH 的附库内容**不会**进入导出 ⇒ **必须逐库分别导出**（A.6） | `export_main_contains_sec_table=False` | — |
| 两者共同 | 路径**可用中文** | OK | — |
| 两者共同 | **库体大小不可作判据**（两次实测一增一减）⇒ 一律以**指纹 + 对象清单**为准 | 方向不一致 | — |

### A.2 无写者判定（**v3 表述已按 R04 修订：读探针不足**）

- **v3 中「读连接能打开，就是锁已释放的直接证据」的说法已撤回**——它过强。
- **实测 2×2 锁互斥矩阵**（holder 持锁方式 × opener 打开方式，跨进程）：

| holder | opener | 结果 |
|---|---|---|
| 读写 | 读写 | ❌ 被拒：`IO Error: Cannot open file "...": 另一个程序正在使用此文件，进程无法访问。` |
| 读写 | 只读 | ❌ 同样被拒 |
| **只读** | **读写** | ❌ **被拒**（只读持有者**也**阻断读写打开） |
| **只读** | **只读** | ✅ **通过**（两个只读可并存） |

- ⇒ **只读探针只能证明「此刻没有进程以读写方式持有该库」**；**只读持有者不会阻断只读探针** ⇒「只读能打开」**不足以证明没有进程持有该库**。
- ⇒ **只读持有者会阻断读写打开** ⇒ 备份与重建窗口内，**该库文件不应被任何进程打开（含只读打开者）**。
- ⇒ **正确判据＝四证合取**（A.7）。

### A.3 回放恢复条件（**阶段 3.4 的前置条件** · 实测）

1. **冲突目标必须被 UNIQUE 或 PRIMARY KEY 约束/索引覆盖**。实测：有主键表 ✅；**有唯一索引（非主键）的表 ✅**；**无任何键的表 ❌**：`Binder Error: The specified columns as conflict target are not referenced by a UNIQUE/PRIMARY KEY CONSTRAINT or INDEX`
2. **delta 必须先物化再回放**：`COPY (SELECT …) TO 'delta.parquet' (FORMAT PARQUET)` → `INSERT INTO t SELECT * FROM read_parquet('delta.parquet') ON CONFLICT (keys) DO UPDATE SET …`（回放后指纹与源一致）。
3. **delta 的边界必须可靠**：每张要回放的表都要有能判定「备份之后哪些行变了」的列或水位；**圈不出 delta 的表 ⇒ 不能回放**。
4. **幂等**：同一 delta 重放第二次指纹不变 ⇒ 中断后可安全重跑。
5. **回放后必须逐表指纹比对**：有键表 `equal=true`、无键表 `equal=false` ⇒ **「未回放表」必须单列清单**。
6. **无键表：不做任何行级回放**，**只整表重灌或明确排除**（A.3-7）。**排除＝恢复不完整**，不是完整恢复。
7. **排除的附加义务（v7 定稿口径）**：排除任何表必须同时给出 **①表名 ②**损失行数** ③损失的时间范围/水位**，并取得**客户明确书面接受**；**未获接受 ⇒ 不得替换生产库**。**损失行数的唯一合法口径见 A.3-8**。
8. **损失量口径（v7 重写 · 关键）**：**禁止用 `count(live EXCEPT backup)` 当损失行数**——它是**集合**语义（去重）且**方向是「live 独有」＝新增**，**看不见被删除、被覆盖的旧值**。实测三类反例（1.5.6 与 1.4.5 读数一致）：

   | 场景（相对备份态） | `count(live EXCEPT backup)` | 多重集 `rows_only_in_base`（真实丢失） | 失真形态 |
   |---|---|---|---|
   | 副本多出 1 行**重复行** | **0** | 0（确无丢失） | **完全看不见「多了」** |
   | 1 行**改值** | **1**（只看见新值行） | **1**（旧值丢失） | 看见的是**新增侧**，不是损失侧 |
   | 1 行改值 **+** 1 行**删除** | **1** | **2** | **低估一半** |

   ⇒ 正确口径＝**多重集 + 方向**：

   ```sql
   WITH l AS (SELECT h, count(*) AS c FROM (SELECT md5(CAST(t AS VARCHAR)) AS h FROM <live> AS t) GROUP BY h),
        b AS (SELECT h, count(*) AS c FROM (SELECT md5(CAST(t AS VARCHAR)) AS h FROM <base> AS t) GROUP BY h)
   SELECT coalesce(sum(greatest(bc - lc, 0)), 0) AS rows_lost,     -- ← 这一栏才是「损失行数」
          coalesce(sum(greatest(lc - bc, 0)), 0) AS rows_added
   FROM (SELECT coalesce(l.h, b.h) AS h, coalesce(l.c, 0) AS lc, coalesce(b.c, 0) AS bc
         FROM l FULL OUTER JOIN b ON l.h = b.h);
   ```

   **报告必须给三个数**：`rows_lost`（**损失行数**）· `rows_added` · **受影响行数**（一次改值＝消耗「丢失 1 + 新增 1」两行；**无键表无法区分「改值」与「删+增」**，须在报告中注明）。**差集计数一律不得当损失行数，也不得当作「变更行数」**。
9. **lost/added 预期表（R08 要求保留 —— 用它自检判据算得对不对）**

   | 场景（相对备份态） | 预期 `rows_lost` | 预期 `rows_added` | 实测（两版一致） |
   |---|---|---|---|
   | 副本多出 1 行**重复行**（与既有行逐位相同） | **0** | **1** | `{rows_lost:0, rows_added:1}` ✅ |
   | 1 行**改值** | **1** | **1** | `{1, 1}` ✅ |
   | 1 行**删除** | **1** | **0** | `{1, 0}` ✅ |
   | 1 行改值 **+** 1 行删除 | **2** | **1** | `{2, 1}` ✅ |

   > 用法：上表是**判据自检**——任一格与实测不符，即说明口径写错或脚本实现对不上，**不得**拿读数编解释。
   > 另注：**本判据的精度受整行指纹边界限制**（§2.4.5）：含 DOUBLE/REAL 列时数值差异可能不被计入；报告须声明。

10. **无键表：不得报告「受影响实体数」（R08 判据）**

    - 实测：无键表「**改 1 行**」与「**删 1 行 + 增 1 行**」的 `(rows_lost, rows_added)` **完全相同**（都是 `1 / 1`）。
    - ⇒ 无键表**只能**报「行级多重集 `rows_lost` / `rows_added`」；**「受影响实体数」「受影响记录数」「涉及多少条数据」一律不得报告**——实体不可识别，该数**无定义**；报告须原样写「**实体数：无定义（无键表）**」。
    - 有键表方可报告实体数，且须注明**键列**及**是否唯一**。

11. **「未证表范围」与不可外推的结论（R09 判据）**

    - **范围**：含 `DOUBLE/REAL/FLOAT` 列的表（枚举 SQL 见 §2.4.6 A）。实测可稳定枚举；`DECIMAL` 不在其内。
    - **结论强度**：这些表**只能**表述为「**文本摘要**一致」，**精确性未证**；**不得**表述为「重建成功 / 恢复完整 / 数据精确一致」；**也不得**用「加一句免责声明」的方式判通过（§2.4.6 B）。
    - **回传义务**：必须列明 **未证表数 + 表名 + 涉及列**，以及每表处置（B1 经审定方法 / B2 未证 / B3 客户书面接受）。**缺此项 ⇒ 该库验收不成立**。

12. **四类负例判定矩阵（R10 判据）**

    | 情形 | 预期读数 | 判定 | 是否可进入阶段 3 |
    |---|---|---|---|
    | N1 空清单（0 行且无 `#EMPTY_DB`） | `FAIL(EMPTY_FROZEN_LIST)` | `FAIL` | **否** |
    | N2 缺表 | `MISSING_IN_COPY: [...]` | `FAIL` | **否** |
    | N3 清单不全（副本多表） | `EXTRA_IN_COPY: [...]` | `FAIL` | **否** |
    | N4 双副本相等但冻结缺失/DIFF | `fp_equal=True` 但冻结比对未过 | **不得判 `PASS`**（源锚未证） | **否**（须补冻结比对） |
    | 真实空库 | `PASS_EMPTY_DB` ＋ 声明原文 | 可用（空库） | 可 |
    | 全部一致且未证表为空 | `RESTORE_PROBE PASS` | 可用 | 可 |
    | 全部一致但存在浮点表 | `PARTIAL_UNPROVEN(n: …)` | **部分未证** | **否**（默认）；确需 ⇒ §3.6 用户例外申请；证④不齐备 |
| 仅 main 基表为空（已核实）、整库有其它对象 | `PARTIAL_EMPTY_MAIN_ONLY(main_base=0, other_objects=n, by_source={…})` | **整库未证** | **否** |
| 只剩一个序列（C5c） | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_NOT_EMPTY(objects=1, by_source={'sequences': 1}))` | `FAIL` | **否** |
| 只有 scalar macro（C5f） | `FAIL(… by_source={'macros_scalar': 1})` | `FAIL` | **否** |
| 只有 table macro（C5g） | `FAIL(… by_source={'macros_table': 1})` | `FAIL` | **否** |
| 最小空库基线非 0（§2.4.1.2） | 五/六源过滤后计数 ≠ 0 | **判据未成立**：先修过滤器，不得改判据 | **否** |
| main_only 但 main 基表非空（C5e） | `FAIL(EMPTY_DB_MAIN_ONLY_CLAIM_BUT_MAIN_HAS_TABLES(…))` | `FAIL` | **否** |
| 五源之一不可探测（C5d） | `FAIL(EMPTY_DB_SCOPE_UNVERIFIED(src))` | `FAIL`（拒绝未覆盖状态） | **否** |
| CSV 带 UTF-8 BOM（C7） | 与同构造无 BOM **完全一致** | 同该构造 | 同该构造 |
| C1 标记在表头前 | `PASS_EMPTY_DB(marker_line=1, copy_tables=0)` | 可用（空库） | 可（NO-OP） |
| C2 标记在表头后 | `PASS_EMPTY_DB(marker_line=2, copy_tables=0)` | 可用（空库） | 可（NO-OP）；v10 在此 `int(None)` 崩溃 |
| C5 声明空库但副本有表 | `FAIL(EMPTY_DB_CLAIM_BUT_COPY_HAS_TABLES([...]))` | **不可用** | **否**（不许自证） |

### A.4 本附录的未测面（**不外推**）

- **内存峰值 / 超大数据量**：未在 447 万行 × 42 列、60GB 级库上实测；阶段 2.3 的空间预算是**估算**。
- **复制/导出过程中并发写**：只测到「文件锁层面读写均被拒」，**未测**持锁进程在复制过程中的写入。
- **观察窗的时间参数**：A.5.5 的采样次数/间隔是**流程性规定**，隔离实测只覆盖**单次探针的鉴别力**；**观察窗本身未在隔离环境实测**。
- **句柄级取证工具**：本机**无 Sysinternals `handle.exe`** ⇒ 句柄级清单降级为「句柄独占探针＋进程身份清点」。
- **其他 DuckDB 版本**：仅 **1.5.6** 与 **1.4.5** 已测（**1.4 线 2026-11-17 EOL**）。

### A.5 写者证据（**双证** · 主判据＝句柄独占探针）

#### A.5.1 句柄独占探针（**主判据**）

```powershell
$db = 'D:\...\QuantStudio\data\quantstudio.db'
try {
  $fs = [System.IO.File]::Open($db,'Open','Read','None'); $fs.Close()
  'HANDLE_PROBE OK  (无进程持有该库文件句柄)'
} catch {
  'HANDLE_PROBE FAIL :: ' + $_.Exception.Message   # 原文回传我方
}
```

- 实测：**只读持有者在场 ⇒ FAIL**（`The process cannot access the file '...' because it is being used by another process.`）；**读写持有者在场 ⇒ FAIL**；**持有者退出后 ⇒ OK**。
- ⚠ 探针**不给出持有者身份** ⇒ 必须配 A.5.2。

#### A.5.2 进程身份清点（**谁在持有/谁可能写**）

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match 'quantstudio\.pipeline\.daemon|main_gui|quantstudio\.db') } |
  Select-Object ProcessId, Name, CreationDate, CommandLine | Format-List | Out-String
```

- **已知写者身份清单**：采集 daemon（`python -m quantstudio.pipeline.daemon`）· GUI（`main_gui.py`）· 自研取数/回补脚本 · BI/报表工具 · 备份软件与同步盘 · Excel 插件 · 杀毒软件实时扫描。
- **匹配面必须双向验证**：按「脚本文本」匹配会**命中包装进程**（`powershell.exe`/`node.exe` 的命令行含整段脚本）——本机实测 `identity_matches=3`（2 包装 + 1 真持有者）；命令行有模块形态与脚本路径形态，**两种都要查**。
- **回传要求**：**PID + 启动时间 + 命令行原文**整行回传；只说「已停」不算证据。

#### A.5.3 探针的鉴别力自检（**没通过自检的「通过」不算证据**）

| 现场状态 | 只读探针 | 句柄独占探针 | 进程身份清点 |
|---|---|---|---|
| 无持有者 | ✅ OK | ✅ OK | — |
| **只读持有者** | ✅ **OK（漏检！）** | ❌ **FAIL（识破）** | ✅ 命中 PID/命令行 |
| 读写持有者 | ❌ FAIL | ❌ FAIL | ✅ 命中 PID/命令行 |
| 持有者退出后 | ✅ OK | ✅ OK | 命中数归零 |

- **硬判据**：**未先完成上述鉴别力自检（或自检未通过）的探针，其任何「通过」结果一律不可用**。

#### A.5.4 窗口独占要求

- **只读持有者会阻断读写打开** ⇒ 备份/重建窗口内，**除本流程自身外，不得有任何进程打开该库文件**（含只读打开者）。
- 若探针失败 ⇒ **不要重试拷贝**，先按 A.5.2 找到持有者并停用它（**不要强杀**）。

#### A.5.5 锁观察窗（**仅证明采样时点**）

| 采样 | 时点 | 独占探针 | 身份清点新增命中 | 记录 |
|---|---|---|---|---|
| S1 | 开始拷贝前 | OK / FAIL | 无 / 有 | |
| S2 | 拷贝进行中（≥30s 后） | OK / FAIL | 无 / 有 | |
| S3 | 拷贝完成后 | OK / FAIL | 无 / 有 | |

- **S1/S2/S3 只证明各采样时点的状态**；**采样间隙内仍可能存在持有者** ⇒ 观察窗是**必要非充分**条件，**不能**单独证明「停机成立」。
- **任一次 FAIL、或任一次身份清点出现新增命中 ⇒ 观察窗不通过 ⇒ 备份作废**，回到阶段 1 重做。
- ⚠ 观察窗的时间参数属**流程性规定**，隔离实测只覆盖**单次探针的鉴别力**；**观察窗本身未在隔离环境实测**。

#### A.5.6 逐库适用

- 锁与句柄都是**按文件**的 ⇒ **每个库文件（含附库）** 各做一次 A.5.1 / A.5.2，并各记一份观察窗。

#### A.5.7 探针关闭后的再打开竞态（**v6 新增 · TOCTOU**）

- **机理**：独占探针在**检查完成后立即关闭句柄**（`$fs.Close()`）⇒ 从探针关闭到拷贝真正开始之间**存在无人持锁的窗口**；此窗口内若有进程打开并写入，就会改变被拷贝的内容。
- **三种实测表现（两版一致）**：

| 情形 | 实测结果 | 含义 |
|---|---|---|
| 写者在场**持续持锁** | 文件级拷贝被系统**直接拒绝**：`PermissionError: [Errno 13] Permission denied` | 竞态窗口表现为**响亮失败**，不会静默产出错误副本 ⇒ **不要重试**，回阶段 1.2 |
| 写者「**写完即退**」（在探针之后、拷贝之前进来又退出） | 拷贝**成功**，且**源与副本 sha256 相等（True）**，但**与停机时冻结指纹不一致（False）** | ⚠ **sha256 相等检不出静默推进**；只有「与停机时冻结指纹逐表比对」能检出 ⇒ 必须做阶段 1.4 |
| **同一条只读连接内完成导出/拷贝** | 该连接存续期间**外部写者被拒**（`另一个程序正在使用此文件`） | ⇒ **在同一条只读连接内完成「导出＋拷贝＋算指纹」可消除该竞态窗口**（推荐做法）；我方实测 `EXPORT DATABASE` 走只读连接时**源库 sha256 不变、旁边无 `.wal`** |

- **缓解四条（按优先级）**：①**停机时冻结指纹**（阶段 1.4）＋ **与冻结指纹逐表比对**（这是唯一能检出静默推进的判据）；②**在同一只读连接内完成导出/拷贝**；③观察窗＋进程身份清点（缩小窗口、发现新进程）；④**拷贝失败即停不重试**。
- **边界**：以上都**不能**证明「停机成立」这一事实，只能提供**举证**；最终判据仍是 **A.7 的四证合取**。

### A.6 附库逐库保全

#### A.6.1 枚举（**先知道有几个库**）

```sql
PRAGMA database_list;
```

- 实测形态：`[["20637","master","…\\master.db"],["22076","sec","…\\attached.db"]]` ⇒ **每个库一个独立 `.db` 文件**。
- ⇒ **附库不会自动进入主库的重建结果**；备份侧它们都在 2.2 的逐文件清单里，重建侧必须**逐库保全**。

#### A.6.2 逐库保全的两条可执行配方（**均已实测**）

```sql
-- 配方一（COPY 路线，首选）
ATTACH '…\\attached.db' AS sec;
ATTACH 'D:/work/rebuilt_sec.db' AS dstsec;
COPY FROM DATABASE sec TO dstsec;      -- 实测 OK：目标库含附库的表，数据指纹一致
```

```sql
-- 配方二（EXPORT + IMPORT 路线）
ATTACH '…\\attached.db' AS sec;
USE sec;
EXPORT DATABASE 'D:/work/exported_sec';   -- 实测产物含该库的表
IMPORT DATABASE 'D:/work/exported_sec';   -- 另开连接还原：表清单与指纹与源一致
```

- ⚠ **命名陷阱**：导出 CSV 文件名会被规范化（实测 `s1`→`s_.csv`）⇒ **表名以 `IMPORT` 之后的清单为准**。

#### A.6.3 逐库核验口径（三核）

- **每个库（含附库）各自完成**：备份 → 逐文件 sha256 → **停机时冻结指纹** → 隔离恢复验证 → 重建/保全 → **七类覆盖清单 diff** → **增量归属** → 库体积 → **四证合取一套**。
- **三核**：**①数据**（指纹一致）· **②对象**（七类 diff 为空）· **③增量**（该库备份后新增行的处置：已幂等重放 / 已丢弃并重拉 / 不可回放→排除（**须填损失行数与时间范围，并取得客户明确接受**））。
- **缺任一库的证据 ⇒ 视为未完成，不得替换生产库**；**覆盖清单与指纹比对都按库分别出具**，不得合并成一个总表。

### A.7 四证合取判据（**R05 判据 · v6 补入冻结与竞态**）

| 证 | 内容 | 出处 | 齐备判据 |
|---|---|---|---|
| **证① 进程身份** | 谁可能持有/写入该库文件（PID / 映像名 / 启动时间 / 命令行原文） | A.5.2 | 输出中**无**该库相关的写者进程；**每个库逐个**核 |
| **证② 锁观察** | 独占探针（含**鉴别力自检**）＋ **观察窗多次采样** | A.5.1 / A.5.3 / A.5.5 | 自检**已做且通过**；S1/S2/S3 **全 OK** 且**无新增命中**；⚠ **只覆盖采样时点**，不构成持续无持有者的证明 |
| **证③ 备份 SHA256 ＋ 停机时冻结指纹** | 逐文件内容一致 **且** 备份内容等于**停机时点**（冻结指纹、WAL 指纹） | 2.2 / 阶段 1.4 | `ALL_SHA256_MATCH` **且** 逐表 rows+fp 与冻结指纹一致 **且** WAL 指纹一致 |
| **证④ 隔离恢复** | 备份**真的能被打开**且内容与源一致；**原备份零打开**（**就本流程已审入口与命令而言**；2.4.3 五项自查全过）＋ 原备份与 WAL 指纹冻结 ＋ **§2.4.1 结论必须为 `PASS` 或 `PASS_EMPTY_DB`**（后者须附 `marker_line` + `scope` + `objects=0` 的**整库取证**）；`PARTIAL_UNPROVEN` / `PARTIAL_EMPTY_MAIN_ONLY` **不计作证④齐备**，且**默认不得进入破坏性阶段 3**（确需者只能走 §3.6 用户例外申请，**例外不使证④齐备**）；`FAIL` 直接不合格 | 2.4 | `RESTORE_PROBE PASS` ＋ **原备份验证前后 sha256 一致、无新增 .wal**；**逐库各做一次** |

**合取规则**

- **证①②③④ 全部齐备 ⇒ 才可判定「停机成立」且「回退点可用」。**
- **缺任何一证 ⇒ 不得声称停机成立、不得进入阶段 3、不得用备份替换生产库。**
- 证①②属**停机/窗口**举证（时间维度，**受竞态限制**），证③④属**备份内容**举证（内容维度）——**两组互不替代**。
- **逐库适用**：现场有几个 `.db`（含附库），证①②③④ 就要有几套。

**四证合取表（请回传勾填；缺证即不通过）**

| 库文件 | 证① 身份 | 证② 锁观察（自检 + S1/S2/S3） | 证③ SHA256 ＋ 冻结指纹 | 证④ 隔离恢复（副本上执行，前后 sha256 一致） | 结论 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
|  |  |  |  |  |  |
