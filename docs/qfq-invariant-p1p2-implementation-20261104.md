# P1 + P2 实施方案（待审核）· QFQ 自检因子查询性能

> 状态：**待用户审核**（六步流水线第①步产出；审核通过后进②审计→③实施）
> 上位方案：`docs/qfq-invariant-factor-query-perf-design-20261104.md`
> 用户裁定（2026-11-04 16:55 + 17:2x 追加）：**P1 + P2 + ISS-QS-MINUTE-REPLAY-SKIP 三件一并完成**，
> 且 REPLAY-SKIP 优先级**不低于** P1+P2；P3（排班）记录立项、暂不实施。
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

### 2.3b 【审核补充条件 2 · 设计修正】失效机制改为「显式事件为主、mtime 为周期级兜底」

**审核质疑（成立）**：SQLite 文件 mtime 在**任何写入**后都会变，包括 daemon post-ingest 写因子。
若每批 invariant 都 stat 一次 mtime，则「post-ingest → mtime 变 → 下一批缓存被清」会形成
**逐批 miss 死循环，P2 装了等于没装**。

**修正后的失效机制（三级，从强到弱）**：

| 级 | 触发 | 实现 | 说明 |
|---|---|---|---|
| **①显式事件（主）** | daemon **写完因子后主动调用** `invalidate_factor_cache(reason="post_ingest")` | 进程内函数调用，**确定性、零轮询** | 唯一的"正确性"失效路径；不依赖文件系统时间戳 |
| **②周期边界（次）** | 每个 `run_once` **开始时**记录一次 `aux_mtime_baseline`；**下个周期开始**再比一次 | 每周期**仅 2 次 stat**，周期内不 stat | 兜底跨进程外部写入（他线/host 工具改了 aux 库）；**不逐批触发** |
| **③显式清空** | 周期结束 / `reanchor` 完成 | `clear_factor_cache()` | 生命周期终点必清 |

**关键约束（写死进代码注释 + 测试）**：
- **禁止在批次内 stat mtime**（这是本修正的核心：逐批 stat 必然逐批 miss）；
- 命中率必须可观测：`hit / miss / evict` 三计数，**每批写日志**、周期结束落 `run_state`；
- 验收必须以**真实批次序列的命中率**为准（见 §3.6），不得只看"缓存对象已创建"。

### 2.4 预期合计效果

| 阶段 | 单批 invariant | 整轮（229 批） | 备注 |
|---|---|---|---|---|
| 现状 | ≈51.8s（实测 12,067.8s/233） | ≈11.1h | — |
| **仅 P1** | 15~25s（同口径实测 **15.3×**） | 1.0~1.6h | ✅ **已验证**（11-05 真机） |
| ~~P1+P2~~ | ~~3~8s~~ | ~~0.4~0.7h~~ | ❌ **实测无效**（forever 轮 hit_rate=0%，见 §2a.2）——**从收益清单移除** |
| **P1 + REPLAY-SKIP** | **<5s**（无变化批直接 0 写入） | **≤0.3h**（且写入量塌缩） | ✅ 已验证（11-05：省写入 77.61%） |

> **本表 2026-11-05 修订**：原「P1+P2」行基于「forever 轮同日多批可命中缓存」的假设，
> 该假设已由 11-05 真机**证伪**（§2a.2）⇒ P2 从收益清单移除，代码与开关保留（不撤回）。
> P1 与 REPLAY-SKIP 两件的收益均已在真机验证。

---

### 2a.1 【真机实测后补·已被 §2a.2 覆盖】P2 适用范围初步判断（2026-11-04 23:1x）

> ⚠ **本节结论已被 §2a.2 推翻**（保留原文作认知演进留痕，勿引用本节结论）。

**实测读数**：`--mode once` 单任务轮 → `[qfq] 因子查询缓存 hit=0 miss=2 evict=0 hit_rate=0.0%`

**原因（不是 bug，是场景不匹配）**：`--mode once` 按**逐日**处理（1 天 = 1 整批 ≈125 万行），
每批 `lo_ms/hi_ms` 不同 ⇒ 缓存键 `(adj_table, lo_ms, hi_ms, code指纹)` 每批必然不同 ⇒ **必然 miss**。

| 运行模式 | 批次结构 | P2 是否有效 |
|---|---|---|
| `--mode once`（单任务/单轮） | 1 天 = 1 批 | ❌ **无效**（键必异，hit_rate≈0） |
| `--mode forever`（常驻轮） | 1 天切成多批（如 11-04 那轮 229 批 × 5 万行） | ✅ 有效（同日多批命中） |

**处置**：**不撤回实现**（开关 `QS_QFQ_INVARIANT_FACTOR_CACHE` 保留、默认开；forever 轮仍受益）；
仅在验收判据中**按运行模式区分**——once 模式**不以 hit_rate 为通过条件**，forever 模式维持
`hit_rate ≥ 50%`（原 §3.6.2 判据仅适用于 forever 轮）。

