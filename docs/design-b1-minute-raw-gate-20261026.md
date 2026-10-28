# B-1 precheck 缺口修复①方案：分钟侧第三处 raw 硬闸接入第二级因子等价识别

**日期**：2026-10-26 ｜ **出件**：主仓 dev 会话 ｜ **状态**：**①方案，待 ②审**
**立项**：用户 2026-10-26 六步立项令 ｜ **推送**：⑥ 按计划随 **#8**
**铁律依据**：25 号（上游可变因子判据：结构化版本差 vs 数据损坏）+ 21 号（方案知识反哺留痕）+ 22 号（R1/R4）

---

## 知识反哺检索留痕

知识反哺：[命中] 上游可变因子下的一致性判据（qd/25-上游可变因子判据，2026-10-26）
—— 采纳要点：①**分层语义**（结构化版本差可放行到下一阶段，但放行≠提交，最终正确性由 apply/postcheck 裁决）②**放行≠放宽**（覆盖/NULL/比值判据完全不受影响）③**证据量下限**（<8 条不做结构推断，fail-closed）。本方案的第二级判据与其**同构**，且分钟侧三处闸必须"判据一致"。
知识反哺：[命中] 六步流水线方案①强制知识反哺留痕（qd/21-知识反哺留痕，2026-10-26）
—— 采纳要点：方案正文固定字段 + grep-able 格式。

---

## 一、问题定义（**实测已澄清，与立项描述对齐且更精确**）

### 1.1 分钟侧有**两处** raw 闸，判据不一致

| 处 | 函数 | 位置 | 第二级（因子等价） | 实测 |
|---|---|---|---|---|
| **第一处** | `_check_minute_cov_raw` | L1215（raw 闸 L1263-1285） | ✅ **已接入** | 15:21:44 日志「589300 freq=1min 分钟 raw 不一致 **84513 行**判定为**因子版本等价**（不 block）」 |
| **第三处** | **`apply_fresh_minute_staged`** | L1389（raw 闸 **L1472-1488**） | ❌ **未接入（裸 BLOCK）** | 15:21:55 `qfq_orch 单元诊断 ETF/589300 status=blocked err=逐 bar 不一致 84513 根` |

**⇒ 机理定谳：前闸放行、后闸拦死 ⇒ 白放行。** 同一券同一 84,513 根不一致，第一处判「因子版本等价、不 block」，第三处直接 `raise ReanchorBlocked`，流程终态 `blocked`。

### 1.2 与日线侧的不对称（**立项描述的实质**）

| 侧 | 函数 | 第二级 | 589300 实测终态 |
|---|---|---|---|
| 日线 | `_check_daily_raw_align`（L1071，第二级 L1093-1119） | ✅ | 361 行判等价 ⇒ **放行、未 blocked** |
| 分钟 | `apply_fresh_minute_staged`（L1389） | ❌ | 84,513 行 ⇒ **blocked** |

⇒ **同券日线放行而分钟被拦** ⇒ **分钟侧放行面被系统性压低**（不是"分钟数据更差"，而是**判据不对称**）。

### 1.3 铁证复现（日志原文，可逐字复核）

```
daemon.log:16590  15:21:37 WARNING qfq_reanchor_engine: [qfq_reanchor] 589300 日线 raw 不一致 361 行判定为**因子版本等价**（不 block，front 按现因子重算）：reason=factor_equivalent ratio_median=1.01070039 ratio_p95=1.011169
daemon.log:16591  15:21:44 WARNING qfq_reanchor_engine: [qfq_reanchor] 589300 freq=1min 分钟 raw 不一致 84513 行判定为**因子版本等价**（不 block，front 按现因子重算）：reason=factor_equivalent ratio_median=1.01106501 day_ju…
daemon.log:16592  15:21:55 WARNING qfq_resident_orchestrator: [qfq_orch] 单元诊断 ETF/589300 status=blocked attempt=0 err=freq=1min stored raw OHLC vs fresh(dividend_type=none) raw 逐 bar 不一致 84513 根（首例…
```

**复现方式**（验收时执行）：`Select-String -Path data\logs\daemon.log -Pattern '589300.*(84513|blocked)'` ⇒ 三行同券同时刻序列即铁证。

### 1.4 同族旁证（非孤例）

```
14:49:44  159119 日线 221 行判等价（不 block）
14:49:50  159119 freq=1min 分钟 raw 不一致 50774 行判等价（不 block）
14:51:48  159221 日线 345 行判等价
14:51:54  159221 freq=1min 分钟 raw 不一致 72705 行判等价
```
⇒ 分钟侧 5–8 万根级不一致在 ETF 中**成规模出现**（同一 ETF 因子版本差），第三处裸闸会把这些券**普遍拦死**。

---

