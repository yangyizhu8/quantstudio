# 方案④：并发写冲突面（write-write conflict）致整批失败 —— 修复方案

> **状态**：①方案（六步①）待审 ｜ **日期**：2026-10-30 ｜ **发起**：主仓 dev 会话（节点⑤）｜ **优先级**：四案之首（2026-10-30 晨轮复现并致整批 FAILED）
> **关联**：3in1 方案件 `docs/design-3in1-window-and-b-secondhalf-20261027.md`（附节六行取证 + 锁矩阵）｜ 2026-10-22 六步③（A6 有界冲突重试）｜ writers.py L90/L145-L196/L267-L276/L501-L511/L1303-L1320

## 0. 知识反哺检索留痕（R1 · 铁律 21/22）

```
知识反哺：[无命中] 主库 knowledge/**（按 kb-info.yaml families × domain 过滤，检索面=并发/冲突/写锁/事务/instance-cache 五组关键词），
          2026-10-30 —— knowledge/** 命中 0 条（并发冲突域尚无条目库）
知识反哺：[命中] 既有框架实现自述（writers.py 源码注释，非条目库）：L90「同进程并发写曾致 23 次 TransactionContext
          write-write conflict」、L196「冲突时 peers_n=0 ⇒ cross_process_candidate 误报（实弹 10-23 07:34 已证）」、
          L267-L273「正解 = 让『谁在写哪段 key』显式可见（作用域）」、L1303「A6（六步③·2026-10-22）有界冲突重试」，
          2026-10-30
知识反哺：[豁免] 通用库 knowleage_base 对应域 MOC —— 本轮无外部库回写需求，只读面未命中相关 MOC 条目
```

> 结论：**并发冲突域在项目条目库中为零覆盖** ⇒ 本案实施时应**新建该域条目**（R4 沉淀），否则下次同类问题仍需从零取证。

## 1. 问题定义（实测证据，非推断）

### 1.1 生产失败实况（2026-10-30 晨轮）

| 时点 | 事件 | 证据源 |
|---|---|---|
| 08:37:17 | `mcp_stock_minutes` 启动，`INCREMENTAL: 2026-10-17 → 2026-10-29`（窗口覆盖 10-29） | `daemon_stderr_20261029_161653.log` / `daemon.log` |
| 08:43:44 → 08:59:43 | `DuckDBWriter] stock_minutes batch=…` 连续 3 次 **WARNING** | 同上 |
| 09:00:34 | **❌ FAILED[streaming]**：`TransactionContext Error: Failed to commit: write-write conflict on key: "000895, 1792373400000, 1min"`；`写失败判别特征: table=stock_minutes`；`所有候选源均失败: ['mcp']` | 同上 |
| 全日 | `write-write conflict` 共 **5 条** | stderr 全文扫描 |

**后果**：该批整批未落库 ⇒ **10-29 分钟数据未入库**（库内直查待停机窗确认，见 §6）。

### 1.2 同日另一失败点（**不同因，据实更正**）

| 时点 | 事件 | 定性 |
|---|---|---|
| 06:29:58 | `mcp_llm_text_events_enriched_pt` **PASSTHROUGH FAILED: create_export_job …** | **云端导出作业失败**（F-2 宽文本 Parquet 路径），**非并发写**；与 09:00:34 不同因，另案跟踪 |

> **更正记录**：2026-10-30 R1 上报曾把此点归为「跨任务并发写面第二点」⇒ **误判，已更正**（判型纪律：证据未到即改判）。

### 1.3 机制层既有事实（3in1 方案件 + 源码）