> 教训（已入知识条目）：性能改动的收益必须**绑定到具体运行结构**陈述，
> 不能笼统写"加缓存可再降一个数量级"——批次结构不匹配时收益为零。

---

### 2a.2 【2026-11-05 真机终判】P2 **实测无效**——假设被证伪，从收益清单移除

**真机读数**（11-05 06:05 forever 轮，全程 07:27~12:1x，316 批）：

```
[qfq] 因子查询缓存 hit=0  miss=278  evict=270  hit_rate=0.0%  (size=8)
```

**被证伪的假设**：§2a.1 曾判断「`--mode forever` 轮 = 1 天切成多批 ⇒ 同日多批可命中」。
**实测并非如此**：forever 轮每批是**不同时间段顺序推进**（10:00→10:10→10:20…），
缓存键 `(adj_table, lo_ms, hi_ms, code指纹)` 每批都变 ⇒ **键结构性不重复** ⇒ 命中率**恒为 0**。

**运维附注 2（机制补充·总调度 2026-11-05 裁定纳入）**：`size=8` 是 LRU 容量上限，
`evict=270` 说明键**持续涌入并挤出旧键**——即使偶有重复键，也会在 8 个新键之内被淘汰。
⇒ 根因是**键空间与批次结构不匹配**（每批一个全新时间窗），容量不是主因但会**加速**失效。

**处置（不撤回实现）**：
| 项 | 决定 |
|---|---|
| 代码 | **保留**（已测链路稳定，删除会破坏已验收的 P1 联动） |
| 开关 `QS_QFQ_INVARIANT_FACTOR_CACHE` | **保留**（默认开，零副作用） |
| 文档 | 标注「**实测无效·结构性 0 命中**」，**从收益清单移除**（§2.4 已改） |
| 验收判据 | **不再以 hit_rate 为通过条件**（原 §3.6.2 的 `≥50%` 判据**作废**） |

> **为什么保留而不删**：删代码属行为变更，需另走六步；而保留一个"命中率恒 0、无副作用"的层
> 成本为零（一次字典查找/批），且若将来批次结构改变（如按 code 分片而非按时间分片）它可能重新生效。
> **负结果的正确处置是标注清楚、移出收益、保留退路**，不是抹掉痕迹。

## 2b ISS-QS-MINUTE-REPLAY-SKIP（第三件，与 P1+P2 同级）

### 2b.1 根因（配置面实锤，2026-11-04 取证）

| 项 | 实测 |
|---|---|
| `config/profiles/mcp_only/collector_tasks.json` 中 `mcp_stock_minutes` | `dataset_kind = **None**`、`skip_unchanged = **None**` |
| 代码门（`daemon.py:3596-3600`） | `if … and task.get("dataset_kind") == "snapshot" and task.get("skip_unchanged", False):` |
| 判定 | 分钟任务**两个条件都不满足** ⇒ `_filter_unchanged_snapshot_rows()` **对分钟任务从不执行** |
| 现场后果 | 229 批全部 `wrote 50000 rows（**新增 0 + 更新 50000**）` ⇒ **100% 是无效重写**（库内已有相同内容仍全量 upsert） |

⇒ 这条不是"写得太慢"，而是**根本不该写**。收益面**大于** P1+P2（P1+P2 只省自检；本条省掉整个写入 + 连带的 invariant）。

### 2b.2 改动设计（解耦 snapshot 语义，新增独立开关）

分钟表是**时序表**（新 bar 追加 + 历史 bar 可能被上游修正），不宜直接标 `dataset_kind="snapshot"`。
因此**不动 snapshot 语义**，而是把"按行去重"能力解耦成独立开关：

```python
# daemon.py:3596 附近
_skip = bool(task.get("skip_unchanged", False))
_eligible = (task.get("dataset_kind") == "snapshot"
             or task.get("dedupe_unchanged") is True)     # ← 新增：显式允许时序表按行去重
if (df is not None and len(df) > 0 and _eligible and _skip):
    df = self._filter_unchanged_snapshot_rows(
        df, table, exclude=task.get("change_compare_exclude", ["update_time"]))
```

配置侧（仅两个任务，精确改动）：

```json
{"name": "mcp_stock_minutes", "skip_unchanged": true, "dedupe_unchanged": true}
{"name": "mcp_etf_minutes",  "skip_unchanged": true, "dedupe_unchanged": true}
```

**全局回退开关**：`QS_MINUTE_DEDUPE=0` ⇒ 视为 `dedupe_unchanged=False`（逐位回到现状）。

### 2b.3 等价性论证（为什么是纯性能型）

- `_filter_unchanged_snapshot_rows()` 的语义是「**内容与库内完全相同**的行不重写」；
- 这些行被跳过 ⇒ 库内**最终状态不变**（行本已存在且内容相同）；
- 因此属铁律 18 的**纯性能型**（可观察结果不变），非数据修正型；
- **必须显式记录的两个语义点**（验收件中单列，防将来误报）：
  1. 跳过写入 ⇒ 该行 `update_time` **不刷新**（`exclude` 只影响比对，不产生写入）。
     若下游有「按 update_time 判定新鲜度」的消费者，其判定口径会变为「最后一次**内容变更**时间」；
  2. 分钟表若出现「上游修正历史 bar」的重拉，**内容变化**的行仍会被正常写入（过滤按内容比对，不按时间比对）。

