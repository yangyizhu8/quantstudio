# 治理方案实施第 3 步终审记录（快照设计 v3+ + 3A 写锁收口）

- 状态：**部分闭合——3A 设计与三项裁定通过；registry 证据表行级错配待修正；文档收尾未完成**
- 日期：2026-08-17
- 审计方：DeepSeek-harness（DSH）
- 审核对象：`docs/governance-snapshot-design.md`（v3+）、`docs/governance-3a-write-lock-design.md`、`scripts/governance_write_conn_scan.py`、`data/snapshots/write_path_registry.json`

## 1. 已确认闭合

- 18 表三集合一致性（`table_set_consistency_report.json`）：CTE 排除成立，机器验证相等；
- 阻塞 2（循环依赖）：3A 重排（锁收口 → 快照 CLI → 修复 → 写后快照 → 复检 → 基线 → 第 5 步流程件）成立；
- 扫描器机制：76 连接点自动统计、MAIN 23/AUX 27/EXCLUDED 26/UNRESOLVED 0、差集空、stats 自动计算；
- locked=true 证据翻转机制（lock_adoption_log + 扫描器校验重置）设计成立；
- 3A §4 行为等价性边界符合铁律（锁为新增并发协调层，验收=接入前后库内容 diff=0）。

## 2. 新发现：registry 证据表行级错配（须修正）

扫描器以文件 default 证据分类，未验证连接目标。逐行核对 snippet 发现的错配：

| 连接点 | 误归类 | 实际目标 | 应归类 |
|---|---|---|---|
| gui/db_helper.py 126/133/153/161 | MAIN | quarantine_path / batch_audit_path（隔离库/审计库） | EXCLUDED |
| sources/mcp_adapter.py 1217/1515/1982 | MAIN | aux_p / mode=ro aux / aux | AUX（1515 为只读） |
| qfq_reanchor_schema.py 963 | MAIN | aux_path | AUX |
| qfq_resident_orchestrator.py 936 | MAIN | self.aux_db | AUX |

影响：方向保守（不破坏 fail-closed），但 3A 接入基数失真（MAIN 23 中约 7 个应重分类；db_helper 若按 MAIN 接锁会错误阻塞 GUI 隔离库操作）。修正后 MAIN≈16 / AUX≈31 / EXCLUDED≈29。

**修正要求**：证据表改为行级覆盖；sqlite 连接按目标路径变量分类；重新生成 registry，3A 接入基数以新产物为准。

## 3. 文档收尾未完成（实地核对）

- `governance-snapshot-design.md:49` 仍写"20 张表稳定排序键"——须改 18；
- `governance-step1-callchain.md:41` 实体表列表仍含 valuation_pit / latest_share——正文须删，仅排除说明保留；
- registry stats 自动计算 ✅。

## 4. 3A §7 三项裁定

1. daemon 持锁粒度：**任务批次级**（批次边界日志可观测；批次内唯一写会话）。
2. GUI 接入：**GUI 操作即持锁**（短事务；失败弹窗提示持有者，不可静默）。
3. 修复脚本族：**CLI 包裹**（`python -m quantstudio.pipeline.snapshot_lock run <cmd>`；透传退出码/stdout/stderr/env；文档化：fork 子进程不继承锁）。

## 5. scripts/ 覆盖边界（须写入 3A 文档）

扫描器覆盖 `quantstudio/**`；`scripts/` 修复脚本族不在 registry 准入范围，治理 = CLI 包裹（行为约束）+ B2 三重 hash（最终防线）。此边界须显式记录。

## 6. 禁止事项（持续）

snapshot create、数据修复/补拉/重锚/D2-F5 清理、黄金基线建立——全部保持禁止，直至上述修正完成并复审通过。

---

# 终审复审通过记录（2026-08-17，A 线 v3+ / 3A 设计）

## 复审结论：**通过**（3A 与快照设计 v3+ 可进入实施微流水线）

实地核验：
- 文档两处已修正（snapshot-design L49=18 张表；step1 L41 实体列表无 CTE；上轮读到旧快照，更正）；
- registry v4：76=14+32+30+0，差集空、unresolved 0；**MAIN 14 逐项 snippet 全部真实主库写（23−9=14 与错配清单精确自洽）**；AUX 32 逐项为 aux 目标，无主库连接误归 AUX（反向安全）；
- 扫描器三级优先逻辑（行级证据 > duckdb 文件默认 > sqlite 目标嗅探）已实现；
- 3A §3.2 scripts/ 覆盖边界声明 + §7 三项裁定（含全部补充要求）固化；
- 快照设计 7.5 重排节存在（3A→3B→修复→写后快照→复检→第 4 步）。

## 非阻塞观察项（3A 实施第 1 步闭环）

AUX 清单 2 类语义过粗的保守归类：
1. `quality_audit.py:584/742`（巡检读 aux 连接）——按写路径接锁会卡住 D2 巡检与写的并发；
2. `qfq_staging_canary.py:83`（staging aux 暂存连接）——非正式 qfq_aux。

要求：3A 实施第 1 步产出**连接语义清单**（46 连接点：写/只读/暂存），纯读与暂存豁免写锁并附代码证据入 lock_adoption_log。

## 流程状态

3A 实施微流水线批准启动 → 验收（锁单测/守卫 exit2/翻转测试/等价性 diff=0）→ lock_adoption_log 证据翻转 → 3B 快照 CLI 解除 create 拒绝 → 首份修复前快照 → 修复工单 → 写后快照 + D2 复检 → 第 4 步基线。

并行许可：Trae QDB qfq 修复通知后 D2 全量重跑（只读）可与 3A 并行。持续禁止：create / 数据修复 / 基线。

---

# 3A 阶段一审计记录（2026-08-17，锁模块 + 语义清单 + 单测）

## 审核结论：**通过**（阶段二批准启动）

实地核验：
- `connection_semantics.json`：46 项 = 33 WRITE + 9 READ + 4 STAGING 完全勾稽（MAIN 14/AUX 32）；豁免点抽查证据成立——cutover 455（PRAGMA quick_check）、revision 627（只读加载）、daemon 2657（纯 SELECT 因子读取）、cutover 592（SELECT COUNT 回放核验），均无 DML；
- `snapshot_lock.py`：O_CREAT|O_EXCL 原子创建（跨平台）、WriteLockHeld 携带持有者信息、陈锁（>600s）仅告警不自动清除、release 幂等、CLI 包裹器透传退出码/流且锁覆盖子进程全程（不继承锁限制已文档化）；
- 单测：`tests/test_snapshot_lock.py` 实跑 **6/6 passed**。

## 阶段二裁定：直接推进，等价性验收隔离化（硬约束）

- 锁接入与 Trae/QDB 零依赖，不必等修复通知；
- **禁止等价性验收直接写生产库**（无写前快照保护的生产写违反顺序锚点）；验收用临时库/临时 aux/dry-run，判据 = 接入前后库内容 diff=0 + 相关测试通过；
- 生产库首次真实写 = 修复工单包（3B 快照落地后）。

## 阶段二附加要求

1. daemon 重启窗口与写任务错开，重启前确认无长事务；
2. lock_adoption_log 逐项证据（模块+行号+commit+无锁拒绝测试）；READ/STAGING 豁免附 DML 扫描证据行（含 5 个无 manual_evidence 的 READ 点收尾：daemon2657/cutover592/maintenance177/event_discovery410/quality_audit584·742）；
3. B 类入口 SystemExit(2) 位于任何写操作之前，错误消息指导用户；
4. 翻转顺序：全部接入 → 扫描器校验 → registry 全 true → 3B 解除 create 拒绝（不留空窗）。

---

# 3A 阶段二中期审计记录（2026-08-17，MAIN 13/14 接入进度）

## 已确认良好

- 锁 API（进程级引用计数 ensure/release + locked_connect）：__exit__ = close + finally release，锁生命周期=连接生命周期无泄漏；
- writers.py 操作粒度拆分（write/advance_watermark → _locked），逻辑平移零改动；
- task_tab.py 回退属实（git 无改动），暂缓合理；
- 合规：create 拒绝保持（locked 全 false）。

## 关键发现 A（升级处理）：formal_cutover "经 CLI 覆盖"不成立

- 存在非 CLI 调用路径（qfq_formal_watermark_release 经 daemon --mode once、qfq_formal_canary import）；cutover 自带 _acquire_dual_locks 与 3A .write_lock 不同源。
- 处理：4 个连接点（364/533/557/592）直接 locked_connect 接入（与 dual_locks 共存）；或提供全部调用链持 3A 锁的逐链证据；在此之前保持 locked=false。

