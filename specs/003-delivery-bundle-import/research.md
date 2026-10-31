# Phase 0 调研与决策记录

**Feature**: 003-delivery-bundle-import | **Date**: 2026-10-31 | **状态**: 无残留 `NEEDS CLARIFICATION`

> 说明：本特性的技术未知项全部为**项目内部事实**（既有实现、平台语义、配置真源），故调研方式是「读代码 + 读既有证据 + 本地实测」，不涉及外部文档检索。每项给 Decision / Rationale / Alternatives considered。

---

## R1 DuckDB 主库如何"checkpoint 后 WAL 归零"

**Decision**：Phase 2 以**读写连接**打开 `data/quantstudio.db`，执行 `CHECKPOINT` 语句，立即 `close()`；随后 Phase 3 断言 `quantstudio.db.wal` 不存在或 0 字节。

**Rationale**：
- spec FR-004 明文要求同一锁窗口内 checkpoint；DuckDB 的 `CHECKPOINT` 只能由读写连接发起（只读连接不支持），故必须短暂以 RW 打开。
- 窗口前提是 daemon 已停（Phase 0 已验证），DuckDB 单写者约束满足。
- DuckDB 每次提交会自行 checkpoint（`docs/handoff/duckdb-window-execution-card-20261030.md` C3 记录"主库 WAL 预期无文件"），因此绝大多数情况下 `CHECKPOINT` 是快速 no-op；断言仍必须做（不能因"预期无 WAL"而不校验）。
- 判据取 `size > 0` 才算未归零：Windows 上可能出现 0 字节 `-wal` 占位文件，把"存在"当失败会造成假阻断（W-10）。

**Alternatives considered**：
- ①只读打开 + 依赖关闭连接自动 checkpoint：不可控（只读连接不触发），且无法给出"已归零"的证据。
- ②不 checkpoint，直接复制 + 带走 WAL：**等于 Q-01 阻断项**，明确否决（把导出方半截事务重放到客户机）。
- ③SQLite 的 `VACUUM INTO` 式热备：DuckDB 无等价原语，且逻辑导出会丢约束（T1）。

---

## R2 SQLite 两代因子库的 checkpoint 与忙碌判定

**Decision**：以 RW 连接对每个 SQLite 件执行 `PRAGMA wal_checkpoint(TRUNCATE)`，读取返回三元组 `(busy, log, checkpointed)`；`busy != 0` ⇒ fail-closed。

**Rationale**：
- `TRUNCATE` 模式 checkpoint 完成后把 WAL 截断为 0 字节，与 FR-004 的"必须归零"直接对齐。
- `busy=1` 表示有并发读者/写者阻止完整 checkpoint ⇒ 同刻一致不可达，必须拒绝而不是降级（例如降级为 `PASSIVE` 会留下非零 WAL，正是 Q-01）。
- 三件套里的 gen1 库当前仅 53,248 B（有效 SQLite，头 `SQLite format 3`），checkpoint 成本与风险可忽略，但**必须同样校验**（它同样是世代路由的候选文件）。

**Alternatives considered**：
- ①`VACUUM`：整库重写，10.81 GB 库耗时与空间不可接受。
- ②`backup API`：会在目标生成新库，与"文件级字节复制"语义冲突（我们的交付件必须与源字节一致）。
- ③只对 `qfq_aux.db` checkpoint、跳过 gen1：会在 gen1 有活跃写入时留下非零 WAL（当前虽空，但语义不可依赖现状）。

---

## R3 复制与哈希是否两次读盘（69 GB 量级）

**Decision**：**单次读盘**——按 1 MiB 块复制并**同时**流式 `sha256`；复制后仅做「size 一致 + 只读打开 + `SELECT 1`」兜底，不默认复算全量哈希。提供 `--verify-hashes` 显式开关做二次校验（约多一次全量读盘）。

**Rationale**：
- 69 GB 两次读在机械盘/受杀软干扰的本机上可能多出十几分钟，直接放大写锁窗口（窗口越长，客户侧 daemon 误启的风险面越大）。
- 流式哈希描述的是"我实际写出的字节序列"，只要写入无截断且 size 一致，就等价于产物的哈希；这不是弱化判据，而是把判据放在正确的因果链上（写出的字节 vs 再读一遍的字节）。
- 残余风险（位翻转/静默损坏）由 `SELECT 1`/`detect_schema_status` 只读打开兜底；对交付包这种"一次性事件"，显式开关足以覆盖强制场景。

