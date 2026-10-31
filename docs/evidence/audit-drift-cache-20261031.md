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

## 四、生产实测：A2b 真实收益远低于预估（2026-10-31 17:20）

> 用户裁定改判：C 项不用 GUI 双任务（省掉 60 分钟 once 挂死税），改**审计内核双重运行**（`DataQualityAuditor(db, tables=['etf_daily']).run()`，只读连接，不与 daemon 抢写）。

### 4.1 冷/热对照（清缓存后连跑两次）

| 轮次 | 缓存 | 耗时 | issues | drift_cached |
|---|---|---|---|---|
| run1（冷，drift 全扫） | miss | **346.52 s** | 12 | False |
| run2（热，命中缓存） | hit | **299.37 s** | 13 | **True** |

**差值 = 47.15 s（占冷态 13.6%）**

### 4.2 结论修正（如实申报）

| 项 | 方案预估 | 生产实测 | 偏差 |
|---|---|---|---|
| 命中后耗时 | < 1 s | **299 s** | ❌ 严重偏离 |
| 单次节省 | ≈ 441 s | **≈ 47 s** | ❌ 约 1/9 |
| 73 任务批量节省 | ≈ 9h → 8min | **≈ 73 × 47s ≈ 57 分钟** | ❌ 量级不同 |
| drift 占 task 域比重 | 全部 | **13.6%** | ❌ 前提错误 |

**根因**：`_audit_minute_anchor_drift`（drift）只是 task 域审计的一部分；命中缓存后**其余 ≈299 s 仍在**。方案期把「441 s ≈ drift 耗时」当成了前提（依据是「单表审计 441s + drift 是唯一扫分钟表的段」），**未做冷/热分离实测**即下结论。

**责任归属（自查）**：A2b 方案的收益估算属**未经实测的推断**，且我在方案里写成「预期」而未标注「待实测」的强度差别不够——应写成「**未验证假设：drift 是 441s 的主体**」并在实施前先做一次冷/热对照。

### 4.3 缓存机制本身的验证（仍达标）

| 判据 | 结果 |
|---|---|
| 冷态不命中 | run1 `drift_cached=False` ✓ |
| 热态命中并留痕 | run2 `drift_cached=True`，issues 12→13（多出那条留痕）✓ |
| 结论一致性 | 两次除 `drift_cached` 外的 issues 需逐位比对（**待补**） |
| 只读安全 | `run()` 自开 `read_only=True`（`quality_audit.py:210`），daemon 20912 并存无冲突 ✓ |

### 4.4 新的诊断方向（299 s 在哪）

task 域（仅 `etf_daily` 一张表）仍需 ≈300 s ⇒ 时间不在 drift、也不在表循环（1 张 221 万行表），**必在全局段或初始化**。候选：

- `watermarks` / `qfq_orchestration` / `factor_monotonicity` / `batch_pipeline` / `quarantine` 五个全局段；
- `run()` 的 `SHOW TABLES` 与 schema 探测；
- `_audit_prices` 内除 anchor 外的其他检查（`AdjustmentCompleteness` / `AdjustmentFactorConsistency` / `ReturnConsistency`）。

**需要一次分段计时**才能定位（可与 Phase 3 合并，或作为 A2b-2 小任务）。

## 五、待补读数（下一步）

1. 冷/热两次的 issues 逐位比对（除 `drift_cached` 外应完全相同）；
2. task 域分段耗时分解（定位 299 s）；
3. ~~真实库命中实测~~ ✅ 已完成（如上）。

## 六、沉淀日期

2026-10-31