| # | 事实 | 出处 |
|---|---|---|
| F1 | **同实例事务交错**（非文件锁）：同路径双 `duckdb.connect` ⇒ 同一 `DatabaseInstance` ⇒ 跨连接可见 ⇒ tuple 级 MVCC 提交期冲突 | instance cache 实测（3in1 附节） |
| F2 | **锁覆盖极低**：74 启用表仅 **2 张**有锁（2.7%）；daemon 实写 14 张中 **12 张无锁** | 锁矩阵（3in1 附节） |
| F3 | `stock_minutes`/`etf_minutes` ∈ `_SERIAL_WRITE_TABLES` ⇒ 走真 RLock；**但 RLock 只护同线程嵌套，不护跨线程/跨连接事务交错** | `writers.py:95` + `table_write_lock`（`threading.RLock`，可重入） |
| F4 | **冲突报告只给 key（code,time,freq）不给表名** ⇒ 对端表名永久丢失 ⇒ `peers_n=0` ⇒ `cross_process_candidate` **误报**（源码 L196 自述已证） | `writers.py:145-163,196` |
| F5 | **观测面三层盲区**：`write_scope`（表级价格表统计）/ `price_write_enter`（单元级）**均不含实例级冲突面** ⇒ `entered=120/conflicts=0` 与冲突并存不矛盾（维度错配） | 3in1 附节六行定谳 |
| F6 | **既有 A6 有界冲突重试**（六步③·2026-10-22）已上线，但今日仍「3 次 WARNING 后整批 FAILED」 | `writers.py:1303-1320` |

### 1.4 归因判定（当前置信度：**中**，两项待实施前补验）

| 候选 | 内容 | 支撑 | 反证/缺口 |
|---|---|---|---|
| **C1 主因** | **同进程多写者对 `stock_minutes` 的 tuple 级 MVCC 交错**：daemon 轻量调度循环并发跑多个任务（今日 75 任务启动），任一任务与 `mcp_stock_minutes` 批写同表同 key 段 ⇒ 提交期冲突；**RLock 只串行化同表写操作，不串行化跨连接长事务** | 冲突 key 恒为 stock_minutes 主键；同日 5 条同族；F1/F3 | **待补验**：为何 `_SCOPE_STATS.conflicts` 与 `entered` 不反映该冲突（F5）；重试 3 次为何仍耗尽 |
| C2 次因 | **作用域等待未覆盖 streaming 写路径**：`price_scope_conflicts` / `wait_scope_release` 等待机制存在（L347/L387），但 streaming 批写路径可能未登记作用域 ⇒ 等不到释放 | L267-L273「正解=让谁在写哪段 key 显式可见」暗示机制未闭合 | 待补验：streaming 路径是否调用 `price_write_enter` |
| C3 排除项 | **跨进程写者**（如 GUI / 回填工具） | 冲突发生在 09:00:34，当时仅 daemon 在跑（我方回填在 16:16 后，GUI 早退） | `cross_process_candidate` 误报已被源码自述确认（F4）⇒ **不可据该字段判跨进程** |

## 2. 改动范围（框架层，三改点 · 纯增益）

| # | 改动 | 落点 | 性质 |
|---|---|---|---|
| **M1** | **冲突报告补全表名与对端**：冲突异常解析时携带 **本表名 + 写入作用域（表/日期段/任务名/batch_id）**，写入 `conflict_key` 同级的 `conflict_table`/`conflict_scope` 字段 ⇒ 修 F4（表名永久丢失） | `writers.py` 冲突解析段（L145-L163） | 纯增益（只增诊断字段，不改判定与重试行为） |
| **M2** | **作用域登记覆盖 streaming 批写路径**：使 `_SCOPE_STATS` 能观测到 minute 表的批写段 ⇒ 修 F5 观测盲区 + 为 C2 提供判据 | `writers.py` streaming/批写路径 + `price_write_enter` 覆盖面 | 纯增益（观测面扩展 ⇒ 新增能力开始报告真相，判型=新增检测型） |
| **M3** | **有界重试策略加固**（依 M2 提供的真实冲突面决定）：冲突时若对端作用域**已释放** ⇒ 提高重试上限/加退避；若对端作用域**仍在** ⇒ 改为**作用域等待**而非盲重试 | `writers.py` L1303-L1320 重试段 | 待 M2 数据定案后二选一（不预设） |

> **不扩大范围**：不改 `_SERIAL_WRITE_TABLES` 覆盖面（扩大锁覆盖属「stock 面框架层写路径治理」另案，见 3in1 方案件挂起项）；不改 DuckDB 事务模型；不改表结构（铁律 24：加列=契约变更，本案零 DDL）。

## 3. 影响面

