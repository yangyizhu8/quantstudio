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

---

## 已落地：日线快照预取缓存键 日界修复（2026-09-18，P-D14 D3 回归）

### 缺陷（回归，非新需求）

`quantstudio/backtest/providers/duckdb_data_access.py` 的 `preload_daily_snapshots` 与
`query_daily_snapshot` 使用了两套互不可调的日界：

| 侧 | 键来源 | `mod 86_400_000` |
|---|---|---|
| 预取（写） | `time // 86_400_000 * 86_400_000`（UTC 日界截断） | 恒为 **0** |
| 查询（读） | `_start_ms(date)`（Asia/Shanghai 日界） | 恒为 **57_600_000** |

两集合**交集为空** ⇒ 预取结果 100% 未被消费，每个交易日退化为一次对
`stock_daily`（971 万行）/ `etf_daily`（215 万行）的全市场窗口扫描。

**注**：该错位是**结构性**的——`// 86_400_000 * 86400000` 的输出 mod 恒为 0，与库内 `time`
实际存什么时区无关。库内实测（只读探针）：`stock_daily` 9,714,918 行与 `etf_daily`
2,147,623 行的 `time % 86_400_000` **全部等于 57_600_000**（CST 零点），零例外。

**引入点**：`bb602f3`（2026-08-27，WP-D P-D14 D3）。该提交前实现为
`for t, grp in df.groupby("time"): cache[int(t)] = ...`（原值键 = 查询键，**命中有效**）。
P-D14 的目标（把 08:00 异常组并入当日键）正确，但实现把日界基准从「数据自身日界」
换成了「UTC 日界」，附带把缓存从 100% 有效打成 100% 失效。

### 修复

- 新增 `quantstudio/backtest/providers/time_axis.py`：日界**唯一真相源**
  （`DAY_MS` / `CST_OFFSET_MS` / `start_ms` / `end_ms` / `day_start_ms` / `is_day_start_ms`），
  只依赖 pandas，不反向依赖 provider（防循环导入）。
- `duckdb_provider._start_ms/_end_ms` 改为 re-export：实现与签名逐字符不变、既有引用点零改动、
  `preload` 中原有的 `str(start_date)[:10]` 归一**留在原调用点**（不挪不删）。
- `preload_daily_snapshots`：`_day` 改用 `day_start_ms(df['time'])`（标量与向量共用同一表达式）。
- `query_daily_snapshot`：**读键/写键逻辑零改动**，仅在落缓存前加纯防御——
  非 CST 日界键不写入（防未来非日界 `date_ms` 的错位窗口结果污染真实当日键）。

### 验证（可重复）

- **契约测试**：`tests/test_daily_snapshot_cache_key.py` T-1~T-5（21 passed）。
  其中 **T-2 为「真命中」断网取证**（把 `_get_conn` 置为不可得后查询仍须返回数据）——
  直接补上 P-D14 T6 的验收漏洞（T6 只比较行数与 code 集合、未断言命中）。
- **P-D14 语义不回归**：影子库等价复验 T2/T3/T4/T6 全 PASS（`rows=7558`、`close=1.334`
  与 P-D14 验收记录逐位一致）。
- **黄金对照（端到端）**：同一策略同一区间（`fall_reversal` 2026-01-05~2026-03-13，影子库）：

  | 项 | 修前等价态 | 修后 |
  |---|---|---|
  | cache hit / miss | **0 / 45** | **45 / 0** |
  | `nav_sha` | `94f6ff57ad306d115baea97f9451e837` | 同左（**全等**） |
  | `trades_sha` | `1ffc7e2364dfd53236a4615767cc1612` | 同左（**全等**） |
  | nav_len / trades_len | 44 / 23 | 44 / 23 |
  | 净值末值 | 101328.63776 | 101328.63776 |

  「修前等价态」的构造依据：修复前缓存 100% 未命中，其可观察效果等价于**预取不填充缓存**，
  故以 `preload_daily_snapshots` no-op 打桩复现，无需改动任何文件。命中计数据此证明
  **两次运行走的是不同路径**——这是「结果相同」具备证明力的必要前提。

### 等价性边界（诚实声明）

物理**行序**在两路径间可能不同（两路径均以 `sort_values('time')` 整理，而该排序对相同
时间戳是不稳定排序）。该差异不构成行为变更：兜底路径本身的行序即非确定契约，且引擎经
`_df_index`（缓存键 `entry_df is df`）按 code→position 映射访问，不依赖行序；端到端黄金
对照全等亦为该结论提供直接证据。

**面向策略层的披露（不可省略）**：策略可通过 `get_snapshot`
（`quantstudio/backtest/providers/duckdb_provider.py:161-169`）直接取得当日快照原始帧。
若策略按**物理位置**（`iloc` / `head` / 逐行遍历）消费行序，则缓存命中路径与 DB 兜底路径
之间的行序差异对其**可观察**——但该行序在本框架中**从来不是保证契约**：DB 兜底路径的 SQL
无 `ORDER BY`，其后处理 `sort_values('time')` 亦为不稳定排序，任何版本下均无行序承诺。
**按 code 取值（`df[df.code == x]` / 自建 code→行 映射 / `_df_index`）的策略不受任何影响。**

