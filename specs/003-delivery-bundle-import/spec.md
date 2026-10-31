# Feature Specification: 数据交付包导出与导入校验（CLI + GUI 双端）

**Created**: 2026-10-31 | **Status**: **Approved for plan/tasks**（v4：v2 修 3 阻断 → v3 落 2 项范围裁定 → v4 补 manifest 版本化 + 第四类失败 + Windows 环境探测）| **Input**: 会话 2026-10-30/31 需求演进链 + 评估制品 `.specify/assessments/data-bundle-export/`、`.specify/assessments/watermark-preset-from-package/` + 独立审核报告（A 有条件通过 / Q-01~Q-08 / C-1~C-6 / D-1~D-10 / E-1~E-3）+ v2 复审与范围裁定 + v3 复审四项补充

## 背景与目标（Why）

客户侧导入数据的方式是：**删除 `data/quantstudio.db`，再复制一份新的 `quantstudio.db`**（整库替换，2026-10-30 用户确认）。这条动作链上存在三个结构性问题，本特性解决前两个（第三个另案）：

1. **交付单元 ≠ 数据依赖单元**：`data/` 下与主库**世代绑定**的因子库（`qfq_aux.db` 走 xtquant-legacy、`qfq_aux_mcp_gen1.db` 走 mcp-gen1）不随包交付。客户覆盖主库后，主库 `qfq_*` 管理表描述的世代与因子库实际内容脱节，QFQ 链在 fail-closed 处拒启；表象极易被误判为「数据坏了」。
2. **现状无一键产出「同刻自洽集」的手段**：既有 `scripts/questdb_package_part1.py` 只导主库 51 张表，不含任何附属库；无清单、无校验和、无世代记录。
3. **客户侧无双端校验入口**：客户无法在覆盖前判断「这个包在我的环境能不能用」，也无回退路径（主库体积大：2026-09-17 交付包实测 50.37 GB，当前约 58 GB；覆盖即不可逆）。
4. （另案，不在本规格）包内水位与包内数据是否一致——由 `.specify/assessments/watermark-preset-from-package/` 的探针决定是否需要修正功能。

**目标**：把「一次交付」变成**可自证、可回滚、可崩溃恢复、双端可操作**的流程——本机一键导出白名单内的自洽数据包；客户侧一键校验并在通过后由程序完成备份与覆盖；全程不影响回测、策略转换等既有功能。

**量级认知（仅背景，不得抄进界面文案）**：主库为数十 GB 级（2026-09-17 实测 50.37 GB，当前约 58 GB）。体积随数据增长，界面上的一切体积读数 MUST 实时读取，禁止写死数字。

## 本轮修订要点（v1 → v2，对应审核阻断项）

| 阻断项 | 修订方向 |
|---|---|
| **Q-01** 非空 WAL 随包发货 | 白名单删除 `-shm`；`-wal` 由「存在则带」改为「checkpoint 后必须归零，非零即 fail-closed」——非零 WAL 是同刻自洽未达成的证据，不是要打包带走的东西 |
| **Q-02** 变更期间不持锁 + 崩溃无重入 | 新增：⑤备份起持有写锁至⑦完成；新增 `import_journal.json` 崩溃恢复；FR-016 删除「原子/事务性」承诺，改为「staged 改名 + journal + 崩溃可恢复 + 完成后复验」 |
| **Q-03** 坏库回验死锁 | 模式由「双模式」扩为**三模式**（更新 / 修复 / 安装）；备份回验在「源文件本就打不开」时降级为取证备份并继续 |
| **v3 复审裁定①** | 删除 `backup_mode` 配置项（FR-020 改两项）——复制式与移动式终态相同、不构成能力增量；复制机制仅作跨卷内部路径（FR-015）保留 |
| **v3 复审裁定②** | 修复模式**保留原件**（`rename` 入备份，零额外占用），结果页补释放提示与取证摘要；否决「摘要后删除」——探测失败 ≠ 数据无价值 |

## User Scenarios & Testing

### User Story 1 - 本机一键导出交付包（Priority: P1）

实施人员在本机打开 GUI「数据交付」页签，选择导出目录后点「交付包导出」：程序先做导出前置校验（不通过则不出包），通过后在**同一写锁窗口内**把白名单文件复制到目标目录，并写出可自证的 `manifest.json`。

**Why this priority**: 没有合格的上游包，后续一切都是空谈；且「出一半的包」比不出包更危险（客户会以为拿到了完整的）。

**Independent Test**: 在一次准备好的生产库上执行导出，检查产出目录的文件清单、`manifest.json` 内容、以及白名单外文件数是否为 0。

**Acceptance Scenarios**:
1. **Given** 主库 schema 状态为 `COMPLETE_2_1` 或 `COMPLETE_2_2`、无其他写锁持有者、daemon 未运行、目标盘空间充足 **When** 点「交付包导出」 **Then** 产出包含白名单文件的交付目录与 `manifest.json`，界面显示文件名、体积（实测）与耗时
2. **Given** 主库 schema 为 `PARTIAL_OR_MIXED` / `UNKNOWN` 或其他非放行状态 **When** 点「交付包导出」 **Then** **不产生任何文件**，界面给出「库状态不合格，需先修复」及实测状态值
3. **Given** 执行 checkpoint 后任一 `-wal` 文件仍非零字节（或无法确认已归零） **When** 继续导出 **Then** **fail-closed，不产出任何文件**，并报「同刻一致未达成：WAL 未归零（实测 X MB）」——MUST NOT 把非空 WAL 装入交付包
4. **Given** 导出目标目录内已存在上一轮交付包 **When** 执行导出 **Then** 强制在目标目录下新建 `<YYYYMMDD_HHMMSS>/` 子目录产出，**旧包永不触碰**（旧包是客户的回退资产）
5. **Given** 导出完成 **When** 检查产出目录 **Then** 其中不含任何 `*.env`、日志、MCP 落盘缓存、staging、backup 类文件（数量必须为 0），也不含任何 `-shm` 文件

