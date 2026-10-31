# firefly 引擎缺陷批次 · 修复证据与案例登记（2026-10-25/26 两批）

> 状态：**代码侧全处置，出仓与客户终验未闭**（#1/#8 维持 partial，见 §六）
> 依据：①客户 firefly 缺陷清单（11 项 + 附录 A 两项，2026-10-25 20:33，引擎 0.3.2+mvp · HEAD `606e688`）→ ②评估件 `.specify/bugs/firefly-engine-defects-20261025/assessment.md` → ③修复件 fix.md / fix-batch2.md ④验证件 test.md / test-batch2.md
> 核验基线：主仓 `D:\miniQMT策略实盘\QuantStudio`，实施起点 HEAD `5f0fd6f`，批次二起点 `d564797`，收笔 HEAD `f515263`
> 铁律：12-客户运维域 · R4 沉淀落件（`QD-2026-0012` / `QD-2026-0013`，族机检双 PASS）

---

## 一、案件概览

| 项 | 内容 |
|---|---|
| 客户 | firefly（量化数据采集侧） |
| 报告形态 | 11 项缺陷 + 2 项 API 陷阱（附录 A）+ 修复优先级建议（附录 B） |
| 客户自测口径 | 本机实测（py-spy 栈采样 / SQL 查询 / 引擎日志 / 隔离实验），不含推测数据 |
| 本方处置 | 10 项修复落码（两批提交）+ 1 项判 invalid（客户版本已修）+ 2 项平台事实沉淀入知识库 |
| 处置链 | ①评估 → ②审（含改码门/范围裁定/判型裁定）→ ③实施 → ④红绿验证 → ⑤用户批准 → ⑥提交（候统推 #8 出仓） |
| 剩余 | **#1/#8 两项待客户现场终验** + 推送出仓 + 客户通知下发（见 §八） |

## 二、逐项处置表（11 项 + 附录 2 项）

