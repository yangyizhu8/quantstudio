# QFQ post-ingest 插桩方案（诊断用，非修复）

- 起草：2026-10-31（客户运维线）
- 依据：用户裁定「插桩要碰生产代码路径——方案先呈小型②审再进窗口」
- 目的：**只采集 H1/H2 的定量拆分**（26 分钟里 N+1 占多少、WAL 放大占多少），不修任何逻辑
- 判型：**新增检测型**（只新增观测输出；默认关闭时零行为变更）

---

## 一、背景与动机

profiling 首份交付（`docs/evidence/qfq-post-ingest-profiling-20261031.md`）已定性：

- 阻塞点 = `record_observations`（`qfq_observation.py:318`），线程 idle、CPU 零增量；
- 该函数对 N 条观测做 ≥2N 次 sqlite 往返（N+1）；
- aux 库 11.07GB、会话期 WAL 1.56GB，**生产链路无 `wal_checkpoint`**。

**缺的是定量**：26 分钟静默中，多少来自「往返次数」，多少来自「巨 WAL 让每次往返变慢」。这决定快赢①②③的优先序与发版决策。

---

## 二、范围与边界

### 2.1 改动文件

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_observation.py` | `record_observations` 内加**条件插桩**：入口/出口计时、各语句分组累计、commit 与 WAL 差值、逐条往返耗时采样 |
| `quantstudio/pipeline/qfq_profile.py`（**新增**） | 插桩基础设施：env 门控、事件写出（JSONL）、P50/P95 计算、零开销短路 |
| `tests/test_qfq_profile.py`（新增） | P1–P6 机检（门控关闭时零输出 / 开启时字段齐全） |

### 2.2 硬要求（用户裁定）

| # | 要求 | 落实 |
|---|---|---|
| H-1 | **env 门控** `QS_QFQ_PROFILE=1`，**默认关** | 未设/非 `1` ⇒ `record_observations` 入口只做一次 `os.environ.get` 判空即返回，**不进入任何计时分支** |
| H-2 | **关闭时零开销** | 门控判定放在函数首行；关闭时后续所有 `if _prof:` 分支均不执行（无 functools 包装、无 try/except 开销） |
| H-3 | **窗口结束即摘或永久保持门控** | 本方案采用「**永久保持门控 + 默认关**」（代码留存，env 开启才生效）；若②审要求摘除，按 `git revert` 单笔回退 |
| H-4 | **读数格式预定义** | 见 §2.3（schema 固定，事后不改字段语义） |

### 2.3 读数 schema（预定义，JSONL 每行一条）

```json
{"schema": 1, "event": "record_observations", "run_id": "...", "asset_type": "stock",
 "ts": "2026-10-31T04:20:00",
 "n_observations": 0, "n_codes": 0, "n_future_excluded": 0,
 "phase_s": {"preprocess": 0.0, "begin": 0.0, "baseline_select": 0.0,
             "value_select": 0.0, "insert": 0.0, "adjacent_check": 0.0,
             "commit": 0.0, "total": 0.0},
 "queries": {"baseline_select": 0, "value_select": 0, "insert": 0, "adjacent_check": 0, "other": 0},
 "wal": {"size_before_bytes": 0, "size_after_bytes": 0, "delta_bytes": 0,
         "pages_before": 0, "pages_after": 0,
         "checkpoint_passive": {"busy": 0, "log": 0, "checkpointed": 0}},
 "roundtrip_ms": {"p50": 0.0, "p95": 0.0, "max": 0.0, "samples": 0},
 "db": {"size_bytes": 0, "task": "mcp_etf_daily"}}
