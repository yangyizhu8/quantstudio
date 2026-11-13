# D20 · QuestDB WAL 持久化与可见性契约：最小证据（P-2 实测 + P-3 文档取证）

> 节点：⑳/㉑ 复权管线修复专属会话 · 复权重锚「写时锚 sidecar」平台能力取证
> 日期：2026-11-13 10:2x
> R26 指令：**D20 继续文档与隔离探针取证；不能整体视为「无权限所以不能推进」**；按「**执行机／端点／数据根**」区分；**生产 DDL／强杀／掉电实验不执行**。
> 性质：**纯只读取证 + 隔离实例实验**。生产实例 `D:\questdb\quest_database` 未写、未 DDL、未强杀、未掉电实验。

---

## 0. 结论速览（三条，全部实测/有出处）

| # | 结论 | 等级 | 依据 |
|---|---|---|---|
| C1 | **`COMMIT` 返回成功 ≠ 数据可读**。隔离实例实测：目标表 `sequencerTxn=1`（已 commit 进 WAL）、`writerTxn=0`、`SELECT count()` 返回 **0 行**——commit 已确认但读侧一行不可见 | **实测（隔离实例）** | 本件 §2 expB |
| C2 | **`COMMIT` 成功 ≠ 掉电持久**。`cairo.commit.mode` 默认 `nosync`＝"no explicit flush (relies on OS page cache)"；仅 `sync` 才 fsync 并可耐受掉电 | **文档取证** | §1 P-3 |
| C3 | **写后读的正确原语是 `wait_wal_table(t)`**，不是裸 `SELECT`。实测两表均在 **4 ms** 内收敛至 `writerTxn == sequencerTxn` 并全量可见 | **实测（隔离实例）** | 本件 §2 expC |

**对写时锚 sidecar 的直接后果（重要）**：当锚表刚被重锚覆盖、而 `ApplyWal2TableJob` 尚未 apply 时，消费方裸读锚表拿到的是**上一版锚**——这是**错值**而非 NULL，直接与「宁 NULL 勿错值」原则冲突。因此：

- 写入侧「写后自检」必须走 `wait_wal_table('qfq_anchor_sidecar')`；
- 消费侧「读锚」必须同法，或携带 `seqTxn` 比对，**禁止裸读**；
- sidecar 状态语义只能声明到「已 commit 且已 applied」，**不得宣称「已持久化」**（默认 `nosync`）。

---

## 1. D20 分层：执行机 / 端点 / 数据根（R26 要求）

R26 纠正："**本工作区无 QuestDB，不代表全任务无该服务**"。**我方上一轮前提错误，现纠正并如实申报**：

| 层 | 执行机 | 端点 | 数据根 | 实例事实（本轮实测 10:19） | 能力 |
|---|---|---|---|---|---|
| **L-A 本机生产** | Windows 本机 | `127.0.0.1:9000`（REST）/ `9009`（ILP）/ `8812`（PG）/ `9003`（minHTTP），均 LISTEN，pid **5624**（java）/ **5036**（launcher） | `D:\questdb\quest_database` | **QuestDB 9.3.5**（`D:\questdb\questdb-9.3.5-rt-windows-x86-64`），**135 张表** | 只读查询；`wal_tables()` 可列 **116 张 WAL 表** |
| **L-B 本机隔离** | 同上（另起进程） | `127.0.0.1:19000/19009/18812/19003` | `D:\questdb\_d20probe`（临时） | 本轮新建，**~3 s 就绪**，实验后已销毁 | **可 DDL、可写、可观测窗口** |
| **L-C 云端源端** | 云端 | `https://124.223.159.234/mcp`（节点 13 报"两腿"） | 云端库 | 109 表（历史读数） | 只读 MCP 返回 |
| **L-D 主仓 DuckDB** | 本机 | 文件 `data/quantstudio.db` | `D:\miniQMT策略实盘\QuantStudio\data` | **66,640,687,104 B ≈ 62.06 GiB**，WAL 4,684,986 B | 单进程写锁；**与 QDB 读无关** |

