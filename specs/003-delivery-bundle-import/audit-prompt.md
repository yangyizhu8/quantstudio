# 审核请求：QuantStudio 数据交付包导出与导入校验（specs/003）

> 本文件是给独立审核方（ZCode / GLM）的完整审核提示词，可整段转发。
> 审核对象：`specs/003-delivery-bundle-import/spec.md`
> 项目根：`D:\miniQMT策略实盘\QuantStudio`

---

## 一、角色与任务

你是独立审核方。请对 `specs/003-delivery-bundle-import/spec.md` 做**方案级审核**（不是代码审核，还没有代码）。目标不是夸方案，而是**找出实施后会出事的地方**。允许并鼓励说"这条实现不了/这条约束是错的/这里漏了场景"。

---

## 二、项目背景

**QuantStudio** 是一套本地量化研究+实盘数据框架（Python + DuckDB 主库 + SQLite 附属库 + PyQt6 GUI + MCP/QuestDB 云端数据源）。核心数据落点：

- `data/quantstudio.db`（DuckDB，**实测 50.37 GB**——2026-09-17 交付包口径，当前约 58 GB；注意项目内 `data/README.md` 仍写"约 12 GB"，是过时文案，不要采信）：行情、财务、估值等业务表，加上运行态表 `source_watermark`（增量水位）、`trade_calendar`、`qfq_*` 管理表（14 张，描述复权世代与切世代意图）。
- `data/qfq_aux.db`、`data/qfq_aux_mcp_gen1.db`（SQLite 两代复权因子库）：由 `config/profiles/mcp_only/qfq_aux_paths.json` 路由；运行时由 `released` 开关 + 主库 `qfq_active_cutover` 记录两个条件共同决定用哪一个（两者都满足才走 gen1，否则 fail-secure 回 legacy）。
- `data/quarantine.db`（隔离区）、`data/batch_audit.db`（采集审计台账）：均为 SQLite，**客户本地运行态**；代码中两者构造时会 `mkdir` + `CREATE TABLE IF NOT EXISTS`，缺失会自动创建。
- `data/snapshots/`（写锁目录）、`data/task_resume/`（一次性任务续传游标）：本地运行态。

**交付现状**：客户拿到数据的方式是「删除 `data/quantstudio.db`，再复制一份新的」（整库替换，2026-10-30 用户确认）。既有导出脚本 `scripts/questdb_package_part1.py` **只导主库 51 张表，不含任何附属库，也没有清单与校验和**。

**由此产生的真实故障面**：客户覆盖主库后，`qfq_aux*.db` 仍是旧版 → 主库 `qfq_*` 管理表描述的世代与因子库实际内容脱节 → 复权链在 fail-closed 处拒绝启动，且表象最容易被误判为"数据坏了"。

---

## 三、本次需求的意图（演进链，供理解为什么是现在这个形态）

1. 起点需求：导入数据包后"按每表最新数据往前推一天预置水位线"，避免无水位时增量退化为全历史重拉。
2. 核查中发现：水位唯一权威存在**主库表**里（不是配置文件），整库替换会把它一起带过来——"预置水位"的前提在覆盖模式下不成立（已被降级为另案）。
3. 核查中发现更严重的问题：**交付只给主库，不给因子库**，导致世代错配（上节所述）。
4. 于是本次需求转为：**做一套「交付包导出 + 导入校验」的双端能力**，保证客户拿到的一整套数据自洽、可自证、可回滚。

---

## 四、目标（要实现什么）

**本机侧（实施人员）**：GUI「数据交付」页签 → 点「交付包导出」→ 程序先做前置校验（不合格不出包）→ 通过后在**同一写锁窗口**内导出白名单文件 → 产出可自证的 `manifest.json`。

**客户侧**：点「导入校验」→ 程序**先只读校验包**（不碰任何现有文件）→ 通过后 → 备份 → 事务性覆盖 → 复验 → 提示「可点全部执行（增量）」。

