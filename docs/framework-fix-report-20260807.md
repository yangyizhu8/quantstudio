# MCP bootstrap 取数适配修复报告（2026-08-08）

> WP7-E3 阻塞修复：bootstrap-run（McpFreshFetcher 链路）在真实 MCP 数据上暴露的
> 数据适配 bug 全部修复。工作包 A（reasonix 审核通过，ZCode 实施）。

## 修复清单（5 bug）

| # | Bug | 位置 | 修复 |
|---|-----|------|------|
| 1 | `_export_batches` 日期格式硬编码 `%Y-%m-%d`，`fetch_none_front` 传入 `%Y%m%d` | `sources/mcp_adapter.py` `_export_batches` | 新增 `_parse_flexible_date` 静态方法，兼容两种格式 |
| 2 | `main_db` 未透传到 `McpFreshFetcher` → `MCPAdapter.main_db=None` → `_sync_factor_snapshot` 返回 False → 线1 还原 fail-fast | `qfq_fresh_fetcher_factory.py` → `qfq_fresh_capture.py` → `sources/mcp_adapter.py` | 工厂透传 `main_db`；`McpFreshFetcher.__init__` 接受并存 `self._main_db`；`_ensure()` 注入 `mcp_cfg["main_db"]` |
| 3 | `trade_date` 硬编码 `%Y%m%d`，MCP 实际返回 `%Y-%m-%d` | `qfq_fresh_capture.py` `fetch_none_front` | 改 `format="mixed", dayfirst=False` |
| 4a | `none_df.mul(ratio, axis=0)` 标签对齐错误：`ratio.index` 是 raw_df 的 RangeIndex，`none_df.index` 是 DatetimeIndex → 无重复行时 front_df **静默全 NaN**（比 ValueError 更危险） | `qfq_fresh_capture.py` `fetch_none_front` | `ratio = pd.Series(adj.to_numpy()/adj_latest, index=idx)` 与 none_df 同 index |
| 4b | none_df 列值保留 Series 的 RangeIndex，构造 DataFrame 时错位 | 同上 | 列值 `.to_numpy()` 剥离 index |
| 5 | `adj_latest` 先 `fillna(1.0)` 再取有效值：窗口末行因子 NaN（停牌）时被当作有效锚 1.0，整列 front 比例缩放错误（静默） | 同上 | 先 `adj[adj.notna() & (adj>0)]` 取最后一个有效因子，再 fillna |

另：`fetch_none_front` 增加 `sort_values(trade_date).drop_duplicates(keep="last")`
去重（MCP export 同一 trade_date 可能多行：分片/数据修正），并 `reset_index(drop=True)`。

## 同批相关改动（WP7-E3 工具链）

- `qfq_formal_canary.py`：`run_held_canary` 新增 `dry_run` / `main_db_override` /
  `staging_db_override` 三参数（互斥校验；dry-run 只读快照不烧 nonce；staging 路径
  必须含 staging/output 标记且不得等于正式库路径）
- `config/profiles/mcp_only/qfq_aux_paths.json`：BOM 修复
- `scripts/generate_formal_manifest.py`：支持 `--wp7-canary-nonce` /
  `--wp7-release-nonce`（operation_grants 多授权）
- `tests/test_qfq_formal_canary_dry_run.py`：13 个新测试（全部通过）

## 验证证据

1. `tests/test_qfq_formal_canary_dry_run.py`：`13 passed in 0.77s`
2. 单个证券 `fetch_none_front` 真实 MCP 数据：none_df/front_df NaN=0、index 对齐、
   close 值正确
3. reasonix 独立复核（2026-08-08）：重复行去重正确、index 唯一、front 数值正确；
   adj_latest 顺序修复后再验证通过

## 分钟线边界发现（bootstrap 门禁相关，工作包 B/C 输入）

主库（staging 副本 + 正式库实测）分钟表 `stock_minutes` / `etf_minutes` 均为 **0 行**。
重锚引擎 `_check_minute_cov_raw`（qfq_reanchor_engine.py:951）的 partial 降级硬条件为
`target_count > 0`——分钟表为空时任何 bootstrap 证券的分钟线必然
`minute_coverage_mismatch` BLOCK（staging 库实测已有 5 个该 BLOCK 事件 + 2 个
`minute_raw_mismatch`）。

结论：**bootstrap 前置必须先把分钟表灌数据（daemon mcp 采集）**；窗口由
`_security_range`（主库分钟表 MIN/MAX）驱动，灌多少重锚多少（fresh ⊇ target 的
partial 语义只 UPDATE 共有区间，不 INSERT 新行）。决策：近 3 个月起步
（2026-08-08 用户确认）。

## 涉及文件

- `quantstudio/pipeline/sources/mcp_adapter.py`
- `quantstudio/pipeline/qfq_fresh_capture.py`
- `quantstudio/pipeline/qfq_fresh_fetcher_factory.py`
- `quantstudio/pipeline/qfq_formal_canary.py`
- `config/profiles/mcp_only/qfq_aux_paths.json`
- `scripts/generate_formal_manifest.py`
- `tests/test_qfq_formal_canary_dry_run.py`（新增）
