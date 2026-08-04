# 线1：is_qfq 还原 raw 方案 — 验收报告

- **日期**：2026-08-03
- **任务书**：`docs/mcp_migration/is_qfq_restore-raw-task.md`
- **审核方**：ZCode（对照 codex 硬条件逐条核查）
- **方案本质**：QuestDB 云端存的是 **qfq（前复权价）** 而非 raw；MCP 管线若直接消费 qfq，会经 aligner 的 `front = raw × adj_i / adj_latest` 二次复权（双重复权）。adapter 侧还原 `raw_i = qfq_i × adj_latest_global / adj_factor_i` 后，管线永远吃 raw + adj_factor，走 aligner 标准路径。
- **状态**：本地实现完成，离线黄金断言全绿（PASS=8 / SKIP=2 / WARN=1）。待 ZCode 审核 → 用户确认后推 GitHub（铁律#1）。

---

## 1. 改动文件清单

| 文件 | 改动 | 性质 |
|------|------|------|
| `quantstudio/pipeline/sources/mcp_adapter.py` | 新增 `_restore_to_raw` / `_get_adj_latest_global` / `_query_adj_latest` / `_coldstart_adj_factors` + 接入 `_fetch_export` / `_fetch_small_table`；常量块 `_RESTORE_PRICE_COLS` / `_RESTORE_TABLES` / `_RESTORE_MISSING_FACTOR_FAIL_FAST`；`__init__` 新增 `enable_qfq_restore` / `enable_adj_coldstart` / 缓存属性；helper `_as_bool_qfq` / `_bare_code` / `_asset_type_of` | 框架层（铁律#1） |
| `quantstudio/pipeline/daemon.py` | 两处（见 §5）：① L584 MCP adj_factor 分支表集合补 `etf_minutes`；② `batch_audit.record` 两路径（普通 + per_stock）`rows_fixed` 并入还原行数（P1-⑥ 追溯） | 框架层（铁律#1） |
| `scripts/verify_mcp_qfq_restore.py` | 新增：离线黄金断言骨架（A1–A9 + A6b），双重复权反例，护栏验证 | 测试 |
| `docs/mcp_migration/mcp_contract_v1_draft.md` | B2.4 新增 fetch_table 返回 metadata 契约（还原字段） | 文档（铁律#1） |
| `docs/mcp_migration/mcp_protocol_probe.md` | §7.4 新增「线1：is_qfq 还原 raw」方案 + 已知限制（front 锚不同） | 文档（铁律#1） |
| `README.md` | 「数据采集」节后新增「MCP 数据源与 is_qfq 还原（线1）」简述 | 文档（铁律#1） |

> 不改：`aligner.py`（front=raw×adj_i/adj_latest 不变）、`writer.py`、`mcp/server.py`、传统 tushare/baostock 模式；不改线2 全量表配置；密码不回显。

---

## 2. 黄金数字（验收基准）

| 项 | 值 | 容差 | 实测 | 结论 |
|----|----|------|------|------|
| 300750 4-21 还原 raw close | 231.36 | 0.01 | 231.3604（偏差 0.0004） | ✅ |
| 300750 4-22 还原 raw close | 230.69 | 0.01 | 230.6903（偏差 0.0003） | ✅ |
| 全局最新 adj_factor（云端系列） | 1.9495 | — | 1.9495 | ✅ |
| tushare 系列最新 | 1.9816 | — | 1.9816（差 1.6%，设计内） | ✅ |

还原公式：`raw_i = qfq_i × adj_latest_global / adj_factor_i`
- 4-21：222.519 × 1.9495 / 1.8750 = 231.3604
- 4-22：226.312 × 1.9495 / 1.9125 = 230.6903

---

## 3. 离线验证结果（PASS=8 / SKIP=2 / WARN=1）

运行：`python scripts/verify_mcp_qfq_restore.py --duckdb-compare --sample-n 20`（系统 Python 3.11.9）

| 用例 | 对应硬条件 | 结果 | 说明 |
|------|-----------|------|------|
| A1 全局最新因子=1.9495 | P0-① | **PASS** | 取数走 `_get_adj_latest_global`（qfq_aux.db 全局最新），非分片末行 |
| A2 4-21 qfq→raw=231.3604 | P0-② | **PASS** | 偏差 0.0004 ≤ 0.01 |
| A3 4-22 qfq→raw=230.6903 | P0-② | **PASS** | 偏差 0.0003 ≤ 0.01 |
| A2b 非价格列不还原 | P1-④ | **PASS** | vol/amount/pct_chg 原样保留 |
| A4 2024-06 窗口还原=202.5001 | P0-① | **PASS** | 反例：vs 误用分片末行(1.8660)得 193.8267，**差 8.6734 元** |
| A5 追溯字段写入 metadata | P1-⑥ | **PASS** | `restored_rows=2`，含 is_qfq_col_present / adj_latest_source / restore_formula 等 |
| A6b is_qfq=False 行不还原 | P1-③ | **PASS** | 输入 231.36 → 输出 231.36（原样） |
| A9 过期锚 fail-fast | 护栏 | **PASS** | adj_i=2.05 > 全局最新 1.9495 → 拦截并报可操作提示 |
| A6 全市场往返对照（DuckDB raw） | P1-③ | **SKIP** | 主库被常驻 qfq_orchestrator 独占，按纪律降级 SKIP（逻辑就绪，待锁释放重跑） |
| A7 ETF fund_adj 覆盖 | P1-⑤ | **WARN** | `fund_adj rows=0`：ETF 还原当前无因子源，必触发冷启动（非 bug，见 §6） |
| A8 在线端到端 | — | **SKIP** | 未启用 `--online` |