## 关键发现 B（观察）：并行工作包改动隔离

- quality_audit.py 242 行属 mcp-minute-front-anchor §4 A1/A2 巡检增强，非 3A 范围。
- 要求：等价性验收基线锁定 git 状态；quality_audit 584/742 READ 豁免在 A1/A2 后复核。

## 收尾清单（修订）

formal_cutover 直接接入 → AUX 19 点 → GUI 弹窗 → lock_adoption_log + 豁免证据 → 隔离化等价性验收（禁生产写）→ 扫描器翻转 → 3B。测试提交全量相关结果 + git diff 基准比对。

---

# 事故审计记录（2026-08-17，git checkout 覆盖并行改动事件）

## 恢复核验：成立（独立核实）

- 管线 v2 实质内容确认找回（stock_dividend→stock_dividend_full L207、ex_date 日期防御 L568-625、去重、_inject_dividend L1875）；
- 锁守卫共存确认（mcp_adapter L1900-1904 ensure + try/finally）；
- py_compile OK；dangling blob ee420a38 存在（来源可靠）；工作区状态与汇报一致。

## 根因定性：结构性风险（R5 代码层重现）

多会话共享 git 工作区 + 并行未提交改动 + 无操作前基线。数据层已有快照/锁/基线治理，代码工作区缺失同类防护。

## 裁定：代码层"写前快照"纳入

1. 硬要求：任何批量/破坏性 git 操作前 `git stash create -u -m "baseline-<ts>"` 记 hash（零副作用回退点）；禁止 stash/WIP commit（共享工作区冲突）；
2. 每轮会话开始记录 git status --porcelain + diff --stat 到会话档案；
3. 写入 AGENTS.md（与数据快照原则并列）。

## 继续收尾约束

逐文件定向 Edit；每批前 stash create 回退点；重放 qfq_revision 2 点 + AUX 剩余 10 点；确认 formal_cutover 直接接入（locked_connect 包裹）；等价性验收前先 stash create 固化基线再对比。

---

# 3A 阶段二终审记录（2026-08-17，写锁收口完成）

## 终审结论：**通过**

实地核验：
- registry 终态：MAIN 13 + AUX 20 = 33，locked 31/33，未锁恰 2 = aux_router:127 + observation:190（pending）；excluded 44；create 拒绝（诚实状态）；
- 锁单测实跑 7/7；等价性 test_3a_equivalence 实跑 3/3（writers/events/子进程拒绝，全程隔离临时库零生产写）；
- 豁免 12 点移入排除 + 证据在；AGENTS.md 四条写前快照铁律已写入；回退点 a9b2ce7b 留存。

## 决策点 1：pending 拆分 → 选项 A（fail-closed 优先）

aux_router:127 / observation:190 为返回存活连接的读写双态工厂，连接生命周期可能逃逸锁窗口；链证据无法覆盖逃逸。裁定：保持 false、create 拒绝；拆分为 3B 前置工单（改调用方传连接 / 上下文式 locked_connect / close 钩子，使锁生命周期可配对）。

## 决策点 2：formal_canary:214 = STAGING（接受）+ 保留 locked_connect 守卫

canary 为预案演练（wp7_held，默认 staging override），STAGING 归类符语义；守卫保留 = 运行时拦截仍生效（防指向正式库）。要求：registry/connection_semantics 标注"STAGING 但保留锁定守卫（防御性）"，防误删。

## 剩余排期

3A-pending 拆分 → 翻转全 true → 3B 快照 CLI + 首份修复前快照 → 修复工单包 → 写后快照 + D2 复检 → 第 4 步基线。

并行许可：Trae QDB qfq 修复通知后 D2 只读重跑可并行。持续禁止：snapshot create（pending 未清前）/ 生产数据写 / 基线。

---

# pending 拆分审计记录（2026-08-17，registry 34/34 全绿）

## 审核结论：有条件通过（2 项软约束需改硬约束）

已确认：registry MAIN 13 + AUX 21 = 34 全 locked、excluded 44、未锁列表空；aux_router connect/connect_locked/initialize_explicit 配对完备；observation _connect_locked 上下文；锁+等价测试实跑 10/10 无回归。

## 必须修复（fail-closed 语义一致）

1. `aux_router.connect(read_only=False)` 写态裸连接 → 改 raise（提示 connect_locked）；现有唯一调用 resident:929 为 read_only=True，零影响；
2. `observation._connect()` 公开裸工厂（含 init_sqlite_schema DDL 写）→ 私有化或运行时拒绝外部调用；只读场景另设只读连接。
3. registry/lock_adoption_log 语义声明：locked=true = 该文件全部写路径已受锁保护，裸写路径结构性消除（非仅约定）。

## 3B 前置

修复完成后 3A 写锁收口"写路径全覆盖无旁路"成立 → 3B 快照 CLI 实施 → create 解除拒绝 → 首份修复前快照 → 修复工单包 → 写后快照 + D2 复检 → 第 4 步基线。

---

# 3A 写锁收口终审（2026-08-17，全覆盖无旁路）→ 3B 批准

## 终审结论：通过

- 硬约束落实：aux_router.connect(read_only=False) 条件性 raise（resident:929 read_only=True 零影响）；observation.__connect 私有化 + _connect_locked 唯一外部入口 + own=False 用外部连接（锁责任归调用方）；
- registry 34/34 + _semantic 结构性消除声明；扫描器零波及（笔误事故未损）；锁+等价+aux_route 22/22 实跑；
- 两处插曲处理得当（陈锁 fixture 卫生钩子、笔误走写前快照纪律恢复）。

## 3B 快照 CLI 实施批准（验收要点）

按快照设计 v3+ 实施 governance_snapshot.py：核心验收=同快照重跑基线策略逐字节一致 + verify=manifest；18 表流式 hash + 三重源校验；VACUUM INTO + integrity；磁盘准入(3)/N=3+保护/unprotect 审计；原子 index；锁联动（写任务持锁时 create 拒绝）。Trae 通知后 D2 只读重跑可并行。禁止：生产数据写、基线。


---

# 3B 批 2 verify 门禁例外 + 分片 hash 立项（2026-08-18/19）

## 根因存疑记录

verify 三轮 OOM 被归因为"DuckDB 引擎级 ORDER BY 不可解"，但与既有事实矛盾：SNAP_001 verify PASS、SNAP_002 create 三次全量 hash 均成功（同样 87M 行）。更可能为"高内存需求 + 并发压力"而非纯引擎限制。分片 hash 对两种根因均稳健，但不可断言"参数不可解"放弃运行窗口排查。

机器安全红线：分片实现落地前禁止现行代码发起全量 verify（防第 4 次 OOM）；任何 verify 前确认系统内存余量。

## 批 2 解锁：附硬条件的 gate exception（需用户确认）

- 以 SNAP_002 create 三 hash 一致 + 表级交叉（19/20 表全同，仅 stock_daily 变）为入口证据；
- 附加文件 stat + 随机抽样 hash 轻量抽查；
- SNAP_002 人为钉住不可 prune/删除（批 2 回退与 diff 锚点）；
- 分片 verify 落地后回填独立 verify + bind --protect 正式关闭例外；
- 例外写入登记表与审计记录，后续批次不自动沿用。

## 分片 hash 微流水线立项（紧迫项，非技术债）

每次快照（批 2 写后、周期、D2 修复后）都需要 verify；拖成技术债将反复卡批与 OOM。规格需含：逐表分片键 + 等价性证明；per-table hash 与现有一致（或迁移方案）；内存上界论证；边界/故障测试；分片边界提取廉价。

---

# 分片 hash 规格审计 + SNAP_003 执行顺序裁定（2026-08-19，DSH 接手第 1 轮）

## 状态

- 接手：已完整阅读 session-cdc0996a-... 会话记录；当前处于 3B 批 2 收尾阶段。
- 当前资产：`docs/governance-sharded-hash-spec.md`（待审计，未修订）、`data/snapshots/SNAP_20260818_002_1f745d17`（pinned/protected，批 2 diff 锚点）、批 2 数据修复已完成并通过专项验证。

## 分片 hash 规格审计结论：**有条件通过，5 项修订后批准实施**

核心方向正确：等价性证明成立、内存有界、改动范围最小化（仅 `table_hash()`）。但实施前必须补全以下 5 项，否则内存上界与正确性无法保证。

### 必须修订项（不修订不得实施）

