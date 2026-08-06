# B-3b.3 Independent CodeBuddy Re-review Package

> Status: CodeBuddy independent re-review passed (P0=0, P1=0) on the project authoritative date 2026-08-05.
> Approval record: CodeBuddy independently reran the migration, related-contract, extended-regression, read-only production and Git-gate checks.
> Production migration, production backfill, active cutover, staging/commit/push/PR remain prohibited.

## 1. Review scope

Implementation files:

- `quantstudio/pipeline/qfq_schema_migration.py`
- `tests/test_qfq_schema_migration.py`

Updated contract/runbook files:

- `docs/mcp_migration/mcp-cutover-design-v2.md`
- `docs/mcp_migration/b3r-schema-exploration.md`
- `docs/qfq-production-enablement-checklist.md`

The authoritative progress report already contains a pre-existing v6.6.3 pending-review entry that describes the superseded hardlink-publish draft. Codex did not edit the authoritative report in this implementation turn. After CodeBuddy approval, correct that entry or append the approved correction immediately with the final evidence; do not record approval before the review passes.

## 2. Frozen B-3b.3 report contract

1. The final report path is reserved before any database read-write connection with `O_CREAT | O_EXCL | O_RDWR`.
2. The reservation immediately stores a valid `PENDING` JSON record.
3. The same owned file descriptor is retained for the full call.
4. No publish temporary file is created and `os.replace` is not used.
5. Parent-directory preparation, allowed-root containment, DB/main-production/aux identity rejection, and existing-path rejection happen before migration.
6. File identity is checked before opening the DB read-write and immediately before COMMIT.
7. Failure cleanup removes a path only when it still identifies the inode created by the current call.
8. Frozen report states:
   - `PENDING`
   - `DRY_RUN_COMPLETE`
   - `ROLLED_BACK`
   - `MIGRATION_COMMITTED`
   - `ALREADY_CURRENT`
   - `FAILED_PRECHECK`
9. A post-COMMIT report/audit failure raises `MigrationCommittedReportError`; CLI returns exit code 3. The DB must be treated as committed and recovered through a new COMPLETE_2_1 already-current audit report.

## 3. Focused mechanical command

```powershell
python -m pytest tests/test_qfq_schema_migration.py -q -rs
```

Codex result:

```text
82 passed, 1 skipped
```

The single skip is Windows symlink creation without SeCreateSymbolicLinkPrivilege (`WinError 1314`). Same-volume hardlink and directory-junction aliases executed and passed.

## 4. Extended regression command

```powershell
python -m pytest tests `
  -k "qfq or reanchor or writer or authority or staging or phase1_pipeline or schema_migration" `
  --ignore=tests/test_qfq_production_schema_readonly.py -q
```

Codex result:

```text
822 passed, 0 failed, 1 skipped, 1199 deselected
```

CodeBuddy first-review P1 regressions are now closed:

1. `supersede_bootstrap_runs` uses an exact-predicate `SELECT COUNT(*)` before UPDATE; DuckDB `.rowcount` is no longer consumed.
2. `write_fresh_capture` writes all columns in `FRESH_CAPTURE_COLS`, including `download_trace`, min/max timestamps, and generation fields; older internal record-like objects receive frozen defaults for optional fields.

Focused closure command:

```powershell
python -m pytest tests/test_qfq_bootstrap_gates.py tests/test_qfq_fresh_capture_download.py tests/test_qfq_authoritative_rebase.py -q
```

Result: `56 passed`.

## 5. Production read-only and config checks

```powershell
$env:QS_PRODUCTION_READONLY='1'
python -m pytest tests/test_qfq_production_schema_readonly.py -q
```

Result: `2 passed`.

ConfigLint results:

- default: 0 errors / 1 existing warning
- mcp_only: 0 errors / 0 warnings

## 6. Required attack checks for CodeBuddy

CodeBuddy should independently rerun or inspect all of the following:

- report equals target DB: reject before migration; DB unchanged;
- report points to production main DB or qfq_aux DB: production refusal;
- report hardlink to DB: reject;
- report outside allowed-root: reject;
- report existing before call: reject without overwrite;
- report parent is a regular file: reject before migration;
- injected competing final-file claimant between path check and `os.open`: exactly one O_EXCL winner; external content unchanged;
- two real concurrent reservation calls for the same final path: exactly one winner;
- no `*.tmp.*` report publish files and no `os.replace` in implementation;
- reserved pathname replacement: Windows denies replacement while descriptor is open; other platforms must be rejected by inode identity checks before DB write/COMMIT;
- controlled before-COMMIT failure: DB remains COMPLETE_2_0 and report becomes ROLLED_BACK;
- true `os._exit(91)` before COMMIT: DB/data restore to legacy and report remains valid PENDING;
- true `os._exit(92)` after COMMIT/before report update: DB is COMPLETE_2_1, old report remains PENDING, fresh already-current report succeeds;
- post-COMMIT report update failure: `MigrationCommittedReportError`, DB COMPLETE_2_1, fresh already-current audit recovery;
- dry-run, apply, and already-current terminal report states;
- legacy/target hashes, per-table migration mappings, PK/NOT NULL validation, and zero shadow/legacy residue.

## 7. Production invariants measured by Codex

```text
data/quantstudio.db
size: 14996746240
mtime_ns: 1785861379886337200
SHA-256: 53e85feda38bc71a2595317092a5d03270b40558a9ea97c375bdf75703d5c677

data/qfq_aux.db
size: 2641793024
mtime_ns: 1785830385636695100
SHA-256: 5966790153c4966a8dfbe61b59f50880c98e4dd49705053fffacdcda9e2158b9
```

No production migration or production write was executed.

## 8. Known residual risk for explicit review

A catastrophic process/OS/power loss during an in-place terminal JSON rewrite can leave the report incomplete. The database physical schema is authoritative; recovery uses a fresh report path and the COMPLETE_2_1 already-current audit. CodeBuddy should decide whether this recovery contract is acceptable for B-3b/B-4 staging, or whether an append-only/dual-file evidence format is required before production execution.

## 9. Git gate

**Staging index terminology**: `git diff --cached --name-only` is empty. The repository has many modified/untracked working-tree files, but none is staged; working-tree changes do not make the staging index non-empty.

Expected before review:

- HEAD unchanged at `f7e8b2d6b4758830ab65f43c062acf7393e8a839`;
- index empty;
- no stage/commit/push/PR;
- no production DB changes.

After CodeBuddy re-review passes, update the authoritative progress report immediately with evidence. Git synchronization still requires the user's separate explicit post-repair confirmation.

## 10. Approval addendum and transition to B-4

CodeBuddy independently concluded **PASS**, P0=0/P1=0. It confirmed 82 passed/1 skipped migration tests, 138 passed/1 skipped focused combination, 822 passed/0 failed/1 skipped/1199 deselected extended regression, unchanged production main/aux evidence, O_EXCL state-machine attacks, and an empty staging index.

The user subsequently selected residual-risk option 1: the database physical schema is authoritative; if a terminal report rewrite is catastrophically interrupted, use a fresh report path for a COMPLETE_2_1 already-current audit. That decision authorizes B-4 staging rehearsal only; it does not authorize production migration or Git synchronization.
