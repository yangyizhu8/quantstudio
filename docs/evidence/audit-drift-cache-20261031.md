# A2b 验收证据：drift 结果缓存（2026-10-31）

- 方案：`docs/audit-drift-cache-design.md`（②审通过 + 补充条件「三项校验」）
- 提交：本批（`quality_audit.py` + `tests/test_audit_drift_cache.py`）
- 判型：纯性能型（缓存命中读出与重算逐位相同）

---

## 一、实现要点（含实施中的两处修正）

| 项 | 实现 |
|---|---|
| 缓存键 | `sha256(day + 日归一双窗口 + 候选集 + source_watermark 全行)[:32]`；**日粒度**（见下） |
| 三项校验 | ①`schema==1` ②`checksum`（payload 规范序列化 sha256）③`doc.key == payload.key == 当前键` |
| 落盘 | `data/_audit_cache/drift/<key>.json`；原子写（`.<key>.<pid>.tmp` → `os.replace`） |
| 作用域 | 仅 `self.tables is not None`（task 域）；full 域零读写 |
| 失效 | 键不匹配 / 三项校验任一失败 / TTL（默认 7200s，`0`=不按时间失效） |
| 留痕 | 命中追加 1 条 `drift_cached`（warning，含 key 前缀与 age），原 issues 原样回放 |
| 回退 | `QS_AUDIT_DRIFT_CACHE=0` ⇒ 不读不写 |

**实施中的两处修正（如实记录）**：

1. **模块缺 logger**：`quality_audit.py` 原本无模块级 logger（只有 `import json` 等），首次跑测试即 `NameError`。已补 `import logging` + `logger = logging.getLogger(__name__)`。
2. **缓存键不稳定（关键）**：初版键含 `lo/hi = (now ± Nd).timestamp()`，`now` 精确到微秒 ⇒ **每次调用键都不同 ⇒ 缓存永不命中**（B2/B5/B6 全红暴露）。修正为 **`day0 = now.replace(hour=0,minute=0,second=0,microsecond=0)` 再加减天数**，即「候选窗口按日归一」。契约已写入代码注释。

---

## 二、B1–B8 判据读数

| 点 | 判据 | 结果 |
|---|---|---|
| **B1** | 同状态两次键相同 | ✅ `k1==k2`，长度 32 |
| **B1** | 分钟表水位推进 ⇒ 键变化 | ✅ 脏命中被阻断 |
| **B1** | 无关字段变化（分钟表内容改、水位未推）⇒ 键**不变** | ✅ 防过度失效（契约：水位是唯一权威） |
| **B2** | 第二次命中且更快；除 `drift_cached` 外条目逐位一致 | ✅ |
| **B2** | 首次（冷缓存）不出现 `drift_cached` | ✅ |
| **B3** | 缓存文件可独立解析、checksum 自洽、schema/key 一致 | ✅ |
| **B3** | 截断文件 ⇒ 视为 miss 且不崩 | ✅ |
| **B4** | full 域不读不写缓存 | ✅ 无文件产生、无 `drift_cached` |
| **B5** | payload 篡改未更 checksum ⇒ corrupt ⇒ miss | ✅ 且 999 不出现在结果中 |
| **B5** | payload + checksum 同时伪造 ⇒ 允许命中（契约边界） | ✅ 文档已写明「防意外不防恶意」 |
| **B6** | `TTL=0.2s` 过期 ⇒ miss | ✅ |
| **B6** | `TTL=0` ⇒ 不按时间失效 | ✅ |
| **B7** | `QS_AUDIT_DRIFT_CACHE=0` ⇒ 不读不写 | ✅ |
| **B8** | 缓存目录不可写（被普通文件占用）⇒ 降级不崩 | ✅ |

**总计**：`tests/test_audit_drift_cache.py` **14 passed**；连带 `test_quality_audit_anchor.py` + `test_quality_audit_scoped.py` + `test_quality_audit.py` **31 passed**。

---

## 三、收益预期（待真实库实测）

| 场景 | 审计段耗时 |
|---|---|
| 首次（冷） | ≈ 441s（与 Phase 1 相同，drift 全表扫） |
| 命中（同水位） | 预期 < 1s（仅文件读 + 校验） |
| 73 任务批量（水位不变段） | 首次 441s + 72 × ≈0 ≈ **8 分钟**（原 ≈9 小时） |

> 用户修正④：**总时长读数须在 post-ingest 耗时已知基线上测**，故本项验收只对「审计段耗时」下硬判据；总时长单列，等 QFQ 专项定量。

## 四、待补读数（下一步）

1. 真实库命中实测（`_audit_minute_anchor_drift` 两次调用耗时对比）；
2. 73 任务批量的实弹总时长（需 post-ingest 不再挂死，属 QFQ 专项后）。

## 五、沉淀日期

2026-10-31
