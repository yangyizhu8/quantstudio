# 设计方案：QFQ 重锚「分钟表结构性为空」致水位冻结（v1.1，含 A9 探针结论与增量方案）

- 立件：2026-10-24（客户运维线 N3）
- 状态：**②审通过（三裁）**；**A9 探针已证伪** ⇒ 按裁②进入增量（扩 apply 侧），增量仍走审计
- 客户来源：firefly 端——仅启用日线任务、不下载分钟线 ⇒ 60 单元全 skipped ⇒ `committed=0` ⇒ 水位永不提交

## 1 背景与动机

见 §2 归因。影响：**合法配置**（任务按表 granular 启停）下 QFQ 水位永久 `held` ⇒ 下游每轮幂等重拉、重锚永不收敛；客户无法在「不下载分钟」前提下推进水位。

## 2 归因（六环，均带代码锚点）

| 环 | 锚点 | 事实 |
|---|---|---|
| ① | `qfq_resident_orchestrator.py:69-72` | `ASSET_PRICE_TABLES = {"STOCK": ("stock_daily","stock_minutes"), "ETF": ("etf_daily","etf_minutes")}` |
| ② | `:587-597` `_security_range` | 分钟表空 ⇒ `(0, 0)`，**无空表校验**（把「结构性无数据」与「区间非法」混同） |
| ③ | `qfq_fresh_capture.py:430-433` | `_ms_to_yyyymmdd(0)` **不抛**（`pd.Timestamp(0)`=1970-01-01）⇒ `"19700101"` |
| ④ | `mcp_adapter.py:851-854` | `if s.year < 1990 or e.year < 1990 or s > e: raise ValueError("export 窗口非法（epoch/逆序）…")` ← **真抛点**（在 fetcher 内） |
| ⑤ | `qfq_resident_orchestrator.py:681-685` | `except ValueError` ⇒ `_skip_security(..., "invalid_range_ms", ...)` |
| ⑥ | `:620-636` `_skip_security` | trigger **保持 pending**、不写 `last_event_id`、不计失败 ⇒ 分钟表永久空 ⇒ **永久重试** |

### 缺陷
- **D-Q1**（根因）：`_security_range` 语义错位——空表 ≠ 非法区间。
- **D-Q2**（放大）：`FreshCapture.capture`（`qfq_fresh_capture.py:439-442`）**无条件双表采集**，无「只按日线」退路。
- **D-Q3**（死锁）：`_skip_security` 的「暂时性重试」语义用于**永久性条件** ⇒ pending 永不清、水位永不动。

### 排除的误归因（关键）
`_skip_security`（`:636`）**不写** `qfq_pending_backfill` ⇒ 本缺陷**不产生死信**；环境中 `QfqDeadLetter=12161` 与本案**无关**，不得合并立项。

### 隐性风险
- **R-Q1**：`_enqueue_pending`（`:931-948`）用 `ranges[1]`（分钟）写 `qfq_pending_backfill.range_start/end` ⇒ 分钟空时会写 `0`（1970 待补录行）。
- **R-Q2**：`_security_range` 另有调用点（`:936`；`:1301` 周边）同样拿 `(0,0)`。
- **R-Q3**：`etf_*` 对称受影响。

## 3 A9 探针结论（2026-10-24，**证伪**）

| 探查点 | 结果 |
|---|---|
| `_to_fresh_frame` 空输入 | **返回带全部列的 0 行 DF**（`qfq_fresh_capture.py:342-364`）⇒ `fresh_minute[[...]].to_csv()` 可算表头 ⇒ **`minute_sha256` 对空 DF 安全** ✓ |
| `allow_partial_minute` 语义 | 处理「**库内**分钟历史缺失（target < staged）」（`qfq_reanchor_engine.py:2140-2142`），**不覆盖 fresh 全空（staged=0）** |
| `stage_fresh_minutes` 空输入 | `:899-901` `raise ReanchorBlocked("fresh_minutes_empty")` |
| **`apply_reanchor_for_security` 空输入** | **`:2423-2424` `raise ValueError("…必须提供非空 fresh_minutes")`**，且 `:2426` 注明**在 try/BEGIN 之前**（事务外）⇒ **空分钟不可能 committed** |

