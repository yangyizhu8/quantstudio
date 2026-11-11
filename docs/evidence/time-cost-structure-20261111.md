# 耗时结构拆解（R-20261111-04·丙案）

> 裁决：总调度 2026-11-11「sqlwrite 拆解与 R-03（other）合并为『耗时结构』单一立项即刻做
> （只读 1h·DuckDBWriter UPDATE 实现 / ART 参与 / 分批粒度 / 事务边界）」
> 执行：节点⑳，2026-11-11 16:21–16:50（只读，未改任何代码）

---

## 零、一句话结论

**「慢」不是单点瓶颈，是**按表不同**的四种结构；其中最大两块是
`stock_minutes` 的 `sqlwrite`（4,628.2s）与 `stock_daily` 的 `validate`（1,809.8s）。
而 `sqlwrite` 慢的机制已确认：**DuckDB 对带 PK 表 UPDATE 一律改写为 DELETE+INSERT**，
266 万行更新 ⇒ 约 532 万次 ART 索引操作。**

---

## 一、今日（11-11）全表耗时结构（实测）

| 表 | total | other | align | validate | write | **主瓶颈** |
|---|---|---|---|---|---|---|
| `etf_daily` | 716.2s | **433.6s** | 4.9s | 261.2s | 16.4s | **other 60.5%** |
| `stock_daily_valuation` | 63.1s | 30.7s | 7.9s | 0.9s | 23.6s | other 48.7% |
| **`stock_daily`** | **2,310.5s** | 330.0s | 26.1s | **1,809.8s** | 144.6s | **validate 78.3%** |
| `stock_float_share` | 2.7s | 2.3s | 0.2s | 0.0s | 0.2s | — |
| `etf_minutes` | 2,370.0s | 926.4s | 347.0s | 605.5s | 491.1s | other 39.1% |
| **`stock_minutes`** | **8,372.7s** | 2,246.6s | 769.7s | 207.1s | **5,149.3s** | **write 61.5%** |
| **合计** | **13,835.2s**（230.6 分钟）| | | | | |

**⇒ 关键：**没有单一瓶颈**。`stock_daily` 慢在校验（78%），`stock_minutes` 慢在写入（61.5%）。**

### 1.1 `stock_minutes` write 内部拆解

```
write=5149.3s
  ├─ copy        2.4s   (0.05%)
  ├─ invariant 518.3s   (10.1%)
  └─ sqlwrite 4628.2s   (89.9%)  ← 根因
```

## 二、`sqlwrite` 慢的机制（代码实证）

### 2.1 DuckDB 的 UPDATE 语义（`writers.py:L54-56` 注释自述）

```python
"""...一致行不进 delete 相位（DuckDB 对带 PK 表的 UPDATE 一律
   改写为 DELETE+INSERT），吞吐逼近纯插入。"""
```

**⇒ 每一行 UPDATE = DELETE（移除 ART 索引项）+ INSERT（插入 ART 索引项）**
**⇒ 今日 `written=2,662,765` 行 ⇒ 约 **532 万次** ART 索引操作。**

### 2.2 应用层**已经**在用 `changed` 过滤（重要澄清）

**证据（昨日 11-10）**：
```
MinuteDiff: validated=50000 changed=67 unchanged=49933
DuckDBWriter: wrote 67 rows      ← 只写 changed 行
```

**⇒ 所以「应用层没利用 changed」这个假设**不成立**——它已经用了。**

**⇒ `sqlwrite` 慢不是因为「写了不该写的行」，而是**今日真的有 266 万行变化**：**

| 日 | MinuteDiff changed | unchanged | 变化率 |
|---|---|---|---|
| 11-10 | 36,877 | 28,607,725 | **0.13%** |
| **11-11** | **3,588,384** | 31,083,712 | **10.3%** |

### 2.3 但今日变化率畸高（10.3% vs 0.13%），且**末尾批 100% 变化**

```
11:08:30  stock_minutes: validated=50000 changed=50000 unchanged=0
11:08:53  stock_minutes: validated=50000 changed=50000 unchanged=0
11:09:10  stock_minutes: validated=4858  changed=4858  unchanged=0
```

**⇒ `stock_minutes` 最后几批**每一行都变了**。这是**数据层面的异常**，需另查（可能与 A4 重拉 / QFQ 重算有关）。**

**对照**：同轮 `etf_minutes` 首批 `changed=87 / unchanged=49,883`（0.17%）⇒ 正常。

## 三、现成开关：`QS_UPSERT_SKIP_IDENTICAL`（已存在，默认关）

### 3.1 实现（`writers.py:L47-59`）

```python
def _is_upsert_skip_identical_enabled() -> bool:
    """T2 feature gate：upsert 跳过"已存在且逐位一致行"默认关闭。
    fail-closed：仅 QS_UPSERT_SKIP_IDENTICAL 为 "1"/"true"/"on" 时开启；
    开启时：ON CONFLICT DO UPDATE 追加行值元组 IS DISTINCT FROM 条件（NULL-safe）。
    一致行不进 delete 相位，吞吐逼近纯插入。"""
    v = os.environ.get("QS_UPSERT_SKIP_IDENTICAL", "").strip().lower()
    return v in ("1", "true", "on")
```