| # | 问题 | 修订要求 |
|---|---|---|
| **R1** | 分片边界算法不具体，按 code 前缀分桶会严重倾斜 | 不得以 `substr(code,1,2)` 作为唯一分片策略。A 股代码前缀分布极不均匀（60/00/30/68 集中），会导致某些分片行数远超 5M。必须给出**按实际行数自适应划分**的算法：先 `SELECT code, COUNT(*) FROM t GROUP BY code ORDER BY code`，再按累计行数每 ~5M 切一个 shard boundary。 |
| **R2** | strategy_events 等按 event_type 分片的风险 | event_type 可能只有 3 个值但行数差异大，直接按 event_type 分片可能单 shard 过大。修订：优先用组合键 `(event_type, event_time, id)` 分片；或按行号/ROW_NUMBER 分片，避免分片键倾斜。 |
| **R3** | "拼接 hash" 语义不精确 | 必须明确是**分片字节流按序拼接后一次 sha256**，还是**每片单独 sha256 后再拼接 hash 字符串二次 sha256**。前者与等价性证明一致、更简单、更易 debug；后者引入额外摘要层。规格须选定并给出伪代码。 |
| **R4** | 空分片 / NULL shard key / 缺片检测 | 必须规定：空 shard 在拼接流中插入空标记（如 shard 序号 + 0 行 sentinel）；shard key 含 NULL 时归入独立 shard；最终 hash 前校验实际 shard 数与预期一致，防止缺片被忽略。 |
| **R5** | create 路径改动的向后兼容实证 | 规格声称"SNAP_001/SNAP_002 既有 manifest hash 无需更新"，但必须**在真实数据上跑新旧 `table_hash()` 对照**：对大小表各取样本，断言分片 hash = 原全表 hash。该对照作为验收标准第 5 条加入 §7。 |

### 非阻塞建议

- 内存上界 3GB 估算偏乐观：每片 5M × 50 列 × 400B = 100GB 原始数据 DuckDB 排序不可能只用 2GB。建议实测峰值 RSS 阈值放宽到 ≤6GB，或按列宽/类型动态估算。
- 错误处理：单 shard 查询失败应 abort 整个 verify，不返回部分 hash。

## 执行顺序裁定：**先创建 SNAP_003，再并行实施分片 hash**

### 裁定

**先创建批 2 写后快照 SNAP_003（只复制并标记 `pending_verify`，不跑全量 verify），然后立即并行启动分片 hash 实现。**

### 依据

1. 批 2 写入后的状态是当前最宝贵的资产，必须尽快冻结。3A 写锁虽在，但锁是进程级软约束，存在失效/释放/误判窗口；快照是物理不可变副本。
2. 快照创建不等于 verify：创建只是文件复制 + 元数据，不违反"禁止现行代码全量 verify"的机器红线。
3. SNAP_002 create 曾三次成功，同等量级下 SNAP_003 create 大概率成功；即使 create 因 hash 计算 OOM，也能快速暴露并转回"先实现分片 hash"路径。
4. 分片 hash 实现需要测试周期，不应让批 2 状态在此期间未冻结。
5. SNAP_003 创建后立即标记 `pending_verify`，在分片 verify PASS 前不执行 `bind --protect`。

### SNAP_003 创建硬约束

```text
确认无写锁 / 无写任务
→ 使用 governance_snapshot create（当前代码，接受其全量 hash 计算）
→ 产出 SNAP_003 + manifest
→ 立即标记 pending_verify=true / protected=false
→ 禁止 prune
→ 不跑 verify
```

### 分片 hash 实施顺序

```text
ZCode 按 R1-R5 修订 spec
→ DSH 复审通过
→ 实现 table_hash() 分片逻辑
→ 等价性测试（新旧 hash 一致）
→ SNAP_001/SNAP_002 分片 verify PASS
→ SNAP_003 分片 verify PASS
→ bind --protect SNAP_003
→ 关闭 3B 批 2 verify 门禁例外
```

### 风险兜底

- 若 SNAP_003 create 立即 OOM：则裁定反转——必须先落地分片 hash，再用分片实现创建 SNAP_003。
- 分片 verify 若发现 SNAP_003 与源库不一致：必须重新 create，禁止带病快照进入 bind。

## 下一步等待

ZCode 按 R1-R5 修订 `docs/governance-sharded-hash-spec.md`，并同步/异步启动 SNAP_003 创建。

---

# 批 2 执行结果确认 + SNAP_003/分片 hash 顺序终审（2026-08-19，DSH 接手第 2 轮）

## 状态

ZCode 汇报批 2 同步恢复/补拉生产执行完成，并再次询问"先实现分片 hash 还是先创建快照"。

## 批 2 执行结果：**确认通过** ✅

实地核对证据文件存在：
- `output/golden_baseline/batch2_sync_apply.json`（2026-08-19 10:44:49）
- `output/golden_baseline/batch2_specialized_verification.json`（2026-08-19 10:45:43）

汇报数字自洽：
- 07-01 ETF 全池补拉：1,974 只插入，本地 07-01 从 73→2,047；
- 末端增量 08-13→08-18：etf 6,270 行 + stock 16,619 行；
- etf_basic / stock_basic 不扩大写入合理（QDB 含历史全量码，本地为设计边界）。

## 二选一终审：**先创建 SNAP_003，再并行实施分片 hash**

### 明确裁定

**先创建 SNAP_003。**

### 依据

1. 批 2 写入后的状态是当前最宝贵的资产，必须尽快冻结为物理不可变副本；
2. 快照创建只是文件复制 + 元数据，**不违反**"禁止现行代码全量 verify"的机器红线；
3. SNAP_002 create 曾三次成功，同量级下 SNAP_003 create 大概率成功；
4. 分片 hash 实现需要测试周期，期间不应让批 2 状态处于未冻结。

### 硬约束（与第 1 轮一致）

```text
确认无写锁 / 无写任务
→ governance_snapshot create（当前代码，接受其全量 hash 计算）
→ 产出 SNAP_003 + manifest
→ 立即标记 pending_verify=true / protected=false
→ 禁止 prune
→ 不跑 verify
```

### 分片 hash 修订状态

`docs/governance-sharded-hash-spec.md` 仍为 2026-08-19 10:29 旧版，未按 R1-R5 修订。**修订前不得实施。**

### 完整下一步顺序

```text
【立即】创建 SNAP_003（pending_verify，不 verify）
【并行】ZCode 按 R1-R5 修订分片 hash 规格 → DSH 复审通过 → 实现 table_hash() 分片逻辑
       → 等价性测试 → SNAP_001/SNAP_002 分片 verify PASS
【随后】SNAP_003 分片 verify PASS → bind --protect → 关闭批 2 verify 门禁例外
```

### 风险兜底

- 若 SNAP_003 create 立即 OOM：裁定反转——必须先落地分片 hash，再用分片实现创建 SNAP_003；
- 分片 verify 若发现 SNAP_003 不一致：必须重新 create，禁止带病快照 bind。

---

# 分片 hash 规格 v2 复审 + 下午 SNAP_003 计划批准（2026-08-19，DSH 接手第 3 轮）

## 状态

ZCode 汇报：
- 批 2 数据修复 + 专项验证 ✅ 完成；
- SNAP_002 pinned/protected ✅；
- SNAP_003 create 暂缓到下午（前置：重启 QuestDB 释放内存，确认内存 ≥12GB）；
- 分片 hash 规格 v2 已按 R1-R5 修订，待 DSH 复审；
- 批 3 / 基线 禁止。

## 分片 hash 规格 v2 复审结论：**有条件通过，1 项阻塞性修正后批准实施**

### R1-R5 修订质量

| 项 | v2 落实情况 | 判定 |
|---|---|---|
| R1 自适应边界 | 改为按 distinct sort key 前缀累计行数等量切分，`compute_shard_boundaries` 算法合理 | ✅ |
| R2 倾斜键 | strategy_events 仅 2,657 行 → 不分片；其他倾斜场景由行数阈值自然处理 | ✅ |
| R3 hash 拼接语义 | 明确为"各片字节流按序追加到同一个 sha256 更新流"，等价性证明直接成立 | ✅ |
| R4 空片/NULL/缺片 | 行数守恒检查已加；**NULL 处理存在排序顺序风险**（见阻塞项 B1） | ⚠️ |
| R5 向后兼容实证 | 验收标准 T1-T5 要求在 SNAP_001/002 真实数据上新旧 hash 逐表对照 | ✅ |

### 阻塞项 B1（必须修正后批准）：NULL 排序顺序一致性

v2 §2.4 提出第一片查询加 `WHERE code IS NULL OR code < first_boundary`，但未指定 `ORDER BY` 的 NULLS FIRST/LAST 语义。

