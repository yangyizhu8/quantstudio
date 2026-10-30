---
id: EOL-REVIEW-20261030
type: eol_review_reminder
status: open
as_of: "2026-10-30"
policy_ref: "specs/002-duckdb-1.5-upgrade/spec.md#FR-010"
---

## duckdb 版本线到期复核提醒（2026-10-30）

- 触发：**里程碑到点：1 条版本线处于非 normal 状态**

| 版本线 | EOL | 距 EOL（天） | 状态 | 应做动作 | 放行区间 |
|---|---|---|---|---|---|
| 1.4 | 2026-11-17 | +18 | close_or_accept | 距 EOL 30 天：完成或书面接受风险 | `1.4.5 ≤ x < 1.5` |
| 1.5 | 2027-03-31 | +152 | normal | 无（距 EOL > 90 天） | `x == 1.5.6` |

> 处置：按 90/60/30 升级判据推进（距 EOL 90 天出方案 / 60 天进实施 / 30 天完成或书面接受风险）；**未完成复核的版本线不得继续放行**。承载方：总调度 EOL 日历守护 + 仓库内守护条目（`scripts/check_duckdb_eol_guard.py`）。
