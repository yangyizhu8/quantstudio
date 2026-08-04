# 数据质量检查覆盖范围

QuantStudio 数据质量检查分为四层：

1. `ConfigLint`：启动前检查 schema、任务、adapter 注册/能力、PIT、主键和有效回退链。
2. `PreIngestValidator`：标准化后、写库前逐行检查；失败行进入 Quarantine。
3. `BatchAudit`：记录 raw/aligned/passed/rejected/fixed/written/new/updated 和批次状态。
4. `DataQualityAuditor`：写库后统一审计 Canonical、批次账本和隔离区；GUI、单任务、批量任务和常驻增量共用。

## 自动检查范围

- schema 表/字段、必填 NULL、regex、enum、`gt/ge` 数值约束；
- 代码格式、毫秒时间戳、未来数据、财务公告日 PIT；
- OHLC、正价格、量额非负、量价单位比例；
- 主键重复、数据源溯源；
- 分钟频率值、任务频率、时间网格；
- 前后复权列完整性、OHLC、倍率一致性、覆盖率、锚点和收益连续性；
- 水位与表内最大业务时间一致性；
- 批次阶段守恒：`aligned = passed + rejected + fixed`（新批次）；
- 写入守恒：`written <= passed`；
- 最近失败批次和被拒数据的 Quarantine 可追踪性；
- Quarantine 记录完整性和 `pending_repair` 积压。

GUI “统一契约审计”显示同一份报告，额外的快速 SQL 项用于交互式定位样本，不定义第二套质量口径。

## 外部基准检查

以下项目必须使用供应商/交易所/PTrade 基准：

- 跨供应商价格、成交量、估值和财务值；
- PTrade 聚源与本地源的复权/流通市值排名边界；
- 逐笔信号、持仓、净值和有效费率。

使用 `FidelityComparator` 的 L1-L4 报告进行验证；成本项按有效费率（费用/成交额）比较，避免跨源整手数量差异造成假阳性。

## GUI task result versus full-database audit

A manual PyQt task has two independent results: the selected task's fetch/align/validate/write result and the post-run full-database `DataQualityAuditor` result. A successful task with an unrelated global audit error is displayed as **success with audit warning**; a real task failure remains a failure. The audit is never skipped.

## Reference-table and QFQ audit contract (2026-08-03)

- `stock_basic` is a canonical reference table created and migrated by `DuckDBWriter`. MCP preserves authoritative `ts_code`, derives the six-digit `code` from `symbol`, and stores `list_status`/`data_source` for metadata APIs.
- `trade_calendar` is shared by MCP and QFQ. It retains the existing single `cal_date` primary key and BOOLEAN `is_open`; `exchange='SSE'` and `pretrade_date` are metadata columns and do not change QFQ calendar queries or lifecycle behavior.
- Persisted enum checks compare native DuckDB values through bound parameters. BOOLEAN values therefore satisfy a schema enum of `[0, 1]` without text-cast false positives.
- `QfqPendingSla` measures how long the row has remained in its current state using `COALESCE(updated_at, created_at)`, not the original event creation time.
- QFQ queue errors must be repaired through the orchestrator CLI (`retry-due`, then reviewed `reopen` operations). Deleting queue rows or raising thresholds to hide failures is prohibited.

## MCP completeness and normalization checks (2026-08-04)

1. **Routing gate**: only exact `(table, freq)` entries in `MCPAdapter._QFQ_ADJFACTOR_TABLES` may synchronize factors and restore qfq prices.
2. **Completeness gate**: non-export pulls must report `fetch_mode=fetch_page` and `lineage.pages`; large mapped datasets must report `fetch_mode=export` plus Raw Landing shards. Never treat `query_snapshot(limit>10000)` as complete.
3. **Composite-code gate**: aligned `index_constituents.index_code` and member `code` must both be six-digit raw codes. Metadata must record filtered out-of-contract `Hxxxxx.CSI` rows.
4. **Nullable-time gate**: `NaT`/missing timestamps become null and are handled row by row by `RequiredValueNull`, `DateValid`, and PIT rules. The conversion helper must not abort the batch, and no synthetic date is allowed.
5. **GUI result boundary**: the collection result and the subsequent full-database QualityAudit are separate outcomes. Audit warnings cannot overwrite a real task failure or be reported as collection success.

Evidence: `docs/evidence/mcp_pipeline_routing_repair_2026-08-04.md`.

Post-repair formal read-only audit: **315 checks / 0 errors / 9 warning classes**. Warnings remain visible and are not treated as collection success or silently cleared.
