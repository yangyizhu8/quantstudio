# 质量审计 OOM + 全库审计粒度 + 批量执行可观测性 —— 方案 v2（两阶段）

- 起草：2026-10-30（客户运维线）
- 客户：不爱热闹
- 触发材料：客户 AI 排查反馈（`once_logs.zip` 1.1MB + 4 段读数 + OOM 栈）
- 版本：209a732（2026-10-29，含 T2 委托化）
- 判型：**Phase 1 = 纯恢复型（修 OOM/性能灾难，行为回到「审计能跑完」）**；**Phase 2 = 纯增益型（新增观测与保护）**

---

## 一、背景与动机

### 1.1 客户侧新证据（四条，均已核实于代码）

| # | 证据 | 代码核实 |
|---|---|---|
| E1 | 3 个任务同栈崩溃：`quality_audit.py:480 _audit_prices → _duckdb.OutOfMemoryException: 12.4 GiB/12.5 GiB` | ✅ `quality_audit.py:480-487`：`SELECT *` + **两个 ROW_NUMBER 全表排序** 物化 CTE |
| E2 | `factor_value_pt` 审计硬啃 **16 小时**才 OOM（10-29 17:18 → 10-30 09:21） | ✅ 同一函数，无超时、无分批、无内存上限 |
| E3 | 每任务间隔 5–9 分钟（73 个任务） | ✅ `daemon.py:775-776`：`run_quality_audit=True` ⇒ **每任务跑一次全库审计** |
| E4 | `est_total=480000000`（4.8 亿）被客户据此推算 ETA≈8 天 | ✅ `mcp_adapter.py:908`：`est_rows or _EXPORT_ROW_ESTIMATE.get("stock_minutes", 480_000_000)` ⇒ **服务端未返回 est_rows 时用兜底常数** |

### 1.2 主因链（四因叠加，非单一）

```
① stock_minutes 增量窗口 2026-09-18 → 2026-10-30（约 6 周）
     + 兜底 est_total=4.8亿（服务端未给 est_rows）
     + 实测 ~4.2 万行/分（calls_per_min 限速）
     ⇒ 真实跑数时长以「天」计（客户观测 72 秒/5 万行，稳定推进）

② 每任务执行完跑「全库审计」（quality_audit full × 73 次）
     ⇒ 单任务 5–9 分钟被审计吞掉大部分；某表更大时 → 16 小时

③ 审计内 `_audit_prices` 用 `SELECT *` + 双 ROW_NUMBER 全表排序
     ⇒ 60GB 库 + 12.5GiB 上限 ⇒ OOM（12.4 GiB 触顶）

④ 审计 OOM ⇒ 任务被判 failed（数据已写入成功）→ 客户看到「失败」
     + WAL 531MB 拖慢 UI 读库 + 执行期零进度刷新
     ⇒ 综合观感「两天没反应」
```

### 1.3 客户影响

- 数据写入本身成功（客户 AI 判断正确：OOM 在写后审计阶段）；
- 但**审计 100% 失败**（大库必炸）+ **每任务全库扫描**（性能灾难）⇒ 采集链路「跑得完但永远判红」，且一个任务被审计拖 16 小时会让整条串行队列停摆；
- 客户无法区分「数据成功/审计失败」，误判为数据损坏。

---

## 二、范围与边界

### Phase 1（P0 · 本次必做）

| 改动文件 | 内容 |
|---|---|
| `quantstudio/pipeline/quality_audit.py` | ①`_audit_prices` 的 `AdjustmentAnchor` 查询重写（去 `SELECT *`、单窗或预聚合、分批兜底）；②审计级内存上限与降级（OOM ⇒ 记 `audit_skipped_oom` 而非抛穿）；③审计超时保护（单表超时 ⇒ 跳过该表并记录） |
| `quantstudio/pipeline/daemon.py` | ④审计范围收窄：`quality-audit full` 改为按**本任务涉及的表 + 全局段**审计（而非全库 60GB）；保留 `full-all` 显式开关走旧行为 |
| `quantstudio/pipeline/sources/mcp_adapter.py` | ⑤`est_total` 来源可辨识：兜底时日志显式标注 `est_total=480000000(fallback)`，避免被误读为真实需求 |
| `tests/test_quality_audit_oom.py`（新增） | A1–A5 机检 + 负向 |
| `docs/evidence/quality-audit-oom-20261031.md`（新增） | 验收证据档 |

### Phase 2（P1/P2 · 观测面）

见既有方案 `docs/incremental-runall-observability-design.md`（F1–F7：心跳/停滞告警/文案/可选中超时/跳过入口/汇总/防回归），**原样沿用，不重复设计**。

### 不改动范围（硬边界）