---

## 4. 最易错点 P0-① 反例详述（2024-06-03）

**错误做法**：从本次 export 分片取 `MAX(time)` 那行的 adj_factor 当全局最新。
**后果**：历史窗口拉取时分片末行（2024-06-26~06-28）因子 = 1.8660，远早于今日；若误用：
```
错误 raw = qfq_close × 1.8660 / 1.8666 ≈ 193.8267
正确 raw = qfq_close × 1.9495 / 1.8666 = 202.5001
差异 = 8.6734 元（系统性偏差，非 tick 级）
```
**正确做法**：`_get_adj_latest_global` 一律从 qfq_aux.db 完整因子历史取 `ORDER BY time DESC LIMIT 1`（300750 → 1.9495），与本次 export 日期窗口无关。已验证 A4 通过。

> 注：任务书附录 C.3 写「分片末行 adj_factor=1.8660」，实测 2024-06-03 当日因子为 1.8666（6-03~6-24=1.8666，6-25=1.8662，6-26~6-28=1.8660）。反推 qfq_close=193.8890 → 正确 raw=202.5001、误用末行=193.8267，差 8.6734 元，与任务书吻合。

---

## 5. daemon.py 两处改动（配套，否则 etf_minutes 普通路径失效）

**改动 ①（L584）**：MCP adj_factor 提取分支表集合由
`("stock_daily", "stock_minutes", "etf_daily")` 扩为
`("stock_daily", "stock_minutes", "etf_daily", "etf_minutes")`，与 per_stock 路径 L1252 对称。
- 背景（ZCode 审核发现）：原普通路径漏 `etf_minutes`，导致 etf_minutes 普通模式即便 adapter 已还原 raw，daemon 也不提取 adj_factor 传 aligner → 复权字段留 NULL，验收#5 无法验证。
- 其余逻辑（normalize_mcp_adj_factor_df 提取 / 列名重命名 / raw_df drop 原始 adj_factor 列）保持不变。

**改动 ②（batch_audit.record 两路径）**：`rows_fixed` 并入 MCP qfq→raw 还原行数（P1-⑥ 追溯）：
- 普通路径：`rows_fixed = (res.fixed_count or 0) + int(metadata.get("restored_rows", 0) or 0)`
  - 采用「叠加」而非「替换」：非 MCP 源（tushare/baostock）`restored_rows` 缺省 0，等价于原 `res.fixed_count`，**不回归回退路径**；MCP 源额外计入还原行数。
- per_stock 路径：新增 `total_restored[0]` 累加器（在 `process_one` 捕获 `metadata.restored_rows` 聚合），`rows_fixed = total_fixed[0] + total_restored[0]`。
- 注：`_execute_task_per_trade_date`（PER_DATE，tushare 日线专用）不在本次范围（无 MCP 还原元数据），保持 `rows_fixed=total_fixed[0]` 原行为。

---

## 6. 已知限制与技术债

1. **front 锚不同（设计内，~1.6%）**：MCP 还原走 qfq_aux.db **云端因子系列**（latest=1.9495），tushare 系列 latest=1.9816，差 ~1.6%。两系列都是合法复权锚，但 MCP 路径产出的 `*_front` 与 tushare 路径 `front` 不会 tick 一致（验收#2 已记录差异预期）。跨源比较 front 需注意锚差异（详见 `mcp_protocol_probe.md` §7.4）。

2. **ETF fund_adj 缺口（WARN A7）**：当前 qfq_aux.db `fund_adj rows=0`，ETF 还原无因子源。
   - 代码路径已具备：首次 ETF 还原尝试触发 `_coldstart_adj_factors("ETF")`（全历史导出 etf_daily → `_inject_adjfactor` → `fund_adj` 表，`_inject_adjfactor` 已正确路由 `etf*` → `fund_adj`）。
   - 冷启动为一次性重操作（每进程每类型仅一次）；在此之前 ETF 还原会 **fail-fast**（缺因子，不静默放行）。
   - 建议：在切换 MCP 为 ETF 主源前，先手动触发一次 ETF 因子冷启动灌库，避免首跑阻塞。

3. **A6 全市场往返对照待补**：DuckDB 主库被常驻 `qfq_orchestrator_cli`（PID 39280，用户正在跑 QFQ 重锚）独占，按纪律不 kill，A6 降级 SKIP。逻辑已就绪（DuckDB raw → 造 qfq → 还原 → 比对），待主库空闲重跑。

