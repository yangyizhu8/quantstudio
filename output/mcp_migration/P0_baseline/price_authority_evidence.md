# P0 价格 Authority 证据（只读快照，零改动）

> 本文件为 P0 基线冻结的一部分，记录 QuantStudio 侧"价格权威源 = xtquant"的落地证据。
> 红线：本轮**禁止修改** `daemon.py:420-446` 价格 authority 守卫（原文照录，未改动任何字符）。

## 守卫位置
`quantstudio/pipeline/daemon.py` 第 420–446 行（子区间 426–446 为具体常量定义）。

## 原文证据（代码片段，未改动）
```python
# daemon.py:420-446 (价格 authority 守卫，本轮只读取证)
MINUTE_AUTHORITY = "xtquant"   # 分钟级价格唯一权威源
DAILY_AUTHORITY  = "xtquant"   # 日级价格唯一权威源

def _reanchor_qfq_snapshot(snapshot_rows, fresh_source=MINUTE_AUTHORITY):
    """
    ...
    fresh_source="xtquant"  # 硬编码锁定：qfq 复权快照以 xtquant 实时行情为权威基准
    """
    ...
    reanchored = _reanchor_with_fresh(fresh_source="xtquant", ...)
```

## source_watermark 佐证（同目录 source_watermark_snapshot.json）
| 表 | 权威源 | 证据 |
|----|--------|------|
| stock_daily / stock_minutes | xtquant | watermark.source=xtquant，freq=daily/1min |
| etf_daily / etf_minutes | xtquant | watermark.source=xtquant |
| balance/income/cashflow_statement | xtquant + tushare 双源 | 财务以 xtquant 为主、tushare 兜底 |
| index_daily / stock_daily_valuation | tushare | 非价格行情，属衍生/基准数据 |

## 对 MCP 契约的约束（B 阶段沿用）
- MCP server 返回的价格数据须与 xtquant 权威口径一致；复权（adj_factor × raw）在**客户端**完成，
  不得以服务端前端复权值覆盖本地 xtquant 权威（见 B5 职责边界）。
- 若 MCP 客户端接入云端 QuestDB 的 `stock_daily`（含 `open_back/close_back` 前端复权列），
  必须显式以 `raw × adj_factor` 重算，不得信任云端预计算复权列作为本地权威替代。
