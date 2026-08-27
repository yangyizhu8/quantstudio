# 回测计时功能 — 手动冒烟证据（运行中 / 定格两帧）

- 日期：2026-08-27 10:58:02 ~ 10:59:14
- 方案：`docs/handoff/baseline_backtest_timer_20260827-105231.txt`（含回退点 `8c248164255ad0ea4faf548703527680b610a2ff`）
- 脚本：`scripts/_smoke_backtest_timer.py`（真实 GUI：BacktestTab + 真实引擎 + 真实 DuckDB；结果窗口打桩，冒烟聚焦计时生命周期）

## 场景

真实策略「二八轮动策略.py」，2026-01-01 ~ 2026-07-13（125 交易日，与用户截图同一区间），初始资金 10 万，默认费率。总耗时 69.52s（wall）。

## 验收断言（脚本自动采集，record.json）

| 断言 | 结果 |
|---|---|
| 运行中计时器激活且显示「已用时」逐秒刷新 | ✅ 帧1 见下（00:00:01 跳动） |
| 完成后计时停止（tick_active_after_finish=false） | ✅ |
| 完成后定格「⏱ 用时 HH:MM:SS」 | ✅ 00:01:08 |
| 完成状态文本「✅ 回测完成: …」 | ✅ |
| worker_finished=true（真实引擎跑完全程） | ✅ |
| frame1_label 记录字段 | ⚠️ 脚本在结束后覆写该字段（显示 00:01:08）；PNG 本体为运行中帧，以下以 modlens 转写为准 |

## 帧 1 — 运行中（10:58:06 捕获）

![frame1-running.png](frame1-running.png)

界面转写（modlens OCR，片段）：`▶ 启动回测 □ 停止 ⏱ 已用时 00:00:01 开始回测...`

→ 停止按钮右侧显示**跳动中的已用时**（每秒刷新，QTimer 1s + QElapsedTimer 单调钟）。

## 帧 2 — 完成后定格（10:59:14 捕获）

![frame2-frozen.png](frame2-frozen.png)

界面转写（modlens OCR，片段）：`▶ 启动回测 □ 停止 ⏱ 用时 00:01:08 ✅ 回测完成: output/backtest_results\20260827_105913_二八轮动策略`

→ 回测完成自动停表，**最终用时定格保留**（00:01:08），状态行显示完成路径。

## 自动测试（同轮验收）

`python -m pytest tests/test_gui_backtest_timer.py tests/test_gui_dark_panels.py tests/test_gui_rebalance_mode.py -q` → **34 passed**（其中计时功能新增 15 项，含 `_fmt_elapsed` 8 组边界参数）。

## 备注

1. 冒烟脚本进程 exit code 1 为控制台编码伪影：`print("⏱")` 在 gbk stdout 触发 UnicodeEncodeError（record.json 先落盘故产物完整），已用 `python -c "print('⏱')"` 复现证实；与功能无关。
2. 副作用：回测结果落盘 `output/backtest_results/20260827_105913_二八轮动策略`（真实引擎正常产物，可清理）。
3. 实施范围：仅 `quantstudio/gui/tabs/backtest_tab.py`（计时 hunk）+ 新增 `tests/test_gui_backtest_timer.py`；同文件保真（fidelity）未提交改动未触碰（hunk 剥离纪律）。