# Frequency 与 Engine Profile 契约

> Derived from docs/strategy-compiler/frequency-and-engine-profile.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

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

| Profile | Spec 可表达 | Schema |
|---|---:|---:|
| `daily-bar-v1` | 是 | 是 |
| `minute-bar-v1` | 是 | 是 |
| `tick-l2-v1` | 是 | 预留 |

Profile 执行状态（READY/BLOCKED/PLANNED 等）由能力探测运行时决定，不在此硬编码——必须以 `capability_report.json` 的实时结果为准（见 `api-capability-matrix.md` 与 SKILL.md 的 R-1 步骤）。

**硬规则**：
- 分钟 Profile 在分钟 Provider/Engine/数据全部 READY 之前，不得标为 READY。
- Tick/L2 第一版执行状态只能是 `BLOCKED`、`PLANNED` 或 `UNSUPPORTED`，永不 READY。

## 3. 频率路由目标

| 资产 | `1d` | 分钟 | Tick |
|---|---|---|---|
| 股票 | `stock_daily` | `stock_minutes` | 预留 Tick 表/Profile |
| ETF | `etf_daily` | `etf_minutes` | 预留 |

Provider 必须接收并贯通 frequency；不支持时返回明确能力错误，**不得回退日线冒充分时数据**。

路由实现约束：`get_bars(frequency='1d')` 走日线路径（字节级不变）；分钟频率查询 `stock_minutes`/`etf_minutes` 的原生 freq 列（1min/5min/15min/30min/60min，每个 freq 独立存储一份）。需某 freq 但表中无时返回结构化能力错误（列出可用 freq 集合）。1m→5m/15m/30m/60m 实时聚合推迟至真实分钟数据入库后出现原生 freq 缺口的实际需求时再实现。

## 4. READY 判定

只有 Schema、数据覆盖、Adapter、Provider、Engine、目标平台与输出目录全部满足要求，且对应冒烟测试通过，Profile 才可宣称 READY。代码仅通过语法检查不等于可执行。
