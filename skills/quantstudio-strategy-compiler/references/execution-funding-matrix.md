# Execution Funding Matrix（执行资金可用性矩阵）

> 固化当前本地引擎的**真实语义**，供 R2/R2.5 设计与 R4 校验交叉核对。
> 本矩阵是验证输入，不是引擎修改请求；任何语义变化都必须走独立的框架行为变更流程。

## 1. 矩阵

| match_price_mode | 决策 callback | basket_active | 同批卖出资金能否供同批买入 |
|---|---|---|---|
| `close` | `run_daily` | 否 | **能**，即时顺序执行（先卖后买在同一回调内生效） |
| `open` | `run_daily` | 否 | **能**，即时顺序执行 |
| `next_open` | `run_daily` | 否 | **不能**，legacy pending 语义（卖出 T 日提交、次一交易日成交） |
| `next_open` | `before_trading_start` | 否 | **不能**，legacy pending 语义 |
| `next_open` | `handle_data` | 是（`rebalance_mode=callback_basket`，`daily-bar-v1`） | **能**，basket 卖出优先成交后再买入 |
| 未知组合 | 任意 | 任意 | **BLOCK**（不允许 agent 猜测引擎语义） |

## 2. Design 契约字段

```json
"rebalance_funding_contract": {
  "requires_same_cycle_sell_proceeds": true,
  "implementation_mode": "sell_then_buy_immediate",
  "cash_only_for_new_buys": false
}
```

`implementation_mode` 允许值：

- `sell_then_buy_immediate` — 同一回调内先卖后买；仅 close/open 即时语义下可依赖同批卖出资金；
- `basket_atomic_sell_first` — `next_open` + `handle_data` + basket_active=true，basket 原子先卖后买；
- `staged_two_phase` — 两阶段调仓：第一阶段只卖出，下一阶段再买入；对所有 match mode 兼容；
- `cash_only_new_buys` — 新增买入只使用现金，不依赖任何同批卖出资金。

## 3. 交叉校验规则（R2/R4）

- `next_open` + `run_daily`/`before_trading_start` + `requires_same_cycle_sell_proceeds=true`
  + `sell_then_buy_immediate` → **BLOCK**（`EXECUTION-SELL-PROCEEDS-UNAVAILABLE`）；
- `basket_atomic_sell_first` 必须同时满足：`profile_id='daily-bar-v1'`、`match_price_mode='next_open'`、
  决策 lifecycle 含 `handle_data`、`engine_profile.rebalance_mode='callback_basket'`，并且必须显式声明
  `engine_profile.expected_engine_semantics_version='0.4.0-next_open_basket'`（Schema 条件约束 + Validator
  双重强制）——真实引擎只在三个条件同时成立时激活 basket，缺少语义版本声明时 R5 没有可比对的
  激活证据。任一缺失 → **BLOCK**（`EXECUTION-BASKET-REQUIRED`）。仅声明 `next_open + handle_data`
  不能证明 basket 已激活；
- R5 必须将 config.csv 的 `engine_semantics_version` 与设计的 `expected_engine_semantics_version`
  比对（`0.4.0-next_open_basket` 即 basket 真实激活的证据），不符 → `ARTIFACT-ENGINE-SEMANTICS-MISMATCH`；
- `cash_only_for_new_buys=true` 与 `requires_same_cycle_sell_proceeds=true` 同时成立 → **BLOCK**（`EXECUTION-FUNDING-INCOMPATIBLE`）；
- 未知 match mode 或未知 implementation_mode → **BLOCK**（`EXECUTION-FUNDING-INCOMPATIBLE`）；
- 全量换仓需求落在 `next_open` legacy pending 上且既不用 basket 也不用两阶段 → 退回 R2/R3 重设计（`EXECUTION-STAGED-REBALANCE-REQUIRED`）。

## 3.1 PyQt rebalance_mode 透出（2026-07-27，F1）

- PyQt 回测控制台新增通用 `rebalance_mode` 下拉框（内部值 `legacy` /
  `callback_basket`，显示文本不作为引擎参数），默认 `legacy`，经
  `EngineConfig.rebalance_mode` 单一路径传入引擎；
- GUI 组合校验：`callback_basket` 仅适用于 daily-bar-v1 + `next_open`，
  `close`/`open` + `callback_basket` 在点击运行前阻断；分钟引擎不支持 basket；
- 生命周期边界不变：`run_daily` / `before_trading_start` 订单永不进入 basket；
  需要 basket 的策略必须把调仓下单放入 `handle_data`；
- PyQt R5 证据要求 basket 时，config.csv 必须出现
  `engine_semantics_version=0.4.0-next_open_basket`，否则 R4/R5 BLOCK。

## 4. 现金缓冲不是通用解

Skill 文档与 agent 设计必须明确：

- 现金缓冲只能解决**费用、整手取整和轻微价格漂移**；
- 它**不能**普遍解决高换手轮动策略的卖出资金时序问题；
- `next_open` + `run_daily` legacy 语义下，15% 现金缓冲只能支持有限的新增仓位，无法完成全量换仓；
- 设计要求全量换仓时，必须使用 basket（`next_open + handle_data + basket_active`）或两阶段调仓；
- 不得把 `0.85` 之类的固定暴露率写成所有策略的通用模板参数；暴露率必须来自逐项确认的 `portfolio_contract`。
