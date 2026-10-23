# 证据：GUI「进程常驻增量拉取」启动失败修复（N4 实施 + 逐点门控）

- 日期：2026-10-23
- 方案件：`docs/daemon-gui-start-failure-design-20261023.md`（②审通过 + 三裁）
- 基线：`HEAD=891a274`（`perf(backtest): check_limit 结果只读缓存`，与本方案实施面无交集）
- 类型：框架层 · 纯增益（FP-1/2/3/5 新增检测型；FP-4 纯恢复型；无数据修正型）

## 0 实施前状态（写前快照纪律）

| 项 | 事实 |
|---|---|
| 工作树 | `daemon_process.py` / `task_tab.py` / `daemon.py` 干净；**`main_window.py` 为他线在途 M（+4 行于 L63/L215-217）** |
| 叠加声明 | 本方案**未改 `main_window.py`**（核对结论见 §4），故未与他线改动叠加 |
| 其他线在途 | 检测到他线会话正在执行 daemon 停启窗口（node→powershell 脚本：优雅停→`verify_minutes_recover.py`→重启→查 WAL，`reason="window-verify"`）⇒ 本轮**全程避让**，未启停任何生产 daemon |

## 1 逐点门控记录

### FP-1（D-B）`read_bootstrap_log_tail` 回退链 — ✅ 绿

- 改动：`quantstudio/gui/daemon_process.py`（`read_bootstrap_log_tail`，+33/−3；三 hunk 全在函数内）
- 判据：**A1** 0B bootstrap + daemon.log 含原因 ⇒ 非空 + 含原因 + 含来源标注（PASS）；**A2** 有内容不越界（PASS）；**A3** 两文件皆缺 ⇒ 含两文件名提示、不抛（PASS）；补充：bootstrap 缺失+daemon.log 在 ⇒ 回退成功（PASS）
- 结果：**9/9 PASS**；AST OK；`git diff` 确认只动该函数

### FP-2（D-A）抢锁失败补写一行到 bootstrap — ✅ 绿

- 改动：`quantstudio/pipeline/daemon.py`（`acquire_instance_lock` 失败分支，+15 纯新增）
- 判据：**A4** 真实撞锁（占锁 dummy + 子进程）⇒ bootstrap 非 0B、含「另一个 daemon 已在运行」、含 token 前 8 位、`[bootstrap]` 仅 1 行（PASS）；**A5** 无 token（CLI 形态）⇒ 不打屏、daemon.log 仍记（PASS）；**A6** print 位于抢锁失败分支内且 try 兜住（PASS）
- 结果：**11/11 PASS**；AST OK

### FP-3（D-C）双源前置判据 + 第三态文案 — ✅ 绿（含两处实测修正）

- 改动：`daemon_process.py` 新增 `_is_daemon_cmdline()` + `find_running_daemon_pids()`；`task_tab.py` `_start_daemon` 加第二源 + 第三态文案
- **实测修正①（误判）**：初版用「cmdline 含 `quantstudio.pipeline.daemon` 子串」，真实环境**误命中** PID 51320——那是 `node.exe … powershell -Command "…quantstudio.pipeline.daemon…"` 的诊断脚本（命令行**提及**该串但非 daemon）⇒ 会误拦正常启动。改为**结构化匹配**（`-m` 相邻配对 / 脚本路径形态）+ 解释器白名单。
- **实测修正②（过严）**：初版只认 `/python.exe` 后缀，桩测用裸名 `python.exe` 全灭 ⇒ 改 **basename 判定**（真实 psutil 两种形态都可能返回）。
- 判据：**A7/A8/A9** 顺序 `alive闸 < 第二源 < 真启动`（PASS）+ 文案四要素 PID/启动于/「刷新」/「停止」（PASS）；**A8** 结构化过滤 7 形态参数化（含 2 条误判回归、裸名、脚本路径、非 python 解释器、`-m` 后非该模块、空）（PASS）；**A9** 枚举异常 fail-soft（PASS）；**A10** once/自身排除（PASS）；真实枚举 = `[53256]` 精确（不含 51320）
- 结果：**13/13 PASS**（修正后复验）；AST OK

### FP-4（R-1）按钮状态实时化 + stopped 态探测 — ✅ 绿（A11-A13 实测通过，含 A11 缺陷修复）

- 改动：`task_tab.py` 4 处 —— ①`_update_daemon_btn` 自适应间隔（running 3s / stopped 5s）+ 定时器保持激活；②`_on_daemon_poll` 新增 `elif stopped` 分支双源探测并同步；③`_toggle_daemon` 点击前 `_sync_daemon_state()`；④移除 3 处 `_daemon_poll_timer.stop()`（L708/L737/L771）
- **演练暴露并修复的缺陷（A11 自愈失效）**：初版在第二源命中后仍调 `_sync_daemon_state()`，而后者**只看 status**——当 status 陈旧/损坏（verify=stale）时会立刻判回 `stopped`，自愈无效。修为：`not is_daemon_running()` 时**以第二源为准**直接置 `running` + 回填 token + 状态栏提示「（状态文件不可用）」。
- 判据实测（隔离环境真实进程，13/13 PASS）：**A11** 自愈 running、按钮变「🟢 运行中」、状态栏含「外部启动」、单次 poll 2.07s（≤5s）；**A12** 三次去抖后 stopped、按钮「🔴 已停止」、定时器仍激活、间隔 5000；**A13** stopped 态 60s CPU **0.317%**（<0.5%，9 次 poll/60s）

