# Frequency 与 Engine Profile 契约

## 1. Profile 字段

```json
{
  "profile_id": "daily-bar-v1",
  "event_type": "bar",
  "bar_frequency": "1d",
  "market_depth": "L1",
  "order_book_levels": 0,
  "schema_supported": true,
  "execution_status": "READY"
}
```

`schema_supported` 只表示结构可表达；`execution_status` 才表示整条执行链是否可运行。

## 2. 冻结 Profile

| Profile | Spec 可表达 | Schema | Provider | Engine | 当前执行状态 |
|---|---:|---:|---:|---:|---|
| `daily-bar-v1` | 是 | 是 | 日线可用 | 逐日引擎可用 | READY（next_open 在 PR2 后为真实 pending queue） |
| `minute-bar-v1` | 是 | 是 | PR3 已贯通频率路由 | PR4 分钟事件循环已完成（待真实数据冒烟） | BLOCKED（data_status=DATA_MISSING） |
| `tick-l2-v1` | 是 | 预留 | 未完成 | 未完成 | PLANNED |

`minute-bar-v1`：PR3 已完成 Provider 频率路由（provider_status=AVAILABLE，仅表代码链路就绪，不得解释为分钟回测可用）。仍 BLOCKED 的原因：data_status=DATA_MISSING（分钟表空）+ engine_status=ENGINE_MISSING（PR4 分钟事件引擎未完成）。

分钟 Profile 在 PR4 验收前不得改成 READY。Tick/L2 在 PR9 前不得改成 READY。

## 3. 频率路由目标

| 资产 | `1d` | 分钟 | Tick |
|---|---|---|---|
| 股票 | `stock_daily` | `stock_minutes` | 预留 Tick 表/Profile |
| ETF | `etf_daily` | `etf_minutes` | 预留 |

Provider 必须接收并贯通 frequency；不支持时返回明确能力错误，不得回退日线冒充分时数据。

PR3 路由实现：`get_bars(frequency='1d')` 走日线路径（字节级不变）；分钟频率查询 `stock_minutes`/`etf_minutes` 的原生 freq 列（1min/5min/15min/30min/60min，每个 freq 独立存储一份）。需某 freq 但表中无时返回结构化能力错误（列出可用 freq 集合）。1m→5m/15m/30m/60m 实时聚合经用户 2026-07-21 批准推迟至 PR3.5（触发条件：真实分钟数据入库后出现原生 freq 缺口的实际需求）。

## 4. READY 判定

只有 Schema、数据覆盖、Adapter、Provider、Engine、目标平台与输出目录全部满足要求，且对应冒烟测试通过，Profile 才可宣称 READY。代码仅通过语法检查不等于可执行。
