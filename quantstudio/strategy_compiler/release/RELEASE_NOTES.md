# QuantStudio Strategy Compiler ? Release Notes

## unreleased-source_import-ptrade-history-translation (2026-08-19)

- **source_import PTrade 行情翻译缺口修复**（框架方案 `docs/source_import-ptrade-history-translation-design.md`，真实平台冒烟通过）：
  - `get_history` wrapper 请求侧剔除本地合成伪列 `trade_date`，返回侧由 `datetime/time` 列或 DataFrame index 合成 `trade_date`（先改名再合成，`YYYY-MM-DD`）；
  - `is_dict=True` 走逐码路径（不依赖多标的返回形态契约），拼 `{code: df}`；
  - 非 dict 模式 `security_list`/`security` 透传原始 API（修复真实 PTrade「未订阅股票池时 security_list 不能为空」）；
  - 门控：仅显式使用 `trade_date` 的策略注入新 wrapper，存量转换输出逐字节不变（纯增益）。
- 验收：`tests/test_ptrade_contract_compliance.py` + `tests/test_source_import.py` 74 PASS（含 `test_trade_date_ext_non_dict_passes_security_list` / `test_trade_date_ext_synthesizes_from_index` 新回归）；真实 PTrade 冒烟（vol_regime_mom_rev）全月跑通。
- 关联：D4-S2（市价单 5 万股上限）/ D4-S3（退市强平 is expired）为双端平台语义差，登记表 pending，不影响本发布。
- 探针：`ptrade/probe_expired_close_ptrade.py`（D4-S3 强平成交明细取证）。

## 0.7.3-backtest-show-tables-cache (2026-07-28 门 1 收敛整改)

- SHOW TABLES 表集合缓存落地（仅 1 项语义等价优化）：`_existing_tables()` 缓存 `SHOW TABLES`
  结果；`preload_daily_bars` / `query_strategy_events` / `query_corporate_actions` 3 处原直接
  `SHOW TABLES` 收敛至统一路径（小市值策略 76 交易日实测 152 → 1）；返回防御性 `set` 副本；
  `close()` 恢复 `None`。
- 调用路径事实（b41400d）：当前共 10 个 catalog/表存在性检查调用方共享 `_existing_tables()`；
  其中 7 个为 b41400d 既有调用方，`preload_daily_bars` / `query_strategy_events` /
  `query_corporate_actions` 3 个原直接 `SHOW TABLES` 已收敛至统一入口；`query_daily_snapshot`
  在 b41400d 中已直接查询 `stock_daily`/`etf_daily`，不属于上述 10 个调用方。
- provider-level get_history 缓存（`query_bars_by_count_multi_table`）拒绝实施、进入 backlog：
  小市值/双均线真实命中 0；`PtradeAPI.get_history()` 已有 `_query_cache`；synthetic 86× 不构成
  生产收益证据；4096 条目上限是条目数非字节上限。
- 验证：定向测试 6 项全绿；全量 nodeid 对比零回归；黄金结果字节级一致；A/B 交错 B,O,O,B,B,O
  确定性收益 SHOW TABLES 152→1、SQL 减少；端到端耗时高噪声。

## 0.7.2-framework-repair-review-round3 (2026-07-27)

- F3: snapshot completeness now uses COUNT(DISTINCT code); quality violations (duplicate/negative/blank codes) force `invalid`; missing expectations config fails closed; variable-count indices must be explicitly registered (`variable_indices`) — unregistered indices are `unknown` and never served.
- F4: official `index_member` has no conflict-resolution rule — raw overlapping intervals are preserved 1:1 (only from>to dirty rows dropped); ambiguous dates fail closed at API level (`ReferenceDataCapabilityError`); `industry_membership_pit` is downgraded to APPROXIMATION_REQUIRES_CONFIRMATION/DATA_BLOCKED (DEGRADED), never formal PIT READY while overlaps exist. Safe migration tool `scripts/rebuild_industry_tables.py` (staging + gates + single-transaction atomic swap; failure injection leaves official tables byte-identical).
- F5: SW2021 L1 universe gate (31 codes / format / uniqueness) — probe failure, short, duplicate or malformed universes fail the whole daemon task with watermark unchanged (public-entry tests).


## 0.7.1-framework-repair-review-correctives (2026-07-27)

