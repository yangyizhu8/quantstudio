# 【归因分析】DuckDB ART 索引反复损坏的根因与治本方案

> 提出：节点⑳ ｜ 日期：2026-11-08 ｜ 触发：用户问「为什么会无缘无故索引坏？如何治本？」
> 两次实证：`etf_daily` 2026-11-07 06:03:11（`PRIMARY_etf_daily_0`）、`stock_minutes` 2026-11-08 14:31:22（`PRIMARY_stock_minutes_0`）
> 结论：**不是"无缘无故"——是 DuckDB 已知引擎缺陷 + 我方使用模式放大 + 检测缺失，三层叠加**

---

## 一、结论先行

| 层 | 判定 | 证据 |
|---|---|---|
| **L1 引擎层** | **DuckDB 已知 ART 索引缺陷**（非我方代码 bug） | 官方 issue #23645 / #23788 / #21394 / #25693 同族；环境吻合度极高 |
| **L2 使用模式** | **我方放大触发概率**（3 个具体因素） | 长跑不 checkpoint、多进程轮转、异常退出留 pending WAL |
| **L3 检测** | **我方缺 canary**，只能等 FATAL | issue 明确指出"corrupt-but-not-yet-fatal"可通过计数差异提前发现 |

**⇒ "无缘无故"是错觉：它有明确的触发条件，只是触发点落在 DuckDB 内部、不报错、延迟暴露。**

---

## 二、L1：引擎层证据（官方 issue 逐条对应）

### 2.1 环境吻合度对照

| 维度 | 官方 issue 报告 | 我方实际 | 吻合 |
|---|---|---|---|
| OS | Windows 11 | Windows | ✅ |
| DuckDB | 1.5.4（pip） | **1.5.6**（pip） | ✅ 同大版本线 |
| 存储 | file-backed DB | `quantstudio.db`（62GB 文件） | ✅ |
| 并发 | **单进程 / 单连接 / 全串行** | daemon 或 once 单进程 | ✅ **关键：不是并发问题** |
| workload | **周期性 upsert，大部分行 unchanged** | `MinuteDiff` changed=477/50000（99% unchanged） | ✅ **高度吻合** |
| 损坏对象 | **可以是 PRIMARY KEY 的 ART 索引本身** | `PRIMARY_etf_daily_0` / `PRIMARY_stock_minutes_0` | ✅ **精确吻合** |
| 数据状态 | **数据完好，仅索引坏** | 修复前 dump 回读 222 万行完好 | ✅ |

**⇒ 这不是偶发，而是 issue #23645 描述场景的**教科书级复现**。**

### 2.2 关键 issue 摘录

