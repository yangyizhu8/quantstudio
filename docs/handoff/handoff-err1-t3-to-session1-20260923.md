# 移交件：错误一 T3（`revision_alert` outbox）→ 会话 1

- **移交方**：客户运维会话 2（新客户四问题案）
- **接收方**：会话 1（错误一案主）
- **日期**：2026-09-23
- **性质**：**成果移交，非问题上报**。本会话不实施。
- **移交原因**：案主归属 + 防两线重复实施（用户 2026-09-23 裁定；此前「暂缓解除」指令未显式指明主体，责任在指令侧）
- **移交内容**：勘察结论作为会话 1 的错误一 T3 **方案输入**（本会话已完成勘察，未动代码）

---

## 一、解除条件已满足（本会话负责的前置已清）

`docs/handoff/customer-ops-ledger.md` S1 原定解除条件：「客户运维 2 的新客户四问题批落地后即恢复实施」。

| 前置 | 状态 | 证据 |
|---|---|---|
| 新客户四问题批落地 | ✅ | 10 笔全在 `origin/main`（`000b5bb`），含 T1 `478ed0e` / T2 `6ceeff9` / T4 `151e984`+`0da4d79` / 门禁 `c62c1cc` / 文档 `41d70bb` |
| 共享文件 `mcp_adapter.py` 不再被本会话占用 | ✅ | 本会话对该文件无在途未提交改动（`git status` 为空） |
| 双仓库一致 | ✅ | `origin/main = local HEAD = 000b5bb`；push URL 覆盖 quantstudio-plus + quantstudio |

**结论**：会话 1 可**直接开工**，无需等待本会话任何后续动作。

---

## 二、勘察结论（可直接作为方案输入）

### 2.1 落点

`quantstudio/pipeline/sources/mcp_adapter.py:2140-2206` —— `_inject_adjfactor(df, freq, table, conn=None)`

```python
# 现状（无任何变更检测）：
conn.executemany(
    f"INSERT OR REPLACE INTO {target} (code, time, adj_factor) VALUES (?, ?, ?)", rows)
# target = "fund_adj"(ETF) | "adj_factor"(STOCK)
```

- `rows` 来自 `normalize_mcp_adj_factor_df(df, freq, asset_type)`，元素为 `(裸码, UTC ms, factor)`；
- 分钟表另有「按 (code, 自然日) 去重保留首 bar」前置（`freq` 含 `"minutes"` 时）；
- **历史值被静默覆盖** ⇒ 9-15 类锚演进事件零留痕（本项要解决的核心）。

### 2.2 检测规则（错误一方案件已定，勘察确认可落地）

> 方案件 §三 T3 原文：注入路径检测 `(code, 新值 ≠ 旧值 @ max_time)` → revision_alert outbox；
> orchestration `_discover` 消费（打通既有 `consume_revision_alerts`）；**9-15 类事件今后必留痕**。
> **双空表由 0→非 0 作为验收硬指标**。

**关键实现提示（勘察所得，供方案设计参考）**：

- 现写入是 `INSERT OR REPLACE`，**覆盖前须先读旧值**才能判「新值 ≠ 旧值」。建议改为两步：
  先 `SELECT code, time, adj_factor FROM {target} WHERE (code,time) IN (...)` 取旧值映射，
  再比对 + `executemany` 写入 + 同事务写 alert（避免「先写后比」导致旧值不可得）。
- 「@ max_time」语义需明确：指**该 code 在目标表中的最大 time**（锚点）：若本批新值落在
  新的更大 time 上，则与「上一锚（旧 max_time 行）」比较；若落在已有 time 上则与同 time 旧值比较。
  两种情形的 `factor_time` 取值需在方案中写死（建议：alert 的 `factor_time` = **本次观测到的锚 time**）。
- 分钟表去重在前，故比对口径应是**去重后**的 `(code, time)` 集合。

### 2.3 outbox 表与消费链（已就绪，无需新建）