⇒ **A9 证伪**：原方案（仅改 orchestrator + capture）**不足以**实现「日线-only 重锚」，必须扩到引擎侧。按裁②进入增量。

## 4 增量方案 v1.1（扩 apply 侧，仍走审计）

### 4.1 范围
| 文件 | 增量改动 |
|---|---|
| `quantstudio/pipeline/qfq_reanchor_orchestrator.py` | `_security_range` 空表返回 `(None,None)`；`_reanchor_security` 传 `allow_empty_minute=minute_range is None`；`_enqueue_pending` 分钟空写 NULL |
| `quantstudio/pipeline/qfq_fresh_capture.py` | `capture(minute_range_ms=None)` ⇒ 跳过分钟 fetch、`fresh_minute`=带列 0 行 DF、record 的 minute 字段=None |
| `quantstudio/pipeline/qfq_reanchor_engine.py` | ①`apply_reanchor_for_security(..., allow_empty_minute=False)`：`True` 时放行 `:2423` 硬检查；②`stage_fresh_authoritative` 空分钟时跳过 `stage_fresh_minutes` 并返回 `staged_minutes={}`；③`run_postchecks` 分钟四项在 `staged_minutes` 为空时跳过（以「无分钟段」显式留痕 `minute_front_coverage="absent"`）；④`fresh_staged`/`fresh_authoritative_rebase` 且 `allow_empty_minute` 且分钟非空 ⇒ **报错防误用** |
| `tests/test_qfq_empty_minute_range.py`（新建） | A1–A11 + 引擎侧 A9b/A9c |

**不改**：schema（`minute_range_*` 已 nullable）、`_skip_security` 语义、水位闸门、`allow_partial_minute` 既有语义、策略层。

### 4.2 功能点（增量后完整清单）
| FP | 判据 |
|---|---|
| FP-1 空表语义 | A1 分钟空+日线有 ⇒ `(None,None)`；A2 双空 ⇒ 双 None；**A3 负向** 双表有数据 ⇒ 与现状逐位相同 |
| FP-2 capture 可选分钟 | A4 传 None ⇒ 仅 1 次 daily fetch（fetcher 计数）；A5 record minute 字段 None；**A6 负向** 传 Tuple ⇒ 两次 fetch、字段不变 |
| **FP-3a 引擎放行（增量）** | **A9b** `allow_empty_minute=True` + 空分钟 ⇒ 不抛 `:2424`、进入 apply；**A9c** `allow_empty_minute=False` + 空分钟 ⇒ 保持抛 ValueError（负向，防沉默漂移） |
| **FP-3b 引擎无分钟段** | **A9d** 空分钟 ⇒ `stage_fresh_minutes` 未被调用（调用计数=0）；staged_minutes={}；postcheck 分钟四项跳过并留痕 `minute_front_coverage="absent"` |
| FP-3c 编排接线 | A7 复现场景 ⇒ `committed`、trigger 推进、水位可提交；A8 daily 也空 ⇒ `skipped`（原语义） |
| FP-4 backfill NULL | A10 分钟空 ⇒ `qfq_pending_backfill.range_start/end` NULL；**A11 负向** 分钟有数据 ⇒ 逐位不变 |

### 4.3 风险与规避
| 风险 | 等级 | 规避 |
|---|---|---|
| 「无分钟段」被误读为「已重锚分钟」 | 高 | 事件审计显式 `minute_front_coverage="absent"` + postcheck 留痕；**禁止**静默当作 0 行已处理 |
| `allow_empty_minute=True` 被误用于有分钟数据场景 | 高 | A9c 负向（分钟非空 + allow_empty ⇒ 报错） |
| 日线-only 重锚后分钟表将来有数据需再锚 | 中 | trigger 语义不变：分钟表一旦非空，`_security_range` 自然回双表路径 |
| `capture_id`/`metadata_sha256` 含 range 字符串，None 需确定性 | 中 | 固定 `"None|None"` 语义并纳入 A4/A6 |
| 引擎 postcheck 跳过项被漏 | 中 | A9d 断言「四项跳过 + 留痕」而非「四项静默通过」 |

