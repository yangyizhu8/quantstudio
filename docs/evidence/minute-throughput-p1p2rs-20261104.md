# 分钟采集吞吐三件套 · 实施与验收（P1 + P2 + REPLAY-SKIP）

> 日期：2026-11-04 | 节点⑳ | 状态：**第 1–6 步完成，待第 7 步（daemon 启动）单独授权**
> 回退点：`945edc47`（baseline-qfq-perf-20261104，铁律 03 四步配方）
> 提交：P1 `11a4df9` / P2 `f97d28f` / RS `49121ae`

---

## 1 P1 · 因子查询加时间窗口

| 项 | 读数 |
|---|---|
| 改动 | `_load_factor_lookup` 增 `day_lo_ms/day_hi_ms`；`check_qfq_invariant` 由抽样行 time 推导 ±3 天窗并下传；开关 `QS_QFQ_INVARIANT_FULL_FACTORS` |
| **等价性实测（真实 11.2GB aux 库只读）** | `adj_factor` 338,298 键/12.32s → **613 键/0.16s = 76.6×**，值逐键相同 ✓<br>`fund_adj` 16,302 键/3.65s → **694 键/0.06s = 60.0×**，值逐键相同 ✓ |
| 测试 | `tests/test_qfq_invariant_window.py` **7 passed** |

**极端边界（审核补充条件 1）实测语义**：长期停牌复牌（因子在窗口外）⇒ 落
`skipped` + `missing_factor_rows`（**不是** `unknown_rows`），`bad=0`、不抛异常。
该语义**非本改动引入**（原全历史路径同样如此），比 `unknown` 更精细；
**属设计语义，不是缺陷**，测试注释与本节显式记录，防将来误报。

## 2 P2 · 单周期因子查询缓存

| 项 | 读数 |
|---|---|
| 改动 | `check_qfq_invariant` 增 `lookup_fn` 注入点；daemon 新增 `_qfq_factor_lookup_cached`（键=table+窗口+code 指纹 sha1，LRU 8）/ `_qfq_invalidate_factor_cache` / `_qfq_factor_cache_report`；`qfq_run_post_ingest` 进入前**显式失效**；开关 `QS_QFQ_INVARIANT_FACTOR_CACHE` |
| **审核补充条件 2 落实** | **不靠 mtime 轮询**（post-ingest 每次写因子都会变 mtime ⇒ 逐批 stat 必永久 miss）。改为显式事件失效 + 周期清空 + **禁止批次内 stat** |
| 测试 | `tests/test_qfq_factor_cache.py` **6 passed**，含 `缓存路径 stat aux 次数 = 0`（monkeypatch 计数断言）与 hit/miss/evict 计数 |

## 3 REPLAY-SKIP · 分钟表按内容去重

| 项 | 读数 |
|---|---|
| 根因 | `mcp_stock_minutes`/`mcp_etf_minutes` 的 `dataset_kind=None`、`skip_unchanged=None` ⇒ 去重函数**从不执行** ⇒ 229 批「新增 0 + 更新 50000」= **100% 无效重写** |
| **实施修正（如实申报）** | 原拟复用 `_filter_unchanged_snapshot_rows`，实测其 `SELECT … FROM <table>` **全表读**（stock_minutes **2.47 亿行** / etf_minutes **1.42 亿行**）⇒ 会读爆。改为新增 **`_filter_unchanged_rows_scoped()`**：只读 `time BETWEEN 本批 min/max` 窗口行，按 pk=(code,time,freq) 比对 |
| 开关 | 表名默认启用（stock_minutes/etf_minutes）+ 显式配置优先 + 全局 `QS_MINUTE_DEDUPE=0`；**零配置文件改动**（避开共享文件他线在途 R-04 条目） |
| 测试 | `tests/test_minute_dedupe.py` **8 passed**（全同批全跳过 / 混合批只留变化 / 新增保留 / **SQL 必带时间窗** / 开关 / 读失败回退全量） |

**语义两要点**（防误报）：①跳过写入 ⇒ `update_time` 不刷新（口径变为「最后内容变更时间」）；
②上游修正历史 bar 时，内容变化行仍正常写入（按内容比对，不按时间）。

## 4 回归读数

| 阶段 | 读数 |
|---|---|
| P1 后 | 9 文件 **82 passed / 0 failed** |
| P2 后 | 16 文件 **145 passed / 0 failed** |
| RS 后 | 15 文件 **130 passed / 0 failed** |
| 全程 | 隔离两变量（`QUANTSTUDIO_DATA_ROOT` + `QS_WRITE_LOCK_DIR`），零触碰生产库 |

## 5 三件独立回退矩阵

| 件 | 开关 | 回退后行为 |
|---|---|---|
| P1 | `QS_QFQ_INVARIANT_FULL_FACTORS=1` | 因子查询回到取全历史（逐位原样） |
| P2 | `QS_QFQ_INVARIANT_FACTOR_CACHE=0` | 不建缓存，每次直查 |
| RS | `QS_MINUTE_DEDUPE=0` | 分钟表回到无条件全量 upsert |

## 6 第 7 步口径（用户 2026-11-04 20:4x 优化裁定：并入 11-05 06:05 真实轮）

**裁定变更**：原「今夜单独起一轮 `--mode once`」→ **并入 11-05 05:50 拉起 + 06:05 真实轮**。理由（用户）：
①免重复采集（11-04 轮已跑，单独再跑＝同日双轮）；②今夜无锁窗留给软锁 T2-T5（节点1 需该窗）；
③06:05 补拉轮（etf_daily FULL_RANGE + 全任务）＝真实批次，读数更具代表性。

**前置事实核对（本节点实测）**：
- `QS_DaemonForever` 计划任务 Trigger = **At system start up**（开机启动型），非 05:50 定时
  ⇒ 05:50 拉起属**他线/调度排期**，本节点不动 daemon；
- 2026-11-04 = 周三，目标轮 **2026-11-05 = 周四**（跨日核对项，已写入守护提示词）。

**取数守护已注册**：`schedule-beb5dc69-1f5a-4fa8-9789-2246d8b5d11c`
→ **2026-11-05 07:10 CST**（= 2026-11-04T23:10:00Z）触发，给 06:05 轮留 1 小时余量。

**该轮须取三读数 + 回补硬门**：

| # | 读数 | 基线（改前） | 通过判据 |
|---|---|---|---|
| 1 | `write_split: invariant=` | 51.8s/批、12,067.8s/轮 | ≤1s/批 |
| 2 | `[MinuteDiff] changed/unchanged` | 「新增 0 + 更新 50000」全量重写 | changed 大幅下降、unchanged 占多数 |
| 3 | `[qfq] 因子查询缓存 hit_rate` | 无缓存 | **≥50%**（低于则停线归因） |
| ★ | 四项计数 `sampled/bad/skipped/unknown_rows` 改前 vs 改后 | 取自 P1 前历史日志基线 | **逐项相等**方算第 5 步硬门正式通过 |

**三态纪律**（写入守护提示词）：目标日/星期核对、daemon 双形态正则 + `--mode` 核对、
未收尾只报进度不判失败、**未启动如实报且绝不用前一日读数冒充**。

预测（按 P1 实测 76.6× 外推）：单批 invariant 51.8s → **≤1s**；叠加缓存后多数批次命中
⇒ invariant 桶接近 0；RS 使无变化批直接 0 写入 ⇒ 单轮预期 **≤0.5h**（原 11.1h）。