| 要素 | 位置 | 状态 |
|---|---|---|
| 表 DDL | `qfq_reanchor_schema.py:537` `qfq_factor_revision_alert`（与 observation 同 SQLite 事务） | ✅ 已存在 |
| 写入参考实现 | `qfq_observation.py:366-388`（`INSERT` 新 revision 行 + 同事务 `INSERT OR IGNORE` alert） | ✅ 可复用形态 |
| `alert_id` 生成 | `qfq_observation.alert_id_of(asset_type, code, factor_time, revision_no, source_generation)` | ✅ 已存在 |
| 消费侧 | `qfq_event_discovery.consume_revision_alerts`（`:417`）+ `qfq_resident_orchestrator:367` 调用 | ✅ 已就绪 |
| 待写侧辅助 | `list_pending_alerts` / `acknowledge_alert`（`qfq_observation.py:408/427`） | ✅ 已存在 |

### 2.4 方案件给定的约束（须在实施中保持）

| 约束 | 出处 |
|---|---|
| 注入写路径语义不变（不改 upsert 结果集） | 方案 §二 表 |
| **告警失败不连带主路径**（fail-soft） | 方案 §二 + R6 |
| 仅 `(code, 新值≠旧值)` 时触发（月均每 code 0-2 次），SQLite 行级 upsert，开销可忽略 | 方案 R6 |
| revision_no 语义：与 `qfq_factor_observation` 的 `revision_no` 递增口径需一致（否则消费侧对不上） | 勘察发现（**方案中需显式定义**） |

### 2.5 与本会话在编域的交叉核对（台账 §一 附注要求）

台账要求：「若实施涉及云端 `etf_minutes` close 口径域（会话 2 已登记 tech-debt），一并核」。

**核对结论：不重叠，无需一并处置。**

- 本项（T3）涉及**因子快照锚演进的事件留痕**（`adj_factor`/`fund_adj` 值变化）；
- 云端 `etf_minutes` close 口径疑点（`docs/pipeline-tech-debt.md` REGISTERED）涉及
  **行情 close 与 amount/vol 的基准自洽性**及还原链乘数；
- 两者共享 `qfq_aux.db` 但**数据面与语义面均不相交**；
- 唯一间接关联（供参考）：本轮已实证「部分码的历史 `adj_factor` 与 close 口径不一致
  （约 3%~5% 行，2026-01 前部分码 `amount ≈ 9×close×vol`）」——若 T3 的锚演进告警
  **恰好捕到这些码的因子被刷新事件**，可作为该 tech-debt 条的**旁证线索**，但**不构成本项前置**。

---

## 三、本会话未做的事情（明确边界）

- ❌ 未修改 `mcp_adapter.py` 或任何代码（本项仅勘察）
- ❌ 未创建/修改 `qfq_factor_revision_alert` 表或写任何 alert
- ❌ 未出方案件、未审计、未实施（按移交令，本会话不实施）

---

## 四、接收方下一步（建议）

按六步流水线正常推进：**方案（六模块）→ 审计 → 实施 → 验收 → 用户确认 → 双仓库推送**。

方案中建议至少解决勘察标注的 4 个设计点：
1. 覆盖前读旧值的事务与批量策略（避免二次查询放大热路径开销）；
2. `factor_time` 与「@ max_time」两种情形的精确定义；
3. `revision_no` 与 `qfq_factor_observation` 的口径一致；
4. 写入幂等（`INSERT OR IGNORE` + `alert_id` 决定）与 fail-soft 边界。

验收硬指标（方案件已定）：**双空表由 0 → 非 0**（`qfq_factor_observation` /
`qfq_factor_revision_alert`），且 9-06 窗口重放对照表不回归（方案 §五-1）。

---

## 五、纪律提醒（共享核心文件）

`mcp_adapter.py` 属共享核心文件，按项目铁律：
① 每次 `edit` 后**即时 `git diff` 自检**（防并行会话覆盖）；
② 精确文件清单提交（禁 `git add -A`）；
③ 实施前建零副作用回退点（`git stash create -u` + `git stash store`）。