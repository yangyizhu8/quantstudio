# 01-06 逐条件对拍 · 判据表（预钉，先于取数）

- 日期：2026-01-06 ｜ 目标票：`600556.XSHG`、`600587.XSHG`（聚宽当日选股并买入 2 只 ✓）
- 聚宽源码：`agent_workspace/first_board_high_open_1to2/SOURCE_A_joinquant.md`（L126-177 `get_stock_list`）
- 本地实现：`output/generated_strategies/first_board_high_open_1to2/strategy.py`（L166-241 选股段）
- **本表在取数之前写定**（预钉纪律：先写预期，再取实际，防把语义假设当判据）

## 一、六条件阈值对照（源码 vs 本地常量）

| # | 条件 | 聚宽源码表达式 | 阈值 | 本地常量 | 一致性 |
| --- | --- | --- | --- | --- | --- |
| 1 | 均价涨幅 | `money[0]/volume[0]/close[0]*1.1 - 1` | `>= 0.07` | `AVG_PRICE_INCREASE_MIN=0.07` | ✅ |
| 2 | 成交额 | `money[0]` | `[5.5e8, 20e8]` | `PREV_MONEY_MIN/MAX` | ✅ |
| 3 | 市值 | `market_cap[0]` | `>= 70`（亿） | `MARKET_CAP_MIN_YI=70.0` | ✅ |
| 4 | 流通市值 | `circulating_market_cap[0]` | `<= 520`（亿） | `CIRC_MARKET_CAP_MAX_YI=520.0` | ✅ |
| 5 | 竞价量比 | `auction.volume[0] / prev_day.volume[-1]` | `>= 0.03` | `AUCTION_VOLUME_RATIO_MIN=0.03` | ✅ |
| 6 | 开比 | `auction.current[0] / prev_day.close[-1]` | `(1.00, 1.06)` 严格 | `AUCTION_OPEN_RATIO_MIN/MAX=1.00/1.06` | ✅ 阈值一致，**基准可能不同** ⚠ |
| 7 | 左压 | `volume[-1] > max(volume[:-1]) * 0.9`（61 根，zyts=zyts_0+5） | — | `LEFT_PRESSURE_BARS=61` | ✅ |

## 二、⚠ 已识别的语义差异（取数前登记）

| # | 差异 | 源码 | 本地 | 影响面 |
| --- | --- | --- | --- | --- |
| D1 | **开比基准** | `prev_day_data['close'][-1]`（T-1 收盘，**未复权**） | `_bar_preclose(current_data, code)` → **preClose**（除权调整后），fallback 才用 daily close | **仅除权日**分叉；非除权日 preClose == T-1 close ⇒ 相同 |
| D2 | 日线复权口径 | `attribute_history(..., skip_paused=True)` 默认 **fq='pre'**（聚宽默认前复权） | `_fetch_history(..., fq='pre', include=False)` | 若两者 base date 不同（聚宽以当日为基准 vs 本地以最新为基准）⇒ 除权日数值不同 ⚠ |

## 三、预钉预期（取数前写定）

**已知事实**：聚宽 01-06 **实际买入** `600556`、`600587` ⇒ 两票在聚宽侧 **7 条件全 PASS** ✓

| 票 | 条件 1 | 2 | 3 | 4 | 5 | 6 | 7 | 预期总判定 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 600556 | ≥0.07 ✓ | 区间内 ✓ | ≥70 ✓ | ≤520 ✓ | ≥0.03 ✓ | (1,1.06) ✓ | 通过 ✓ | **PASS（应入选）** |
| 600587 | ≥0.07 ✓ | 区间内 ✓ | ≥70 ✓ | ≤520 ✓ | ≥0.03 ✓ | (1,1.06) ✓ | 通过 ✓ | **PASS（应入选）** |

**待验要点**（结论分支预置）

| 分支 | 观测 | 结论 |
| --- | --- | --- |
| A | 两票**不在** 01-06 池（池=64） | ⇒ **池构建缺陷**（涨停判定/硬过滤）⇒ 回修 S-1 或硬过滤 |
| B | 两票在池，但某条件取值与聚宽不符（差值大） | ⇒ **数据口径差异**（D1/D2）⇒ 定位到具体字段 |
| C | 两票在池，取值一致但**判定翻转** | ⇒ **时点/边界**（09:27 vs 09:31，D-A1）⇒ 取 09:27 与 09:31 双时点数据 |

## 四、A-1 择时并行取数项

- 聚宽：`get_buy` 挂 **09:27**（源码 L59 + `run_daily(get_buy, '09:27')`）
- 本地：`run_daily(context, get_buy, time='09:31')`（注释称「近似 A-1」，引擎分钟循环首根 bar 09:31）
- **须取数**：`auction.volume`（09:30 竞价 bar）与 `auction.current` 在两时点是否相同 ⇒ 若相同则 A-1 不影响条件 5/6 ✓