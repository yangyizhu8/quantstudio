# B-4 Independent Review Package

> B-4 implementation evidence date: 2026-08-05; CodeBuddy independent review date: 2026-08-06.
> Host-generated timestamps are preserved exactly as recorded in the evidence files.
> Status: CodeBuddy independent review passed on 2026-08-06 (P0=0, P1=0); B-5 local implementation is allowed.
> B-5 local implementation is allowed; production migration and Git stage/commit/push/PR remain separately blocked.

## 1. Review scope

New files:

- `scripts/qfq_b4_staging_drill.py`
- `tests/test_qfq_b4_staging_drill.py`

Modified framework/test files:

- `quantstudio/pipeline/qfq_schema_migration.py` (post-COMMIT hard-crash boundary moved after durable COMMIT and before normal cleanup/report)
- `tests/test_qfq_schema_migration.py` (strict exit-92 contract retained; docstring clarified)

Updated documentation:

- `README.md`
- `docs/strategy_toolbox.md`
- `docs/prompt_engineering.md`
- `docs/qfq-production-enablement-checklist.md`
- `docs/mcp_migration/mcp-cutover-design-v2.md`
- `docs/mcp_migration/b3r-schema-exploration.md`
- `docs/mcp_migration/b3b3-codebuddy-rereview-package.md`
- authoritative progress report outside the Git worktree

Evidence root:

- `output/mcp_migration/b4_20260805_final/`
- primary report: `output/mcp_migration/b4_20260805_final/b4_drill_report.json`

## 2. Frozen B-4 boundary

B-4 is production-preflight plus staging-copy rehearsal. It must not implement B-5 dynamic generation/cutover filtering, global RETURNING conversion, discovery-baseline CAS, or B-6 legacy retirement/active pointer/mcp-gen1 activation.

The drill is outside the production daemon call chain. Default invocation is zero-write preflight. `--execute` writes only under the requested output run directory. During the formal-file copy it holds both QuantStudio daemon and collector locks. The formal DB paths are required to equal the configured production main and aux paths; migration itself continues to operate only on copies.

## 3. Full-copy command and result

```powershell
python scripts/qfq_b4_staging_drill.py --run-id b4_20260805_final --execute
```

Result: exit code 0, elapsed about 4m20s, evidence directory about 43.657 GiB（单次 run 近似值）.

Branch states:

- baseline: COMPLETE_2_0
- normal: COMPLETE_2_1
- recovery: COMPLETE_2_1

Normal report sequence:

- DRY_RUN_COMPLETE
- ROLLED_BACK at `before_commit`
- DRY_RUN_COMPLETE after rollback, with the same logical hashes as initial dry-run
- MIGRATION_COMMITTED
- ALREADY_CURRENT

Recovery sequence:

- `after_commit_before_report` raises `MigrationCommittedReportError`
- DB remains COMPLETE_2_1
- fresh report path returns ALREADY_CURRENT

## 4. MCP bootstrap and first-discover evidence

The drill builds a controlled offline auxiliary DB from copied real factor rows and configures `price_source=mcp` with attack values `source_generation=mcp-gen1` and a fake cutover id. Existing B-3a code must still persist only the frozen pre-cutover generation.

Observed:

- bootstrap trigger count unchanged
- factor observation new counts: stock=0, ETF=0
- factor revision triggers=0
- stock_dividend first pass=2181 new triggers
- immediate identical replay=0 new triggers in every category
- MCP observation cursors use `price_source=mcp`, `source_generation=xtquant-legacy`
- `qfq_active_cutover` count=0
- all ten generation-bearing tables have `mcp-gen1` count=0

Important review point: current pre-B-5 stock-dividend discovery is explicitly a full-table content-hash scan and does not use the cursor to suppress history. Therefore B-4 records 2181 as the real first-pass baseline and requires immediate replay idempotence. Requiring first-pass zero would incorrectly import the future B-5 discovery-baseline/CAS contract into B-4.

## 5. Production invariants

Main `data/quantstudio.db` before and after:

- size: 14996746240
- mtime_ns: 1785861379886337200
- SHA-256: 53e85feda38bc71a2595317092a5d03270b40558a9ea97c375bdf75703d5c677

Aux `data/qfq_aux.db` before and after:

- size: 2641793024
- mtime_ns: 1785830385636695100
- SHA-256: 5966790153c4966a8dfbe61b59f50880c98e4dd49705053fffacdcda9e2158b9

Formal main remains COMPLETE_2_0. No production migration/write occurred.

## 6. Tests to rerun independently