**[#23645](https://github.com/duckdb/duckdb/issues/23645)**（april0326，2026-07-18）：
> 「**the corrupt index can be the PRIMARY KEY's ART index itself**, not only explicit non-unique indexes —
> reached via plain `UPDATE` with no `ON CONFLICT` involved」
> 「Windows 11, duckdb 1.5.4, file-backed DB, **single long-lived process, single connection, all access serialized**」
> 「**Three occurrences across two unrelated databases in 10 days**」
> 「**Detectable before the fatal**: in the corrupt-but-not-yet-fatal state, **indexed/filtered queries return
> short counts while full scans are correct** — e.g. `SELECT COUNT(*) WHERE stage = 1` returned 51 vs 84 from
> `GROUP BY stage`. **A filtered-vs-full-scan parity check makes a reliable canary.**」
> 「data fully intact, **`DROP INDEX` + `CREATE INDEX` clears it**」

**[#23788](https://github.com/duckdb/duckdb/issues/23788)**：
> 「Secondary ART index serialized corrupt when **a second in-process engine checkpoints a pending WAL**」
> 「**regression since 1.4.0**」

**[#21394](https://github.com/duckdb/duckdb/issues/21394)**：
> 「Internal errors triggered by DELETE after UPDATE — "Failed to delete all rows from index",
> "UNIQUE constraint violation", or "**Corrupted...**"」

### 2.3 版本修复状态（铁律 16 文档查证）

| 版本 | 官方说明 | 对我方意义 |
|---|---|---|
| **1.5.1**（2026-03-23） | 「This release ships **two fixes for ART indexes**. If you are using indexes in your workload … we recommend updating to v1.5.1」 | ART 问题**长期存在**，1.5.1 修过一轮 |
| **1.5.6**（2026-09-28，我方在用） | Fixes → Generic Bugfixes：**「#25693 – Fix dead node counting in ART indexes」** | **修了 dead node 计数**，但**未覆盖 #23645/#23788 全族** |
| 现状 | 我方 1.5.6 **仍发生损坏** | **⇒ 1.5.6 不是该缺陷的完整修复** |

**⇒ 诚实结论：在当前版本线（1.5.x）内，此缺陷无法通过升级彻底消除（1.5.6 已是最新且仍复现）。**

---

## 三、L2：我方使用模式的三个放大因素

### 因素 1：**长跑进程不 checkpoint**（最可疑）

| 代码事实 | 影响 |
|---|---|
| `db_checkpoint.py` 提供 `checkpoint_if_idle()` | 能力具备 |
| **但唯一调用点是 `ResidentCollector.close()`**（`daemon.py:327`） | **⇒ 只有进程退出时才 checkpoint** |
| `daemon --mode forever` 常驻数小时~数天 | **⇒ WAL 持续增长、索引状态长期只存在于 WAL/内存** |
| 历史实证：曾有 **1,027 MB 的 WAL 备份**（`quantstudio.db.wal.bak.20261026_115507`） | **⇒ WAL 确实曾积累到 GB 级** |

**⇒ 机理**：`#23788` 说「pending WAL 被另一个引擎 checkpoint ⇒ 索引序列化损坏」。
**我方 forever 跑几小时不 checkpoint ⇒ WAL 持续 pending ⇒ 任何一次进程重启（daemon 退出后 once/GUI 打开）都会触发该路径。**

### 因素 2：**多进程轮转打开同一库**

| 实证 | 读数 |
|---|---|
| 11-07 损坏前 | `WARNING [write-lock] **回收残留写锁（持有者进程已不存在）**` |
| 说明 | **此前有进程异常退出**，留下 stale 锁 + 可能的 pending WAL |
| 日常工作模式 | daemon forever + GUI + once 交替打开（同一 `quantstudio.db`） |
| `#23788` 场景 | 「second in-process engine checkpoints a pending WAL」⇒ **正好是我方模式** |

**⇒ 「残留写锁被回收」= pending WAL 存在的强信号，而这正是 #23788 的触发前置。**

### 因素 3：**异常退出（强杀 / invalidated 自退）**

| 实证 | 读数 |
|---|---|
| 11-07 19:44:21 | `收到停止请求（reason=invalidated-zombie），准备优雅退出` |
| 多次 | 我方多次 `Stop-Process -Force`（强杀 forever / once） |
| 强杀的后果 | **跳过 close() ⇒ 跳过 checkpoint ⇒ 留 pending WAL** |

**⇒ 每次强杀都在为下一次索引损坏积累条件。**

---

## 四、L3：检测缺失（为什么是"突然"发现）

**issue 原话**：损坏有「corrupt-but-not-yet-fatal」阶段，表现为
> filtered 查询返回短计数，而 full scan 正确

**⇒ 这意味着索引损坏后有**一个可观测的早期窗口**，但我方没有探针 ⇒ 只能等 FATAL。**

**我方现状**：
- `QualityAudit` 有 `QfqDeadLetter` 等业务检查
- **无「索引一致性」检查**（没有 filtered vs full-scan 对拍）

---

## 五、治本方案（三层对应，缺一不可）

### T1 引擎层：**接受风险 + 降低触发面**（无法根治，故必须配 T2/T3）

| 动作 | 说明 | 优先级 |
|---|---|---|
| **T1.1 版本跟踪** | 持续跟踪 #23645/#23788 修复状态；一旦官方发布含修复的版本（1.5.7+ 或 2.0），**在验证后升级并放宽 policy 放行集** | P2 |
| **T1.2 评估「去 PK 索引」** | 若业务允许，把 `PRIMARY KEY` 降级为 `UNIQUE` 之外的普通列 + 应用层幂等（代价大，仅备选） | P3（不推荐） |

### T2 使用模式：**三个必须改的动作**（这是最有效的治本）

| 动作 | 具体做法 | 预期收益 |
|---|---|---|
| **T2.1 周期性 CHECKPOINT**（**最高优先**） | 在 daemon 主循环里**每 N 分钟**（建议 30 分钟）或**每完成 K 个任务**后调用 `checkpoint_if_idle()`（已有函数，只需加调用点） | **直接消除 #23788 的 pending WAL 条件** |
| **T2.2 强制串行 + 优雅退出** | ①同一时刻只允许一个写进程（已有 write_lock，需确认覆盖 once/GUI）②**禁止强杀**，改为发送停止请求走 `close()` 路径 | 消除「异常退出留 pending WAL」 |
| **T2.3 启动时先 checkpoint** | 任何进程（daemon/once/GUI）打开库后**先执行一次 `checkpoint_if_idle()`** 再开始业务 | 把上一次遗留的 pending WAL 先收干净，再让新进程工作 |

**⇒ T2.1 + T2.3 两条改动量都很小（各几行），但直击 #23788 的触发条件。**

### T3 检测：**canary + 自动恢复**（把"突然发现"变成"提前发现"）

| 动作 | 具体做法 |
|---|---|
| **T3.1 索引一致性 canary**（**建议立即加**） | 对关键表（`etf_daily` / `stock_minutes` / `stock_daily` / `etf_minutes`）定期执行：<br>`full = COUNT(* WHERE <PK 某列> IS NOT NULL)` vs `agg = SUM(COUNT(*)) GROUP BY <PK 列>`<br>**两者不等 ⇒ 索引已坏（但未 fatal）⇒ 告警** |
| **T3.2 自动重建（限 explicit 索引）** | issue 提到「DROP INDEX + CREATE INDEX clears it」——**但 PK 索引无法 DROP**（已验证 `ALTER TABLE DROP CONSTRAINT` 不支持）⇒ **PK 损坏只能重建表**，故 T3.2 仅对可 DROP 的显式索引有效 |
| **T3.3 损坏后快速恢复 SOP** | 写成 runbook：dump → DROP → 重建 → 导入 → V1/V2/V3（`etf_daily` 已跑通，`stock_minutes` 待跑） |

---

## 六、优先级与工作量

| 优先级 | 项 | 工作量 | 为什么 |
|---|---|---|---|
| **P0** | **T2.1 周期性 CHECKPOINT**（主循环每 30 分钟） | **~20 行** | 直击 #23788，改动最小、收益最大 |
| **P0** | **T2.3 启动时先 checkpoint** | **~5 行/入口** | 收干净上次遗留 |
| **P1** | **T3.1 索引一致性 canary** | ~50 行 + 定时 | 把延迟暴露变提前发现 |
| **P1** | **T2.2 禁强杀 + 优雅退出**（改运维习惯 + 加信号处理） | 中 | 消除人为制造 pending WAL |
| **P2** | T1.1 版本跟踪（1.5.7/2.0 发布后评估升级） | 低 | 等官方根治 |
| **P2** | T3.3 重建 SOP 固化为 runbook | 低 | 已跑通一次（etf_daily） |

---

## 七、一句话回答用户

> **不是无缘无故**：DuckDB 1.5.x 存在已知的 ART 索引持久化缺陷（官方 issue #23645/#23788/#21394），
> 触发条件是「**大量 upsert + 大部分行 unchanged + file-backed + 单进程串行**」——与我方 workload 精确吻合；
> 而**我方"长跑不 checkpoint + 多进程轮转 + 强杀"把触发概率放大了**。
>
> **治本**：①主循环每 30 分钟 CHECKPOINT（消除 pending WAL 条件）②进程启动先 checkpoint ③加 filtered-vs-full-scan canary 提前发现 ④禁止强杀。
> **根治**需等 DuckDB 官方（1.5.6 已含 #25693 修复但未覆盖全族，当前版本线无解）。

---

## 八、R4 沉淀

- `本轮沉淀：N/A`（理由：归因分析件；待 T2.1/T3.1 落地并观察后，按 db 族入册「DuckDB ART 索引缺陷」条目）