## 二、改动范围（单点收口，最小改动）

### 2.1 改动点（**唯一文件/唯一函数**）

`quantstudio/pipeline/qfq_reanchor_engine.py` → `apply_fresh_minute_staged`（L1389）内 raw 闸 **L1472-1488**

**现状代码（裸 BLOCK）**：
```python
_eps = _raw_match_eps_minute(asset_type)
raw_pred = " OR ".join(f"ABS(t.{c} - s.{c}) > {_eps!r}" for c in RAW_COLS)
mism = conn.execute(
    f"SELECT COUNT(*), MIN(t.time) FROM {minute_table} t "
    f"JOIN {staged_minute} s ON t.time = s.time "
    f"WHERE t.code=? AND t.freq=? AND ({raw_pred})", [code, freq_c]).fetchone()
raw_mismatch = int(mism[0])
coverage["raw_mismatch"] = raw_mismatch
if raw_mismatch:
    raise ReanchorBlocked("minute_raw_mismatch", ... "同源前提不成立，整券 BLOCK")
```

### 2.2 目标形态（**镜像 `_check_daily_raw_align` L1093-1119 的三步**）

```python
_eps = _raw_match_eps_minute(asset_type)
raw_pred = " OR ".join(f"ABS(t.{c} - s.{c}) > {_eps!r}" for c in RAW_COLS)
# ① 计数（保留，用于 coverage 与 fail-closed）
mism = conn.execute(...  COUNT(*), MIN(t.time) ...).fetchone()
raw_mismatch = int(mism[0]); coverage["raw_mismatch"] = raw_mismatch
# ② 明细（仅当 n>0 且开关开）—— 供第二级分析
mismatch_df = None
if raw_mismatch and _factor_equivalent_enabled():
    _cols = ", ".join([f"t.{c} AS db_{c}" for c in RAW_COLS] +
                      [f"s.{c} AS fresh_{c}" for c in RAW_COLS])
    mismatch_df = conn.execute(
        f"SELECT t.time AS time, {_cols} FROM {minute_table} t "
        f"JOIN {staged_minute} s ON t.time = s.time "
        f"WHERE t.code=? AND t.freq=? AND ({raw_pred})", [code, freq_c]).fetchdf()
# ③ 第二级裁决：等价 ⇒ 不 block（记 factor_equivalent）；否则 fail-closed
if raw_mismatch:
    verdict = _factor_equivalent_analysis(mismatch_df, asset_type=asset_type)
    coverage["factor_equiv_detail"] = verdict
    if not verdict.get("equivalent"):
        _why = ("" if verdict.get("reason") == "not_analyzed"
                else f"（因子等价识别未通过：{verdict.get('reason')}）")
        raise ReanchorBlocked("minute_raw_mismatch",
            f"freq={freq_c} ... 逐 bar 不一致 {raw_mismatch} 根 ... {_why}",
            coverage=coverage, phase="precheck", freq=freq_c)
    coverage["factor_equivalent"] = True
    logger.warning(
        "[qfq_reanchor] %s freq=%s 分钟 raw 不一致 %d 根判定为**因子版本等价**"
        "（不 block，front 按现因子重算）：reason=%s ratio_median=%.9g",
        code, freq_c, raw_mismatch, verdict.get("reason"),
        verdict.get("ratio_median") or 0.0)
```

**复用点（不新增判据实现）**：
- `_factor_equivalent_analysis(mismatch_df, asset_type)`（L228）—— 已含 **E2 比值有界（ρ∈[1e-3,1e3]）**、**E1′ tick 归一**（`_FACTOR_EQUIV_TICK_MULT=2.0`）、**E5 整段系统性偏移**、**证据量下限 `_FACTOR_EQUIV_MIN_BARS=8`**；
- `_factor_equivalent_enabled()`（L172）—— 保留 kill-switch（env 可关，回退逐位严格）；
- 分钟时间戳为 ms，`_factor_equivalent_day_index(times_ms)` 天然适用（无需改动）。

### 2.3 返回契约变更

`result.factor_equivalent`（L2586 `_b_flags.get("factor_equivalent")`）将**开始为 True**（当前恒 False，因裸闸）。这是**新增观测面**，不是行为放宽：
- 此前：等价 ⇒ blocked（观测不到）
- 此后：等价 ⇒ 放行 + `factor_equivalent=True` 入审计上下文（`audit_ctx`）

---

## 三、★ 不放宽面（fail-closed 边界，逐条明确）

