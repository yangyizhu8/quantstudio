# QFQ 水位门禁判定域收敛 · 处理配额 · 批次可追溯（FP-5/FP-6/FP-7 设计方案·呈批件）

- 日期：2026-10-20
- 上游：firefly「水位线」案 N3 ②审通过 + 三裁（裁-1/裁-2 照准；裁-3 受控窗照准）
- 判型：FP-6 = **数据修正型（行为修正，数据正确性边界变更）**；FP-7 = 节奏变更型；FP-5 = 纯恢复型 + 新增列
- 状态：待 ②审计（ZCode）→ 待 ⑤用户终审（**FP-6 需显著标注后终审**）
- 关联：本案 N0–N4 记录、代码 FP-1~FP-4 已落地（工作树未提交）

## 0. 知识反哺检索留痕

```
知识反哺：[命中] 案例卷宗-CASE-006交付质量案（12-客户运维/案例卷宗/案例卷宗-CASE-006交付质量案.md，2026-10-20）→ 采纳要点：qfq 窗口守卫族问题须落框架层修复、每项配负向对照与复现矩阵
知识反哺：[无命中] 12-客户运维/已知错误（门禁判定域收敛 / applying 处理配额 / 批次可追溯主题），2026-10-20
```

## 1. 问题定义（全部为实测证据）

| 事实 | 证据 |
|---|---|
| 水位自 2026-09-03/09-04 冻结，数据已到 2026-10-16 | `source_watermark` vs `max(time)`；偏差 42–43 天 |
| 门禁判定域为**全表全历史** | `qfq_resident_orchestrator.py` `_qfq_gate`：`orphan in_progress` 全表计数、`dead_letter` 阈值 `dead_letter_max=0`，任一 >0 即 `passed=False` |
| 历史残渣已使水位永久锁死 | 2026-10-20 22:30 实测：`in_progress=48,733`、`dead_letter=11,993`、`retryable_failed=2,477` |
| applying 无配额，单窗不可完成 | 10-17 日志：`已处理 50/4818`（15:27→19:55，≈5.4 min/单元）⇒ 全量 ≈18 天 |
| 悬挂周期已可自愈（FP-3 已生效） | 10-19 10:18:22 回收 10 条 `started` → `interrupted`，`error='reaped: previous process not alive before finalize (FP-3 …)'` |
| 当前仍有 1 条周期悬挂 | `cyc_e9e72441d15e` 自 10-19 10:18:22 起 `applying`，`finished_at IS NULL`（其所属进程已于 10-19 22:34 停止） |

结论：FP-1~FP-4 解决了"看不见""不闭合"，但**水位前进仍被 FP-6/FP-7 两个语义/节奏缺陷锁死**——缺一仍死锁（裁-1 判断成立）。

## 2. 改动范围

### FP-5 批次可追溯（纯恢复型 + 新增列）

- `source_watermark` 增列 `last_run_id VARCHAR`（迁移幂等，默认 NULL，不设 NOT NULL/CHECK）。
- 提交点（`qfq_resident_orchestrator._advance_watermark` 的 dynamic 分支与 `writers.advance_watermark`）同时写 `last_run_id=run_id`。
- `last_batch_id` 语义与格式**保持不变**（历史行为零漂移）；**不新增**任何拒绝性约束（客户报告方案 D 的 CHECK 已否决）。
- 迁移脚本：`scripts/migrate_source_watermark_last_run_id.py`（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，幂等 + 预检 + `--dry-run`）。

### FP-6 门禁判定域收敛（数据修正型，env 双轨可回退）

- `_qfq_gate` 新增判定域参数 `scope`：
  - `cycle`（新默认，经 `QS_QFQ_GATE_SCOPE` 可切）：`orphan in_progress` 与 `dead_letter` 只统计
    **本周期 claim 集合 ∪ 候选水位窗口内的 trigger**；
  - `global`（现状，回退值）：保持现有全表计数语义逐位不变。
- 全表计数改写入 `report["health"] = {"in_progress": n, "dead_letter": n, ...}`，**只告警不参与 passed**
  （GUI「水位状态」列与日志均可读；新增检测型信息，不改变单单元判定）。
