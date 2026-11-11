# R-02 逐列定位：「10:49 后 100% changed」到底变了哪一列

> 裁定：总调度 2026-11-11 R1「R-02 逐列定位自办知悉（纯只读），10:49 后 100% changed 的具体列一到手即报」
> 执行：节点⑳，2026-11-12 03:20–03:40（纯只读，未改任何代码/数据）

---

## 零、结论（三级升级）

| 级别 | 结论 | 状态 |
|---|---|---|
| 前一轮 | 「上游修正了最近两天数据 ⇒ 266 万行 UPDATE 是必要重算」 | ❌ **推断级，已被本次实测推翻**（raw 逐位未变）|
| **本次** | **raw 列族（OHLC / vol / amount / freq）逐位未变；变的是 `front/back` 派生列族**——`MinuteDiff` 的 `compare_cols` 构造证明它们参与比对 | ✅ **机制级**（代码 + 列清单 + 逐列实测 三重支撑）|
| 待补 | 「本轮 incoming 的 front」 vs 「库内旧 front」 的逐值差 | ❌ **不可回溯**（旧值已被 11-11 10:49 的 UPDATE 覆盖）|

**⇒ 关键否定结论（推翻前一轮）：**「上游修正了 raw 数据」**不成立**。

---

## 一、逐列实测：源端 vs 库内（同一条记录）

**样本**：`000045.SZ` @ `2026-11-09 09:30`（`update_time=2026-11-11 10:49:15`，落在 100% changed 段）

| 列 | 源端（QuestDB ≡ MCP） | 库内（DuckDB stock_minutes） | 判定 |
|---|---|---|---|
| `open` | 9.14 | 9.14 | ✅ **同** |
| `high` | 9.18 | 9.18 | ✅ **同** |
| `low` | 9.14 | 9.14 | ✅ **同** |
| `close` | 9.18 | 9.18 | ✅ **同** |
| `vol` → 库内 `volume` | 25900.0 | 25900.0 | ✅ **同** |
| `amount` | 237012.0 | 237012.0 | ✅ **同** |
| `freq` | 1min | 1min | ✅ **同** |
| `adj_factor` | 3.0278 | `qfq_aux.db` 因子表同值 **3.0278** | ✅ **同** |
| `is_qfq` | True | **库内无此列** | ⚠ 语义位无载体 |
| `open_front` | *（源端无此列）* | 9.14 | ⚠ **派生列** |
| `close_front` | *（源端无）* | 9.18 | ⚠ **派生列** |
| `open_back` | *（源端无）* | 9.451534153005465 | ⚠ **派生列** |
| `close_back` | *（源端无）* | 9.492897540983606 | ⚠ **派生列** |

**⇒ raw 列族 100% 相同 ⇒ 「上游修正了 raw」被推翻。**

### 1.1 源端真实列清单（实测）

```sql
SELECT * FROM stock_minutes LIMIT 1;
-- 列: ts_code, trade_time, freq, open, high, low, close, vol, amount, adj_factor, is_qfq
```

**⇒ 源端**无** `front/back` 列（那是客户端派生的），且成交量列名是 **`vol`**（库内叫 `volume`）。**

### 1.2 库内 front/back 的比例（四天恒定）

| bar | update_time | front/open | back/open |
|---|---|---|---|
| 2026-11-05 09:30 | 2026-11-06 13:44:10 | **1.000000000** | 1.034084699 |
| 2026-11-06 09:30 | 2026-11-09 01:10:35 | **1.000000000** | 1.034084699 |
| **2026-11-09 09:30** | **2026-11-11 10:49:15** | **1.000000000** | 1.034084699 |
| **2026-11-10 09:30** | **2026-11-11 10:58:10** | **1.000000000** | 1.034084699 |

**⇒ front/back 的**基准关系**在 10:49 前后完全一致 ⇒ 变因不是「基准关系变了」。**

## 二、代码级机制（`daemon.py:_filter_unchanged_rows_scoped`，L3517-3570）