### 2b.4 验收（独立于 P1/P2，各自可回退）

1. **库内零变化**：同一批输入，改前 vs 改后对 `stock_minutes` 该批覆盖面做
   行集 `sha256`（按 `code,time,freq,open,high,low,close,volume` 排序）**逐位相同**；
2. **写入量下降可测**：日志由 `更新 50000` 变为 `更新 0`（或显著下降），单批耗时由 ≈175s 降至 <10s；
3. **混合批正确**：构造「一半行内容相同 + 一半行内容变化」的批次 ⇒ 只写变化行，且库内最终内容正确；
4. **回退可达**：`QS_MINUTE_DEDUPE=0` 恢复现状（一条测试固定）。

### 2b.5 三件合并实施的风控（替代"拆分"）

铁律 02 要求性能与正确性修复拆分。本次按用户裁定**同批实施**，以三条替代风控满足其实质：

| 风控 | 内容 |
|---|---|
| ① **独立提交** | P1 / P2 / REPLAY-SKIP 各一笔 commit，禁止混为一笔 |
| ② **独立开关** | `QS_QFQ_INVARIANT_FULL_FACTORS` / `QS_QFQ_INVARIANT_FACTOR_CACHE` / `QS_MINUTE_DEDUPE`，任一可单独回退 |
| ③ **独立验收** | 三份验收读数各自成表；任一件不达标只回退该件，不牵连其余两件 |

## 3 验收标准（缺一不可）

1. **逐位等价**（核心）：用真实批次回放同一份输入 df + 同一 aux 库，
   `sampled / bad / skipped / unknown_rows` 与改前**完全相等**；`bad_detail` 逐条相同；
2. **性能可测**：`write_split: invariant=` 由 ≈51.8s/批 降至 ≤8s/批（P1+P2），日志为证；
3. **既有测试全绿**：`tests/test_*qfq*` + `test_*delivery*` + 黄金回测零差异；
4. **回退可达**：`QS_QFQ_INVARIANT_FULL_FACTORS=1` 与 `QS_QFQ_INVARIANT_FACTOR_CACHE=0`
   均能恢复改前行为（各一条测试固定）；
5. **缓存不泄漏**：周期结束缓存必为空（测试断言）；显式 `invalidate_factor_cache` 后必失效（测试断言）；
   **批次内不得 stat mtime**（用 monkeypatch 计数断言 stat 次数 ≤ 周期数×2）；
6. **命中率达标**：真实序列 hit_rate ≥ 50%（§3.6.2）；
7. **窗口外因子语义**：极端边界用例结果 = unknown 且与全历史路径一致（§3.6.1）。

---

### 3.6 验收增补（审核补充条件，必须各自成条）

**3.6.1 极端边界用例：长期停牌后复牌 ⇒ 窗口外因子必须落到 `unknown`（设计语义，非缺陷）**

- 构造：某 `code` 的 bar 落在 D 日，但其因子行最新时间在 D-10 日（超出 ±3 天窗口）；
- 断言：结果与「全历史路径」**逐位一致**——即该行被判 `unknown_rows += 1`（未找到因子），
  而**不得**出现 `bad`（错误值）、也不得抛异常；
- **必须在验收件中显式写明**：「窗口外因子 ⇒ unknown 是**设计语义**，不是缺陷」——
  预置防线：防止将来有人把它当 bug 报（与 D1「审计归因细分位」同类误报模式）。

**3.6.2 缓存命中率实测（不得只验"对象已创建"）**

- 必须跑**真实批次序列**（≥20 个连续 shard），采集 `hit / miss / evict` 三计数；
- 通过判据：**hit_rate ≥ 50%**（同交易日多 shard 场景预期 80%+）；
- 若 hit_rate < 50% ⇒ 视为失效机制未生效，**停线归因**（不得带着"永远 miss 的缓存"进验收）；
- 读数落 `docs/evidence/`，并附 aux mtime 在周期内的**变化次数**（证明未逐批 stat）。

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

## 5 P3 立项记录（用户裁定：记录立项、暂不实施；REPLAY-SKIP 已上提为实施件见 §2b）

**立项号建议**：`ISS-QS-MINUTE-SCHED`（分钟采集排班与吞吐结构优化）

- **范围**：分钟任务在 06:00 采集链中的顺序前置、分钟与日线并行、分钟按日流水线推进；
- **为何单独立项**：触及引擎行为与水位推进语义（铁律 02 禁止与性能修复捆绑）；
- **前置**：P1+P2 实测读数（若已能闭环则 P3 可降级为观察项）；
- **关联议题 `ISS-QS-MINUTE-REPLAY-SKIP` 已按用户 2026-11-04 追加裁定上提为实施件**（见 §2b），
  与 P1/P2 同级一并完成；本条 P3 仅保留「排班/并行/流水线」范围。
