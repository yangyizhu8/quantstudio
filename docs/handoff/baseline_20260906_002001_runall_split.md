# 会话基线 · 2026-09-06 00:20 ·「全部执行」拆分实施前

## 任务
采集任务页 GUI：run_all 改名「▶ 全部执行（增量）」+ 新增「▶▶ 全部执行（全量）」按钮（方案已审计批准，纯 GUI 层，pipeline/worker/daemon 零改动）。

## 回退点（零副作用，AGENTS.md 写前快照纪律）
- `git stash create -u -m "baseline-20260906-002001"` → hash **de4b2248b105f50e10abb2648fba4cf29a633b5b**
- 已 `git stash store` 持久化（stash@{0}: baseline-20260906-002001）
- 误操作恢复：`git reset --hard de4b2248b105f50e10abb2648fba4cf29a633b5b`（最后手段，优先文件级恢复）

## 目标文件状态（动码前核查）
- `quantstudio/gui/tabs/task_tab.py`：**干净**（无未提交改动）
- `tests/test_gui_task_audit_separation.py`：**干净**（无未提交改动）

## HEAD
- aca1267 feat(align): 三线并窗收官 — A2 观测网 + B2 归一消音 + B2' 跨日窗口 + D4 缓存键 + 探针 v2 哈希追认（branch: main）

## 工作区其他脏文件（属其他会话，本会话不触碰）
- M: config/profiles/mcp_only/sources_config.json, docs/evidence/ptrade-pctchg-synth-evidence.md, docs/handoff/baseline_20260821_0040_dispatch.md, docs/portfolio-live-cash-design.md
- D: quantstudio/backtest/strategies/first_board_pullback_daily__candidate_quantstudio.py, sw_industry_etf_rotation_8f__candidate_quantstudio.py
- 大量 ?? 未跟踪（agent_workspace/ 等）——不纳入本会话任何 add 操作（精确 add 纪律）

## 文件级备份
- docs/handoff/backup-task_tab-20260906-002001.py
- docs/handoff/backup-test_gui_task_audit_separation-20260906-002001.py

## 本会话预期改动清单（精确 add 范围）
- quantstudio/gui/tabs/task_tab.py
- tests/test_gui_task_audit_separation.py
- docs/handoff/baseline_20260906_002001_runall_split.md（本文件）
- docs/handoff/backup-task_tab-20260906-002001.py
- docs/handoff/backup-test_gui_task_audit_separation-20260906-002001.py
- docs/evidence/gui-runall-split-20260906.md（验收证据，实施后写）
