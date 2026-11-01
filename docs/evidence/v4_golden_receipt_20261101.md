# V4 黄金逐位实测回执（闸开 vs SKIP=1 同窗对比）— 2026-11-01 20:35

## 判定：**PASS（7/7 逐位一致，SHA256 全同）**；锚三方对照亦 7/7 全同。

## 跑法（可复现）

- 策略：`output/generated_strategies/first_board_high_open_1to2/strategy.py`（SHA256 `CE735FF61440F635977626C20468D77EF68C2C2E14B22B099696D1558D64E73C`）
- 入口：`python quantstudio/backtest/run_ptrade_strategy.py <策略> 2026-01-05 2026-01-16 --profile minute-bar-v1`
- Python：`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`（3.11.9）
- 数据根：`QUANTSTUDIO_DATA_ROOT=D:\miniQMT策略实盘\QuantStudio\data\backup_import_20261101_040935`（副本只读；主库 daemon 未触碰）
- 公共 env：`PYTHONIOENCODING=utf-8`；差异 env 仅 `QS_MINUTE_COVERAGE_SKIP`（A=未设，B=1）
- 驱动脚本：`output/v4_golden_20261101/v4_golden_run.ps1`（顺序 A→B）
- 比对脚本：`output/v4_golden_20261101/v4_golden_compare.ps1`

## 双跑记录

| 跑 | env | 起止 | 耗时 | rc | 产物目录 |
|---|---|---|---|---|---|
| A 闸开 | 无 SKIP | 20:11:06~20:22:39 | 692.4s | 0 | `output/backtest_results/20261101_202233_strategy` |
| B SKIP | `QS_MINUTE_COVERAGE_SKIP=1` | 20:22:39~20:34:38 | 719.3s | 0 | `output/backtest_results/20261101_203432_strategy` |

A 闸读数（run_A_open.log）：`[minute-gate] ①整日缺=0 ②低覆盖=0 ③副本陈旧=0 耗时=3.638s → ✅ PASS`。
B 留痕三件（run_B_skip.log 两行 + `minute_coverage_gate_SKIPPED.json` 525B，verdict=SKIPPED，skip_reason=QS_MINUTE_COVERAGE_SKIP=1：整闸跳过）。

## 7 产物 SHA256（A vs B，完整 64 位逐字相等；锚=2026-10-30 验收前 16 位）

| 文件 | SHA256（A=B 同值） | 锚(前16) | 三方 |
|---|---|---|---|
| benchmark.csv | 44DC0265B63C668DEA7851BB36213AD2B0FB4FD5FEF5431EFAE212D41B085FC9 | 44DC0265B63C668D | 同 |
| config.csv | FF4E25136E7BDBC4283179FF41E409D5F80EB4CC586052BD594AA30774B7363 | FF4E25136E7B6DBC | 同 |
| daily_stats.csv | A39F1E9DA3063887198B34ED548455908D2C30895C612E25CA98A7ED3BA8EB24 | A39F1E9DA3063887 | 同 |
| ptrade_metrics.csv | 8D26021A70450DB9E123708A3CA195B09C9BCB8CA09093058469CD4F71242881 | 8D26021A70450DB9 | 同 |
| ptrade_metrics.json | 1E294532D1BF4119886E77E52FDCDEAF4DBD1F769110CD570FA9554DE5E5B261 | 1E294532D1BF4119 | 同 |
| round_trips.csv | 1D0F01033EB6C9036E9BE1B35B02553B08810B0DEFAD88B067815135CF7C0EAB | 1D0F01033EB6C903 | 同 |
| trades.csv | DDE240B02B49E76D660434B35F0E65BA3164D2EF3A1CD1403F5A86E5DDD9E828 | DDE240B02B49E76D | 同 |

SAME=7 DIFF=0。行数：trades=10、round_trips=4、daily_stats=10。

## 关键指标（A=B 逐位同；来源 ptrade_metrics.csv）

| 指标 | 值 |
|---|---|
| 总收益率 | 6.275768770000001 % |
| 最大回撤 | 5.641001455691985 %（锚同） |
| 夏普比率 | 7.877695491898896（锚同） |
| 胜率 | 25.0 %（盈利 1 / 亏损 3） |
| 年化收益率 | 357.9904059833789 % |
| 基准(沪深300) | 2.2015404087309953 % |
| 超额收益 | 5.976473555120032 % |
| 交易日数 | 10；最终净值 106,275.77 |

## 三方对照说明

- 锚文件：`docs/evidence/coverage-gate-acceptance-20261030.md`（⑦ 黄金逐位一致详证，7 产物前 16 位）。
- 锚跑库为已删除的 `agent_workspace/r1_live_copy_20261011`（2026-11-01 用户令删除）；本次用 `data/backup_import_20261101_040935`。**换库后 7 产物哈希仍与锚逐字相同** ⇒ 该窗口数据在两库间亦逐位一致。
- 锚耗时 A=688.6s / B=679.4s；本次 A=692.4s / B=719.3s（耗时非判据，B 略慢属负载波动）。
- 锚 B 留痕 528B vs 本次 525B：差异仅为 db_path 字符串不同（库路径不同），该文件本就不属 7 产物。

## 纪律与受阻项

- 未改 `quantstudio/`、`scripts/`、`config/` 任何文件；未动 daemon；未改数据。产物全部 untracked（output/ 在 .gitignore）。
- daemon 41832（python，2026-11-01 17:26:03 起）跑前、跑后均在位存活；回测走副本只读，无锁冲突，**无受阻项**。
- 副本时效注记：backup_import 系 10-31 16:06 主库快照（窗口 2026-01 在其中，无时效问题）。