**消费点**：`writers.py:L2102`（唯一一处）

### 3.2 它的实际收益 —— **比想象的小**

**因为应用层已经只写 `changed` 行，所以进到 SQL 层的行**本来就已经是「变了的」**。**

**⇒ `skip_identical` 在 SQL 层再加 `IS DISTINCT FROM`，对「应用层判 changed 但 SQL 层判一致」的**边界差异行**才有用。**

**⇒ 收益估计：取决于 `MinuteDiff.changed` 与「SQL 层实际差异」的偏差。今日 `changed=358 万` vs `written=266 万`，两者接近 ⇒ **开关收益有限**。**

**⇒ ⚠ 这是一个**需要实测才能定论**的点，不能拍数。**

## 四、事务边界与分批粒度

| 项 | 实测/代码 | 说明 |
|---|---|---|
| **批大小** | `validated=50,000` 行/批 | `MinuteDiff` 与 `DuckDBWriter` 同批 |
| **批数** | `stock_minutes` 438 shard | 昨日 384 |
| **事务边界** | 每批一次 `_write_locked`（`L1896`）| 未见显式 BEGIN/COMMIT（DuckDB 隐式自动提交）|
| **写锁** | `ensure_write_lock(f"writers:write:{table}:{batch_id}")` `L1894` | 操作粒度（每批）|
| **重试** | `_max_retry`（默认 3）+ 退避 | `A6` 有界冲突重试 |
| **CHECKPOINT** | `_is_batch_checkpoint_enabled()`（默认关）| `T1` feature gate |

**⇒ 猜测：每批（50,000 行）一个隐式事务 ⇒ 438 个事务 ⇒ 每事务 200-240 万行？不对（50,000×438=2190 万 = raw）。**

**⇒ 实际 `written=266 万` ⇒ 平均每批写 6,077 行 ⇒ 每批 UPDATE 6,077 行 = 12,154 次 ART 操作。**

## 五、可优化方向（按风险排序）

| # | 方向 | 机制 | 预估 | 风险 |
|---|---|---|---|---|
| **1** | **查「为何今日 266 万行变化」** | 数据层：A4 重拉/QFQ 重算是否导致全量重写 | 若查明可避免重复变化 ⇒ 直接省 77 分钟 | **低（只读）** |
| **2** | 开 `QS_UPSERT_SKIP_IDENTICAL=1` | SQL 层 `IS DISTINCT FROM` 过滤 | **待实测**（应用层已过滤，收益可能很小）| **低（env，可回退）** |
| 3 | 减少 UPDATE：改「整表替换」或「分区重写」 | 若某 shard 内变化率高（如今日 100%），DELETE 整片 + INSERT 比逐行 UPDATE 快 | 大 | **高（改写入语义，触铁律 01/18）** |
| 4 | `stock_daily` 的 validate 优化 | 1,809.8s（78%）——校验逻辑本身 | 大 | 中（触校验口径）|
| 5 | 提高批大小 | 减少事务数 | 小 | 低 |

**⇒ 我方建议：先做 **1**（查「266 万行变化」的成因），它是**只读且可能是最大的免费收益**。**

## 六、与 settle 的关系（不冲突）

| 方面 | settle（明晚） | 本拆解 |
|---|---|---|
| 触的路径 | **判据路径**（`missing_target` 计算）| **写入路径**（`DuckDBWriter._write_locked`）|
| 是否冲突 | ❌ 不冲突 | ❌ 不冲突 |
| 可否并行 | ✅ | ✅ |

## 七、如实划界：本拆解**未**完成的

| # | 未完成 | 原因 |
|---|---|---|
| 1 | **`QS_UPSERT_SKIP_IDENTICAL` 实际收益** | 需开一轮实测，未做（改 env 属生产变更，需裁）|
| 2 | **今日 266 万行变化的成因** | **未查**（下一步建议项 1）|
| 3 | `stock_daily` validate 1809.8s 的内部结构 | 未拆（本轮聚焦 write）|
| 4 | `other` 段（`stock_minutes` 2246.6s / `etf_minutes` 926.4s）的内部 | **未拆**——`_sp` 只有 align/validate/write 三段，`other` 是黑盒 |
| 5 | 事务粒度的精确边界 | 未查 DuckDB 隐式提交的实际行为 |

**⇒ 关键未完成：`other` 段（合计约 3,600s = 60 分钟）仍是**黑盒**。这是 R-03 的原始目标，本轮**未能给出结构**——因为 `_sp` 埋点不够。**

## 八、下一步建议（供裁）

| 选项 | 内容 |
|---|---|
| 甲 | 查「今日 266 万行变化成因」（只读，可能最大免费收益）|
| 乙 | 给 `_sp` 加 `other` 细分埋点（改埋点 → 下轮可见）|
| 丙 | 开 `QS_UPSERT_SKIP_IDENTICAL=1` 实测一轮（改 env，可回退）|

**⇒ 我方倾向甲（零风险）+ 乙（一轮后拿到 `other` 结构）**。