**风险**：DuckDB 默认 `ORDER BY code` 为 NULLS LAST，而上述查询把 NULL 行放入第一片。若第一片内 NULL 被排在非 NULL 之后，则拼接流顺序为 `[非 NULL < first_boundary] + [NULL] + [非 NULL ≥ first_boundary]`，与全表 `ORDER BY code`（所有非 NULL 在前、NULL 在最后）不一致，导致 hash 不等价。

**修正要求（二选一）**：
1. 全部分片查询与全表查询统一使用 `ORDER BY code NULLS FIRST`（NULL 始终在最前）；或
2. NULL 行单独归为最后一片：`WHERE code IS NULL`，不参与前 N 片的范围切分。

规格须显式选定并写入 §2.4。

### 非阻塞观察项

- **O1 单一 code 行数超限**：当前实测 etf_minutes 单 code 最大 68,367 行，远低于 5M 阈值，R1 算法安全。建议规格增加兜底说明：若单一前缀行数超过阈值，自动降级为复合边界或报错，不静默生成超大分片。
- **O2 内存上界**：v2 估计峰值 ~2.5GB 偏乐观，但以 T7「RSS ≤ 4GB」作为实测门禁可接受。

## 下午 SNAP_003 创建计划：**批准**

计划合理：
1. 重启 QuestDB 释放内存；
2. 确认系统内存 ≥12GB；
3. 执行 `governance_snapshot create`；
4. 产出后立即标记 `pending_verify=true / protected=false`；
5. **不跑 verify**（等分片 hash 实现后）。

### 下午执行前确认清单

- [ ] QuestDB 已重启且服务正常（HTTP 9000 可达）；
- [ ] `snapshot_lock` 状态：无活跃写锁 / 无写任务；
- [ ] 可用内存 ≥12GB；
- [ ] create 产出后检查 manifest 存在且 SNAP_003 目录非空；
- [ ] 立即写入 `pending_verify=true`，不 bind --protect。

## 下一步

1. ZCode 修正规格 v2 的 B1（NULL 排序顺序）；
2. DSH 复审通过后，ZCode 实施分片 hash；
3. 下午按上述清单创建 SNAP_003；
4. 分片 hash 验证 SNAP_001/002/003 后 bind --protect。

---

# 分片 hash 规格 v2/v3 状态核查（2026-08-19，DSH 接手第 4 轮）

## 发现

- `docs/governance-sharded-hash-spec.md` 文件已变动：mtime 08/19 11:40:42，大小 7,921 bytes（之前 4,673 bytes）；
- 但 **B1 NULL 排序顺序一致性仍未修正**；
- `data/snapshots/` 仍无 SNAP_003；
- 无其他相关新交付物。

## B1 仍未修正的证据

规格 §2.4 仍写：

> NULL shard key：若 sort key 第一列含 NULL，DuckDB `ORDER BY` 默认 NULL LAST。分片查询需覆盖：第一片查询加 `WHERE code IS NULL OR code < first_boundary`；若无 NULL（主键列通常 NOT NULL），此分支不触发。

该表述未指定 `ORDER BY ... NULLS FIRST` 或 NULL 单独最后一片，等价性证明在含 NULL 场景下不成立。规格尚未达到可实施标准。

## 要求

必须产出 **v3** 并显式修正 B1（二选一）：
1. 全部分片查询与全表查询统一使用 `ORDER BY {sort_key} NULLS FIRST`；或
2. NULL 行单独归为最后一片，不参与前 N 片范围切分。

同时建议：
- 将版本号更新为 v3（当前文件头仍写 v2）；
- 在 §2.4 明确写出含 NULL 时的 shard 0 / shard_last 查询模板。

## SNAP_003

仍按计划下午创建，前置条件不变（重启 QuestDB → 内存 ≥12GB → 无写锁 → create → pending_verify）。

---

# SNAP_003 创建启动确认（2026-08-19，DSH 接手第 5 轮）

## 启动汇报

ZCode 汇报 SNAP_003 create 已启动：
- 前置门禁通过：SNAP_002 pinned/protected、三 hash 一致、批 2 专项验证通过、无 journal/无写锁；
- 系统可用内存 15.8GB；
- 任务 ID `exec_7202cb10`；
- 证据将落 `output/golden_baseline/snap003_create_evidence.json`；
- 预计耗时 10-11h（三遍 hash + 26GB 复制）。

## 审计确认

- **启动条件满足**：内存 ≥12GB、无写锁、SNAP_002 已保护，与先前批准清单一致；
- `data/snapshots/hash_spill` 目录 16:03 有更新，与 create 任务活动吻合；
- 当前未发现 `snap003_create_evidence.json`，符合"任务刚启动"状态。

## 持续约束（执行期间不变）

1. **禁止用现行代码跑全量 verify**（机器红线，防 OOM）；
2. create 完成后必须标记 SNAP_003 `pending_verify=true / protected=false`；
3. 批 3 / 基线 持续禁止；
4. 分片 hash 实施仍须等 v3 规格（B1 NULL 排序顺序修正）复审通过。

## 下一步

等待 ZCode 汇报 SNAP_003 create 完成，届时审计证据文件并确认 pending_verify 标记。

---

# SNAP_003 create 失败事故审计 + guard 修复裁定（2026-08-20，DSH 接手第 6 轮）

## 事故经过

- 2026-08-19 15:56:47 SNAP_003 create 启动（PID 10096）；
- 2026-08-20 02:50:08 运行中让路检查 `_yield_check_data_side()` 命中一个 PowerShell 进程（PID 27544）→ GuardAbort → tmp 由 finally 清理，约 10.9h 工作废弃；
- 数据无损：SNAP_002 pinned/protected，批 2 生产库数据完好，写锁已释放。

## 根因核对（实地，代码 + 日志）

**ZCode 定性"guard 把所有 PowerShell 当数据侧任务杀"不成立**：

- `governance_snapshot.py L265`：cmdline 可读的进程仅命中 `DATA_SIDE_PATTERNS` 子串才计数；
- `L242-243`：powershell 明确不做 fail-closed（防 QuestDB 看门狗常驻误报）；
- `L267`：仅 python cmdline 不可读才 fail-closed；
- guard_refused.log 确认 abort 条目存在，但 cmd 截断于 120 字符（`cl[:120]`），无法看到命中的 pattern。

**真实根因：自指命中**——监控命令自身 cmdline 内嵌了 DATA_SIDE_PATTERNS 字面量（很可能健康检查正在 grep 数据侧任务名），裸 `Get-Process -Id 10096` 不含任何 pattern、不可能触发。

日志佐证 guard 对真实任务工作正常：15:49 拒绝含 `get_tushare_dat...` 的 python；20:33 对 3 个 SYSTEM 不可读 python fail-closed。

## 裁定 1：先修复 guard 再重跑（选项 A）

"直接重跑并暂停所有监控"在多会话环境不可执行（DSH/其他会话都可能 spawn PowerShell，靠全员纪律不可靠——正是治理方案要消除的协调失败）。

## 裁定 2：否决 ZCode 两个修复方案，改为扩展名锚定匹配

| 被否方案 | 理由 |
|---|---|
| Get-Process 等只读 cmdlet 白名单 | 覆盖不全（Get-Content/Select-String/Get-ChildItem/type/findstr 全漏） |
| -NonInteractive + 写关键词黑名单 | 排除法追不完；提及 sync/daemon 的内联命令仍误杀 |

**修正版**：真实数据侧任务只有两种形态——python 跑脚本（cmdline 含 `xxx.py`）、任务计划 powershell 跑包装器（cmdline 含 `xxx.ps1`）；监控命令只是裸提及（无扩展名）。

```python
SHELL_PROC_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe"}
# shell 族：仅 pat+".ps1" 或 pat+".py" 命中
# python 等：维持原子串匹配（行为不变）
# python 不可读 fail-closed：维持
```

内层 writer 由 python 子进程兜底（如 `powershell -Command "python -m ..."` 的 python 子进程原子串命中），无漏检。

**可诊断性要求**：hits 记录 `matched_pattern` 字段（本次因 120 字符截断无法定位命中 pattern）。

## 裁定 3：语义红线——保留 abort，禁止改 pause

hash 期间真实写入若 pause-继续 → 时点不一致快照（前旧后新）。abort 是一致性保护；缺陷仅在误报匹配，不在 abort 本身。

## 测试要求

| # | 场景 | 期望 |
|---|---|---|
| U1 | `powershell -Command "... Select-String get_tushare_data"`（内联裸提及） | 不命中 |
| U2 | `python ...\get_tushare_data.py --periods 1d` | 命中 |
| U3 | `powershell -File ...\run_cloud_sync.ps1` | 命中 |
| U4 | python cmdline 不可读（SYSTEM） | 命中（fail-closed 保持） |
| U5 | 既有快照测试 | 全过 |