### User Story 2 - 存量客户一键校验并导入（Priority: P1）

客户已有一份可运行的库，拿到交付包后在 GUI 点「导入校验」：程序先**只读校验包**，通过后再持锁、备份、覆盖、复验。校验不通过时**零改动**。

**Why this priority**: 客户场景的核心价值；顺序错了（先覆盖再校验）会造成不可逆数据损失。

**Independent Test**: 在「已有库」的演练环境执行一次成功导入 + 一次故意破坏包的失败导入，检查成功路径的四态报告与失败路径下旧库的完整性。

**Acceptance Scenarios**:
1. **Given** 交付包完整、程序版本与包内 schema 匹配、`released` 与实际世代可判定、运行时因子库路径与导入目标路径一致 **When** 点「导入校验」 **Then** 依次完成「校验 → 持锁 → 备份 → 覆盖 → 复验」，最终显示「可增量」结论与逐表四态
2. **Given** `manifest.json` 缺失、SHA256 不符、或白名单文件缺失 **When** 点「导入校验」 **Then** 提示「交付包不完整或已损坏，请重新获取」并**终止，不触碰任何现有文件**
3. **Given** 包内主库 schema 实测与当前程序期望不一致 **When** 点「导入校验」 **Then** 提示「程序版本与数据版本不匹配，请更新程序」并终止
4. **Given** `released` 指向的因子库不在包内、或客户配置解析出的运行时因子库路径与导入目标路径不一致、或 `DATA_ROOT` 不是有效项目数据目录 **When** 点「导入校验」 **Then** 提示「配置不匹配，请检查」并给出两路径对照，终止
5. **Given** 校验通过但覆盖过程中某个文件失败（程序内可捕获） **When** 覆盖中断 **Then** 按 journal 回滚到备份状态，**不得**留下「主库新、因子库旧」的混合状态
6. **Given** 覆盖与复验完成 **When** 客户点「全部执行（增量）」 **Then** 采集能按包内水位续拉（不自动触发采集，由客户手动发起）

### User Story 3 - 全新客户首次安装（Priority: P1）

客户从 GitHub 拉到项目代码，`data/` 下只有 `snapshots/`、`.gitkeep`、`README.md`（无任何 db 文件）。用交付包完成首次数据落地。

**Why this priority**: 最常见的交付场景，且与「更新」语义不同（无文件可备份），流程若不分岔会给出误导性提示。

**Independent Test**: 在干净 clone 的项目上执行一次首次导入，检查落地后的 db 集合、以及首次启动采集是否会自动创建 `quarantine.db`/`batch_audit.db`。

**Acceptance Scenarios**:
1. **Given** `DATA_ROOT` 下不存在 `quantstudio.db` **When** 点「导入校验」 **Then** 进入**安装模式**：跳过备份、直接放置白名单文件，并明确提示「首次安装，未做备份（无文件可备）」
2. **Given** 安装模式完成 **When** 检查 `data/` 下既有文件 **Then** `snapshots/`、`.gitkeep`、`README.md` **仍然存在**（覆盖只增改、不删除）
3. **Given** 安装模式完成但 `quarantine.db`/`batch_audit.db` 不在包内 **When** 首次启动采集 **Then** 两个库自动创建，采集正常，不报缺失错误
4. **Given** `DATA_ROOT` 不可写（权限/杀软） **When** 点「导入校验」 **Then** 在备份/覆盖之前就提示「数据目录不可写」，零改动

### User Story 4 - 坏库修复（Priority: P1，v2 新增）

客户现存主库已损坏（0 字节 / 无法打开 / schema 为 `UNKNOWN`）——这正是最需要导入修库的场景。

**Why this priority**: 若修复路径被自己的保护机制卡死，客户将永远无法用交付包恢复，这是审核 Q-03 指出的阻断缺陷。

**Independent Test**: 构造一个 0 字节的 `quantstudio.db`，执行导入，验证流程不因「备份回验失败」而终止。

**Acceptance Scenarios**:
1. **Given** `DATA_ROOT/quantstudio.db` 存在但无法只读打开 **When** 点「导入校验」 **Then** 判定为**修复模式**：MUST 先探测源文件可读性；不可读 ⇒ 备份保留作为**取证**，界面明示「旧库在导入前已损坏（取证备份已保留）」，并**继续覆盖**
2. **Given** 源文件可打开、但备份过程自身出错 **When** 备份回验 **Then** 判定为**真失败**，终止并回滚（不得归入修复模式的放行分支）
3. **Given** 修复模式完成 **When** 复验 **Then** 输出四态与「可增量」结论，且结果页保留「原库已损坏」的记录

### User Story 5 - 失败与崩溃可回滚（Priority: P1）

任何一步失败，或进程被强杀/断电，客户都能回到导入前的状态，且重入时不会被半成品误导。

**Why this priority**: 主库体积为数十 GB，无回退的导入等于一次不可逆操作；崩溃重入误判会污染备份（审核 Q-02②）。

**Independent Test**: 人为在覆盖阶段杀死进程，重启后再点导入，验证 journal 检测与二选一恢复。

**Acceptance Scenarios**:
1. **Given** 已完成备份、覆盖中途程序内失败 **When** 进入回滚 **Then** 旧库按备份还原，还原后主库可只读打开、schema 状态与导入前一致
2. **Given** 覆盖中途进程被强杀/断电 **When** 下一次启动 GUI 或再次进入导入入口 **Then** MUST 检测到残留 `import_journal.json`，并给出「继续完成 / 回滚到导入前」二选一，**MUST NOT** 直接按「文件是否存在」推断模式重新开始一次新导入
3. **Given** 磁盘可用空间不满足 FR-015 分卷门槛 **When** 点「导入校验」 **Then** 在备份开始前就拒绝并报出所需/可用读数
4. **Given** daemon 正在运行或写锁被其他进程持有 **When** 点「导入校验」 **Then** 提示「请先停止采集」并终止，不尝试覆盖
5. **Given** 导入完整成功 **When** 对同一包再次执行导入 **Then** 第二次的复验四态与第一次完全一致（幂等）