| # | 缺陷 | 客户级别 | 本方 Verdict | 判型（铁律18） | 修复落点 | commit | 验证结论 |
|---|---|---|---|---|---|---|---|
| 1 | DuckDBWriter 大表 UPSERT 卡死 | P0 | **likely valid, needs reproduction**（2026-10-30 回访：**env 取值已固定＝T2 关闭态**；沙箱真规模 4-pass 357 批**零退化零冻结**，但**客户现场卡死未在任何环境复现** ⇒ 归因收窄至客户现场专有因子） | 性能鲁棒型 | `writers.py`：T2 路径去 `RETURNING 1`（changed 审计退役，全仓无下游消费）+ `_apply_conn_spill_defaults`（temp_directory 兜底 + `QS_DUCKDB_MEMORY_LIMIT` env 门控） | `5655fb4` | **partial**（防御性修复已落码；**T2 收益实测 ≈20 倍**（507.2s→25.0s／4.45M 次重复 upsert）；引擎侧修复**非根治项**——客户侧主处置＝开 T2 + 重建库回收死元组 + 串行 + 降批；关闭判据＝客户现场 env 值 + 「2018 起」复跑过 357 批不冻结） |
| 2 | qfq bootstrap 播种与校验同轮竞态 | P1 | **valid（根因修正：控制流缺陷，非竞态）** | 纯恢复型 | `qfq_resident_orchestrator.py:1691-1709` fail-closed 块移入「复检未过」分支 | `5655fb4` | verified（红败→绿；负向硬门 ×2：真失败路径仍 fail-closed held） |
| 3 | `supersede_stale_intents` 永久作废可恢复 intent | P1 | **valid** | 纯恢复型 | 同文件 `supersede_stale_intents` 排除 `finalized_held` + 每键过继最大 candidate 给新周期（经 gate 重裁）+ `begin_cycle` 重排 | `5655fb4` | verified（红败 ×2→绿；死周期/orphan 照旧清障负向） |
| 4 | `_skip_security` 留 `in_progress` 毒化 gate | P1 | **valid** | 纯恢复型 | 同文件 `_skip_security` 显式回退 `pending` + 清 claim 租约（CAS 守卫仅 in_progress 态） | `5655fb4` | verified（红败→绿；非 in_progress 不覆盖、跨世代不动） |
| 5 | GUI 只有水位线列、无数据止点列 | P1 | **invalid（客户 HEAD `606e688` 已含 FP-1/FP-2 修复，2026-10-18）** | — | 不改码 | — | 客户现象表自身画出了「数据止点」列且值为 2026-10-23；建议客户核对 GUI 运行构建版本 |
| 6 | qfq 周期跨资产类别污染 | P1 | **valid** | 行为修正型（保守口径） | 同文件 `_claim_and_merge` 资产域守卫 + `_asset_domain_ready_map`（EXISTS LIMIT 1，探测 fail-open） | `5655fb4` | verified（红败→绿；域有 1 行即放行，防过度限定） |
| 7 | `advance_watermark` 无防倒退守卫 | P2 | **valid** | 行为修正型（守卫） | `writers.py::_advance_watermark_locked` + `qfq_resident_orchestrator.py::_advance_watermark`（含/无 `last_run_id` 两形态）：`last_date=GREATEST(COALESCE(旧行,新值),新值)` | `2707414` | verified（红败 ×2→绿；旧行 NULL 首写不吞） |
| 8 | MCP `fetch_page` 不支持日期过滤 | P2 | **valid（口径订正：服务端 fetch_page 实测**支持**日期过滤——客户实测的「无法按窗取数」是**引擎侧 `mcp/client.py` 未透传**；本轮已落出口径修正）** | 纯性能型（判型依据=对拍门，已过） | `mcp_adapter.py`：`_fetch_export_passthrough` 透传归一化时间窗（`_norm_window_date`，None=不下推）+ `export_passthrough_tables` 显式清单路由（默认 `sw_daily`/`sw_weight`）；**更优解候选＝引擎侧透传服务端 filters，待②审/用户裁定，本轮不擅改** | `2707414` | **verified**（2026-10-30 生产同源 MCP 通道真机对拍：列 15/15 且列序一致 · 行 8,673=8,673 · 键集合全等 · 逐列逐值零差异 · parquet sha256 与 manifest 一致；附 export `trade_date` 为 DATE 的落库 dtype 风险 ⇒ 清单启用与批量启用解耦） |
| 9 | `qfq_orchestrator.freqs` 是死配置 | P2 | **valid** | 文档注释级（零行为改动） | `qfq_orchestrator_types.py`：字段块 + `validate()` 处标注「仅启动校验、不参与门控」 | `2707414` | verified（零代码路径变化；类型套件全绿） |
| 10 | QualityAudit 与 writer gate 不对齐 | P2 | **valid** | 行为修正型（观测面） | `quality_audit.py`：EpsBackfillGap 读同一开关 `QS_AUTO_BACKFILL_EPS`（关→WARN 带补救指引 / 开→error） | `2707414` | verified（红败→绿；gate 开维持 error 负向） |
| 11 | 质量门阈值对小样本过严 | P2 | **valid** | 行为修正型（判负线） | `daemon.py::_failure_gate` 末端 OR 豁免：`failed≤max_reject_abs_floor(200)` 且 `rate≤max_reject_hard_cap(0.05)`（`quality_gate.*` 可配，仅拒批通道） | `2707414` | verified（红败→绿；灾难率 50% / 超下限 250 / 拉取失败通道 / 配置归零 四重负向） |
| A-1 | `estimated_size` 不可靠 | 附录 | 平台事实（不修码） | — | 知识条目 `QD-2026-0013` | `f515263` | 已沉淀（估计行数 ≠ 实际行数；不得据此推断死元组） |
| A-2 | duckdb 1.4.5 `VACUUM` 空操作 | 附录 | 平台事实（不修码） | — | 知识条目 `QD-2026-0013` | `f515263` | 已沉淀（空间回收唯一途径＝ATTACH+CREATE TABLE AS 重建库） |

### 对客户报告的三处修正（评估阶段确认，已写入提交信息）

1. **#2 根因**：客户判为「播种与校验同轮提交可见性竞态」；代码实锤为**控制流缺陷**（fail-closed 块写在复检 `if/else` 之外，播种成功亦必然 held）——客户建议的「强制 commit / 刷新连接可见性」**修不好它**。客户日志「同秒内播种 INFO + fail-closed WARNING」与该控制流逐字吻合。
2. **#5 判 invalid**：客户自述引擎 HEAD `606e688`（2026-10-24）**已含** 2026-10-18「数据止点」列修复（FP-1/FP-2，列 7→9，正是此前 firefly 水位线案）；其现象表自身亦画有该列且值非空。
3. **#1 事实互斥 + 升级封死**：客户称 `QS_UPSERT_SKIP_IDENTICAL` 默认关闭，但 py-spy 冻结行（`RETURNING` 行）**仅在开关开启时执行**（606e688 与 HEAD 同构）——两者互斥，取值待回访确认；且版本钉 `duckdb>=1.4.5,<1.5`（1.5.x ART index-delete fatal 回归，issue duckdb#23645）**封死**「升级验证」路径。

## 三、提交清单（三笔）

