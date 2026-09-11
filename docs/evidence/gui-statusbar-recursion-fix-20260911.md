# GUI 顶部状态栏自递归 P0 修复 验收证据 · 2026-09-11

方案：《GUI 顶部状态栏自递归致命缺陷修复》（总调度八项独立核验通过）。本件为该方案实施后的验收证据。

## 1. 缺陷、根因与逃逸链

| 项 | 内容 |
|---|---|
| 现象 | 采集任务页**任何**状态写入路径（常驻启停 / 单任务 / 批跑 / 进度回传）→ 无限递归 → `RecursionError` → GUI 退出 |
| 根因 | `quantstudio/gui/tabs/task_tab.py:312` 写入口自调用：`self._set_status_text(msg)`（应为 `self.status_label.setText(msg)`） |
| 引入 | `67ca8824`（2026-09-06 `feat(gui): 采集控制台「全部执行」拆分 — 增量改名 + 全量新增按钮`）——`git blame` 实测该函数 306-313 行整段出自此提交 |
| 逃逸 | `tests/test_gui_task_audit_separation.py` 的 `DummyTab`(L145) / `DummyRunAllTab`(L267) **复制了一份** `_set_status_text` 实现（替身），真实类方法从未被执行；替身与真身分叉后真身坏掉而测试全绿 |
| 同族唯一性 | AST 自递归扫描：`task_tab.py` 修复前命中 **1** 处、`quantstudio/gui/` 全树命中 **1** 处（即本缺陷），无第二处同族缺陷 |

根因取证（docstring 契约与实现不符）：

```python
def _set_status_text(self, msg: str):
    """顶部状态栏统一写入口：setText + setToolTip。   # 契约：setText + setToolTip
    ...
    """
    self._set_status_text(msg)          # 缺陷：自调用（RecursionError）
    self.status_label.setToolTip(msg)
```

## 2. 改动清单（精确三件，无夹带）

| 文件 | 改动 | 规模 |
|---|---|---|
| `quantstudio/gui/tabs/task_tab.py` | 312 行 `self._set_status_text(msg)` → `self.status_label.setText(msg)` | **1 行**（+1 / -1） |
| `tests/test_gui_task_status_real.py` | 新增：真实方法回归测试（堵替身逃逸缺口） | 新增 122 行 |
| `docs/evidence/gui-statusbar-recursion-fix-20260911.md` | 本证据文档 | 新增 |

未触碰：其他 tab / 布局 / workers / daemon / pipeline / 策略层 / §8 已知限制。

修复 diff（`git diff` 原文）：

```diff
@@ -309,7 +309,7 @@ class TaskTab(QWidget):
         status_label 设了 setMinimumWidth(80)，长状态文本会被裁剪；
         tooltip 保留全文供悬停查看，信息不丢失。
         """
-        self._set_status_text(msg)
+        self.status_label.setText(msg)
         self.status_label.setToolTip(msg)
```

## 3. 写前快照（AGENTS.md「写前快照」纪律）

```
git stash create -u -m "baseline-gui-statusbar-fix-20260911"
  -> 0dd5d1de127440cfacdab95f26fae1234f07f458
git stash store -m "baseline-gui-statusbar-fix-20260911" 0dd5d1de...
  -> stash@{0} baseline-gui-statusbar-fix-20260911
git status --porcelain quantstudio/gui/tabs/task_tab.py   -> 空（无他人未提交改动叠加）
HEAD 基线: 55e788bd83447887ed018bbf1fc16b7e4ec907f9
```

回退手段：`git reset --hard 0dd5d1de127440cfacdab95f26fae1234f07f458`

## 4. V1 新用例红→绿（**同一测试文件，红态取证后未再改动**）

### 4.1 红态（未修复代码，修复前原样输出）

```
$ python -m pytest tests/test_gui_task_status_real.py -q
_______________ test_status_text_writes_label_text_and_tooltip ________________
    def test_status_text_writes_label_text_and_tooltip(status_tab):
>       status_tab._set_status_text(LIVE_MSG)
tests\test_gui_task_status_real.py:93:
quantstudio\gui\tabs\task_tab.py:312: in _set_status_text
    self._set_status_text(msg)
quantstudio\gui\tabs\task_tab.py:312: in _set_status_text
    self._set_status_text(msg)
E   RecursionError: maximum recursion depth exceeded
!!! Recursion detected (same locals & position)
_____________________ test_status_text_never_calls_itself _____________________
>       assert "self._set_status_text(" not in body, (...)
E       AssertionError: _set_status_text 函数体内出现自调用，会触发无限递归（P0 回归）
E         'self._set_status_text(' is contained here:
E                   self._set_status_text(msg)
tests\test_gui_task_status_real.py:104: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_gui_task_status_real.py::test_status_text_writes_label_text_and_tooltip
FAILED tests/test_gui_task_status_real.py::test_status_text_never_calls_itself
2 failed in 2.12s
```

> 说明：Windows 控制台按 gbk 解码 pytest 的 UTF-8 输出，中文提示显示为乱码属**控制台显示层**现象，非断言失败原因；失败判据为 `RecursionError` 与源码自调用断言。