**Alternatives considered**：
- ①默认双次读（复制后再算哈希）：更"稳"，但窗口成本高；作为可选开关保留。
- ②`shutil.copy2` + 事后 `hash`：`copy2` 保留源 mtime，容易被误当"同刻证据"（SC-003 已明确禁止用 mtime 作证），且无字节级进度。
- ③用 OS 级 `CopyFileEx`（`shutil.copyfile` 底层路径）：无进度回调，取消语义差。

---

## R4 manifest 的逐表统计取自「源库」还是「产出副本」

**Decision**：取自**产出副本**（锁内、只读连接），统计对象是客户将要拿到的那个文件。

**Rationale**：
- manifest 的职责是"自证这个包里有什么"。若统计源库，则"复制时的字节精确性"与"manifest 数值"之间缺一环——恰好是 T1/T2 类事故最需要证明的环节。
- 顺带构成对复制的第一道校验：副本打不开/行数异常 ⇒ Phase 5 立即失败，而不是等客户投诉。
- 副本只读打开不需要写权限，不引入额外风险；成本与"读源库"相同（一次全表扫）。

**Alternatives considered**：
- ①统计源库：少一次打开副本，但失去"manifest 描述交付件"的自证性。
- ②统计源库 + 副本各一次：双倍扫表，窗口成本翻倍，收益不足以抵消。

---

## R5 「进水位体系」表集边界（承接审核方 Q2 裁定）

**Decision**：主集 = `questdb_table_map.json.tables`（64 张）∩ 主库 introspection 实际存在的表；逐表输出状态（含 `MISSING`）；空表（`row_count==0` 且无水位行）标 `EMPTY` 不算 MISSING；`source_watermark` 行只作状态数据来源；`collector_tasks.json`（实测 88 个任务）只做 `UNKNOWN-SOURCE` 交叉核对。

**Rationale**：
- 审核方的核心理由成立：若以水位行集为边界，`MISSING` 在定义上不可能出现，而复验报告最有价值的信号恰是「有数据、无水位 ⇒ 客户点增量会退化为全历史重拉」。
- 逐表 `count(*)`/`max(basis)` 发生在**实施机导出侧**，不是客户机；DuckDB 列存 + zonemap 使这类扫描为分钟级而非小时级；且它被包在已必须持有的锁窗口内（复制 69 GB 本来就要十几分钟）。
- 集合取交集而非全 64 张：map 里可能登记了库里尚未创建的表（配置先行），统计不存在的表会产生噪声失败。

**Alternatives considered**：
- ①全 64 张不裁剪：对不存在的表无法统计，只能整表标失败，噪声大。
- ②以 `source_watermark` 行集为边界：**被裁定否决**（消灭 MISSING）。
- ③以 `collector_tasks.json` 任务映射为边界：任务↔表是多对多且含隐藏/disabled 任务，边界不稳。

---

## R6 GUI 薄壳的可自动化测试途径（SC-009/SC-010）

**Decision**：用 `QT_QPA_PLATFORM=offscreen` 在 pytest 内构造 `QApplication` + `DeliveryTab`，并直接调用 GUI worker 的内核调用路径，与 CLI 路径比对产物。

**Rationale**：
- 仓库已有 Qt 测试基础设施（`tests/conftest.py:146-158` 的 QApplication 会话保活 fixture，明确"只在已有 QApplication 时登记引用，不主动创建"），offscreen 可避免弹窗与显示依赖。
- SC-009 的机检核心是"两个入口 → 同一内核 → 同产物"，因此测试只需驱动两端的**入口函数**，不需要真正渲染界面。

**Alternatives considered**：
- ①只在生产窗口内人工点两次：不可机检、不可重复，违反 constitution「每项验收标准可机检」。
- ②用 `pytest-qt`（新增第三方依赖）：破坏"零新增依赖"约束。

---

## R7 CLI 退出码约定

**Decision**：`0` 成功；`2` 用法/参数错误；`3` 前置检查拒绝（fail-closed）；`4` 包问题；`5` 配置不匹配；`6` 程序版本不匹配（含包格式代差）；`7` 存在残留 journal 需先恢复；`8` 内部错误；`9` 导入失败但已成功回滚；`10` 导入失败且回滚失败（需人工介入）。

**Rationale**：
- 与仓库既有约定同风格（`quantstudio/strategy_compiler/cli.py` 已用 `2`=用法/规格、`3`=拒绝、`5`=流水线错误），保持"轻量数字 + 稳定语义"。
- 区分离 `9/10` 是运维必需：客户回执里"失败"和"失败且环境已受损"是完全不同的处置难度。

