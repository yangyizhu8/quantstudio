# GUI 非阻塞启动修复 · 实施与验收（D+1，2026-09-23）

- 方案：`docs/gui-startup-nonblocking-design.md`（修订版已过审）
- 归属：客户运维会话｜实施日：D+1（2026-09-23）｜验收：V1a–V8
- 状态：**五笔全部实施完成；V1a/V2/V3/V4/V7 已实测**；V8（主 venv 迁移）为运维动作，待执行

## 1. 五笔实施清单（分笔提交，精确路径）

| 笔 | 内容 | 提交 | 文件 |
|---|---|---|---|
| 笔2 | 只读查询**超时降级 + 降级后自动恢复** | `7ca1d6a` | `quantstudio/gui/db_helper.py`、`tests/test_gui_db_helper_deadline.py` |
| 笔1 | **非阻塞启动**（首屏只建 tab0 + 其余分帧增量创建 + 首查/首刷延后） | `22d7c5b` | `quantstudio/gui/main_window.py`、`quantstudio/gui/tabs/task_tab.py` |
| 文档 | 台账 + 设计文档（过审）+ A 案卷宗 | `ea4bca1` | `docs/handoff/customer-ops-ledger.md` 等 4 文件 |
| 笔3 | daemon **轮次收尾安全检查点**（+ 共享模块） | `aef6b9d` | `quantstudio/pipeline/db_checkpoint.py`、`quantstudio/pipeline/daemon.py`、`tests/test_db_checkpoint.py` |
| 笔4 | **WAL 体积巡检**（含 daemon 运行前置守卫） | `28eaf41` | `scripts/wal_health_check.py` |
| 笔5 | **版本闸三入口** | 见本批 | `main_gui.py`、`quantstudio/pipeline/daemon.py`、`scripts/activate_venv.bat`、`quantstudio/pipeline/duckdb_version_gate.py`、`tests/test_duckdb_version_gate.py` |

## 2. 验收实测（V1a–V8）

| # | 判据 | 实测 | 结论 |
|---|---|---|---|
| **V1a** | 构造大 WAL 场景下窗口可交互 ≤5 s | **2.45 s**（修复前：>15 分钟窗口不出现；维护后基线 12.4 s） | **PASS** |
| **V1b** | 首查完成时间单独记录、不显著劣化 | 构造期 **14.94 s → 1.49 s**；`_refresh_tab(0)` 2.91 s 移出构造期，首查在事件循环内完成（窗口已可见，不阻塞交互） | **PASS**（首查不再影响可交互时间） |
| **V2** | 超时降级文案出现 + **降级后自动恢复** + 数据不损坏 | `tests/test_gui_db_helper_deadline.py` **5 passed**：C1 超时快速降级（≤deadline）/ C2 下次调用自动收割结果 / C3 真故障仍上抛 / C4 不新起线程 / C5 成功路径透传；UI 侧每 30 s 自动重试 + 提示 + 手动「刷新」入口 | **PASS** |
| **V3** | daemon 收尾后 WAL 体积上限 | `daemon.py ResidentCollector.close()`（每轮采集后调用）追加安全检查点；`tests/test_db_checkpoint.py` **5 passed**：无 WAL 空操作 / 副本库真实收敛归零 / 超时放弃 / 失败不抛 | **PASS**（机制+副本实证） |
| **V4** | WAL 巡检告警 | `scripts/wal_health_check.py` 实跑：只巡 → 生产 WAL 3.0 MB / 阈值 268.4 MB → 正常（exit 0）；`--checkpoint` 在 daemon 运行时**拒绝**（exit 2，理由「status=running 且 pid=40096 存活」）→ **防自卡死守卫生效** | **PASS** |
| **V5** | GUI 回归全绿 | GUI 相关 10 文件 **87 passed**（含修复 1 处因延后加载引起的既有用例时序回归：改用「拆分」——JSON 读取留构造期、DB 查询延后） | **PASS**（GUI 面） |
| **V6** | 证据入 `docs/evidence/` | 本文件 + `gui-startup-wal-checkpoint-baseline-20260923.md` + `duckdb-version-environment-map-20260923.md` | **PASS** |
| **V7** | 版本闸：以 `venv_miniQMT`（1.5.3）**实际触发**拒启（验证器过≠闸过） | 真实进程触发三条受支持入口**全部拒启（exit 3）**：① `venv_miniQMT\python.exe main_gui.py` ② `venv_miniQMT\python.exe -m quantstudio.pipeline.daemon …` ③ `venv_miniQMT\python.exe -c "from quantstudio.pipeline.daemon import main; main()"`（GUI 拉起路径等价形态）；逃生阀 `QS_DUCKDB_VERSION_GATE=0` 放行且留醒目警告；合规解释器（1.4.5）`-m --help` 正常。机制用例 `tests/test_duckdb_version_gate.py` **8 passed** | **PASS** |
| **V8** | 主 venv `venv_quant_studio`（1.5.4）迁移验证 | **待执行**（运维动作：降级该 venv 或改用合规解释器；闸门落地后未迁移即被拒启——预期行为） | **PENDING** |

## 3. 实施期发现与更正（如实记录）

1. **「直启」形态在本代码库结构上不被支持**：`python quantstudio/pipeline/daemon.py` 必然
   `ImportError: attempted relative import with no known parent package`（模块使用相对导入
   `from .task_resume import …`）。实测：先补 sys.path 引导仍失败于相对导入 → 已**回退**该引导
   （不留死代码）。**受支持入口** = `-m` / GUI 拉起子进程 / `-c import main; main()`，三者均已过闸。
2. **A 案卷宗一处推断更正**：`daemon_status.json.cmdline` 在 `-m` 形态下与直启**同形**
   （`sys.argv[0]` = 模块文件绝对路径），故不能据此判断启动形态；且直启会直接崩 →
   09-21 那次 daemon **应为 `-m` 启动**（非「直启」）。已在卷宗与本文件更正。
3. **一处测试时序回归（已修）**：笔1 初版把 `_load_tasks` 整体延后，导致既有用例
   `test_gui_task_stop::test_v3_cancelled_task_done_marks_stopped` 读 `tab.tasks` 为空而失败；
   改为**拆分**（JSON 读取留构造期 → `self.tasks` 契约不变；仅 DB 查询延后）后 87 passed。

## 4. 待办

1. **V8**：主 venv（`_runtime\venv_quant_studio`，duckdb 1.5.4）迁移路径落地（降级或切换解释器），
   并记录迁移后启动实测；
2. **V5 全量回归**：GUI 面已 87 passed；待跑与本次改动相关的更大范围回归（数据管线/写入器）
   与黄金结果对比；
3. **推送**：五笔 + 文档批均为**本地提交**（未推送）；按六步第 5/6 步经用户确认后双推
   （注意：本地领先远程含他会话提交，推送需协调，见 `docs/ops-verification-conventions.md` C7）；
4. trading 同步门：本次触及 `quantstudio/`、`main_gui.py`、`scripts/` → **不豁免**。