4. **P3 复权验证结论重审**：本任务落地的 adapter 侧还原，使「MCP 入库管线完整复现 QFQ 事件驱动闭环（P3-7）」前提成立——还原后的 raw 走 aligner 标准 `front=raw×adj_i/adj_latest`，与既有 tushare 路径同源。但 MCP 闭环复权结果与 xtquant 闭环在 tick 容差内一致的**实测验证（P3-7 验收基线 §7.3）**仍待 `McpFreshFetcher` + 冻结快照对拍（任务书原 §7.4 / §9 待办）。本任务不替代该验证。

---

## 7. codex 硬条件逐条对照

| 硬条件 | 要求 | 本任务落地 | 状态 |
|--------|------|-----------|------|
| P0-① | adj_latest 取全局最新（非分片末行） | `_get_adj_latest_global` + A4 反例验证（差 8.6734 元） | ✅ |
| P0-② | 300750 黄金数字 + front 差异记录 | A2/A3（raw 231.36/230.69）+ 已知限制 §6.1 记 ~1.6% | ✅ |
| P1-③ | is_qfq=False 抽样对照 | A6b（不还原）+ A6 全市场对照（SKIP 待补） | 🟡 部分（A6 待主库空闲） |
| P1-④ | 价格容差 ≤ 0.01 | A2b + A2/A3 偏差 0.0003~0.0004 | ✅ |
| P1-⑤ | etf_minutes fund_adj 系列 | daemon L584 补 etf_minutes；`_inject_adjfactor` 路由 fund_adj；A7 WARN（fund_adj 待冷启动） | 🟡 代码就绪，数据待灌 |
| P1-⑥ | is_qfq 标记追溯 | metadata 追溯字段（A5）+ daemon batch_audit rows_fixed（§5 改动②） | ✅ |
| P2-⑦ | 两模式 front 锚文档 | `mcp_protocol_probe.md` §7.4 已知限制 | ✅ |
| P2-⑧ | metadata 契约文档 | `mcp_contract_v1_draft.md` B2.4 | ✅ |
| 铁律#1 | 本地验证→文档同步→确认→推 GitHub | 文档已同步（§1 清单）；待 ZCode 审核 + 用户确认 | ⏳ 进行中 |
| 铁律#4 | 进度报告更新（含 P3 结论重审） | 待 ZCode 通过后更新 `实时进度报告.md` | ⏳ 进行中 |

---

## 8. 下一步

1. ZCode 审核本报告 + 代码 diff（对照 codex 硬条件）。
2. 审核通过后更新 `实时进度报告.md`（铁律#4，含 §6.4 P3 结论重审、§6.2 fund_adj 技术债）。
3. 汇报用户 → **用户明确确认后**才 commit/push GitHub（铁律#1，包含 README + docs/strategy_toolbox.md + docs/prompt_engineering.md 同步；本次 strategy_toolbox/prompt_engineering 复权口径表述未受本修复影响，无需改动，已核对）。
4. 待 DuckDB 主库空闲后重跑 A6 全市场往返对照，补齐 P1-③ 全量证据。


## 2026-08-03 ETF non-monotonic-factor correction (PyQt full pull)

- Batch `mcp_etf_daily_mcp_20260803_234326_0b47a6` downloaded, restored, and aligned 2,093,147 rows, but validation rejected 100,501 rows (4.801431%).
- Root cause was not PyQt or MCP transport. Commit `a6fef1b` changed the restore anchor from the factor at `MAX(time)` to historical `MAX(adj_factor)`. For 510500, latest=0.3401 while historical max=1.0, inflating current raw price by about 2.94x.
- Repair: restore the factor at `MAX(time)`; remove the invalid monotonicity assumption; fail fast when factor-snapshot synchronization cannot write; keep UnitCheck on canonical raw close rather than `close_front`.
- Full 2,093,147-row Parquet analysis: historical-max anchor caused 28,871 UnitCheck anomalies; correct latest-time anchor leaves 1,973 genuine anomalies (0.0943%, below the 1% gate). Real first/last samples for 510500/560010/563330/511030 have unit ratios in [0.9950, 1.0047].

## 7. Routing/pagination/normalization addendum (2026-08-04)

This addendum belongs to the same MCP framework repair work package. Detailed evidence is in `docs/evidence/mcp_pipeline_routing_repair_2026-08-04.md`.

- The isolated `index_constituents` pipeline fetched 251,947 rows, filtered 27,740 out-of-contract `Hxxxxx.CSI` rows, and wrote 224,207 canonical rows without QFQ factor access.
- The isolated `index_daily` and `block_trade` pipelines wrote 64,373 and 61,195 rows, proving the production path no longer stops at the 10,000-row snapshot cap.
- `fin_indicator` no longer aborts on `NaT`: 135,840 rows were written and 111 invalid source rows were quarantined by `DateValid`/`RequiredValueNull`.