### User Story 6 - 双端对等（Priority: P2）

同一份数据、同一份配置，CLI 与 GUI 两个入口产出的交付包（除 manifest 内时间戳外）与校验报告一致。

**Why this priority**: 项目铁律要求新功能模块双端对等；也保证自动化与人工操作不会产出不同结果。

**Independent Test**: 在 daemon 停止且无写入的窗口内，对同一份数据分别用 CLI 与 GUI 连跑两次导出，比对文件清单与 SHA256。

**Acceptance Scenarios**:
1. **Given** daemon 已停止、无写入、同一份库、不同导出目录 **When** 分别经 CLI 与 GUI 导出 **Then** 文件清单与每个文件的 SHA256 逐位一致（manifest 时间戳字段除外）
2. **Given** 同一交付包 **When** 分别经 CLI 与 GUI 校验 **Then** 校验结论与逐表四态一致

## Requirements

### 导出（Export）

- **FR-001**: 导出 MUST 采用**硬编码白名单**，不得做成可由用户或配置扩展的清单。白名单固定为：`quantstudio.db`、`qfq_aux.db`、`qfq_aux_mcp_gen1.db`。**`-wal` 与 `-shm` 一律不列入白名单**（理由见 FR-004）。
- **FR-002**: 导出 MUST **排除**：`batch_audit.db`、`quarantine.db`、`task_resume/`、`daemon_status.json`、`snapshots/`、`logs/`、`mcp_landing/`、`staging*/`、`backup*/`、`wp2_release/`、`ptrade_fidelity/`、`*_c4resume_staging.db`、任何 `*.env`、任何 `-shm`。
- **FR-003**: 导出 MUST 为**前置校验 + fail-closed**：主库 `detect_schema_status` MUST ∈ {`COMPLETE_2_1`, `COMPLETE_2_2`}；白名单文件 MUST 可只读打开；MUST 无其他写锁持有者；MUST 无运行中的 daemon。任一不满足 ⇒ **不产生任何产出文件**。**Windows 环境探测（两项 MUST）**：
  - ①**句柄占用探测**：对每份白名单源文件与导出目标目录 MUST 以 `try-open`（尝试以独占方式打开/创建并删除临时文件）判定可用性。失败 ⇒ 提示「文件被其他程序占用（杀软实时扫描 / Explorer 预览窗格 / 同步客户端）」，终止。
  - ②**同步盘探测**：`export_dir` 若判定位于 OneDrive/Dropbox 等同步盘（按路径特征或目录属性）⇒ MUST 醒目警示「目标目录在同步盘上，数十 GB 复制会触发同步风暴且可能被钩住句柄」并**要求用户显式确认**后才继续（不阻断）。
  > **注**：`EMPTY_OR_NEW` 不放行导出——空库对客户无意义，且空 data/ 起步由「安装模式」覆盖。**本特性不存在「空包交付」路径**：新客户装机由实施方在已采集的生产机上出包。
- **FR-004**: 导出 MUST 在**同一写锁窗口**内完成「取锁 → checkpoint（DuckDB `CHECKPOINT` / SQLite `PRAGMA wal_checkpoint(TRUNCATE)`）→ **校验 WAL 已归零** → 复制 → 释放锁」。**WAL 归零校验为硬门**：checkpoint 后若任一 `-wal` 非零字节或无法确认已归零 ⇒ **fail-closed，不产出任何文件**，报「同刻一致未达成」。理由：checkpoint 成功时 WAL 必为 0 字节或被删除；非零即说明同刻自洽没有达成，把它装进包会把导出方的半截事务重放到客户机上（`knowledge/iron-rules/23-WAL残留处置.md` 即此类教训）。
- **FR-005**: 复制 MUST 为**文件级字节精确复制**；MUST NOT 使用 CTAS / EXPORT DATABASE 等逻辑导出（已知会丢 PK/NOT NULL/CHECK 约束，导致写入者 schema 安全闸判 `partial_or_mixed` 并拒启）。
- **FR-006**: 导出 MUST 产出 `manifest.json`，字段至少包含：**`manifest_schema_version`（本 manifest 格式版本，独立于主库 schema 状态——二者 MUST NOT 混用同一字段名）**、导出时刻、导出方程序版本、白名单文件清单（相对路径、字节数、SHA256）、主库 schema 状态实测值、`released` 当前值、`qfq_active_cutover` 世代与 `price_source`、每张受统计表的 `row_count` / `max_date` / `source_watermark.last_date` / **`date_basis_col`（本次所用列的列名，自证口径）**。**路径编码约定**：manifest 内所有路径字段 MUST 以 UTF-8 编码写、MUST 使用相对路径，**禁止写入绝对路径**。
  **受统计表集（v5 修正，2026-10-31 真实库实测后）**：`应推进水位的表 ∪ questdb_table_map 覆盖的表 ∩ 库内实际存在`。其中「应否推进水位」的判据 MUST 取 daemon 语义（`dataset_kind != passthrough`），**MUST NOT 以"是否出现在 questdb_table_map 内"作水位体系判据**（实测：map 64 表与 `source_watermark` 24 行**零交集**——map 覆盖的是云端 QuestDB 表清单，多为 passthrough；有水位体系的 canonical 表不在其中）。
  **`max_date` 取列口径**：优先从 `config/profiles/mcp_only/questdb_table_map.json` 的 `watermark_basis` 解析；map 未覆盖的水位体系表，MUST 从任务配置 / aligner schema 的 `time_key` 解析（与运行时 `_get_safe_watermark` 同源）；两处均无 ⇒ 标 `date_basis_col:"unknown"` 并**不输出** `max_date`（不猜）。**MUST NOT 统一用 `trade_date`**（分钟表与日表的基准列不同）。