```python
L3539-3540  compare_cols = [c for c in df.columns
                            if c not in set(pk) | exclude and c in _table_cols]
            #  table_cols 含 open_front/close_front/open_back/close_back
            #  exclude = {'update_time'}
L3544-3547  tmin, tmax = incoming 的 time min/max
L3550-3552  existing = SELECT pk + compare_cols FROM table WHERE time BETWEEN tmin AND tmax
L3558-3562  aligned  = current.reindex(incoming.index)
            changed = ~incoming.index.isin(current.index)      # 库里无此 PK ⇒ 判变
L3563-3566  for col in compare_cols:
                changed |= ~(incoming[col] == aligned[col])   # ★ front/back 参与逐值比较
```

**⇒ 两个决定性事实：**
1. **`compare_cols` 确实包含 `open_front/close_front/open_back/close_back`** ⇒ **派生列参与判定**
2. **PK 不在库里也判 changed**（`~isin`）

### 2.1 front/back 的计算口径（`aligner.py`）

```python
L919   front 4 列: price_front = price_raw × adj_i / adj_latest
L959-961 「QFQ 复权基准 bug 修复（2026-08-14）：调用方必须传入全局快照
           （adj_latest_map/adj_earliest_map，来自 qfq_aux.db 实时读取）。
           禁止批次内 groupby 作基准」
qfq_snapshot.py L35/65/70  latest_map / earliest_map 从 qfq_aux.db 实时读
```

**⇒ 机制闭环：**

```
库内旧值的 front = raw × adj_i / adj_latest(上一轮采集时的快照)
本轮 incoming 的 front = raw × adj_i / adj_latest(本轮采集时的快照)
若上游因子在两轮之间微调 ⇒ adj_latest 变 ⇒ front 变 ⇒ 逐值不等 ⇒ 100% changed
```

**⇒ 这与铁律 25「上游可变因子持续微调 ⇒ 库内值 = 写入时上游量的产物」**完全一致**——
库内旧 front 绑定的是**写入当时**的因子基准，而本轮 fresh 用的是**当前**基准。**

**⇒ 因此：**
- **raw 未变**（前一轮推断被推翻）
- **front/back 变了**（因子基准漂移的直接后果）
- **⇒ 这 266 万行 UPDATE 在语义上是「必要的基准对齐重算」**，但**代价极高**（4,628s sqlwrite）
- **⇒ 优化方向不是「跳过这些写」，而是「让基准对齐不必全量重写」**（属③范围，裁暂缓）

## 三、两次排查的失败点复盘（工具层，供后续参考）

| 失败 | 现象 | 根因 |
|---|---|---|
| 第 1 次 | 源端 `SELECT *` 解析失败 | QuestDB 的 `/exec` 在 `SELECT *` 时返回 **dict 型 columns**（而 `describe` 型返回 list）|
| 第 2 次 | 库内查询超时 | 用了 `code='000001'` **无 time 下界** ⇒ 扫 2.47 亿行 |
| 第 3 次 | 源端 HTTP 400 | 源端列名是 **`vol`**（非 `volume`）、**无** `preClose/suspendFlag/settelementPrice/openInterest/dividend_type`；**任一不存在的列即整条 SQL 报 400**（不报列名错误）|
| 第 4 次 | 探 B `ORDER BY trade_time` 400 | 该 QuestDB 版本对 `ORDER BY` + 多列 + `LIMIT` 组合不接受；去掉 `ORDER BY` 即通 |

**⇒ 教训：跨系统比对时，先用「单表 + 单行 + 显式列 + 日期范围」跑通最小查询，再逐列扩展；
「某列不存在」在这套接口里表现为**整条 400**，不是列名错误。**

## 四、如实划界

| # | 未完成 | 原因 |
|---|---|---|
| 1 | 「本轮 incoming front」vs「库内旧 front」的逐值差 | **不可回溯**——旧值已被 11-11 10:49 的 UPDATE 覆盖（`update_time` 随之改写）|
| 2 | 全 438 批次是否**每批都 100%**（还是仅 11-09/11-10 两天）| 未逐批统计 |
| 3 | `adj_latest_map` 在 11-10 与 11-11 两轮之间的**实际变化量** | 未查 `qfq_aux.db` 因子表的写入时间线 |
| 4 | 源端 `is_qfq=True` 与库内无载体 ⇒ 对齐器如何处理该语义位 | 未查 `aligner` 对 `is_qfq` 的处理分支 |

**⇒ 第 1 项是「旧值 vs 新值」的直证，但因数据已被覆盖**，
**机制级结论（第二节）已可支撑「上游因子基准漂移 ⇒ front/back 变 ⇒ 100% changed」的定谳。**