- F2 corrective: `get_stock_info` stock behavior restored to pre-repair values byte-for-byte (name=bare code, listed_date=first `stock_daily` bar); only ETF metadata is extended. Golden value-level comparison vs HEAD: identical.
- F3 corrective: snapshot completeness now comes from the formal batch contract `index_constituents_snapshot_meta` (n/expected_count/status, determined at ingest, never from future snapshots); future writes cannot change historical query results.
- F4a corrective: `fetch_table` normalizes None/'ALL'/['ALL'] before dispatch (no more `ALL.SI` requests); empty single-industry member fetch is all-or-nothing fail-closed.
- F4b corrective: industry membership intervals are repaired by a deterministic daily-uniqueness transform (gate: positive overlaps / multi-current / orphan / bad ranges all 0); formal table primary keys now include `industry_level`.
- F5 corrective: the formal daemon universe `get_index_daily_universe()` (CSI + SW2021 L1) serves full/incremental/resident through the per-stock path; next-day incremental watermark behavior covered by daemon integration tests.
- F6 corrective: `get_industry` profile entry corrected to the exact local signature/shape; capability inspection now probes through real Provider/API calls and reports DATA_BLOCKED honestly (meta missing / interval overlaps / resident path unreachable). The ambiguity fail-closed probe now enforces a hard gate: only when `get_industry` genuinely raises `ReferenceDataCapabilityError` on an overlapping-interval (ambiguous) date is the capability `provider_status=AVAILABLE` with message "verified"; otherwise (`get_industry` returns without error / wrong exception type / no ambiguous sample / probe internal error) the `industry_membership_pit` capability is `provider_status=BLOCKED` with `message` declaring `contract BROKEN`, and never claims an unqualified "fail-closed verified".


## 0.7.0-framework-repair-f1-f6 (2026-07-27)

- F1: PyQt backtest console exposes generic `rebalance_mode` (`legacy` default / `callback_basket`) via a single `EngineConfig.rebalance_mode` path; GUI blocks `callback_basket` with `close`/`open`; `run_daily` orders never enter the basket.
- F2: unified stock/ETF security metadata layer (`query_security_metadata`) feeding `get_security_info` / `get_stock_info`; ETF list/delist dates served; listing-date fallbacks explicitly marked; unknown-security compat behavior unchanged.
- F3: `get_index_stocks(date)` is now strict as-of PIT (latest complete snapshot on/before date; no history union, no future snapshots, fail-closed empty); partial snapshots are flagged and never served as complete PIT data.
- F4: formal SW2021 industry tables `industry_classification` + `industry_membership` (PIT effective ranges) replace the removed name-matching logic; `get_industry` serves historical as-of membership and fails closed when formal tables are missing; legacy `sw_industry` is audit-only.
- F5: SW industry index daily bars flow through the unified `index_daily` pipeline (tushare `sw_daily` routed for SW2021 L1 codes, canonical 股/元 units, full/incremental/resident + watermark inherited); `get_history` routes 801xxx to `index_daily` with raw-OHLC `fq='pre'` fallback.
- F6: PTrade profile 1.9.0 registers `get_industry` and declares the PIT/date contracts; capability report adds machine-checkable capabilities (`security_metadata_stock/etf`, `index_constituents_pit`, `index_constituents_history_coverage`, `industry_classification_sw2021`, `industry_membership_pit`, `sw_l1_index_daily`, `gui_rebalance_mode`, `callback_basket_pyqt`) with `status_detail` tokens; R1 checks are deeper than table existence.


## 0.5.0-user-pyqt-candidate-flow (2026-07-25)

- Added independent R0 backtest-execution ownership: agent-managed or user-PyQt.
- Added hash-bound `__candidate_quantstudio.py` generation after R4 PASS.
- Added structured user backtest evidence validation with ETF lower-bound, database, profile, completion and runtime-check gates.
- Added failure routing to R1/R3/R4 and source-drift invalidation.
- R6 now promotes user-tested candidates by regenerating formal targets and removing the candidate.


## 0.4.0-target-aware (2026-07-25)

- Added QuantStudio-only PIT ETF universe API `get_etf_list_local(query_date=None, etf_type="equity", active_only=True)` through injected API -> ReferenceDataProvider -> DuckDB data access.
- Added `etf_basic` metadata synchronization via `scripts/sync_etf_basic.py`; ETF classification/listing metadata remains separate from strategy indicators.
- Kept `get_etf_list()` as the PTrade-named contract and blocked it in backtest validation.
- Added R0 target selection, schema-conditional portability, static-whitelist dual ETF mode, dynamic-local ETF mode, target-aware validation, and target-aware publication.
- QuantStudio-only publication creates no PTrade placeholder and records PTrade validation / dual consistency as `NOT_APPLICABLE`.


