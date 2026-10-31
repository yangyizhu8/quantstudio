# 方案②：全局段审计缓存推广（factor_monotonicity 为首）—— 设计 v1

- 起草：2026-10-31（客户运维线，会话节点② 指示「键候选实测 + 方案②成文，今夜或明晨，正式②审呈调度」）
- 判型：**纯性能型**（审计结论零漂移；命中读出与重算逐位相同）
- 前置：A2b（`af25e66`）已落地并验证；A 分段计时（`5cc35ad`）已出表

---

## 一、背景与动机

### 1.1 实测事实（A 分段计时，RO 加固后）

| 段 | task-cold | task-warm（A2b 命中） |
|---|---|---|
| **`_audit_factor_monotonicity`** | **334.25 s（90.0%）** | **390.72 s（98.9%）** |
| `_audit_minute_anchor_drift` | 34.05 s（9.2%） | 0.02 s（0.0%）✅ A2b 已消除 |
| `_audit_watermarks` | 0.92 s | 3.21 s |
| `batch_pipeline` / `quarantine` | 0.00 s | 0.00 s |

⇒ **A2b 只治了 9.2%；90% 在 `_audit_factor_monotonicity`。**

### 1.2 该段为何贵（探针结论）

```python
# quality_audit.py:1164-1172
for factor_tbl in ("adj_factor", "fund_adj"):
    bad = conn.execute(
        f"WITH x AS (SELECT code, adj_factor, "
        f"LAG(adj_factor) OVER (PARTITION BY code ORDER BY time) AS prev "
        f"FROM {factor_tbl}) "                    # ← 无 WHERE，全表窗口
        f"SELECT DISTINCT code FROM x WHERE prev IS NOT NULL AND adj_factor < prev - 1e-9"
    ).fetchall()
```

| 表 | 行数 | 单 `COUNT(*)` |
|---|---|---|
| `adj_factor` | **31,148,140** | 926 ms（热）/ 22.3 s（冷） |
| `fund_adj` | **12,490,690** | 337 ms（热）/ 8.4 s（冷） |
| 合计 | **43,638,830** | — |

**双层缺陷**：①**全表窗口无界**（4364 万行 `LAG`，与 Phase 1 A1b 同族）；②**每次任务审计都重算**（73 任务 = 73 次）。

### 1.3 目标

| 场景 | 现状 | 目标 |
|---|---|---|
| task 域单次（冷） | ≈371 s | ≈371 s（首次仍需全算） |
| task 域单次（热） | ≈371 s（A2b 后无变化） | **≈37 s** |
| 73 任务批量（审计段） | ≈7.5 h | **≈45 min** |

---

## 二、范围与边界