### FP-5（R-2）诊断留痕 — ✅ 绿（落点实现级调整已声明）

- 改动：`daemon_process.py` 新增 `diagnose_start_gate()`；`task_tab.py` 在判定「未拦住」分支调用
- **实现级调整（相对方案）**：原方案写 `daemon.log`；实施改为 **`data/logs/gui_gate_diag.log`**（JSON 行）。理由：`daemon.log` 由 daemon 侧 `TimedRotatingFileHandler` 持有并轮转，GUI 直接追加有轮转冲突/占用风险；落同目录专用文件便于 grep 且不干扰既有日志契约。**判据 A14 相应表述为「诊断文件出现含 verdict= 的行」。**
- 判据：**A14** 默认开 ⇒ 落一行含 `verdict`/`proc_pids`（PASS）；**A15** `QS_GUI_DAEMON_DIAG=0` ⇒ 不写（PASS）
- 结果：**PASS**；env 默认开（按三裁：R-2 定谳后转默认关）

## 2 测试结果

| 项 | 结果 |
|---|---|
| 新建固件 `tests/test_daemon_gui_start_paths.py` | **20 passed in 4.11s**（A4 真实撞锁 1.92s、A5 1.58s — 确认真跑非短路） |
| daemon/gui 全量回归（19 文件） | **243 passed / exit 0**（6m28s） |
| 语法 | 三文件 AST OK |
| 生产数据 | 全程临时 `QUANTSTUDIO_DATA_ROOT` / monkeypatch `_data_root`，**零写入生产 data/**；未启停生产 daemon |

## 3 逐点门控汇总

| FP | 门禁 | 结论 |
|---|---|---|
| FP-1 | A1–A3 + 补充 | ✅ 绿（9/9） |
| FP-2 | A4–A6 | ✅ 绿（11/11） |
| FP-3 | A7–A10 + 误判回归 | ✅ 绿（13/13） |
| FP-4 | A11–A13 | ✅ **实测绿（13/13，隔离环境真实进程）** |
| FP-5 | A14–A15 | ✅ 绿（2/2） |

**未完成项**：无（A11-A13 已于 2026-10-23 夜用**隔离 `QUANTSTUDIO_DATA_ROOT` + 真实 daemon 子进程**补验通过，未占用生产停服窗口）。

## 4 范围收窄（相对方案）

- 方案原列 `main_window.py`（第二入口 `toggle_daemon`）为改动面；**实施前核对后不改**：
  该入口走 `DaemonLifecycle.ensure_started()`（`main_window.py:481-483`）——**进程内路径**，不启动子进程、不读 bootstrap、不弹「启动后立即退出」，**不属本案缺陷面**；且该文件在实施时为他线在途 M 文件。按纯增益原则不做无必要改动。
- 相应：本方案实际改动 **3 个源文件 + 1 个测试文件**（原计划 4+1）。

## 5 变更清单（待提交）

| 文件 | 性质 |
|---|---|
| `quantstudio/gui/daemon_process.py` | M（FP-1 回退链 + FP-3 双源函数 + FP-5 诊断） |
| `quantstudio/gui/tabs/task_tab.py` | M（FP-3 双源判据/文案 + FP-4 四处 + FP-5 调用 + import） |
| `quantstudio/pipeline/daemon.py` | M（FP-2 补写一行） |
| `tests/test_daemon_gui_start_paths.py` | 新增（A1–A15 固件） |
| `docs/daemon-gui-start-failure-design-20261023.md` | 新增（方案件） |
| `docs/evidence/daemon-gui-start-failure-20261023.md` | 新增（本文件） |

**未提交、未推送**（等 ⑤用户确认）。

## 6 纪律声明

- **避让他线 daemon 窗口**：全程未启停任何生产 daemon、未写生产库；A4/A5 的撞锁子进程均指向临时 `QUANTSTUDIO_DATA_ROOT`。
- **未触碰他线在途文件**：`main_window.py` 只读，未修改。
- **编码委派**：未委派 `zcode_code`（逐点门控需即时验证、上下文强耦合；已在会话中声明）。

## 7 A11-A13 补验记录（2026-10-23 夜 · 隔离环境真实进程）

**为何不走生产窗**：原计划在生产停服窗内演练，实施期间检测到他线 daemon 窗口与 FP-5 迁移前置（迁移要求 daemon 停），且演练只依赖「daemon 存在/不存在」这一事实——故改为**隔离 `QUANTSTUDIO_DATA_ROOT` 起真实 daemon 子进程**，主进程对生产仅只读，任意时刻可跑，不占窗口、不干扰迁移。

**脚本**：`docs/handoff/n4-a11-a13-drill-20261023.py`（可复跑）

**结果（13/13 PASS）**：

| 判据 | 实测 |
|---|---|
| A12 去抖第 1/2 次仍 running | PASS（真实日志同步输出 1/3、2/3） |
| A12 第 3 次判 stopped | PASS |
| A12 按钮文案=已停止 | PASS（🔴 进程常驻增量拉取：已停止（点击启动）） |
| A12 stopped 后定时器仍激活 / 间隔 5000 | PASS |
| A11 前置：隔离 root 真实 daemon 拉起（pid 53868） | PASS |
| A11 前置：枚举能看见它 | PASS（`[{'pid': 53868, 'started_at': ...}]`） |
| A11 自愈为 running（status 不可用模拟） | PASS |
| A11 按钮文案=运行中 / 状态栏含「外部启动」 | PASS |
| A11 单次 poll 内完成 | PASS（**2.07s** ≤5s） |
| A13 stopped 态 60s CPU | PASS（**0.317%** <0.5%，9 次 poll/60s） |

**由此修复的缺陷**：见 §1 FP-4 条目（A11 自愈失效——第二源命中后不得再走只看 status 的 `_sync_daemon_state()`）。

**回归**：固件 20 passed；`test_gui_task_stop.py` / `test_gui_task_status_real.py` / `test_daemon_lifecycle.py` 42 passed。

## 8 生产窗 ④⑤ 执行记录（2026-10-23 21:59，与 FP-5 2.2 迁移合窗）

**依据**：`docs/evidence/fp5-22-migration-window-20261023.md`（`17250b2`, 21:55）明确「阶段④-⑤（客户专属总运维执行）：N4 A11-13 真实启停演练（停/启本身即演练对象；"启"=在 2.2 库上拉起 ⇒ 同步构成 A35 的 daemon 可启动实证）；⑤ 拉起 daemon + 双验证」。
**脚本**：`docs/handoff/n4-prod-window-20261023.py`（可复跑；`--dry-run` 只读）

### 8.1 前置（①）

| 项 | 实测 |
|---|---|
| daemon | 停止态（`is_daemon_running=False`）；status 陈旧 `pid=69832 running`（进程已不存在，verify=stale） |
| 库 | `qfq_bootstrap_run` 末版 `reanchor-2.2` ✓；`source_watermark` **9 列**（含 `last_run_id`）✓；**24 行** ✓ |

### 8.2 A11（②·GUI 同款路径启动 + poll 自愈）

- `start_daemon_subprocess` 启动：token `7bac366e…`、pid **46576**
- 生产 status 变 `running`；`_on_daemon_poll()` 一次即自愈为 `running`，按钮「🟢 运行中」，**0.00s**（≤5s）
- 新 pid ≠ 旧 69832 ✓

### 8.3 A12（③·优雅停 + 三次去抖）

- `request_graceful_stop(token)` 成功 → daemon 确证退出 → 去抖 1/2 次仍 `running` → **第 3 次判 `stopped`**，按钮「🔴 已停止」✓（与日志 `收到停止请求→优雅退出→清理本进程 status` 逐条对应）

### 8.4 ⑤ 最终拉起 + 双验证

- 拉起：pid **69036**（token `85e82511…`），status `running`，`started_at=2026-10-23T21:59:35`
- **观察期 60s**：进程稳定存活、WS 118MB 平（±0.1MB）、`is_daemon_running()=True`、`find_running_daemon_pids()=[69036]`
- **daemon.log 近 200 行 `ERROR/CRITICAL/Traceback = 0`** ✓
- 日志实证链：`清理陈旧 status 文件（原进程已退出）` → `status 已发布(46576)` → `收到停止请求` → `优雅退出` → `status 已发布(69036)` → `DuckDBWriter tables initialized` → `ConfigLint 校验通过(0 错误/0 警告)`
- `gui_gate_diag.log` **不存在** ⇒ 本轮启动未走「未拦住」分支（正常路径），FP-5 无噪声 ✓

### 8.5 一处判据修正（判据错、非产物错）

脚本 ⑤ 原判据「daemon 运行中只读打开库」**FAIL**：`Cannot open file … 另一个程序正在使用此文件` —— DuckDB 单写独占，**daemon 运行中任何进程（含 read_only）都无法打开**，此为既有设计（同一设计早已记录在 QFQ 取证卡死问题上）。**正确判据**：水位表可读性在**停态**验证（① 已证 24 行 / 9 列）；运行中改以 status + 日志 + 进程稳定性为证。已按此修正结论，**不计为缺陷**。

### 8.6 由此获得的额外实证（价值高于原判据）

1. **正常启动时 bootstrap 亦为 0B**（`daemon_bootstrap_7bac366e…log` size=0 而启动成功）⇒ **FP-1 回退链的价值不止撞锁场景**：任何「启动后未握手/超时」场景，用户都只看到空尾部。
2. **daemon 启动会自动清理陈旧 status**（日志原文）⇒ R-2 的「status 陈旧」不是永久故障，第二源（FP-3）与自动清理共同兜住。
