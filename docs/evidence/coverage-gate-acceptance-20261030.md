# 覆盖闸 ④ 验收报告（终版，2026-10-30 21:05）—— **7 项全过**

## 结论

> **④ 验收 PASS（7/7）**。六步流水线 ③ 实施已完成并通过验收；按铁律 01 进入 ⑤ 用户确认，**未获批准前不 commit / 不 push**。

## 逐条验收

| # | 项 | 结果 | 实测证据 |
| --- | --- | --- | --- |
| 1 | 实现三型三开关 | PASS | 三开关 SKIP / STRICT / MIN_RATIO（默认 0.5）；日键 ((time+28800000)//86400000) |
| 2 | 新测试 | PASS | pytest tests/test_coverage_gate.py = 26 passed in 19.69s |
| 3 | 既有回归 | PASS | 6 个回测入口相关文件 = 65 passed in 2.74s（exit 0） |
| 4 | 日键一致性 | PASS | 引擎交易日 = 窗口内 stock_daily 的 CST distinct 日（query_trade_days_range，duckdb_data_access.py:2538）；引擎日界 = time_axis.day_start_ms（time_axis.py:41）；闸 SQL 日键与其同族恒等 ⇒ 同源 |
| 5 | 复用核 audit_watermark | PASS（结论：不复用） | audit_watermark_coverage.py 仅 DESCRIBE / COUNT(*) / MAX(time_col) / get_last_date，无逐日 distinct code ⇒ 语义无重叠；禁两套判据现状满足（闸唯一实现，CLI 与回测入口均 import 同一函数） |
| 6 | 真实副本实跑耗时 | PASS | 缺口库 BLOCK（9/4/13，exit 2，8.05s）；补齐库 PASS（0/0/0，exit 0，4.16s）⇒ 实测 4~8s，**60s 拟值作废** |
| 7 | **黄金逐位一致** | **PASS** | 短窗 2026-01-05~2026-01-16，A（闸开）vs B（QS_MINUTE_COVERAGE_SKIP=1）：**7/7 结果文件 SHA256 完全相同，DIFF=0** |

## ⑦ 黄金逐位一致详证

| 文件 | SHA256 前 16 位 | A | B |
| --- | --- | --- | --- |
| benchmark.csv | 44DC0265B63C668D | 同 | 同 |
| config.csv | FF4E25136E7B6DBC | 同 | 同 |
| daily_stats.csv | A39F1E9DA3063887 | 同 | 同 |
| ptrade_metrics.csv | 8D26021A70450DB9 | 同 | 同 |
| ptrade_metrics.json | 1E294532D1BF4119 | 同 | 同 |
| round_trips.csv | 1D0F01033EB6C903 | 同 | 同 |
| trades.csv | DDE240B02B49E76D | 同 | 同 |

- 汇总 SAME=7 DIFF=0；关键指标逐位相同（max_drawdown_pct 5.641001455691985、sharpe_ratio 7.877695491898896）。
- A 次耗时 688.6s，B 次耗时 679.4s（短窗 10 交易日，分钟 profile）。
- B 次 SKIP 留痕已生成：output/backtest_results/20261030_205411_strategy/minute_coverage_gate_SKIPPED.json（528B）⇒ 跳过必留痕要求满足。
- 结论含义：闸在数据完整的库上 PASS ⇒ **不改变回测行为**（前置只读检查，零副作用）；A/B 同窗逐位一致即黄金等价实证。

## 呈批清单（⑤ 待批准）

待提交的精确 4 路径（禁带入策略目录他线差异）：
1. quantstudio/backtest/libs/coverage_gate.py（新，23,797 B）
2. scripts/check_minute_coverage.py（新，4,016 B）
3. quantstudio/backtest/run_ptrade_strategy.py（改，diff 86 行；前置检查链新增 _precheck_minute_coverage）
4. tests/test_coverage_gate.py（新，21,539 B）

另建议同笔附带本验收报告（agent_workspace 内为工作件，是否入仓请示）。

## 纪律状态

- 写前快照 30bb2787e56dabcd5279b77630483bfba2a2a2bf（stash）仍在，禁 pop。
- 策略源码零改动：两处策略目录差异为他线在途（fall_reversal_quantstudio.py mtime 09-12；3 个 candidate 策略被删），本轮未触碰。
- 副本口径确认（用户 2026-10-30 裁定）：终值重跑系 r1_live_copy（补齐库），无需回溯更正；r1_copy_b2 保持未补齐态作对照库留档。