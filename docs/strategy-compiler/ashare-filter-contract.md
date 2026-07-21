# A股硬过滤契约

## 1. 股票默认强制项

```json
{
  "exclude_st": true,
  "exclude_suspended": true,
  "exclude_delisted": true,
  "exclude_delisting_sorting": true,
  "exclude_star_market": true,
  "exclude_bse": true,
  "min_listing_trade_days": 252,
  "exclude_invalid_price": true,
  "exclude_zero_volume": true,
  "block_limit_up_buy": true,
  "block_limit_down_sell": true,
  "enforce_t1": true,
  "round_lot": 100
}
```

T+1、停牌、涨跌停、退市/退市整理、无效价格、零成交量和整手是交易真实性门禁，不得静默关闭。科创板、北交所和上市天数属于产品默认排除项，用户调整时必须在 Spec 中显式记录。

## 2. 应用顺序

```text
构建股票池
→ 证券代码规范化（PR1）
→ 静态市场/板块排除
→ 上市天数与退市状态
→ 当日 ST/停牌/无效价格/零成交量
→ 信号计算与排名
→ 下单时再次检查 T+1、涨跌停、停牌、资金和整手
```

过滤不能只在选股阶段执行；订单执行阶段必须重新检查动态状态并记录拒单原因。

## 3. 资产差异

ETF、可转债、期货等必须使用各自 Profile 的 T+0/T+1、税费、最小交易单位和涨跌停规则，不得盲目继承股票 Profile。

## 4. 版本

PR1 runtime authority: `quantstudio/backtest/libs/security_code_rules.py`; `security_code_rules_version=1.0.0`. Runtime callers delegate to the module, and documentation is generated from module metadata.