## 裁定 4：重跑排期硬约束（新发现）

create 全程 10-11h 必然跨越至少一个数据侧窗口（09:00 巡检+备份 / 16:00-21:30 ETL / 22:30 cyq / 03:00 云同步）。不解决排期，修好的 guard 会正确地再次 abort。

要求 ZCode 给出窗口方案，参考：04:00 启动 + 当日临时停用 09:00 `check_etl_integrity`/`qdb_snapshot_backup`（QDB 侧重载、不写 DuckDB 主库）→ ~14:30 完成，避开 16:00 ETL。停用与恢复须登记，不自动沿用。

## 监控纪律（双方，立即生效）

1. create/verify 期间改读 stdout 日志文件监控；
2. 监控命令不得内嵌 DATA_SIDE_PATTERNS 字面量（必要时字符串拼接构造）；
3. DSH 承诺重跑窗口内不 spawn PowerShell 进程扫描，改用 read 工具读日志。

## 下一步顺序

```text
ZCode 按修正版修复 guard（扩展名锚定 + matched_pattern 记录）
→ U1-U5 单测 + 既有测试全过
→ 给出重跑排期方案（含临时停用清单与登记）
→ DSH 确认后重跑 create（stdout 日志监控）
→ 产出后 pending_verify=true / 不 verify
```

---

# Guard 修复复审（2026-08-20 03:00+，DSH 接手第 7 轮）

## 复审结论：**通过** ✅（附 1 项非阻塞登记 + 1 项排期修正意见）

## 实地核验（非转述）

### 1. 代码逐行比对（与 DSH 裁定完全一致）

`governance_snapshot.py`：
- L247 `SHELL_PROC_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe"}` ✅
- L270-274 shell 族扩展名锚定：`(pat + ".ps1") in cl or (pat + ".py") in cl` ✅
- L276-278 python 等维持原子串匹配（行为不变）✅
- L280-281 hits 增加 `matched_pattern` 字段 ✅
- L282 不可读 python fail-closed 维持 ✅

### 2. 新测试独立实跑：10/10 通过（0.09s）

`tests/test_guard_extension_anchor.py`：U1×3（powershell/cmd 内联裸提及不命中）、U2×2（python xxx.py 命中）、U3×3（.ps1/.py 经 shell 调用命中）、U4×1（不可读 python fail-closed）、U5×1（matched_pattern 字段）。

### 3. 生产环境活证据（本轮复审的意外收获）

复核既有测试时发现 2 个 `test_governance_snapshot.py` 失败——**不是回归**，是测试运行此刻（03:00 窗口）**真实云同步在跑**，guard 当场捕获：

```
powershell -File ...\run_cloud_sync.ps1 → matched_pattern: run_cloud_sync（.ps1 锚定命中）
python ...\run_sync_now.py --all       → matched_pattern: run_sync_now（python 子串命中）
```

guard 修复的核心场景（真实任务捕获 + 误报消除 + matched_pattern 可诊断性）在真实环境一次验证通过。第 3 个失败 `test_mcp_etf_latest_anchor` 为同因（云同步写数据干扰），非 guard 相关。

## 非阻塞登记项 T1：测试隔离缺陷

`test_governance_snapshot.py` 的 hash 测试直接调用真实 `_yield_check_data_side()`，未 mock 进程扫描 → 在数据侧任务运行时段必然失败（ZCode 的 27/27 是在空闲时段跑的）。**不是产品缺陷**。建议：测试中 patch `_data_side_tasks_running` 返回 []（或 patch `_yield_check_data_side` 为 no-op）。排入登记表，不阻塞重跑。

## 排期修正意见（对 04:00 方案）

本轮复审实测 03:00 云同步仍在运行（run_sync_now --all）。04:00 启动前必须确认：
1. 云同步已完成（guard 启动检查会自动拦截，若未完成 create 会被 REFUSED 退出码 6——非 abort，无损失）；
2. 若 04:00 同步未结束，**顺延至同步完成 + 内存复核后再启动**，不抢跑。

排期方案（04:00 + 临时停用 09:00 QDB 巡检/备份 + 登记 + ~14:30 完成避开 16:00 ETL）**原则批准**，按上述两点执行。

## 下一步

```text
ZCode 执行重跑：确认云同步结束 → 内存 ≥12GB → create 启动（guard 自动拦截异常）
→ stdout 日志监控（不 spawn PowerShell 扫描；监控命令不内嵌 pattern 字面量）
→ 产出后 pending_verify=true / 不 verify → DSH 审计证据
```

---

# 04:00 拒因核查 + 重跑窗口终审（2026-08-20 08:50，DSH 接手第 8 轮）

## 一、ZCode 两问的实地核查结果

### ① QuestDB 重启：**不需要** ✅

- QuestDB（PID 29716，08-19 10:25 启动，未重启过）工作集已被 Windows 完全 trim 至 **0GB**（同步结束后系统自然回收物理页）；
- HTTP 9000 存活（端口监听在，探测返回 405 系方法不当，服务健康）；
- **系统可用内存实测 14.81GB ≥ 12GB 门槛**。ZCode 汇报的"18.9GB/可用 1.6GB"是 04:00 时刻状态（同步 + QDB 双占用），同步结束后已自愈。04:00 的真正阻塞是同步进程 fail-closed 拦截（正确行为）。

### ② 同步结束时间：**已结束**，但时长假设须修正

- PID 48556 已消失；
- 实测时长：03:00 启动 → ~08:30 结束（约 **5.5h**），不是"通常 1-2 小时"。后续排期按 5-6h 假设。

### 附：三重校验纵深核实（顺带确认）

`cmd_create` 结构：`pre_hash → shutil.copy2(主库) → VACUUM INTO(aux) → post_hash → copy_hash`，校验 `pre == post == copy`。复制期间主库被写入会因 `pre≠post` 失败退出——**跨数据侧窗口不会产出撕裂快照**（只会浪费 10h），排期避窗是效率与 OOM 风险问题，非一致性问题。

## 二、任务计划实测（排期依据）

| 任务 | 排程 | 下次 |
|---|---|---|
| TradingCloudSync（增量） | Weekly | **周五** 03:00 |
| TradingCloudSyncFull（全量） | Weekly | **周六** 03:00 |
| Trading_Daily_ETL_1600 | Daily | 每天 16:00（**含周末**） |
| Trading_CyqChips_Evening_Fill | Daily | 每天 22:30（含周末） |
| 09:00 巡检/备份 | Daily | 每天 09:00 |
| 交易时段 guard | 周一~五 09:15-15:05 | 启动时硬拦 |

**结论：工作日无连续 10-11h 窗口**（白天交易时段 + 晚间 ETL + 凌晨同步 5.5h + 早上 09:00 任务，全覆盖）。周末亦非干净窗口（周六 03:00 全量 + Daily ETL/cyq 含周末）。

## 三、重跑窗口终审：**批准方案 A（周六 23:30 启动）**

| 方案 | 时段 | 需临时停用 | 风险 | 裁定 |
|---|---|---|---|---|
| **A（推荐）** | **周六 23:30 → 周日 ~09:30** | 周日 09:00 `check_etl_integrity` + `qdb_snapshot_backup`（QDB 侧重载、不写主库，停一次低风险） | 周日非交易日：无交易时段、无盘中实盘内存竞争；周日 03:00 无同步任务 | ✅ **批准** |
| B（备选） | 周四 23:30 → 周五 ~10:00 | 周五 03:00 增量同步 + 周五 09:00 两项 | 收尾落周五盘中，与实盘框架抢内存（SNAP_002 verify 三轮 OOM 教训）；同步停一次 | 不推荐 |
| C | 周五 23:30 → 周六 10:30 | 周六 03:00 **全量**同步 | 停全量一周，数据完整性基线延迟 | 否决 |

方案 A 执行清单：
- [ ] 周六 22:30 cyq 结束确认（stdout/日志方式，不 spawn PowerShell 扫描）
- [ ] 23:30 create 启动（guard 启动检查自动拦异常）
- [ ] 周日 09:00 两项任务已停用并登记（登记表 + 审计记录，不自动沿用）
- [ ] 周日 ~09:30 完成 → `pending_verify=true / protected=false` → DSH 审计证据

## 四、小观察（登记，非阻塞）

guard 日志 `matched_pattern: 'fail_closed'`——fail-closed 分支哨兵值，可区分保守处置与真实 pattern 命中，可诊断性目标达成，行为正确。

---

# 跳过 SNAP_003 提案审计（2026-08-20，DSH 接手第 9 轮）

