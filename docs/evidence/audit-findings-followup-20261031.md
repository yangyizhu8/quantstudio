# 06:00 轮审计发现 · 数据问题跟进清单（2026-10-31）

> 来源：`run=r_61cc7fdc`（2026-10-31 06:00 轮）审计结论 `9 类错误，32 类警告；checks=336`
> `quality_audit = {"scope":"all","tables_audited":23,"elapsed_s":1985.4,"skipped_oom":0,"query_error":0,"timeout":0,"incomplete":false}`
> 性质：**这些是审计在报告真实数据问题，不是审计故障**（Phase 1 的目标是让审计能跑完并说真话）

| # | 项目 | 读数 | 归属 | 状态 | 跟进动作 |
|---|---|---|---|---|---|
| 1 | `etf_daily/RequiredValueNull` | count=6（open/high/low 各 6 行） | **数据面** | 待查 | 定位具体 6 行与日期；与 #5 的 6 行是否同一批待核 |
| 2 | `etf_daily/AdjustmentCompleteness` | count=6（front 缺失） | **数据面** | 待查 | 同上 |
| 3 | `etf_daily/AdjustmentFactorConsistency` | count=2（front） | **数据面** | 待查 | 复权因子一致性，查是否与 QFQ 编排未完成有关 |
| 4 | `qfq_trigger_queue/QfqDeadLetter` | **count=14302**（dead_letter=14302） | **QFQ 专项** | 登记 | 并入 QFQ 专项读数集；死信需 reopen 或调查 |
| 5 | `qfq_trigger_queue/QfqPendingSla` | **count=50762**（pending/retryable_failed > 72h） | **QFQ 专项** | 登记 | **H0 假设来源**：积压 × N+1 可能就是挂死本体 |
| 6 | `stock_minutes/AdjustmentAnchorDrift` | count=150（FAIL>0.5%，max=0.9258） | **数据面** | 观察 | 与昨日 148 相比 +2（正常波动或新增） |
| 7 | `etf_minutes/AdjustmentAnchorDrift` | count=66（FAIL>0.5%，max=0.6603） | **数据面** | 观察 | 与昨日 66 相同（稳定） |
| 8 | `stock_daily/WatermarkConsistency`（历史） | 曾报 watermark vs max 不一致 | **数据面** | 已含于 doom-loop 案 | 见 `etf-daily-write-conflict-20261031.md` |
| 9 | `stock_daily` A4 熔断跳过 | 999 个 repair/full 窗口 > 阈值 20 | **任务策略** | 已记录 | 主动跳过（非失败）；与 doom-loop 同源（水位停滞 ⇒ 累积待修窗口） |

## 关联关系（重要）

```
日线写冲突 doom-loop（etf_daily / stock_daily 水位停滞 55+ 天）
   ├─→ #1 #2 #3  日线表空值/复权不完整（写入失败的直接后果）
   ├─→ #9        A4 熔断累积 999 窗口（水位停滞 ⇒ 待修窗口越积越多）
   └─→ #8        水位与 max 不一致

QFQ post-ingest 挂死（record_observations N+1 + aux WAL 无 checkpoint）
   ├─→ #4        死信 14302
   └─→ #5        pending 超 SLA 50762（H0：积压 × N+1 = 挂死本体）
```

## 处置边界（零越权）

- #1–#3：本会话**只登记**，修复属数据面/写入线；
- #4–#5：并入 QFQ 专项（插桩读数将裁决 H0/H1/H2）；
- #6–#7：观察，若持续增长则另立专项；
- #9：A4 熔断是既有保护，**不改阈值**（那是设计决策）。

## 沉淀日期

2026-10-31