**因此**：D20 的 P-2（两表分 commit 可见性）**不需要云端、不需要生产 DDL**——**L-B 隔离实例即可完成**（本轮已做）。P-1（DuckDB checkpoint）亦可在本机临时目录完成（本轮**未做**，见 §5）。

### 1.1 云端端点取证受阻（如实申报）

`Invoke-WebRequest -SkipCertificateCheck` 在 **PS 5.1 无此参数**（参数不存在，命令未发出），故本轮**未完成**云端 MCP 端点探活。**不据此断言云端不可达**。

---

## 2. P-2 实测：WAL 两表分 commit 的可见性窗口

**环境**：隔离实例 `127.0.0.1:19000`，数据根 `D:\questdb\_d20probe`（已销毁）；生产根 mtime 全程 `10-26 01:48:17` 未变。
**脚本**：`docs/evidence/d20-p2-wal-visibility-probe-20261113.py`
**原始结果**：`docs/evidence/d20-p2-wal-visibility-result-20261113.json`

建表（均 WAL，`cairo.wal.enabled.default=true`）：

```sql
create table anchor_wal (sym symbol, adj double, ts timestamp) timestamp(ts) partition by day wal
create table target_wal (sym symbol, px  double, ts timestamp) timestamp(ts) partition by day wal
```

### expA — 单行写后立刻读 `wal_tables()`

| 观测 | 读数 |
|---|---|
| 轮次 | 20 |
| `writerTxn != sequencerTxn` 轮次 | **15 / 20（75%）** |
| 最大滞后 | **5 笔事务** |
| gap 分布 | `{0: 5, 1: 11, 2: 1, 3: 1, 4: 1, 5: 1}` |

⇒ 单行写入后**四分之三**的立即读拿到的是「尚未 apply」的状态。

### expB — 250k 行大事务，两表分别提交，提交返回后立即快照（决定性）

| 表 | `writerTxn` | `sequencerTxn` | 可见行数 | 判定 |
|---|---|---|---|---|
| `anchor_wal` | 21 | 21 | **250,020** | 已 apply，全可见 |
| `target_wal` | **0** | **1** | **0** | **已 commit（含 1 笔事务）但一行不可见** |

提交耗时：anchor 0.077 s / target 0.092 s（服务端 `long_sequence(250000)` 生成，非网络瓶颈）。
`inconsistency_observed = true`。

⇒ **「commit 已返回成功」与「数据可读」之间存在可观测的确定性窗口**，不是理论风险。

### expC — `wait_wal_table` 收敛效果

| 观测 | 读数 |
|---|---|
| `wait_wal_table('anchor_wal')` | `true` |
| `wait_wal_table('target_wal')` | `true` |
| 两表等待总耗时 | **0.004 s** |
| 等待后 `writerTxn == sequencerTxn` | **两表均成立**（21/21 与 1/1） |
| 等待后计数 | 250,020 / 250,000 |

⇒ `wait_wal_table` 是**低成本（4 ms 级）且有效**的收敛原语，应作为写后读的强制中间步。

### expD（失败，不影响结论）

`select * from configuration() where key like '%commit%'` → HTTP 400。运行期配置查询路径不可用；`cairo.commit.mode` 结论**改由 §1 P-3 的文档 + `server.conf` 双源支撑**，不依赖 expD。

---

## 3. P-3 文档取证：`cairo.commit.mode` 与持久化

**依据（铁律16：标注文档出处）** — Context7 库 `/websites/questdb`：

