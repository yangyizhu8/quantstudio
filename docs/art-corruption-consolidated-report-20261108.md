# 【合并报告】stock_minutes ART 索引损坏 + 归因分析 + 治本方案（呈审核裁定）

> 提出：节点⑳ ｜ 日期：2026-11-08 15:0x ｜ 性质：**待审核裁定**
> 关联：`docs/art-index-corruption-rootcause-20261108.md`（`177cea0`）、`docs/evidence/obs3-once-round-terminal-20261107.md`
> 说明：本文合并两轮内容——①自愈执行与 stock_minutes 损坏的发现 ②根因归因分析，供审核会话统一裁定

---

# 第一部分：自愈执行结果与重大发现

## 1.1 once 执行结果：**拉取成功，写库失败**

按裁定甲-1 执行（停 forever → once 自愈 → 再启），once 全过程：

```
13:52:00  [qfq] 协调周期开启 cycle_id=cyc_b98193aee90e
13:52:05  INCREMENTAL: 2026-10-17 → 2026-11-08 (last_watermark=1792134000000)
13:52:47  _export_batches: start=2026-10-17 end=2026-11-08 batches=12
14:21:36  批次 12/12 完成 → 流式 export 分批=12 落盘分片=359     ← 拉取全部成功
14:31:22  [MinuteDiff] stock_minutes: validated=50000 changed=477 unchanged=49523
14:31:22  ❌ FatalException: Corrupted unique ART index "PRIMARY_stock_minutes_0":
          encountered an existing gated leaf in unique index while inserting
14:31:30  [task=mcp_stock_minutes] 所有候选源均失败: ['mcp'] → once 失败收尾
14:31:33  CHECKPOINT 完成: wal 0.0MB → 0.0MB（此时才 checkpoint，已太晚）
```

**⇒ 12 批全部拉取成功（359 分片 ≈ 1,795 万行），但写入时 ART 索引损坏 ⇒ 整轮失败。**

## 1.2 数据侧核实：**自愈零效果**

| 日期 | 执行前 | 执行后 | 变化 |
|---|---|---|---|
| 11-02 / 11-03 | 1,258,743 / 1,258,261 | 同 | 未变 |
| **11-04** | 1,204,763 行 / 5,003 只 | **同** | **未变** |
| **11-05** | 48,749 行 / 204 只 | **同** | **未变** |
| **11-06 / 11-07 / 11-08** | 0 | **0** | **仍未写** |

**⇒ 拉取全部成功、写入全部失败 ⇒ 数据一行没进。**

## 1.3 这是 `stock_minutes` 的同族第二例

| 表 | 损坏索引名 | 主键 | 状态 |
|---|---|---|---|
| `etf_daily` | `PRIMARY_etf_daily_0` | `(code, time)` | ✅ 11-07 已修（DROP+重建+导入，V1/V2/V3 通过） |
| **`stock_minutes`** | **`PRIMARY_stock_minutes_0`** | **`(code, time, freq)`** | ❌ **现在坏着** |

## 1.4 这解释整条死锁的**物理根因**

```
stock_minutes ART 索引损坏
  → 每次写 stock_minutes 必 FATAL（大表写入必触发）
  → 数据永远补不上（11-04 缺 219 只 / 11-05 只剩 204 只 / 11-06 起全缺）
  → rebase 判定「分钟覆盖不全：缺 482/964 行」→ 单元 blocked
  → gate held → 水位冻结（10-16）
  → 重拉同一窗口 → 再撞索引损坏 → 死锁自我维持
```

**⇒ 也解释了「7 天里 etf_daily 有 5 天静默、stock_minutes 却从没补上」——两张表都坏，只有 etf_daily 被修了。**

---

# 第二部分：归因分析（为什么"无缘无故"坏）

## 2.1 结论：不是无缘无故，是**三层叠加**

| 层 | 判定 | 证据 |
|---|---|---|
| **L1 引擎层** | **DuckDB 已知 ART 索引缺陷**（非我方代码 bug） | 官方 issue #23645 / #23788 / #21394 / #25693 同族 |
| **L2 使用模式** | **我方放大触发概率**（3 个具体因素） | 长跑不 checkpoint、多进程轮转、强杀 |
| **L3 检测** | **我方缺 canary**，只能等 FATAL | issue 明确可提前发现 |

## 2.2 L1：官方 issue 环境吻合度对照（教科书级复现）

