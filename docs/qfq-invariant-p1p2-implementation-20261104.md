# P1 + P2 实施方案（待审核）· QFQ 自检因子查询性能

> 状态：**待用户审核**（六步流水线第①步产出；审核通过后进②审计→③实施）
> 上位方案：`docs/qfq-invariant-factor-query-perf-design-20261104.md`
> 用户裁定（2026-11-04 16:55）：**先实施 P1+P2 看效果**；P3 记录立项、暂不实施。
> 环境前置：常驻 daemon 已停（PID 9828，2026-11-04 16:59 强杀），主库锁已释放（只读连接验证通过），
> **WAL 无残留**（铁律 23 免除处置）。

---

## 0 实施基线（改动前实测，用于验收对比）

| 项 | 读数 | 来源 |
|---|---|---|
| `stock_minutes` 表行数 | 246,976,496 | `quantstudio.db` 只读 COUNT |
| aux 库 `qfq_aux.db` | 11,177.7 MB；`adj_factor` 31,467,772 行；`fund_adj` 12,514,155 行 | sqlite 只读 |
| 555 code 全历史查询 | `adj_factor` 3,275,402 行 / 25.4~38.8s；`fund_adj` 2,591,439 行 / 56.1~66.6s | 实测 |
| 单批 shard 时间跨度（样例） | `2026-10-19 09:30:00 ~ 09:39:00`（**10 分钟**） | `daemon.log` conflict 上下文 `tseg=` |
| 当前 invariant 桶 | `stock_minutes` 12,067.8s / 233 批 ≈ 51.8s/批 | 阶段时延日志 |
| 当前整轮耗时 | ≈11.1h（229 批 × 175s） | §2.2b 推算 |

**关键新读数**：单批 shard 只有 **10 分钟**跨度 ⇒ P1 的时间范围过滤会把因子候选集从
「该 code 十余年全历史」压到「**1 天**」量级，实测同类查询（30 天）已快 10~121×，
1 天范围预期 **1~2 个数量级**。

---

## 1 P1 实施细节（时间范围过滤）

### 1.1 改动点（共 2 处，均在 `quantstudio/pipeline/qfq_invariant.py`）

**A. `_load_factor_lookup()`（当前 :276-309）新增可选范围参数**

```python
def _load_factor_lookup(aux_conn, adj_table, codes, *,
                        day_lo_ms: Optional[int] = None,
                        day_hi_ms: Optional[int] = None) -> Dict[Tuple[str, str], float]:
    ...
    sql = (f"SELECT code, time, adj_factor FROM {adj_table} "
           f"WHERE code IN ({placeholders})")
    params = list(codes)
    if day_lo_ms is not None and day_hi_ms is not None:
        sql += " AND time >= ? AND time < ?"
        params += [int(day_lo_ms), int(day_hi_ms)]
    rows = aux_conn.execute(sql, params).fetchall()
```

**B. `check_qfq_invariant()`（当前 :138-244）计算范围并下传**

```python
# 由本批 df 的 time 列推导窗口：本身跨度 ± 冗余（保守 3 天）
day_lo_ms = day_hi_ms = None
if not _full_factors_enabled():        # 回退开关未开
    _t = pd.to_numeric(df["time"], errors="coerce").dropna()
    if not _t.empty:
        _pad = 3 * 86_400_000
        day_lo_ms = int(_t.min()) - _pad
        day_hi_ms = int(_t.max()) + _pad
lookup = _load_factor_lookup(aux_conn, _adj_table_of(table), codes,
                             day_lo_ms=day_lo_ms, day_hi_ms=day_hi_ms)
```

**C. 回退开关**（新增，模块级）

```python
INVARIANT_FULL_FACTORS_ENV = "QS_QFQ_INVARIANT_FULL_FACTORS"   # =1 走原全历史路径

def _full_factors_enabled() -> bool:
    return str(os.environ.get(INVARIANT_FULL_FACTORS_ENV, "0")).strip().lower() \
        in ("1", "true", "yes", "on")
```

### 1.2 等价性论证（为什么结果逐位不变）

原逻辑：候选集 = 该 code 的**全部**因子行 → 按 `(code, bar_day)` 取 `time` 最大者。
新逻辑：候选集 = 该 code 在 `[min(time)-3d, max(time)+3d]` 内的因子行 → 同一规则取最大者。

**不变性成立的条件**：对每个出现在结果里的 `(code, bar_day)`，**它的全部候选因子行**都落在新窗口内。
- `bar_day` 由因子行的 `time` 推导（`_bar_day_from_ms`，日粒度）；
- 结果键 `(code, bar_day)` 只会是**本批 df 里出现过的** bar_day（否则该键不会参与比对）；
- 本批 df 的时间跨度在 `[min(time), max(time)]` 内；该 bar_day 当天任意时刻的因子行，
  其 time 必然落在 `[该日 00:00, 该日 23:59]` ⊂ `[min-3d, max+3d]`。✔
- 3 天冗余进一步覆盖「因子行 time 与 bar 日期口径错位（如 T 日盘后发布、跨时区）」的既有实态。

⇒ `sampled / bad / skipped / unknown_rows` 四个计数与改前**逐位一致**（验收即按此判据）。

### 1.3 不改变什么（边界声明）

