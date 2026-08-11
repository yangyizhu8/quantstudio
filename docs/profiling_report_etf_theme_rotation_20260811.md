# etf_theme_rotation 回测 37 分钟瓶颈定位报告

> 日期：2026-08-11 ｜ 状态：**定位完成，优化方案待 zcode 审核（未实施）**

## 1. 场景与参数

| 项 | 值 |
|---|---|
| 策略 | `quantstudio/backtest/strategies/etf_theme_rotation_quantstudio.py`（144 行，用户 GUI 22:05 回测同名策略，产物 `output/backtest_results/20260811_220529_strategy`） |
| 窗口 | 2025-01-01 ~ 2026-08-10（382 交易日） |
| profile | daily-bar-v1（日线），match_price=next_open |
| DB | profiling 用 staging 快照副本 `output/t5_roundtrip/quantstudio.db`（08-08 快照 14.6GB，与生产 `data/quantstudio.db` 结构一致——生产 stock_daily 亦无 ETF 代码，已实测） |
| 策略数据调用 | 每交易日：`get_etf_list_local(query_date, equity, active_only)`（PIT 池 ~825 只）+ `get_history_batch(universe, count=70, '1d', fields=['close','amount'], fq='pre')` + 逐只 numpy/pandas 指标循环 |

## 2. 方法

动态 wrap `PtradeAPI` 类方法 + 模块级 bound method + `ptrade_import` 三层（策略实际调用链），仅计时不改逻辑；`>0.5s` 落盘 + 进程内汇总。runner：`output/perf_runner.py`（不入 commit，output/ 已 gitignore）。

## 3. [PERF] 汇总（10 交易日窗口 2025-01-02~2025-01-15，staging 副本）

```
=== PERF SUMMARY (threshold>0.50s) ===
get_history_batch            count=    10 total=    31.5s max=  5.11s avg=3.149s
get_history                  count=    10 total=    31.4s max=  5.11s avg=3.144s   ← batch 内部调用（重叠计数）
get_etf_list_local           count=    10 total=     0.8s max=  0.25s avg=0.080s
TOTAL_API_TIME=63.7s   （API 31.5s + 策略/引擎循环 32.2s ≈ 3.2s/天）
```

外推：382 天 × (API 3.15s + 循环 3.2s) ≈ 40 分钟 —— 与用户观察 37 分钟**量级吻合**。

## 4. 单次 get_history_batch（825 只 × 70 根，56299 行）耗时分解

| 段 | 耗时 | 占比 | 证据 |
|---|---|---|---|
| 纯 SQL 执行（fetchall） | 1.37s | 62% | 实测（无索引） |
| fetchdf 转换 | 0.59s | 27% | 1.96-1.37 |
| groupby→dict 拆分（825 组） | 0.31s | 14% | 实测 |
| 合计 | 2.27s | — | 与 API 计时一致（额外 0.9s 为 API 包装/缓存键等） |

**索引评估**：`etf_daily(code, time)` 复合索引后 SQL 0.95~1.09s（建索引 6.8s/209 万行）——仅提升 ~30%。主成本是 `ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC)` 的**窗口函数排序**（56299 行结果排序），索引只能加速过滤不能消除排序。`duckdb_indexes()` 确认生产/副本全库无索引。

## 5. 瓶颈定位结论（双头）

- **A：get_history_batch 单次 3.1s/天**（SQL 1.4s 无索引 + Python 层 0.9s + 缓存不命中），382 天 ≈ 20 分钟。
  当日查询缓存（ptrade_api.py:1122-1127）的 cache_key 含完整 sec_list tuple——动态池每日微变（新股上市）→ **缓存永不命中**。
- **B：策略逐只 Python 循环 3.2s/天**（825 只 × `df['close'].astype`/`np.nanmean`/`_pct_rank` 等），382 天 ≈ 20 分钟。
- 两者叠加 ≈ 37-40 分钟，与用户观察一致。**不是**"get_history_batch 逐只查询"（日线已批量化单条 SQL，zcode 判断正确）。

## 6. 优化方向（仅评估，**未实施**，待 zcode 审核）

1. **池快照缓存**（A）：按"交易日 + 池子哈希"缓存 get_history_batch 结果，动态池每日变化小时多数天可命中；需论证新上市 ETF 延迟一日纳入的语义影响（PIT 规则变更风险，属行为变更审核）。
2. **SQL 裁剪**（A）：窗口函数排序 1s 是主成本——评估按 code 分别 LIMIT 的等价改写（DuckDB 尚无 index skip scan，或按池子切片分 2-3 条 SQL 并行）。
3. **策略循环向量化**（B）：825 只 × 70 根的 close/amount 可直接从 batch 返回的 dict 转矩阵（numpy 2D）批量计算，替代逐只循环——纯内部实现优化，但需等价性验证（NaN 语义、rank 的 pct=True 跨截面一致）。
4. **索引**（A，收益有限 ~30%）：可作为附带优化，非主线。

## 7. 附注

- profiling 期间发现 `filter_stock_by_status` 的 DELISTING 过滤用 `stock_daily` 作状态源，ETF 不在其中会被全部判"已退市"→ 池空（etf_hot_theme_rotation 复现 0 ETF）。**与本任务无关，但已在 08 报告外记录**——生产/副本 stock_daily 均无 ETF 代码（实测 517900/510300 = 0 行），属既有行为，未改动。