### 4.2 绿态（修复后）

```
$ python -m pytest tests/test_gui_task_status_real.py -q
2 passed in 1.41s
```

### 4.3 测试设计（堵替身逃逸）

- 用**真实 `TaskTab`**（offscreen 构造，实测 `tasks=74`）；仅宿主注入 `_StubMainWindow`/`_StubDbHelper`（补 `profile_options`/`current_profile`/`config_dir`/`db_helper.get_watermarks`），**不替身被测方法**。
- 兜底 harness 亦经 `types.MethodType(TaskTab._set_status_text, obj)` 绑定**真实类方法**，`status_label` 为真实 `QLabel`——**零实现复制**。
- 用例 A：`status_label.text() == msg` 且 `toolTip() == msg`（生产实文案「🟢 常驻采集进程已启动」，unicode 转义书写）。
- 用例 B（防回归）：`inspect.getsource` 断言函数体（去 signature 行）不含 `self._set_status_text(`；AST 加固捕获任意接收者上的 `._set_status_text(...)` 自调用形态；并以真实调用证明常量时间返回。

## 5. V2 GUI 回归套件

| 时点 | 命令 | 结果 |
|---|---|---|
| 改动前（基线） | `python -m pytest tests -k gui -q` | **71 passed**, 2874 deselected in 18.91s |
| 修复后 | `python -m pytest tests -k gui -q` | **73 passed**, 2874 deselected in 21.50s |

基线 71 passed 与方案陈述一致；+2 即新增测试文件两用例，**零回归**。

## 6. V3 全量套件

| 时点 | 命令 | 结果 |
|---|---|---|
| 改动前（基线） | `python -m pytest tests -q` | **61 failed, 2873 passed**, 3 skipped, 8 xfailed, 48 warnings in 1205.73s (0:20:05) |
| 修复后 | `python -m pytest tests -q -rf` | **61 failed, 2875 passed**, 3 skipped, 8 xfailed, 48 warnings in 1194.84s (0:19:54) |

- 通过数 **+2**（= 新增测试文件两用例），失败数 **不变**。
- 失败集合逐条比对：**NEW_FAILURES = 0**，NO_LONGER_FAILING = 0 → 前后失败集合**完全相同**。

**取证方法（双源一致，防缓存污染误判）**：

1. 基线失败集合：基线运行结束时的 pytest `lastfailed` 缓存 ∩ 当前可收集 nodeid = **61**，与基线摘要 `61 failed` 逐字吻合。
   缓存另含 31 条陈旧条目（其他会话已删除/改名的测试，不在当前收集集内）——按「本次全量运行中凡通过者即被弹出」语义剔除，故交集即真实失败集合。
2. 修复后失败集合：`-rf` 全量日志（`Tee-Object` 输出为 UTF-16）解析 = **61** 条；`lastfailed` ∩ 收集集 = **61** 条；**两源完全一致**（`TWO_SOURCES_AGREE = True`）。
3. 集合差集：`postfix - prefix = 空`、`prefix - postfix = 空`。

**61 条失败为改动前既有，与本次修复无关**：落点在 `pipeline/` `backtest/` `strategy_compiler` 契约/策略生成等模块（共享工作区内其他会话在途改动与环境依赖所致），本次改动文件仅 `task_tab.py`（1 行）+ 新增测试文件，**零触碰**上述模块。

```
PRE_FIX_FAILED= 61
POST_FIX_FAILED= 61
NEW_FAILURES= 0
NO_LONGER_FAILING= 0
VERDICT: V3-PASS (no new failures)
```

> 说明：V3 判据按方案为「**无新增失败**」（非「全绿」）——仓库基线存在 61 条既有失败，属改动前状态，本次不扩面处理。

## 7. V4 13 个状态写入路径逐条触发（真实控件）

harness（非提交件）：真实 `TaskTab`(offscreen) + 真实 `QLabel`；仅替身外部依赖（`QMessageBox`、daemon 进程函数、`LockedTaskWorker`/`LockedRunAllWorker`、`StateToolTip`）。判据：`status_label.text()` 与 `toolTip()` **精确等于**该路径产出文案。

| # | 行 | 场景 | 期望文案（实测 = 期望） | 结果 |
|---|---|---|---|---|
| 1 | 436 | 常驻启动握手成功 | `🟢 常驻采集进程已启动` | OK |
| 2 | 481 | 优雅停止完成 | `🔴 常驻采集进程已停止` | OK |
| 3 | 510 | 强制终止成功 | `⚠ 常驻进程已被强制终止` | OK |
| 4 | 531 | 轮询发现异常退出 | `⚠ 常驻进程异常退出` | OK |
| 5 | 597 | 单任务启动（增量） | `执行中(增量): probe_task...` | OK |
| 6 | 618 | 单任务启动失败 | `❌ 启动失败: probe_task2` | OK |
| 7 | 750 | 批跑启动失败 | `❌ 启动失败: boom-runall` | OK |
| 8 | 814 | 批跑失败（error） | `❌ 全部执行（增量）失败: boom` | OK |
| 9 | 830 | 批跑 QFQ 水位告警 | `⚠ 全部执行（增量）完成（1/1 拉取成功；1 个 QFQ 任务水位未提交: t1(finalized_held)）` | OK |
| 10 | 834 | 批跑全成功 | `✅ 全部执行（增量）完成（1/1）` | OK |
| 11 | 840 | 批跑部分失败 | `⚠ 全部执行（增量）完成（0/1 成功，1 失败）` | OK |
| 12 | 848 | 采集进度回传 | `progress-msg` | OK |
| 13 | 889 | 单任务完成回调 | `✅ probe_task3` | OK |