| 文档页 | 原文（摘） | 用途 |
|---|---|---|
| `/docs/configuration/cairo-engine` → `cairo.commit.mode` | **Default: `nosync`**；`nosync`: "no explicit flush (relies on OS page cache)"；`async`: "flush call is scheduled but returns immediately"；`sync`: "waits for flush on appended column files to complete"；**Reloadable: no** | 默认不 fsync；且**不可热改**，须重启 |
| `/docs/architecture/storage-engine` → Durability | "By default, QuestDB relies on OS-level durability… For stronger guarantees, enable sync commit mode: `cairo.commit.mode=sync`．This invokes `fsync()` on each commit, ensuring data survives OS crashes or power loss at the cost of reduced write throughput." | **COMMIT ack ≠ 掉电持久**的直接出处 |
| `/docs/connect/java-embedded` → Commit changes | "WAL writer commits aren't immediately visible to readers. The committed data becomes visible once it was applied by the `ApplyWal2TableJob` job." | C1 的官方依据 |
| `/docs/cookbook/operations/check-transaction-applied` | `writerTxn` = "the last committed transaction available for read-only queries"；`sequencerTxn` = "the last transaction committed to WAL"；两者相等 ⇒ 全部可见 | 可机读的窗口判定器 |
| `/docs/query/functions/meta` → `wait_wal_table()` | `wait_wal_table(tableName, seqTxn?)` 阻塞直至 applied；**非 WAL 表立即返回 `true`**；表被 drop 或 suspended 时抛错 | C3 的原语出处 |

### 3.1 本机 `server.conf` 一手事实（`D:\questdb\quest_database\conf\server.conf`）

| 项 | 值 | 说明 |
|---|---|---|
| `cairo.wal.supported` | `true` | WAL 可用 |
| `cairo.wal.enabled.default` | `true` | **新建表默认 WAL 模式** ⇒ sidecar 表若用 SQL 建表，默认走 WAL，必然受 C1 约束 |
| `cairo.commit.mode` | 注释态（`#cairo.commit.mode=nosync`）⇒ **运行值为默认 `nosync`** | 未开启 fsync |
| `cairo.commit.latency` | 注释态 ⇒ 默认 `30s` | 物理 commit 强制前的合并窗上限 |
| `cairo.wal.max.lag.size` | 注释态 ⇒ 默认 `75M`（达限后 commit 50M、保留 25M） | WAL lag 上限 |
| `cairo.wal.segment.rollover.row.count` | 注释态 ⇒ 默认 `200000` | 段滚动阈值 |

> **注**：`server.conf` 全为注释态的项即"取默认值"，与 §3 文档默认值一致，构成**双源互证**。

---

## 4. 对 sidecar 规格的强制修订点（待并入 v3.6）

1. **状态语义降级**：`qfq_anchor_sidecar` 写入成功只能声明 **"committed & applied"**；在 `cairo.commit.mode != sync` 的前提下，**禁止**任何"已持久化/掉电安全"表述。
2. **写后自检强制加 `wait_wal_table`**：`wait_wal_table('qfq_anchor_sidecar')` 返回 `true` 后，方可记「锚已就位」。**S1 前夜的裸 `count()` 自检属违规实现**（应改为 wait + `writerTxn/sequencerTxn` 双读）。
3. **消费侧读锚强制**：任何 ratio 消费方在计算 `front/back` 前，必须先 `wait_wal_table` 或比对 `seqTxn`；**否则会读到上一版旧锚 = 错值**（比 NULL 更危险，与「宁 NULL 勿错值」正面冲突）。
4. **fail-closed 判据修正**：原「锚表无行 ⇒ 置 NULL」须扩为「锚表未 applied 或 `seqTxn` 落后 ⇒ **拒绝出数**（不得回退到上一次锚）」。**「读到旧锚」不得被误当作"有锚"**。
5. **发布顺序不可省 `wait`**：v3.3/v3.4/v3.5 的五段顺序（写锚 → commit → **wait applied** → 校验 → 发布）中，`wait applied` 为**必过步，非优化项**。

---

## 5. 未完成项（如实申报）

