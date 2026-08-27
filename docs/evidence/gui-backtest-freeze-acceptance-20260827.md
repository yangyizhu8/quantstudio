# GUI 回测定格方案 — 实施与验收证据（2026-08-27）

## 方案要点（用户批准 + 一处小修）
- 小修：`_on_stop()` 也执行定格（stop tick + setText）——"最终用时以按下时刻为准"真正成立；
- `_on_error` 定格幂等覆盖（同 tick 后者为准）。

## 确认①②

### ① 取消路径路由到 _on_error（实证）
`CancelBtn → _on_stop → self._worker.cancel()`（设 `_cancelled=True`）→ BacktestWorker
「逐日进度回调」`_on_engine_progress` 检测 `self._cancelled` → `raise RuntimeError("用户取消回测")`
→ `run()` 的 `except` 捕获 → `finished_err.emit` → `tab._on_error(err)`。
代码位：workers.py L17-18(cancel)/L460-462(raise)/L456-458(emit)；backtest_tab.py L235-236(connect)。
→ **取消路径确实到 _on_error，无另挂槽需要；_on_error 定格即天然幂等覆盖。**

### ② 定格冒烟（运行中/定格后/错误覆盖三帧）
```
[frame1/running]  回测进度 5/22 2026-07-06 运行中...
[frame2/stopped] ⏹ 停止于 10:43:42      ← stop tick 定格（按下时刻）
[frame3/error]   ❌ 回测失败于 10:43:42  ← _on_error 定格时刻幂等覆盖（同 tick）
```
- 产物：`output/gui_freeze_smoke/smoke_frames.txt`（文本快照）+ 3 PNG 占位（offscreen 完整
  grab 在自动化环境超时——完整 UI 截图需实机手测，入验收清单）。

## 改动
`quantstudio/gui/tabs/backtest_tab.py`：
- `_on_stop`：`cancel()` + 定格 `⏹ 停止于 {HH:MM:SS}`（`_freeze_mark` 记录按下时刻）——替代旧"正在停止..."；
- `_on_error`：定格 `❌ 回测失败于 {_freeze_mark 或当前时刻}` 幂等覆盖；
- 新增 `_now_text()` helper；
- **hunk 剥离**：该文件他线 4 hunk（L11/L94/L161-179/L180-225 = 皮肤/保真未提交改动）位置在我改动区（L248+）之前，Zero 冲突；commit 时选择性暂存我的 L248-300 区。

## 验收
- GUI 相关测试三件套 **33/33 全绿**（dark_panels / rebalance_mode / task_audit_separation / backtest_data_source_priority / backtest_result_window_savefig）；
- 冒烟 SMOKE-PASS（定格文本/时间/覆盖语义断言）；
- 存量失败归因：`test_ptrade_fidelity_config` 4 失败 = **P-D13（D2）默认值 passthrough→basic 的存量测试契约滞后**（断言 `'basic'=='passthrough'`）——非本改动引入，登记后续同步测试（不属于 GUI 定格范围）。
- 回退点：`cb0ea1b1e6ead8567ff4b7d80118d8935176c843`。

## 补充（2026-08-27 二轮：取消分支结构化改造后）

- **结构判定（禁字符串嗅探）**：workers.py 新增 `BacktestCancelled(RuntimeError)` 专用异常；
  raise 处（_on_engine_progress）+ emit 分支（isinstance 传递异常对象 vs traceback 文本）两行改动（甲方案）；
  `finished_err` 信号 `str → object`（全部消费者仅 str()/存储，兼容）。
- **backtest_tab._on_error**：`isinstance(err, BacktestCancelled)` 判定 →
  取消 = `⏹ 已取消于 {定格用时}` + info 无弹窗；真错误 = `❌ 回测失败` + critical 保持现状。
- **冒烟 v3**：f2 停止 `⏱ 用时 00:00:12` / f3 取消 `⏹ 已取消于 ⏱ 用时 00:00:12` **critical=0** /
  f4 真错误 `❌ 回测失败` **critical=1** —— SMOKE-PASS。
- **回归**：GUI 相关 73/73 通过（workers 信号 object 化零破坏）；4 失败仍为
  test_ptrade_fidelity_config P-D13 存量契约滞后（非本改动，登记）。
- 注：本文件定格部分与他线并行实现的计时定格（_clock/_tick/_freeze/_fmt_elapsed）
  融合——_on_error 取消分支叠加于既有 _freeze 幂等语义之上。