- **FR-007**: 导出 MUST 做**红线自检**并写入 manifest：白名单外文件数 = 0；`*.env` 命中数 = 0；每个产出的 db MUST 可只读打开并 `SELECT 1`。任一不满足 ⇒ 标记产出不合格并告警。
- **FR-008**: 导出 MUST NOT 向主库写入任何内容（含 checkpoint 之外的 DDL/DML）。
- **FR-034**: 导出 MUST **强制新建时间戳子目录**产出（`<export_dir>/<YYYYMMDD_HHMMSS>/`）；MUST NOT 覆盖或删除目标目录内已有的任何旧交付包。
- **FR-035**: `export_dir`、`import_dir` 与 `DATA_ROOT` **三者 MUST 互不包含**（任意两者不得存在祖先/后代关系）。导出前与导入前 MUST 各校验一次；违反 ⇒ 拒绝并给出三路径对照。理由：包目录若落在 `data/` 内，rename 备份与临时复制会与包自身冲突甚至递归。

### 导入校验（Verify & Import）

- **FR-009**: 校验 MUST 在**覆盖之前**完成，且校验阶段 MUST NOT 修改 `DATA_ROOT` 下任何文件。
- **FR-010**: 校验 MUST 覆盖四要素：
  ①`manifest.json` 存在性与每个文件的 SHA256；
  ②主库 schema **实测**（用当前程序自身的判定）与**程序期望**比对，并与 manifest 记录比对（不一致即告警「包可能被改动或导出时状态有误」）；
  ③**世代与路径一致性**：以「客户 `qfq_aux_paths.json` + 包内主库 `qfq_active_cutover`」调用运行时路由解析，得到客户实际会使用的因子库路径 P，断言 **P == 本次导入的放置目标路径**（`DATA_ROOT/qfq_aux*.db`）；不等 ⇒ 归入「配置不匹配」终止并给出两路径对照。**配置缺失/损坏分支 MUST 显式输出**：路由 fail-secure 回 legacy ⇒ 结论 MUST 写明「配置缺失，已按 legacy 判定；包内 `qfq_aux.db` 必须校验通过；如需 gen1 请修复 config」；
  ④白名单完整性（缺任一必需文件即失败）。
- **FR-011**: 校验失败 MUST 按**四类原因分派**不同提示与引导：①包问题（重新获取包）/ ②程序版本不匹配（更新程序）/ ③配置不匹配（检查配置）/ ④**包格式代差**。MUST NOT 用统一话术「更新项目」覆盖四类。
  **第四类定义**：校验端读到 `manifest.manifest_schema_version` **高于**当前程序支持的版本 ⇒ MUST 提示「包由更新版本的程序生成，请先更新程序」并终止（引导归入②）；MUST NOT 抛异常崩溃、MUST NOT 尝试按旧格式解析或忽略未知字段继续执行。
- **FR-012**: `DATA_ROOT` MUST 由系统既有路径解析（`_paths`）确定并在界面**只读显示**；MUST NOT 允许用户通过弹窗另行选择，避免「备份了 A、覆盖了 B」。
- **FR-013**: 导入流程 MUST 按下述顺序执行：①读配置与 `DATA_ROOT`（并校验 FR-035 的三路径互不包含）→ ②前置检查（daemon 未运行、无写锁、`DATA_ROOT` 可写、磁盘空间满足 FR-015 门槛、**FR-003 同款的两项 Windows 环境探测：句柄占用 try-open + 同步盘警示（任一失败按 FR-003 同语义终止）**）→ ③**只读校验**（FR-010）→ ④失败即终止且零改动 → ⑤**取写锁并写 journal** → ⑥备份 → ⑦覆盖 → ⑧复验 → ⑨释放锁并放行提示。
- **FR-014**: 系统 MUST 支持**三模式**，判据按顺序：
  - **残留 journal 存在** ⇒ 进入**恢复流程**（FR-036），MUST NOT 按文件存在性重新推断模式；
  - `DATA_ROOT/quantstudio.db` **存在且可只读打开** ⇒ **更新模式**（备份 → 覆盖）；
  - 该文件**存在但无法只读打开**（0 字节 / 打不开 / schema `UNKNOWN`）⇒ **修复模式**（取证备份 → 覆盖，见 FR-015 降级分支）；
  - 该文件**不存在** ⇒ **安装模式**（跳过备份、直接放置，提示「无文件可备」）。
  四分支的校验与复验逻辑一致。
- **FR-015**: 备份 MUST 落到 `DATA_ROOT/backup_import_<YYYYMMDD_HHMMSS>/`，MUST 显示备份实际体积。**备份方式 MUST 按同卷/跨卷分派**：
  - **同卷（默认）**：MUST 用 `rename`（移动）把将被覆盖的旧文件移入备份目录；`rename` 为 O(1) 且零额外空间。此模式下磁盘空间门槛 MUST 为「包体积 × 1.1」。
  - **跨卷**：MUST 退回复制，空间门槛 MUST 为「备份体积 + 包体积 × 1.1」，并 MUST 在界面明示额外占用。
  - **备份回验 MUST 区分两种失败**：①源文件**在导入前就可只读打开** ⇒ 回验失败判**真失败**，终止并回滚；②源文件**在导入前已无法打开**（修复模式）⇒ 回验降级为**取证保留**：原件 MUST 以 `rename` 移入备份目录（同卷零额外占用、零复制耗时；「损坏」判定只是探测失败，不等于数据无价值，MUST NOT 删除原件），界面明示「旧库在导入前已损坏（原件已保留在备份目录，零额外占用；确认新库正常后可手动删除释放空间）」，取证摘要记入 journal（FR-036），**继续覆盖**。MUST NOT 因坏库而卡死修复路径。
  - `rename` 模式下备份期间旧文件不在原位，MUST 由 FR-036 的 journal 保证可恢复。
