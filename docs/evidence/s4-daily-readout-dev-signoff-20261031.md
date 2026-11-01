# S4 每日读数接口 · dev 线会签回执（定稿）

- **日期**：2026-10-31｜**回签方**：主仓 dev 会话（节点⑤）｜**基线文本**：`docs/evidence/s4-daily-readout-spec-20261031.md`（4,214B）
- **结论**：**直签通过**（4 项签注：2 项确认已含 + 1 项口径修正 + 1 项补入）
- **性质**：本件为会签定稿回执；签署后 S4 接口口径生效，S1 落地时 `poison_count` 字段自动转实值

## 一、9 字段逐项签署

| # | 字段 | 签署 |
|---|---|---|
| 1 | `date` | ✅ 同意（**读数所属交易日**，非采集时刻 —— 口径正确） |
| 2 | `table_name` | ✅ 同意 |
| 3 | `watermark_date` | ✅ 同意（`source_watermark.last_date` 换算） |
| 4 | `max_time_date` | ✅ 同意（`MAX(time)` 换算） |
| **5** | **`gap_days`** | ⚠️ **签署附修正**：口径须明确为**交易日差**（`date` 与 `watermark_date` 之间的**交易日**数，按 `trade_calendar.is_open` 计），**不是自然日差**。理由：①自然日差在长假/停市周会虚高（如国庆、春节），把正常水位显示成"落后 10+ 天"；②与既有健康基准口径（交易日/`COUNT(DISTINCT code)`）一致；③本文示例值 `57` 若为自然日需按交易日重算后回填 |
| 6 | `a4_action` | ✅ 同意（`fused` / **`skipped_no_baseline`** / `normal` —— 已含无基准系，签项②完成） |
| 7 | `window_count` | ✅ 同意 |
| 8 | `last_error_class` | ✅ 同意（`write_write_conflict` / `export_exceeds_time_budget` 等） |
| 9 | **`poison_count`** | ✅ 同意（S1 未落地填 **`-1`**；**S1 落地后自动转实值**，签项④完成 —— 与 S1 poison 机制绑定） |

## 二、四项签注（签项逐条）

| # | 签项 | 处置 |
|---|---|---|
| **①** | **`gap_days` = 交易日**（非自然日） | **修正入基线**：见 §一 #5；实施时按 `trade_calendar` 计交易日差；历史示例值重算 |
| **②** | `a4_action` 加 `no-baseline` | **确认已含**（基线枚举 `skipped_no_baseline`）✅ |
| **③** | **JSONL 保留期 12 个月** | **补入基线**：`data/logs/gap_daily_<yyyyMM>.jsonl` 保留 **12 个月**，到期按月归档/清理（清理须留档记录，禁静默删） |
| **④** | `poison` 跟 S1 | **确认已含**（`poison_count`，S1 未落地填 `-1`）✅ |

## 三、频次与落点签署

| 项 | 签署 |
|---|---|
| 频次：每日一轮一次（`run_state` 写入时） | ✅ 同意 |
| 落点①：`data/logs/gap_daily_<yyyyMM>.jsonl`（原子写 tmp+replace，追加） | ✅ 同意（补充：保留 12 月，见签项③） |
| 落点②：`run_state` 汇总键 `gap_snapshot`（`{date, tables_overdue_over_7d, max_gap_days, fused_tables, poison_total}`） | ✅ 同意 |
| **缺数据语义**：表当日未参与轮次 ⇒ **不写该表行**（不填 0） | ✅ **强同意** —— 避免"看起来正常"的假象，与「证据到哪说到哪」一致 |

## 四、dev 线附加要求（2 条，实施时落实）

| # | 要求 | 理由 |
|---|---|---|
| A1 | `gap_days` 计算须**容错**：`watermark_date` 为 null ⇒ `gap_days` 填 **null**（非 0、非 -1） | null 与 0 语义不同；0 会被误读为"水位已同步" |
| A2 | `last_error_class` 须与 **M1 冲突字段**对齐（`conflict_table` / `conflict_scope`）——错误类为 `write_write_conflict` 时，同一行**附 `conflict_scope` 摘要** | 使 S4 读数与 S1/④案证据链同源，避免两套口径 |

## 五、签署

- **dev 线**：主仓 dev 会话（节点⑤）｜签署时间：**2026-10-31 23:45**
- **状态**：**S4 定稿**（本回执为会签终版；运维1 侧对称留档）
- **生效**：S1 落地前 `poison_count=-1`；S1 落地后自动转实值

## 六、追补（2026-11-01）：once 形态口径（用户裁定 b）

| 项 | 口径 |
|---|---|
| once 轮水位 | **不推水位为设计语义**（人工/回填形态自管水位）⇒ S4 读数中 once 轮的水位不动**不构成告警条件** |
| `a4_action` 场景 | 场景覆盖含 once 形态（once 轮 `gap_days` 增长属预期，不判异常） |
| 依据 | 方案件 §2.3（docs/design-write-selflock-and-a4-repair-20261101.md） |
