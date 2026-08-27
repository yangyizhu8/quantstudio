# G2a prefetch 失败归档 + export_cache 0 行 bug 最小复现（R11/R12）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0（2026-08-17） |
| 关联 | `docs/mcp-minute-front-anchor-design.md` v1.2 §4.0-④（R11 独立立项）、R12 归档 |
| 状态 | 归档；export_cache bug 待独立立项（六步流水线） |

---

## 1. 尝试与结果

| 轮次 | 命令 | 结果 |
|---|---|---|
| 1（逐 code） | `python scripts/etf_minute_reanchor.py prefetch`（v1 实现：McpFreshFetcher.fetch_none_front 逐只） | 首只 90s+ 未完成 → 主动 kill（per-code 全历史 = 全市场网格导出 ×124，不可行） |
| 2（批量） | `python scripts/etf_minute_reanchor.py prefetch`（v2 实现：fetch_table 批量，export_cache=True） | **exit 1**：66 窗全市场导出实拉 2182s（36 分钟），最终 **0 行**（export_cache 读取路径 codes 过滤 124→0） |

## 2. 根因链（export_cache 0 行 bug）

1. `fetch_table(table, ..., codes=[124只])` → `_fetch_export` → `_export_batches` 网格化
   （66 个时间窗 × 每窗全市场 1m 分片 25-51 片，每窗 ~200 万行）；
2. 前 60 窗**实拉**成功（日志 `export_dataset etf_minutes: N shards`），parquet 落盘
   `data/mcp_landing/exp_etf_minutes_*`（**工件完好，18.1GB，含 adj_factor/is_qfq 列**）；
3. 缓存命中段（`export_cache 命中 etf_minutes|窗|片=N codes过滤=124→0`）——**缓存读取
   后 codes 过滤返回 0 行**；汇总 `export 次数=66 总分片=0` → `raw_df 0 行`；
4. 脚本因 `raw_df 无 code 类列`（空 df）exit 1。

**期望**：缓存命中片应返回与实拉等价的该窗全市场行（含 124 只候选）。
**实际**：0 行。疑似缓存键/读取路径与 codes 过滤的交互缺陷（`mcp_adapter.py`
`_export_batches`/`fetch_table` 的缓存读取分支），**待最小复现定位**（独立立项）。

## 3. 关键观测（对 v1.2 的价值）

- 导出的全市场分钟 parquet **含 `adj_factor` 与 `is_qfq` 列**（云端正本）→
  **独立因子参照的优先来源**（`output/g2a_independent_factors.csv` 提取中）；
- 抽样行 `is_qfq=True`（如 159001.SZ 2026-06-17）——**与"云端分钟是不复权原始数据"
  的陈述存在语义矛盾**（标记 True 但值 raw？或值 qfq？），记录待云端侧核实
  （不影响本轨道：因子交叉验证用 adj_factor 列，与 is_qfq 无关）。

## 4. 时间线（2026-08-17）

- 23:41 逐 code 模式启动 → 23:43 kill
- 23:45 批量模式启动 → 23:46-00:22 66 窗实拉（36 分钟）
- 00:22:22 `1m 批量拉取完成: 0 行` → exit 1
- 00:24 确认 `data/mcp_landing/exp_etf_minutes_*` 工件完好（18.1GB）
- 00:28 启动独立因子提取（`_extract_indep_factors.py` → `output/g2a_independent_factors.csv`）

## 5. 最小复现（给 export_cache 立项）

```bash
python scripts/etf_minute_reanchor.py prefetch --main-db data/quantstudio.db
# 期望：124 只 fresh parquet 落盘；实际：exit 1，0 行，日志见上
# 复现要素：export_cache=True + codes=[124] + 全历史时间窗（66 网格窗）
```