**硬约束**：不影响回测、策略转换等既有功能（表结构与配置零变化）；全程不触碰 `config/`、客户台账、日志、凭据。

---

## 五、具体实现流程与逻辑（审核时请对照 spec 的 FR 逐条核）

### 5.1 导出（本机）

前置校验（fail-closed，任一不过则**不产出任何文件**）：
1. 主库 `detect_schema_status` ∈ {`COMPLETE_2_1`, `COMPLETE_2_2`}
2. 白名单文件均可只读打开
3. 无其他写锁持有者
4. 无运行中的 daemon
5. 目标盘空间充足

导出六步：
1. 取写锁（`data/snapshots/.write_lock`）
2. checkpoint（DuckDB `CHECKPOINT`；SQLite `PRAGMA wal_checkpoint(TRUNCATE)`）
3. 按**硬编码白名单**复制：`quantstudio.db`、`qfq_aux.db`、`qfq_aux_mcp_gen1.db` + 各自 `-wal`/`-shm`（存在则带）
4. 写 `manifest.json`（文件清单 + 大小 + SHA256 + 导出时刻 + 导出方程序版本 + 主库 schema 实测状态 + `released` 值 + `qfq_active_cutover` 世代 + 每表 row_count / max_date / 水位）
5. 红线自检：白名单外文件数 = 0；`*.env` 命中 = 0；每个 db 可只读打开
6. 释放写锁

**必须排除**：`batch_audit.db`、`quarantine.db`、`task_resume/`、`daemon_status.json`、`snapshots/`、`logs/`、`mcp_landing/`、`staging*/`、`backup*/`、`*_c4resume_staging.db`、任何 `*.env`。

**为什么必须文件级复制、不能用 CTAS/EXPORT**：既有先例已实测——CTAS 只复列与数据、不复制 PK/NOT NULL/CHECK/UNIQUE 约束，而写入者的 schema 安全闸用指纹校验这些约束，会把包判为 `partial_or_mixed` 并**拒绝启动**。依据见 `scripts/questdb_package_part1.py` 的 file-copy 分支注释。

### 5.2 导入（客户侧）

八步：
1. 读配置（`export_dir` / `import_dir`）与 `DATA_ROOT`（**只读显示，不允许用户选**——`_paths.py` 才是唯一真相源，若允许另选会出现「备份 A、覆盖 B」）
2. 前置检查：daemon 未运行、无写锁、`DATA_ROOT` 可写、磁盘空间满足 FR-015 的分卷门槛（同卷 rename 模式仅需「包体积 × 1.1」；跨卷复制模式需「备份体积 + 包体积 × 1.1」）
3. 校验包（只读，不碰现有文件）——四要素：
   ① `manifest.json` 存在 + 每文件 SHA256；
   ② 主库 schema **实测** vs **当前程序期望** vs manifest 记录（三方比对）；
   ③ `released`（读客户本地）+ `qfq_active_cutover`（读包内主库）联合判定客户实际会用的因子库，确认该文件在包内且校验通过；
   ④ 白名单完整性
4. 失败 ⇒ 按**三类原因**分派提示（包问题 → 重新获取包 / 程序版本不匹配 → 更新程序 / 配置不匹配 → 检查配置），**终止且零改动**
5. 备份到 `data/backup_import_<YYYYMMDD_HHMMSS>/`：**同卷用 `rename` 移动（零额外空间、O(1)）**，跨卷才退回复制；并回验备份可独立只读打开
6. 覆盖：先复制为临时名，全部成功后改名；任一失败则删临时并回滚
7. 复验：重跑校验 + 输出逐表四态（MISSING / BEHIND / AHEAD / OK）+ 一句话结论
8. 放行：提示可点「全部执行（增量）」，**不自动触发采集**

**双模式**：`DATA_ROOT/quantstudio.db` 存在 ⇒ 更新模式（备份→覆盖）；不存在 ⇒ 安装模式（跳过备份、直接放置、提示"无文件可备"）。