```

**字段说明（用户补充项已含）**：

| 字段 | 服务对象 |
|---|---|
| `n_observations` / `n_codes` | H1 定量（N 的实际值） |
| `phase_s.*` | 各语句分组耗时（定位最贵语句） |
| `queries.*` | 往返次数（与 `phase_s` 对照验证 N+1） |
| `wal.size_before/after/delta` | H2 定量（本事务让 WAL 增多少） |
| **`wal.pages_before/after`** | **用户补充**：WAL 页数变化（比字节更贴近 sqlite 内部成本） |
| **`wal.checkpoint_passive`** | **用户补充**：`PRAGMA wal_checkpoint(PASSIVE)` 的 `(busy, log, checkpointed)` 三元组——**只读探测**，不触发 TRUNCATE，用于判断「WAL 已积累多少可回写页」 |
| `roundtrip_ms.p50/p95/max` | 单次往返分布（与 WAL 规模做相关性） |
| `db.size_bytes` / `task` | 相关性分析的分组维度 |

### 2.4 明确不改动

- **不改任何判据/分支/事务边界**：`BEGIN IMMEDIATE`/`commit`/`rollback` 语义不变；
- **不改写入内容**：观测行集合、`revision_no` 推导、时间戳语义全部不变；
- **不启用 `wal_checkpoint(TRUNCATE)`**（那是快赢①的范围，需单独论证）；
- 门控关闭时**输出零行、耗时增量 < 1ms/次**（P1 判据）。

### 2.5 回退

| 开关 | 默认 | 作用 |
|---|---|---|
| `QS_QFQ_PROFILE` | 未设（关） | `1` ⇒ 开启插桩；其余 ⇒ 完全关闭 |
| `QS_QFQ_PROFILE_OUT` | `data/logs/qfq_profile.jsonl` | 输出位置 |

---

## 三、任务拆解

| 点 | 功能 | 判据 | 负向对照 |
|---|---|---|---|
| **P1** | 门控零开销 | 关闭时：`record_observations` 单次调用额外耗时 < 1ms（基准对比 100 次）；**输出文件不产生** | 设 `QS_QFQ_PROFILE=0` ⇒ 同样零输出（不因「已设」而开启） |
| **P2** | 事件字段齐全 | 开启跑一次 ⇒ JSONL 单行含 §2.3 全部字段且类型正确 | 字段缺失 ⇒ P2 红 |
| **P3** | 分组计时一致 | `phase_s.total` ≈ 各分组之和（误差 <5%）；`queries.*` 之和 == 实际 `c.execute` 次数（用 spy 计数对拍） | 手工改 spy 计数 ⇒ P3 红 |
| **P4** | WAL 读数正确 | `delta_bytes == size_after - size_before`；`pages_*` 与 `PRAGMA page_count` 一致 | WAL 不存在 ⇒ `delta_bytes=0` 且不报错 |
| **P5** | checkpoint PASSIVE 只读 | 探测前后 aux 库/WAL 尺寸**不变**（PASSIVE 不回写主库） | 若发现尺寸变化 ⇒ 判为误用 TRUNCATE，P5 红 |
| **P6** | 输出不阻塞主流程 | 写 JSONL 失败（如磁盘满/目录不可写）⇒ 记一次 warning 后**继续执行**，不抛穿 | 目录不可写 ⇒ 业务结果与关闭插桩时**逐位相同** |
| **P7** | 防回归 | `pytest tests/` 中本次相关全绿 | 删除门控短路 ⇒ P1 必红 |

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| 插桩改变执行路径 | 采集行为漂移 | 只加计时与写出，不改分支/事务；P2/P3 对拍 |
| 计时本身拖慢 | 读数失真 | 用 `time.perf_counter()`（微秒级），单次开销可忽略；P1 硬判据 |
| JSONL 膨胀 | 磁盘 | 单次任务一条事件（非逐条观测），体积 KB 级 |
| **PASSIVE 探测的锁行为** | 与写者交互 | PASSIVE 不回写、不 TRUNCATE，仅读取 WAL 状态；在**副本库先验证**后再上生产窗口 |
| 窗口忘记摘 | 长期带插桩运行 | 默认关闭 + 门控留存（H-3） |

---

## 五、验收要点

1. **P1–P7 全绿**；
2. **副本库预演**：在 `data/` 外复制 aux 库（或小型合成 aux）跑一次开启态，确认字段与 PASSIVE 读数正确、无锁异常；
3. **生产窗口单次**：跑 `once mcp_etf_daily --quality-audit none`（**关审计**，避免审计段污染读数），采集 1 条完整事件；
4. **读数入档**：`docs/evidence/qfq-post-ingest-profiling-20261031.md` 追加「H1/H2 定量拆分」章节；
5. **摘除/留存**：按 H-3 保留门控，代码默认关；不保留则单笔 revert。

---

## 六、质量判据

1. **零行为变更**：门控关时采集结果与插桩前逐位一致（同一输入对拍）；
2. **读数可用于决策**：能直接回答「26 分钟中 N+1 贡献 X%、WAL 放大贡献 Y%」；
3. **不引入新故障面**：PASSIVE 探测在副本验证、写出失败不阻断；
4. **可摘除**：单笔 revert 即可回到插桩前状态。

---

## 附：快赢①（aux checkpoint 安全性）副本实验方案 —— 并行批准项

> 用户提醒：H2 不只是性能问题，**WAL 无界增长本身是客户机磁盘风险**（本机 1.56GB）。论证须同时算「磁盘卫生」与「耗时」双重收益。

| 项 | 内容 |
|---|---|
| 环境 | **副本 aux 库**（`data/` 之外复制，或合成小型 aux）；绝不碰生产 |
| 实验矩阵 | ①单写者连续写 → 中途 `wal_checkpoint(TRUNCATE)`；②一写多读（模拟 daemon 读路径）→ 同上；③`busy_timeout` 边缘（写者持锁时 checkpoint 的 `busy` 返回） |
| 读数 | 每次 checkpoint 的 `(busy, log, checkpointed)`；checkpoint 前后耗时；`PRAGMA integrity_check`；WAL 体积收敛曲线 |
| 双重收益量化 | 磁盘：WAL 从 1.56GB → 收敛后体积；耗时：收敛前后同一 workload 的 p50/p95 往返差 |
| 红线 | 不在生产运行期执行；不复制主库（只复制 aux）；实验结束删除副本 |
| 产出 | `docs/evidence/qfq-aux-checkpoint-lab-<日期>.md`（实验矩阵 + 读数 + 结论建议） |