- **不改**写入路径：`writers.py`、`stock_minutes` upsert、水位推进、锁语义零改动；
- **不改**审计判据口径：所有检查项与阈值不变，只改「怎么算」与「算不完怎么办」；
- **不改** manifest 契约与 GUI 委托协议；
- **不改**默认 `--quality-audit full` 语义（仍为「审计，但只审本任务相关表 + 全局段」；`full-all` 保留旧全库行为）；
- **不允许**以「跳过审计」冒充修复：OOM/超时必须**显式留痕**（`audit_skipped_oom` / `audit_timeout`），且报告可查。

### 兼容与回退

| 开关 | 作用 |
|---|---|
| `QS_AUDIT_SCOPE=task`（默认）/ `all` | `all` = 逐位还原旧「全库审计」行为 |
| `QS_AUDIT_TABLE_TIMEOUT_S`（默认 300） | 单表审计超时；0 = 不限（旧行为） |
| `QS_AUDIT_MEMORY_LIMIT` | 审计连接 `SET memory_limit`（默认不设，走 DuckDB 默认） |
| `QS_AUDIT_ANCHOR_MODE=new`（默认）/ `legacy` | `legacy` = 逐位还原旧 `SELECT *` + 双窗口 |

---

## 三、任务拆解

### Phase 1

| 点 | 功能 | 落点 | 独立验收判据 | 负向对照 |
|---|---|---|---|---|
| **A1** | `AdjustmentAnchor` 查询重写：改用 `arg_min/arg_max` 预聚合取首尾，避免 `SELECT *` 与双 ROW_NUMBER | `quality_audit.py:_audit_prices` | 合成 100 万行表（20 列）跑新旧两版，**结果 `anchor` 计数逐位相同**；峰值内存（`duckdb` `PRAGMA database_size` 或进程 RSS 采样）**下降 ≥50%** | 构造 1 行首条 close_front 偏差 3% 的样本 ⇒ 新版必须检出（计数 +1）；`QS_AUDIT_ANCHOR_MODE=legacy` 时新旧结果**逐位一致** |
| **A2** | 审计 OOM 降级：捕获 `OutOfMemoryException` ⇒ 该表记 `audit_skipped_oom`，不抛穿 | `quality_audit.py:run()` 表循环 | 设 `QS_AUDIT_MEMORY_LIMIT=256MB` + 大表 ⇒ 报告含 `audit_skipped_oom` 条目、`run()` **正常返回** | 未设限制时同样输入 ⇒ 无 `audit_skipped_oom`（不误记） |
| **A3** | 单表审计超时：`QS_AUDIT_TABLE_TIMEOUT_S`（默认 300）⇒ 超时记 `audit_timeout` 并跳过该表 | 同上 | 设 1s + 构造慢查询 ⇒ 300ms~2s 内该表被跳过并留痕 | 设 0 ⇒ 不跳过（旧行为），跑完不误记 |
| **A4** | 审计范围收窄：`QS_AUDIT_SCOPE=task` 默认只审本任务表 + 全局段 | `daemon.py:775-776` + `quality_audit` 入参 | 单任务 `mcp_etf_daily` once ⇒ 审计报告表数 == 该任务表数（1）+ 全局段数，**不含 stock_minutes 等无关表** | `QS_AUDIT_SCOPE=all` ⇒ 报告表数 == 全库表数（逐位还原旧行为） |
| **A5** | `est_total` 兜底标注 | `mcp_adapter.py:906-948` | 服务端无 `est_rows` ⇒ 日志含 `est_total=480000000(fallback)` | 服务端给 `est_rows=1234` ⇒ 日志含 `est_total=1234`（无 fallback 后缀） |
| **A6** | 任务结果口径：审计 OOM/超时**不**把任务判 failed（数据成功则 ok），但报告与 UI 必须显红「审计未完成」 | `daemon.py` 审计结果消费处 | 审计返回含 `audit_skipped_oom` ⇒ 任务 `task_ok=True` 且 result 带 `quality_audit_incomplete=True` | 审计全过 ⇒ `quality_audit_incomplete` 不存在（键不出现） |
| **A7** | 防回归 | `tests/test_quality_audit_oom.py` | ≥10 用例全绿；`pytest tests/` 全绿 | 把 A2 的 except 去掉 ⇒ A2 用例必须**红** |

### Phase 2（沿用既有方案 F1–F7，逐点门控不变）

### 门控纪律

