# A2b：质量审计「分钟锚点漂移」结果缓存 —— 方案 v1

- 起草：2026-10-31（客户运维线）
- 立项依据：用户 2026-10-31 裁定（A2b 批准，附四项设计修正）
- 判型：**纯性能型**（可观察审计结论不变；缓存命中时读出与实时计算逐位相同）
- 前置：Phase 1 已验收（`6c73be5`）；本方案不含其他改动

---

## 一、背景与动机

### 1.1 现象

Phase 1 实弹暴露：task 域只审 1 张表，但 `elapsed_s = 441.2`（7.4 分钟）。原因是**全局段 `_audit_minute_anchor_drift` 每次都对 2.4 亿行分钟表全表扫描**（实测报出 `stock_minutes/AdjustmentAnchorDrift count=148`、`etf_minutes count=66`）。

### 1.2 放大器

GUI「全部执行」= 73 个独立 once 子进程，**每个子进程各跑一次审计** ⇒ drift 段被重复执行 73 次：

| 场景 | 审计段耗时 | 73 任务总耗时（审计段） |
|---|---|---|
| 现状（Phase 1） | 441s/次 | **≈ 9 小时** |
| 缓存命中后 | 首次 441s，其余 ≈ 秒级 | **≈ 8 分钟** |

> 注意：**总时长还包含数据拉取与 post-ingest**（后者挂死问题另立项）。故本方案的验收**只对「审计段耗时」下硬判据**，总时长待 post-ingest 基线确定后再测（用户修正④）。

### 1.3 为什么缓存是正确的（而非「跳过」）

drift 段的输入只有两个：
1. **分钟表的当前数据**（由 `source_watermark.last_date` 唯一刻划——分钟表写完才推水位）；
2. **除权候选集**（由 `stock_dividend`/`etf_dividend` 的 `ex_date ∈ [now-120d, now+7d]` 决定）。

两个输入都没变 ⇒ 输出必然相同 ⇒ 重复计算纯属浪费。**这不是跳过正确性检查，而是消除同一输入的重复计算。**

---

## 二、范围与边界