| 面 | 影响 |
|---|---|
| **既有功能** | 无行为变更（M1/M2 只增诊断与观测；M3 仅在冲突分支生效） |
| **性能** | M2 增加一次作用域登记（量级：每批 1 次，非逐行）；M3 仅冲突时生效 |
| **精度** | 零影响（不触数据口径） |
| **回测** | 不触回测引擎；`tests/` 需补并发用例（见 §4） |
| **他线** | 副本 `QuantStudio-trading` 共享层同步门**不豁免**（铁律 14） |

## 4. 验收标准（可机检）

| # | 验收项 | 判据 |
|---|---|---|
| V1 | **冲突可定位** | 构造并发写 `stock_minutes` ⇒ 异常/日志含 **表名 + 作用域**（不再只有 key） |
| V2 | **观测面闭合** | 并发窗口内 `_SCOPE_STATS.conflicts` 与实际冲突数**同量级**（不再 0）⇒ 修 F5 |
| V3 | **不误判跨进程** | 同进程冲突时 `cross_process_candidate` **不再恒 True** ⇒ 修 F4 |
| V4 | **整批不失败** | 复现用例（并行两写者同 key 段）⇒ 重试/等待后**写入成功**（不再 3/3 耗尽） |
| V5 | **回归零衰减** | `writers.py` / `daemon_lifecycle` / `qfq` 相关既有测试全绿；黄金结果逐位一致 |
| V6 | **生产复核** | 修复后 ≥1 个真实晨轮：**stock_minutes 批写 0 次 FAILED** 且 10-29 类增量批落库成功 |
| V7 | **知识沉淀** | 新建并发冲突域条目（`kb_hook.validate_record` 通过）⇒ R4 落 `PD-2026-10NNN` |

## 5. 回退条件

| 触发 | 动作 |
|---|---|
| M2 观测开销超阈（登记耗时 > 批写耗时 1%） | 回退 M2（仅保留 M1 诊断字段） |
| M3 加固后出现**新的**失败形态（重试/等待未命中） | 关闭 M3 分支，退回 A6 原行为（纯增益约束：不改既有判定） |
| V5 任一既有测试红 | 立即回退该改点（按路径单文件回退，禁批量） |
| 生产出现写入延迟劣化（晨轮总时长 +10%） | 回退 M3，M1/M2 保留（诊断面无性能代价） |
| 窗口冲突 | 与升级线 11-07 窗口**互斥** ⇒ 本案实施避开该窗 |

## 6. 待实施前补验项（缺口清单，诚实登记）

| # | 待补验 | 方法 | 用途 |
|---|---|---|---|
| G1 | **冲突对端是谁**（哪个任务/哪张表） | 复现用例 + M1 落地后的日志 | 确认 C1/C2，替代 `peers_n=0` 猜测 |
| G2 | **重试 3 次的来源与退避** | 读 `writers.py:1303-1320` 完整实现 + 复现观测 | 定 M3 分支 |
| G3 | **streaming 路径是否登记作用域** | 读 streaming 批写调用链 | 定 M2 落点 |
| G4 | **10-29 是否真未入库**（库内直查） | 下次停机窗内直查 `stock_minutes` 末日 | 闭环 §1.1 后果（当前为日志铁证，非直查） |

## 7. 判型声明（铁律 18 三型）

- **M1**：**纯恢复型**（恢复「冲突可定位」本应具备的诊断能力，不改任何业务行为）
- **M2**：**新增检测型**（观测面扩展 ⇒ verdict 可被新证据改变；按铁律 18 新增能力开始报告真相，须在方案阶段单列，不得与纯恢复型混办 ⇒ 本案已单列 §2-M2 与 §4-V2）
- **M3**：**待 G1/G2 定案后判**（若仅调重试参数 ⇒ 纯恢复型；若改为作用域等待 ⇒ 涉及调度语义，需再走判型）

### 7.1 判型更正（用户裁定 B · 2026-10-30）

> 原判 M2 = 「新增检测型」；**经②审裁定更正为「缺陷修复型」**。

**更正理由**：M2 使既有 B3/B4 写前让行**能看见任务批写作用域**，后到任务可让行等待（≤120s，超时 defer 不判失败）——**该行为变化即修复本体**，因改动前是**缺陷态**：

