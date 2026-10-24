# 证据：QFQ「分钟表结构性为空」致水位冻结 · 修复实施与验收（2026-10-24）

- 方案件：`docs/qfq-empty-minute-range-design-20261024.md`（v1.1，②审通过 + A9 证伪后增量，三裁）
- 闭环通知底稿：`docs/handoff/customer-notice-qfq-empty-minute-20261024.md`
- 基线：`origin/main = 7508c29`（N4 三笔已出仓）
- 类型：框架层 · **纯增益**（新增检测型 + 纯恢复型；无数据修正型、无 schema 变更）

## 1 改动面（3 源文件 + 109/−21；1 固件 243 行）

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | FP-1 `_security_range` 空表返回 `(None,None)`（原 `(0,0)`）；FP-3c 接线（日线空⇒skip `empty_daily_range`；分钟空⇒`allow_empty_minute=True` 日志留痕）；FP-4 修订 分钟空⇒**跳过** backfill 登记（不写 NULL/不写 0） |
| `quantstudio/pipeline/qfq_fresh_capture.py` | FP-2 `capture(minute_range_ms=None)` ⇒ 跳过分钟采集、`fresh_minute`=带列 0 行、record minute 字段 None；`daily_range_ms=None` ⇒ 显式 ValueError |
| `quantstudio/pipeline/qfq_reanchor_engine.py` | **FP-3a** `apply_reanchor_for_security(..., allow_empty_minute=False)`（默认 False ⇒ 零漂移；仅 `fresh_authoritative_rebase` 可放行；None 仍拒）；**FP-3b** `stage_fresh_authoritative` 空分钟跳过并返回 `minute_absent=True`；**FP-3c** rebase 分支空分钟不 stage + postcheck 留痕 `minute_front_coverage="absent"` |
| `tests/test_qfq_empty_minute_range.py`（新） | A1/A2/A3/A3b/A4/A5/A6/capture-daily-None/A9c×3/A9d/A9d-2 = **12 用例** |

## 2 判据结果（逐点门控）

| FP | 判据 | 结果 |
|---|---|---|
| FP-1 | A1 日线正常+分钟 None；A2 双 None；**A3 负向** 双表有数据逐位一致；A3b 他码不算本码；ETF 对称 | ✅ 7/7 PASS |
| FP-2 | A4 仅 1 次 daily fetch；A5 record minute=None + 保列 + minute_sha256 可算；**A6 负向** 传 Tuple 两表照旧；daily=None 显式报错 | ✅ 14/14 PASS |
| FP-3a | A9c 默认拒（`非空 fresh_minutes`）；A9c² 其他模型拒（`仅支持`）；A9c³ allow 时 None 也拒（`带列的空 DataFrame`） | ✅ 3/3 PASS |
| FP-3b | A9d 未允许 ⇒ `ReanchorBlocked: fresh_minutes_empty`；**A9d-2** 允许 ⇒ `minute_absent=True` + staged_daily 就绪 + minute_partial={} | ✅ 4/4 PASS |
| 固件 | `pytest tests/test_qfq_empty_minute_range.py` | ✅ **12 passed in 2.65s** |

## 3 全量回归 + 负向对照（纯增益硬门）

**全量（62 文件，qfq|reanchor|daemon）**：`1093 passed, 5 failed, 2 skipped`（1488.55s）

**5 项失败逐一定性 —— 负向对照（决定性）**：

```
操作：备份 3 个改动文件 → git checkout HEAD -- <3 files>（恢复改动前）→ 跑 5 项 → 恢复改动（SHA256 校验一致）
基线结果：5 failed, 5 passed in 3.44s
```

