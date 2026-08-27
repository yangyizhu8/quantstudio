# 稳定化线（本会话）交付文件清单申报

> 申报时间：2026-08-26 04:15（总调度方案 2 强化版 ③ 项）
> 与 DSH 双端对齐线清单的差集 = 交付范围（供 11:45 终审）

## A. 框架代码（QuantStudio 仓库，均 untracked 新文件——周三 governance commit 走六步）

| 文件 | 内容 | 测试 |
|---|---|---|
| `scripts/governance_snapshot.py` | 快照 CLI（既有 3B 产物，untracked）+ 本线增量：v1.1 QDB 域白名单扩展（9 pattern）+ marker 父链归因（S1 启动豁免/yield 豁免/过期清扫）+ A-豁免（verify-only yield，`_VERIFY_YIELD_EXEMPT`） | 见 C |
| `scripts/final_snapshot_orchestrator_monday.py` | 周一夜窗口 orchestrator（O1 周一锚定） | py_compile（未运行需求） |
| `scripts/governance_snapshot.py.bak_h3_20260819` | （历史备份，可选不入 commit） | — |

## B. 测试（untracked 新文件）

| 文件 | 用例 | 实跑 |
|---|---|---|
| `tests/test_guard_qdb_domain_marker.py` | V1-V6（10 用例：marker 归因/父链/跨上下文/过期清扫/歧义名排除/yield+启动豁免） | 10/10 |
| `tests/test_verify_yield_exemption.py` | A-豁免 2 用例（verify 不 abort / create 红线不变） | 2/2 |

## C. 设计/证据文档（docs，均新文件）

| 文件 | 性质 |
|---|---|
| `docs/governance-guard-system-proc-design.md` | 白名单方案 v1.1（方案→审计→实施→验收全链） |
| `docs/evidence/final-snapshot-20260822-briefing.md` | 总简报（两事故+三次拦截+RSS 决断+marker 首验+全时间线） |
| `docs/evidence/snap003-verify-protect-checklist-20260825.md` | 执行清单 |
| `docs/evidence/snap003-acceptance-20260825-template.md` | 验收证据（已填实） |
| `docs/evidence/snap003-verify-20260826.json` | SNAP_003 分片 verify（20 表逐 hash） |
| `docs/evidence/snap002-backfill-verify-20260826.json` | SNAP_002 回填 verify |
| `docs/evidence/snap003-fullchain-evidence-index-20260826.md` | 全链证据汇总（交付 docs 引用） |
| `docs/evidence/backup-disable-authorization-20260824.md` | 备用双禁用启用/恢复登记 |

## D. 快照工件（data/snapshots/，运行产物，默认不进 git；是否入档由终审定）

`SNAP_20260825_003_81260e83/`（manifest+双库）、`index.json`、`protect.log`、`guard_refused.log`、启动证据 JSON ×2

## E. 仓库外（不计入本仓库 commit）

- `私募工作文件/.../issue_registry.md` v1.46（gate exception 关闭）
- `trading-battle-back/scripts/run_cloud_sync*.ps1 / run_repair_minutes_scheduled.ps1`（marker 代码，DSH 已以"正确日期版"重写恢复；该工作区 commit 归属另议）

## F. 技术债（登记，不入本次交付）

guard pattern 子串匹配缺陷（bash 裸提及误报 + pid3 幻影）→ WP8 后修复（词边界+排除 bash 宿主 / marker 专责）