- **FR-016**: 覆盖 MUST 采用**可分步恢复的替换语义**（**不承诺跨文件原子性**——该语义在 Windows/NTFS 上不存在）：
  - 新文件 MUST 先复制到目标目录内的临时名（`*.incoming`）；
  - 全部临时文件就绪后，**逐个** `os.replace` 到正式名，每完成一个 MUST 更新 journal；
  - 任一失败 ⇒ 按 journal 反向回滚已替换文件到备份状态；MUST NOT 留下「主库新、因子库旧」的混合态；
  - 完成后 MUST 复验（FR-018）。
- **FR-017**: 覆盖 MUST 为「**只增改不删**」：MUST NOT 删除或清空 `DATA_ROOT` 下白名单外的任何文件（含 `snapshots/`、`.gitkeep`、`README.md`）。
- **FR-018**: 复验 MUST 再跑一次 FR-010 的校验，并输出逐表四态（`MISSING` 有数据无水位 / `BEHIND` 落后 N 天 / `AHEAD` 领先 N 天 / `OK` 一致 / **`UNKNOWN-SOURCE`：包内主库有该表水位，但客户配置无对应采集任务或无凭据**）与一句话结论；MUST NOT 自动触发采集。四态的日期差计算 MUST 与 FR-006 使用同一 `date_basis_col`。**另设三个排除性标签（不参与四态判定，仅为可显示的事实）**：`EMPTY`（无数据且无水位行——不算 MISSING，避免噪声）；`UNKNOWN-BASIS`（该表 `date_basis_col` 未知——不输出 `max_date`，不猜）；`NO-WATERMARK-SYSTEM`（该表属 passthrough / 无水位体系——只报 `row_count`，不输出水位与四态；判据取 daemon `dataset_kind` 语义，见 FR-006 v5 修正）。
- **FR-019**: 校验与复制 MUST 提供**字节级**进度反馈（已处理字节 / 总量 / 速率 / 预计剩余）；MUST 支持取消；取消或中途失败 MUST 回滚。校验 MUST **逐文件 fail-fast**，文件处理顺序 MUST 为**按体积升序（小文件优先）**——使坏包在数十秒内报错而非先跑完 58 GB 主库的全量哈希。
- **FR-036**: 导入 MUST 具备**崩溃恢复**能力：
  - 进入 FR-013 第⑤步前 MUST 写 `data/import_journal.json`，至少记录：模式、备份目录、包标识（manifest 哈希）、待替换文件清单及其临时名/正式名、逐项状态（pending/done）、开始时刻；**修复模式下 MUST 另记取证摘要**（源文件字节数、SHA256 或「计算失败」标记、打开错误全文、schema 探测结果）；
  - 每次替换/回滚后 MUST 更新该文件（原子写）；
  - GUI 启动与导入入口 MUST 检测残留 journal，提供「**继续完成** / **回滚到导入前**」二选一，并展示已完成项数；
  - 恢复完成后 MUST 删除 journal；成功路径结束时同样 MUST 删除。
- **FR-037**: 导入的**变更区间（备份起至复验止）MUST 持有 `data/snapshots/.write_lock`**，与 daemon 互斥由既有锁协议保证；MUST NOT 只做「前置检查」而不持锁。理由：数十 GB 的备份/复制耗时以十分钟计，期间 daemon 到点自启会写一个正在被替换的库。
- **FR-038**: `import_dir` 内 **MUST 恰好包含一个交付包**（以存在且仅存在一个 `manifest.json` 的子目录为准）；零个或多个 ⇒ 提示「交付包位置不正确，请重新选择」并终止。
- **FR-039**: 备份目录内 MUST 放置 `RESTORE_README.txt`，说明「本目录是导入前旧库的原件（由同卷移动而来，零额外占用）；确认新库运行正常前请勿删除；恢复方法为将本目录文件移回 `data/`；确认无误后可删除本目录释放空间」。

### 配置与界面

- **FR-020**: 新增独立配置 `config/delivery_config.json`，含两项：`export_dir`、`import_dir`。MUST NOT 把数据目录路径写入该配置（`DATA_ROOT` 已有唯一真相源）。**MUST NOT 提供 `backup_mode` 之类的备份方式开关**——复制式备份与移动式备份终态完全相同，不构成能力增量；复制机制仅作为跨卷内部路径保留（见 FR-015）。
- **FR-021**: 配置写入 MUST 为原子写（临时文件 + rename）；配置缺失或字段为空时 MUST 视为「未选择」，不得报错启动失败。
- **FR-022**: 两个入口按钮（「交付包导出」「导入校验」）在对应配置为空时 MUST 置为禁用并给出「请先选择…」提示，MUST NOT 让用户点击后才报错。
- **FR-023**: 新增独立 GUI 页签「数据交付」，MUST NOT 与既有「K 线分库导出」页签混放（后者产物供 QMT/PTrade 挂载、不覆盖生产文件，风险等级不同）。
- **FR-024**: 「选择交付包目录」按钮 MUST 弹出目录选择框，选择后在按钮旁显示完整路径；启动 GUI 时 MUST 自动读取并回显配置路径（未选择则为空）。
- **FR-025**: 「交付包导出」「导入校验」按钮 MUST 在旁侧标注影响范围（导出：将导出主库 + 两代因子库、期间暂停采集；导入：将备份并覆盖 `DATA_ROOT` 下指定文件）。体积 MUST 由程序**实时读取文件实际大小**后显示；**MUST NOT 在界面文案、文档、或本规格条款中写死体积数字**。
- **FR-040**: 导入结果页 MUST 展示：模式、备份路径与体积、逐文件替换结果、复验四态、以及体积实测值；MUST 提供「打开备份目录」入口。**修复模式下 MUST 额外展示**：①「旧库原件已保留在 `backup_import_xxx/`（同卷移动，零额外占用）；确认新库正常后可手动删除该目录释放空间——释放权在客户」；②取证摘要（字节数、SHA256、打开错误全文、schema 探测结果）。

