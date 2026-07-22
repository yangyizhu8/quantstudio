# A 股硬过滤与证券代码规则

> Derived from docs/strategy-compiler/ashare-filter-contract.md + docs/strategy-compiler/security-code-rules.md @ 2026-07-22
> 权威源：docs/strategy-compiler/ 下的原始契约文档
> 本文件为 Skill 派生快照，契约变更时必须同步（见 SKILL.md 同步纪律）

本 reference 聚合两个权威源：A 股交易真实性硬过滤（§1–§4）与证券代码/市场分类规则（§5–§7）。

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

**T+1、停牌、涨跌停、退市/退市整理、无效价格、零成交量和整手是交易真实性门禁，不得静默关闭。** 科创板、北交所和上市天数属于产品默认排除项，用户调整时必须在 Spec 中显式记录。

## 2. 应用顺序

```text
构建股票池
→ 证券代码规范化（见 §5–§7）
→ 静态市场/板块排除
→ 上市天数与退市状态
→ 当日 ST/停牌/无效价格/零成交量
→ 信号计算与排名
→ 下单时再次检查 T+1、涨跌停、停牌、资金和整手
```

**过滤不能只在选股阶段执行；订单执行阶段必须重新检查动态状态并记录拒单原因。**

## 3. 资产差异

ETF、可转债、期货等必须使用各自 Profile 的 T+0/T+1、税费、最小交易单位和涨跌停规则，**不得盲目继承股票 Profile**。

## 4. 版本

Runtime authority: `quantstudio/backtest/libs/security_code_rules.py`; `security_code_rules_version=1.0.0`. Runtime callers delegate to the module, and documentation is generated from module metadata. **本文档永不作为运行时依赖。**

## 5. 后缀别名

| Market | Accepted input | QMT output | PTrade output |
|---|---|---|---|
| Shanghai | `.SH` / `.SS` / `.XSHG` / bare | `.SH` | `.SS` |
| Shenzhen | `.SZ` / `.XSHE` / bare | `.SZ` | `.SZ` |
| Beijing | `.BJ` / `.XBJ` / `.XBSE` / bare | `.BJ` | `.BJ` |

## 6. 北交所边界

- Current BSE equity range: `920`.
- Legacy compatibility uses the exact official old/new mapping, never a blanket `4`/`8` prefix rule.
- Official legacy mapping count: **248**.
- Legacy prefixes present in the official mapping: `430`, `830`, `831`, `832`, `833`, `834`, `835`, `836`, `837`, `838`, `839`, `870`, `871`, `872`, `873`.
- `400xxx`, `420xxx`, and arbitrary unmapped `8xxxxx` codes are **not** BSE equities.
- Mapping source: `https://www.bse.cn/service/code_mapping.html`.

## 7. 分类优先级与兼容策略

分类优先级：

```text
index -> bse -> star_market -> chinext -> convertible_bond -> etf -> main_board -> unknown
```

兼容规则：
- Suffix aliases are normalized, but historical security numbers are **not** rewritten to `920`; this preserves historical-data lookup semantics.
- Unknown bare codes retain the pre-PR1 Shanghai fallback.
- ETF, index, and convertible-bond checks precede main-board checks to prevent overlapping-range misclassification.