A1 → A2 → A3 → A4 → A5 → A6 → A7 逐点过；任一点红即停在该点不扩散。
Phase 1 全绿并验收后，再进 Phase 2。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **A1 重写导致审计口径漂移** | 审计结果与旧版不一致 ⇒ 误报/漏报 | 判据强制「新旧 `anchor` 计数逐位相同」+ `legacy` 开关；差异即验收失败回退 |
| **A4 收窄漏审跨表问题** | 某些跨表一致性检查（如 minute vs daily 对齐）依赖多表同审 | 全局段（`segment:*`）**不在收窄范围**（仍全跑）；跨表检查列保留；`QS_AUDIT_SCOPE=all` 可全审 |
| **A2/OOM 降级掩盖真问题** | 「跳过」看起来像通过 | 报告条目 severity 固定 `warning` 且 **UI/日志显红**；A6 保证 result 带 `quality_audit_incomplete` |
| **A6 改变任务成败语义** | 监控/告警口径变化 | 新增字段而非改 `task_ok` 语义；`quality_audit_incomplete` 显式存在即可被监控消费 |
| **超时值过紧误跳** | 大表被误跳 | 默认 300s；可配；超时留痕可查；不影响写入 |
| **客户侧 WAL 531MB** | 运行期无法收敛（红线：daemon 在写禁 CHECKPOINT） | 不属本次改动；给客户口径「停机后跑 `check_wal.ps1`」，不在运行期动 |

---

## 五、验收要点

### 功能验收
- A1–A7 全绿；`pytest tests/` 全绿（含既有 42 条 minutes/writers 与 GUI 相关）。

### 场景验收（本机可复跑）
1. **大表审计不 OOM**：构造 500 万行 `stock_daily`-结构表，`QS_AUDIT_MEMORY_LIMIT=2GB` 跑审计 ⇒ 不崩，结果正确或显式 `audit_skipped_oom`；
2. **新旧口径一致**：同一合成库跑 `QS_AUDIT_ANCHOR_MODE=new` vs `legacy` ⇒ `AdjustmentAnchor` 计数**逐位相同**；
3. **收窄生效**：单任务 once ⇒ 审计表数显著减少（对比 `all`）；
4. **OOM 不判任务失败**：限内存触发 OOM ⇒ 任务 `task_ok=True` + `quality_audit_incomplete=True`。

### 客户侧验收（观察窗）
- 客户更新后重跑一个任务（如 `mcp_etf_daily` 单行增量）⇒ 任务成功、审计完成、不再 OOM；
- 观察 1 轮日常批次，确认 `audit_skipped_oom` / `audit_timeout` 零出现（若出现则查该表规模）。

---

## 六、质量判据

1. **OOM 结构性消除**：`_audit_prices` 不再用 `SELECT *` + 双全表排序；即使大表也走预聚合；
2. **不再「一个审计拖死整条队列」**：单表超时 + 范围收窄 + 每任务不再全库扫；
3. **「算不完」必须显式**：OOM/超时留痕（`audit_skipped_oom`/`audit_timeout`/`quality_audit_incomplete`），禁止静默吞；
4. **口径零漂移**：审计判据与阈值不变，`new` vs `legacy` 逐位一致；
5. **可审计**：证据档 + 复跑命令 + 输出摘录落 `docs/evidence/`。

---

## 附一：知识反哺检索留痕（N8 第二轮）

- `知识反哺：[命中] 已知错误-GUI启动卡死-WAL生命周期（12-客户运维/已知错误-，2026-10-30）`——**同族缺陷第二次出现**（该案为「构造期 duckdb 连接遇大 WAL 慢的成功」；本案为「审计查询无 deadline 无内存上限」）⇒ 复用其「慢的成功必须 deadline + 显式降级」原则，并在本案升格为通用判据。
- `知识反哺：[无命中] 12-客户运维/已知错误（质量审计 OOM / 审计粒度族）、12-客户运维/案例卷宗（审计 OOM 拖死串行队列），2026-10-30`

## 附二：客户侧即时口径（供 N1 止血转发）

| 问 | 答 |
|---|---|
| 要不要停 daemon？ | **建议不停**：`stock_minutes` 在稳定推进（72 秒/5 万行），停了且水位未提交则该段白跑；等它跑完或按需再停 |
| WAL 531MB 要紧吗？ | 运行期正常（写入累积），**不可运行期收敛**（红线）；daemon 正常退出/轮次收尾会自动 CHECKPOINT；停机后可跑 `check_wal.ps1` 兜底 |
| 数据丢了吗？ | 大概率没丢：3 次 OOM 都在**写后审计**阶段，主写入成功；可用任务行「水位线」核对推进 |
| 现在能用库吗？ | 能读但慢（WAL 大）；急用可停 daemon 后跑 `check_wal.ps1` 再读 |
| 8 天 ETA 可信吗？ | **不可全信**：`est_total=4.8 亿` 是服务端未返 `est_rows` 时的**兜底常数**，非真实需求；真实进度看日志「wrote N rows」累计 |