- 不改写入、不改水位推进、不改 `_filter_unchanged`、不改告警升级链（streak/阻断阈值）；
- 不改 `aux_path` 解析与路由；
- 不改任何表结构、不改 schema 指纹（铁律 24 不触发）；
- 不改复权计算本身（自检是**观测面**，不参与 front 值生成）。

---

## 2 P2 实施细节（单周期内因子查询缓存）

### 2.1 为什么需要（P1 单独不够的理由）

P1 后单批预计 15~25s，229 批 ≈ 1~1.6h。但**同一交易日的多个 shard（如 09:30~09:39、09:40~09:49 …）
会重复查询同一批 code 的同一天因子**——这类重复占多数，缓存可把「每批一次查询」降为「每日一次查询」。

### 2.2 设计

| 项 | 决定 |
|---|---|
| 缓存存放 | `ResidentCollector` 实例属性（**不跨进程、不落盘**） |
| 键 | `(adj_table, day_lo_ms, day_hi_ms, codes_fingerprint)`；`codes_fingerprint` = 排序后 code 列表的 `sha1` 前 16 位 |
| 值 | `Dict[(code, bar_day), factor]`（即 `_load_factor_lookup` 的返回） |
| 容量 | LRU，**最多 8 条**（防内存膨胀；单条最大约 555 code × 1 日 ≈ 千级键） |
| 生命周期 | **单次 `run_once` / 单个 QFQ 协调周期**；周期结束（`run_once` 返回、`qfq_end_cycle`）**必须清空** |
| 失效边界 | ①跨周期必失效；②`reanchor` 完成后必失效；③aux 库文件 mtime 变化即失效 |
| 开关 | `QS_QFQ_INVARIANT_FACTOR_CACHE=0` 关闭（默认开） |
| 作用域 | **仅用于自检观测**；禁止任何写库/复权计算路径复用该缓存 |

### 2.3 铁律 25 合规声明

复权因子是可变的第三方量。本缓存的**唯一消费者是自检观测**（`check_qfq_invariant`），
不参与 front 值生成、不参与写入决策；即使因子在周期内被上游微调，
影响的也只是「本周期自检读数的时间点」而非数据正确性。
**明确禁止**：把该缓存用于 `aligner` 复权计算或任何写入路径（代码注释 + 测试双固定）。

### 2.4 预期合计效果

| 阶段 | 单批 invariant | 整轮（229 批） |
|---|---|---|
| 现状 | ≈51.8s（实测 12,067.8s/233） | ≈11.1h |
| 仅 P1 | 15~25s | 1.0~1.6h |
| **P1+P2** | **3~8s**（每日首批 15~25s，其余命中） | **0.4~0.7h** |

---

## 3 验收标准（缺一不可）

1. **逐位等价**（核心）：用真实批次回放同一份输入 df + 同一 aux 库，
   `sampled / bad / skipped / unknown_rows` 与改前**完全相等**；`bad_detail` 逐条相同；
2. **性能可测**：`write_split: invariant=` 由 ≈51.8s/批 降至 ≤8s/批（P1+P2），日志为证；
3. **既有测试全绿**：`tests/test_*qfq*` + `test_*delivery*` + 黄金回测零差异；
4. **回退可达**：`QS_QFQ_INVARIANT_FULL_FACTORS=1` 与 `QS_QFQ_INVARIANT_FACTOR_CACHE=0`
   均能恢复改前行为（各一条测试固定）；
5. **缓存不泄漏**：周期结束缓存必为空（测试断言）；aux mtime 变化后必失效（测试断言）。

---

## 4 实施步骤（审核通过后执行）

| 步 | 动作 | 纪律 |
|---|---|---|
| 1 | 按铁律 03 建回退点（`git add` 本次涉及路径 → `git stash create` → `stash store` → `reset`） | 回退点 hash 记入会话 |
| 2 | 改 `qfq_invariant.py`（P1 两处 + 开关）、`daemon.py`（P2 缓存持有与清空） | 精确文件 add，禁 `-A` |
| 3 | 写测试：等价性回放 1 例 + 回退开关 2 例 + 缓存生命周期 2 例 | 新文件 `tests/test_qfq_invariant_perf.py` |
| 4 | 隔离环境跑 `tests/test_qfq_*` + `tests/test_*delivery*` | 两变量隔离，零触碰生产库 |
| 5 | **回放对照**：用同一真实 batch 输入跑改前/改后，比对四项计数 | 差异即停 |
| 6 | 证据落 `docs/evidence/`，报②审计 | 不改码只报 |
| 7 | 通过后再启动 daemon 跑一轮实测，取 `write_split` 真读数 | 需声明占用窗 |

---

## 5 P3 立项记录（用户裁定：记录立项、暂不实施）

**立项号建议**：`ISS-QS-MINUTE-SCHED`（分钟采集排班与吞吐结构优化）

- **范围**：分钟任务在 06:00 采集链中的顺序前置、分钟与日线并行、分钟按日流水线推进；
- **为何单独立项**：触及引擎行为与水位推进语义（铁律 02 禁止与性能修复捆绑）；
- **前置**：P1+P2 实测读数（若已能闭环则 P3 可降级为观察项）；
- **同时登记的关联议题**：`ISS-QS-MINUTE-REPLAY-SKIP`——本轮实测 229 批全部
  「新增 0 + 更新 50000」，即重放在重写已有数据而 `skip_unchanged` 未短路（属数据/短路语义面）。