## 一、前提事实核查：**"批 3 = 删 4 行"定性不成立** ❌

登记表（issue_registry.md）实地核对：

| 条目 | 登记表实际状态 | 与 ZCode 表述的出入 |
|---|---|---|
| D2-F1 B 组重锚 | `repairing`——"**~40 码历史 front 重锚刷新**，走唯一写入会话，等 A 线快照落地；第 4 步基线阻塞中"（L14，含 300750=S2-2 根因） | **批量价格数据 UPDATE**，非 4 行删除 |
| D2-F5 | 4 行 strategy_events 去重（L44） | 与表述相符，但只是批 3 的一小部分 |
| D2-F6 | 589020 因子链 7 月末损坏，`pending`（批 2 排查，L54） | 是否已在批 2 修复未明，批 3 范围待确认 |

另外"已浪费 2 次尝试 ~20 小时"不准确：首次 abort 损失 ~10.9h，二次 04:00 被启动守卫即拒（退出码 6，零损失），合计约 11h。

**结论：跳过理由的事实基础错误，不能按"4 行删除"的定性直接批准。**

## 二、重新裁定：**跳过 SNAP_003 本身仍可批准**（基于修正后的理由 + 硬条件）

### 成立的真实理由

1. **回退锚存在且已验证**：SNAP_002（pinned/protected）+ `batch2_sync_recovery.py --apply` 可重放（批 2 已成功执行过一次）→ 恢复到批 3 前状态可行，代价为小时级而非不可达；
2. **写前快照对批 3 的两项实际价值均可由更轻手段替代**：
   - 回退价值 → SNAP_002+重放覆盖；
   - 差异锚点价值 → 批 3 脚本的**行级变更清单**（粒度优于快照 diff）；
3. **快照无法即时验证**：分片 hash 未落地，SNAP_003 产出后只能 pending_verify 挂着——纯持有成本；最终快照（数据定型后）才是基线绑定对象，两个 10-11h 快照合并为一个，净省一个完整周期；
4. **纵深防御不因缺快照而空**：3A 写锁（唯一写入会话）+ DuckDB 事务原子性 + 行数断言 + D2 复检 + 最终快照三重 hash + 基线双跑一致性。

### 硬条件（全部满足才可执行批 3）

| # | 条件 | 说明 |
|---|---|---|
| C1 | **批 3 范围精确枚举** | 执行前列出全部工单（D2-F1 B 组确切码清单与行数——**精确枚举，禁止抽样估计**；D2-F5；D2-F6 是否在内及结论），报 DSH 确认后才动 |
| C2 | **行级变更清单** | 重锚脚本每笔 UPDATE 记录 `(code, date, old_front, new_front)` 落盘为变更清单文件（差异锚点替代） |
| C3 | **事务 + 行数断言** | 批 3 全程 3A 写锁 + 事务包裹；受影响行数/码数超出 C1 枚举值即 ROLLBACK，禁止带偏差继续 |
| C4 | **D2 复检 PASS 门槛不变** | 批 3 后重跑 D2（修正时区口径版），全 PASS 才可建最终快照 |
| C5 | **最终快照 = 唯一必需快照** | 数据定型后 create → 分片 hash verify → bind --protect；此快照同时承担"批 3 写后快照"与"基线绑定快照"双职责 |
| C6 | **例外登记** | 本例外（跳过每批一快照）写入登记表与审计记录，注明理由与条件，**不自动沿用**；后续批次默认恢复快照纪律 |

### 顺序

```text
C1 范围枚举报审 → 批 3 执行（C2/C3）→ D2 复检 PASS（C4）
→ 最终快照 create（周末窗口方案 A）→ 分片 hash 实现+verify（等 v3 规格复审）
→ bind --protect → 黄金基线建立（第 4 步）
```

## 三、给用户的提示

本裁定是对原方案"每批一快照"纪律的**显式例外**：以"SNAP_002+脚本重放回退锚 + 行级变更清单差异锚"替代批 3 写前快照。防御纵深仍在，但依赖批 3 脚本质量（C2/C3 是关键）。若您倾向保守（严格执行原纪律），则维持周六 23:30 窗口先建 SNAP_003 再批 3，代价是多一个 10-11h 周期。

---

# C1 批 3 范围枚举复审（2026-08-20，DSH 接手第 10 轮）

## 复审结论：**通过** ✅（三项实证全部独立验证成立，批准批 3 立即执行）

## 独立验证（非转述）

### ① D2-F1 B 组已修复（SNAP_001 vs SNAP_002 表级 hash 独立证据）

- `batch1_reanchor_apply.json`（2026-08-18T15:33:35）：apply=true，40 码清单在案（含 002029/600060/300750）；
- `batch1_table_hash_delta_SNAP001_vs_SNAP002.json`：20 表中**仅 stock_daily 变化**，19 表逻辑 hash 一致——与"40 码重锚"范围精确自洽，无越界改动；
- 早前会话（第 13 轮）记录的是批 1 之前的旧状态，登记表 L14 状态行过时属实。**同意 repairing→closed。**

### ② D2-F5 = 3 行删除（独立复算确认）

我方直连主库复算：
- total=2657, DISTINCT*=2657 → **全列完全重复 = 0**（此前"1 行全列重复"估计确有误，ZCode 自我勘误属实）；
- 复合键重复恰 3 组×2 行：`first_cover` @2026-06-26 的 002792.SZ / 603861.SS / 002380.SS；
- 每组两条记录 imported_at 完全相同（2026-07-25 12:39:59.87121）——"保留最新"在时间并列时退化为**任删其一**，结果等价且确定（两行全列同值）。**批准 3 行删除，2,657→2,654。**

### ③ D2-F6 重新定性成立（数学复算确认）

我方独立复算 589020：
- aux.adj_factor 该码 0 行、fund_adj 2,617 行（ETF 因子确在 fund_adj）；
- 07-26~08-05 逐日 `close/close_front` 恒等于 **1.9992**（= 08-10 合并事件因子，前复权分母统一），08-10 起为 **1.0**——front 数学正确，因子链完好；
- front 震荡源 = raw 价格自身震荡（2.555→4.798→2.377…），与 D3-KN-2 模拟数据特征同类。**同意 pending→known-noise。**

## 批 3 最终批准范围

| 工单 | 操作 | 验证 |
|---|---|---|
| D2-F5 | DELETE 3 行（first_cover @2026-06-26 三码，事务包裹） | C2 变更清单 + 删后 count=2654 + 复合键零重复 |
| D2-F1 | 登记表 repairing→closed（附批 1 证据引用） | 文档操作 |
| D2-F6 | 登记表 pending→known-noise（附本复算证据） | 文档操作 |

## 执行要求（C2/C3/C4 收口）

1. **C2**：DELETE 前落盘 3 行完整快照（全列值）至 `output/golden_baseline/batch3_deleted_rows.json`；
2. **C3**：3A 写锁 + 单事务；删后断言 count=2654 且复合键重复=0，异常即 ROLLBACK；
3. **C4**：D2 复检（时区修正版）+ strategy_events 表级 hash 更新入证据；
4. 完成后报我验收，随后进入最终快照窗口（周六 23:30 方案 A）→ 分片 hash verify → 基线。

---

# D2 复检 FAIL 逐项裁定（2026-08-20，DSH 接手第 11 轮）

## 独立验证（裁定依据）

### ① G2 归因"云端 stale as-of"：**验证成立** ✅

抽 600675 @2020-09-16 独立复算本地 front 自洽性：
- aux.adj_factor 该日尾因子 40.3998、最新因子 43.8979；
- `expected = close × f_sample/f_latest = 4.07 × 40.3998/43.8979 = 3.745673`；
- `actual = 3.745673` —— **精确匹配（<1e-6）**。
本地 front 按当前锚重算正确，G2 的 11 笔 real_diff（stock 8 + etf 3，全部 vol_match=True）定性为"已解释：云端 stale as-of"成立，与 D2-F1 A 组同性质。

### ② G1 缺口"不触达基线策略"声明：**部分不实，需修正后接受** ⚠️

ZCode 称"基线策略 2026-07 区间内 600069/BJ 代码不被触达"。实地核查：
- **600069**：本地 0 行，确实不触达 ✅；
- **BJ 920xxx**：stock_daily 2026-07 有 7284 行（332 码）、**stock_daily_valuation 有 337 个 BJ 码**——小市值策略按 float_value 排序**会触达这些码**。"不触达"声明不成立。
- 但缺口方向核查：338 个"稀疏"BJ 码全部是**近期上市新股**（920107 上市 08-17 仅 2 行、920138 上市 08-11 仅 5 行……），行数少 = 上市晚的自然属性而非同步缺陷；上市日全部 ≥ 2026-07-15（多数在 08 月）。
- **对基线的影响**：新股在上市首日前无 bar 本来就不进池（PIT 语义正确）；已进入 2026-07 池的 BJ 码（上市 ≤07 月的）数据完整。**结论：不阻塞日线基线，但归因表述须从"不触达"修正为"BJ 新股 PIT 自然属性 + valuation 覆盖完整"。**

