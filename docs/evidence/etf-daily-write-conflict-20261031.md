# etf_daily 写冲突可复现案例（2026-10-31）

> 交付对象：writers 属线（`_SERIAL_WRITE_TABLES` 域是否应扩日线表属其设计决策）
> 本文件只交**取证读数**，不含修复方案；零越权，未改 writers.py

## 一、两次独立复现

| # | 场景 | 时间 | 结果 |
|---|---|---|---|
| 1 | 06:00 每日轮（daemon 内部串行任务）`mcp_etf_daily` | 2026-10-31 06:01:45–06:01:56 | 重试 1/3→2/3→3/3 **全败** → `❌ FAILED[streaming]` |
| 2 | 轮后补跑 `once mcp_etf_daily`（daemon 19148 存活并存） | 2026-10-31 14:49:14–14:49:28 | 同样 3 次重试**全败** |

两次均**同一冲突键**：`conflict_key = "588390, 1788710400000"`（`588390` @ **2026-09-07**）。

## 二、完整日志字段（06:00 轮，最完整的一条）

```
WARNING [writers] write-write conflict 上下文：
  conflict_table=etf_daily
  conflict_scope=table=etf_daily
  batch=mcp_etf_daily_mcp_20261031_060023_4bd736
  path=_write_locked
  tseg=2026-09-07 00:00:00~2026-10-16 00:00:00 [1788710400000~1792080000000]
  conflict_bar=2026-09-07 00:00:00
  conflict_key=588390, 1788710400000
  peers_n=0
  cross_process_candidate=True

ERROR 写失败判别特征:
  path=_write_locked serial=False ctx_src=failure ctx_table=etf_daily
  ctx_path=_write_locked ctx_exc=TransactionException
  exc=TransactionException conflict_key="588390, 1788710400000"
  peers_n=0 cross_proc=True peers=-
```

## 二·五、🔴 **机制修正：这不是偶发竞态，是自锁死循环（doom-loop）**

> 用户 2026-10-31 追加发现并实测确认。**修复必须能打破循环**（日线串行保护 / 交叠消除 / 冲突合并策略任一），只调「重试次数」无效。

### 实测水位（只读核对，2026-10-31 15:40）

| 表 | `source_watermark.last_date` | 表内 `MAX(time)` | 差距 |
|---|---|---|---|
| `etf_daily` | **2026-09-04** | 2026-10-29 | 水位落后 **55 天** |
| `stock_daily` | **2026-09-03** | 2026-10-30 | 水位落后 **57 天** |

**关键细节**：表内数据已推进到 10-29/10-30，但**水位停在九月**——说明写入是**部分成功**（前一阶段写进去），失败后水位不提交 ⇒ 下次仍按旧水位拉。

### 循环链

```
日线写冲突 3 重试全败
   → 任务判 FAILED
   → 水位不推进（last_date 停在 09-04）
   → 下次拉取窗口仍是 09-05 起的 5+ 周大区间
   → 同一毒键（588390 @ 2026-09-07）再次落在并发分片交叠区
   → 再次冲突  ⟲（每天复制一遍，且每天再缺一天数据）
```

⇒ **「100% 复现」的真正原因是棘轮自锁，不是数据巧合**。这也解释了为什么 06:00 轮与 14:47 补跑用**同一个键**失败。

### 数据面影响（优先级应上调）

两张日线表已缺近两月数据，且在修复前**每天都会继续失败并再缺一天**。属线的响应优先级应据此上调。

### 可选诊断（留 writers 线决定）

补跑一次**排除 588390 的窗口**（如 09-08 起）：若成功推进水位，即证明冲突**确定性绑定该键**，证据链闭合；代价是 2026-09-07 该行留洞待定向补。

---

## 三、三条读数（供属线判断）

### 3.1 拉取窗口跨一个月，不是当日增量

`tseg = 2026-09-07 ~ 2026-10-16` —— 今天（10-31）的 etf_daily 拉取窗口覆盖九月上旬至十月中旬，冲突落在**窗口起点那一根 bar**。