## 0.3.2-mvp (Runtime Compatibility and Stable Publish Corrective)

- PTrade daily templates accept pandas and NumPy structured-array `get_history` results.
- Unsupported or history-empty securities are skipped instead of crashing on `BarDict`.
- Daily ranking is computed before trading from previous-close history; trading is scheduled with `run_daily`; `handle_data` does not repeatedly recalculate or access unsupported symbols.
- Multi-stock portfolios use `order_target_value` target sizing for daily equal-weight restoration.
- Shanghai/Broker suffix aliases are normalized and QuantStudio batch-history keys use bare-code lookup.
- Skill delivery publishes QuantStudio strategies to `quantstudio/backtest/strategies/` and PTrade strategies to project-root `ptrade/`.
- Delivery prefers the live project package over stale site-packages and can locate the capability inspector from the project Skill.
- Real data digest and cross-platform Fidelity remain deferred.

## 0.3.0-mvp (G4 Release Closure)

End-to-end hermetic strategy compile pipeline: **Spec → IR → dual Renderer
(QuantStudio + Strict-PTrade) → strategy package**, driven by a CLI, with Skill
install/validation and this release metadata.

### What's included
- **G1-I basket engine** (`bcdc85d`, merged): next_open `callback_basket` rebalance
  (0.4.0-next_open_basket) — sell-then-buy rotation with T+1 drain state machine.
- **G2 CP3 Reference closure** (`53d90f5`, merged): independent hand-written Oracle
  producing frozen signal/order/NAV reference artifacts + source/data digests.
  Hermetic/Synthetic Reference Partial Closure.
- **G3 Package Closure** (`9a99b18`, merged): dual Renderer → deterministic strategy
  package with manifest (version, entry points, artifact digests), import boundary,
  and optional G2 reference linkage (portable logical IDs).
- **G4 Release** (this): `qs-compile package` CLI, Skill install/validation flow,
  0.3.0-mvp version alignment, release metadata + docs.

### CLI usage
```
python -m quantstudio.strategy_compiler.cli package <spec.json> --out <dir> \
    [--g2-frozen-dir <frozen_dir>] [--package-version <semver>]
```
- Builds a strategy package dir under `--out` containing manifest.json, the frozen
  Spec/IR, both rendered strategies, `__init__.py`, README.md.
- Retains G3 manifest/digest verification (every artifact_digest matches the file;
  manifest never self-references its own digest).
- Surfaces Golden Protection (exit 3) and invalid-spec/missing-file errors (exit 2)
  honestly — never silent success on failure.
- `--g2-frozen-dir` links G2 frozen closure; records `data_digest_status=blocked`
  honestly (never faked as frozen).

### Skill install/validation
```
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --dest <skills_root>
```
Copies the Skill, runs quick_validate on the installed copy, rolls back on failure.

### Honest boundaries (IMPORTANT)
- **data digest: blocked.** `input_data_digest=null`, `data_digest_status=blocked`.
  Real market-data digest is **deferred**, not faked. This release does NOT validate
  real-data Fidelity or real-market Reference.
- **No real market data / live QMT / resident daemon** in this release. All tests
  are hermetic (synthetic scenarios / frozen artifacts).
- **Real Fidelity/Reference verification: deferred** to a later release.

### Delivery flow integration (0.3.0-mvp)
- The Skill **auto-orchestrates** orchestrator validation + qs-compile package
  generation. Normal users do NOT need to manually run qs-compile.
- `qs-compile` remains the formal CLI entry for advanced users, scripts, and CI.
- CLI direct entry: `qs-compile package <spec> --out <dir>` (preferred).
  `python -m quantstudio.strategy_compiler.cli` as dev/diagnostic fallback.
- `deliver_strategy()` API chains both steps into a unified output dir:
  `validation/` (orchestrator artifacts) + `package/` (strategy package) +
  `DELIVERY_REPORT.md` (unified summary).

### Known limitations
- 5 repository tests remain non-hermetic (calendar + fidelity golden-baseline
  fixtures) — environment-only failures, not pipeline defects.
- `np.log` RuntimeWarning in one CP3 NaN-exclusion test (non-blocking).


### Reproducibility
- Deterministic builds: fixed render-timestamp sentinel, canonical JSON,
  byte-identical packages across builds and processes (PYTHONHASHSEED-stable).

- Promoted `etf_basic` from a manual bootstrap-only sync to a first-class Tushare-only snapshot task shared by full, incremental, and resident collection, with DuckDB-baseline field/date/unit normalization and changed-row upsert.
