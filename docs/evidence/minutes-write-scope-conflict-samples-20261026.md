# minute 写冲突实弹样本集（附注 2 取证备查，2026-10-26）

> 用途：供 dev 侧「连接级取证」增量复现比对（枚举进程内 duckdb 连接/open transaction）。
> 数据源：data/logs/daemon.log（原始行逐字摘录，保留时间戳与批号）。

## 样本 1：第 20 次冲突（B1-B7 全量在产下首次捕获）

- 时点：2026-10-26 14:11:04（补跑轮 r_d96c8866）
- key：000895, 1792373400000, 1min（当前增量窗新数据段；非既往恒定历史 key 000055@09-07）
- 失败槽（逐字）：

```
14:11:04 ERROR quantstudio.pipeline.daemon: [mcp_stock_minutes_mcp_20261026_140252_4a0413] 写失败判别特征: table=stock_minutes batch=mcp_stock_minutes_mcp_20261026_140252_4a0413 path=_write_locked serial=True ctx_src=failure ctx_table=stock_minutes ctx_path=_write_locked ctx_exc=TransactionException exc=TransactionException conflict_key="000895, 1792373400000, 1min" peers_n=0 cross_proc=True peers=-
```

- 重试行（逐字，14:09:56 起）：

```
14:09:56 WARNING quantstudio.pipeline.writers: [DuckDBWriter] stock_minutes batch=mcp_stock_minutes_mcp_20261026_140252_4a0413 写冲突重试 1/3（TransactionException: TransactionContext Error: Failed to commit: write-write conflict on key: "000895, 1792373400000, 1min"）
14:10:17 WARNING quantstudio.pipeline.writers: [DuckDBWriter] stock_minutes batch=mcp_stock_minutes_mcp_20261026_140252_4a0413 写冲突重试 2/3（TransactionException: TransactionContext Error: Failed to commit: write-write conflict on key: "000895, 1792373400000, 1min"）
14:10:41 WARNING quantstudio.pipeline.writers: [DuckDBWriter] stock_minutes batch=mcp_stock_minutes_mcp_20261026_140252_4a0413 写冲突重试 3/3（TransactionException: TransactionContext Error: Failed to commit: write-write conflict on key: "000895, 1792373400000, 1min"）
```

- 冲突窗口（14:05-14:11）daemon 内活动：仅 MCPAdapter 向 qfq_aux.db 注入 adj_factor（每 1-2s / 5224 行）；主库无引擎 apply、无作用域登记（write_scope 全零）。

```
14:05:40 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:41 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
14:05:42 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:43 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
14:05:44 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:44 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
14:05:46 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:46 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
14:05:47 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:48 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
14:05:49 INFO quantstudio.pipeline.sources.mcp_adapter: [normalize_mcp_adj_factor_df] STOCK 1min 标准化 5224 行（去重后）
14:05:49 INFO quantstudio.pipeline.sources.mcp_adapter: [MCPAdapter] §7.2-A 注入 adj_factor 5224 行 → data\qfq_aux.db (asset_type=STOCK)
```

- 3A 锁持有者（14:30 取证）：pid=34232 task=event_discovery:292（daemon 自身）；进程清单无跨进程写者 ⇒ cross_proc=True 为误报。

## 样本 2：历史恒定 key 型（对照，第 1-17 次）

- etf：159512, 1789090200000, 1min（2026-09-11 09:30）
- stock：000055, 1788744600000, 1min（2026-09-07 09:30）
- 该型在 B1-B7 之前反复复现（19 次），A6 有界重试 3/3 耗尽。

## 取证要点（供 dev）

1. MCPAdapter「线1 还原」在同进程内的落库路径：是否存在直写主库 stock_minutes 的分支（不经 writer 表锁 / 不登记作用域）？
2. 冲突窗口内 daemon 内 duckdb 连接清单：谁持 stock_minutes 的 open transaction？
3. adj_factor 注入（qfq_aux.db）与主库写盘的并发关系：是否共享连接/事务上下文？
4. 复现触发条件：stock_minutes 增量窗写入（当前窗 key）+ 同进程 adj_factor 高频注入。