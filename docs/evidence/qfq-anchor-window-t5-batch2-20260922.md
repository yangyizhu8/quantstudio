# 错误一批二验收（T5 fallback fail-safe）· 2026-09-22

- 拆批依据：批一（T1/T2）已独立成笔 `6b472ce`；本笔 T5 是同一误报链的**写入侧根除**
  （批次内回退锚 = 2026-08-14 实测 1442 万行 front=raw 的同机理残留——当年修复只把它
  从主路径降级到回退分支，机理未除；9-06 起 16 次 / 713 行经此路径写入错值）。
- **退役件说明**：合并稿 `qfq-anchor-window-t1t2t3t5-acceptance-20260922.md`（未跟踪）
  已退役——其 T1/T2 内容由批一件 `qfq-anchor-window-t1t2-batch1-20260922.md` 承载，
  本件承载 T5 与 G 系排表，避免同源经验重复登记。

## 一、T5 实现（`aligner.py` `_apply_qfq`）

| 项 | 内容 |
|---|---|
| 删除 | 「快照缺 code → 回退批次内 `groupby().last()` 锚」整块（原 L963-974：anchors 计算 + 两次 `fillna` + warning） |
| 改为 | `_miss = _adj_latest.isna() \| _adj_earliest.isna()` → 该行 **front/back 置 NULL**（NaN 自然传播）+ 计数 WARNING（含 codes 样例，`fallback_disabled` 语义） |
| 不变 | 无快照整批 `raise ValueError` fail-fast（2026-08-14 防线原样）；`raw` 列保留原值（观测面不清零）；主快照路径逐字节不动 |

**行为变更披露（如实）**：缺锚行的前/后复权价由「错值（批次内锚）」变为「NULL」——下游走
既有 NULL 语义（因子缺失日不复权价缺失是既有行为）；代价是缺锚行 front 不可用，
**正确处置是补因子而非写错值**，与 `qfq_invariant` 模块「宁可少算不可错算」取向一致。

## 二、验收（实测）

| # | 证据 | 值 |
|---|---|---|
| 1 | 新用例 `test_t5_missing_code_in_snapshot_nulls_not_batch_anchor` | front/back 全 NULL + `raw` 逐值保留 + 快照非空（map 有 OTHER 无 X） |
| 2 | 既有兼容（零修改） | `test_without_snapshot_raises_fail_fast`（整批 raise）✓、`test_bug_scenario_front_equals_raw_without_snapshot`（8-14 防线）✓、`test_with_global_snapshot_front_uses_global_anchor`✓ |
| 3 | 全套（工作区态，含批二） | **122 passed / 0 failed**（12 文件） |
| 4 | **worktree 仓库态自证（同批一方法）** | 一次性检出批二 commit 跑 6 套件 → 见提交回报（预期全绿） |
| 5 | 叠加纪律 | aligner.py diff 全在 T5 区（+13/−13），他线零卷入 |

## 三、G 系判据观察排表（两窗，各自出对账单，不等齐）

判据全集 G1-G7 定义见批一件 `qfq-anchor-window-t1t2-batch1-20260922.md` §四。

| 窗 | 触发时机 | 本窗对账项 | 产出 |
|---|---|---|---|
| **窗 A（本机换代际首窗）** | daemon 换代际（lint 自然重启带出首轮，窗口流程照走） | G1 脏码 skip 计数、G2 冷启动零误触、G3 orch 连续 ≥3 周期无崩、G4 revision_alert 非空、**G5 单批误报 CRITICAL=0**、**G6 selfcheck_log 新列有值**、G7 skip 硬计数 | 窗 A 对账单（逐项 PASS/FAIL + 原始日志/查询摘录） |
| **窗 B（客户机拉取后首窗）** | 客户拉取 `fa8e64f`+本批/批一后代际首运行窗（客户回报日志时） | 按 **G1/G2/G3/G5/G6** 逐项对账（G4/G7 视客户日志可得性） | 窗 B 对账单（同上格式） |

**两窗独立**：窗 A 不待窗 B，窗 B 不待窗 A；各自出单、各自呈报。
**注**：G4（revision_alert 非空）与 G1/G2/G3 同属错误二生效面，但 G4 同时是错误一 T3
（范围收敛：outbox 机制原已存在，双空表系 orch 崩溃之果）的解锁自证——窗 A 若见 G4>0，
即错误一 T3 无需新增代码的实证。

## 四、后续节奏

- **错误二 T3（audit-only passthrough 过滤）**：按方案节奏排在批二之后；其 **audit-only 轮
  可与 G 系观察窗并行**（只计数不拦截，互不阻塞）；enforce 待 audit-only 对账（非契约行数
  == 已知脏行数）通过后另呈。
- **T4 排水**：依赖错误二在客户/本机上线运行后自证，随窗 A/B 的 G 系结果推进。
- **T6 读窗复验 + 3 code 回测 A/B**：留「计划空档窗」，不动。

## 五、回退

单 commit revert 即可（aligner.py + 测试 + 本件，无交织）。
