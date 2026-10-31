# S4 每日读数接口 · 会签基线（2026-10-31）

> 状态：**我方草案经调度照准为会签基线**，与 dev 直签定稿。
> 调度补充：字段增加 **`poison_count`**（S1 的 poison 有界机制落地后须每日可见）。
> 本文为会签基线文本，dev 确认后即定稿。

## 一、口径来源

| 项 | 来源 |
|---|---|
| 数据缺口主症 | 日线表 doom-loop（水位停滞 55+ 天），见 `docs/evidence/etf-daily-write-conflict-20261031.md` |
| 水位读数 | `source_watermark`（`table_name/last_date`）vs 各表 `MAX(time)` |
| A4 行为 | `daemon.log` 的 `A4 熔断` / `A4 跳过检测` 两类记录 |
| poison 读数 | S1 poison 有界机制（待落地；落地前该字段填 `-1` 表示未接入） |

## 二、字段定义（9 项）

| # | 字段 | 类型 | 语义 | 取值示例 |
|---|---|---|---|---|
| 1 | `date` | `YYYY-MM-DD` | 读数所属交易日（非采集时刻） | `2026-10-31` |
| 2 | `table_name` | str | 被读数的表 | `etf_daily` |
| 3 | `watermark_date` | `YYYY-MM-DD` \| null | `source_watermark.last_date` 换算的日期 | `2026-09-04` |
| 4 | `max_time_date` | `YYYY-MM-DD` \| null | 该表 `MAX(time)` 换算的日期 | `2026-10-29` |
| 5 | `gap_days` | int | `date - watermark_date` 的天数（**水位落后天数**，核心指标） | `57` |
| 6 | `a4_action` | enum | `fused`（熔断跳过）/ `skipped_no_baseline`（无 last_sync 基准）/ `normal` | `fused` |
| 7 | `window_count` | int | A4 检出的 repair/full 窗口数（`a4_action=normal` 时为 0） | `999` |
| 8 | `last_error_class` | str \| null | 当日该表最后一条错误的类别（如 `write_write_conflict` / `export_exceeds_time_budget`），无则 null | `write_write_conflict` |
| 9 | **`poison_count`** | int | 当日被 poison 有界机制拦下的毒键/毒批次计数；**S1 未落地时填 `-1`** | `-1` |

## 三、频次与落点

| 项 | 约定 |
|---|---|
| 频次 | **每日一轮一次**，在轮次收尾（`run_state` 写入时）产出 |
| 落点① | `data/logs/gap_daily_<yyyyMM>.jsonl`（每日 1 行/表，追加写，原子写：tmp + replace） |
| 落点② | 当日汇总一行并入 `run_state`（键名 `gap_snapshot`），字段 = `{date, tables_overdue_over_7d, max_gap_days, fused_tables, poison_total}` |
| 缺数据语义 | 表当日未参与轮次 ⇒ **不写该表行**（不填 0，避免「看起来正常」的假象） |

## 四、回执格式（供 dev 直签确认）

**JSONL 单行示例**（`etf_daily`，2026-10-31）：

```json
{"date":"2026-10-31","table_name":"etf_daily","watermark_date":"2026-09-04","max_time_date":"2026-10-29","gap_days":57,"a4_action":"fused","window_count":999,"last_error_class":"write_write_conflict","poison_count":-1}
```

**run_state 汇总示例**：

```json
{"gap_snapshot":{"date":"2026-10-31","tables_overdue_over_7d":2,"max_gap_days":58,"fused_tables":["stock_daily"],"poison_total":-1}}
```

## 五、我方承诺的判据（可机检）

1. **幂等**：同一交易日重复运行 ⇒ JSONL 不重复写（按 `(date, table_name)` 去重）；
2. **不掩盖**：某表当日失败 ⇒ 该行 `last_error_class` 必须非 null（禁止只写水位不写错误）；
3. **不倒因为零**：`gap_days` 只由「交易日 − 水位日」计算，不因 A4 熔断而填 0；
4. **poison 未接入时显式 -1**，不得填 0（0 会被误读为「毒键已清零」）。

## 六、待 dev 直签项

| 项 | 我方建议 | 待确认 |
|---|---|---|
| `gap_days` 是否按**自然日**还是**交易日** | 自然日（实现简单、与调度日历无关） | dev 若按交易日更贴合业务，我方可改 |
| `a4_action` 是否需要第四值（如 `error`） | 暂不需要（错误走 `last_error_class`） | 待 dev |
| JSONL 保留期 | 按月文件，永久保留（每行 ~200B，年增 <1MB） | 待 dev |
| `poison_count` 接入时点 | S1 落地后由 dev 提供取值接口，我方改 `-1` → 实值 | 待 dev |

## 七、会签记录

| 角色 | 状态 | 时间 |
|---|---|---|
| 客户运维线（我方） | **已提交基线** | 2026-10-31 |
| 调度 | **照准为会签基线**（+ `poison_count`） | 2026-10-31 |
| dev | 待直签 | — |
