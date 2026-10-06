# 契约档案 · 注入 API 契约（API 类）· 骨架 v1

> 对应注册表 [../registry.md](../registry.md) §API。P2 阶段从 `quantstudio/backtest/ptrade_api.py`
> 暴露面**全量枚举**条目（预期约 40 条），逐条建档。

## 建档模板（每条）

```
### API-nn <函数名>
- 签名 / 参数语义 / 返回结构
- 平台口径（文档依据 Context7 /kay-ou/ptradeapi <文档名> + 平台实证指针）
- 本地实现锚点（文件:行）
- 边界（空值/异常/日期边界/fallback）
- 契约测试指针
- 状态
```

## 已锚定存量

### API-00 平台吸收规则集（批次锚定）
- 一批 get_history / include / fq 等语义的平台吸收修复；
- 证据：`docs/evidence/ptrade-platform-absorptions-20260903.md`（指针不复制）。

## 已知重要单项（待 P2 正式编号建档）

- `get_history` include=False D-1 锚定（E1 铁律，ptrade_api.py:598 `attach_bar` 直写 `_prev_date`）
  ——与 DAT-03 复用同一证据链；
- `get_index_day_bar` 专门 API 契约（daily-bar-v1 含当前日 D，恐慌抄底先例）——契约已审定，
  边界警示：match open 策略须显式取 D-1 行（AGENTS.md E1 §7）。

## 待枚举清单（P2）

get_history / get_price / get_history_batch / get_etf_list_local / order / order_value /
order_target / order_target_value / order_market / get_positions / get_position /
get_orders / get_open_orders / cancel_order / set_universe / set_benchmark / set_cost /
set_slippage / set_commission / run_daily / run_weekly / run_monthly / write_log /
get_current_data / get_trading_day / get_trade_days / get_cash / get_stock_name /
get_stock_info / get_price_change_rate / get_stock_blocks / get_fundamentals / …
（以 ptrade_api 实际暴露面为准全量清点）