⇒ 与「今日行情」无关，是**历史区间分批写入**时的重叠。

### 3.2 `serial=False`：日线表不走串行写路径

```
_SERIAL_WRITE_TABLES = frozenset({"stock_minutes", "etf_minutes"})   # writers.py:95（HEAD 已提交态）
```
`etf_daily` 不在其中 ⇒ `serial=False` ⇒ 无进程内串行保护。

### 3.3 ⚠️ `cross_process_candidate=True` 这个标记可能误导归因

关键在于同一行里 `peers_n=0`：

| 字段 | 值 | 含义 |
|---|---|---|
| `peers_n` | **0** | 同进程的锁登记表里**看不到**任何 peer 写者 |
| `cross_process_candidate` | **True** | 据此推断「可能是跨进程冲突」 |

**但 `peers_n=0` 也可能意味着「同进程有第二个连接，只是没走 registry 登记」**——例如 `streaming` 分片自行 `duckdb.connect()`。此时：
- DuckDB 层面：同进程两个读写连接 ⇒ 报 `write-write conflict`（真实存在）
- registry 层面：看不见它 ⇒ `peers_n=0`
- 推断层：`cross_process_candidate=True`（**误判**）

⇒ **建议属线先确认**：streaming 分片是否自开连接、是否登记到 registry。若确认未登记，则该标记应改名为「registry 未见的写者」而非「跨进程候选」，否则会把排查引到错误方向（本人第一轮就据此错误归因到他线改动）。

### 3.4 与本次 Phase 1 / A2b 无关的证据

- 冲突发生在 `writers.py::_write_locked`，本会话改动全部在 `quality_audit.py` / `daemon.py`（审计段）/ `daemon_lifecycle.py` / GUI 三文件 / `mcp_adapter.py`，**零触及写入路径**；
- `_SERIAL_WRITE_TABLES` 的分钟限定是 HEAD 已提交态（`d31e6ef` 2026-10-08 引入），非在途改动；
- 本轮 daemon 19148 的 `git_commit=6c73be5` 与失败无因果。

## 四、附：同日第二次复现时暴露的两个操作性事实

### 4.1 `once` 模式不响应 `stop.request`

`consume_stop_request_with_reason` 只在 `run_forever` 轮询循环内调用；`run_once` 路径无轮询 ⇒ **对 once 进程写 stop.request 永远无效**。终止 once 只能用精确 PID 强杀（本日两次：44080、40500，均排除自身 PID 后执行）。

### 4.2 QFQ post-ingest 挂死 **100% 复现**

补跑在写失败后进入 post-ingest，再次卡在**同一栈**：

```
record_observations          (qfq_observation.py:318)
_observe_factors             (qfq_event_discovery.py:464)
observe_stock_adj_factor     (qfq_event_discovery.py:537)
_discover                    (qfq_resident_orchestrator.py:548)
run_post_ingest              (qfq_resident_orchestrator.py:1852)
qfq_run_post_ingest          (daemon.py:465)
```

| 项 | 读数 |
|---|---|
| 卡死时长 | 14:51 → 15:23（**32 分钟**，与昨日 26 分钟同量级） |
| 线程状态 | `idle`（等 sqlite I/O） |
| CPU（终止前） | 1013s（累计），卡死期间零增量 |
| 内存 | 3743 MB |
| 3A 锁 | 持锁者=自身（`event_discovery:*`），peer 0 |

⇒ QFQ 专项诊断的「H1 N+1 / H2 WAL 无 checkpoint」假设**再次获得强支持**（同类任务必挂，非偶发）。

## 五、当日数据面现状（供运维决策）

| 表 | 2026-10-31 行数 | 说明 |
|---|---|---|
| `etf_daily` | **0** | 写冲突失败（本文件主题） |
| `stock_daily` | **0** | **A4 熔断主动跳过**（`检出 999 个 repair/full 窗口 > 阈值 20`，非故障） |
| `etf_daily` max(time) | 2026-10-29 | 10-30、10-31 均缺 |

## 六、沉淀日期

2026-10-31（本案为 writers 属线交付；本会话零修复动作）