**Alternatives considered**：
- ①统一非 0：客户与自动化都无法分支处置。
- ②只用 0/1：丢失四类失败分派（FR-011）在退出码面的表达。

---

## R8 崩溃注入如何做到"真强杀"又不误伤

**Decision**：`QS_DELIVERY_TEST_HOOKS=1` + `QS_DELIVERY_TEST_KILL_AFTER_REPLACES=N` 双开关；命中时在完成第 N 个 `os.replace` 后调用 `os._exit(9)`（不触发 finally、不释放锁——正是要复现的崩溃形态）。未设 env 时钩子代码路径完全不生效，并用单测断言"未设 env 时行为与无钩子版本一致"。

**Rationale**：
- 真强杀才能验证"进程死掉、锁残留、journal 半写"的真实组合；`monkeypatch` 抛异常只能验证"程序内失败"（SC-006），无法覆盖 SC-014 要求的两条路。
- 用子进程 + env 钩子，不涉及 `taskkill`/PID 猜测，从根上避开铁律 5 的误杀面。
- `os._exit` 不执行 atexit（`snapshot_lock` 的优雅释放也在 atexit），因此锁会残留——这恰好检验 `snapshot_lock` 的死亡自愈与 journal 的"锁字段可覆盖"设计。

**Alternatives considered**：
- ①`taskkill /PID` 打子进程：可行，但 Windows 上强杀时机不稳定（copy 阶段耗时波动），且引入进程管理面。
- ②`monkeypatch` 抛 `SystemExit`：会走 finally，无法复现"锁未释放"。
- ③只在人工演练中做：不可进门禁。

---

## R9 SC-008 的 A/B 回测数据从哪来（生产库禁止导入）

**Decision**：在 `tmp_path` 内用 **ATTACH + INSERT SELECT** 从生产主库**只读**抽取一个有界子集（例如 2 个标的 × 近 3 年日线 + 相关表），构造"结构同源、内容小"的 fixture 主库；在该 fixture 上做「导入前/导入后」两次短回测并逐位比对。**同时**在 evidence 中写明：该子集构造属**测试专用**，不适用于交付包（交付包仍必须文件级字节复制，禁 CTAS/EXPORT）。

**Rationale**：
- 生产库上禁止导入（审核方硬边界①），因此不可能在生产库上取"导入后"读数；而 SC-008 又必须给出"导入不改变回测行为"的正面证据，只靠传导论证会被判为"论证而非实测"。
- 子集库保留原始表结构（DDL 同源）与少量真实数据，足以跑通取数与撮合，从而检出"导入是否改变了取数契约"这类问题。
- 明确标注测试专用，避免与 T1「交付包禁逻辑导出」混淆（两者目的相反：一个造测试数据，一个造交付件）。

**Alternatives considered**：
- ①只做传导论证：成本最低，但 SC-008 变成纸面结论。
- ②复制整库到副本后导入：68.9 GB 空间 + 时间，且被 B 方案否决的同类成本。
- ③用空库跑回测：无数据 ⇒ 回测不产生信号，A/B 恒等，属"必然通过"的假判据。

---

## R10 安装模式（无备份）的回滚语义

**Decision**：安装模式下 journal 额外记录 `created_files`（导入前不存在的目标文件）；「回滚到导入前」= 删除这些本次创建的文件，**不触碰**其他任何文件（含白名单外的 `snapshots/`、`.gitkeep`、`README.md`）。

**Rationale**：
- FR-017 是"只增改不删"，但它约束的是**白名单外**文件；删除"自己刚创建的白名单件"是实现回滚语义的唯一途径（否则安装模式一旦失败就只能留下半套库）。
- 显式记录 `created_files` 而非"按目录差异推断"，避免把客户原有的同名文件误删（若客户此前已有同名文件，则它属于"更新模式"分支，不会进 `created_files`）。

**Alternatives considered**：
- ①安装模式失败后不提供回滚（只报告）：客户会留下"主库有、因子库无"的半套，正是要消灭的错配形态。
- ②安装模式也先备份（备份到哪？无文件可备）：语义空转，spec FR-014 已明确跳过备份。

---

## 残留 NEEDS CLARIFICATION

无。所有技术未知项已由上 10 项决策消解；需求侧的待裁定项（Q1/Q2/Q3）已由审核方裁定并落在本文件与 `plan.md`。
