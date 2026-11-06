---
title: 8a6de2d..74b4181 三十五笔逐笔归属清单（授权快照更新附件）
date: 2026-11-06
purpose: 依用户裁定一（甲）——更新授权快照 tip→74b4181，逐笔标注归属线名，供总调度复核
---

# 一、汇总（⚠ 更正此前口径）

> **此前我方口径「我方 3 + 他线 32」不准确，特此更正。**
> 实际：**本线（策略研发）15 笔 + 他线 20 笔 = 35 笔**。
> 差因：此前只把「性能三笔」算作我方，**遗漏了本线同期产生的 12 笔审核台账/方案 docs**。

| 归属线 | 笔数 |
| --- | --- |
| **策略研发线（本线）** | **15**（性能 3 + 审核台账/方案 12）|
| QFQ / 数据线 | 10 |
| QMT 转换管线线 | 6 |
| PTrade 部署线 | 3 |
| 共享层·授权闸 | 1 |
| **合计** | **35** |

# 二、逐笔清单（时间正序）

| # | sha | 时间 | 标题（截断）| 改动面 | 归属线 |
| --- | --- | --- | --- | --- | --- |
| 1 | `66ba57d` | 11-05 15:39 | docs: QMT 停牌判据方案＋QD-2026-0021 | docs/qmt-status-halt-criterion-fix-design-20261105.md, knowledge/qmt/QD-2026-0021.md | QMT 转换管线线 |
| 2 | `1c60b3b` | 11-05 15:43 | fix(gate): 授权快照 UTF-8 读取 | scripts/prepush-gate.ps1 | 共享层·授权闸（图调度/dev） |
| 3 | `7d10729` | 11-05 16:29 | docs(p2)+kb: P2 负结果＋DB-2026-0029 | docs/qfq-invariant-p1p2-*.md, knowledge/db/DB-2026-0029.md | QFQ/数据线 |
| 4 | `c905282` | 11-05 16:41 | fix(qmt): 停牌判据移除 IsTrading 误用 | source_import_qmt.py, tests/test_qmt_*.py×3, docs/evidence/*qmt* | QMT 转换管线线 |
| 5 | `a193179` | 11-05 17:26 | docs: T-A~T-D 落实（W1 入件＋候裁台账＋两案自查） | docs/backtest-cli-guard-*, docs/decisions-pending.md, docs/handoff/1104-submission-pack, two-docs-precheck | **策略研发线（本线）** |
| 6 | `caed733` | 11-05 17:49 | docs: 批 0 前置取证（两处「复用」不成立） | docs/handoff/batch0-preflight-20261105.md | **策略研发线（本线）** |
| 7 | `542ab68` | 11-05 17:49 | docs: 合并判据补 R3 | docs/join-key-daykey-unify-design-20261101.md | **策略研发线（本线）** |
| 8 | `9d4b812` | 11-05 20:38 | docs: 修订编号 R3→R4＋precheck 第 7 项 | two-docs-precheck, join-key-daykey-unify-design | **策略研发线（本线）** |
| 9 | `cfbdd67` | 11-05 20:38 | docs: QMT 六项缺陷分诊＋N2 时区方案 | docs/qmt-iface-defect-triage-and-tz-shim-design-20261105.md | QMT 转换管线线 |
| 10 | `cfe36cb` | 11-05 21:27 | fix(qmt): 时区垫片修 EPS 静默失效（QD-2026-0022） | source_import_qmt.py, tests/test_qmt_tz_shim.py, knowledge/qmt/QD-2026-0022.md | QMT 转换管线线 |
| 11 | `b33d31a` | 11-05 22:09 | docs(state): PROJECT_STATE 追加 QMT 线快照 | docs/PROJECT_STATE.md | QMT 转换管线线 |
| 12 | `e459036` | 11-05 22:12 | docs(wm-freeze): 链①②审裁定＋W2/W3/W5 确认 | docs/wm-freeze-fix-design.md | QFQ/数据线（水位冻结） |
| 13 | `ab414a5` | 11-05 22:23 | docs(wm-freeze): §11.3 双档权威口径 | docs/wm-freeze-fix-design.md | QFQ/数据线（水位冻结） |
| 14 | `017a655` | 11-05 22:56 | docs: 性能优化审核方基准落盘 | docs/handoff/perf-optimization-review-baseline-20261105.md | **策略研发线（本线）** |
| 15 | `02c00df` | 11-05 23:54 | docs: 审核基准修正 R1（DB 非瓶颈收窄） | 同上 | **策略研发线（本线）** |
| 16 | `a15ec41` | 11-06 00:16 | docs: 方案③ QFQ post-ingest v1 | docs/qfq-post-ingest-incremental-design.md | QFQ/数据线 |
| 17 | `819a8fa` | 11-06 00:37 | docs: R-PERF-02 裁定（准进④·C1-C5） | perf-optimization-review-baseline | **策略研发线（本线）** |
| 18 | `75cbda5` | 11-06 01:27 | docs: 方案③ v2（T1/T2/T4 闭合） | docs/qfq-post-ingest-incremental-design.md | QFQ/数据线 |
| 19 | `3a36a9a` | 11-06 01:31 | docs(evidence,kb): PD-0017 E′ 验证点订正 | docs/evidence/ptrade-deploy-pd0017-evidence.md, knowledge/ptrade-deploy/PD-2026-0017.md | PTrade 部署线 |
| 20 | `d7af64f` | 11-06 01:56 | docs: R-PERF-02 ④裁定（C2 通过/C1b 准开工） | perf-optimization-review-baseline | **策略研发线（本线）** |
| 21 | `831cbca` | 11-06 02:20 | docs: PD-0017 E′ 加速替代路径备案 | ptrade-deploy 证据/知识 | PTrade 部署线 |
| 22 | `b0c4af9` | 11-06 02:20 | docs: R1R2补报+C同族+C1b③ 三呈请裁定 | perf-optimization-review-baseline | **策略研发线（本线）** |
| 23 | `b1e2a76` | 11-06 02:22 | docs: 方案③ v3（G12 走 aux 游标表） | docs/qfq-post-ingest-incremental-design.md | QFQ/数据线 |
| 24 | `be31508` | 11-06 03:14 | docs(evidence): PD-0017 撤单呈批前提订正 | docs/evidence/ptrade-deploy-pd0017-evidence.md | PTrade 部署线 |
| 25 | `a0e0600` | 11-06 03:47 | feat(qfq): 方案③ G1–G4 增量路径落地 | qfq_observation.py, tests/test_qfq_observation_incremental.py, docs/evidence/qfq-incremental-impl-* | QFQ/数据线 |
| 26 | `fc805e9` | 11-06 04:10 | docs(evidence): 6 红归因实证销项 | docs/evidence/qfq-incremental-impl-g1-g4-20261106.md | QFQ/数据线 |
| 27 | `aa90760` | 11-06 04:53 | docs: 方案③ v3.1（G5b 硬门） | docs/qfq-post-ingest-incremental-design.md | QFQ/数据线 |
| 28 | `0051a68` | 11-06 09:54 | docs: C1b④全窗FAIL裁定（门设计双缺陷） | perf-optimization-review-baseline | **策略研发线（本线）** |
| 29 | `3872ca5` | 11-06 10:27 | docs: 审核方工作指示 R-PERF-03A | perf-optimization-review-baseline | **策略研发线（本线）** |
| 30 | `07b45ee` | 11-06 11:29 | feat(qfq): 方案③ G12 增量水位游标 | qfq_observation.py, qfq_reanchor_schema.py, tests/test_qfq_observation_incremental.py | QFQ/数据线 |
| 31 | `22d1c9e` | 11-06 11:37 | docs(qmt)+kb: V7 真机通过＋QD-2026-0022 关闭 | docs/PROJECT_STATE.md, knowledge/qmt/QD-2026-0022.md | QMT 转换管线线 |
| 32 | `6b7eb7a` | 11-06 11:38 | docs: R-PERF-03A 归因裁定（改判口径错位） | perf-optimization-review-baseline | **策略研发线（本线）** |
| 33 | `5827f2d` | 11-06 14:45 | perf(backtest): C2 get_stock_status 向量化 | quantstudio/backtest/ptrade_api.py | **策略研发线（本线·性能·本批授权目标）** |
| 34 | `8d8f5f8` | 11-06 14:46 | fix(backtest): filter_stock_by_status 重复码封堵 | quantstudio/backtest/ptrade_api.py | **策略研发线（本线·性能·本批授权目标）** |
| 35 | `74b4181` | 11-06 14:47 | perf(backtest): C1b get_bars 日线批量化 | duckdb_data_access.py, duckdb_provider.py | **策略研发线（本线·性能·本批授权目标）** |

# 三、需总调度复核的不确定项

| # | sha | 疑点 | 我方判断 |
| --- | --- | --- | --- |
| 1 | `1c60b3b` | `scripts/prepush-gate.ps1` 属共享层，归「图调度/dev」还是「运维」未定 | 建议记「共享层·授权闸机制」不细分线 |
| 2 | `b33d31a`/`22d1c9e` | `docs/PROJECT_STATE.md` 是跨线状态文件，但内容为 QMT 线快照 | 按内容归 QMT 线 |
| 3 | `e459036`/`ab414a5` | `wm-freeze-fix-design.md` 属水位冻结，与「数据线」是否同线待确认 | 暂归「QFQ/数据线（水位冻结子项）」|

# 四、快照更新建议参数

| 参数 | 值 |
| --- | --- |
| prev_tip | `8a6de2d` |
| new_tip | `74b4181` |
| declared | 35 笔（见§二） |
| 硬件条件 | `74b4181` 为本地 HEAD 的祖先、`8a6de2d` 为其祖先 ⇒ **fast-forward 成立** |
| 不含 | `c6699fc`（他线 11-06 14:47 新增，候其自行出仓）|