```powershell
python -m pytest tests/test_qfq_b4_staging_drill.py -q -rs
python -m pytest tests/test_qfq_schema_migration.py tests/test_qfq_b4_staging_drill.py -q -rs
python -m pytest tests/test_qfq_bootstrap_gates.py tests/test_qfq_event_discovery.py tests/test_qfq_fresh_capture_download.py tests/test_qfq_authoritative_rebase.py -q -rs
python -m pytest tests -k "qfq or reanchor or writer or authority or staging or phase1_pipeline or schema_migration" --ignore=tests/test_qfq_production_schema_readonly.py -q
$env:QS_PRODUCTION_READONLY='1'; python -m pytest tests/test_qfq_production_schema_readonly.py -q
```

Current local results (independent rerun required):

- B-4 tool tests: 5 passed
- migration + B-4: 87 passed / 1 skipped
- related bootstrap/discovery/fresh/authoritative: 64 passed
- extended regression: **827 passed / 0 failed / 1 skipped / 1199 deselected**
- production read-only: 2 passed
- ConfigLint default: 0 errors / 1 existing warning; mcp_only: 0 errors / 0 warnings
- only skip is Windows symlink privilege (WinError 1314)

## 7. Attack/review matrix

- default preflight creates no run directory and performs no DB write
- formal paths must exactly match configured production main/aux paths
- output run directory is exclusive/no overwrite
- evidence JSON uses O_EXCL/no overwrite
- insufficient disk fails before copy
- busy daemon or collector lock fails before copy
- non-empty DuckDB/SQLite WAL or journal sidecar fails before raw copy
- copy hashes equal formal hashes while both locks are held
- formal evidence unchanged after entire drill
- rollback restores COMPLETE_2_0 logical hashes
- committed interruption recovers only via a fresh report path
- no active cutover, no mcp-gen1, no B-5/B-6 behavior
- immediate discover replay is idempotent
- report says `production_ready=false` and `git_sync_authorized=false`

## 8. Git gate

Expected at review time:

- HEAD remains `f7e8b2d6b4758830ab65f43c062acf7393e8a839`
- `git diff --cached --name-only` is empty
- no stage/commit/push/PR

CodeBuddy B-4 review passed on 2026-08-06. The authoritative progress report records B-4 PASS and B-5 local admission. Git synchronization remains a separate user gate.

## 9. Evidence file hashes

- `preflight.json`: size 1289, SHA-256 `3ba1190b00e9049c00e96faf5fc50ce41d24caea4e60c7692e965cf817fb064a`
- `evidence/copy_manifest.json`: size 2665, SHA-256 `315c3d23bb32f85726170e989265186911930e3b8fc831d2b852c54b1df21a84`
- `b4_drill_report.json`: size 57005, SHA-256 `297726786693222a72832695e16456fbe8e8b831299e3ae261067857072280b2`
- `normal/report_04_apply.json`: size 12241, SHA-256 `c6a6d173abd5e5d5255dd0b00273a990b799fb2a45d2fc98b9c4646c87bb5f24`
- `recovery/report_02_fresh_already_current.json`: size 4875, SHA-256 `340dd0974b3716765208af53920a5952b076aa964dd96892016c4d8466fe18d6`

## 10. Windows post-COMMIT hard-crash boundary repair

Final regression initially exposed a repeatable Windows native failure: the test expected `os._exit(92)` but occasionally received `0xC0000005`. Failed samples still had DB=COMPLETE_2_1 and report=PENDING, but accepting the access violation or widening the allowed exit codes was prohibited.

Root cause analysis showed that the crash injection point was reached only after normal DuckDB connection cleanup. The minimal framework repair moves `after_commit_before_report` to immediately after durable `COMMIT` and `committed=True`, before normal connection cleanup/report update. Normal execution and controlled exceptions still close the connection in `finally`; only a true process hard exit skips cleanup, which is the intended crash model.

Validation was strictly serial because running separate DuckDB pytest processes concurrently reproduced a Windows native interference signal:

- direct no-marker `os._exit(92)`: 30/30 returned 92;
- correctly typed Windows `TerminateProcess(..., 92)` diagnostic: 20/20 returned 92 (diagnostic only; production test continues to use `os._exit`);
- original pytest hard-crash test, strictly serial: 20/20 passed;
- migration + B-4 suite: 87 passed / 1 skipped;
- final extended regression, strictly serial: 827 passed / 0 failed / 1 skipped / 1199 deselected.

The exit assertion remains exactly 92. No tolerance was widened, and `0xC0000005` is not accepted. Independent review should verify the fault point is after durable COMMIT but before normal connection cleanup/report processing.

## 11. CodeBuddy independent review result

- Date: 2026-08-06
- Verdict: **PASS**
- P0: 0; P1: 0
- B-5 local implementation: allowed
- Production migration: not authorized
- Git synchronization: not authorized