| 维度 | 官方 issue [#23645](https://github.com/duckdb/duckdb/issues/23645) | 我方实际 | 吻合 |
|---|---|---|---|
| OS | Windows 11 | Windows | ✅ |
| DuckDB | 1.5.4（pip） | **1.5.6**（pip） | ✅ 同大版本线 |
| 存储 | file-backed DB | `quantstudio.db`（62GB） | ✅ |
| 并发 | **单进程 / 单连接 / 全串行** | daemon 或 once 单进程 | ✅ **连"不是并发问题"都吻合** |
| workload | **周期性 upsert，大部分行 unchanged** | `changed=477/50000`（**99% unchanged**） | ✅ |
| 损坏对象 | **可以是 PRIMARY KEY 的 ART 索引本身** | `PRIMARY_etf_daily_0` / `PRIMARY_stock_minutes_0` | ✅ |
| 数据状态 | **数据完好，仅索引坏** | dump 回读 222 万行完好 | ✅ |

**关键 issue 原文**：

> **[#23645](https://github.com/duckdb/duckdb/issues/23645)**：「the corrupt index can be the **PRIMARY KEY's ART index itself** … single long-lived process, single connection, all access serialized … **Three occurrences across two unrelated databases in 10 days** … **Detectable before the fatal**: indexed/filtered queries return short counts while full scans are correct … data fully intact, `DROP INDEX` + `CREATE INDEX` clears it」

> **[#23788](https://github.com/duckdb/duckdb/issues/23788)**：「Secondary ART index serialized corrupt when **a second in-process engine checkpoints a pending WAL**」— **regression since 1.4.0**

> **[#21394](https://github.com/duckdb/duckdb/issues/21394)**：DELETE after UPDATE 触发「"Failed to delete all rows from index" … 或 "**Corrupted...**"」

## 2.3 版本修复状态（铁律 16 文档查证）

| 版本 | 官方说明 | 对我方意义 |
|---|---|---|
| **1.5.1**（2026-03-23） | 「ships **two fixes for ART indexes** … we recommend updating to v1.5.1 as soon as possible」 | ART 问题长期存在 |
| **1.5.6**（2026-09-28，**我方在用**） | Fixes → Generic Bugfixes：**「#25693 – Fix dead node counting in ART indexes」** | 修了 dead node 计数，但**未覆盖全族** |
| 现状 | 我方 1.5.6 **仍复现** | **⇒ 当前 1.5.x 线无完整修复** |

## 2.4 L2：我方三个放大因素（这是能改的部分）

| # | 因素 | 证据 | 与 issue 的对应 |
|---|---|---|---|
| **1** | **长跑期间从不 checkpoint** | `checkpoint_database` **唯一调用点在 `ResidentCollector.close()`（进程退出时）**，且 `logger.debug`；forever 跑数小时~数天 | **直击 #23788**；历史曾积累 **1,027 MB** WAL 备份（`quantstudio.db.wal.bak.20261026_115507`） |
| **2** | **多进程轮转打开** | 11-07 损坏前：`[write-lock] **回收残留写锁（持有者进程已不存在）**` | 正是 #23788 的**触发前置**（残留锁 = 曾有 pending WAL） |
| **3** | **强杀** | 多次 `Stop-Process -Force` | 跳过 `close()` ⇒ 跳过 checkpoint ⇒ 留 pending WAL |

**⇒ 一句话：DuckDB 的 bug 需要「pending WAL + 另一进程 checkpoint」这个条件，而我们的 forever 长跑 + 多进程轮转 + 强杀，每次都在制造这个条件。**

## 2.5 L3：检测缺失（为什么感觉"突然"）

issue 明确指出损坏有「corrupt-but-not-yet-fatal」早期窗口：
> filtered 查询返回短计数，而 full scan 正确（例：`COUNT(*) WHERE stage=1` 返回 51 vs `GROUP BY stage` 的 84）
> **A filtered-vs-full-scan parity check makes a reliable canary.**

**⇒ 我方没有任何此类探针 ⇒ 只能等 FATAL。**

---

# 第三部分：修复路径（三方案全部验证不可用）

| 方案 | 实测结果 |
|---|---|
| `REINDEX` | ❌ `ParserException`（DuckDB 1.5.6 无此语句，11-07 已验证） |
| `ALTER TABLE DROP CONSTRAINT` | ❌ `NotImplementedException: No support for that ALTER TABLE option yet!` |
| `ALTER TABLE ADD PRIMARY KEY` | ❌ `CatalogException: table can have only one primary key` |

**⇒ 只能 DROP + 重建 + 导入（与 `etf_daily` 同法）。**

---

# 第四部分：治本方案（按优先级）

| 优先级 | 编码 | 动作 | 工作量 | 直击哪个环节 |
|---|---|---|---|---|
| **P0** | **T2.1** | **主循环每 30 分钟 CHECKPOINT**（复用已有 `checkpoint_database`，只加调用点） | **~20 行** | 消除 #23788 的 pending WAL 条件 |
| **P0** | **T2.3** | **任何进程打开库后先 checkpoint** | **~5 行/入口** | 收干净上次遗留 |
| **P1** | **T3.1** | **filtered-vs-full-scan canary**（对关键表定期对拍计数） | ~50 行 | 把"突然发现"变"提前发现" |
| **P1** | **T2.2** | **禁强杀**（信号处理走 `close()` 路径） | 中 | 消除人为制造 pending WAL |
| P2 | T1.1 | 跟踪 1.5.7/2.0 修复后评估升级 | 低 | 等官方根治 |
| P2 | T3.3 | 重建 SOP 固化 runbook | 低 | `etf_daily` 已跑通 |

## 根治边界（必须说清）

| 手段 | 能否根治 |
|---|---|
| 升级 DuckDB | ⚠️ **1.5.6 已是最新且仍复现** ⇒ 当前版本线无解 |
| 单独重建索引 | ❌ **三条路全断** ⇒ PK 损坏只能重建整表 |
| T2.1 + T2.3 + T2.2 | ✅ **大幅降低触发概率** |
| T3.1 canary | ✅ **提前发现** |

**⇒ 治本的现实形态是「预防 + 早发现 + 快速恢复 SOP」，而不是"消灭 bug"。**

---

# 第五部分：代价评估（不可逆生产变更）

| 项 | 数值 |
|---|---|
| 表行数 | **251,877,433**（2.5 亿，是 `etf_daily` 的 **113 倍**） |
| 预计耗时 | **30–60 分钟**（导出 parquet ≈3-5GB + DROP + 重建 + 导入） |
| 磁盘 | 当前可用 **63GB**，dump 约 3-5GB ⇒ 够 |
| 服务影响 | 期间 daemon/forever 不可跑；数据只读可用 |
| 回退 | 全库备份（11-07 那份 62GB）+ parquet dump 双保险 |

---

# 第六部分：呈审核裁定

## 6.1 两件事需要裁定

| # | 事项 | 选项 |
|---|---|---|
| **A** | **是否重建 `stock_minutes`**（不可逆生产变更） | 甲：立即重建（30-60 分钟）／乙：不重建（分钟数据永久停摆）／丙：先 dump 后重建 |
| **B** | **治本方案先做哪个** | **甲（推荐）：先 T2.1+T2.3 堵漏，再重建** ／ 乙：先重建再治本 ／ 丙：并行 |

## 6.2 我方建议：**先堵漏（T2.1+T2.3），再重建**

**理由**：
1. T2.1/T2.3 是**纯增量、低风险**（各 20/5 行，复用已有函数）
2. **不先堵住触发条件，重建完 2.5 亿行，下一个 forever 夜跑又坏了 = 白做**
3. 这也是归因分析的最大价值：**把"修数据"变成"先修机制，再修数据"**

## 6.3 若同时批准 A（甲）+ B（甲），执行顺序

```
1. T2.1 + T2.3 编码 + 单测（~1 小时）
2. 重启验证：checkpoint 按预期触发（看日志）
3. stock_minutes 重建：备份确认 → dump → V1/V2 基线 → DROP → 重建 → 导入 → V1/V2/V3
4. 再跑 once 自愈 → 验证 11-04/05/06 补齐
5. 验水位是否推进 / reanchor 是否通过
```

---

## 附：本轮已完成的代码工作（供参考）

| 项 | commit | 状态 |
|---|---|---|
| 1.5 动态下限 + 1.4 逐日对账（纯函数 + 27 测） | `b14e124` | ✅ |
| 对账脚本 + 04:30 runbook | `c891cea` | ✅ |
| 1.6 挂账+下轮重拉（纯函数 + 23 测） | `c166912` | ✅ |
| 1.6 钉子「已收盘语义」（+17 测） | `aa62689` | ✅ |
| 缺口账本 aux 侧（SQLite + 12 测） | `aa62689` | ✅ |
| P0-D 三分类清单 | `aa62689` | ✅ |
| 回填脚本复权两处缺陷修复 | `4ade651` | ✅ |
| **ART 索引归因分析** | **`177cea0`** | ✅ |
| **合计单测** | — | **79 passed** |