**覆盖必须「只增改不删」**：全新客户的 `data/` 下只有 `snapshots/`、`.gitkeep`、`README.md`（git 文件），覆盖不得删除它们。

### 5.3 配置与界面

- 新增独立 `config/delivery_config.json`，只放两项：`export_dir`、`import_dir`。**数据目录不写入**（已有真相源）。原子写。
- GUI 新增独立页签「数据交付」，**不与既有「K 线分库导出」页签混放**（后者产物供 QMT/PTrade 挂载、不覆盖生产文件，风险等级不同）。
- 按钮：`交付包导出`、`选择交付包目录`、`导入校验`。配置为空时按钮禁用并提示。

### 5.4 双端对等

导出与校验各一个内核函数，CLI 与 GUI 都是薄壳；同一输入下产物（文件清单 + 逐文件 SHA256）与校验报告须一致（manifest 时间戳除外）。

---

## 六、审核对象

```
D:\miniQMT策略实盘\QuantStudio\specs\003-delivery-bundle-import\spec.md
```

辅助参考（同项目内，可用于核对约束是否真实）：

- `quantstudio/pipeline/qfq_schema_status.py`（schema 状态与放行集合）
- `quantstudio/pipeline/writers.py`（`_init_tables` 里的 schema 安全闸）
- `quantstudio/pipeline/qfq_aux_router.py`（`resolve_runtime_aux_path` 的双条件路由）
- `config/profiles/mcp_only/qfq_aux_paths.json`（世代映射）
- `quantstudio/pipeline/quarantine.py`、`quantstudio/pipeline/daemon.py`（`BatchAudit` 自动建表）
- `quantstudio/_paths.py`（`DATA_ROOT` 解析优先级）
- `quantstudio/pipeline/snapshot_lock.py`（写锁协议）
- `scripts/questdb_package_part1.py`（既有导出先例与 CTAS 教训）
- `data/README.md`、`config/quality_baseline.json`（需要同步/会误报的两处）

---

## 七、重点审核项（请逐条给结论：成立 / 有问题 / 无法判断）