| 项 | 状态 | 原因／解除条件 |
|---|---|---|
| **P-1** DuckDB `CHECKPOINT` 隔离探针 | **未做** | 本轮时间/上下文优先投向 P-2/P-3；可在临时目录独立 DuckDB 文件完成，**不涉主库**。解除条件：下轮排期 |
| **P-2 云端端点交叉验证** | **未做** | PS 5.1 无 `-SkipCertificateCheck`，HTTPS 探活命令未发出；需 `curl.exe` 或 PS7 |
| **P-3 掉电/强杀实验** | **依指令不做** | R26 明确"生产 DDL、强杀或掉电实验不执行"。**结论由文档 + 默认配置支撑，不由破坏性实验支撑**（该能力缺口保持明示） |
| `configuration()` 运行期查配置 | 失败 | HTTP 400；已改用文档 + `server.conf` 双源，不影响 C2 |

---

## 6. census 判据改判（R26 第3点）

**撤回两条过判据**：

| 原判据 | 问题 | 改判后 |
|---|---|---|
| ①「daemon 长驻进程在 ⇒ 负载不通过」 | daemon 持的是**主仓 DuckDB 写锁**；census 读 **QuestDB**，两者**不同存储引擎、不同文件、不同锁域** | 改为：**核目标库（QDB）实际负载**——本轮实测 QDB 9000 正常响应（`select 1` = 1，`tables()` 135 表，`wal_tables()` 116 表全 `suspended=false`、`errorTag` 空）⇒ **QDB 无过载证据** |
| ③「`docs/handoff` 命中占用/候选计划文档 ⇒ 占用不通过」 | **文档落盘 ≠ occupied 窗口**；他线写候选计划不等于占用 QDB | 改为：**核真实 occupied 窗口**——本轮②的阻塞实体是他线 `g9_r03_probe.py --wait-lock`（pid 30696，起 10:19:41，等**主库**锁）与 `main_gui.py`（pid 51136），二者**均不涉 QDB**；QDB 侧无任何他线写入声明 |

**结论：census（QDB 全库逐段清单）不再被「无关锁」阻塞。** 唯二真实约束为：
- **后端**：L-A 本机 135 表 / L-C 云端 109 表，须按端点分别出清单（不可混算）；
- **长跑方式**：按 R26 第4点，改用**持久 manifest + 可续批次**，禁止一次性长跑占满上下文。

---

## 7. T3 三交易日裁定（R26 第5点）

**权威日历实测**（本机 QDB `trade_cal`，`exchange=SSE`，覆盖 **1990-12-19 ~ 2027-12-31**，**13,527 行**）：

| 日期 | `is_open` | `pretrade_date` | 判定 |
|---|---|---|---|
| 2026-11-12 | **1** | 2026-11-11 | **交易日 ①** |
| 2026-11-13 | **1** | 2026-11-12 | **交易日 ②** |
| 2026-11-14 | **0** | 2026-11-13 | 休市（周六） |
| 2026-11-15 | **0** | 2026-11-13 | 休市（周日） |
| 2026-11-16 | **1** | 2026-11-13 | **交易日 ③** |

⇒ **R26 建议的 11-12／11-13／11-16 三交易日成立**；旧指示的 **11-14 经权威日历证伪为休市日**，不在非交易日凑样本。
已挂载的守护：`schedule-40f5f2d0`（11-16）**口径正确保留**；`schedule-38e4837c`（11-14）降级为**非交易日旁证**，不再作为取样窗。

---

## 8. 铁律对照

- **铁律16（平台先查文档）**：§3 全部结论标注 Context7 文档页；未取到者（云端端点）如实声明，未伪装为查证结论。
- **铁律19（daemon 占用）**：本轮**未停/未启/未接管**任何 daemon；仅新建并销毁**独立隔离实例**（19000，独立数据根），生产实例与数据根全程未触碰（mtime 未变）。
- **铁律24（schema 指纹）**：生产库**零 DDL**。隔离实例的 DDL 仅作用于 `_d20probe` 临时根，已随实例销毁。
- **零实施**：v2 五条写入路径**未改一行**；全部产物为文档/证据/夹具。
