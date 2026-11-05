---
title: 回测引擎性能优化（日线档 / 分钟档）· 审核方基准与裁决口径
date: 2026-11-05
role: 审核方 = 策略研发专属会话（节点⑫）；被审方 = 新开 SDD 会话
basis: 铁律 01（六步流水线）· 铁律 02（性能优化纯等价）· 铁律 18（纯增益审计）
knowledge_refill: [命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-28）——日键口径铁律（性能改动不得隐式改口径）
---

# 一、角色与边界

| 方 | 职责 |
| --- | --- |
| **被审方（SDD 会话）** | ①方案→③实施→④自验→出证据；**不自行②审、不自行⑤确认、不自行⑥推送** |
| **审核方（本线）** | ②审（方案准入）+ ④验收复核（证据核对）+ 逐轮裁决与指令；**不代写代码、不代跑** |
| **用户** | ⑤确认、⑥推送授权 |

# 二、事实基线（已确证，被审方直接采信，禁重复调研）

| # | 事实 | 位置 |
| --- | --- | --- |
| 1 | 分钟取数已批量：每交易日 1 次 `query_minute_bars_by_range_batch`，**≤2 SQL/日**，与 universe 无关 | `backtest_engine.py:2448` |
| 2 | 历史教训：曾逐 code 循环（5,525×4 次 DB）⇒ DuckDB GIL 崩溃；2026-07-22 已批量 | 同上 `:2436-2440` |
| 3 | 分钟 bar 按 `groupby('time')` 切 ~241 组，**每组 `.copy()`** 后注入 preClose/name | `:2481-2489` |
| 4 | 每 bar `attach_bar` 清空 `_query_cache`（PIT 语义，**不得为性能取消**） | `:2596` / `ptrade_api.py:722-740` |
| 5 | `attach_day_minute_history` 内存切片 ⇒ `get_history('1m')` 零 DB | `:2563` / `ptrade_api.py:742` |
| 6 | `DataDict` 已惰性构建 | `ptrade_api.py:294-344` |
| 7 | 被回测策略**未定义 `handle_data`** ⇒ 239/241 bar 空转；重活仅 09:31 / 14:55 | `strategy.py:126-127` |
| 8 | **实测**：全窗 148 日 ≈ 4~5h；单日窗 140~193s（含启动）⇒ **0.40~0.50 s/bar** | `_RERUN_HANDOFF_20261029.md:26,59`；`REPORT.md` |
| 9 | 日线档：148 日 = 148 次迭代（**1/241** 于分钟） | `:562-563` |

# 三、待证假设（须 profile 证伪/证实；不成立也要留痕）

| # | 假设 | 验证手段 |
| --- | --- | --- |
| H1 | 每 bar `group.copy()`（35,668 次 × ~5000 行）为主因 | cProfile cumtime 占比 |
| H2 | `groupby('time')` 处理 1.78 亿行为主因 | cProfile |
| H3 | 构价 `dict(zip)` ≈1.8 亿次插入为主因 | cProfile |
| H4 | 内存/GC 压力为主因 | 峰值 RSS + `tracemalloc` |
| H5 | 盘前 `_build_first_board_pool` 占每日固定开销大头 | 分段计时 |
| H6 | 日线档存在同源低效 | 日线短窗 profile |

# 四、禁改清单（语义防线，触任一即②审否决）

1. 撮合价 `match_price_mode` 与撮合语义（close/open/即时撮合）
2. 涨跌停档位判断（`preClose`/`name` 注入逻辑与档位计算）
3. T+1 解锁时机
4. `scheduler` 触发时机（end-labeled；scheduler 在 `handle_data` 之前）
5. 每日生命周期顺序（`before_trading_start`→bar 循环→`after_trading_end`→日终净值）
6. `get_history` 的 PIT 截断（`bar_cutoff_ms`）——**`_query_cache` 每 bar 清空不得取消**
7. `all_bars` 列集契约（`tests/test_phase4_minute_memory_slice.py` 为硬门）
8. 任何 API 名/签名/默认值/返回契约/列序/dtype
9. 策略源码（`output/generated_strategies/**`、`strategies/**`）

# 五、验收硬门

| # | 门 | 判据 |
| --- | --- | --- |
| G1 | **黄金逐位一致** | 同窗×同参，`nav_history` / `trades.csv` / `positions` 逐字段 sha256 与改动前**完全相等** |
| G2 | 双窗验收 | ①短窗 3~5 日（快速回归，每次改动后必跑）②**148 日全窗**（终验，需申报窗口） |
| G3 | 既有测试全绿 | 含 `test_phase4_minute_memory_slice.py` |
| G4 | 性能读数规范 | **同机、同环境、同数据根**前后对照；报**墙钟 + 峰值内存**；标执行环境（禁止「环境条件性全绿」） |
| G5 | 可独立回退 | 每项优化可分步独立回退，禁捆绑 |

# 六、审核回执格式（被审方每轮按此交，审核方按此判）

```
【本轮】R-PERF-NN | 阶段：②审 / ③实施 / ④验收
【改动清单】文件:行 + 一句话语义说明 + 归入哪类手段（零拷贝/减分配/批量/缓存）
【等价性论证】为什么可观察结果不变（逐项）
【实测】短窗 墙钟 before→after；峰值内存 before→after；执行环境（python/duckdb 版本、DATA_ROOT）
【黄金】sha256 前后对照（相等/不等 + 若不等则定位）
【测试】pytest 结果（数量 + 全绿/红项）
【如实申报】未做项 + 原因；已知风险；回退点
```

# 七、严打清单（历史教训，命中即退回）

| # | 问题 | 依据 |
| --- | --- | --- |
| 1 | 无 profile 数据就称「热点在 X」 | 铁律 02 要求先定位真实热点 |
| 2 | 「差异很小/结果更合理」放行 | 铁律 02 明文禁止 |
| 3 | 性能读数未标执行环境 | 全局纪律（2026-11-05 我域犯次） |
| 4 | 裸探测跑回测（未设 `QUANTSTUDIO_DATA_ROOT`） | a37d3002；会停主采集 |
| 5 | 以「避开某轮采集」论证可跑性 | forever 常驻持锁判据 |
| 6 | 未过②审直接实施 | 铁律 01 |
| 7 | `git add -A` / 未核 index | 铁律 10 + 反向串车补丁 |
| 8 | 多项优化捆绑提交（无法独立回退） | G5 |

# 八、首轮指令（R-PERF-01，仅调研 + 方案，禁改代码）

| 步 | 动作 | 交付 |
| --- | --- | --- |
| 1 | 环境隔离：查 daemon 是否 `forever` 持锁；设 `QUANTSTUDIO_DATA_ROOT=data\backup_import_20261101_040935` | 一行环境声明 |
| 2 | 短窗基线：2026-03-02~03-06（5 交易日）跑 `minute-bar-v1`，记墙钟 + 峰值 RSS | 基线读数 |
| 3 | **cProfile** 同窗口，出 cumtime Top 25，标注归属（取数/引擎/策略/pandas 内部） | profile 表 |
| 4 | 逐条判定 H1~H6（成立/不成立/部分 + 实测占比） | 判定表（含不成立项） |
| 5 | 出优化候选表（落点/预期收益/等价性论证/风险/验证方式），按收益-风险排序 | 候选表 |
| 6 | 出黄金验证方案（sha256 命令 + 双窗）与回退开关设计 | 方案件 |
| 7 | 日线档同法短窗 profile（收益量级判断） | 对照段 |

**首轮交付物**：`docs/evidence/perf-minute-bar-profile-YYYYMMDD.md` + `docs/minute-bar-perf-design-YYYYMMDD.md`（六步① 骨架）
**首轮禁区**：不改任何生产代码；不跑全窗（4~5h，须单独申报窗口）；不碰 §四 禁改清单

---

# 九、审核方基准修正 R1（2026-11-06，依 R-PERF-01 实测反馈 + 审核方代码复核）

> **本节修正 §二 事实基线 #1 的适用范围**，并补六条新事实。原表述保留以便追溯。

## 9.1 修正：「DB 已批量 ⇒ DB 不是瓶颈」只对**分钟 bar 装载**成立

| 原表述 | 修正后 |
| --- | --- |
| 分钟取数已批量：每交易日 1 次批量 SQL，≤2 SQL/日 ⇒ DB 非瓶颈 | **收窄为**：`_load_minute_snapshots`（分钟 bar 装载）确为 ≤2 SQL/日 ✓；**但盘前池走另一条路径**——`check_limit → get_bars`（**日线区间路径**）为**逐码 SQL**，实测 5 日窗 **18,591 次 / 315.7s / 55.2%** ⇒ **DB 是主瓶颈，只是不在我原先检查的那条路径上** |

**审核方自记**：此为我在 §二 基线中的**结论外推错误**（把一条路径的批量结论当成全链路结论），特此更正。

## 9.2 新事实（审核方代码复核，`duckdb_provider.py` / `duckdb_data_access.py`）

| # | 事实 | 位置 | 优化含义 |
| --- | --- | --- | --- |
| 1 | `get_bars` **日线区间分支逐码循环**：`for code in codes: query_bars_by_range(code,...)` | `duckdb_provider.py:64-66` | **C1b 对象**——18,591 SQL 的直接来源 |
| 2 | `get_bars` **分钟区间分支同样逐码**：`for code in codes: query_minute_bars_by_range(...)` | `duckdb_provider.py:73-77` | **C1a 对象**——见 #3 |
| 3 | **`query_minute_bars_by_range_batch` 已存在**（:1178），且 `backtest_engine.py:2448` 生产在用 | `duckdb_data_access.py:1178` | **C1a = 漏用现成批量函数**（零新增算法、已被生产验证）⇒ **风险最低、应优先** |
| 4 | **`query_bars_by_range_batch`（日线区间批量）不存在** | — | **C1b 需新增**，可仿 #5 模板 |
| 5 | `get_bars_by_count` **已批量化**：`query_bars_by_count_batch(codes, count, ...)`，注释「单条/少量批量 SQL 取代 N 次单码 SQL，**与逐只调用字节级等价**」 | `duckdb_provider.py:83-86` / `duckdb_data_access.py:821` | **C1b 的等价性论证模板与实现范式** |
| 6 | `check_limit` **本身已批量**（传 list 一次 `get_bars`）+ **已有 P1′ 缓存**（`_limit_cache`，`QS_LIMIT_CACHE_DATES` 默认 8，`QS_LIMIT_CACHE_DIAG=1` 强制禁用）+ 命中率计数 | `ptrade_api.py:1256-1315`、`:1238-1247` | 逐码问题在 `get_bars` 内，**不在 check_limit**；缓存已存在，C1 落地后应评估缓存是否仍必要 |

## 9.3 由此产生的审核增补要求

| # | 要求 |
| --- | --- |
| A1 | C1 **必须拆为 C1a（分钟·用现成 batch）/ C1b（日线·新增 batch）两笔独立提交**，各带独立黄金门 |
| A2 | C1b 等价性论证必须逐项覆盖：①空结果语义（`if not df.empty` 跳过 vs 返回空 df）②列集/列序/dtype ③`use_qfq` 分支 ④重复 code ⑤未知 code ⑥单码 vs 多码返回结构一致性 |
| A3 | 因 `get_bars` 是**双档共用**路径，黄金门**必须双档覆盖**：分钟档（首板1进2）+ **日线档（至少 1 个日线策略，如 CANSLIM）**，不可只测首板 |
| A4 | 报告中的「45h 口径」表述须更正：实测 484.6s / 5 日 ⇒ 96.9s/日 ⇒ 148 日 ≈ **3.98h**，与 4~5h 口径**一致**，不构成不符 |