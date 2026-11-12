# settle 白名单减免缺陷与修订方案（2026-11-12 R7 裁定）

> 触发：审核方 R7「白名单按日期计数、未与实际缺失键求交及去重的风险仍在。出修订方案及真实 SQL 负例」
> 状态：**已确认缺陷成立**（非理论风险，见 §一代码实证）；本件给方案 + 负例，待审后实施
> 前置：本轮**不新增停启/清 flag 权限**，不触碰运行中 daemon（PID 53244）

---

## 一、缺陷实证（当前代码 `L1151-1181`）

```python
L1166   idx.setdefault(key, []).extend(str(d) for d in (e.get("days") or []))
L1175   days = _KNOWN_VIOLATIONS_CACHE.get((str(code), str(table))) or []
L1178   if after is None: return len(days)
L1181   return sum(1 for d in days if str(d) >= cutoff)
```

### 缺陷 1：未与「实际缺失键」求交 ⇒ **过度豁免**

`sum(1 for d in days if d >= cutoff)` 统计的是「**白名单里登记过的日期**」，而不是
「**实际缺失且被白名单覆盖的日期**」。

**反例**：白名单登记 `(000007, stock_daily, ["2017-05-22", "2017-05-23"])`
- 实际缺失只有 `2017-05-23`（`05-22` 在 fresh 里**已匹配**）
- 当前实现 ⇒ 减免 **2**；正确值应为 **1**
- 后果：`missing_target = 2 - 2 = 0` ⇒ **本应 BLOCK 的单元被误放行**

### 缺陷 2：未去重 ⇒ **扩大豁免额度**

`extend` 直接累加，同一 `(code, date)` 若在多个 entry 或同 entry 的 `days` 里重复出现，
`sum` 会计多次。

**反例**：白名单对 `(000007, stock_daily)` 重复登记 `2017-05-22` 两次
- 实际缺失 1 天
- 当前实现 ⇒ 减免 **2** ⇒ 可抵掉另一天的真实缺失

### 缺陷 3：与冻结边界交叉 ⇒ **重复抵扣**

当前仅用 `d >= cutoff` 按**日期字符串**过滤，未保证该日期**确实落在 missing 集内**。
若某日期既 < `frozen_before`（已被冻结豁免）又被白名单登记，理论上会被二次计入
（当前 cutoff 过滤挡住了大部分，但**不是求交**，边界情形仍可漏）。

**⇒ 审核方要求：「不把『冻结前重复抵扣』列为已证」——即当前实现**未被证明**安全，只能标未验证。**

---

## 二、正确算法（三个条件同时成立才减免）

某 `(code, table, date)` 可被白名单减免，当且仅当：

```
① target 含该 date        —— 库内该表确有这一行（code[/freq] 匹配）
② fresh 不含该 date       —— 它是**真缺失**，不是已匹配
③ 白名单登记了该 date     —— 且 date >= frozen_before（不与冻结豁免重叠）
```

**⇒ 数学形式**：`减免量 = | {date} ∩ MissingSet ∩ WhitelistSet |`（集合求交，天然去重）

## 三、修订实现

### 3.1 新函数（替代计数式白名单）

```python
def _whitelist_days(code: str, table: str) -> set:
    """白名单该 (code,table) 的日期集合（**set 语义，天然去重**）。"""
    # 加载逻辑同现状，但返回 set 而非 list；文件缺失 ⇒ 空集（fail-closed）

def _count_missing_in_whitelist(conn, table, code, frozen_before=None,
                               mfreq=None, view=None) -> int:
    """实际缺失 ∩ 白名单 的去重计数 —— 唯一正确的减免量。

    三条同时满足才计：库内有该 date、fresh 无该 date、白名单登记该 date。
    frozen_before 过滤在 SQL 内完成 ⇒ 与冻结豁免不重叠（不重复抵扣）。
    """
    if not view:
        return 0
    days = _whitelist_days(code, table)
    if not days:
        return 0
    sql = (f"SELECT t.time FROM {table} t "
           f'WHERE t.code=? AND NOT EXISTS ('
           f'  SELECT 1 FROM "{view}" s WHERE s.code=t.code AND s.time=t.time)')
    params = [code]
    if mfreq is not None:
        sql += " AND t.freq=?"
        params.append(mfreq)
    if frozen_before is not None:
        sql += " AND t.time>=?"
        params.append(int(frozen_before))
    missing_days = {_ms_to_date(r[0]) for r in conn.execute(sql, params).fetchall()}
    return len(missing_days & days)      # 求交 + set 去重
```

### 3.2 判据接线变更

```python
# 现状（计数式，有缺陷）
_known = _count_in_whitelist(code, table, after=_frozen_before)
missing_target = max(0, missing_target - _known)

# 修订（求交式）
_known = _count_missing_in_whitelist(
    conn, table, code, frozen_before=_frozen_before, [mfreq=..., ] view=view)
missing_target = max(0, missing_target - _known)
```

**⇒ 注意：`_count_missing_in_whitelist` 需要 `view` 存活 ⇒ 调用点必须在 try 块内**
（与 §2.1 同约束，两处判据的 view 都在 finally unregister）。

### 3.3 保留旧函数

`_count_in_whitelist` 保留（V4 现有测试打桩依赖），但**判据不再调用**；
注释标明「计数式已废弃，仅供兼容测试；正确实现见 `_count_missing_in_whitelist`」。

## 四、真实 SQL 负例（审核方指定的三条）

### 负例 A：白名单含已匹配日 ⇒ 仍须 BLOCK

