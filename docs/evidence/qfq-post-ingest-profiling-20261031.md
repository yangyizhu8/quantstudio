# QFQ post-ingest 挂死诊断 · 首份 profiling（2026-10-31）

> 专项立项依据：用户裁定「QFQ post-ingest 专项立项，优先级高于 A2b；**先诊断后开方**——首份交付物是 profiling 读数，不带读数不出修复方案」。
> 本文**不含修复方案**，只交读数 + 待验证清单 + 快赢候选（需专项内论证）。

---

## 一、现象与取证方式

| 项 | 内容 |
|---|---|
| 触发 | 2026-10-31 02:52 实弹 `once mcp_etf_daily --quality-audit task`（加载 Phase 1 代码的 daemon 时代） |
| 现象 | 数据写入**已成功**（`raw=70862 passed=70859 written=70859`，124.2s），但 once **永不返回**，进程持续占用 3A 锁 |
| 取证 | ①日志时间线 ②`py-spy dump` 栈 ③进程 CPU/工作集采样 ④代码静态统计 ⑤aux 库文件级读数 |

---

## 二、读数

### D1 时间线（日志级）

| 时刻 | 事件 |
|---|---|
| 02:52:25 | once 启动（manifest 写入 + tables init + config_lint 通过） |
| 02:52:27 | `INCREMENTAL: 2026-09-05 → 2026-10-31` |
| 02:54:31 | ✅ 数据写入完成（124.2s）＋ `[qfq_orch] TD-D2 released 门生效` |
| 02:56:09 | 最后一条日志：`[qfq_event] adj_factor code 契约预过滤 skip 1 行` |
| 02:56:09 → 03:22 | **静默 26 分钟**（日志零增长，CPU 90s 增量为 0），直至被终止 |

**⇒ post-ingest 阶段静默 ≥26 分钟且未完成。**

### D2 栈（py-spy，阻塞点精确定位）

```
record_observations          (qfq_observation.py:318)   ← 阻塞在此
_observe_factors             (qfq_event_discovery.py:464)
observe_stock_adj_factor     (qfq_event_discovery.py:537)
_discover                    (qfq_resident_orchestrator.py:548)
run_post_ingest              (qfq_resident_orchestrator.py:1852)
qfq_run_post_ingest          (daemon.py:465)
execute_task                 (daemon.py:774)
run_once                     (daemon.py:2526)
```

线程状态 `idle`、CPU 增量为 0 ⇒ **阻塞在系统调用（sqlite I/O）**，非计算。

### D3 进程采样

| 指标 | 读数 |
|---|---|
| CPU 累计 | 1105s → 1136s（终止前），**90s 内增量 0** |
| 工作集 | 3540 MB |
| 线程/句柄 | 79 / 810 |
| 3A 锁持有者 | **自身**（`task_id=event_discovery:292`，heartbeat 持续刷新）⇒ 不是等别的进程 |

### D4 `record_observations` 查询模式（静态统计）

单次调用内（`qfq_observation.py:241-390`）：

| 位置 | 语句 | 频次 |
|---|---|---|
| :299 | `BEGIN IMMEDIATE` | 1 次（单事务，好） |
| :307-310 | `SELECT 1 … WHERE asset_type=? AND code=? LIMIT 1`（baseline 判定） | **每 code 1 次** |
| :314-319 | `SELECT factor_value, revision_no … ORDER BY revision_no DESC LIMIT 1` | **每条 observation 1 次** |
| :323-329 | `INSERT INTO qfq_factor_observation …` | **每条 1 次** |
| :333+ | 相邻 factor_time 变化检测 `SELECT … WHERE factor_time < ?` | 命中新观测时追加 |
| :357/:368/:376 | 其他 SELECT/UPDATE | 条件触发 |

**⇒ 典型 N+1：N 条观测 ⇒ ≥2N 次 sqlite 往返 + 每条一次 INSERT。**

### D5 aux 库形态（文件级）

| 项 | 读数 |
|---|---|
| `qfq_aux.db` | **11,070.8 MB** |
| `qfq_aux.db-wal`（会话期） | **1,557.8 MB** |
| `qfq_aux.db-shm` | 6.9 MB |
| 连接 PRAGMA | `journal_mode=WAL`、`busy_timeout=30000`（`qfq_observation.py:193-194`） |

### D6 🔴 **生产路径无 aux WAL checkpoint**

全仓 `wal_checkpoint` 调用点扫描：

```
qfq_staging_canary.py:86  PRAGMA wal_checkpoint(TRUNCATE)   ← 仅 canary 测试脚本
```