### 双端对等与纪律

- **FR-026**: 导出与校验 MUST 各有一个**内核函数**，CLI 与 GUI 均为薄壳调用同一内核；MUST NOT 存在两套独立实现。
- **FR-027**: CLI MUST 提供等价入口：导出（指定输出目录）、校验/导入（指定交付包目录，含 `--dry-run` 仅校验不导入）、恢复（处理残留 journal）。
- **FR-028**: 同一输入下 CLI 与 GUI 的导出产物（文件清单 + 逐文件 SHA256）与校验报告 MUST 逐位一致（manifest 时间戳字段除外）。
- **FR-029**: 本特性 MUST 为**纯增益**：不改 upsert/合并语义、不改写入通道契约、不改 `source_watermark` 表结构、不改 QFQ 世代机制、不改回测与策略转换的取数契约。
- **FR-030**: 导出与导入 MUST 全程不触碰 `config/` 下任何既有配置（`collector_tasks.json` / `questdb_table_map.json` / `qfq_aux_paths.json` / `data_config.json`），也不触碰客户台账与日志。
- **FR-031**: 备份目录（`backup_import_*`）与 `import_journal.json` MUST 纳入 `.gitignore`。
- **FR-032**: 实施时 MUST 同步更新 `data/README.md` 的分发说明——现行文案「将分发的 `quantstudio.db` 压缩包解压到本目录」在交付包机制落地后成为过时指引（会诱导客户手动解压并踩「层级错位」坑），MUST 改为指向 GUI「数据交付」页签；其中体积句 MUST 删除或改为「体积以交付页实测显示为准」（**不得写入具体数字**）。
- **FR-033**: 导入完成后 MUST **按条件触发**基线提示：复验时若实测包内水位晚于 `config/quality_baseline.json` 的 `generated_at` 对应水位 ⇒ 升级为醒目警示「巡检基线已过时，继续沿用会产生误报；请联系实施方获取新基线，或在质量页选择忽略」；否则仅提示「基线可能过时」。MUST NOT 自动改写该文件，MUST NOT 提供本地重建按钮（基线的生成依赖实施方全量对拍口径）。
- **FR-041**: 「写入路径零改动」MUST 可机检：受保护路径（`writers.py` / upsert 相关路径 / QFQ 引擎文件）的 diff **MUST 相对本会话开工基线快照逐字一致**（共享工作区存在他线在途改动，绝对零 diff 不可作为判据）。开工时 MUST 落基线快照（受保护路径清单 + 逐文件 diff 哈希）至 `docs/evidence/`；每波收口与最终收口 MUST 重算并与基线比对、归档读数；**若比对不一致 MUST 申报并区分来源**（本会话违规 vs 他线推进，以后者为例外但须留证）。

### 范围覆盖对照（需求 → 用户故事）

| 需求 | 覆盖用户故事 |
|---|---|
| FR-001 ~ FR-008、FR-034、FR-035 | US1（导出） |
| FR-009 ~ FR-019、FR-037 ~ FR-040 | US2（存量导入）、US4（坏库修复）、US5（回滚与崩溃恢复） |
| FR-012、FR-014 | US3（全新安装）、US4（坏库修复） |
| FR-020 ~ FR-025 | US1 / US2 的界面与配置 |
| FR-026 ~ FR-028 | US6（双端对等） |
| FR-029 ~ FR-031、FR-041 | 全体（纯增益与越界约束） |
| FR-036 | US5（崩溃恢复） |

### 非范围（Out of Scope）

- 不导出整个 `data/` 目录；不导出任何凭据、日志、MCP 落盘、staging、backup、`-shm`。
- 不覆盖 `batch_audit.db` / `quarantine.db`（客户本地台账；两者会由程序在首次使用时自动创建）。
- 不实现压缩、上传、分发渠道（由人工打包转发）。
- 不改 QFQ 世代机制、不改主库 schema、不改 `qfq_aux_paths.json` 语义。
- 不修正包内水位与数据的偏差（属 `.specify/assessments/watermark-preset-from-package/` 另案；本规格只报告四态，不做修正）。
- 不自动触发采集、不自动修复客户存量错配。
- 不承担「客户程序版本升级」职责（版本不匹配时只提示，不代升级）。
- **不承诺跨文件原子替换**（Windows/NTFS 无此语义）；承诺的是「可分步恢复 + journal + 崩溃可恢复 + 完成后复验」。

## Success Criteria

