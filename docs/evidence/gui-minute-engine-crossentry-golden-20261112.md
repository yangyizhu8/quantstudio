# 证据 · GUI 分钟引擎跨入口黄金对照（④验收条件①）· 2026-11-12

> 被验对象：`feat(gui)` commit `6215dff`（GUI 回测 tab 支持分钟引擎 + 生成目录可见）
> 对应方案：`docs/gui-backtest-minute-engine-design-20261111.md`（②审通过版）
> 验收判据：方案 §六 第 5/6 条 —— 同锚跨入口对等

## 一、结论

| 项 | 读数 |
| --- | --- |
| **golden_sha256** | **`b4b538a0ca0eff1ae69a283fe15b1793b2c3ed6e00b7897ea2f0757caf85fabb`** |
| **锚（探针基线）** | `b4b538a0ca0eff1ae69a…` |
| **是否逐位相同** | ✅ **MATCH = True** |
| nav_days | **5**（与探针基线 5 一致） |
| trades | **4**（与探针基线 4 一致） |
| wall_sec | 300.407 |
| export_dir | `output/backtest_results/20261112_033933_strategy` |

**⇒ 跨入口对等成立**：GUI 参数集（含新增 `engine_profile='minute-bar-v1'`）经
引擎产出的结果，与命令行探针在同一策略/同一窗口/同一引擎上的结果**逐位相同**
⇒ **参数链 GUI → params → run_backtest 无信息丢失**。

## 二、实际参数集（实测传入）

| 参数 | 值 | 来源 |
| --- | --- | --- |
| `engine_profile` | **`minute-bar-v1`** | ★ 本次改动新增（GUI 选 minute） |
| `match_price_mode` | `close` | GUI `match_price_combo` 第一项 |
| `match_profile` | `ptrade` | GUI `match_profile_combo` 第一项 |
| `capital` | 100000 | GUI `capital_spin` 默认 |
| `cost` | `TradeCost(commission_rate=0.00035, min_commission=5.0, stamp_tax_rate=0.001, transfer_fee_rate=0.00001, slippage_rate=0.0)` | 照抄 `BacktestWorker` L494-499 |
| `rebalance_mode` | `legacy` | GUI combo 第一项 / 引擎默认 |
| `fidelity_config` | `None` | GUI 默认未勾选 |
| `etf_t0` | `False` | 引擎默认（日线强制 False；本跑 minute 且未勾选） |
| `db_path` | `data/backup_import_20261101_040935/quantstudio.db` | **隔离库**（见 §四） |

## 三、⚠ 两处实施期发现（均首次通过本节留痕）

### 3.1 `run_backtest` **不接受** `commission` / `stamp_tax` / `slippage`

直调试验（03:35）报：`TypeError: run_backtest() got an unexpected keyword argument 'commission'`。

⇒ **GUI 的 `params` 不是 `run_backtest` 的 kwargs**：`commission`/`stamp_tax`/`slippage` 只是
`BacktestWorker` 用来构造 `TradeCost` 的**中间值**（L494-499），构造完以 `cost=` 传入引擎。
⇒ **含义**：验证「跨入口对等」必须复现 Worker 的**转换**，不能把 GUI params 直接喂引擎。
本文读数即按 Worker 转换后构造。

### 3.2 🔴 审核方误杀一个正在正常运行的进程（自认）

首次尝试用**真实 `BacktestWorker`** 跑（03:26 启动），超过 600s 未返回 ⇒ 我**判为「卡在等待采集」并 kill**。

**实际不是卡住**——被 kill 前的日志证明它在正常推进：
```
03:32:30 QS_BARS_CACHE_PROGRESS table=stock_daily loaded=2/2 elapsed=2.2s
03:33:26 买入 600635.SS
03:33:28 QS_REBALANCE_AUDIT date=2026-03-04 selected=1 tradable=1 sell_submitted=0 buy_submitted=1
```

⇒ **真因**：该窗口经 Worker 链路需 **6 分钟以上**（本次直调版 `wall_sec=300.4s`，Worker 另有
开销），而我把「超过单次工具超时」误读为「无限等待」。
⇒ **教训**：超时 ≠ 卡死。判定「卡住」前必须**读进程日志看是否仍在推进**，不能只凭时长。
（同族：`Select-Object -First N` 的假失败——把观测装置的边界读成被测对象的缺陷。）

## 四、⚠ 口径边界（必须与结论同框）

| 项 | 说明 |
| --- | --- |
| **本跑用的库** | **隔离库** `data/backup_import_20261101_040935`（与探针同库，锚可比） |
| **GUI 实际会用的库** | `db_path()` = `data/quantstudio.db`（生产库） |
| ⇒ 本跑证明了什么 | **参数链无丢失**（GUI 参数 → 引擎 → 结果 == 命令行探针结果） |
| ⇒ 本跑没证明什么 | **跨库数据等价**（两库数据若不同，生产库读数可能不同——那是数据问题，非本次改动引入） |

> 之所以用隔离库：①锚 `b4b538a0…` 产生于隔离库，同库才能对照；②**避免动生产库**（daemon 常驻持锁，铁律 19）。

## 五、可复现

```powershell
cd D:\miniQMT策略实盘\QuantStudio
python agent_workspace\first_board_high_open_1to2\_golden_direct2.py
```

读数落盘：`agent_workspace/first_board_high_open_1to2/_crossentry_readings.json`