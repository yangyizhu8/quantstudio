# settle 丙方案实施验收（2026-11-12 窗口）

> 裁定：总调度 R-20261112-01 裁**甲**——今晚 16:00 轮收工后实施，窗口至 20:00
> 窗口开启核准：**15:48** 轮次 14:10:10 结束 / 库锁已释放（258,422,297 行可读）/ daemon 已退出
> 方案：`docs/qfq-settle-design-20261110.md`（四版审定）

---

## 零、验收总表

| 项 | 判据 | 状态 |
|---|---|---|
| **V1** | 边界前有缺 ⇒ 豁免且 pass | ✅ **PASSED** |
| **V2** | 边界后**有缺** ⇒ 不豁免（防回归核心）| ✅ **PASSED** |
| **V3** | 拿不到边界 ⇒ 不豁免 | ✅ **PASSED** |
| **V4** | 白名单命中 ⇒ 减 | ✅ **PASSED** |
| **V5/V5b** | 白名单/边界文件缺失 ⇒ 不减 / fail-closed | ✅ **PASSED** |
| **V6/V6b** | `QS_QFQ_FROZEN_BEFORE=0` 退回严格 / `=1` 返回边界 | ✅ **PASSED** |
| **V7** | 豁免必留 `history_exempt` 痕迹 | ✅ **PASSED** |
| **V8** | **回测哈希逐位一致** | ✅ **PASS**（`6ff884a9…` 三策略全一致）|
| **V9** | 既有回归全绿 | ⏳ 跑中 |
| **V10** | 实跑放行率 0% < x < 90% | ⏸ 明日 06:00 轮观察 |

**单测**：`pytest tests/test_qfq_settle.py` ⇒ **18 passed, 1 skipped**（skip = 前态基线按设计退役）

## 一、实施内容（`f75b9a8`）

**文件**：`quantstudio/pipeline/qfq_reanchor_engine.py`（+240/−12）

### 1.1 新增 6 个函数（按方案 §3.3 签名，未自造）

| 函数 | 职责 | fail-closed |
|---|---|---|
| `_ms_to_date` | epoch ms → `YYYY-MM-DD` | — |
| `_load_frozen_bounds` | 读边界 → `{(asset,code): ms}`（模块级缓存）| 文件缺失/损坏 ⇒ **None** |
| `_resolve_frozen_before` | 方案 §3.3 原文 | env=0 / 文件缺 / code 不在 ⇒ **None** |
| `_count_target_after` | 边界后 target 行数 | — |
| `_count_matched_after` | 边界后 matched 行数（需 view 存活）| view=None ⇒ 0 |
| `_count_in_whitelist` | 白名单命中数（`after` 过滤）| 文件缺失 ⇒ **0（不减）** |
| `_reset_frozen_bounds_cache` / `_reset_known_violations_cache` | 测试用清缓存 | — |

### 1.2 两处判据改造（只改 `missing_target` 的分子分母）

```
_check_daily_coverage_strict  → after 口径 + 白名单串联 + history_exempt 留痕
_check_minute_cov_raw         → 同上
```

## 二、三处实现关键点（方案伪代码未点明，属实现约束）

### 2.1 view 存活期（否则 BinderException）

两处判据的 `view` 都在 `finally` 里 `unregister`，而 `missing_target` 在 finally **之后**计算
⇒ **after 计数必须在 try 块内完成**。

### 2.2 ⚠ 与方案的有意偏离：保护 `extra` 语义

方案 §3.2 改动 2 写「覆盖 `target_count` / `matched`」，但：

```python
extra = staged_count - matched   # 若 matched 被替换为 after 口径
                                 # ⇒ 「边界前已匹配的行」被算成多余
                                 # ⇒ V1 场景误报「多 2 行」而 BLOCK
```

**⇒ 本实现只把 after 口径用于 `missing_target`，`extra` 仍用全量 `matched`（语义不变）。**
已在代码注释写明偏离理由。

### 2.3 分钟 view 无 `freq` 列

分钟 view 只注册 `code/time/RAW_COLS`（L1231）⇒ 只能过滤 `t.freq`，**不可写 `s.freq`**
（首次运行即 `BinderException: Values list "s" does not have a column named "freq"`）。
与原判据 L1237-1240 口径一致。

## 三、测试 fixture 修正（首轮 3 失败的真因）

**现象**：V1/V4/V7 首轮 FAILED，报「缺 2」（即走了严格路径）。

**真因（**不是实现缺陷**）**：测试合成 `code='X'`，而真实 `data/qfq_frozen_bounds.json`
里并无 `'X'` ⇒ `_resolve_frozen_before` **正确地** fail-closed 返回 None ⇒ 永远测不到豁免语义。

**修正**：新增 `frozen_at_d` fixture，**只打桩 `_resolve_frozen_before` 这一个纯函数**
（判据本体与 SQL 全走真实实现）；V2 也改用该 fixture，确保它测的是「边界后有缺」
而非「严格路径 BLOCK」的重复。

## 四、V8 回测哈希比对（已 PASS）

**基线**：`docs/evidence/v8-settle-baseline-20261112.json`（04:36 捕获，`6ff884a9…`）
**比对**：16:31–16:54，同三策略同窗口

| 策略 | now | exp | 判定 |
|---|---|---|---|
| `etf_theme` | `a9d8c1c8…` | `a9d8c1c8…` | ✅ |
| `smallcap` | `6702c722…` | `6702c722…` | ✅ |
| `overnight_s7`（minute-bar-v1）| `bf738a9e…` | `bf738a9e…` | ✅ |
| **overall** | **`6ff884a9…`** | **`6ff884a9…`** | ✅ **PASS** |

**⇒ 结论**：settle 判据改动**不改变回测产物**（回测路径不调 `qfq_reanchor_engine`，
本次实测确认无间接影响）。

## 五、回退（双路在位，未使用）

| 路径 | 方式 | 验证 |
|---|---|---|
| ① 环境开关 | `QS_QFQ_FROZEN_BEFORE=0` ⇒ 全部退回严格 | **V6 已测 PASSED** |
| ② 代码回退 | `git checkout -- <file>`（写前快照：HEAD 锚 `4f8e93f` + `%TEMP%\qs_settle_backup_20261112_154925` 字节级备份）| 文件本净，可直接恢复 |

## 六、待完成

| # | 项 | 时点 |
|---|---|---|
| 1 | **V9 既有回归全绿** | 跑中（`pytest tests/`）|
| 2 | **V10 实跑观察**（放行率 0% < x < 90%）| **明日 06:00 轮**（须先重启 daemon）|
| 3 | **重启 daemon**（加载 settle + fetch 埋点修正）| 收工前（≈20:00）|
| 4 | 水位推进验证（候选已到 2026-11-11，held 解除后应一次性推进 26–68 天）| 明日轮后 |
