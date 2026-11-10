# A1 基线（修复前执行计划）+ A4 前置实测（2026-11-11 00:08）

- 依据：节点2「副本 EXPLAIN 基线预置照准（A1 升级同库同 SQL 前后对照）」「②审＝通过（条件：A1 前后对照+基线先行）」
- 环境：副本 `D:\qs_g6_replica_20261110\qfq_aux.db`（45,122,444 行），**只读**（`mode=ro` + `query_only=ON`）
- 规模：20 个真实 STOCK code；两版 SQL 均返回 **115,070** 行

---

## 一、A1 执行计划前后对照（同库同 SQL，仅差外层 `code IN`）

### 修复前（当前实现 `:272-282`）

```
(2,   0, 0, 'CO-ROUTINE m')
(8,   2, 0, 'SEARCH qfq_factor_observation USING COVERING INDEX sqlite_autoindex_qfq_factor_observation_1 (asset_type=? AND code=?)')
(119, 0, 0, 'SEARCH o USING INDEX sqlite_autoindex_qfq_factor_observation_1 (asset_type=?)')   ← 仅 1 列
(128, 0, 0, 'BLOOM FILTER ON m (factor_time=? AND code=?)')
(141, 0, 0, 'SEARCH m USING AUTOMATIC COVERING INDEX (factor_time=? AND code=?)')
```

### 修复后（外层补 `AND o.code IN (...)`）

```
(2,   0, 0, 'CO-ROUTINE m')
(8,   2, 0, 'SEARCH qfq_factor_observation USING COVERING INDEX sqlite_autoindex_qfq_factor_observation_1 (asset_type=? AND code=?)')
(119, 0, 0, 'SEARCH o USING INDEX sqlite_autoindex_qfq_factor_observation_1 (asset_type=? AND code=?)')  ← 2 列 ✓
(198, 0, 0, 'BLOOM FILTER ON m (factor_time=? AND code=?)')
(211, 0, 0, 'SEARCH m USING AUTOMATIC COVERING INDEX (factor_time=? AND code=?)')
```

### ⇒ A1 判据（`SCAN` → `SEARCH ... code=?`）**达成**

差分只在**外层 o 的索引使用列数**：`(asset_type=?)` → `(asset_type=? AND code=?)`。
子查询侧（`CO-ROUTINE m`）前后完全一致 —— 说明问题确实只在**外层缺限定**。

---

## 二、A2 结果一致性

| 项 | 读数 |
|---|---|
| 修复前 rows | **115,070** |
| 修复后 rows | **115,070** |
| `sorted(old) == sorted(new)` | **True** |

⇒ **A2 达成**（逐位相同）。

---

## 三、🔴 A4 实测：**不达**

| 版本 | wall | 相对 |
|---|---|---|
| 修复前 | **76.184 s** | 1× |
| 修复后（仅补外层 `code IN`） | **7.112 s** | **10.7×** |
| **方案 A4 目标** | **≤ 0.5 s** | **需 ≥152×** |

⇒ **A4 不达（7.112 s vs 0.5 s，超 14 倍）**。

### 归因（新发现，方案未覆盖）

补 `code IN` 只解决了「**扫描范围**」（全表 → 20 code），**没解决「返回行数」**：

- `_inc_fetch_last(c, asset_type, codes)` 的语义是「取这批 **code** 的全部 `(code, factor_time)` 最新行」
- 20 个 code × 约 5,753 个 `factor_time` ⇒ **返回 115,070 行**
- 而上层只消费输入涉及的 **120 个 `(code, ft)` 键**
- ⇒ **99.9% 的返回行被丢弃**，7.1 s 主要是「取多了 + 传多了」的成本

### 两条补救路径（呈裁）

| 路径 | 做法 | 预期 | 风险 |
|---|---|---|---|
| **甲** | **把 `(code, factor_time)` 对也限定**——SQLite ≥3.15 支持行值 IN：`WHERE (o.code, o.factor_time) IN ((?,?),...)`，参数 `2×N`（N≤256 ⇒ ≤512 < 999） | 返回行数 115,070 → **120**，wall 预期**毫秒级** | 行值 IN 的查询计划需实测（可能不走索引，需先 EXPLAIN 验证）；SQL 复杂度上升 |
| **乙** | **按 code 分块**：每块只取**该 code 输入涉及的 ft**（`code=? AND factor_time IN (...)`) 逐 code 查（pandas 式「每 code 一次」） | 往返 O(code 数)=20，但每次返回仅 ~6 行 ⇒ 总计仍快 | 往返数从 O(1) 回到 O(code 数)，与 G1 的「批量」初衷背离（但 20 次仍远优于 20×1.25s） |
| **丙** | 保持 F1 现状（7.1 s），把 **A4 阈值重定**为「≤10 s」或「相对改善 ≥10×」 | 已达成 10.7× | 7.1 s 下开启增量仍**比 old 0.066 s 慢 100×** ⇒ **开关仍不可启用**，等于放弃该改进项的实际价值 |

**我方倾向：甲**（先 `EXPLAIN` 验行值 IN 是否走索引，走则实施；不走则退乙）。

---

## 四、本轮未做（如实申报）

| 项 | 状态 | 原因 |
|---|---|---|
| F1 代码修改 | **未做** | A4 实测不达硬门 ⇒ 依铁律「与既有裁定冲突先指出」，**先呈裁再动手**，不擅自降阈值或跳过 |
| A3 / A5 / A6 | **未做** | 依赖 F1 落地；F1 未落地则无对象可验 |
| commit | **未做** | 无代码改动 |

---

## 五、复现命令（可机检）

```powershell
# 全量只读；副本 aux 必须存在
python -c "
import sqlite3,time
con=sqlite3.connect(r'file:D:/qs_g6_replica_20261110/qfq_aux.db?mode=ro',uri=True)
con.execute('PRAGMA query_only=ON')
codes=[r[0] for r in con.execute(\"SELECT DISTINCT code FROM qfq_factor_observation WHERE asset_type='STOCK' ORDER BY code LIMIT 20\")]
ph=','.join('?'*len(codes))
old=('SELECT o.code,o.factor_time,o.factor_value,o.revision_no FROM qfq_factor_observation o JOIN ('
     'SELECT code,factor_time,MAX(revision_no) mr FROM qfq_factor_observation '
     'WHERE asset_type=? AND code IN ('+ph+') GROUP BY code,factor_time) m '
     'ON o.code=m.code AND o.factor_time=m.factor_time AND o.revision_no=m.mr '
     'WHERE o.asset_type=?')
new=old[:-len('WHERE o.asset_type=?')]+'WHERE o.asset_type=? AND o.code IN ('+ph+')'
newsql=old.replace('WHERE o.asset_type=?','WHERE o.asset_type=? AND o.code IN ('+ph+')')
for name,sql,p in (('old',old,['STOCK']+codes+['STOCK']),('new',newsql,['STOCK']+codes+['STOCK']+codes)):
    print(name, [r[3] for r in con.execute('EXPLAIN QUERY PLAN '+sql,p)][2])
    t=time.perf_counter(); n=len(con.execute(sql,p).fetchall()); print(name, round(time.perf_counter()-t,3),'s rows',n)
"
```

2026-11-11 00:12