| 失败项 | HEAD 基线 | 判定 |
|---|---|---|
| `test_audit_qfq_staleness.py::TestEtfUniverse::test_builder_parses_real_dict_config_and_yields_codes` | ❌ 同样失败 | **既有基线红** |
| `…::test_builder_list_schema_compat` | ❌ 同样失败 | 既有基线红 |
| `…::test_default_run_audit_uses_nonempty_provider` | ❌ 同样失败 | 既有基线红 |
| `test_qfq_range_r1a.py::test_golden_300750_range_vs_count` | ❌ 同样失败 | **既有基线红**（虽涉 range，但非本改动引起） |
| `test_qfq_resident_orchestrator.py::test_require_bootstrap_fail_closed` | ❌ 同样失败 | 既有基线红（与前期登记一致：`bootstrap_required` 字段未置位，日志 WARNING 已正确 fail-closed） |

⇒ **零新增回归**：改动前后失败集合完全相同（5/5 同一批）。**纯增益成立**。

## 4 三型判定

- **FP-1/FP-2/FP-3a/FP-3b/FP-3c/FP-4** = **新增检测型 + 纯恢复型混合**：仅改变「分钟表结构性为空」这一原本**必然 skip** 的路径；对分钟表有数据的路径**逐位不变**（`allow_empty_minute` 默认 `False` ⇒ 引擎默认行为零变化，A9c 负向固化）。
- **无数据修正型**：不改任何已提交数据；`qfq_fresh_capture.minute_range_*` 在空表场景**原本就不会落记录**（capture 前即抛错），故无历史数据迁移面。
- **无 schema 变更**：`minute_range_*` 列本就可 NULL；`qfq_pending_backfill.range_start/end` 为 `NOT NULL`+PK ⇒ FP-4 改为「不登记」而非「写 NULL」，**规避契约变更**（避免触发六件套）。

## 5 待补验（诚实登记，非技术阻塞）

| 项 | 说明 | 补验方式 |
|---|---|---|
| **A9b 端到端 committed** | 需完整日线存量 + fresh 一致数据满足 A/B/C 预检；本批已证到 `stage` 层（`minute_absent=True`）与 apply 入口（A9c 放行/拒绝） | 下次真实 QFQ 周期（或客户侧）观察：`qfq_reanchor_event.status='committed'` 且 `minute_plan` 含 `minute_front_coverage=absent` |
| **A7 水位推进** | 同上，需真实 cycle | 观察 `source_watermark` 前进 |
| 说明 | 生产 daemon（pid 69036）**未重启** ⇒ 内存中仍是旧代码，本批**尚未在生产生效**；生效时点由总调度裁定 | — |

## 6 本轮沉淀（R4）

```
本轮沉淀：N/A（理由：实施轮；条目待 A9b/A7 真实周期补验通过后，随闭环通知一并沉淀入 12-客户运维/已知错误，避免把未端到端验证的结论入库）
```

**知识反哺命中（复用）**：`已知错误-因子版本漂移致rebase自锁`（通用库/12-客户运维）——与本案**同族**（判据设计不当致闸门自锁；被拦的恰是唯一能修复它的操作），其「分层放行 ≠ 提交、后段裁决」框架直接复用于本方案 §4.3。

## 7 A9e 补丁（2026-10-24 15:5x · 生产实证回归 · 修一送一风险当轮闭合）

**生产实证（当日 13:24:01）**：QFQ post-ingest 跑到新代码并失败——

```
qfq_fresh_capture.py:442  ms = _ms_to_yyyymmdd(minute_range_ms[0])
qfq_fresh_capture.py:91   dt = pd.Timestamp(int(ms), unit="ms")
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'
→ [DaemonLifecycle] QFQ post-ingest 未完全通过：status=failed
```

**根因**：`_security_range` 的空表语义是 **tuple `(None, None)`**，orchestrator 原样下传给 `capture`；
而 FP-2 的保护只判 `is not None`（**tuple 非 None ⇒ 通过**）⇒ `minute_range_ms[0]` 为 `None` ⇒ 崩。
**定性：我方 FP-2 未核对调用点实际传值形态的疏漏**（属「修一送一」风险，按铁律当轮修复、不挂账）。

