# 回测框架性能优化记录

## 已落地：SHOW TABLES 表集合缓存（2026-07-28 门 1）

`quantstudio/backtest/providers/duckdb_data_access.py` 的内部语义等价性能优化（仅 1 项）：

- `_existing_tables()` 缓存 `SHOW TABLES` 结果；首次查询后复用，避免每个调用方重复执行
  `SHOW TABLES`。
- `preload_daily_bars` / `query_strategy_events` / `query_corporate_actions` 三处原直接
  `SHOW TABLES` 改为走 `_existing_tables()` 缓存（小市值策略 76 交易日实测 SHOW TABLES 调用
  **152 → 1**）。
- 返回防御性 `set` 副本；调用方修改返回的 set 不会污染内部缓存。
- `close()` 将 `_tables_cache` 置 `None`，重连后可看到新表。

### 调用路径事实（b41400d）

b41400d 上共 **10 个** catalog / 表存在性检查调用方最终共享 `_existing_tables()`：

- **7 个**在 main 中已使用该入口（`query_listing_dates` / `query_security_metadata` /
  `query_index_constituents` / `query_index_constituents_quality` /
  `query_industry_membership_quality` / `query_sw_index_daily_coverage` /
  `query_industry_membership`）。
- **3 个**原直接执行 `SHOW TABLES` 已收敛至统一路径：`preload_daily_bars` /
  `query_strategy_events` / `query_corporate_actions`。

`query_daily_snapshot` 在 b41400d 中已直接查询 `stock_daily`/`etf_daily`，不属于上述 10 个调用方。
旧基线 d8a0791 曾包含该调用方，因此历史数量为 11，但不适用于本次发布基线；该重构不影响优化收益，
SHOW TABLES 152→1 的实证不变。

### 决定不实施（backlog，非生产代码）

provider-level get_history 缓存（`query_bars_by_count_multi_table` 日内行情缓存）：

- 小市值策略与双均线策略真实回测中 `get_history` 缓存命中均为 **0**：`get_history` 每次都
  用不同的 `count` / 不同标的，缓存键几乎不重复。
- `PtradeAPI.get_history()` 已有 `_query_cache` 层（按 symbol+count+end 缓存），重复取数已被
  该层吸收；框架层再叠加一层只会增加内存与维护成本。
- synthetic 86× 不构成生产收益证据：该数字来自「同一 (symbol, count, end) 重复取数」的合成
  负载，真实回测中不存在这种重复。
- 4096 条目上限是「条目数」上限，不是字节数内存上限；无法据此证明内存安全。
- 后续若实施，必须满足：byte-bounded LRU（按字节预算淘汰）+ 真实生产命中证据（≥某阈值）。
- 本条不保留任何生产代码或 synthetic-only 的正式契约测试。

### 验证（可重复）

- 定向测试：`tests/test_duckdb_data_access_caching.py`（表集合缓存 6 项）。
- 全量测试 nodeid 对比（baseline / optimized 公共 nodeid 零新增失败、零状态变化）。
- 黄金结果：小市值策略 `golden_baseline.json` 与 `golden_optimized.json` 字节级一致
  （canonical JSON 的 sha256 相同）。
- A/B：交错 B,O,O,B,B,O；仅 SHOW TABLES 152→1、SQL 调用减少为确定性收益；端到端耗时
  高噪声，不宣称稳定提升。

### 复用说明

本优化与 `PtradeAPI.get_history()` 的 `_query_cache` 正交：前者缓存「表是否存在」的 catalog
探测，后者缓存「具体行情」查询。两者互不替代。
