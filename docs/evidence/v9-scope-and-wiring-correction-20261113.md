# V9 范围对账 + 回退红闸纠正（R10 四条）

> 触发：R10 审核四条纠正——①legacy 示例测试不是回退红闸 ②40/43/49 须按路径集合对账
> ③00:40 只作前置核验 ④72 分钟重拉纳入既有 A4/P2，不重复立同义项
> 状态：①②**已返工并实测**；③已改提醒；④已撤回措辞

---

## 一、R10 ①：legacy 函数级测试 ≠ 回退红闸（已纠正 + 已补测）

### 1.1 我的错误

`test_negA_legacy_would_mispass` 直接调用 `_count_in_whitelist_legacy(...)`，
断言「旧算法减免 ≥ 真实缺失」。它只证明**旧算法本身会误抵扣**。

**⇒ 若生产判据被改回 legacy 接线，该用例照样绿**（因为它测的是函数，不是判据）。
**⇒ 它不是防复归红闸。R10 判断正确。**

### 1.2 补充：接线级反向验证（新增用例）

```python
def test_wiring_rollback_makes_negA_mispass(self, frozen_at_d, monkeypatch):
    # 模拟接线回退：让判据拿到的减免量 = legacy 计数式结果
    monkeypatch.setattr(eng, "_count_missing_in_whitelist",
        lambda conn, table, code, frozen_before=None, mfreq=None, view=None:
            eng._count_in_whitelist_legacy(code, table, after=frozen_before))
    _stub_whitelist(monkeypatch, {"2026-11-01"})      # 已匹配日（负例 A 构型）
    conn = _mk_minute_db([_T_PRE1, _T_D, _T_POST1])
    # 库内[PRE1,D,POST1] / fresh[PRE1,D] ⇒ 真缺 {POST1}
    # 新实现：求交 {POST1}∩{D}=∅ ⇒ 减免0 ⇒ BLOCK
    # 回退后：legacy 算 1 ⇒ missing=max(0,1-1)=0 ⇒ 误放行
    assert mispass  # 回退 ⇒ 误放行 ⇒ 负例 A（期望 BLOCK）必然变红
```

**实测**：`test_wiring_rollback_makes_negA_mispass` **PASSED**

**⇒ 红闸成立逻辑**：
```
若生产接线回退到 legacy
  ⇒ 本用例证明「回退后不再 BLOCK」
  ⇒ test_negA_new_blocks_when_whitelist_has_matched_day（期望 BLOCK）**变红**
⇒ 两者组合 = 真正的接线回退检测
```

**⇒ 分工定性（已写入测试 docstring）**：
- 函数级测试：证明**旧算法性质**
- 接线级测试：证明**判据接线可被检测**
- **只有后者是红闸**

## 二、R10 ②：路径集合对账（六类清单，不靠 grep 计数）

**同一版本**：`HEAD=0f136dd`；脚本 `v9_scope_recon.py`（读文件内容做引用判定，不用 grep 命中数）

| 类别 | 数量 | 说明 |
|---|---|---|
| **全库** | **398** | `tests/**/test_*.py`（含子目录），其中**未 git 跟踪 2** |
| **相关引用** | **41** | 文件内容含 `qfq_reanchor_engine` / `qfq_reanchor_schema` / `_check_daily_coverage_strict` / `_check_minute_cov_raw` / `qfq_authoritative_rebase` |
| **已跑** | **6** | 见下表 |
| **待跑** | **35** | = 相关引用 − 已跑 |
| **方案 §七 40 文件** | **40 全部存在** | 0 个「列了但不存在」|
| skip | 见 pytest（本轮 `test_qfq_settle.py` 为 1 skip = 前态基线设计性退役；全量 skip 待专窗统计）| — |

### 2.1 差集（关键）

**仅方案列了（未命中相关引用判定）——7 个**
```
tests/test_minutes_write_scope.py          tests/test_minutes_write_conflict.py
tests/test_minutes_conflict_m1m2.py        tests/test_snapshot_lock.py
tests/test_qfq_schema_migration.py         tests/test_delivery_pure_gain.py
tests/test_mcp_ckey_filtered_pushdown.py
```
**⇒ 它们不 import qfq_reanchor 族模块，但方案仍列入（可能经间接路径相关）—— 需保留在范围内。**

**仅相关引用命中（方案未列）——8 个**
```
tests/test_qfq_authoritative_rebase.py     tests/test_qfq_factor_equivalence.py
tests/test_qfq_gate_scope.py               tests/test_qfq_observation_incremental.py
tests/test_qfq_reanchor_batch1.py          tests/test_qfq_reanchor_batch2.py
tests/test_qfq_reanchor_empty_fresh_skip.py tests/test_qfq_settle.py
```
**⇒ 前 6 个即我已跑的那批；后 2 个（`gate_scope` / `observation_incremental`）应补入待跑。**

### 2.2 正确 V9 范围（裁定用）

```
V9 范围 = 相关引用 ∪ 方案40 = 41 ∪ 40 = 48 个文件（去重后）
  已跑 6
  待跑 42 = 35（相关引用待跑）+ 7（仅方案有）
```

**⇒ 我此前说的「40 / 43 / 49」均不准确。正确是 48，待跑 42。**

### 2.3 原全库验收要求不被替代

**R10 明确：原全库验收要求不由 grep 命中数替代。**
**⇒ 本件只做**范围界定**，不宣称 V9 通过。**
**⇒ 全库 398 是否全跑，需总调度裁定「V9 正本口径 = 48 相关 / 398 全库」。**

## 三、R10 ③：00:40 只作前置核验

**已改提醒**：
- 00:40 若库仍被 daemon 独占（22:36 实测 `File is already open in PID 53244`）
  ⇒ **改期**，报告实测错误原文
- **不停 daemon、不清 flag**（无停启权限）
- 06:00 轮已开始 ⇒ 同样改期

**⇒ 00:40 不承诺跑完 48 文件，只做前置核验 + 能跑则跑。**

## 四、R10 ④：撤回「72 分钟立项」措辞

**我原话**：「这正是『消化不完』的量化机制之一，**建议立项**（可与 settle 效果同批评估）」

**⇒ 撤回「立项」措辞**，改为：
- 72 分钟重拉是**已观察成本**，纳入**既有 A4 / P2 责任链**，**不重复立同义项**
- **不据此确认 aux WAL 写入因果**（我只观察了时间与 written=0，未追 WAL）

## 五、本轮实测汇总

| 项 | 读数 |
|---|---|
| `pytest tests/test_qfq_settle.py` | **24 passed, 1 skipped**（新增接线级用例后 +1）|
| 新增用例 | `test_wiring_rollback_makes_negA_mispass` **PASSED** |
| 对账脚本 | `%TEMP%\qs_scripts\v9_scope_recon.py` → `docs/evidence/v9-scope-reconciliation-20261113.json` |
| HEAD | `0f136dd`（对账时）|
| 代码改动 | **仅测试文件**（+1 用例），未动产线代码 |
| daemon / flag | **零触碰** |