**修复（3 处，双处防御 + 形态归一）**：
1. `qfq_fresh_capture.capture`：双重判定
   `_has_minute = (mr is not None and mr[0] is not None and mr[1] is not None)`；
2. record 的 `minute_range_start/end` 改用 `_has_minute`（原 `is not None` 会在 `(None,None)` 时 `int(None)`）；
3. `qfq_resident_orchestrator` 下传统一归一：`minute_range_ms=(None if _empty_minute else minute_range)`。

**判据 A9e**（参数化 3 形态：`(None,None)` / `(M1,None)` / `(None,M2)`）：不崩、分钟不采集（calls=`['daily']`）、record 字段 None ⇒ **15 passed（5.60s）**。
**生产路径复现**（ETF/160128 同参、直传 `(None,None)`）：不抛、`calls=['daily']`、`record.minute_range_start=None` ✅

**回归**：`test_qfq_fresh_capture` + `test_qfq_resident_orchestrator` + `test_qfq_reanchor_batch1` ⇒ **112 passed / 1 failed**（唯一失败 `test_require_bootstrap_fail_closed` = 已证 HEAD 既有基线红）。

**残留影响（如实申报）**：今日 13:24 那次 QFQ post-ingest 因本缺陷 failed ⇒ **该轮已提交的水位不含 QFQ 增量**（`14:20:49 今日轮次全部成功(74 任务)` 指采集任务，不含 QFQ 附加段）；修复将于下次重启窗生效后由下一真实周期补验 A9b/A7。

## 8 积压风险裁定与**我方判断修正**（2026-10-24 20:19 总调度裁定）

**我方原判断（错误）**：17:06 上报提出「本批未含限流，建议另立项限流」。
**裁定（正确）**：**限流已在位**——FP-7 `max_units_per_cycle=60` 属 firefly 批已上生产。

**代码复核（我方自查，四锚点）**：
| 锚点 | 事实 |
|---|---|
| `qfq_orchestrator_types.py:364` | 默认配置 `"max_units_per_cycle": 60` |
| `:424` / `:485` | 字段默认 `60`；`int(cfg.get("max_units_per_cycle", 60))` |
| `:526-527` | 校验 `>= 0`（0=不限），非法即 `QFQConfigError` |
| `qfq_resident_orchestrator.py:559` | `cap = int(getattr(self.cfg, "max_units_per_cycle", 0) or 0)` |
| **生产实证** | `13:11:11 INFO [qfq_orch] FP-7 配额生效：本轮处理 60 单元，顺延 4576 单元（cap=60；顺延单元保持 pending，不进 apply）` |

⇒ **结论修正**：51,013 积压将按 **60 单元/轮** 消化，不构成"无保护的一次性涌入"。我方"未含限流"表述作废。

**消化速率算术（供后续调参决策，非本批范围）**：51,013 ÷ 60 ≈ **850 轮**。若 QFQ 每日仅 1 轮，则需 ≈850 天；实际速率取决于 daemon 每轮是否都触发 QFQ 段（今日 13:24 有 QFQ 活动即为例证）。**该算术仅作为「首轮实际节奏读数」的对照基线**；按裁定，调参属配置值变更（非代码），届时呈报。

## 9 窗口分工（裁定 2026-10-24）

- **窗口执行方 = 客户运维1**；本会话**不再执行任何停/启**。
- 本件 `docs/handoff/qfq-a9e-reboot-window-20261024.py` 转为**前置门禁**：`--gate-only`
  契约 = `exit 0` 允许重启 / `exit 2` **禁止重启**（fail-closed），供执行方在重启前调用；
  `--check-only` 供事后核验；`--execute` 保留但**仅限执行方**使用。
- `a4cfeaa` 列入**统推 #6**（周日晚随批出仓）。
- A9b/A7 补验窗 = **明晨 06:00 后单码抽样**（选项 C）。