```
改动前（缺陷态）：盲写 → write-write conflict → A6 有界重试 3/3 耗尽 → 整批 FAILED
                （实弹：2026-10-30 09:00:34 mcp_stock_minutes 整批失败）
改动后（修复态）：作用域可见 → 后到任务让行（≤120s）→ 写成功
```

⇒ M2 **不属「新增能力开始报告真相」**，而属**把缺陷态修复到正确行为** ⇒ 判型更正为**缺陷修复型**。
**连带**：`QS_PRICE_WRITE_SCOPE=0` 为**回退阀**，不另设批写面独立开关；⑤呈批须明示本更正。

### 7.2 裁定回写（B / C · 2026-10-30）

| 裁定 | 内容 | 落点 |
|---|---|---|
| **B** | M2 **保留**；判型更正「缺陷修复型」；`QS_PRICE_WRITE_SCOPE=0` 为回退阀不另设开关；⑤呈批明示 | §7.1 + ⑤呈批件 |
| **C** | R4 族归属照准 **`DB-2026-0004`**（`knowledge/db/` 2026-10-29 立族，`fix_layer` 含 `write_path`）；原「新建并发冲突域 PD-…」表述**作废**（记为「写方案前未查实 KB 族实态」教训） | §9.5 + `knowledge/db/` 待机检落盘 |


## 8. 落点与时序

- 本案属**框架层内核组件（写入路径）改动** ⇒ 铁律 01 六步流水线适用：①方案（本件）→②审（ZCode/用户）→③实施→④验收（§4 七项）→⑤用户确认→⑥双仓推送
- ②审通过后方可实施；**M3 不得先于 M2 落地**（否则等于盲重试）

## 9. ③实施留痕（2026-10-30 · 第一批 M1+M2）

### 9.1 实际改动落点（全部纯新增，`git diff --stat` = writers.py +99/-0）

| 改点 | 文件:行号（实施后） | 内容 |
|---|---|---|
| M1-辅助 | `quantstudio/pipeline/writers.py:127-131` | `_fmt_ms()`：毫秒 epoch→可读时间（失败回退原值） |
| M1-辅助 | `writers.py:134-163` | `_describe_write_scope(info)`：作用域描述拼装（table/batch/path/task/tseg/conflict_bar，缺失省略不填占位；tseg 取本线程 A1 登记条目） |
| M1-主落点 | `writers.py:200-219`（`record_write_failure` 内，peers 指认之后） | 冲突族且表名可得 ⇒ 增 `conflict_table`+`conflict_scope` 字段 + 一行 `logger.warning` 诊断（含 peers_n/cross_process_candidate）；整个新增段自带 try/except 兜底 |
| M2-辅助 | `writers.py:504-540` | `_register_batch_write_scope(table, path, batch_id, df)`：域限定 `_SERIAL_WRITE_TABLES` + `QS_PRICE_WRITE_SCOPE=0` 逐位回退；写前 `price_scope_conflicts` 仅计数（不等待不 defer），随后 `price_scope_enter` 登记（scope_id=`batch:<table>:<batch>:<uuid8>`） |
| M2-接线 | `writers.py:1519,1522-1527`（`_write_locked` 写临界区） | `price_write_enter` 之后补作用域登记 |
| M2-接线 | `writers.py:1657-1658`（`_write_locked` finally） | `price_scope_exit` 先于 `price_write_exit`（finally 必达，写毕即撤） |
| 单测 | `tests/test_minutes_conflict_m1m2.py`（新文件，13 用例） | M1×7（V1）+ M2×6（V2），见 §9.3 |

未动：`is_write_conflict`（L557 一带）、A6 重试段（write L1359-1382 一带）、`_SERIAL_WRITE_TABLES` 覆盖面、`_write_passthrough`/`_write_passthrough_chunked`（passthrough 表无分钟表，经查不承载）、daemon.py / qfq_reanchor_engine.py 零改动、零 DDL。

### 9.2 改动前后行为对照

