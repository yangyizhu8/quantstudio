# v3.1 停止语义 V6 实测缺陷修复 · 证据 · 2026-09-12

派单：《v3.1 停止语义 V6 实测缺陷——A4 修复重拉循环缺取消检查点》。
上一版证据：`docs/evidence/gui-stop-semantics-v31-20260912.md`（主循环三级语义，已过复验）。

## 1. 缺陷（V6 实测）

`_check_cloud_updates_and_repull` 的 A4 修复段（逐窗口局部重拉）**没有任何取消检查点**：
用户点停止后，A4 段仍会逐窗口重拉到底，且停止提示只承诺主循环粒度——用户侧表现为「点了停止没反应」。

## 2. 修复内容（对应派单 ①②③）

| 项 | 落点 | 实现 |
|---|---|---|
| ① A4 窗口循环接线取消检查 | `daemon.py::_check_cloud_updates_and_repull` + 新 `_a4_boundary` | 每窗口完成后调用**与主循环同一谓词**；命中 → `raise TaskCancelled` → `execute_task` 收口（不再进入主增量） |
| ② A4 段进度记账 | `task_resume.py::advance_a4 / a4_completed` | 每窗口完成写入游标 `a4.completed_windows`；再次运行**跳过已修复窗口** |
| ③ UX 文案含 A4 段状态 | `task_tab.py::_request_stop` + `daemon.py::_emit_task_progress` + `workers.py` | 停止提示改为「停止已请求：**A4 修复段** / 当前日批完成后停止（…）」；A4 段每窗口上报「A4 修复段 X/Y 窗口完成（日期）」→ 停止等待期显示「正在停止…（A4 修复段 3/7 窗口完成）」 |

配套加固（不改语义，只堵漏）：

- **A4 段异常处理补 `except TaskCancelled: raise`**——原外层 `except Exception` 会把停止请求降级成
  「A4 变更检测异常（降级跳过，不影响增量）」，导致**用户已停止却继续跑主增量**（与三处透传同款陷阱）；
- **空窗口（`len(raw_df)==0`）同样走段边界**——原实现 `continue` 会绕过该窗口的取消检查。

## 3. 实施期关键更正（**请审计重点复核**）

派单 ② 的表述是「A4 每窗口完成同样推进游标」。**字面执行会制造数据缺口**，故按下述方式落地并显式上报：

- 主游标 `last_completed` 的语义 = **主窗口的连续前缀**（续跑从 +1 起拉）；
- A4 窗口来自**云端 repair/full 声明的修复日期集合**，与主窗口起点无关；
- 若把 A4 修复日写进 `last_completed`：停止于 A4 第 2 窗（如 2024-05-11）后，续跑会从 2024-05-12 起拉，
  **跳过 2024-01-01~2024-05-09 这段从未拉取的主窗口区间** → 静默数据缺口；
- 故落地为：**A4 进度在同一个游标文件内独立记账**（`a4.completed_windows`），
  同样达成「A4 段进度在停止后不丢失、续跑跳过已修复窗口」，但不污染主续传点。

已加**防线测试** `test_a4_progress_does_not_corrupt_main_resume_point`：断言 A4 完成后 `last_completed` 仍为 `None`。

## 4. 验收

| # | 项 | 结果 | 证据 |
|---|---|---|---|
| ① | A4 段响应停止 | PASS | `test_a4_segment_honours_stop_and_records_progress`：第 2 个窗口边界命中 → 仅 2 个窗口被重拉，`TaskCancelled` 上抛 |
| ①' | 空窗口不绕过边界 | PASS | `test_a4_boundary_is_checked_even_for_empty_windows`：空窗口不写库但**到达段边界**（首边界即命中） |
| ② | A4 进度不丢失 | PASS | 同上用例断言 `a4_completed() == {前 2 个窗口}`；`test_a4_resume_skips_already_repaired_windows`：预置 1 窗完成 → 复跑仅处理剩余 2 窗 |
| ②' | 不污染主续传点 | PASS | `test_a4_progress_does_not_corrupt_main_resume_point`（数据缺口防线） |
| ③ | A4 段进度上报 GUI | PASS | 同上用例断言 progress 回调收到含「A4 修复段」的文案；`workers` 侧断言 `progress_cb` 已接线 |
| 回归 | GUI 套件 | PASS | `73 → 85 → 89 passed`（本修复 +4 用例，**零回归**） |

```
$ python -m pytest tests/test_gui_task_stop.py -q
................   16 passed in 3.29s        # 12 既有 + 4 本缺陷新增
$ python -m pytest tests -k gui -q
89 passed, 2876 deselected in 48.79s
```

## 5. 缺陷 ④（水位溯源覆盖审计 + 补建）：**BLOCKED — 主库写锁竞争实证**

派单要求对在库任务表做水位覆盖审计并补建缺失水位。**本次无法执行，原因是硬阻塞，非未做**：

```
$ python scripts/audit_watermark_coverage.py
[BLOCKED] 无法打开主库（单写者锁）： IOException
  IO Error: Cannot open file "...\data\quantstudio.db": 另一个程序正在使用此文件，进程无法访问。
  File is already open in ...\Python311\python.exe (PID 213124)
[ACTION] 请先关闭 GUI（main_gui.py）与回填进程，再重跑本工具。
```

DuckDB 为单写者文件锁语义：主库（37.9 GB）当前被 PID 213124（GUI/回填链）持有，
**连只读打开都被拒**（`IOException`），故审计与补建都无法在不打扰在用进程的前提下进行——
这正是设计 §六回退条件中的「与回填运行发生写锁竞争实证 → 暂停实施待窗口」。

**已交付可直接执行的工具**（未执行写盘）：`scripts/audit_watermark_coverage.py`

- 默认 **dry-run 只读**：枚举 `collector_tasks.json` 全部启用任务表，逐表输出
  `TABLE / FREQ / STATUS / ROWS / WATERMARK`，缺失者单列 `[TO-BUILD] table/freq -> watermark=<值>`；
- `--apply` 才实际补建；补建走**正式水位写入路径** `writer.advance_watermark(...)`
  （8 列 DDL + 审计列哨兵 + 写锁），**不裸写 SQL**；
- 初始水位口径与框架同一归一化：`str(int(MAX(time_key)))`（对齐 `ResidentCollector._max_date`，
  即水位为 epoch-ms 字符串，非日期字符串）；
- 锁占用时以退出码 2 + 明确 ACTION 提示失败，不静默。

**待办（需安全窗口）**：关闭 GUI 与回填 → `python scripts/audit_watermark_coverage.py`（看清单）
→ 确认 → 加 `--apply` 补建 → 抽查 `source_watermark` 行。
注意：该脚本的 `--apply` 路径**未对真实主库实测**（因锁无法打开），首次运行请先 dry-run 核对候选值。

## 6. 快照 / 边界

- 零副作用快照：`bf03679e3a595ad612c849cb55eee48bc09d37b9`（`git stash store` 已持久化）。
  说明：本次改动的前置基线即 HEAD 提交（上一笔 80cc85a 已入库），故精确回退为
  `git checkout HEAD -- <本次改动文件>`，不需要整树回退；
- 不触碰：daemon 三段式停止 / QFQ cycle 状态机 / 官方水位提交路径 / 其他 tab / 回测取消路径；
- 未触碰主库（测试全用 tmp_path；审计工具只读 dry-run）。