## 裁定 1：G2 全部已解释——**接受** ✅

11 笔全部归入"已解释：云端 stale as-of"（登记表 known-noise 类，引用本复算证据）。**不阻塞基线。**

## 裁定 2：G1 日线/静态表缺口——**有条件接受，不阻塞基线**

~939 行缺口逐一归类（须按此更新登记表）：

| 项 | 归因 | 登记 |
|---|---|---|
| stock_daily 849 | 600069 整码（长期停牌 0 行）+ BJ 新股上市晚自然属性 | 已解释 known-noise + 持续同步工单（600069 恢复交易后需补拉） |
| etf_daily 20 | 增量滞后残余 | 已修复主体 + 尾差入 D1-TODO-1 跟踪 |
| etf_basic 35 / etf_dividend 15 / stock_basic 7 | QDB 有历史全量码 vs 本地设计边界 | **已解释（设计边界）**，新 ETF/IPO/分红事件入持续同步工单 |
| index_daily 13 | 末端+历史回填（D2-F4 既有） | known-noise + 工单（第 4 步基线前不强制） |
| 分钟表两大项 | D3-KN-1 既有 | known-noise（维持） |

## 裁定 3：C4 门槛语义确认

**本复检 = 批 3 后的 D2 复检通过判定依据**：G3 PASS + G2 全部已解释 + G1 剩余全部归类为已解释/设计边界/既有 known-noise + 新增持续同步工单。**C4 满足，基线解除阻塞。**

## 基线建立前置链（当前状态）

```text
批 3（3 行删除）✅ → D2 复检归类完毕（本轮裁定）✅
→ 最终快照 create（周六 23:30 方案 A）
→ 分片 hash 实现（等 v3 规格 B1 修正）
→ 最终快照 verify PASS + bind --protect
→ 黄金基线建立（第 4 步）
```

---

# 批 3 验收 + 周六窗口准备审核 + 未审变更裁定（2026-08-20，DSH 接手第 12 轮）

## 一、批 3 验收：**通过** ✅（独立验证）

- `batch3_deleted_rows.json`（09:36:08）：3 组×2 行完整值在案（C2 ✅）；
- 我方直连主库复算：**total=2654, DISTINCT*=2654, 复合键重复组=0**——与汇报 deleted=3/count=2654/dup=0 一致（C3 断言 ✅）；
- D2-F5 登记表 L44 → **closed** ✅。

## 二、登记表 v1.21：**确认** ✅

L79-80 变更记录与 DSH 第 11 轮裁定逐条对应：G2 11 笔 known-noise（引用 600675 复算确证）、G1 939 行逐项归类、D2-F1 closed、D2-F6 known-noise、C4 满足基线解除阻塞。

## 三、未审先改发现：`QDB_READ_ONLY_PATTERNS` 白名单（L359-376）⚠️

ZCode 汇报"guard 修复：QDB 只读任务 yield 不触发 abort"——**这是新行为变更，未经 DSH 审计即实施**，且表面上触碰了第 6 轮裁定 3 的红线（"保留 abort，禁止改 pause"）。

### 实地核查（代码 L359-376）

- `_yield_check_data_side()` 过滤掉 `matched_pattern ∈ {check_etl_integrity, qdb_snapshot_backup}` 的命中，仅剩余写者触发 GuardAbort；
- **启动守卫 `data_side_guard()` 未变**——QDB 任务运行时 create/verify 仍会被启动拒绝（干净起点保持）；
- 三重 hash（pre==post==copy）不受影响——若 QDB 任务真写了主库，hash 校验会失败退出。

### 裁定：**追认通过**，附 2 项条件

语义精确性：这**不是**"abort→pause"（pause = 检测到写者后等待再继续），而是**收窄 yield 检查的拦截范围**（QDB 只读任务不触发 abort）。第 6 轮红线禁止的是"对真实 DuckDB 写者等待继续"，本变更不违反——DuckDB/qfq_aux 写者仍然 abort。且推理链成立：这两个任务只读 QDB（不写快照源），启动守卫保证起点干净，三重 hash 是最终防线。**与周六窗口方案 A 原目的（临时停用 09:00 QDB 任务）部分重叠但更精细。**

**条件**：
1. **该变更须补微流水线记录**（改动说明追加至本审计文件即可，已由本节完成）+ 单测（QDB 任务命中不 abort、DuckDB 写者仍 abort 两用例）——下次汇报附结果；
2. **OOM 风险不因白名单消除**：QDB 备份重载可能挤内存。周日 09:00 停用建议**维持**（协调归属见下）。

## 四、周六窗口执行确认

| 项 | 状态 | 备注 |
|---|---|---|
| 时间 | 周六 23:30 → 周日 ~09:30 | 周日非交易日 ✅ |
| 磁盘 | 319GB ≥ 27GB ✅ | |
| SNAP_002 | pinned ✅ | |
| 自动启动链 | ZCode 自设（22:30 cyq 确认 → 23:30 create） | 批准 |
| 周日 09:00 QDB 停用 | **建议维持**（防 OOM，非防 abort） | 须临时停用 `check_etl_integrity` + `qdb_snapshot_backup` 并登记 |

### 协调归属裁定

**由用户决定执行方式**：ZCode 声明"你协调周日 09:00 QDB 任务停用"指向 DSH，但 DSH 是审计方且目标窗口内承诺不执行生产操作（第 6 轮监控纪律）。**建议**：ZCode 在自动启动链中自带停用步骤（schtasks /change /disable + 完成后 /enable + 登记），或由用户手动停用。DSH 仅在窗口外预检磁盘/SNAP_002/日志文件（只读）。

## 五、分片 hash v3：催办

最终快照 verify 依赖分片实现，分片实现依赖 **v3 规格（B1 NULL 排序修正）**。v2 至今未修正（11:40 版本无 NULLS FIRST/单独最后一片表述）。**周六窗口前必须产出 v3 并送审**，否则最终快照只能 pending_verify 挂起（可接受——快照物理副本已冻结，verify 可后补）。

## 六、批 3 完成后的登记表更新（ZCode 待办）

跳过 SNAP_003 例外（C6）+ QDB_READ_ONLY_PATTERNS 追认（本节）写入登记表。

---

# 分片 hash v3 复审 + QDB 白名单单测验收 + orchestrator 审核（2026-08-20，DSH 接手第 13 轮）

## 一、QDB 白名单单测：**验收通过** ✅

12/12 独立实跑通过（0.09s），含两个追认条件用例：`test_qdb_readonly_no_abort`（check_etl_integrity 命中不抛）+ `test_duckdb_writer_still_aborts`（get_tushare_data 命中仍 GuardAbort）。第 12 轮追认条件 1 关闭。

## 二、分片 hash v3 复审：**B1 论证存在数学矛盾，须 v4 修正后批准** ❌

### 矛盾点

v3 §等价性论证（L39）自相矛盾：

> "NULL 行在全表排序中位于最末（NULLS LAST），等价于 `IS NULL OR < first_boundary` 的**第一片**之后所有分片之后。"

同一句话里：NULL 在全表排**最末**，但方案把 NULL 放**第一片**——拼接流顺序为 `[NULL + 小 key] → [中 key] → [大 key]`，而全表流为 `[小 key] → [中 key] → [大 key] → [NULL]`。**两个流不同，hash 必不相等。** 这正是第 3 轮裁定 B1 时预言的 bug，v3 修反了方向。

### v4 修正要求（二选一，必须显式）

1. **NULL 归最后一片**（与 DuckDB 默认 NULLS LAST 对齐，推荐——改动最小）：
   - 最后一片查询 `WHERE key IS NULL OR key >= last_boundary`，且**片内** `ORDER BY key NULLS LAST, ...` 使 NULL 排在该片非 NULL 之后；
   - 全表基准查询同样 NULLS LAST（DuckDB 默认，无需改现行 table_hash 的 ORDER BY）；
2. 或全部查询（全表+每片）统一 `NULLS FIRST`，NULL 归第一片且片内 NULL 排最前。

另须补：NULL 计数守恒断言（`count(key IS NULL)` 单列核对）；T8/T9 判据按所选方案改写。