| 面 | 改动前 | 改动后 |
|---|---|---|
| 冲突产出 | 仅 `conflict_key`/`conflict_time`（表名在 ctx 有但不进冲突段、作用域/写时段永久丢失） | 增 `conflict_table`（本表名）+ `conflict_scope`（表/批/path/task/写时段/冲突 bar 时刻）+ 冲突期一行 warning 日志 |
| `peers_n=0 ⇒ cross_process_candidate` | 恒 True（误报，判定未动） | **判定仍不动**（M3/另案）；但 scope 带 batch/path/tseg ⇒ 同进程并行可由人判读（`test_m1_same_process_vs_cross_process_human_judgeable`） |
| `_SCOPE_STATS.entered` | 仅引擎单元（qfq_reanchor L2597）+ 手动登记可见；**任务批写段全盲（F5）** | 分钟表批写（write→_write_locked）每写一段登记一次（每批 1 次，非逐行）；写毕即撤 |
| `_SCOPE_STATS.conflicts` | 仅 daemon 写前检查（B3/B4）对**引擎作用域**计数；批写重叠不可见 ⇒ 恒 0 | 批写进入临界区时对在程作用域（引擎单元等）重叠计数 ⇒ 真实重叠窗口不再恒 0；同进程双批写经表锁串行 ⇒ 0 属「已核实零」 |
| daemon B3/B4 写前让行 | 看不见任务批写作用域（C2：等不到释放） | **既有机制**（2026-10-23 上线，本批零改动）现在能看见任务批写在程作用域 ⇒ 并发窗口内后到任务可让行等待（上限 120s，超时 defer 不判失败）——此为 M2 登记面的**既有设计后果**，回退面同 `QS_PRICE_WRITE_SCOPE=0` |
| 写入语义/锁/事务边界/重试/退出码 | — | **逐位不变**（diff 纯新增；登记/计数均为 dict 级观测操作；锁序 table→conn→registry 无环） |

### 9.3 测试读数（如实申报）

- 新增 `tests/test_minutes_conflict_m1m2.py` 13 用例：M1 7 例（含生产实弹文案形态、缺失省略、非冲突族不产出、task 前向兼容、日志行、人判读面）+ M2 6 例（entered 反映批写段 / 引擎样重叠 conflicts==1 / 无重叠已核实零 / 双线程表锁串行零冲突 / 非分钟表不登记 / 开关=0 逐位回退）。
- **本会话未能执行 pytest**：会话权限层拦截一切 python 执行形态（`python <file>` / `-m pytest` / `-c` / `-V`，主会话与子代理双探测，报错原文均为 `No permission client configured for Bash`；仅 `python --version` 放行）⇒ TDD 红绿循环与 V1/V2/V3 读数**缺失**，已呈用户裁定（补跑命令清单见回执）。静态自检已做：`git diff --check` 干净、13 用例在册、无解释器语法校验（如实声明未跑）。
- 补跑命令（待裁定后）：`python -m pytest tests/test_minutes_conflict_m1m2.py tests/test_minutes_write_conflict.py tests/test_minutes_write_scope.py tests/test_write_integrity_r2_serial.py -q`（V1/V2/V3 前三文件面）+ daemon_lifecycle/qfq 既有套。

### 9.4 G3 补验结论（§6 缺口闭合）

**streaming 批写路径未登记作用域**（证：writers.py 三写路径仅 `price_write_enter`，全仓 `price_scope_enter` 调用仅 qfq_reanchor_engine.py:2597）⇒ M2 取「补登记」分支。G1（对端是谁）/G2（重试 3 次来源=write L1359-1382 已读通：`_write_retry_max` 默认 3、退避 0.5/1/2s+抖动、冲突族才重试、耗尽计入 exhausted）/G4（10-29 是否入库）仍开放，归 M3 前置与停机窗。

### 9.5 本轮沉淀

本轮沉淀：N/A（理由：本会话权限层无法执行 python，`scripts/kb_draft.py` 起草与 `kb_hook.validate_record` 机检不可运行，按铁律 22「机检不过不落盘」暂不落盘；**族裁定**：经查 `knowledge/kb-info.yaml` families，并发写冲突属既有 **db 族**（`knowledge/db/`，fix_layer=`write_path`，2026-10-29 立族）**无需新立族**——呈批件原假设「PD-2026-10NNN」应更正为 **DB-2026-0004**（既有 0001-0003）；条目草案（六必填字段齐）已备于回执⑦，待可执行会话补机检后落盘）
