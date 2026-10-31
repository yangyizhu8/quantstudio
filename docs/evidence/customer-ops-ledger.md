# 客户运维线 · 台账（持续更新）

> 用途：与调度侧台账**对称留档**。调度侧条目（A1/A2 等）在我侧有对应指针，避免两侧口径分叉。
> 更新纪律：每笔落地即登记；状态只用「已闭环 / 进行中 / 待补观察 / 已移交」四态。

---

## 一、A1 / A2 条目（调度对称留档）

| 编号 | 事项 | 落点 | commit | 状态 |
|---|---|---|---|---|
| **A1** | **审计 `AdjustmentAnchor` 查询重写 + 分钟表按 code 分批（A1b）** | `quality_audit.py` `_audit_prices` | `6c73be5` | 已闭环 |
| **A2** | **分钟锚点漂移有界化（候选上限 + 分批 fetch + 截断留痕）** | `quality_audit.py` `_audit_minute_anchor_drift` | `6c73be5` | 已闭环 |

> A1/A2 的关键读数（供两侧对账）：
> - A1：合成 10 场景 new≡legacy 逐位相同；真实库 `etf_daily` 5=5、`stock_daily` 0=0；**A1b 分批后分钟表 1.41 亿行 32.9s / 2.46 亿行 91.8s 无 OOM**（单语句在 1.41 亿行仍 OOM 7.4GiB）。
> - A2：截断可见（30 code / 上限 20 ⇒ `AnchorDriftTruncated count=30`）；legacy 无截断；候选 < 上限时集合相同；分批 fetch 等价。

---

## 二、S4 每日读数接口（定稿）

| 项 | 内容 |
|---|---|
| 状态 | **定稿**（调度照准我方草案为会签基线 + 补 `poison_count`；dev 四签注已获） |
| 文档 | `docs/evidence/s4-daily-readout-spec-20261031.md` |
| 字段 | 9 项：`date / table_name / watermark_date / max_time_date / gap_days / a4_action / window_count / last_error_class / poison_count` |
| 频次落点 | 每日一轮一次；`data/logs/gap_daily_<yyyyMM>.jsonl`（每表 1 行）+ `run_state.gap_snapshot`（汇总 1 行） |
| **dev 四签注** | ① `gap_days` = **交易日**（与判据 D 口径一致）② `a4_action` **加第四值 `no-baseline`**（我发现的「A4 跳过检测：无 last_sync 基准」类）③ JSONL **保留 12 个月**（月度文件自然滚动）④ `poison_count` 接入时点 = **S1 实施时**（现填 `-1` 口径正确） |
| 我方承诺 | 幂等（同 `(date,table)` 不重写）/ 不掩盖（失败必填 `last_error_class`）/ 不倒因为零 / `poison` 未接入填 `-1` |
| 待 dev 实施 | 字段落地与 JSONL 写入（dev 侧） |

---

## 三、本会话交付清单（按时间序）

| # | 事项 | commit | 证据档 | 状态 |
|---|---|---|---|---|
| 1 | Phase 1：审计范围绑定任务 + OOM 结构性消除 + 三态语义（A0–A10） | `6c73be5` | `docs/evidence/quality-audit-oom-20261031.md` | 已闭环 |
| 2 | 日线写冲突可复现案例（doom-loop + 水位冻 57/58 天） | `75a8431` | `docs/evidence/etf-daily-write-conflict-20261031.md` | 已移交（writers 线） |
| 3 | 审计五类数据问题跟进清单（含 QfqDeadLetter 14302 / PendingSla 50762） | `0e50c81` 内 | `docs/evidence/audit-findings-followup-20261031.md` | 已闭环（登记） |
| 4 | A2b：drift 结果缓存（水位键） | `af25e66` | `docs/evidence/audit-drift-cache-20261031.md` | 已闭环 |
| 5 | QFQ post-ingest 插桩（门控默认关） | `0e50c81` | `docs/evidence/qfq-post-ingest-profiling-20261031.md` | 已闭环（诊断） |
| 6 | QFQ H0/H1/H2 定量拆分（4390 万行重放 × N+1 为主因，WAL 放大排除） | `fa4a57e` | 同上 §八 | 已闭环 |
| 7 | 审计分段计时 + 300s 去向表（真凶 `factor_monotonicity` 90%） | `5cc35ad` | `docs/evidence/audit-section-profile-20261031.md` | 已闭环 |
| 8 | 方案② 设计 + S4 会签基线 | `7aa6726` | `docs/audit-global-section-cache-design.md` | 已闭环（②审通过） |
| 9 | 方案② 实施：全局段缓存（冷 378s → 热 1.01s） | `07e6238` | `docs/evidence/audit-fm-cache-20261031.md` | 已闭环 |
| 10 | 客户通知 **N-20261031**（已批，用户直发中；三裁口径：单发/柔性/内部表删除） | `9da631d` | `docs/customer-notice-20261031-draft.md` | **已批** |
| 11 | KEDB 新族：测试替身误伤生产进程族 | 知识库 `a0d6558` | `12-客户运维/已知错误/已知错误-测试替身误伤生产进程族-隔离数据根.md` | 已闭环 |
| 12 | KEDB：质量审计 OOM 族（含 A1b 判例） | 知识库 `d3d6a36` | `12-客户运维/已知错误/已知错误-质量审计OOM-全表窗口无界与范围失控.md` | 已闭环 |

---

## 四、待补观察项（不阻塞，但需下次触发时记录）

| # | 观察项 | 触发条件 | 要记什么 | 用途 |
|---|---|---|---|---|
| **O1** | **checkpoint 致缓存误失效频率** | aux 库有真实写入并触发 checkpoint（`qfq_aux.db-wal` 非 0 后回落） | 键变化次数 / 占总审计次数比例 / 每次重算耗时 | 「73 任务 7.5h→≈2min」估算的**唯一前提风险**；若误失效率 >20% 需按 `(1−失效率)` 折算并考虑改键 |
| **O2** | 陈锁自愈的日志证据 | 下次 daemon 取锁遇到陈锁 | 是否出现判活清理日志行 | 06:00 轮时结果符合但**过程日志未捕获**，如实申报过 |
| O3 | 日线 doom-loop 修复后水位恢复 | writers 线修复上线 | 水位是否从 09-03/04 推进、缺口是否回填 | 验证 R2 案闭环 |

---

## 五、与调度侧的对账约定

| 项 | 约定 |
|---|---|
| 台账口径 | 本文件为**我侧唯一台账**；调度侧条目以调度台账为准，冲突时以**调度台账**为准并回改本文件 |
| commit 口径 | 以 `git log` 为准（执行类回执不认口头） |
| 状态口径 | 四态制；「已闭环」需有证据档 + commit 双证 |
| 更新时点 | 每笔落地即更新；轮末上报时附本台账变更摘要 |

## 六、更新记录

| 时间 | 变更 |
|---|---|
| 2026-11-01 01:20 | 建账；入 A1/A2 对称条目、S4 定稿（含 dev 四签）、12 项交付清单、3 项待补观察 |
