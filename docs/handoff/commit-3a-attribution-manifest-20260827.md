# 3A 写锁收口独立 commit——归属切分清单（Step 1 方案）

> 产出：ZCode 稳定化线，2026-08-27（总调度批准切分指令）
> 目的：3A 工作包（2026-08-17 实施滞留工作树）独立入库推送，剥离他线混入（hunk 级纪律，同 P-D14b 先例）

## 一、3A 本体——整文件纳入（16 文件 + 3 证据 + 2 测试）

| 文件 | 状态 | 判定依据（diff 实测） |
|---|---|---|
| `quantstudio/pipeline/snapshot_lock.py` | untracked 新增 | 3A 核心模块（O_CREAT\|O_EXCL 原子锁/WriteLockHeld/CLI 包裹） |
| `tests/test_snapshot_lock.py` | untracked 新增 | 锁单测 7/7（DSH 终审实跑） |
| `tests/test_3a_equivalence.py` | untracked 新增 | 等价性验收 3/3（writers/events/子进程拒绝，隔离临时库） |
| `qfq_aux_router.py` | M | connect(read_only=False) raise + connect_locked 硬约束（3A 终审硬约束 1） |
| `qfq_observation.py` | M | __connect 私有化 + _connect_locked 上下文（硬约束 2） |
| `qfq_maintenance.py` | M | 7/7 新增行全为锁接入 |
| `qfq_formal_cutover.py` | M | 20/24 锁（4 连接点 locked_connect 与 dual_locks 共存，中期审计 A 项） |
| `qfq_formal_cutover_cli.py` | M | 7/12 锁 CLI 接入 |
| `qfq_formal_canary.py` | M | 8/9（STAGING 保留锁定守卫，决策点 2） |
| `qfq_calendar.py` | M | 6/11（qfq_calendar:163 含幂等 DDL 连接持锁） |
| `qfq_event_discovery.py` | M | 3/4 |
| `qfq_formal_postcutover_audit.py` | M | 3/4 |
| `qfq_reanchor_schema.py` | M | 3/4（aux_path 归类后接锁） |
| `qfq_revision.py` | M | 7/9 |
| `qfq_schema_migration.py` | M | 7/12 |
| `qfq_orchestrator_cli.py` | M | 8/13 |
| `qfq_resident_orchestrator.py` | M | 9/13（resident:936 fallback 直连持锁） |
| `daemon.py` | M | 5/7（daemon 写连接接锁；余 2 行为锁上下文配套） |
| **证据（untracked）**：`data/snapshots/lock_adoption_log.json`、`connection_semantics.json`、`write_path_registry.json`、`read_exemption_evidence.json` | | registry 34/34 全绿的机器证据 |

## 二、混入文件——hunk 级剥离（1 文件）

| 文件 | 3A hunk | 他线 hunk | 处置 |
|---|---|---|---|
| `sources/mcp_adapter.py` | **4 锁位**（勘误：初版漏列 1982，registry 实含）：import locked_connect；`mcp:1217` aux 写连接锁（enter/exit）；`mcp:1902` inject_dividend ensure/release；`mcp:1982` aux 写连接锁（enter/exit） | ~60 行 = 管线 v2 数据源映射（etf_dividend / stock_float_share / stock_dividend_full 源切换，他线） | **方案甲（总调度已裁定）**：定向 patch `docs/handoff/patches-3a/mcp_adapter_3a_only.patch`（7 diff hunk=4 锁位，已对 HEAD 副本实测 apply OK + 3A 标记 7 处/v2 内容 0 处），他线 60 行留工作树——commit 后 registry 34/34 与已提交代码自洽闭环；**方案乙**：整文件排除、3A hunk 随管线 v2 线后推（缺点：已提交 registry 含未提交锁代码，34/34 闭合性破口，需登记） |

## 三、非 3A——明确排除（他线，不碰）

| 文件 | 归属 |
|---|---|
| `qfq_invariant.py`（19 行，0 锁） | 性能修复 v6.7.53 向量化（他线性能包） |
| `config_lint.py` / `source_capabilities.py`（各 1 行） | 他线 |
| `backtest_engine.py` / `events.py` / `ptrade_api.py` / `run_ptrade_strategy.py` / `source_import.py` | 撮合实证线/转换线（slippage 9 hunk 另行确认，总调度已解除其 3A 归属） |
| `tests/test_qfq_reanchor_batch1.py` | 批 1 重锚线（9 锁相关行为该线配套，随其归属走） |
| `gui/*`、`workers.py`、`test_agent_*` 等 | GUI/skill 线 |

## 四、DSH 2026-08-17 终审证据引用（第 2 步审计依据）

`docs/governance-step3-audit.md`：
- "3A 写锁收口终审（全覆盖无旁路）→ 3B 批准"节：registry MAIN 13 + AUX 21 = 34 全 locked；锁单测 7/7；test_3a_equivalence 3/3（隔离临时库零生产写）；豁免 12 点移入排除 + 证据在案；
- "pending 拆分审计"节：34/34 全绿 + aux_router/observation 硬约束两项修复落实（与 §一 前两文件 diff 精确对应）；
- 事故审计节：AGENTS.md 写前快照铁律来源（回退点 a9b2ce7b 留存）。

## 五、执行序（Step 2 审过后）

1. 暂存核对闸：按本清单 staging → `git diff --cached --stat` 逐文件比对（零清单外文件）；
2. 锁测试复跑（test_snapshot_lock + test_3a_equivalence + guard 回归）全绿留证；
3. 独立 commit（message：3A 写锁收口 34/34 + 终审证据引用）；
4. **用户确认（Step 5）→ 双仓推送（Step 6）→ 两远程 HEAD 核对**。
