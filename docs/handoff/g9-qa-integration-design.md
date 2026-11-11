# 方案：G9 分解段并入 QA 常规轮（乙案 · 常态承载）

- 依据：节点2 2026-11-11 裁「②G9 裁＝丙先行＋乙承载……乙＝常态承载纳入 QA 常规轮（分解段并入 quality_audit 轮·**方案随批出**，不再人工找窗）」
- 关联：`DB-2026-0042`（测量/归因类判据族）；`docs/evidence/g9-split-query-20261111.md`（本日三联查读数）；`scripts/g9_cause_distribution.py`（丙案脚本，已端到端验证）
- 知识反哺：`知识反哺：[命中] DB-2026-0042（db/D5/DB-2026-0042.md，2026-11-11）——采用其 L3「测量条件不可比」与「快照时刻重算对照（差值<1%=真信号）」作为本方案的判据设计原则`；`知识反哺：[无命中] knowledge/db（D1–D5）其余条目 + knowledge/iron-rules（主题：QA 轮内嵌只读诊断段 / 判据时间基准）`

---

## 一、背景与动机

**问题（生产事实，非推测）**：G9 两条判据在生产上恒红——

| 判据 | 读数（2026-11-10 14:29 副本） | 阈值（实读 `quality_audit.py:731-733`） |
|---|---|---|
| `QfqDeadLetter` | **18,713** | `dead_letter_max` = **0** |
| `QfqPendingSla` | **≈45,945** | `pending_sla_hours` = **72** |
| `QfqStaleInProgress` | 0 | `stale_in_progress_hours` = 24 |

**但 G9 无法人工复跑**：生产库被 `quantstudio.pipeline.daemon`（`--mode forever`）**独占持有**——实测
`duckdb.connect(read_only=True)` 抛 `IOException: ... 另一个程序正在使用此文件 / File is already open in python.exe (PID 36372)`。
⇒ 「轮次任务间有间隙窗」前提**不成立**：**轮次间隙 ≠ 锁释放**（DuckDB 单写者排他）。
⇒ 该 SQL 已多轮顺延，本轮改在副本执行（副本已于 2026-11-11 10:30 清理，释放 74.42 GB）。

**动机**：把 G9 分解段**并入 QA 常规轮**——QA 本身跑在 daemon 进程内（**天然持锁**），
无需人工找窗、无需夜间拷贝副本，且读数与轮次同刻，不再有「快照 vs 实时」时间基准问题。

## 二、范围与边界

| 项 | 内容 |
|---|---|
| **改动文件** | `quantstudio/pipeline/quality_audit.py`（新增一个「分解段」输出，**不改三判据既有判定逻辑与阈值**）；`scripts/g9_cause_distribution.py`（**已存在**，改为可被 QA 段调用的纯函数，CLI 保留） |
| **不改动** | 三判据的 SQL 语义、阈值默认值（`dead_letter_max=0` / `pending_sla_hours=72` / `stale_in_progress_hours=24`）、报告结构既有字段、任何写入路径 |
| **兼容边界** | 分解段**只增不减**：既有 `report` 结构与 verdict 逻辑零变化；分解结果落**独立字段**（如 `report["qfq_trigger_cause_dist"]`），下游若不读该字段则完全无感 |
| **不改动范围（明确排除）** | 不清理 `dead_letter`、不改 `pending` 状态、不排水——那是甲案，候丙结果另裁 |
| **范围缩放** | 无（分解段只读、聚合查询，不遍历表全量行） |

## 三、任务拆解 + 功能点分解表