| commit | 内容 | 变更规模 | message 要点 |
|---|---|---|---|
| `5655fb4` | 批次一 #1#2#3#4#6 | 6 文件，+625/−64 | ⑤依据=2026-10-25 用户批准（firefly 第一批·#1 partial 口径）+ 判型 + 偏离 4 条 + 红绿读数 + 回退点指针 |
| `2707414` | 批次二 #7#8#9#10#11 | 8 文件，+380/−13 | ⑤依据=2026-10-26 用户批准（启动令+提交令）+ 判型四型 + **#7 扩围随令追认** + **#8 对拍关闭条件** + 偏离 1 条 |
| `f515263` | R4 知识沉淀 QD-2026-0012/0013 | 2 文件，+74 | 依据=2026-10-26 R4 落件推进令 + kb_draft 起草 + kb_hook qd 族机检双 PASS + `本轮沉淀：QD-2026-0012 + QD-2026-0013` |

回退点（铁律 03 写前快照，stash store 持久化不 drop）：`2ead3bb0`（批次一）· `93f6fbe5`（批次二）· `bacc3562`（R4 落件）。
铁律 10 纪律：全程精确路径 add（禁 `add -A`）、staged 清单逐项核对后提交、提交后残留检查为空；共享工作树他线在途文件零触碰零混入。

## 四、验证读数（红→绿）

| 批次 | 红态（修复前基线 worktree） | 绿态（修复后） | 机检 |
|---|---|---|---|
| 一（#1#2#3#4#6） | HEAD `5f0fd6f` worktree：`7 failed, 6 passed`（败项＝五缺陷正向面：水位倒退 ×2 / export 时间窗 ×2 / audit 误判负 / 小样本判负；过项＝负向对照，两侧皆绿） | 17 套件合并回归 **201/201 全绿（102.62s）** | — |
| 二（#7#8#9#10#11） | HEAD `d564797` worktree：`6 failed, 8 passed`（败项＝五缺陷正向面） | 14 套件合并回归 **203/203 全绿（101.90s）**（含水位语义最相关的 `test_qfq_formal_watermark_release`） | — |
| R4 沉淀 | — | — | `kb_draft.py` 起草（族 schema 驱动）+ `kb_hook.validate_record` 按 qd 族真 schema **PASS ×2**（空错误列表） |

**负向对照清单（防矫枉过正）**：#2 真失败路径仍 fail-closed held ×2 · #3 failed/orphan 照旧清障 + 同键只过继最大候选 · #4 非 in_progress 不覆盖、跨世代不动 · #6 域有 1 行即放行 · #7 旧行 NULL 首写不吞 · #10 gate 开维持 error + 无缺口不报 · #11 灾难率/超下限/拉取通道/配置归零四重。

## 五、判型与偏离

**判型四型**（铁律 18）：#2/#3/#4 纯恢复型 · #6 行为修正型（存量影响面：客户库 447 条 in_progress 毒化 trigger + 已被 superseded 的 etf_daily 候选，自愈路径见 fix.md「#6 存量处置」）· #1 性能鲁棒型 · #7 行为修正型（守卫）· #8 纯性能型（对拍门候真机）· #9 文档注释级 · #10 行为修正型（观测面）· #11 行为修正型（判负线，判拒结果零变化）。

**偏离评估 5 条**（批次一 4 条 + 批次二 1 条，均已随 commit message 申报）：

1. #3 实施为「排除 + 过继」而非纯排除（纯排除会使 held 候选永久休眠＝客户痛点不解；过继须经新周期 gate 重新裁决，不绕过任何门）；
2. #1 `memory_limit` 改 env 门控（`QS_DUCKDB_MEMORY_LIMIT`，默认不设）而非直接设默认值——直接设会改变所有部署的内存行为，违背纯增益前置审计；
3. #4 回退 UPDATE 加 CAS 守卫（`AND status='in_progress'`）与部分回退留痕（评估未细化并发语义）；
4. 三个既有测试契约随修复更新（`test_require_bootstrap_fail_closed` 改造为真失败路径、`empty_fresh` 的 C2 契约、T2 changed 断言退役）——均为修复必然引起的契约变化，非放松；
5. #7 扩围（orchestrator 回退 upsert 同守卫）——呈批件标注「待裁」，经 2026-10-26 提交令追认。

**编码委派纪律**：两批均**未委派 zcode_code、自行实施**并已在 fix.md 申报——五/六处改动皆为手术式小改，与评估逐行代码证据强耦合，委派的上下文重建成本高于直接实施。

## 六、#1 / #8 的终验状态（2026-10-30 回访实测更新；回执全件 `docs/evidence/firefly-visit-readout-20261030.md`）