| 判据 | 处理 | 依据 |
|---|---|---|
| **覆盖检查**（missing_target / missing_staged / duplicates / staged_count==target_count） | **完全不变**，任一非 0 仍 `ReanchorBlocked`（在 L1427-1440 及后续覆盖判据处，本方案不触碰） | 25 号铁律「覆盖检查与 NULL/NaN 判据完全不受影响」 |
| **`minute_raw_null`**（stored raw OHLC 存在 NULL/非 finite/>0 违规） | **完全不变**（L1455-1471，本方案在其**之后**） | 同上 |
| **比值不成立**（E2：ρ 越界/非恒正/退化） | **仍 block**（由 `_factor_equivalent_analysis` 返回 not equivalent） | 同上 |
| **跨列比值不一致**（同记录各列比值不同 ⇒ 单列异常 = 损坏） | **仍 block** | 铁律 25 判据①「标量一致」 |
| **比值不连续**（相邻跳变突变 = 损坏，非上游平滑微调） | **仍 block**（E5 整段偏移检测） | 铁律 25 判据②「连续性与有界」 |
| **样本 < 8 条** | **仍 block**（不做结构推断，fail-closed） | 铁律 25 判据③「证据量下限」 |
| **`_factor_equivalent_enabled()==False`**（kill-switch 关） | **退回裸 BLOCK**（逐位严格基线） | 铁律 25「保留双轨回退开关」 |

**⇒ 放行仅限"结构化版本差"（跨列同比值 + 有界 + 连续 + 样本充足），任何"数据损坏"特征仍 fail-closed。**

---

## 四、影响面与纯增益审计（18 号铁律三型判定）

**判型：新增检测型 + 纯恢复型复合**

| 审计项 | 结论 |
|---|---|
| **是否影响其他功能** | 否。改动限于 `apply_fresh_minute_staged` 的 raw 闸；覆盖/NULL/比值判据不动；`_factor_equivalent_analysis` **零改动**（复用） |
| **是否影响回测性能** | 否。第二级仅在 `raw_mismatch>0` 时触发（常态 0 ⇒ 零额外查询）；触发时多一次明细 SELECT（O(mismatch) 行） |
| **是否影响回测精度** | **不降低**：放行的前提是"结构化版本差"，最终正确性由 **apply/postcheck**（`_run_minute_staged_postchecks`）裁决 —— 与日线侧已有分层语义一致 |
| **回退条件** | `QFQ_FACTOR_EQUIV=0`（kill-switch）即退回逐位严格；或 `git revert` 单笔 |

**预期效果**：589300 类券的分钟单元由 `blocked` 转 `committed`（或进入后续 postcheck 正常裁决），分钟侧放行面与日线侧**判据对称**。

---

## 五、验收标准

| # | 判据 | 方法 | 通过线 |
|---|---|---|---|
| 1 | **铁证复现** | `Select-String data\logs\daemon.log -Pattern '589300.*(84513|blocked)'` | 改前：三行序列（daily 放行 / minute 放行 / orchestrator **blocked**）；改后：orchestrator **不再 blocked** |
| 2 | **589300 单元转态** | 重跑该单元或等自然轮 | `qfq_orch 单元诊断 ETF/589300 status=` 不再是 `blocked` |
| 3 | **不放宽面（负向对照）** | 构造①raw NULL ②ρ=1e6 越界 ③样本 5 条 ④跨列比值不一致 四类合成输入 | **四类全部仍 block**，错误码分别为 `minute_raw_null` / `minute_raw_mismatch(reason=ratio_*)` / `reason=insufficient_bars` / `cross_column_inconsistent` |
| 4 | **kill-switch** | `QFQ_FACTOR_EQUIV=0` 重跑 589300 | 退回 `minute_raw_mismatch`（逐位严格基线） |
| 5 | **零回归** | 分钟相关测试套件 + `py_compile` | 全绿 |
| 6 | **日线侧不受影响** | 589300 日线日志 | 仍「361 行判等价（不 block）」 |

---

## 六、R1/R4 留痕

**R1（开工留痕）**：本文 §知识反哺检索留痕已列（2 条命中）。
**R4（验收沉淀，实施轮填）**：预计 `qd-2026-00XX`「分钟侧两处 raw 闸判据不一致 ⇒ 前闸放行被后闸拦死」——含症状/根因/修复落点/验证证据/回哺方式（回哺 = 同族闸判据一致性检查项）。

---

## 七、六步流水线位置

| 步 | 状态 |
|---|---|
| ① 方案（本件） | ✅ 出件 |
| ② 审计 | ⏳ **待 ②审** |
| ③ 实施 | 审计通过后（预计 <30 行改动，单函数） |
| ④ 验收 | 按 §五 六项 |
| ⑤ 用户确认 | — |
| ⑥ 推送 | 按令 **随 #8** |

---

*本件为 ①方案，未实施。实测澄清：分钟侧「已有第二级」的是第一处闸（`_check_minute_cov_raw`），缺口在第三处（`apply_fresh_minute_staged`）；两者判据不一致致"白放行"。*
