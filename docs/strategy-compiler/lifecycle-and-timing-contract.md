# 生命周期与时序契约

## 1. 生命周期

默认 PTrade 公共生命周期：

```text
initialize(context)                       # 一次
before_trading_start(context, data)       # 每交易日盘前
handle_data(context, data)                # 日线引擎每日一次；分钟引擎按 Bar
run_daily callback                        # 由 Engine Profile 的时钟触发
资金/持仓估值与记录
finally: after_trading_end(context, data) # 每交易日盘后
```

Renderer 必须依据目标 Profile 生成生命周期，不得假设所有券商部署完全相同。

## 2. Time Model 字段

- `market_data_frequency`：底层行情粒度。
- `factor_frequency`：指标更新粒度。
- `signal_frequency`：信号计算粒度。
- `decision_clock`：策略做出决策的生命周期点或精确时刻。
- `execution_clock`：订单可以成交的 Bar/交易日。
- `portfolio_valuation_frequency`：净值估值粒度。
- `holding_period_unit`：持有期单位。
- `signal_data_cutoff`：信号允许看到的数据截止点。

禁止将这些含义折叠到一个 `frequency`。

## 3. 无未来函数规则

下列情况必须阻断：

1. 盘前读取当日完整 close/high/low 并用于交易。
2. T 日完整 close 形成信号并以同一 close 成交。
3. 当前完整日线 high/low 被用来判断已发生的盘中触发。
4. 财务数据未按公告日 PIT。
5. `next_open` 在 T 日提前改变现金、持仓或净值。
6. 分钟信号读取未来分钟；聚合 Bar 未结束时提前使用完整聚合值。

## 4. next_open 冻结语义

目标语义版本要求：

```text
T 日产生订单 → pending queue
T 日不改变现金/持仓/成交记录
T+1 开盘检查停牌、涨跌停、资金和整手
T+1 成交或带原因拒单
T+1 更新现金、持仓、订单和成交日期
```

当前引擎尚未实现该语义，`engine_semantics_version=0.1.0-legacy`。PR2 修复时必须提升版本并更新 Run Card/Changelog。

## 5. 日线代理

- `daily_open_proxy ⇔ match_price_mode=open`
- `daily_close_proxy ⇔ match_price_mode=close`
- 必须保存用户原始执行时刻、代理原因、风险说明和确认记录。
- close 代理若依赖当日完整 close 信号，必须改为下一交易日执行，或在分钟能力 READY 后改为近收盘信号。
