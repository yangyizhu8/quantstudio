# MCP pipeline routing, pagination, and normalization repair evidence

- **Date**: 2026-08-04
- **Scope**: QFQ routing isolation, complete cursor pagination, composite index-constituent codes, and nullable timestamp normalization
- **Git state**: framework/config/test/documentation repair committed and pushed to `origin/main` as `fed25dcc5caff15dfe1f0e9f83d64ff0d26810af`; remote SHA verified

## Repairs

1. `MCPAdapter._QFQ_ADJFACTOR_TABLES` remains an explicit `(table, freq)` whitelist. `index_constituents`, financial, valuation, and index datasets never touch factor synchronization merely because they use export transport.
2. Every non-export mapped or passthrough dataset consumes `fetch_page` cursors to exhaustion. `query_snapshot` is not a production full-table path. Metadata identifies `fetch_mode=export|fetch_page`.
3. `index_constituents` filters out-of-contract `Hxxxxx.CSI` source indices before alignment. `FieldAligner.code_cols` and `PreIngestValidator.code_fields` normalize and validate both `index_code` and member `code`.
4. `to_ms_timestamp` maps `None`, NaN, and `NaT` to null; the existing required, DateValid, and PIT rules decide quarantine row by row.
5. Empty QFQ DataFrames skip factor synchronization. Non-empty stock/ETF QFQ batches still fail fast when factor synchronization fails.

## Validation evidence

| Scenario | Result | Evidence |
|---|---:|---|
| Focused routing/QFQ/pagination regression | 20 passed | `tests/test_mcp_fetch_routing.py`, `tests/test_mcp_etf_latest_anchor.py`, `tests/test_pipeline_migration.py` |
| Extended framework regression | 225 passed | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, 2026-08-04 |
| mcp_only ConfigLint | 0 errors / 0 warnings | 86 tasks, 72 enabled |
| Default ConfigLint | 0 errors / 1 existing warning | Existing unsupported Tushare `sw_industry` candidate warning |
| Isolated `index_constituents` pipeline | 251,947 raw -> 224,207 written | 27,740 rows / 43 `Hxxxxx.CSI` codes filtered; quarantine 0 |
| Isolated `index_daily` pipeline | 64,373 written | One source row with null OHLC quarantined by the existing gate |
| Isolated `block_trade` passthrough pipeline | 61,195 written | More than 10,000 rows; source columns preserved |
| Isolated `fin_indicator` pipeline | 135,840 written | 111 source rows with `end_date=1970-01-01` quarantined |
| Online `fetch_page` probe | passed | Dict rows and string cursors for `index_daily`, `index_weight`, `stock_basic`, `trade_cal`, and `ws_exdiv` |
| Formal read-only QualityAudit | 315 checks / 0 errors / 9 warning classes | Existing visible warnings retained; no production data mutation |

## Retained risks and deliberate non-changes

- Passthrough still returns a complete DataFrame before full replacement. `sw_weight` is about 4.18 million rows and `sw_daily` about 1.50 million rows. Cursor pagination prevents truncation but retains memory/latency risk. This repair does not change the public adapter/writer contract or reject valid data with a hard row cap.
- Invalid source values remain in quarantine. The repair does not invent prices, dates, or PIT availability, and does not relax rejection thresholds.
- No formal production database write was performed. After explicit post-repair user confirmation, the coordinated framework/config/test/documentation scope was committed and pushed to `origin/main` as `fed25dcc5caff15dfe1f0e9f83d64ff0d26810af`; `quantstudio/test_n8n.py` was explicitly excluded and remains untracked.
