# 客户运维会话 · 工作台账

- 维护：客户运维会话｜最后更新：2026-09-23（D+1 开工令）
- 用途：本会话在途任务、暂缓项、纪律要求的唯一台账（防遗漏、防误判为取消）

## 一、暂缓项（**暂缓 ≠ 取消**）

| # | 项 | 状态 | 暂缓原因 | 解除条件 | 登记时点 |
|---|---|---|---|---|---|
| S1 | **错误一 T3**：`quantstudio/pipeline/sources/mcp_adapter.py` 的 `revision_alert` outbox | **暂缓-等会话 2 批** | 客户运维 2 的新客户四问题批对**同一文件**大改先行，避免共享文件并行叠加（本周期第二类事故的针对性防线） | 会话 2 该批**落地后**（其提交入库、工作区该文件不再被其占用）即恢复实施 | 2026-09-23（ZCode 协调令） |

> 处置口径：本条**不是取消**；恢复时按六步流水线正常推进（方案→审计→实施→验收→确认→双推）。

## 二、D+1 实施批（GUI 启动卡死修复，五笔）

- 方案：`docs/gui-startup-nonblocking-design.md`（修订版**已过审**，2026-09-23）
- 纪律：**分笔提交 + 路径限定**；`mcp_adapter.py` / `daemon_lifecycle.py` / `main_gui.py` **每次 edit 后即时 `git diff` 自检**；推送前经用户确认

| 笔 | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | GUI 异步化（窗口先显示，首查移出构造期 + 首屏「读取中」占位） | `main_gui.py` / `gui/main_window.py` / `gui/tabs/task_tab.py` | 进行中 |
| 2 | 超时降级 + 降级后自动恢复（deadline 5 s / 自动重试 30 s / 手动刷新） | `gui/db_helper.py`（+ GUI 侧重试入口） | 待实施 |
| 3 | daemon 收尾 CHECKPOINT（轮次收尾/优雅退出前） | `pipeline/daemon_lifecycle.py` | 待实施 |
| 4 | WAL 体积巡检 + 阈值告警（**前置：daemon 不在运行 + RW open 超时放弃**） | `scripts/wal_health_check.py`（+ daemon 空闲期挂载） | 待实施 |
| 5 | 版本闸三入口（wrapper / 直启 / GUI 拉起；非 1.4.x 拒启；含主 venv 迁移路径） | `main_gui.py` / `pipeline/daemon.py` / `scripts/activate_venv.bat` | 待实施 |

**验收**：V1a/V1b｜V2｜V3｜V4｜V5｜V6｜**V7（`venv_miniQMT` 1.5.3 真实触发拒启）**｜V8（主 venv 迁移验证）。

## 三、已闭环（本会话近期）

| 案 | 结论 | 证据 |
|---|---|---|
| CASE-005 写锁残留停更（三客户） | 已闭环（用户裁定 2026-09-18），卷宗 `docs/case005-write-lock-stale-selfheal-incident.md` | T7 验收 + 通知链 + 归档提交 `9df318c` |
| GUI 启动卡死案（维护+归因） | CHECKPOINT 完成（WAL 2.33 GB→0，回放 1338.4 s / 检查点 7.3 s）；冷启动 12.4 s | `docs/evidence/gui-startup-wal-checkpoint-baseline-20260923.md` |
| A 案：duckdb 混版 | **定谳**：钉版从未覆盖实际运行环境；裁定①=版本闸三入口 + 连接级隔离 | `docs/evidence/duckdb-version-environment-map-20260923.md`（含 §6 今晚口径落地闭环：pid 40096 / `python3.12.9` / duckdb 1.4.5 / WAL=0） |
