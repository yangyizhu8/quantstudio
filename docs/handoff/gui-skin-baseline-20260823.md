# GUI 皮肤改造 · Phase 0 基线记录（20260823）

## 基本信息

| 项 | 值 |
|---|---|
| 时间 | 2026-08-23 20:47 |
| git HEAD | `996bbac`（skill: R0 新增策略工作流图审核步骤） |
| D3 前置 commit | `84702d9` fix(gui): import MessageBox（main_window.py 一行 import，已单独提交） |
| 工作区快照（零副作用） | `git stash create -u` → **`32f04b32f3e4377991db53474d2312852b88c2a2`**（误操作后 `git reset --hard 32f04b3` 找回） |
| 状态清单 | `docs/handoff/gui-skin-status-20260823-204717.txt` |
| diff 统计 | `docs/handoff/gui-skin-diffstat-20260823-204717.txt` |

## 文件级备份（他人未提交改动共存防护，审核补充③）

- `docs/handoff/backup-config_editor_tab-20260823-204717.py`（34396 B）
- `docs/handoff/backup-task_tab-20260823-204717.py`（49660 B）

他人改动段与皮肤改动段位置核实（hunk 不相邻，无需启用延后预案）：
- task_tab.py 他人改动：`_reset_watermark`（L873-920，3A 写锁）；皮肤改动段：`_setup_ui` 顶部（L71-145）
- config_editor_tab.py 他人改动：L71-132（etf_dividend 字典项）；皮肤改动段：L37-51（`_DARK_CONFIG_STYLE`）

## GUI 测试基线（皮肤实施前，含 D3 修复）

运行环境：`QT_QPA_PLATFORM=offscreen`，pytest 8.x。

### tests/test_gui_dark_panels.py — 12 collected：7 PASS / 4 FAIL / 1 HANG

失败 4 条（**基线既有，测试夹具与生产代码漂移**，与皮肤、与 D3 均无关）：
1. `test_source_tab_uses_transparent_fluent_scroll_area` — `SourceTab._setup_ui` 调 `self.mw._current_label()`，测试的 `DummyMainWindow` 未实现该方法 → AttributeError
2. `test_source_tab_dark_scroll_content_uses_dark_background_and_white_text` — 同上（未到达颜色断言即失败）
3. `test_config_editor_pages_use_scoped_dark_scroll_panels[alignment_page-...]` — `DummyMainWindow` 缺 `_reset_in_progress`/`app_root`
4. `test_save_tasks_preserves_source_priority_and_passthrough_fields` — 同上

挂起 1 条（**基线既有**）：
- `test_backtest_finished_does_not_repeat_completion_log` — 根因：测试用 `StubBacktestResultWindow` 无 `export_report_images` 属性 → `_on_finished` 内 `QTimer.singleShot(0, ...)` 取属性抛 AttributeError → except 分支调用**真实模态** `QMessageBox.warning`（该测试未 monkeypatch warning）→ offscreen 下事件循环永久阻塞。后续整文件运行需 `--deselect tests/test_gui_dark_panels.py::test_backtest_finished_does_not_repeat_completion_log`。

### tests/test_gui_rebalance_mode.py — 3 FAIL（基线既有漂移）

- `test_worker_passes_rebalance_mode_into_engine_config` / `test_worker_defaults_to_legacy_without_param` / `test_worker_default_engine_config_matches_pre_fix_fields` — `KeyError: 'engine_kwargs'`（workers/引擎配置演化后测试断言未跟上）

### tests/test_gui_task_audit_separation.py — 全部 PASS
### tests/test_backtest_result_window_savefig.py — 全部 PASS

**验收口径**（据实修正原"全绿"表述）：皮肤改造后 ① 上述 PASS 集合保持 PASS；② FAIL/HANG 集合不扩大；③ 计划内更新的 2 处颜色断言后，相关测试按 ①② 口径判定。

## before 截图（output/skin_preview/before/，12 张）

window-full / tab00..tab08（9 Tab）/ window-full-with-logs / result-window。

截图环境注意（offscreen 平台特性，非缺陷）：
- `QT_QPA_FONTDIR=C:\Windows\Fonts` 必须设置，否则 CJK 文字渲染为 □（豆腐块）；
- before 阶段 `setMicaEffectEnabled(True)` 在 offscreen 下 Mica 不生效且 FluentWindow 不绘制底色 → 整窗截图呈平台浅灰底（Tab 内 QSS 深色区正常）。after 阶段关 Mica + `setCustomBackgroundColor` 后 offscreen 也能正确绘制深底——该差异本身即皮肤生效证据。
- 生成命令：`python scripts/gui_skin_preview.py --out output/skin_preview/before`

## 预存问题清单（非皮肤范围，仅登记）

| # | 问题 | 根因 | 处置 |
|---|---|---|---|
| P1 | main_window.py MessageBox NameError（切数据源/错误弹窗崩溃） | import 缺失 | **已修**（D3，commit 84702d9） |
| P2 | test_gui_dark_panels 4 条失败 | DummyMainWindow 夹具漂移（缺 `_current_label`/`_reset_in_progress`/`app_root`） | 登记待用户裁决（测试修复属行为域，独立轨道） |
| P3 | test_gui_dark_panels 1 条挂起 | Stub 缺 `export_report_images` → 真实模态弹窗阻塞 | 同上 |
| P4 | test_gui_rebalance_mode 3 条失败 | `engine_kwargs` 断言漂移 | 同上 |