- **SC-001**: 导出前置校验的**拦截有效性**：构造 schema 不合格 / 存在写锁 / daemon 运行 / 空间不足 / **WAL 未归零** 五种场景，各执行一次导出，**五种场景下产出文件数均为 0** 且界面给出对应原因。
- **SC-002**: 导出**零泄露**：产出目录中 `*.env`、日志、缓存类文件命中数 = 0；白名单外文件数 = 0；`-shm` 命中数 = 0。
- **SC-003**: 导出**同刻一致性**：同一锁窗口内产出的三个 db MUST 均可只读打开，且主库 `qfq_active_cutover` / `source_watermark` 状态与两个因子库内容可共同读通（以「三文件一次连续只读打开 + 关键状态查询成功」为证）。**不得以文件 mtime 作为同刻证据**（`copy2` 保留的是源文件 mtime，与导出时刻不同源）。
- **SC-004**: 存量客户导入**成功路径**一次跑通：校验 → 持锁 → 备份 → 覆盖 → 复验全通过，复验输出四态表且结论为「可增量」。
- **SC-005**: 存量客户导入**失败路径零改动**：分别用「包损坏」「schema 不匹配」「配置不匹配」三种包各跑一次，`DATA_ROOT` 下文件（大小 + mtime + SHA256）与导入前**逐位一致**。
- **SC-006**: **回滚有效性**：覆盖中途程序内注入失败，回滚后主库可只读打开、schema 状态与导入前一致、抽样表行数一致。
- **SC-007**: 全新客户安装模式一次跑通：`data/` 下 `snapshots/`、`.gitkeep`、`README.md` 保留；首次启动采集后 `quarantine.db`/`batch_audit.db` 自动创建；随后「全部执行（增量）」可用。
- **SC-008**: **功能面零变化**（**带豁免条件**）：在**不跨复权世代**的导入（包内 `qfq_active_cutover` 与导入前一致）下，同一份回测参数在导入前/后各跑一次，结果逐位一致。
  **豁免**：若包携带新的复权世代，历史复权价被合法重算 ⇒ 结果**理应改变**，本条判据不适用；此时改用传导论证——「文件级字节一致 ⇒ 表结构与约束一致 ⇒ 取数契约不变 ⇒ 行为等价」，以 SC-013（diff 审计为零）为证。
- **SC-009**: **双端一致**（**带前提**）：在 **daemon 停止且无写入的窗口内**，对同一份库用 CLI 与 GUI 各导出一次，文件清单与逐文件 SHA256 一致率 100%（manifest 时间戳除外）；校验报告结论一致率 100%。**前提未满足时（期间有写入）本条不适用**。
- **SC-010**: **配置健壮性**：删除 `delivery_config.json`、或字段为空、或路径指向不存在目录，三种情形下 GUI 均能正常启动，且按钮呈禁用态并提示「请先选择目录」。
- **SC-011**: **备份方式与空间**（证据链按序取第一个可用项，避免 Windows 无可靠 inode）：
  ① `os.stat().st_ino` 非零且备份与源一致；② 否则「rename 前后 size + mtime_ns 一致，且源路径已不存在」；③ 外加「操作耗时 < 5 秒」与「磁盘可用空间无显著下降」。跨卷情形 MUST 明示额外占用体积；空间不足时 MUST 在备份开始前拒绝。
- **SC-012**: **体积显示为实测值**：界面显示的体积 MUST 与文件系统实际字节数一致（一次读数比对）；代码与文档中 MUST NOT 存在写死的体积常量。
- **SC-013**: **纯增益可机检（基线相对口径）**：受保护路径的收口 diff 与开工基线快照**逐字一致**（比对读数归档 `docs/evidence/`；不一致时已申报并区分本会话/他线来源）；既有测试套件全绿（零新增失败）。
- **SC-014**: **崩溃恢复有效性**：在覆盖阶段强杀进程，重启后 MUST 检测到 journal 并给出二选一；选择「回滚」后 `DATA_ROOT` 恢复为导入前状态（逐位一致）；选择「继续」后能完成剩余文件并复验通过。**且 MUST NOT 出现「把半新半旧状态当原始态再备份一次」**。
- **SC-015**: **坏库修复路径可用**：以 0 字节 `quantstudio.db` 为起点执行导入，流程 MUST 不因备份回验失败而终止，最终库可用且结果页保留「原库已损坏」记录。
- **SC-016**: **导入幂等**：同一包连续导入两次，第二次复验四态与第一次完全一致。
- **SC-017**: **导入期间磁盘耗尽**：在复制阶段人为制造 ENOSPC，MUST 按 journal 回滚到备份状态，且 journal 被正确清理或保留可恢复。

## 关键约束依据（供审核核对，非实现要求）

| 约束 | 依据 |
|---|---|
| 主库 schema 只放行 EMPTY_OR_NEW / COMPLETE_2_1 / COMPLETE_2_2，其余写前 fail-fast | `quantstudio/pipeline/qfq_schema_status.py`；`quantstudio/pipeline/writers.py`（`_init_tables` 调用 schema 安全闸） |
| 不可用 CTAS / EXPORT：会丢约束致写入者拒启 | `scripts/questdb_package_part1.py` 的 file-copy 模式注释（2026-09-12 实测） |
| **checkpoint 后 `-wal` 必须归零；非零 ⇒ 同刻一致未达成** | SQLite WAL 恢复语义（非空 wal 在下次打开时必被重放）；`knowledge/iron-rules/23-WAL残留处置.md`。**注**：先例脚本带 WAL 是「无锁窗口在线热复制」的折衷，本方案有锁窗口 + checkpoint，条件不同，不得照抄 |
| 两世代因子库并存与路由 | `config/profiles/mcp_only/qfq_aux_paths.json`（相对 config 的路径）；`quantstudio/pipeline/qfq_aux_router.py:178-231`（released + active cutover 双条件；config 缺失 ⇒ `legacy:fail-secure:no_aux_paths_config`） |
| 每表水位基准列的真源在配置、不在库 | 配置真源：`config/profiles/mcp_only/questdb_table_map.json` 的 `watermark_basis` 字段（逐表，由 `scripts/gen_questdb_table_map.py` 生成）；消费口径：`quantstudio/pipeline/sources/questdb_adapter.py:188,200,310` 从 `spec["watermark_basis"]` 读取（与真源同源）。`source_watermark` 表列集仅 `source/table_name/freq/last_date/...`，**无 basis 列** |
| `quarantine.db` / `batch_audit.db` 缺失会自建 | `quantstudio/pipeline/quarantine.py`（`__init__` mkdir + DDL）；`quantstudio/pipeline/daemon.py`（`BatchAudit.__init__` mkdir + CREATE TABLE IF NOT EXISTS） |
| `DATA_ROOT` 唯一真相源 | `quantstudio/_paths.py`（env → data_config.json → 项目根/data） |
| 写锁协议（含死亡自愈、拒绝静默） | `quantstudio/pipeline/snapshot_lock.py:11-70`（O_CREAT\|O_EXCL） |
| 水位为 epoch 毫秒、键为 (source, table, freq) | `quantstudio/pipeline/writers.py`；`quantstudio/pipeline/daemon.py`（`_bump_date`） |
| 质量审计对水位偏差分级 | `quantstudio/pipeline/quality_audit.py`（watermark > max 为 error，< max 为 warning） |
| 主库体积量级 | `quantstudio_data_package_20260917.manifest.json`：`package_size_gb: 50.37`；`data/README.md:23` 的「约 12 GB」为过时文案，不得作为依据 |