### 2.1 改动文件

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/quality_audit.py` | `_audit_factor_monotonicity` 增加缓存读写；新增 `_fm_cache_key/_load/_store`（复用 A2b 的三校验与原子写，抽出共享 helper） |
| `tests/test_audit_fm_cache.py`（新增） | F1–F8 机检 |
| `docs/evidence/audit-fm-cache-<日期>.md`（新增） | 验收证据档 |

### 2.2 明确不改动

- **不改判据**：`LAG` 语义、`1e-9` 容差、`DISTINCT code` 口径、severity（warning）、detail 文案全部不变；
- 不改其他审计段；
- **full 域不缓存**（`self.tables is None` ⇒ 原路径，逐位不变）；
- 不做 aux 库 DDL（加索引属 QFQ 线，见 §2.5）。

### 2.3 缓存键（本方案的**核心技术决策**，已实测）

**实测各候选成本**：

| 候选 | 耗时 | 结论 |
|---|---|---|
| `MAX(time) FROM adj_factor` | **2147 ms** | ❌ 索引是 `(code, time)` 复合，`MAX(time)` 用不上 ⇒ 全扫 |
| `MAX(time) FROM fund_adj` | **916 ms** | ❌ 同上 |
| `MIN/MAX` 两表 | 2750 + 1157 ms | ❌ 更贵 |
| `COUNT(*)` 两表 | 926 + 337 ms（热） | ❌ 30s 级（冷） |
| `PRAGMA data_version` | <1 ms | ⚠️ **连接级**，once 子进程每次新连接 ⇒ 跨进程不可靠 |
| **aux 文件 `(mtime_ns, size)`** | **0.322 ms** | ✅ **采用** |

**最终键**：

```
key = sha256(
    "adj_factor/"  + f"{mtime_ns}:{size}"  of qfq_aux.db
  + "wal/"         + f"{mtime_ns}:{size}"  of qfq_aux.db-wal (不存在则 "0:0")
  + f"tables={sorted(['adj_factor','fund_adj'] 交集 actual)}"
)[:32]
```

**为什么必须含 WAL**：SQLite WAL 模式下，写入先落 WAL，**主库 mtime 不变**；只看主库会漏掉「已写未 checkpoint」的变更。实测当前 `-wal size=0`（已 checkpoint），但运行期会变化。

**已知代价（明示）**：`checkpoint` / `VACUUM` 会改文件 mtime ⇒ **误失效**（宁失效不错；失效后重算一次 ≈371 s，且 TTL 兜底 2 h 内最多一次）。

### 2.4 缓存落盘（复用 A2b 机制，抽共享 helper）

| 项 | 设计 |
|---|---|
| 位置 | `data/_audit_cache/fm/<key>.json` |
| 写入 | 原子写（`.<key>.<pid>.tmp` → `os.replace`） |
| 三项校验 | `schema` / `checksum`（payload 规范序列化 sha256）/ `value.key == 当前键` |
| TTL 兜底 | `QS_AUDIT_FM_CACHE_TTL_S`（默认 7200） |
| 回退 | `QS_AUDIT_FM_CACHE=0` ⇒ 不读不写，**逐位还原** |
| 损坏 | 任一校验失败 ⇒ miss + `fm_cache_corrupt` warning，绝不返回错值 |
| 作用域 | 仅 `self.tables is not None`（task 域）；full 域零读写 |
| 留痕 | 命中追加 `fm_cached`（warning，含 key 前缀 + age_s + 原 issue 数） |

### 2.5 不做但登记（aux 库层的第二层优化）

`MAX(time)` 不 O(1) 的根因是**缺 `(time)` 单列索引**。加 `CREATE INDEX idx_adj_factor_time ON adj_factor(time)` 可让键成本降到 O(1)，但：
- 属 **aux 库 DDL**（QFQ 线领地）；
- 3114 万行建索引需时间与磁盘；
- **本方案不依赖它**（文件 stat 已 O(1)）⇒ 仅作为**建议项**登记，不纳入范围。

---

## 三、任务拆解

### 3.1 功能点分解表（逐点门控 F1→F8）

| 点 | 功能 | 判据 | 负向对照 |
|---|---|---|---|
| **F1** | 键计算（文件 + WAL 双 stat） | 同状态两次调用键相同；**主库 mtime 变 ⇒ 键变**；**WAL size 变 ⇒ 键变**；键计算耗时 < 5 ms | 只改其他文件（如 `batch_audit.db`）⇒ 键**不变** |
| **F2** | 冷态不命中 | 首次 `fm_cached` 不出现，耗时 ≈371 s | — |
| **F3** | 热态命中 | 第二次耗时 **< 5%** 首次，且报告含 1 条 `fm_cached`；其余 issues 逐位相同 | 缓存不存在 ⇒ 无 `fm_cached` |
| **F4** | full 域不缓存 | `tables=None` ⇒ 零读写（spy 断言） | full 域两次耗时相同 |
| **F5** | 三项校验 + 投毒 | 改 payload 不改 checksum ⇒ miss + `fm_cache_corrupt`；改 payload 且改 checksum ⇒ 按契约命中（边界明示） | 截断文件 ⇒ miss 不崩 |
| **F6** | 回退开关 | `QS_AUDIT_FM_CACHE=0` ⇒ 不读不写、两次都重算、结果逐位相同 | 启用/禁用结果同一（仅多/少 `fm_cached`） |
| **F7** | 降级不阻断 | 缓存目录不可写 ⇒ 记 warning、审计照常完成 | 磁盘满/权限错 ⇒ 不抛穿 |
| **F8** | 防回归 | 新增 ≥12 用例全绿；`quality_audit` 系既有全绿 | 删 checksum 校验 ⇒ F5 红；改进程内 dict ⇒ 跨进程用例红 |

### 3.2 实施纪律

写前快照四步；精确文件清单 add；逐点门控；任一红停在该点。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **键漏检**（写入未反映到 mtime/WAL） | 脏命中 ⇒ 漏报非单调 | 键同时含主库与 WAL 的 `(mtime_ns,size)`；TTL 2h 兜底；F1 三条负向全测 |
| **误失效**（checkpoint/VACUUM 改 mtime） | 缓存形同虚设 | 明示为可接受代价（宁失效不错）；若实测过于频繁，再评估改用 `MAX(time)`（3 s 成本）作主键 |
| 缓存投毒/损坏 | 返回错审计结论 | checksum + schema + value.key 三重校验 + `fm_cache_corrupt` |
| 并发写 | 文件半截 | 原子写 + PID 后缀 tmp |
| **把缓存当「跳过检查」** | 非单调检出延迟 | 键捕捉任何 aux 变更 ⇒ 延迟趋零；文档与注释写明论证 |
| 收益被 COUNT/stat 吃掉 | 收益缩水 | 键只用两次 stat（0.6 ms），不用 COUNT/MAX |

---

## 五、验收要点

### 5.1 功能验收
F1–F8 全绿；`test_quality_audit*.py` 既有用例全绿。

### 5.2 场景验收（本机可复跑，全 RO）

| 场景 | 判据 |
|---|---|
| 冷 → 热 | 371 s → **< 20 s**（目标 ≈37 s，含 watermarks 等固定开销） |
| 变更加 WAL（模拟写入） | 键变 ⇒ 重算（耗时回 371 s 量级） |
| full 域 | 零读写、两次耗时相同 |
| 逐位一致 | 命中结果 vs 禁用缓存重算结果，`FactorMonotonicity` 条目逐位相同 |

### 5.3 边界验收
- aux 无 `adj_factor`/`fund_adj` 任一张 ⇒ 不写缓存、不报错；
- WAL 不存在 ⇒ `wal:0:0` 参与键，行为正常；
- `TTL=0` ⇒ 不按时间失效（仅键）。

### 5.4 客户侧验收（观察窗）
「全部执行（增量）」跑一轮 ⇒ 审计段从 ≈7.5 h 降到 **≈45 min**（每任务日志应有 `fm_cached`），总时长单列（受 post-ingest 制约，属 QFQ 专项）。

---

## 六、质量判据

1. **结论零漂移**：命中 vs 重算，`FactorMonotonicity` 条目逐位相同（F3 + 5.2）；
2. **不漏检**：任何 aux 变更（含仅 WAL 内）⇒ 键变 ⇒ 重算（F1 三条）；
3. **损坏可检出**：三校验 + `fm_cache_corrupt`；
4. **可回退**：`QS_AUDIT_FM_CACHE=0` 逐位还原；
5. **跨进程可用**：落盘 + 原子写（73 子进程共享）；
6. **收益可测**：审计段耗时独立判据（≤20 s），总时长单列。

---

## 附一：N8 知识反哺留痕

- `知识反哺：[命中] 已知错误-质量审计OOM-全表窗口无界与范围失控（12-客户运维/已知错误-，2026-10-31）`——本方案是该条「全表窗口无界」缺陷在 `factor_monotonicity` 上的续治。
- `知识反哺：[命中] 审核判据·「零收窄」边界 × 高频调用 = 隐形放大器（全局记忆 1e8b0c32）`——本方案是其「频次>1/轮 必须给复用/缓存」判据的第二次落地。
- `知识反哺：[命中] 两条工程判据·缓存键稳定性（全局记忆 e3502363）`——键必须日/文件粒度稳定，不得用 `now()` 原值。
- `知识反哺：[无命中] 12-客户运维/已知错误（aux 库缓存族）；12-客户运维/案例卷宗（全表 LAG 窗口 × 73 次重算），2026-10-31`

## 附二：与既有工作的关系

| 项 | 关系 |
|---|---|
| A2b（`af25e66`） | 同模式；本方案抽其 helper 复用，不重复实现 |
| Phase 1（`6c73be5`） | 本方案在其审计框架上增量，不改语义 |
| QFQ 增量专项 | 本方案**不改善** post-ingest；总时长仍受其制约 |
| Phase 3 | 本方案不动拉取窗口与限速 |