1. **schema 放行集合的完备性**：FR-003 只放行 `COMPLETE_2_1` / `COMPLETE_2_2`。若实施机主库是 `EMPTY_OR_NEW`（新装机、未采集过），是否应该允许导出？允许的话导出的"空库"对客户有没有意义？不允许的话，实施方会不会卡在"想导但不能导"？
2. **跨文件原子性在 Windows 上是否成立**：FR-016 要求"全部成功后原子改名"。Windows 上 `os.replace` 对已被打开的文件会失败，且三个 db 之间**不存在真正的跨文件原子**。请判断：规格里的"事务性"表述是否过于理想？应改成什么可实现的语义（例如"逐个改名 + 失败按日志回滚 + 回滚后复验一致性"）？
3. **安装模式的判据强度**：FR-014 用"`DATA_ROOT/quantstudio.db` 是否存在"区分模式。如果该文件**存在但损坏**（0 字节、无法打开、schema 为 `UNKNOWN`），判据会误入"更新模式"并试图备份一个坏文件。判据是否应改为"存在且可只读打开且 schema 可判定"？
4. **`released` 缺失分支**：FR-010 第③条要求读客户本地 `released`。若客户的 `qfq_aux_paths.json` 不存在（或损坏），路由会 fail-secure 回 legacy。规格是否覆盖了"配置缺失"这一分支？校验报告此时应给出什么结论？
5. **manifest 的"每表最新日期"取列口径**：FR-006 要求每表 `max_date`。这张表该取哪一列？如果实现方统一用 `trade_date`，会与运行时水位口径（按 `watermark_basis`）错位。规格是否已经把这一点说清楚？若不够，请指出该补什么。
6. **文件级 SHA256 能否作为双端一致性判据**（FR-028 / SC-009）：SQLite 与 DuckDB 文件在两次导出之间，即使逻辑内容相同，**页内空闲空间/文件布局是否可能不同**导致 SHA256 不一致？若能，判据是否应改为"逻辑内容摘要"（每表行数 + 关键列校验和）而非文件 SHA256？
7. **约 58 GB 的校验耗时与取消语义**：SC 里没有性能指标。客户点「导入校验」后算 SHA256，58 GB 在普通 SSD 上约 2–5 分钟、机械盘可能 10 分钟以上。规格只要求"进度反馈与取消"（FR-019）。这个要求够不够？是否需要给"可接受耗时上限"或"分段校验 + 快速失败"？另外：这么大的文件，是否应该改用**区间抽样校验 + 全量校验可选**？
8. **备份方式（FR-015 已改为 rename 优先）**：规格要求同卷时用 `rename` 把旧库移入备份目录（零额外空间、O(1)），跨卷才退回复制。请审查：①`rename` 期间旧库不在原位，若进程被强杀（无 atexit）如何恢复？②NTFS 上同卷跨目录 rename 是否原子？③备份目录若在 `DATA_ROOT` 子目录内，`rename` 是否仍有意义？④这种"移动而非复制"的备份，与用户对"备份"的直觉（应该是副本）不符，是否会造成误解与误删？⑤是否应同时提供"复制备份"选项给磁盘充裕的客户？
9. **质量审计误报**（FR-033）：导入后 `config/quality_baseline.json` 的 `max_watermark` 仍是 2026-09-24 的旧快照，会持续误报。规格的处理方式是"只提示、不自动改"。这个取舍是否正确？还是应该提供"一键重建基线"？
10. **纯增益与铁律合规**：本特性属"新增能力型"模块。规格 FR-029/FR-030 声明了不改写路径、不改 schema、不改 config。请判断这些声明是否足够覆盖"不影响回测与策略转换"的举证责任，还缺哪些可验证判据（例如 SC-008 的"结果逐位一致"是否真的可执行——数据内容变了，回测结果本来就会变，这条判据怎么落地？）。

---

## 八、额外请你主动挑刺的三件事

1. **有没有"看起来合理但会造成数据损失"的步骤**？（上一轮我们自己已经抓出两个：让用户选 data 目录会导致"备份 A、覆盖 B"；先覆盖再校验会不可回滚。请找第三个。）
2. **有没有 FR 之间互相矛盾的地方**？（例如导出要求"同锁窗口"，但导入校验是只读的，两者对锁的使用是否冲突？）
3. **验收判据 SC 里有没有"无法执行"或"必然通过"的假判据**？

---

## 九、输出格式要求

请按以下结构输出：

### A. 总体结论
一句：**通过 / 有条件通过（列出条件）/ 退回重写**。

### B. 问题清单（按严重度排序）
每条包含：
- **编号**：Q-01, Q-02 …
- **严重度**：阻断 / 重要 / 建议
- **对应条目**：FR-xxx / SC-xxx / 章节名
- **问题**：具体说清哪里不对
- **依据**：引用代码/文件或逻辑推理
- **建议改法**：可执行的具体修改

### C. 遗漏场景清单
你认为规格没覆盖、但实施时一定会遇到的情况（每条一句话说明为什么必须处理）。

### D. 逐条回应第七节
对第七节的 10 个重点项，逐条给"成立 / 有问题 / 无法判断"+ 一句理由。

### E. 你认为实施后最可能出事的三个点
按可能性排序，每个给一句"出事后客户会看到什么现象"。

---

## 十、审核纪律

- **不要因为方案细节多就默认它正确**；每一条约束都要能落到代码或逻辑上。
- **不要给出无法验证的建议**（如"建议加强健壮性"）；每条建议都要能变成一条 FR 或一个测试。
- 若你判断某条约束**在代码里不成立**，请明确指出文件与行为，不要凭印象。
- 若你认为整体方向错误，直接说，并给出替代方向。
