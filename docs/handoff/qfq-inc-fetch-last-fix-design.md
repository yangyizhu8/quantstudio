# 方案：qfq 增量路径 `_inc_fetch_last` 全表扫修复（P2 · 六步流水线①）

- 立项依据：节点2 2026-11-10「前值块预取改进项照准立项（方案→审→实施·P2·prefetch_factor_times 同型）」
- 关联条目：DB-2026-0042
- 知识反哺：`知识反哺：[无命中] knowledge/db（D1–D5）+ knowledge/iron-rules（主题：SQLite JOIN 子查询计划 / PK 前缀失效）——同主题条目待本条落地后沉淀`

---

## 一、背景与动机

G6 写入维补测（commit `4ba2e34`）测得：同一 120 条输入、同一起点（恢复法），
**旧路径 `=0` 用 0.066 s，增量路径 `=1` 用 74.947 s（慢 1135×）**。
差分布 115,070 键**全 0** ⇒ **语义等价，是纯性能缺陷**。

**根因（实读定位）**：`qfq_observation.py:272-282` 的 `_inc_fetch_last` SQL：

```sql
SELECT o.code, o.factor_time, o.factor_value, o.revision_no
FROM qfq_factor_observation o
JOIN (SELECT code, factor_time, MAX(revision_no) AS mr
        FROM qfq_factor_observation
       WHERE asset_type=? AND code IN (...)      -- 子查询有限定
       GROUP BY code, factor_time) m
  ON o.code=m.code AND o.factor_time=m.factor_time AND o.revision_no=m.mr
WHERE o.asset_type=?                              -- ⚠️ 外层缺 code IN 限定
```

⇒ 外层 `WHERE` 只有 `asset_type`，**JOIN 键是 `code+factor_time`（PK 的第 2、3 列，非前缀）**
⇒ 优化器无法把外层限制在本批 code ⇒ **SCAN 全表（4,500 万行）** ⇒ ≈1.25 s/条。

**影响**：增量开关开启后写入侧净倒退 ⇒ 该开关**事实上不可启用**（当前默认关，风险已隔离）。

## 二、范围与边界

| 项 | 内容 |
|---|---|
| **改动文件** | `quantstudio/pipeline/qfq_observation.py`（`_inc_fetch_last`，:262-287） |
| **代码范围** | 仅 SQL 的外层 `WHERE` 子句 + 参数列表；**函数签名/返回结构/语义不变** |
| **不改动** | 不动 `_inc_touch` / `_inc_cursor_*` / 旧路径（`:442-455`）/ baseline 判定（`:400-405`、`:609-615`）/ 开关默认值（仍 `"0"`） |
| **兼容边界** | 返回 dict 键值不变 ⇒ 上层 `_inc_record_observations` 零改动；旧路径零影响（开关关时不进入本函数） |
| **范围缩放** | 无（不缩遍历范围 ⇒ G5b 仍无需触发） |

## 三、任务拆解 + 功能点分解表

| # | 功能点 | 验收判据（可机检 / 可复跑 / 负向对照） |
|---|---|---|
| **F1** | 外层补 `o.code IN (...)` 限定（与子查询同界） | **A1** `EXPLAIN QUERY PLAN` 中 `_inc_fetch_last` 的外层由 `SCAN qfq_factor_observation` 变为 `SEARCH ... USING PRIMARY KEY (asset_type=? AND code=?)` 或等价的 code 索引访问 |
| **F2** | 返回结果与修复前**逐位相同** | **A2** 同一 aux（副本）上，新旧 `_inc_fetch_last` 对同一批 code 的返回 dict **逐位相同**（含 `factor_value` 浮点、`revision_no`） |
| **F3** | 端到端写入等价 | **A3** `record_observations` 在 `=1` 下新增观测行/alert 行与 `=0` **逐位相同**（复用 `scripts/g6_write_dim_probe.py`，判据 `obs_set_identical` & `alert_set_identical`） |
| **F4** | 性能恢复 | **A4** 副本上 120 条输入：`=1` 的 wall 由 ~74.9 s 降至**与 `=0` 同量级**（目标 ≤0.5 s，即 ≥150× 改善；记录实测值，不设通过阈值以外的主观空间） |
| **F5** | 负向对照（防线有效） | **A5** 构造「故意去掉 code IN」的变体 ⇒ A1 必须**失败**（证明 A1 测的是真东西，非空转） |
| **F6** | 既有测试零回退 | **A6** `tests/test_qfq_observation_incremental.py`（21 passed）+ `tests/test_qfq_g5_effective_dates_batch.py`（6 passed）**全绿** |

## 四、风险描述

| 风险 | 等级 | 规避手段 |
|---|---|---|
| SQL 改写引入语义漂移（漏行/多行） | **中** | A2「新旧返回 dict 逐位相同」为硬门；差异即停 |
| 参数占位符数量不匹配（code IN 出现两次） | 低 | 参数列表按 `[asset_type, *codes, asset_type, *codes]` 构造并用 A2 覆盖 |
| SQLite 版本对 `code IN` 两次使用有参数上限（999） | 中 | 块大小沿用现有 `_inc_batch`（≤256）⇒ 2×256=512 < 999 ✓；并在代码注释标明该约束 |
| 修改影响旧路径 | 极低 | 旧路径不调用本函数（开关关时走 `:400+` 分支） |

## 五、验收要点

- **功能验收**：A1（计划改善）+ A2（逐位相同）
- **场景验收**：A3（端到端等价）+ A4（性能恢复，实测记录）
- **边界验收**：A5（负向对照）+ A6（既有测试全绿）+ 空 codes 早退（`if not codes: return {}` 保留）

## 六、质量判据

1. **纯性能型修复**（铁律 18）：可观察结果（观测行/alert 行/差分布）**逐位不变**，仅执行计划改善 —— A2/A3 为证；
2. **防回归**：A1 + A5 构成「计划级门禁」（正向改善 + 负向能失败），A6 保证既有行为零衰减；
3. **可复跑**：全部判据通过 `scripts/g6_write_dim_probe.py` + 一条 `EXPLAIN QUERY PLAN` 命令复现，不依赖本轮环境；
4. **不夸大**：性能读数在**副本**（4,500 万行真实规模）测得，注明测点位置（`_inc_fetch_last` 函数内计时），禁用「更大数据量下更快」之类外推。

---

## 待审提示

- 本方案仅到「六步流水线①方案」；实施须经②独立审核通过。
- 附带问题：`QS_QFQ_OBS_INCREMENTAL` 是否**永久**保持默认关？本方案不改默认值（仍 `"0"`），开启决定留待 A4 性能恢复后另行裁定。