- **单单元判定零放宽**：claimed 单元内任一 trigger 非 `committed` → 仍 `passed=False`；`discovery_baseline` 审计不变；`scoped reconcile` 强制 hold 逻辑不变。
- 候选窗口定义：`[old_watermark, candidate_watermark]`（intent 的 `old_watermark`/`candidate_watermark`，与 `defer_watermark` 同源），按各表 trigger 的 `effective_date` 过滤。

### FP-7 applying 处理配额（节奏变更）

- `_claim_and_merge` 增加 `max_units_per_cycle`（默认 **800**，`qfq_orchestrator` 配置块可覆盖，首值建议 500–1000）。
- 超额单元**不 claim**（保持 `pending`/`retryable_failed` 原态，进下轮 `recover_pending_due`），不做"claim 后丢弃"。
- 单元内部流程、`_reanchor_security`、`_apply_trigger_outcome` 与 outcome 判定**零改动**。
- 新增 `summary.units_deferred = len(units) - 已处理` 供观测。

### 不改动（红线）

`_advance_or_defer_watermark` 三分支；`_commit_or_hold_watermarks` 语义；`defer_watermark`；`reap_stale_cycles` 双条件门；`close_cycle_interrupted`；任何策略源码；`source_watermark` 既有列语义。

## 3. 影响面（🔴 显著标注：数据正确性边界变更）

| 项 | 影响 | 说明 |
|---|---|---|
| 水位推进行为 | **会变**：历史 `dead_letter>0` 不再单独阻断提交 | 这是本案的**目的**；提交条件改为"本周期窗口内一致" |
| 数据一致性保证强度 | **收紧空间变窄但有界**：窗口内仍要求全部 committed | 窗口外历史残渣转入 health 指标 + 独立告警 |
| 残余风险 | 若历史 dead_letter 与**候选窗口重叠**，窗口内判定已覆盖（同 trigger 必在窗口内被统计） | 仍存在的盲区：`effective_date` 落在窗口外但影响窗口内复权因子的历史事件——以 health 告警 + 观察期复核缓解 |
| 回测精度/性能 | 无 | 不触及回测链路与数据内容 |
| 节奏 | 每轮处理 ≤800 单元，收敛时间被拉长 | 换取"单窗可 finalize"，这是水位能前进的前提 |

**结论：FP-6 不是纯增益，属"按正确口径改变结果"的数据修正型，须 ⑤ 用户终审后再实施。**

## 4. 验收判据（可机检 / 可复跑 / 负向对照齐备）

| 判据 | 内容 | 形态 |
|---|---|---|
| A20 | 窗口内全 committed + 历史 `dead_letter=100` → `passed=True` 且 `report.health.dead_letter=100` | 正例 |
| A20-N | 窗口内 1 条 trigger 非 committed → `passed=False`（**负向对照，必须修复前红/修复后保持红以外的正确方向**） | 负例 |
| A21 | claimed trigger 非 committed 仍 fail（既有用例复跑，判定不放宽） | 回归 |
| A22 | 4,818 单元 + 配额 800 → 本轮 claim=800，剩余保持 pending，下轮续 claim（幂等） | 正例 |
| A23 | 同一单元在配额开/关下 outcome 完全一致 | 等价性 |
| A24 | 新提交行 `last_run_id` 非空、`last_batch_id` 语义不变；旧行不变 | 正例 |
| A25 | 迁移重复执行无异常（幂等） | 正例 |
| A26 | qfq 测试套件 + daemon 集成 + GUI 相关全绿（含本案已建 34 例） | 回归门 |
| A27 | 受控窗内跑一轮 `once`，gate report 与提交行数逐项归因 | 生产取证 |
| A28 | `QS_QFQ_GATE_SCOPE=global` 时行为与现状逐位一致 | 回退门 |

## 5. 回退条件

1. 任一 A 判据红 → 停在该点，回退该 FP（FP-6 立即置 `QS_QFQ_GATE_SCOPE=global`）。
2. FP-5 迁移异常 → 用迁移前快照还原库（写前快照纪律）。
3. 受控窗内发现窗口外不一致被提交 → 置 global + 冻结水位 + 回滚该周期提交（人工核对 `qfq_watermark_intent`）。