**⇒ 生产链路（daemon / once / 编排器）无任何 `wal_checkpoint` 调用。**
WAL 只在「最后一个连接关闭」时由 sqlite 自动 checkpoint——**这解释了为什么 44080 被终止后 `qfq_aux.db-wal`（1.56GB）立刻消失**（本次实测已观测到该现象）。

### D7 调用链上游（观察生成侧）

`qfq_event_discovery.py` 的除权扫描已有批量路径（`_use_batch_scan()` / `_scan_dividend_v2_batch`，可用 `QFQ_DIVIDEND_SCAN_BATCH=0` 回退逐行），但 **`record_observations`（因子观测写入）本身无批量实现**。

---

## 三、结论（三条根因假设，按证据强度排序）

| # | 假设 | 证据强度 | 状态 |
|---|---|---|---|
| **H1** | **N+1 写入**：每条观测 2–4 次 sqlite 往返，N 大时线性放大 | 强（D4 静态统计 + D2 栈定位在该函数） | 待插桩定量（需 N 的实测值） |
| **H2** | **aux WAL 无 checkpoint** ⇒ WAL 膨胀至 1.56GB ⇒ 每次往返都要在巨型 WAL 上做页查找/追加 | 强（D6 全仓扫描 + D5 文件读数 + 关闭即消失的实证） | 待插桩定量（WAL 大小与单次往返耗时相关性） |
| **H3** | 二者叠加放大（WAL 越大，N+1 的每次往返越慢） | 中（逻辑推断） | 待插桩定量 |

---

## 四、待插桩才能拿到的读数（下一步前置）

> 插桩属变更动作，需按 19/六步纪律走窗口；以下清单为**插桩方案**的输入，不是修复方案。

| 读数 | 插桩点 | 目的 |
|---|---|---|
| `record_observations` 的 **N（观测条数）** | 函数入口计数 | 定量 H1 |
| **各语句累计耗时**（SELECT / INSERT / 相邻检测分开计时） | 语句前后 `time.perf_counter()` 累加 | 定位最贵语句 |
| **事务提交耗时** + WAL 大小前后差值 | `BEGIN`/`commit` 两端 | 定量 H2 |
| **单次往返 P50/P95** | 逐条计时采样 | 与 WAL 大小做相关性 |
| **各任务类型的 post-ingest 耗时分解** | `run_post_ingest` 各阶段打点 | 判断 observation 是否唯一大头 |
| WAL 大小与往返耗时相关性 | 在不同 WAL 规模（如 200MB / 800MB / 1.5GB）采样 | 验证 H2 的因果 |

---

## 五、快赢候选（需专项内论证，非结论）

**候选 1：post-ingest 收尾后对 aux 执行 `wal_checkpoint(TRUNCATE)`**

- 收益假设：把 1.56GB WAL 收敛，后续轮次往返变快（H2 的镜像解法）；
- **必须先论证的安全性**：daemon 常驻进程**长期持有 aux 连接**（observation store / 因子读取），checkpoint 与并发写者的锁交互；主库 23 号铁律「运行期禁 CHECKPOINT」是否适用于 aux（aux 为 sqlite，语义不同，需单独论证）；
- 论证方式：在**副本 aux 库**上做并发写 + checkpoint 实验，测 busy/lock 行为与数据完整性（`PRAGMA integrity_check`）。

**候选 2：`record_observations` 改批量**

- 形态：`executemany` 批量 INSERT + 一次 `SELECT … WHERE (asset_type,code) IN (…)` 取 baseline 与现值；
- 纯增益边界：输出逐位一致（观测行集合/`revision_no`/时间戳语义不变）、`QFQ_OBSERVATION_BATCH=0` 回退；
- 验收：真实 aux 副本对拍（同一输入两版写出的行集合与字段逐位相同）。

**候选 3：N 上限/分批**

- 若单次 N 过大，按 code 分批提交（参照 A1b 的分区键分批思路）。

**优先级**：候选 1（快、独立）→ 候选 2（结构性）→ 候选 3（兜底）。**三项均需先出方案走②审**。

---

## 六、一句话给发版决策

> post-ingest 的 26 分钟静默是「数据写完但任务不返回」的根因；它与 Phase 1 修的「审计 OOM」是两个独立故障面。
> **在 H1/H2 定量读数到手前，不能向客户承诺「全部执行恢复正常」。**

---

## 七、沉淀日期

2026-10-31（首份交付，诊断未闭环）