```
库内 (X, stock_minutes, 1min): [D-1, D, D+1]
fresh:                          [D-1, D]        ← D 已匹配，D+1 缺失
frozen_before:                  D-1
白名单:                          {D}             ← 登记的是"已匹配"的 D，不是缺失的 D+1

target_after   = {D, D+1} = 2      （D-1 被冻结排除）
matched_after  = {D}      = 1
missing_after  = 1                  ← 缺 D+1

白名单求交 = MissingSet{D+1} ∩ {D} = ∅ ⇒ 减免 0
⇒ missing_target = 1 - 0 = 1 > 0 ⇒ **BLOCK** ✅

（当前缺陷实现会算 sum(days>=cutoff)=1 ⇒ missing=0 ⇒ 误放行 ❌）
```

### 负例 B：重复登记同一日 ⇒ 不扩大豁免额度

```
库内: [D, D+1]        fresh: [D]        frozen_before: D
missing_after = {D+1} = 1
白名单重复登记 {D+1, D+1, D+1}（3 次）

求交 = {D+1} ∩ {D+1,D+1,D+1} = {D+1} ⇒ 减免 **1**（set 去重）
⇒ missing_target = 1 - 1 = 0 ⇒ PASS

若漏了 D+2（真实缺失）：
  库内 [D, D+1, D+2]，fresh [D] ⇒ missing = {D+1, D+2} = 2
  求交 = {D+1,D+2} ∩ {D+1} = 1 ⇒ 减免 1
  ⇒ missing_target = 2 - 1 = 1 > 0 ⇒ **BLOCK** ✅
  （缺陷实现：sum=3 ⇒ missing=max(0,2-3)=0 ⇒ 误放行 ❌）
```

### 负例 C：与冻结边界交叉 ⇒ 不误抵扣

```
库内: [D-2, D]        fresh: []        frozen_before: D
target_after = {D} = 1
matched_after = {}  = 0
missing_after = 1                   ← 缺 D
白名单: {D-2}                        ← 登记的是冻结之前的日期

SQL 已过滤 time>=frozen_before ⇒ MissingSet={D}
求交 = {D} ∩ {D-2} = ∅ ⇒ 减免 0
⇒ missing_target = 1 ⇒ **BLOCK** ✅（D-2 的豁免由冻结边界承担，白名单不重复抵扣）
```

## 五、须同步补的集成用例（审核方要求）

审核方：「extra 保留全量 matched 的偏离理由可接受，但须集成用例验证」

**⇒ 补 1 条集成用例**：构造「边界前缺行（被冻结豁免）+ 边界后缺行（真实缺失）」
同时存在时，断言：
- `missing_target` 只反映边界后缺失
- `extra` **不因 after 口径而虚增**（这正是我偏离方案的原因）
- 最终结果为 BLOCK（边界后有真缺失时不能被边界前豁免掩盖）

## 六、本轮不做（按裁定边界）

| # | 不做 | 依据 |
|---|---|---|
| 1 | 不重启/不停止 daemon（PID 53244 在跑）| 「本轮不新增停启权限」|
| 2 | 不删/不改 `backtest_stop.flag` | 同上 |
| 3 | 不跑完整回归（398 文件）| 「须协调专窗，不能与他线生命周期操作或回测并发抢占」|
| 4 | 不断言「冻结前重复抵扣已证」| 未验证，仅标风险 |

## 七、完整回归清单（待专窗执行，本轮只列不跑）

`tests/` 共 **398** 个 `test_*.py`；其中 **49 个**引用 `qfq_reanchor` 相关模块。
本轮已跑 6 个（246 项），**待跑 43 个**：

**最高优先（直接覆盖判据/编排）**
`test_daemon_qfq_integration.py` / `test_qfq_resident_orchestrator.py` /
`test_minutes_write_scoped.*`（`test_minutes_write_scope.py` / `test_minutes_write_conflict.py` /
`test_minutes_conflict_m1m2.py`）/ `test_qfq_bootstrap_gates.py` /
`test_qfq_global_snapshot.py` / `test_qfq_observation_incremental.py` /
`test_qfq_gate_scope.py` / `test_snapshot_lock.py`

**次高（数据/因子/迁移）**
`test_qfq_b4_staging_drill.py` / `test_qfq_b5_generation.py` / `test_qfq_b6_activation.py` /
`test_qfq_b6_wp4.py` / `test_qfq_formal_cutover.py` / `test_qfq_formal_cutover_hard_crash.py` /
`test_qfq_formal_postcutever_audit.py` / `test_qfq_schema_migration.py` /
`test_qfq_schema_status.py` / `test_qfq_schema_22_last_run_id.py` /
`test_qfq_factor_equivalence_e1.py` / `test_qfq_factor_new_date_trigger.py` /
`test_qfq_factor_refresh.py` / `test_qfq_factor_split_storage.py` /
`test_qfq_fresh_capture.py` / `test_qfq_fresh_capture_download.py` /
`test_qfq_aux_route.py` / `test_qfq_event_discovery.py` /
`test_qfq_dividend_payload_full.py` / `test_qfq_empty_minute_range.py` /
`test_qfq_g5_effective_dates_batch.py` / `test_qfq_orchestrator_cli.py`

**其余（间接/交付/写入完整性）**
`test_delivery_pure_gain.py` / `test_delivery_sc008_ab.py` /
`test_firefly_defects_batch1.py` / `test_firefly_defects_batch2.py` /
`test_gui_manual_qfq_cycle.py` / `test_mcp_ckey_filtered_pushdown.py` /
`test_restore_missing_factor_d1_a0_d2.py` / `test_t3_revision_detect.py` /
`test_write_integrity_r123.py` / `test_write_integrity_r2_serial.py`

**⇒ 合计 6（已跑）+ 43（待跑）= 49，与 grep 命中数闭合。**
