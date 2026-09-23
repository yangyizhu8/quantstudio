# No-Lookahead Rules

> New file aggregated from lifecycle-and-timing-contract.md §3 + master-implementation-plan-v1.0.md §9 @ 2026-07-22
> 权威源：docs/strategy-compiler/lifecycle-and-timing-contract.md §3 + master plan §9
> 本文件为 Skill 派生快照，契约变更时必须同步

The following patterns leak future information into a decision and **MUST block** strategy generation or backtest — not merely warn. `validation_policy.no_lookahead` is `const: "BLOCK"` in the schema (not configurable to ALLOW).

## Hard blocks (6, from lifecycle contract §3)

1. **Pre-open read of same-day full close/high/low for trading.** `before_trading_start` must not see current-day close.
2. **T-day close must NOT form the signal.** `get_history` must use `include=False` (daily and minute) — signal uses T-1 and earlier data only (daily) / previous bar and earlier (minute). Execution at T-day close price (close mode) is safe because the signal does not contain T-day close. `next_open` execution mode is DEPRECATED (2026-08-13) — it introduces T+1 data into the T-day time slice, violating strict PIT semantics.
3. **Using current complete daily high/low to judge intraday triggers that already happened.** Intraday triggers must use intraday data, not daily H/L after the fact.
4. **Fundamentals not PIT by announcement date.** Financial data must use `ann_date` (announcement), not `end_date` (report period), for point-in-time correctness.
5. **`next_open` order changing cash/positions/NAV on T-day.** next_open execution settles at T+1 open; T-day NAV must not reflect it.
6. **Minute signal reading future minutes; aggregated bar used before it completes.** A 5m aggregate must not be consumed before the 5m window closes.

## E1 原则（2026-09-22 定稿）：日线零例外

> 由 SKILL.md 绝对规则区 E1 条目派生；本小节为同一原则的展开与实证记录。

**E1-1 通用历史 API 取数口径（强制）**
以日线为信号源的策略，信号计算一律经 `get_history` / `get_price` / `get_history_batch` 以
`include=False` 读取前一交易日（D-1）日线。取数 API 恒不返回「当前回调所在交易日的日线」——
约束落在 **API 口径层**，不依赖策略作者自觉，不依赖 profile 或回调时点选择。
`include=True` 的日线取数在所有生成策略中禁止；**不存在以「当日已收盘」为理由的例外**。

**E1-2 成交价模式由策略语义裁定**
`close` / `open` 均合法（恐慌抄底、股息防守等已发布策略均为 E1 取数 + `close` 成交）。
当策略语义为「开盘执行」时，必须显式声明 `match_price_mode='open'`——沿用默认 `close`
会使成交价漂移到 D 日收盘，属**执行价差偏差**（非信号时效问题）。引擎 close/open 同属
语义版本 `0.1.0-legacy`（`backtest_engine.py:454-458`）。

**E1-3 执行层数据边界**
`data[code]` 当日 raw 快照仅限**执行层判断**（涨跌停 / 停牌），**不得作为信号输入**；
信号价格一律来自 `get_history(..., fq='pre', include=False)`。执行价基 = raw（`execution_price_basis=raw_trade_price`），
信号价基 = 前复权，两者不得混用。

**E1-4 专门 API 边界（`get_index_day_bar`）**
profile-aware 专门 API 保留其已审定契约——`daily-bar-v1` 下**含当前日 D**
（`ptrade_api.py:1937-1939`：`before_ms = day_end_ms` / `mode = "daily_incl_T"`），
恐慌抄底先例依赖该契约，不因 E1 变更；契约变更须走框架流程。
**消费规则**：凡成交时点早于当日收盘的策略（如 `match open`），消费该 API 时必须显式取 D-1 行
（`count>=2` 取 `[-2]`；或断言末行 `trade_date == D` 后弃用）。
D 日开盘成交而信号消费 D 日收盘指数行 = **真实未来函数泄漏**。
（恐慌抄底先例中 `count=2, pct[-2]` 即 D-1 取法的既有实现；其 `[-1]` 消费因 close 成交而合法——
恰说明「成交时点」限定词的必要性。）

**E1-5 实证依据：四格实测（2026-09-22）**

| 执行日时钟 | `include` | 返回的末行 `trade_date` | 判定 |
| --- | --- | --- | --- |
| 2025-07-01 15:00 | `False` | 2025-06-30（D-1） | 不含 D |
| 2025-07-01 15:00 | `True` | 2025-07-01（D） | 含 D（proxy 语境可用，E1 禁止） |
| 2025-07-02 09:31 | `False` | 2025-07-01（D-1） | ✅ E1 合规取数 |
| 2025-07-02 09:31 | `True` | 2025-07-02（D） | **含 D 全日 bar = 真实泄漏** |

实测探针：`agent_workspace/ou_reversal/probe_include_semantics2.py`（真实 `run_backtest` 跑通，
输出 `trade_date` 列逐格取证）。
> 归档注记：实测运行环境为 `daily-open-close-proxy-v1` profile（该策略已弃用）；
> **include 锚定结论对 profile 通用**——锚定由 `attach_bar` 直写 `_prev_date` 实现
> （`ptrade_api.py:598`），与 profile 选择无关。

**E1-6 校验器落点**
`validate_agent_strategy.py` 的 `NO-LOOKAHEAD-INCLUDE` 规则已实现 E1-1 的 BLOCK
（现行豁免仅限「已确认分钟回调 + 频率 ∈ {1m}」域）；本次定稿**不对校验器做任何改动**。

## High-risk items (10, from master plan §9)

Static + semantic checks must cover these — all are blocking, not warning:

1. `before_trading_start` uses same-day full close.
2. T-day close forms signal AND trades on the same close.
3. Using current complete daily high/low to judge intraday triggers.
4. Ranking or fundamentals using current-day future-available data.
5. `include=True` used incorrectly (future row leakage in joins).
6. Financial data not PIT by announcement date.
7. `next_open` orders booked into T-day ahead of time.
8. Daily-proxy mode match-price口径 inconsistent.
9. Minute signals incorrectly using future minutes.
10. 1m → 5m aggregation seeing an unfinished 5m bar early.

## How the schema enforces this

The `strategy_spec.schema.json` `allOf` rules encode several of these structurally:
- `daily_close_proxy` + `signal_data_cutoff=T-close` + `execution_clock=current_bar` → `not` (hard reject at schema validation, line 77).
- Proxy modes force `market_data_frequency=1d` + `bar_frequency=1d` + matching `match_price_mode` + an `approximations` entry (lines 75-76).

What the schema does NOT catch (validate_strategy_spec.py and downstream IR checks must):
- Fundamentals PIT discipline (ann_date vs end_date) — runtime/IR-level.
- Minute-aggregation timing — runtime/IR-level.
- `include=True` misuse — IR-level (PR6 scan_lookahead.py).

## PR5 boundary

PR5's `validate_strategy_spec.py` enforces schema-level no-lookahead (via jsonschema). IR-level and runtime-level lookahead scans belong to PR6 (`scan_lookahead.py`). A Spec that passes schema validation is not guaranteed lookahead-free — it must still pass PR6 IR checks before smoke backtest.
