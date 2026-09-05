# GUI「全部执行」拆分 验收证据 · 2026-09-06

## 改动内容（方案：用户已审计批准）

**范围：纯 GUI 层。pipeline / workers / daemon / collector 零改动。**

| 文件 | 改动 | 规模 |
|---|---|---|
| `quantstudio/gui/tabs/task_tab.py` | 工具栏双批跑钮（增量改名+全量新增）、`_run_all(mode)` 参数化、全量确认框（默认 No）、`_reset_run_all_buttons` / `_set_status_text` 辅助、`_run_all_active_mode` 重入守卫、完成回调模式标签 | +111 / -36（147 行变更块） |
| `tests/test_gui_task_audit_separation.py` | `DummyRunAllTab`/`DummyTab` 补桩 + 2 条新用例（双钮复原 / 全量模式文案） | +48 |

净变化：2 files changed, 159 insertions(+), 36 deletions(-)。

## 验收证据

### 1. 单元测试
- `python -m pytest tests/test_gui_task_audit_separation.py -q` → **13 passed**（11 既有含 run_all 完成回调/QFQ 门控路径 + 2 新增：`test_run_all_done_restores_both_buttons`、`test_run_all_done_full_mode_labels_status`）
- `python -m pytest tests -k gui -q` → **71 passed, 2770 deselected**（GUI 关键词回归全绿）

### 2. 静态检查
- `python -m py_compile quantstudio/gui/tabs/task_tab.py` → OK
- `python -c "import quantstudio.gui.tabs.task_tab"` → IMPORT_OK
- 旧文案残留扫描：`"▶ 全部执行"`（裸文案）0 处；新符号（双钮/守卫/两辅助）31 处落位

### 3. offscreen 几何探针（确定性布局取证，agent_workspace/probe_toolbar_geometry.py）
真实 TaskTab + FakeDB（不触碰真实 DuckDB），QT_QPA_PLATFORM=offscreen：

**宽窗 1320px**：daemon x=11 ｜ 刷新 x=659 w=82 ｜ **增量 x=749 w=166** ｜ **全量 x=923 w=180** ｜ 重置水位 x=1111 ｜ status_label x=1229（压缩至最小宽 80）——全部 **y=103 同一行**，x 严格递增、零重叠。
**窄窗 900px**：四钮仍 y=103 同行、宽度不变（按钮完整不被挤压），status_label 承担压缩兜底（80px）。
断言全过：同Row（y 差≤1px）/ x 有序 / 无重叠 / 按钮宽>60 / 文案精确匹配 / 双钮初始 enabled / minimumWidth==80 → **PROBE_ALL_OK**。

### 4. 真机启动冒烟
- `python main_gui.py` 后台启动 → 主窗口「QuantStudio 数据管线控制台」出现（PID 49944），TaskTab 构建无异常；验证完毕已正常关闭（exit 1 为强杀终止，非故障）。
- **未自动执行项**：增量/全量批跑的实际点击触发。原因：a) 会对共享正式库 data/quantstudio.db 触发真实重拉（全量=全表重拉，重副作用）；b) 本会话视觉通道不可用（vision 桥无法摄入像素），无桌面点击自动化通道。对应逻辑已由单测覆盖：`_on_run_all_done` 以非绑定方式直接单测了增量/全量两条文案路径与双钮复原；确认框路径为纯 QMessageBox 交互（代码审查 + 方案裁定已确认）。**请用户实点两钮做最终目测验收**（全量钮点击后应弹确认框，选 No 无副作用）。

### 5. 已知限制（设计内）
- 视觉通道（vision_glance/modlens）本会话不可用：截图存在（.dsh-vision-toolkit/tmp/gui-smoke-toolbar-20260906-v4.png，1320x880）但无法程序化读取像素；布局正确性以几何探针为准（比目测更严格）。
- 单任务运行中点批跑钮：维持既有行为（Worker 抢锁 5s 超时报错），本次按方案不扩大互禁范围。

## 回退手段（AGENTS.md 写前快照纪律）
- 基线 stash：`baseline-20260906-002001` = **de4b2248b105f50e10abb2648fba4cf29a633b5b**（`git stash store` 已持久化）
- 文件级备份：docs/handoff/backup-task_tab-20260906-002001.py、backup-test_gui_task_audit_separation-20260906-002001.py
- 动码前核查：两目标文件当时均干净（无他人未提交改动）；工作区其他脏文件属其他会话，本次零触碰

## 提交闸门
实施+验收已完成并汇报。**未经用户明确确认，不做任何 git 提交/双仓库推送。**