## 附：审核意见处置记录（v2）

| 审核项 | 结论 | 处置 |
|---|---|---|
| Q-01 WAL 入包 | **采纳（阻断）** | FR-001 删 `-shm`；FR-004 改为 checkpoint 后 WAL 必须归零否则 fail-closed；US1-场景3、SC-001/SC-002、关键约束表同步改写 |
| Q-02 变更期不持锁 + 无 journal | **采纳（阻断）** | 新增 FR-036（journal + 崩溃恢复）、FR-037（变更区间持锁）；FR-013 流程改为九步；FR-016 删除「原子/事务性」；新增 US5、SC-014 |
| Q-03 坏库回验死锁 | **采纳（阻断）** | FR-014 扩为三模式 + 优先看 journal；FR-015 增加回验降级分支（取证备份）；新增 US4、SC-015 |
| Q-04 配置路径与导入目标不一致 | **采纳（重要）** | FR-010③ 增「运行时路径 == 导入目标路径」断言 + legacy 分支显式输出 |
| Q-05 basis 解析来源未指明 | **采纳并补正** | FR-006 明写真源为 `questdb_table_map.json` 的 `watermark_basis`（审核原文写作「sources 配置」，本方案落到更精确的文件）；manifest 增 `date_basis_col` 自证；无字段不猜 |
| Q-06 FR-025 自相矛盾 | **采纳（重要）** | FR-025、FR-032 删除全部具体体积数字；数字仅保留在背景章并标注「不得抄进文案」 |
| Q-07 SC-011 inode 判据不可靠 | **采纳（建议）** | SC-011 改为三选一证据链 |
| Q-08 SC-003 mtime 是弱证据 | **采纳（建议）** | SC-003 改为「三文件连续只读打开 + 状态查询成功」，删除 mtime 证据 |
| C-1 路径重叠 | **采纳** | 新增 FR-035 |
| C-2 旧包被覆盖 | **采纳** | 新增 FR-034（强制时间戳子目录，旧包永不触碰） |
| C-3 导入期间磁盘耗尽 | **采纳** | 新增 SC-017 |
| C-4 客户无凭据的源 | **采纳** | FR-018 四态增 `UNKNOWN-SOURCE` |
| C-5 双端对等测试前提 | **采纳** | SC-009 补「daemon 停止且无写入」前提 |
| C-6 import_dir 多包 | **采纳** | 新增 FR-038 |
| D-1 空包路径 | **采纳** | FR-003 增注「本特性不存在空包交付路径」 |
| D-2 Windows 原子性 | **采纳** | FR-016 改写为 staged 改名 + journal |
| D-3 模式判据 | **采纳** | FR-014 三模式 |
| D-4 released 缺失 | **采纳** | FR-010③ |
| D-7 校验耗时 | **采纳** | FR-019 增字节级进度 + 逐文件 fail-fast；**不采用抽样校验** |
| D-8 rename 四问 | **采纳** | ①journal（FR-036）②NTFS 单文件 rename 原子成立 ③DATA_ROOT 内仍有意义 ④RESTORE_README（FR-039）⑤**`backup_mode` 配置项已按 v3 复审裁定删除**——复制式与移动式终态相同、不构成能力增量；复制机制仅作为跨卷内部路径保留（FR-015），不暴露为用户选项 |
| D-9 quality_baseline | **采纳** | FR-033 改条件触发；仍不提供本地重建按钮 |
| D-10 纯增益举证 | **采纳** | SC-008 补豁免 + 传导论证；新增 SC-013（diff 审计）、SC-016（幂等） |
| E-1 SC-008 必翻车 | **采纳** | 见 SC-008 豁免条款 |
| E-2 非空 WAL 发货 | **采纳** | 见 Q-01 |
| E-3 重入误判污染备份 | **采纳** | 见 Q-02 / FR-036 / SC-014 |
| **v3-R1** `backup_mode` 配置项 | **按复审裁定删除** | 删原 FR-039；FR-020 改两项；FR-040~FR-042 顺延为 FR-039~FR-041；范围覆盖表与 D-8⑤ 同步改注 |
| **v3-R2** 修复模式取证备份 | **按复审裁定保留原件** | FR-015 明写「探测失败 ≠ 数据无价值、MUST NOT 删除原件、同卷 rename 零额外占用」；FR-039（RESTORE_README）与 FR-040（结果页）补释放提示；FR-036 补取证摘要字段 |
| **v4-A** manifest 版本化 | **已补入** | FR-006 增 `manifest_schema_version`（与主库 schema 状态分立）+ 路径 UTF-8/相对路径约定 |
| **v4-B** 包格式代差（第四类失败） | **已补入** | FR-011 由三类扩为四类；读到更高版本 ⇒ 提示更新程序并终止，禁崩溃/禁按旧格式解析 |
| **v4-C** Windows 环境探测 | **已补入** | FR-003 增两项：句柄占用 try-open 探测、同步盘警示需显式确认；FR-013 第②步引用同款探测 |
