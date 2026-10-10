# V-4 黄金对照 —— Run A（对照：期望完全一致）

- 日期：2026-10-10　｜　方案：`docs/r1-framework-fix-design-20261009.md`（V-4）
- 策略：`quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py`（`get_fundamentals(fields=['float_value','circulating_market_cap'])` 的**排序消费**型——尺度不变，期望零变化）
- 窗口：2026-01-05 ~ 2026-02-27（36 个交易日）｜档位：`daily-bar-v1`｜数据源：副本库 `r55_readonly`
- 对照口径：修复前 = worktree@`b099ec0`（无 D-2/D-3/D-1/D-1b）；修复后 = 工作区（四项修复全在）

## 1. 结果：**7 项产物 SHA-256 全部逐位一致**

| 产物 | 修复前 | 修复后 | 判定 |
| --- | --- | --- | --- |
| `config.csv` | `8E3B0368F1A0…` | `8E3B0368F1A0…` | **一致** |
| `daily_stats.csv` | `A261A615FF06…` | `A261A615FF06…` | **一致** |
| `trades.csv` | `72505868F949…` | `72505868F949…` | **一致** |
| `round_trips.csv` | `4F168ECB4D9F…` | `4F168ECB4D9F…` | **一致** |
| `benchmark.csv` | `E8C770F471FA…` | `E8C770F471FA…` | **一致** |
| `ptrade_metrics.csv` | `F7A13A34E503…` | `F7A13A34E503…` | **一致** |
| `ptrade_metrics.json` | `790F0AD6ADB1…` | `790F0AD6ADB1…` | **一致** |

行数对照：`daily_stats` 35/35、`trades` 364/364、`round_trips` 179/179 ⇒ 逐日净值、逐笔成交、回合结构**完全同构**。

## 2. 结论

- **D-2/D-3（估值契约）** 对排序消费型策略**零可观察影响** ✓（尺度换算不改排名，与前置影响面分析一致）
- **D-1/D-1b（分钟档 preclose/high_limit、ST 档位）** 在日线档对本样本**无影响**（该策略不交易 ST 股、不依赖 high_limit 判据）
- ⇒ 对照样本满足「**修复前后零衰减**」硬门

## 3. 运行方式（可复跑）

```
# 修复前（worktree）与修复后（工作区）同参各跑一次，随后比对产物哈希
QUANTSTUDIO_DATA_ROOT=<r55_readonly> python <tree>/quantstudio/backtest/run_ptrade_strategy.py \
    <tree>/quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py \
    2026-01-05 2026-02-27 --profile daily-bar-v1
```

作业方式：后台作业（单次回测 >10 分钟 ⇒ 前台会触顶超时；两处自纠入册）。

## 4. Run B / Run C 设计（进行中）

| 轮次 | 样本 | 期望 | 状态 |
| --- | --- | --- | --- |
| **B** | `ETF轮动.py`（ORM `query(valuation.market_cap)` + `filter(market_cap 20~30)`） | **声明性变化**：修复前 ORM `/1e8` 致量级错（恒无匹配 ⇒ 无交易）→ 修复后 `/1e4` 恢复正常匹配 | 已启动（`pwsh-8`/`pwsh-9`） |
| **C** | 分钟档样本（验 D-1/D-1b） | 差异**只能归因于** `preclose/high_limit` 恢复与 ST 档位修正 | 待启动 |
---

# V-4 黄金对照 —— Run B（声明性变化：ORM 估值单位修正）

- 策略：`quantstudio/backtest/strategies/ETF轮动.py`（ORM `query(valuation.code, valuation.market_cap).filter(market_cap >= 20, <= 30)`）
- 窗口 2026-01-05 ~ 2026-02-27｜`daily-bar-v1`｜副本库

## 1. 结果

| 产物 | 修复前 | 修复后 | 判定 |
| --- | --- | --- | --- |
| `config.csv` | `434DF21D3B6C…` | `434DF21D3B6C…` | **一致**（证明同参） |
| `daily_stats.csv` | `CA806AC96A8C…`（35 行） | `30223B096A36…`（35 行） | 不一致（**预期**） |
| `trades.csv` | **无文件（0 笔）** | `000D054FB965…`（**39 笔**） | 不一致（**预期**） |
| `round_trips.csv` | `F01A374E9C81…`（**0 回合**） | `EEB7E9B7F9F0…`（**18 回合**） | 不一致（**预期**） |

月度收益：修复前 **+0.00% / +0.00%**（全程空跑）→ 修复后 **+2.39% / +13.16%**。

## 2. 归因（逐项）