### 2.1 改动文件

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/quality_audit.py` | `_audit_minute_anchor_drift` 增加缓存读写；新增 `_drift_cache_key()` / `_drift_cache_load()` / `_drift_cache_store()` 三个私有方法 |
| `tests/test_audit_drift_cache.py`（新增） | B1–B8 机检 + 负向 |
| `docs/evidence/audit-drift-cache-<日期>.md`（新增） | 验收证据档 |

### 2.2 明确不改动

- 不改 drift **判据**：候选窗口（120d/7d）、判别 bar 时段（14:59–15:01）、阈值（0.5%/0.3%）、`AnchorDriftTruncated` 语义、`_clean_factor_segments` 全部不变；
- 不改其他审计段（`_audit_prices` / watermarks / qfq / factor_monotonicity / batch / quarantine）；
- **full 域不缓存**（`self.tables is None` ⇒ 直接走原路径，逐位不可变）；
- 不改 `run_state.quality_audit` 九键结构（仅在 `incomplete` 判定中不含缓存项）。

### 2.3 缓存契约（一等约束，跨进程）

> 用户修正②：73 个任务 = 73 个独立 once 子进程，**进程内缓存无效，必须落盘**。

| 项 | 设计 |
|---|---|
| 位置 | `data/_audit_cache/drift/<key>.json`（目录不存在则创建） |
| **键** | `sha256( f"{asset_type}:{code}" × 排序后的候选集 ‖ 分钟表 watermarks ‖ 除权表 watermarks )[:32]` |
| 键成分①候选集 | `stock_dividend` / `etf_dividend` 在候选窗口内的 code 排序后拼接 |
| 键成分②分钟表水位 | `source_watermark` 中 `stock_minutes` / `etf_minutes` 的 `(source, freq, last_date)` 全行，按 `(source,freq)` 排序 |
| 键成分③除权表水位 | 同上，`stock_dividend` / `etf_dividend` 的行 |
| 值 | `{"schema": 1, "key": "<key>", "created_at": "<iso>", "checksum": "<sha256 of payload>", "payload": {"issues": [...], "truncated": {...}, "rows_scanned": N, "codes_scanned": M}}` |
| 写入 | **原子写**：`tmp = path.with_suffix(".tmp.<pid>")` → `write` → `os.replace(tmp, path)` |
| 读取 | 校验 `schema` 与 `checksum`；任一不符 ⇒ 视为 miss，并记 `drift_cache_corrupt`（warning），不回退到错误结果 |
| 失效 | ①键不匹配（水位/候选集变化）⇒ 自然 miss；②`created_at` 距今 > `QS_AUDIT_DRIFT_CACHE_TTL_S`（默认 7200s）⇒ miss |
| 并发 | 多进程同时 miss 会各自计算并各自原子写（最后写者胜，无损坏）；**不做文件锁**（避免引入锁风险） |
| 清理 | 命中时顺手删除同目录下 TTL 已过期的条目（尽力而为，失败忽略） |

### 2.4 回退与开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `QS_AUDIT_DRIFT_CACHE` | `1`（启用） | `0` ⇒ 不读不写缓存，**逐位还原 Phase 1 行为** |
| `QS_AUDIT_DRIFT_CACHE_TTL_S` | `7200` | 墙钟兜底 TTL（用户修正①：仅作安全带） |
| `QS_AUDIT_DRIFT_CACHE_DIR` | `data/_audit_cache/drift` | 落盘位置（测试用临时目录） |

---

## 三、任务拆解

### 3.1 功能点分解表（逐点门控 B1→B8）

| 点 | 功能 | 落点 | 独立验收判据 | 负向对照 |
|---|---|---|---|---|
| **B1** | 键计算 `_drift_cache_key()`：候选集 + 分钟表水位 + 除权表水位 | `quality_audit.py` | 同一库同一状态两次调用键相同；**推一次水位（改 `source_watermark.last_date`）⇒ 键变化**；增删一个候选 code ⇒ 键变化 | 只改 `update_time`/无关表水位 ⇒ 键**不变**（不得过度失效） |
| **B2** | 缓存读：命中 ⇒ 复用 payload；`drift_cached` 留痕 | 同上 | 第二次调用（同库同状态）耗时 < 首次 5%，且报告含 1 条 `drift_cached`（detail 含 `age_s` 与 `rows_scanned`） | 缓存不存在 ⇒ 无 `drift_cached` 条目（不误标） |
| **B3** | 缓存写：原子写 + checksum | 同上 | 写入后文件可被独立 `json.load` 解析且 `checksum` 校验通过；并发 5 进程同时写 ⇒ 文件始终可解析（无半截） | 手动截断文件 ⇒ 下次读取视为 miss 且记 `drift_cache_corrupt`（不崩、不返回错误结果） |
| **B4** | v1 仅 task 域 | 同上 | `tables=['etf_daily']` ⇒ 读写缓存；`tables=None`（full 域）⇒ **零读写**（用 spy 断言缓存函数未被调用） | full 域两次调用耗时相同（不复用） |
| **B5** | 投毒负向对照 | 同上 | 手工把 payload 里的 `count` 改成 999 且**不更新 checksum** ⇒ 读取判 corrupt ⇒ miss 重算 ⇒ 结果与真实一致 | 手工改 payload **且**同步更新 checksum（等价于伪造完整缓存）⇒ 允许命中（这是契约边界，文档写明：checksum 防意外损坏，不防恶意伪造） |
| **B6** | TTL 兜底 | 同上 | `TTL=1s` + 等待 2s ⇒ 第二次 miss（重算） | `TTL=0` ⇒ 视为「永不按时间失效」（仅键失效） |
| **B7** | 回退开关 | 同上 | `QS_AUDIT_DRIFT_CACHE=0` ⇒ 不读不写（spy 断言）、重复调用均重算、**结果与启用时逐位相同** | 启用/禁用两模式对同一库产出 issue 集合**逐位相同**（仅多/少 `drift_cached` 条目） |
| **B8** | 防回归 | `tests/test_audit_drift_cache.py` | ≥10 用例全绿；`pytest tests/` 中本次相关全绿 | 删掉 checksum 校验 ⇒ B3/B5 必红；把缓存改成进程内 dict ⇒ B4（跨进程）必红 |

### 3.2 实施纪律

- 写前快照四步；精确文件清单 add；
- 逐点门控；任一红停在该点。

---

## 四、风险描述

| 风险 | 影响 | 规避 |
|---|---|---|
| **缓存投毒/损坏** | 返回错误审计结论（最严重） | `checksum` + `schema` 双校验，不符即 miss + `drift_cache_corrupt`；B5 负向对照 |
| **键设计不足 ⇒ 过度失效**（缓存形同虚设） | 无收益 | B1 负向：无关字段变化不得使键失效 |
| **键设计过松 ⇒ 脏命中** | 漏检漂移 | 键含「分钟表水位 + 除权表水位 + 候选集」三面；水位是数据变化的唯一权威刻划 |
| **跨进程并发写** | 文件半截/损坏 | 原子写（tmp+replace，PID 后缀避免 tmp 冲突）；读侧校验失败即 miss |
| **磁盘增长** | `_audit_cache` 无限增 | 键数上界有限（水位组合 + 候选集），且命中时清理过期项；TTL 兜底 |
| **把缓存当「跳过检查」** | 漂移检出延迟 | 水位未动即数据未动 ⇒ 延迟为 0；水位一动即失效。文档与代码注释写明该论证 |
| **收益读数被 post-ingest 污染** | 验收结论失真 | 验收**只对审计段耗时**下硬判据；总时长单列并注明「待 post-ingest 基线」（用户修正④） |

---

## 五、验收要点

### 5.1 功能验收
B1–B8 全绿；既有 `test_quality_audit_anchor.py`（drift 相关 23 例）全绿。

### 5.2 场景验收（本机可复跑）

| 场景 | 判据 |
|---|---|
| **首次计算 → 命中** | 同库连续两次 `_audit_minute_anchor_drift`：第 2 次耗时 < 第 1 次 5%，报告多 1 条 `drift_cached` |
| **水位推进即失效** | 手工 UPDATE `source_watermark.last_date`（副本库）⇒ 第 2 次重算（耗时回到首次量级） |
| **投毒** | 见 B5 |
| **full 域不缓存** | spy 断言 + 耗时相同 |
| **逐位一致** | 同输入下「缓存命中结果」与「禁用缓存重算结果」的 `AdjustmentAnchorDrift`/`AdjustmentAnchorTruncated` 条目**逐位相同** |

### 5.3 边界验收
- 空候选集 ⇒ 不写缓存（无意义），不报错；
- 缓存目录不可写 ⇒ 记 warning、退化为不缓存（**不得因缓存失败而中断审计**）；
- `TTL=0` / `TTL` 极大 ⇒ 行为符合 §2.3。

### 5.4 客户侧验收（观察窗）
- 客户端「全部执行（增量）」跑一轮 ⇒ **审计段耗时从 ~9 小时降到 ~分钟级**（每任务日志应有 `drift_cached`）；
- 总时长读数单列，与 post-ingest 专项读数分开汇报。

---

## 六、质量判据

1. **结论零漂移**：缓存命中 vs 重算，issue 条目逐位相同（B8 + 5.2 逐位一致）；
2. **算不完/算不动的问题不被掩盖**：缓存只消除重复，不改变任何判据与阈值；
3. **损坏可检出**：checksum/schema 双校验 + `drift_cache_corrupt` 留痕，绝不静默返回错值；
4. **可回退**：`QS_AUDIT_DRIFT_CACHE=0` 逐位还原 Phase 1；
5. **跨进程可用**：落盘 + 原子写，73 子进程共享（B4 跨进程用例）；
6. **收益可测且不被污染**：审计段耗时独立判据 + 总时长待基线。

---

## 附一：N8 知识反哺留痕

- `知识反哺：[命中] 已知错误-质量审计OOM-全表窗口无界与范围失控（12-客户运维/已知错误-，2026-10-31）`——本方案是其「全局段放大器」问题的结构性续治。
- `知识反哺：[命中] 审核判据·「零收窄」边界 × 高频调用 = 隐形放大器（全局记忆 1e8b0c32）`——本方案正是该判据「频次>1/轮必须给复用/缓存/降频」的落地。
- `知识反哺：[无命中] 12-客户运维/已知错误（审计结果缓存族）、12-客户运维/案例卷宗（跨进程审计缓存），2026-10-31`

## 附二：与 Phase 1 / 其他立项的关系

| 项 | 关系 |
|---|---|
| Phase 1（`6c73be5`） | 本方案在其之上增量；不改其任何代码路径的语义 |
| QFQ post-ingest 专项 | 独立故障面；本方案**不声称**改善 post-ingest |
| Phase 2（F1–F7 观测面） | 独立；本方案的 `drift_cached` 可作为观测面的一个信号源 |
| Phase 3（分钟拉取性能） | 独立；本方案不动窗口策略与限速 |
