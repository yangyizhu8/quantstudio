# smallcap_overnight_scalp 分钟路径批量化 + 当日预加载（Phase 4）性能验证报告

> 日期：2026-08-12 ｜ 状态：**完成（代码等价性 PASS；性能结论如实记录，剩余瓶颈另立课题）**
> 改动范围：`duckdb_provider.get_bars_by_count` 分钟分支（4A 批量化）、`ptrade_api.get_history` 分钟内存切片 + `backtest_engine` 当日全量注入（4B）。**未改动**：策略文件、撮合/生命周期、bar_cutoff_ms PIT 语义、日线分支（get_bars_by_count frequency='1d' 与 query_bars_by_count_batch）。

## 1. 窗口与数据事实（重要）

| 项 | 值 |
|---|---|
| 生产 DB | `data/quantstudio.db`（14.6GB） |
| stock_minutes 数据范围 | **2026-07-01 ~ 2026-08-06**，且仅 19 个交易日有数据（**07-17/20/21/22/29/30/31 缺口**；07-02/06/07/08/10 部分缺失） |
| etf_minutes 数据范围 | 2026-05-06 ~ 2026-08-07 |
| stock_daily 数据范围 | 2018-01-02 ~ 2026-07-31 |
| 原方案 382 天窗口 | **不可行**：2025-01-02 即报 `TABLE_EMPTY`（无分钟数据）。按审核修订【5】改用数据支持的最大窗口 |
| 等价性窗口 | **2026-07-01 ~ 2026-07-31**（23 交易日，stock_daily 与 stock_minutes 交集） |

> 注：分钟数据缺口日（如 07-17）引擎照常运行——当日无股票 bar，由 etf_minutes 的 bar 驱动循环；此为数据不完整下的既有行为，基线与优化后同样处理，不影响等价性对比。

## 2. 性能实测

| 指标 | 优化前（基线） | 优化后（4A+4B） |
|---|---|---|
| 23 天窗口总耗时 | ~90 分钟（期间有其他并行回测进程竞争） | **84.6 分钟（5075s，含 ~4 分钟引擎初始化）** |
| 逐只分钟 SQL（query_minute_bars_by_count / _by_range） | 每日 ~500 次调用 × 3 次 SQL | **0 次（完全消失）** |
| 分钟 batch SQL | 每日 1 次（引擎加载，与优化无关） | 每日 1 次 + **~53 次/天缺失 code 补查**（缓存外停牌/数据缺口候选） |
| T5 等价性 | — | **PASS（容差 0，daily_stats/trades/benchmark 逐位相等）** |

## 3. 逐日耗时构成（细粒度计时，07-14 单日窗口 328 秒）

| 环节 | 耗时 | 占比 | 说明 |
|---|---|---|---|
| **get_history（日线，533 次）** | **154.2s（289ms/次）** | **47%** | PR7 日线缓存**冷启动**：每日候选池中新增 code 触发 `WHERE code IN (?)` 全表扫描 stock_daily（1140 万行，无索引）+ `_post` 后处理 |
| get_stock_status（104 次） | 24.6s（236ms/次） | 7% | 参考数据 API，无预加载/无索引 |
| _load_minute_snapshots（引擎每日 1 次） | 6.6s/天 | 2% | **batch 查询本身很快**（130 万行结果，SQL ~3s + fetchdf/groupby） |
| attach_bar（960 次） | 1.5s（2ms/次） | <1% | 每 bar 注入 |
| 其他（bar_prices 构建、DataDict、日终估值、策略循环） | 剩余 ~140s | 43% | 引擎生命周期逻辑（铁律禁止改动） |

## 4. 结论（如实记录）

1. **Phase 4 的代码目标是结构性消除分钟路径 N+1**：✅ 达成。优化后 `minute_by_count=0 / minute_by_range=0`，对"批量调用分钟数据"的策略（一次传全池）收益显著；4A 的 batch 化 + 4B 的内存切片在 23 个单元测试中与旧路径逐位等价。
2. **T5 铁律验收**：✅ PASS。优化前后 NAV + 成交 + 基准**逐位相等（容差 0）**。
3. **"解决 smallcap 超时"的目标：未达成，且根因与方案性能模型不符**。smallcap 的分钟调用只在 09:31 一次性 ~500 次（每次单只），每日仅 ~14 秒；**真实瓶颈是日线 get_history（PR7 缓存冷启动全表扫描，289ms/次）与 get_stock_status（236ms/次）**——二者合计占单日耗时 >54%。23 天窗口优化后仍 ~85 分钟，超过 compare_roundtrip 的 30 分钟超时。如需继续提速，须另立课题（见 §6），按 AGENTS.md 铁律走"纯性能优化"审核流程。
4. 索引实验（stock_minutes (code,time) ART 索引，副本验证）：批量查询 2.86s→1.77s（1.6x）。**收益有限**（batch 本非瓶颈），且建索引会写生产 DB 文件——不擅自执行。

## 5. 数据质量告警（与本次改动无关，顺带记录）

- stock_minutes 仅 19 个交易日有数据且 5 天不完整——smallcap 在 07-01~07-31 的实际交易只有 8 笔（基线/优化后一致），**分钟数据缺口导致策略信号稀疏**，回测结论参考性有限。建议数据管线补齐分钟历史后再做策略层面的结论。

## 6. 后续优化方向（未实施，待审核）

1. **日线 get_history 冷启动**（最大头，154s/天→目标 <10s）：PR7 `_ensure_bars_in_cache` 对新增 code 全表扫描——改为"按 code 前缀/区间分片 + 一次 SQL 多 code 加载"或 `(code, time)` 索引（stock_daily 同样无索引）。属日线路径优化，与 Phase 4 隔离。
2. **get_stock_status / 参考数据 API**（24.6s/天）：加入预加载内存路径（对齐 get_fundamentals 的 preload 模式）。
3. 全库索引评估：stock_minutes/stock_daily `(code, time)` 索引（副本实测 1.6x，需评估数据管线增量写入兼容性）。

## 7. 验证证据索引

- 基线 CSV：`output/t5_phase4/before/`（daily_stats/trades/benchmark）
- 优化后 CSV：`output/t5_phase4/after/`
- T5 脚本：`output/t5_compare_phase4.py`（PASS）
- 单元测试：`tests/test_phase4_minute_batch_provider.py`（12 项）、`tests/test_phase4_minute_memory_slice.py`（11 项）
- 计时日志：`output/perf_timer_4d.log`（引擎方法）、`output/perf_timer3_1d.log`（策略侧 API）