| 项 | 已完成 | 状态 |
|---|---|---|
| **#8** 大表 passthrough export 通道 | 路由（显式清单，默认 sw_daily/sw_weight）+ 时间窗透传（服务端下推）单测全绿；清单外表行为逐字节不变；**2026-10-30 生产同源 MCP 通道真机对拍通过**——`qdb.sw_daily` 窗口 2026-09-01~2026-09-30：列 15/15 且**列序一致**、行 **8,673 = 8,673**、键集合全等（only_a=0/only_b=0）、日期归一后**逐列逐值零差异**、export parquet sha256 与 manifest 核验一致（`844961a0…`） | **verified**（附两项口径修正：①**服务端 fetch_page 实测支持日期过滤**——客户实测的「无法按窗取数」是**引擎侧未透传**（更优解候选＝引擎侧透传 filters，待裁，本轮不擅改）；②export `trade_date` 为 DATE ⇒ **落库 dtype 风险**，建议清单启用与批量启用解耦） |
| **#1** 大表 UPSERT 卡死 | 防御性修复已落码（去 RETURNING + spill 兜底）；**env 取值已固定＝T2 关闭态**（进程空 + 全仓配置零命中）；**沙箱真规模 4-pass 复跑零退化零冻结**（447 万行 × 42 列 × 5 万行/批 × 357 批 = 17.85M 次冲突 upsert：head 5.539s→tail 4.791s，**growth 0.865**；四 pass 均值 5.398/5.680/4.749/4.839s）；**T2 收益实测**：对逐位一致重复写提速 ≈**20 倍**（507.2s→25.0s，−95.1%，4.45M 次重复 upsert；identical 臂；首轮对照因批次改值致 skip 路径未触发已作废公示） | **partial（维持）**——客户现场卡死**未在任何环境复现**（本机同规模 4 遍重写仍稳定 ≈5.2s/批 vs 客户 52s/批后冻结）⇒ 归因收窄至客户现场专有因子（历史全量拉取遗留死元组未回收 / daemon 并发 / 磁盘内存压力）；**引擎侧修复非根治项**，客户侧运维五条见回执 §1.4；关闭判据不变（客户现场 env 值 + 「2018 起」复跑过 357 批不冻结） |

> 诚实性铁律声明（更新）：**#8 已 verified**（生产同源通道实测三一致成立）；**#1 维持 partial**——客户现场读数未到，不以沙箱零退化冒充生产验证；`QD-2026-0013` 条目 `status=open` 不变（不冒充 closed）。
> 复现固件（仓内）：`.specify/bugs/firefly-engine-defects-20261025/_bench_b1_upsert.py`（真规模 4-pass + identical 双臂）· `_cmp_export_vs_fetchpage.py`（三一致对拍）· `_probe_local_db.py`（本地库只读探针）。

## 七、12-客户运维域 · 案例登记

| 登记项 | 内容 |
|---|---|
| 案号 | firefly-20261025-defect-batch（11 项 + 附录 2 项） |
| 客户 / 归属线 | firefly ｜ 客户运维（由项目缺陷修复会话节点⑰承接实施与证据） |
| 现象 | ①空库首跑 qfq 周期必 held（水位冻结）②ETF 周期被 STOCK 单元毒化 gate ③held 候选水位被作废 ④skip 单元留 in_progress ⑤小样本拒批判负 ⑥大表重叠 upsert 冻结（447 万行/8.5h）⑦水位可被 full_range 小窗写回 ⑧audit 默认误判负 ⑨大表 passthrough 只能整表拉 ⑩freqs 配置名不副实 ⑪#5 客户版本已修（invalid） |
| 根因 | 编排器控制流缺陷族（#2/#3/#4/#6 同案四型）+ 平台执行模型事实（#1 族 + 附录 A）＋ 阈值/审计口径设计缺口（#10/#11）＋ 文档与命名缺口（#9） |
| 修复落点 | `quantstudio/pipeline/qfq_resident_orchestrator.py`（#2#3#4#6）· `writers.py`（#1#7）· `mcp_adapter.py`（#8）· `qfq_orchestrator_types.py`（#9）· `quality_audit.py`（#10）· `daemon.py`（#11） |
| 证据 | 本件 §二~§六 · `.specify/bugs/firefly-engine-defects-20261025/{assessment,fix,test,fix-batch2,test-batch2}.md` · `tests/test_firefly_defects_batch1.py`（13 例）· `tests/test_firefly_defects_batch2.py`（14 例）· `knowledge/qmt/QD-2026-0012.md` · `knowledge/qmt/QD-2026-0013.md` |
| 复现固件 | `tests/test_firefly_defects_batch1.py`（红→绿 7 败→201/201）· `tests/test_firefly_defects_batch2.py`（红→绿 6 败→203/203） |
| 知识沉淀 | `QD-2026-0012`（qfq 编排器控制流缺陷族·engine/D3）· `QD-2026-0013`（duckdb 1.4.x 平台事实·engine/D4）——两条 `gate_rule` 已写明回归门与升级/回收纪律 |
| 状态 | **代码侧闭环**（两批修复 + R4 沉淀 + 三件 docs，五笔提交已全部进 origin/main）｜ **客户侧未闭**（#1 待现场读数，partial；**#8 已 verified**）｜ 通知件 `N-20261027-01` 已按回访读数修订，出稿候用户审定下发 ｜ 回访回执 `docs/evidence/firefly-visit-readout-20261030.md` |