## 6. 实施顺序与窗口

1. **窗口声明**：停服窗纪律全套（占用声明 + 窗口结束时刻守护 + 清场核对）。
2. FP-5 迁移（dry-run → 实跑 → 幂等复跑）。
3. FP-7 配额（dry-run 计数 → 开跑）。
4. FP-6 gate 域（最后上线，带 env 开关；先 `global` 跑一轮取证 A28，再切 `cycle`）。
5. 受控窗内 `once` 一轮取证 A27；随后恢复常驻。
6. ⑤ 呈用户终审（含本表全部判据结果）→ 双仓库推送。

## 7. 预计时点（供总调度排程）

| 阶段 | 预计 | 依赖 |
|---|---|---|
| 呈批件出件 | **已出（2026-10-20 22:40）** | — |
| ② ZCode 审计 | 30–45 min | 本文档 |
| 实施 + 验收（A20–A26、A28） | 90–120 min | 审计通过 + 受控窗 |
| 受控窗 once 生产取证（A27） | 40–60 min（含 800 单元 apply） | 窗口 |
| ⑤ 用户终审 | 待您批 | 上述全绿 |

## 8. 受控窗执行记录（2026-10-20 23:38 → 2026-10-21 02:30）

### 8.1 FP-6 / FP-7：实施 + 验收完成 ✅

- `_qfq_gate` 增加 `scope`（`QS_QFQ_GATE_SCOPE=cycle|global`）+ `report["health"]`；
  `_claim_and_merge` 增加 `max_units_per_cycle` 配额（默认 **60**，见 8.3 判据修订）。
- 新增 `tests/test_qfq_gate_scope.py`（A20 / A20-N / A20-window / A28 / A22 / A23 / A24 / A25）**9 passed**；
  本批四文件合计 **43 passed**；qfq+daemon+GUI 回归 **137 passed**（1 deselected＝预存在失败，
  HEAD 基线同样红，见 N4 证据）。

### 8.2 FP-5：⚠ 执行中被否决并已回滚（**契约列集严格比对**）

| 步骤 | 结果 |
|---|---|
| `--dry-run` | 通过（只探测） |
| 实跑 `ALTER ... ADD COLUMN last_run_id VARCHAR` | 列生成、24 行不变 |
| 幂等复跑 | 通过（跳过） |
| **`--mode once` 启动** | **被拒**：`主库处于 partial_or_mixed 状态，禁止 writer init` |
| `detect_schema_status()` 实测 | `ALTER` 后 = `partial_or_mixed`；`DROP COLUMN last_run_id` 后 = **`complete_2_1`** |

**结论（已定谳）**：`source_watermark` 契约按**列集严格比对**（多一列即判 partial），
因此 `last_run_id` 不是"普通加列"，而是 **2.1 → 2.2 schema 契约变更**；
必须与 `qfq_schema_contracts` 期望列集、版本常量、迁移 runner、相关测试**同一 commit** 落地，
并经 ②审计后方可执行。**本批 FP-5 撤销执行，库已恢复 `complete_2_1`**。
迁移脚本已加硬门（无 `--allow-schema-contract-change` 直接 exit 5），防止复发。

### 8.3 判据修订申报（2 项）

1. **FP-7 配额首值 500–1000 → 实测定为 60**。依据：10-17 生产日志实测 ≈5.4 min/单元
   （`已处理 50/4818`，15:27→19:55），单窗 6h 仅能完成 ≈66 单元；取 60 留余量。
   500–1000 对应 45–90 小时，单窗不可能 finalize，与原意（单窗可完成）相反。
2. **A27 顺延**：受控窗内本轮完整 finalize 需 ≈5.4h（60 单元），超出窗口。
   窗内可取证：FP-3 回收、FP-7 配额日志、`gate_scope=cycle` 与 `health` 字段结构；
   **"水位实际前进"需下一轮（06:00）复核**，收窗报告将显式标注该项未完成。

