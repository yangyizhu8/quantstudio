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

---

## 7 11-05 真实轮读数（06:05 轮 · 催报 · 2026-11-05 09:35）

> 执行环境：Windows / Python 3.11 / Windows PowerShell 5.1；daemon 单实例
> PID 40988（`--mode forever`，05:55:55 启动，编排拉起）；全程只读，未干预。

### 7.1 三读数

| 读数 | 实测 | 基线（改前） | 判定 |
|---|---|---|---|
| **① `invariant=`** | `stock_daily` **24.8s**（`total=305.0s`，`sqlwrite=37.1s`） | `stock_daily` **850.6s**（`total=1190.3s`） | ✅ **34.3×**（invariant）/ 3.9×（total） |
| **② `MinuteDiff`** | `stock_minutes` 132 批：`changed=12,849 / unchanged=8,895,942` ⇒ **省 99.86%**<br>`etf_minutes` 129 批：`changed=3,615 / unchanged=6,238,044` ⇒ **省 99.94%** | 全量重写（改前 `wrote 50000（新增 0 + 更新 50000）`） | ✅ **无效写几乎全消** |
| **③ 缓存命中率** | `hit=0 miss=278 evict=270 hit_rate=**0.0%**` | 无缓存 | ❌ **结构性失效** |

**③ 归因（假设被证伪）**：方案原文假设「forever 轮同日多批 ⇒ 同日多批可命中」，
实测 forever 轮每批是**不同时间段顺序推进**（键含 `lo_ms/hi_ms`）⇒ 键恒变 ⇒ 结构性 0 命中。
`evict=270`（容量 8）进一步证明键持续涌入挤出旧键。⇒ **P2 判为无效实现**（保留代码与开关，
文档标注"实测无效"，从收益清单移除）。

### 7.2 第 5 步硬门：四项计数对照

| 表 | 11-05 记录数 | sampled 分布 | **bad** | skipped / missing / unknown |
|---|---|---|---|---|
| `etf_minutes` | 129 | 20（123 条）/ 9~19（6 条） | **全部 0** ✅ | 全部 0 ✅ |
| `stock_minutes` | 132+ | 11~1,210 等（随 RS 过滤后 df 大小浮动），含 1 条 `5000` | **全部 0** ✅ | 全部 0 ✅ |
| `stock_daily` | 15 | **5000（13 条）** | **2 条 bad=2、1 条 bad=4** ⚠ | 全部 0 ✅ |

**⚠ 关键澄清（避免误判为「判据漂移」）**：
- `stock_daily` 的 `bad=2 / 4` 与 **11-04 同日同值逐条一致**
  （11-04 日志：`自洽偏离 2/5000 行（rate=0.04% unknown=0 xsrc=5000）`、`自洽偏离 4/5000 行`）
  ⇒ **同一上游数据批的稳定复现**，**不是 P1 窗口过滤造成的漂移**
  （P1 已实测「窗口 vs 全历史 lookup 逐键相同」76.6×/60.0×）；
- 固化基线原文「两表 bad 恒为 0」**其范围是 stock_minutes / etf_minutes 两表**（该陈述准确）；
  **stock_daily 历史上存在 bad>0 记录**（11-04 的 2/5000、4/5000），本轮同值复现。
- ⇒ 硬门判据须**按表分别裁**：`bad=0` 对 minutes 两表成立；对 `stock_daily` 须以
  「与改前同批同值」为判据（**不是 0**）。

### 7.3 `sampled` 缩减的语义（非缺陷，但判据须改）

`sampled` 由基线 5000 变为 11~1,210（minutes）/ 5000（stock_daily），
原因是 **RS 过滤后传给自检的 df 只剩变化行** ⇒ 抽样对象变小。
`skipped / missing_factor_rows / unknown_rows` 全为 0 ⇒ **窗口未误伤任何本应命中的因子行**
（若窗口收窄过头，会出现 `skipped/missing>0`；本轮为 0，**P1 窗口 ±3 天未被证伪**）。

### 7.4 链①证据（④审可用）

| 表 | 当前水位 | 表内 max | 落后 |
|---|---|---|---|
| `stock_daily` | **2026-09-03** | 10-30 | **57 天**（与链① 10-04 记载 `old=1788364800000(09-03)` **逐字吻合**） |
| `etf_daily` | **2026-09-04** | 10-30 | 56 天 |
| `etf_minutes` | **2026-10-09** | 10-30 | 21 天 |
| `stock_minutes` | **2026-10-16** | 10-30 | 14 天 |

机制直接证据（07:37:31）：`短路判定：skip=False 理由=a4_verdict=3（有 repair/full 或熔断 ⇒ 照常重拉）
forced_replay_days=1 wm=1792134000000(2026-10-16)` ⇒ 每轮从旧水位重拉 14~57 天。

### 7.5 未收尾项

`stock_minutes` 批次（`mcp_stock_minutes_mcp_20261105_072721_05da56`，07:27:21 起）**至 09:35 仍在跑**，
阶段时延行尚未产出 ⇒ 其 `invariant=` 读数待补（其 `MinuteDiff` 与四项计数已有）。