## 八、全案关闭条件声明

**本案**在下列五项全部达成前**不得宣布闭环**：

1. **推送出仓**：~~`f515263`（R4 沉淀笔）随统推 #8 推至 origin/main~~ → **已达成**（2026-10-31 核实：我方五笔 `5655fb4`/`2707414`/`f515263`/`62196c3`/`76e9e50` 均已在 origin/main 等 4 个远端引用内；双远程一致性由出仓窗口核对）；
2. **客户终验①（#1）**：取得**现场** `QS_UPSERT_SKIP_IDENTICAL` 实际取值 + 2018 起全量重拉复跑过 357 批不冻结的读数 ⇒ #1 由 partial 升 verified（我方侧已就绪：env 口径固定＝T2 关闭态、沙箱 4-pass 零退化、T2 ≈20 倍量化、客户侧处置五条已写入通知件 §4.2）；
3. **客户终验②（#8）**：~~取得 export vs fetch_page 同窗三一致读数~~ → **已达成**（2026-10-30 生产同源 MCP 通道实测：列 15/15+列序一致 · 行 8,673=8,673 · 键集合全等 · 逐值零差异 · sha256 一致 ⇒ **#8 已 verified**；遗留：export `trade_date` 为 DATE 的落库 dtype 风险 → 清单启用与批量启用解耦 + 更优解候选「引擎侧透传服务端 filters」待裁）；
4. **客户通知下发**：`N-20261027-01`《修复完成通知》经用户审定后由用户转发下发（不可逆动作保留用户确认闸门；本节点只出稿，不代下发）——**通知件已按回访读数修订**（终验②销号 + 终验①补处置三条 + 口径说明）；
5. **#5 客户侧澄清**：客户确认 GUI 运行构建版本（≥`606e688` 即含 FP-1/FP-2 数据止点列）；若仍缺列，按旧构建/旧进程排查（客户侧动作，我方只出排查判据）。

以上第 2/5 项属客户侧动作，本节点**只出判据与核验清单，不代客户执行、不代客户判终验**；第 4 项保留用户确认闸门。任一项未达成时，本案状态维持「代码侧闭环 · 客户侧未闭」，禁止以单测或单方声明升级结论。

## 九、留痕与纪律

- **R1（开工留痕）**：知识反哺：[无命中] 检索面＝主 `knowledge/**`（qd/pd 两族 + iron-rules），关键词 GREATEST/倒退/export_dataset/fetch_page/max_reject/quality_gate/EpsBackfill/水位回退，2026-10-26。
- **R4（验收沉淀）**：`本轮沉淀：QD-2026-0012 + QD-2026-0013`（两批 fix.md 的 R4-N/A 合并补齐；条目过 `kb_hook.validate_record` 按族校验）。
- **铁律 03/10**：写前快照（stash store 持久化不 drop）+ 精确清单 add + staged 逐项核对 + 提交后残留检查 + 共享工作树他线改动零触碰。
- **铁律 12（六步）**：归因→派单→反馈审核→闭环→客户通知（本件 §七/§八）→案例归档（本节）——**通知下发与案例归档终态待用户审定后闭合**。
- **铁律 18**：修复前置纯增益审计逐项判型（§五），无「数据修正型」——本次零数据口径变更、零回测结果影响。
- **出仓批次订正（2026-10-31）**：`111552d`（回访读数落仓内可核位笔）提交信息内写的「随 **#14** 出仓」系笔误，**实为随 #13 终池出仓**（总调度 2026-10-31 订正）。该笔**未 amend**——其上已叠他线提交（`0e50c81` / `fa4a57e`）且该笔已推送（共享工作树禁止历史手术，铁律 03），故以本行留痕订正；总调度台账同记。