### 现实缓冲（不改变结论）

当前 18 表 sort key 首列实测均无 NULL（DSH 第 3 轮核查 stock_daily/etf_daily/stock_minutes/etf_minutes/index_daily 均 0），v3 的 `IS NULL OR` 分支实际不会触发——**但等价性证明是规格的根基，不能带病批准**；且"理论上都有可能含 NULL"是 v3 自己写的适用范围。

## 三、orchestrator 审核：**2 项缺陷须修**

`scripts/final_snapshot_orchestrator.py`（42 行）实地核查：

1. **O1 日期不校验**：`target = now.replace(hour=23, minute=30)` 只对时/分——**周日~周五任何时刻启动都会等到当天 23:30**，不是"等到周六"。若周五晚被误启动，周五 23:30 起 create 将撞上周六 03:00 全量同步窗口（guard 拦截但浪费一次 10h）。修：显式 `while now.weekday() != 5: sleep 到次日` 或启动时断言 `weekday()==5`；
2. **O2 rc 非 0 无重试/通知**：失败仅记日志退出（可接受——人工介入语义），但**失败时的 guard 拒因未落日志**（guard 自身写 guard_refused.log，orchestrator 日志应引用该文件路径，方便晨起诊断）。

批准运行的前提：O1 必修（周六窗口正确性）；O2 建议修（可诊断性）。

## 四、周日 09:00 QDB 停用：任务名已实证不存在于 schtasks

第 8 轮 schtasks 全量查询中 `\SyncHealthMonitor`/`TradingCloudSync` 等在册，但**无 check_etl_integrity / qdb_snapshot_backup 条目**——ZCode"搜索未找到"属实。这两个 pattern 更可能是 QDB 侧自建调度（非 Windows 计划任务）。**改判**：orchestrator 加 schtasks 停用步骤的方案**不成立**；须先定位真实调度源（QDB 内部 cron / 进程守护 / 其他），找到后再定停用方式。若找不到调度源，则以 QDB_READ_ONLY_PATTERNS 白名单 + 内存预检兜底（guard 启动检查会拦内存不足）。

## 五、周六窗口最终清单（更新）

- [ ] ZCode 产出 **v4 规格**（B1 方向修正）→ DSH 复审
- [ ] orchestrator O1 必修；O2 建议修
- [ ] 周日 09:00 QDB 停用：先定位调度源，找不到则以白名单+内存预检兜底
- [ ] 周六 22:30 cyq 结束确认 → 23:30 orchestrator 启动 create
- [ ] 周日晨 DSH 只读审计 manifest + pending_verify 标记

---

# 分片 hash v4 终审 + orchestrator 验收 + 周六窗口放行（2026-08-20，DSH 接手第 14 轮）

## 一、v4 复审：**批准实施** ✅

### 等价性论证验证（逐步复算）

- 全表流（NULLS LAST）= `[全部非NULL 升序] + [NULL]`；
- v4 拼接流 = `[片1..N-1 非NULL 升序] + [最后片: (key≥last_boundary 升序) + (NULL 片内末尾)]`（最后片内 `ORDER BY` 默认 NULLS LAST，NULL 排片内最末）；
- 两者一致 ✅。**v3 方向错误已正确勘误。**
- NULL 计数守恒 + 总行数守恒双断言在案；T8/T9 判据与方案对齐。

### 非阻塞观察 O3（实现时一并处理）

`compute_shard_boundaries` 按 `GROUP BY key ORDER BY key` 累计行数——**若 NULL 组自身触发边界，会 append `None` 作边界值**，导致前一 shard 范围失效（`key < NULL` 恒假）。行数守恒断言会拦截（fail-safe 不沉默），但应在边界循环中**跳过 NULL 组**（NULL 由最后片 IS NULL 子句承载，不参与边界生成）。实现时加此防御 + T8 用例覆盖该场景。

### 实施排期优化（重要）

v4 既已批准，**分片实现不必等周六**——本周即可实施并完成 T1-T9 等价性验证（在 SNAP_001/SNAP_002 上跑新旧 hash 对照），周日晚最终快照产出后**立即用分片 verify**，同时回填 SNAP_002 的独立 verify（关闭批 2 gate exception 的遗留承诺）。

## 二、orchestrator O1/O2：**验收通过** ✅

实地核查（55 行版本）：
- O1：`while target.weekday() != 5 or now >= target` 循环逻辑正确（周四启动→指向周六 23:30 ✅；周六 23:45 启动→顺延下周六 ✅）；启动 `assert weekday()==5` 双保险（进程挂起恢复到周一则安全中止）；
- O2：失败分支引用 guard_refused.log 尾 3 行 ✅；
- 时区固定 +8 无夏令时问题；单写者纪律下 `index.json[-1]` 取最新快照合理。

## 三、QDB 09:00 调度源兜底：**接受** ✅

调度源非 schtasks（双方独立确认一致）。兜底三层（白名单 + 启动内存预检 10GB + 三重 hash）+ finally 半成品清理，风险定性正确：最坏情况 = OOM 中止重跑，不产损坏数据。一个补充：若 OS 级硬杀进程，finally 可能不执行、tmp 目录残留——**下次 create 前应清理孤儿 tmp**（现有 tmp 命名含时间戳，可在启动时清扫），登记为实现注意项。

## 四、周六窗口：**放行** ✅

最终清单（更新）：
- [x] v4 规格批准（本轮）
- [x] orchestrator O1/O2 验收（本轮）
- [x] QDB 兜底方案接受（本轮）
- [ ] **本周**：分片 hash 实施 + T1-T9（SNAP_001/002 等价性）→ 报 DSH 验收
- [ ] 周六 22:30 cyq 结束确认 → 23:30 orchestrator 启动 create
- [ ] 周日晨：create 完成 → **分片 verify**（若已验收）→ bind --protect → 回填 SNAP_002 独立 verify → 关闭 gate exception
- [ ] 周日 DSH 只读审计 manifest/verify/protect 全链

## 五、流水线状态

```text
批1-3 ✅ D2 ✅ C4 ✅ QDB白名单 ✅ v4 批准 ✅ O1/O2 ✅
→ 分片实施+T1-T9（本周）→ 周六 23:30 最终快照
→ 周日：分片 verify → protect → SNAP_002 verify 回填 → gate exception 关闭
→ 第 4 步黄金基线（三策略已定案）
```

---

# 分片 hash v4 正式审核（应 ZCode 审核请求，逐项裁定）（2026-08-20，DSH 第 14 轮补充）

**审核对象**：`docs/governance-sharded-hash-spec.md` v4（2026-08-20 10:34:47，2,300 bytes，与第 14 轮审计版本一致）

## 逐项裁定

| 审核要点 | 裁定 | 依据 |
|---|---|---|
| ① B1 等价性论证 | **正确** ✅ | 逐步复算：全表流（NULLS LAST）= `[非NULL 升序]+[NULL]`；v4 拼接流最后片内 ORDER BY 默认 NULLS LAST → NULL 排片内最末 → 拼接 = `[全部非NULL 升序]+[NULL]` = 全表流。v3 勘误方向正确 |
| ② NULL 计数守恒 | **完备** ✅ | 双断言在案（L44-46）：`shard_null_count == null_count` + `sum(shard_counts) == total_count`，任何漏片/漏行/NULL 错位都会被拦截 |
| ③ T8/T9 判据 | **与 v4 一致** ✅ | T8 = 含 NULL 表分片 hash == 全表 hash（NULL 在最后片）；T9 = 最后片 NULL 计数 == 全表 NULL 计数。均按 v4 方向改写 |
| ④ v2 主体不变 | **正确** ✅ | 自适应分片（R1）/等价性证明（R3 拼接语义）/R5 验收/内存上界均不受 B1 方向修正影响 |

## 实施附加要求（2 项，随实现落地）

1. **O3（第 14 轮已提，重申为实施必选）**：`compute_shard_boundaries` 边界循环**跳过 NULL 组**——`GROUP BY key ORDER BY key` 中 NULL 组排最后，若其累计行数触发边界会 append `None`，导致前一 shard 范围失效（`key < None` 恒假）。守恒断言会拦（fail-safe），但必须在边界生成时排除；T8 增加该场景用例；
2. **首片模板**：v2 首片定义 `key < first_boundary` 保持**不含** IS NULL 子句（NULL 已归最后片）——实施时确认未从 v3 残留 IS NULL 到首片。

## 审核结论：**通过，批准实施**

目标：周六窗口前完成 实现 + T1-T9（SNAP_001/002 新旧 hash 逐表等价）+ 分片 verify 就绪。
