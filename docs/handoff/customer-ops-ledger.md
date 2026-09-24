# 客户运维会话 · 工作台账

- 维护：客户运维会话｜最后更新：2026-09-23（D+1 开工令）
- 用途：本会话在途任务、暂缓项、纪律要求的唯一台账（防遗漏、防误判为取消）

## 一、暂缓项（**暂缓 ≠ 取消**）

| # | 项 | 状态 | 暂缓原因 | 解除条件 | 登记时点 |
|---|---|---|---|---|---|
| S1 | **错误一 T3**：`quantstudio/pipeline/sources/mcp_adapter.py` 的 `revision_alert` outbox | **已解除暂缓 → 恢复实施**（排本线节奏） | 曾因客户运维 2 的新客户四问题批对**同一文件**大改而暂缓（防共享文件并行叠加） | **已满足**：会话 2 该批已落地并推送（`c62c1cc` 分表型质量门禁、`41d70bb` 等）；工作区该文件不再被其占用 | 2026-09-23（ZCode 协调令 / 同日解除令） |

> 恢复实施纪律（共享文件）：`mcp_adapter.py` 每次 edit 后**即时 `git diff` 自检** + 精确清单提交；
> 若实施涉及云端 `etf_minutes` close 口径域（会话 2 已登记 tech-debt），一并核。

> 处置口径：本条**不是取消**；恢复时按六步流水线正常推进（方案→审计→实施→验收→确认→双推）。

## 二、D+1 实施批（GUI 启动卡死修复，五笔）

- 方案：`docs/gui-startup-nonblocking-design.md`（修订版**已过审**，2026-09-23）
- 纪律：**分笔提交 + 路径限定**；`mcp_adapter.py` / `daemon_lifecycle.py` / `main_gui.py` **每次 edit 后即时 `git diff` 自检**；推送前经用户确认

| 笔 | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | GUI 异步化（窗口先显示，首查移出构造期 + 首屏「读取中」占位） | `main_gui.py` / `gui/main_window.py` / `gui/tabs/task_tab.py` | **已完成** `22d7c5b`（V1a 2.45 s） |
| 2 | 超时降级 + 降级后自动恢复（deadline 5 s / 自动重试 30 s / 手动刷新） | `gui/db_helper.py`（+ GUI 侧重试入口） | **已完成** `7ca1d6a`（5 passed） |
| 3 | daemon 收尾 CHECKPOINT（轮次收尾/优雅退出前） | `pipeline/daemon_lifecycle.py` → 实落 `daemon.py:close()` + `pipeline/db_checkpoint.py` | **已完成** `aef6b9d`（5 passed） |
| 4 | WAL 体积巡检 + 阈值告警（**前置：daemon 不在运行 + RW open 超时放弃**） | `scripts/wal_health_check.py` | **已完成** `28eaf41`（守卫实测 exit 2） |
| 5 | 版本闸三入口（非 1.4.x 拒启；含主 venv 迁移路径） | `main_gui.py` / `pipeline/daemon.py` / `scripts/activate_venv.bat` + `pipeline/duckdb_version_gate.py` | **已完成** `b429f98`（三入口真实拒启 exit 3） |
| 6 | **V8 收口**：口径修正笔（官方解释器=Python311 + bat 帮助文本修正）+ 主 venv 降级 1.4.5 + 补装依赖 + GUI 冒烟 + psutil 自愈验证 | `scripts/activate_venv.bat`（修正笔）+ 环境（`_runtime\venv_quant_studio`） | **已完成**（GUI 冒烟 1.76 s / psutil 自愈 PASS） |

**验收**：V1a/V1b｜V2｜V3｜V4｜V5｜V6｜**V7（`venv_miniQMT` 1.5.3 真实触发拒启）**｜V8（主 venv 迁移验证）。

## 三、已闭环（本会话近期）

| 案 | 结论 | 证据 |
|---|---|---|
| **jabberwock 缺陷（ckey 空 shard → IndexError）** | **已闭环**：快审通过（`c44c81f`）→ **推送落地（远程 HEAD `1ae083f`，三方一致、在途 0）** → **通知件 N-20260924-02 已出件下发（用户转发，2026-09-24，目标版本 `1ae083f`）**，待客户回报（三路由） | `docs/jabberwock-ckey-empty-shards-fix-design.md`、`docs/handoff/notice-to-jabberwock-20260924.md` |
| **错误一 T3（revision_alert outbox）** | **实施完成并过验收 + 已推送**（`bcd6268`）：V1–V6 已验（V1 = outbox 0→非0）+ A/B 归因证实 8 例基线红；**V9 常令：明晨排定窗开启即开工**；**V7/V8 下轮真实采集后只读取样**；**G4 判据（部署后）：本机换代际后 `qfq_factor_revision_alert` 首笔写入 = outbox 通道首次实弹** | `docs/evidence/error1-t3-revision-alert-implementation-20260924.md` |
| **CASE-007 GUI 启动卡死**（WAL 残留 × 构造期同步打开） | **已闭环**：维护 CHECKPOINT（WAL 2.33GB→0；回放 1338.4s / 检查点 7.3s）+ 六笔修复 + V1a–V8 全 PASS（V1a 2.45s；回归 312 passed） | 卷宗 `docs/case007-gui-startup-hang-wal-replay-incident.md` |
| **CASE-008 duckdb 混版统一**（A 案 + 版本闸 + V8） | **已闭环**：定谳「钉版从未覆盖实际运行环境」；版本闸三入口真实拒启 exit 3；V8 主 venv 降级 1.4.5 + 补装 + GUI 冒烟 1.76s + psutil 自愈 PASS | 卷宗 `docs/case008-duckdb-version-mixing-unification-incident.md` |
| CASE-005 写锁残留停更（三客户） | 已闭环（用户裁定 2026-09-18） | `docs/case005-write-lock-stale-selfheal-incident.md` |

**推送闭环**：21 笔上远程；三方核对 local = origin/main = quantstudio-plus = quantstudio = `000b5bb`（0 笔残留）。

## 四、本线 backlog（低优先）

| # | 项 | 性质 | 追单 |
|---|---|---|---|
| B1 | `test_pit_filter::test_validator_is_single_chokepoint` 既有红（`writer.write` 实际 4 处：1334/1336/2993/3021，断言要求 1 处；15 passed / 1 failed） | **基线即红、与本批无关**；待归因（契约漂移 vs 期望陈旧） | `docs/handoff/tracking-test_pit_filter-baseline-red-20260923.md` |
| B2 | 云端 `etf_minutes` close 口径与还原链自洽性疑问（会话 2 已登记 tech-debt） | 同域核查（若错误一 T3 实施涉同域则一并核） | 会话 2 tech-debt 条目 |

## 五、探针脚本入库（验收可复现性）

本线探针已随批入库（精确清单，6 文件）：`agent_workspace/v8_gui_smoke.py`（V8 GUI 冒烟）、
`selfheal_psutil_check.py`（psutil 自愈路径）、`wal_semantics_probe.py`（read_only 不收敛 WAL 实证）、
`db_checkpoint_once.py`（一次性 CHECKPOINT 维护）、`gui_startup_profile.py` / `gui_tab_profile.py`（V1a 定位计时）。