| # | 功能点 | 验收判据（可机检/可复跑/负向对照） |
|---|---|---|
| **F1** | `g9_cause_distribution.py` 抽出纯函数 `compute_cause_distribution(con, sla_hours, stale_hours) -> dict`（CLI 复用同一函数，单一内核） | **A1** CLI 与函数调用对同一库产出 **同一 dict**（`json.dumps(sort_keys=True)` 逐字相同） |
| **F2** | `quality_audit` 的 QFQ 段（`:733` 附近）在既有三判据**之后**追加调用该函数，结果落 `report["qfq_trigger_cause_dist"]` | **A2** QA 报告新增该键且结构 = 函数返回；既有键（`checks`/`verdict`/各判据值）**逐位不变** |
| **F3** | 分解段受开关控制，**默认关**（`QS_QA_CAUSE_DIST=1` 才跑） | **A3** 未置位时报告**不含**该键（负向对照）；置位时含 |
| **F4** | 分解段异常不得影响 QA 主流程 | **A4** 注入一个会抛异常的假函数 ⇒ QA 仍正常产出报告且 `verdict` 不变，异常记入 `report["qfq_trigger_cause_dist_error"]` |
| **F5** | 双时间基准内建 | **A5** 返回 dict 必含 `sla_now`/`sla_snapshot`/`sla_verdict` 三键；三档判定（无超期/单侧为0/真信号/可疑）各有单测 |
| **F6** | 既有测试零回退 | **A6** QA 相关既有测试全绿（当前基线：`test_qfq_observation_incremental.py` 21 + `test_qfq_g5_effective_dates_batch.py` 6 = 27 passed） |

## 四、风险描述

| 风险 | 等级 | 规避手段 |
|---|---|---|
| 分解段拖慢 QA 轮（多组 GROUP BY） | **中** | ①默认关（F3）；②每组 `GROUP BY` 均为聚合、无全表扫；③实测耗时写入门槛——若开启后 QA 轮耗时增幅 >10%，改回默认关并在方案补性能数据 |
| 分解段异常中断 QA 轮 | 中 | F4 硬门：try/except 包裹，异常只落 `*_error` 键 |
| 与既有 `checks` 结构冲突 | 低 | 独立键、不改既有键（A2 逐位比对） |
| 阈值默认值被误改 | 低 | 方案明确「不改阈值」；A2 校验既有键逐位不变 |
| 下游误读新键 | 低 | 键名带 `qfq_trigger_cause_dist` 前缀，且默认关 ⇒ 不置位时不存在 |

## 五、验收要点

- **功能验收**：A1（单一内核逐字一致）+ A2（既有键逐位不变）
- **场景验收**：A3（默认关闭的负向对照）+ A5（双时间基准三档）
- **边界验收**：A4（异常不影响主流程）+ A6（既有 27 passed 全绿）
- **生产验证**：daemon 下一轮 `--mode once` 或自然轮次中置 `QS_QA_CAUSE_DIST=1` 跑一次，确认报告含分解段且 QA 总耗时增幅可接受（记录实测）

## 六、质量判据

1. **纯增益型**（铁律 18，新增能力型）：默认关 ⇒ 行为零变化（A3 负向对照）；开启只增字段（A2 逐位不变）；
2. **防回归**：A2 + A3 + A4 + A6 四门；A1 保证单一内核（避免 CLI 与 QA 段两套实现分叉，符合「单一内核+双薄壳」）；
3. **可复跑**：全部判据可用临时 DuckDB 库（同 schema + 假数据）复现——丙脚本已用该法端到端验证过一轮（28 列/192 行/exit=0/JSON 27 键）；
4. **不夸大**：性能增幅以 QA 轮实测为准，不预估；若超 10% 则默认关并如实记录。

---

## 七、与丙案的关系（执行顺序）

```
今晚 A4/M1 停窗（生产静默窗）
  └─ 丙：跑 scripts/g9_cause_distribution.py --json-out docs/evidence/g9-cause-YYYYMMDD.json
         （已端到端验证，可直接执行；全只读）
        └─ 出「45,945 条按月/按码族/按 trigger_type/last_error」分布
              ├─ 若分布显示「集中成因」⇒ 立甲案（排水/归档）方案
              └─ 若分布显示「长期零散积压」⇒ 仅调阈值叙事，不排水
明晚 settle 乙 → R-05 流入复测 → 仍积压再立甲案
并行：本方案（乙案）走六步流水线 ①方案 →②审 →③实施 →④验收 →⑤确认 →⑥推送
```

## 待审提示

- 本方案仅到「六步流水线①方案」，实施须经②独立审核。
- F3 的开关默认关是**方案自定**（保守），若审核方认为应默认开以便常态观测，请指明（届时 A3 需反向改写）。
