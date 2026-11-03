# audit_ok / quality_audit_ok 全消费面回扫清单（D1 · 节点⑳ ②审条件②）

- 生成：2026-11-03（改码后回扫，全仓 `quantstudio/**` + `scripts/**`）
- 目的：D1 引入归因细分位后，逐个确认**谁在读审计结论**、**是否需要改**、**改法**；
  避免「改了退出码却漏了另一处判定」——本次客户事故正是判定口径不一致导致。

## 0 判定口径（D1 定谳）

| 开关 | 语义 |
|---|---|
| `QS_AUDIT_STRICT_FAIL=1` | 旧语义逐位还原：任何 audit 失败 ⇒ exit 1（**优先级最高**） |
| `QS_AUDIT_SCOPE_ATTRIBUTION=legacy` | 旧归因：全局段失败按表级处理 ⇒ exit 1 |
| 默认（新语义） | 表级失败 ⇒ exit 1；仅全局段失败 ⇒ **exit 0 + `AUDIT_GLOBAL_FAIL`** |
| `audit_ok` | **真值不变**：审计跑完且无 error 级问题（不因细分位改写） |

## 1 回扫结果（逐行）

| 文件 | 行 | 代码片段 | 是否需改 | 处置 |
|---|---|---|---|---|
| `quantstudio/gui/workers.py` | 166 | `"quality_audit_ok": not qa_incomplete,` | 是（D2/D3 批处理） | payload 拆 `tasks_all_ok` / `audit_ok` / 四态计数 |
| `quantstudio/gui/workers.py` | 216 | `quality_audit_ok 语义变更如实声明：任务级审计汇总（全部有审计任务皆过=True），` | 是（D2/D3 批处理） | 改结构化字段消费 |
| `quantstudio/gui/workers.py` | 286 | `"quality_audit_ok": (all(audit_all) if audit_all else None),` | 是（D2/D3 批处理） | payload 拆 `tasks_all_ok` / `audit_ok` / 四态计数 |
| `quantstudio/gui/workers.py` | 318 | `audit_ok = None` | 是（D2/D3 批处理） | 改结构化字段消费 |
| `quantstudio/gui/workers.py` | 322 | `audit_ok = bool(self.collector._run_full_quality_audit())` | 是（D2/D3 批处理） | 改结构化字段消费 |
| `quantstudio/gui/workers.py` | 324 | `audit_ok = False` | 是（D2/D3 批处理） | 改结构化字段消费 |
| `quantstudio/gui/workers.py` | 334 | `"quality_audit_ok": audit_ok,` | 是（D2/D3 批处理） | payload 拆 `tasks_all_ok` / `audit_ok` / 四态计数 |
| `quantstudio/gui/tabs/task_tab.py` | 1172 | `audit_warning = result.get("quality_audit_ok") is False` | 否 | 未见语义消费 |
| `quantstudio/gui/tabs/task_tab.py` | 1242 | `and result.get("quality_audit_ok") is False` | 否 | 未见语义消费 |
| `quantstudio/pipeline/daemon.py` | 735 | `audit_ok = True` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 782 | `audit_ok = self._run_full_quality_audit(` | 否 | audit_ok 取值口径不变（真值） |
| `quantstudio/pipeline/daemon.py` | 800 | `# 旧语义 `task_ok and audit_ok` 让「数据已写入成功、只是审计没跑完」被判 failed` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 808 | `return task_ok and audit_ok` | 否 | A6 旧语义开关位（`QS_AUDIT_STRICT_FAIL` 控制），保持 |
| `quantstudio/pipeline/daemon.py` | 809 | `if task_ok and not audit_ok:` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 2487 | `{"task_found": bool, "task_ok": bool, "audit_run": bool, "audit_ok": bool,` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 2511 | `audit_ok = True` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 2553 | `audit_ok = self._run_full_quality_audit(tables=scope_tables)` | 否 | audit_ok 取值口径不变（真值） |
| `quantstudio/pipeline/daemon.py` | 2572 | `"audit_ok": audit_ok,` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 4195 | `def resolve_audit_exit(*, task_ok: bool, audit_run: bool, audit_ok: bool,` | 否 | 本批新增：退出码判定的**唯一 owner** |
| `quantstudio/pipeline/daemon.py` | 4206 | `if audit_ok:` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon.py` | 4397 | `audit_ok=bool(result.get("audit_ok")),` | 否 | 审计真值消费 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 730 | `quality_audit_ok = False` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 779 | `success_count=0, failed_count=0, quality_audit_ok=False,` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 998 | `quality_audit_ok = collector._run_full_quality_audit(` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 1002 | `quality_audit_ok = False` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 1041 | `quality_audit_ok=quality_audit_ok, task_summary=task_summary,` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 1081 | `quality_audit_ok=quality_audit_ok, task_summary=task_summary,` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/daemon_lifecycle.py` | 1111 | `quality_audit_ok=quality_audit_ok, task_summary=task_summary,` | 待确认（D2 批） | 常驻链路读 `quality_audit_ok`：仅用于日志/状态，不影响退出码 |
| `quantstudio/pipeline/quality_audit.py` | 58 | `# ── D1 归因聚合：表级 / 全局段 分开判定（audit_ok 仍为真值，不变）────────` | 否 | 归因实现方（scope 打标 + table_ok/global_ok） |

## 2 结论与后续

- **唯一退出码判定点**：`daemon.resolve_audit_exit()`（本批新增，纯函数）；
  `daemon.main()` 的 once 分支只调它 ⇒ 退出码口径单一真源。
- `audit_ok` 保持真值，任何消费者**不需要**因 D1 改动行为；
  需改的只有两类：① GUI 汇总（workers/task_tab）——改读四态计数与细分位（D2/D3）；
  ② 契约测试（`test_daemon_once_exit_contract.py`）——已按新契约更新（原键一个不删）。
- `daemon_lifecycle`（常驻链路）只把 `quality_audit_ok` 写日志/状态，**不影响退出码**；
  D2 批核对其文案，避免把「全局段告警」显示成「审计失败」。
