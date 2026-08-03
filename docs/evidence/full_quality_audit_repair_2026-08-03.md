# Full-database quality-audit repair evidence

- **Authoritative date:** 2026-08-03. Machine-generated `2026-08-04` identifiers are treated as clock/rollover anomalies.
- **Scope:** local framework repair and database operations. GitHub synchronization remained gated until the user gave explicit post-repair confirmation on 2026-08-03.

## Root causes and repairs

1. `stock_basic/TableMissing`: MCP profile defined the canonical schema, but `DuckDBWriter` did not create or write the table. Added DDL, column manifest, key routing, type protection, and the metadata fields consumed by backtest reference APIs.
2. `trade_calendar/SchemaColumnMissing(exchange)`: MCP and QFQ shared a table whose QFQ DDL lacked MCP metadata. Added `exchange` and `pretrade_date` while preserving the existing `PRIMARY KEY(cal_date)` and all QFQ queries.
3. `trade_calendar/EnumCheck(is_open)`: the auditor cast BOOLEAN values to text (`true/false`) before comparing them with `0/1`. It now compares native values using bound parameters.
4. `QfqPendingSla`: the auditor measured age from immutable event `created_at`. It now measures current-state age from `COALESCE(updated_at, created_at)`.
5. QFQ queue backlog: no rows were deleted and thresholds were not relaxed. Staging used the supported `retry-due` and reviewed `reopen` operations.
6. MCP `trade_calendar`: moved from generic export to `query_snapshot` because the source has no `ts_code` column and contains exactly 10,000 rows; the requested range through 2026-08-03 returned 3,137 rows.
7. Non-security schemas can explicitly set `code_field: null`; absent configuration retains the previous primary-key-first inference.

## Staging evidence

Full pre-repair database copy: `data/staging/quality_audit_repair_run_6f2c9a/quantstudio.db`.

- MCP `stock_basic`: raw/aligned/passed/written = 5,222/5,222/5,222/5,222; rejected=0.
- MCP `trade_calendar` through 2026-08-03: 3,137/3,137/3,137/3,137; rejected=0; all rows updated under the unchanged `cal_date` key.
- Before queue recovery, all reference/schema/enum errors were gone. Remaining errors were 29 dead letters and 27,649 stale in-progress claims.
- `retry-due`: stale recovered=27,649; retryable due=154; scheduled promoted=2.
- Reviewed dead letters reopened=29.
- Final staging audit: **passed=true, checks=315, errors=0**. Existing warnings remain visible (adjustment-return review, optional balance-statement columns, recent failed batches, quarantine trace/backlog).

## Regression evidence

- New focused audit repair tests: 8 passed.
- Aligner/validator/industry compatibility set: 35 passed.
- Affected framework and QFQ regression set: 220 passed.
- Broader metadata set: 191 passed, 1 environment/golden-data mismatch (`300750` listing date) caused by current production data/worktree changes outside this repair; no audit-repair assertion failed.

## Production application result

The independent R1-A read audit produced its evidence and released the DuckDB connection naturally; it was not terminated. Before production mutation, the repair captured a full 15 GB database copy plus Parquet snapshots of 27,836 non-terminal/error queue rows, four old active-cycle rows, and all 3,652 calendar rows.

Production then used the staging-approved sequence:

- reference schema migration and MCP replay: `stock_basic` 5,222/5,222 passed and inserted; `trade_calendar` 3,137/3,137 passed and updated;
- QFQ `retry-due`: stale in-progress recovered=27,649, retryable-due recovered=154, scheduled promoted=2;
- reviewed dead letters reopened=29;
- final production audit: **passed=true, checks=315, errors=0**.

Final persisted invariants:

- `stock_basic`: 5,222 rows; `symbol`, `ts_code`, and `data_source` complete;
- `trade_calendar`: 3,652 rows, 2,428 open days, all `exchange` values present, 3,137 MCP-sourced rows, and unchanged `PRIMARY KEY(cal_date)`;
- QFQ queue: committed=27,068, pending=27,834, scheduled=2, with no dead-letter, retryable-failed, or stale in-progress audit errors. Pending rows remain queued for normal orchestrator processing; they were not deleted or falsely marked complete.

Evidence files are under `data/staging/quality_audit_repair_run_6f2c9a/`, including `production_rollback_manifest.json`, `production_schema_and_reference_repair.json`, and `production_final_quality_audit.json`.

## Host-clock correction completed

The supported QFQ recovery commands initially ran after the host clock crossed into the future date 2026-08-04, while the authoritative date remained 2026-08-03. With explicit user authorization, the long-running R1-A child process PID 5080 and parent PID 35228 were terminated after their identities and command lines were verified. A snapshot of all 27,834 affected rows was saved before correction.

A single transaction changed only those rows' `updated_at` values using `updated_at = updated_at - INTERVAL 1 DAY`. The resulting range is 2026-08-03 03:04:43 through 03:06:07. Queue status counts were identical before and after, and all QFQ timestamp columns now contain zero values on or after 2026-08-04. Post-correction verification again passed 315 checks with zero errors and 220 focused regression tests.
