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