```
TRIGGERS: 13 FAILURES: 0 []
VERDICT: V4-PASS
```

**零 `RecursionError`。**

### 7.1 对照组（证明 13 路径此前全数受害）

在同一 harness 内以内存方式装回修复前写入口（仅实验用、不落盘）：

```
[CONTROL MODE] pre-fix recursive writer installed in memory
L436..L889 -> RECURSION-ERROR   (13/13)
TRIGGERS: 13 FAILURES: 13
VERDICT: V4-FAIL
```

结论：修复前 **13/13 路径全部触发 RecursionError**，修复后 **13/13 全部 OK**——「13 调用点全通」双向取证。

### 7.2 harness 期望修正记录（诚实登记）

V4 首次运行 `L830` 报 `TEXT-MISMATCH`：实测 `t1(finalized_held)`、harness 期望 `t1(watermark_held)`。
归因：**harness 期望值写错**——`_qfq_warning_from_result` 先判 `status != "finalized"` 即返回 reason=`finalized_held`，故 held 大于 0 的分支不可达（产品行为正确，非缺陷）。修正 harness 期望并复跑 → 13/13 PASS。**产品代码零改动。**

## 8. V5 静态与同族 AST 扫描

| 检查 | 结果 |
|---|---|
| `python -m py_compile quantstudio/gui/tabs/task_tab.py` | exit 0（OK） |
| `import quantstudio.gui.tabs.task_tab`（QT_QPA_PLATFORM=offscreen） | `IMPORT_OK TaskTab`，exit 0 |
| AST 自递归扫描 `quantstudio/gui/tabs/task_tab.py` | 修复前 **1** → 修复后 **0** |
| AST 自递归扫描 `quantstudio/gui/` 全树 | 修复前 **1** → 修复后 **0** |
| AST 自递归扫描 `quantstudio/` 全树 | 修复前 5 → 修复后 4（余 4 处见下） |

剩余 4 处命中经逐一核验为**合法收敛递归**，与本次缺陷非同族、本次零触碰：

- `pipeline/aligner.py::to_ms_timestamp`（188/198 行）：float→int、数字串→int 的类型收敛派发（int 分支不再回调，源码注释已声明）；
- `pipeline/qfq_snapshot_evidence.py::_json_value`（34/36 行）：list/dict 容器递归下降，含标量 base case。

扫描器判定规则（防误报）：仅当 `Call.func` 为 `Attribute` 且 `attr == 所在函数名` 且 `value` 为 `Name(self)`，或裸名自调用成立；据此排除 `super().__init__()`、`logger.info(...)`、`self.x.close()` 等接收者非 self 的形态。

## 9. 同步义务检查（README + 引用文档）

| 文档 | 检索词 | 命中 | 结论 |
|---|---|---|---|
| `README.md` | `_set_status_text` / `RecursionError` / 状态栏 | 0 / 0 / 0 | 无涉本缺陷 |
| `README.md` | 自递归 / 递归 | 1 | L139 = `_QS_GF_LIST_CHUNK`（get_fundamentals 500 码分块），**与本缺陷无关** |
| `docs/strategy_toolbox.md` | `_set_status_text` / `RecursionError` / 状态栏 | 0 / 0 / 0 | 无涉本缺陷 |
| `docs/strategy_toolbox.md` | 递归 | 1 | L131 = 同上财务分块，无关 |
| `docs/prompt_engineering.md` | `_set_status_text` / `RecursionError` / 状态栏 | 0 / 0 / 0 | 无涉本缺陷 |
| `docs/prompt_engineering.md` | 递归 | 1 | L95 = 同上财务分块，无关 |

**结论：本次为 GUI 层缺陷修复，不属策略工具箱/提示词工程表述范围；README 与引用文档无涉本缺陷，无需同步更新。**

## 10. 边界与回退

- **未做**：不重构、不改名、不动布局、不动其他 tab、不顺带处理 §8 已知限制（GUI 点击自动化缺口单独立项）。
- **未做**：未修改既有 `tests/test_gui_task_audit_separation.py`（其替身保留——新测试单独承担「真实方法」覆盖，避免扩大改动面）。
- **回退**：`git reset --hard 0dd5d1de127440cfacdab95f26fae1234f07f458`。
- **V6 实机目测**：由用户执行（`python main_gui.py` 实点：常驻开关启停 → 行内「全量拉取」「增量拉取」→「▶ 全部执行（增量）」，GUI 不退出且状态栏文案正确、🟢 可见）。本会话不做桌面点击自动化。