| 差异 | 归因 | 性质 |
| --- | --- | --- |
| 修复前 0 笔成交、0 回合、月收益 0% | `query_valuation_orm` 对 `stock_daily_valuation.total_mv`（**万元**）误除 `1e8` ⇒ 市值量级偏小 1e4 倍 ⇒ `market_cap ∈ [20,30]`（亿元）**恒无匹配** ⇒ 策略静默失效 | **D-3 数据修正型**：修复前口径错误 |
| 修复后 39 笔、18 回合、月收益 +2.39%/+13.16% | 改除 `1e4` ⇒ 亿元口径正确 ⇒ 过滤按设计匹配 | 恢复设计意图（非新增行为） |
| `config.csv` 逐位一致 | 参数/窗口/引擎档位未变 | 证明差异非参数漂移 |

## 3. 结论

- Run B 的**全部**差异可归因于 D-3 单位修正这一条，且方向为「从静默失效恢复为设计意图」 ⇒ **属已声明差异类，非回归**。
- 该策略修复前后对照同时构成 D-3 的**功能级证据**（修复前该策略不可用）。

> 附注：`ETF轮动.py` 名为 ETF 实为按市值选股的股票策略（历史命名遗留），不影响对照效力。
---

# V-4 黄金对照 —— Run C（分钟档对照：期望一致）

- 策略：`smallcap_overnight_scalp_7_quantstudio.py`｜档位：**`minute-bar-v1`**（engine semantics `0.3.0-minute-bar`）
- 窗口：2026-01-05 ~ 2026-01-16（10 个交易日）｜副本库
- 对照：修复前 worktree@`b099ec0` vs 修复后工作区

## 1. 结果：**4 项产物 SHA-256 全部逐位一致**

| 产物 | 修复前 | 修复后 | 行数 pre/post | 判定 |
| --- | --- | --- | --- | --- |
| `config.csv` | `5CA3C529BF19…` | `5CA3C529BF19…` | 2 / 2 | **一致** |
| `daily_stats.csv` | `93DA9DD7B3AE…` | `93DA9DD7B3AE…` | 11 / 11 | **一致** |
| `trades.csv` | `92346E8C9D97…` | `92346E8C9D97…` | 26 / 26 | **一致** |
| `round_trips.csv` | `34FF0302E505…` | `34FF0302E505…` | 13 / 13 | **一致** |

`config.csv` 逐字段相同（同参、同 `match_price_mode=close`、同引擎语义版本）⇒ 差异非参数漂移。

## 2. 归因与解读

- 该策略**不消费** `data[code].high_limit/preclose`，其订单拒单走**日级 `pctChg`** 路径（`test_minute_uses_daily_pctchg_not_bar_pctchg` 已锁定该契约）⇒ D-1/D-1b 对该样本**零可观察影响** ✓
- ⇒ Run C 是分钟档的**无回归对照**；D-1/D-1b 的**正向证据**由 V-2 专项断言提供（002743 更名日 10%→5%、002442 涨停日 `high_limit` = 涨停价，两档位一致）。

## 3. 过程自纠（入册）

首次比对误取「最新结果目录」⇒ 把**修复前分钟档**与**修复后日线档（Run A 产物）**错配比对（假不一致）。
已改为**按显式目录名**取配对；教训：跨档位对照必须显式指定两侧目录，不得用「最新」推断。

---

# V-4 汇总（Run A/B/C + 回归 + 字段级证据）

| 轮次 | 样本/档位 | 期望 | 实测 | 判定 |
| --- | --- | --- | --- | --- |
| A | 排序消费型 / 日线档 | 零变化 | 7 项产物逐位一致 | **PASS** |
| B | ORM 市值过滤 / 日线档 | 声明性变化 | 0 笔 → 39 笔；月收益 0% → +2.39%/+13.16%；`config` 同参 | **PASS（差异全归因 D-3）** |
| C | 不消费档位字段 / 分钟档 | 零变化 | 4 项产物逐位一致 | **PASS** |
| 回归 | 6 文件测试子集 | 零新增 | 8 failed / 56 passed = 基线 | **PASS** |
| 字段级 | V-2 专项（两档位） | ST 5% / 涨停价正确 | 002743 更名日切换、002442 涨停日一致 | **PASS** |

**已声明差异类（V-4 归因口径）**：
1. **D-3 ORM 单位修正**：ORM 面 `market_cap/circulating_market_cap` 由错量级恢复亿元 ⇒ 相关策略从静默失效恢复设计意图（Run B 实证）。
2. **D-1b ST 档位修正**：ST/*ST 股 `high_limit` 由 10% 纠正为 5%（普通股不变）⇒ 交易 ST 股的策略涨停判据变准（V-2 实证）。