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


### API-01 check_limit（平台原生涨跌停判定 · 2026-10-17 建档）
- **签名**：`check_limit(security, query_date=None) -> dict`；`security` 单码或列表；`query_date` 形如 `YYYYMMDD` / 日期对象
- **返回结构**：`{ptrade码: 状态码}`，五态：`1` 涨停 / `-1` 跌停 / `2` **触涨停** / `-2` **触跌停** / `0` 无
- **平台口径**（文档依据）：Context7 `/kay-ou/ptradeapi` → `docs/api-reference-en/utilities.md`：
  `check_limit(security, query_date=None)`，`query_date` 格式 `YYYYMMDD` 默认当日，返回值枚举 `1/-1/2/-2/0`；
  版本注记（同库 `docs/original/Ptrade东莞.md`）：PBOXQT1.0V202202.01.038 起 **支持回测/研究场景**
- **本地实现锚点**：`quantstudio/backtest/ptrade_api.py`（`check_limit` 方法）
- **单一路径数据源**：由 `stock_daily` 原始价（`close`/`preClose`/`high`/`low`，`fq=None`）派生；
  **明确不作为价源**：`stk_limit`（主仓 DuckDB 实测不存在）、`limit_list_*` / `limit_step`（列表/统计表）——防双源口径漂移
- **取整口径**：`up = round(preClose × (1 + pct), 2)`、`down = round(preClose × (1 - pct), 2)`；`pct` 由 `get_price_limit_pct` 提供（含 ST 5% 档）；
  Python `round` 为银行家舍入 ⇒ 以**分价容差 `0.001`** 吸收与交易所四舍五入的差异
- **日期语义（PIT）**：返回 `query_date` **当日**的涨跌停状态；`query_date = D`（当日）在回测语境 = 消费 D 的收盘/最高/最低 ⇒ **仅执行层上下文（收盘后/日终）合法**；
  盘中或盘前一律须传 `query_date = T-1`（或更早）。与 E1 铁律关系：E1 约束**信号价**（`get_history(fq='pre', include=False)`）；本 API 服务**执行层涨跌停判定**（E1 §4 允许 raw 快照用于执行层）⇒ 职责分离
- **⚠ 分歧点（非对齐项 · 显式安全分歧）**：`query_date=None` 时**本地缺省取「上一交易日」，PTrade 原生缺省为「当日」**；
  此为刻意的安全分歧（防盘中泄漏），**不得**在将来对照 PTrade 文档时误判为缺陷；如需严格对齐原生行为，须**显式传当日**
- **边界（记 `QS_DIAG` 后返回 0，禁静默）**：`LIMIT_CHECK_NO_ROW`（该日无行/非交易日）/ `LIMIT_CHECK_NO_PRECLOSE`（无前收或 ≤0）
  / `LIMIT_CHECK_NO_HILO`（无 high/low ⇒ ±2 不可判，退化为 ±1 判定）/ `LIMIT_CHECK_NO_PREV_DAY`（缺省日期不可解析）
- **校验器守卫**：`LIMIT-CHECK-EXPLICIT-DATE`（省略 `query_date` ⇒ BLOCK）、`LIMIT-CHECK-SAMEDAY`（字面量=回测窗口端点 ⇒ BLOCK）
- **⚠ 守卫生效前提（已知限制，明示入档）**：`LIMIT-CHECK-SAMEDAY` 的静态判据是「`query_date` 为字面量且命中**设计声明的回测窗口端点**」；而 design 2.3 的 `backtest_window_contract` **不含窗口日期字段**（窗口由客户确认，见 `actual_window_selected_by`）⇒ 该规则在**当前设计契约下为 inert**（不触发），实际防护由 `LIMIT-CHECK-EXPLICIT-DATE` + 本条日期条文 + 运行时 `QS_DIAG` 共同承担。将来若 design 增补 `backtest_window_contract.start_date/end_date`，该规则**自动生效**，无需改代码。
- **契约测试指针**：待 R1-C 后补（负向 3 例 + 守卫 3 例 + ±2 实测 2 例 + 边界 3 例 + PIT 抽验 1 例）
- **状态**：本地已实施（提交 `5116f19`）；**平台直通**（`portability_rules.py:215` 原生名单）
