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
| `daily-bar-v1` | 是 | 是 | 日线可用 | 逐日引擎可用 | READY（但 next_open 仍是 legacy 语义） |
| `minute-bar-v1` | 是 | 是 | frequency 未贯通 | 分钟事件循环未完成 | BLOCKED |
| `tick-l2-v1` | 是 | 预留 | 未完成 | 未完成 | PLANNED |

分钟 Profile 在 PR3/PR4 验收前不得改成 READY。Tick/L2 在 PR9 前不得改成 READY。

## 3. 频率路由目标

| 资产 | `1d` | 分钟 | Tick |
|---|---|---|---|
| 股票 | `stock_daily` | `stock_minutes` | 预留 Tick 表/Profile |
| ETF | `etf_daily` | `etf_minutes` | 预留 |

Provider 必须接收并贯通 frequency；不支持时返回明确能力错误，不得回退日线冒充分时数据。

## 4. READY 判定

只有 Schema、数据覆盖、Adapter、Provider、Engine、目标平台与输出目录全部满足要求，且对应冒烟测试通过，Profile 才可宣称 READY。代码仅通过语法检查不等于可执行。