### 4.4 验收
A1–A11 + A9b/A9c/A9d 全绿；既有 QFQ 全量回归（`test_daemon_qfq_integration.py` 等）全绿；四格场景矩阵（分钟空/日线有 ⇒ committed；双表有 ⇒ 逐位不变；双空 ⇒ skipped；分钟有/日线空 ⇒ skipped）；负向 A3/A6/A11/A9c 齐备。

### 4.5 回退
任一 FP 使 QFQ 既有测试转红 ⇒ 停在该点回退该点；`allow_empty_minute` 默认 False ⇒ 引擎默认行为零变化，回退只需 orchestrator 不再传 True。

## 5 客户止血通知要点（裁③·待下发）

1. **唯一不碰状态解**：启用分钟任务（哪怕跑一小段）⇒ 分钟表非空 ⇒ 单元可 committed ⇒ 水位推进；
2. **或**接受 `held`：数据不丢（下轮从旧水位**幂等重拉**），代价是重复耗时；
3. **两条禁令（显著）**：①**不得**手工改 `qfq_trigger_queue` 状态；②**不得**清 `qfq_pending_backfill`（破坏崩溃恢复语义）；
4. 说明这是**框架缺陷**（已定位、正在修），不是客户操作错误；修复将对「分钟表为空」场景支持「只按日线重锚」。

## 6 知识反哺检索留痕（2026-10-24，按 R1 新口径：主=结构化条目库，辅=通用库）

```
知识反哺：[无命中] knowledge/**（QD 族 / PD 族，按 families+domain 过滤）· 相关命中 0 条（3 条命中均为 xtdata 接口文档，与 QFQ 空分钟无关），2026-10-24
知识反哺：[命中] 案例卷宗-firefly水位线案（通用库/knowleage_base/12-客户运维/案例卷宗，2026-10-24）——同客户线（firefly）水位线案；可复用其「水位不推进」的取证路径与通知口径
知识反哺：[命中] 已知错误-因子版本漂移致rebase自锁（通用库/knowleage_base/12-客户运维/已知错误，2026-10-24）——**与本案同族**：均为「判据/闸门设计不当导致自锁——被拦下的恰是唯一能修复它的操作」；本案为空表被判非法区间⇒永久 skip⇒水位 held，该案为逐位判据⇒rebase blocked⇒committed=0。分析框架直接复用
```

**本轮沉淀（R4）**：`本轮沉淀：N/A（理由：本轮为实施轮，条目待 N5 验收后随闭环通知一并沉淀入 12-客户运维/已知错误）`

## 6b 任务变化（2026-10-24 用户裁定）

**止血通知不下发**（客户不急）——原 §5 止血草案转作**闭环通知底稿**：其中「问题原因」「修复内容」两段可直接复用，需补 **git pull 更新步骤 + 更新后验证清单**；闭环通知在 **N3 ⑤ 之后**经总调度呈用户下发。

## 7 ②审三裁记录（总调度 2026-10-24）

1. 方案通过（六环复核 + 12161 死信排除 + 三缺陷 + R-Q1~Q3）；落档 → 审计 → FP 逐点门控，**A9 探针第一步**；
2. **A9 证伪 ⇒ 当轮增量授权**（扩 apply 侧，仍走审计，不回炉重批）——**本条已启用**；
3. 客户止血通知照准出（N1 双选项 + 两条禁令显著），经总调度转用户下发。

## 8 A9 证伪后的执行决定

**暂停 FP-1/FP-2 单独落地**：若只改 orchestrator/capture 而不改引擎，空分钟会在 `:2423` 抛 `ValueError`（事务外）⇒ 行为从「skipped」变成「apply 异常」，**比现状更差**。故 FP-1→4 必须与增量 FP-3a/3b **同批**实施与验收。
