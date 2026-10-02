# A 项门禁连续性修复 · 验收证据（④，2026-10-03）

- 归属：dev｜方案件：`docs/a-gate-continuity-fix-design.md`（②审计通过，四决策点已裁）
- 依据：`agent_workspace/csi300_slow_kd_reversal_r0/FIX_RECOMMENDATION.md` §2（A 节）
- 类型：**新增检测型**（verdict 可被新证据改变，非既有行为漂移）
- 提交：`8fd8373`（实施）→ `d9ae79b`（收尾：文案条件化 + F6 期望 + 缺口登记）

## 一、实施摘要

| 项 | 内容 |
|---|---|
| 落点 | `skills/quantstudio-strategy-compiler/scripts/inspect_capabilities.py`（**skill 层单点**，按物理位置，无双落位） |
| 新增 | 模块级 `PIT_SNAPSHOT_GAP_MAX_DAYS=45` + `_pit_gap_threshold_days()`（`QS_PIT_SNAPSHOT_GAP_MAX_DAYS` 环境变量覆盖）+ `_fmt_ms_date` + `_snapshot_gap_intervals` + `_coverage_gaps` |
| A-1 | 分层抽样 `sorted({0, len//2, -1})`（去重；`len<3` 自动退化，**不改** `len(snaps)>=2` 门槛）+ 间隔检测并入 `pit_ok`；中段点仅作附加诊断证据（**不参与** `changes`，只加严） |
| A-2 | 探查段新增 `complete_snapshots_by_index`（**一次 SQL 聚合，零新增 provider 调用**）；`cov_ok` 增 `and not _gap_map` |
| 明确不改 | as-of 解析逻辑 / 快照表契约 / provider 行为 / **任何策略源码** —— 均未触（`git diff` 仅 2 文件 + 证据件） |

## 二、V1 单元（红态 → 绿态对照）

| 阶段 | 结果 | 说明 |
|---|---|---|
| **红态**（改动前） | **7 failed / 1 passed** | 中段空洞序列被误判 `READY`（`get_index_stocks(date) strict as-of PIT verified`）——**缺陷复现** |
| **绿态**（改动后） | **8 passed** | 空洞序列双能力 FAIL；连续序列双 PASS；非 000300 指数同判 |

**测试方法学**：合成库按 `writers.py:267-283` **真实 DDL** 建表，走**真实 provider 调用链**（非 mock）；`tests/test_a_gate_continuity.py` 8 例。

## 三、V2 现场复现（真实库，READY→BLOCKED 前后对照）**PASS**

**窗口**：2026-10-03 02:01–02:04（daemon 已停，RO `WINDOW-OK`）；**三重闸齐备**：① RO 空档闸通过 ② 版本自证 `BEFORE_has_fix=0 / AFTER_has_fix=3` ③ 两跑 `exit=0`。

| 能力 | BEFORE（修复前脚本） | AFTER（修复后脚本） |
|---|---|---|
| `index_constituents_pit` | **READY** | **BLOCKED** |
| `index_constituents_history_coverage` | **READY** | **BLOCKED** |

**BEFORE 证据**（旧宽松判据只探首尾两点）：
```
sample=000300 complete_snapshots=35
as_of(2018-01-31) n=300 deterministic=True
as_of(2026-08-31) n=300 changes_result=True
not_history_union=True
```

**AFTER 证据**（分层三点 + 间隔检测）：
```
sample=000300 complete_snapshots=35
sampled_dates=2018-01-31,2020-11-30,2026-08-31      ← 三层抽样（首/中/尾）
as_of(2018-01-31) n=300 deterministic=True
as_of(2026-08-31) n=300 changes_result=True          ← changes 仍为首尾比较（只加严）
not_history_union=True
as_of(2020-11-30) n=300                              ← 中段点附加诊断
gap_threshold_days=45
gap_intervals=[2018-01-31..2018-03-30] 58.0 天; [2018-03-30..2018-05-31] 62.0 天; …
```

**A-2 覆盖空档（真实库实测样例）**：
```
gap: 000852 [2020-11-30..2025-04-30] 1613.0 天     ← 旧判据完全看不见的中段巨洞
gap: 000016 [2025-12-31..2026-07-31] 212.0 天
gap: 000300 [2018-01-31..2018-03-30] 58.0 天
```

## 四、V4 边界（非一刀切）**PASS**

- **实库**：coverage 证据按指数**选择性**标注——无空档指数**不列入** `gap:` 清单（只有存在超阈空档的指数逐区间列出）；
- **合成库**：仅含连续序列的库两能力均 **PASS**（V1 `test_contiguous_series_both_caps_ready`）；
- ⇒ 判据对「覆盖良好」的输入不产生告警，非「一刀切 FAIL」。

## 五、V5 证据串（FAIL 时含空档区间起止 + 天数）**PASS**

证据串格式（逐区间一行，可读可追溯）：
```
index_constituents_pit          → gap_intervals=[<起>..<止>] <N> 天; …（无空档时 gap_intervals=none）
index_constituents_history_coverage → gap: <code> [<起>..<止>] <N> 天
```

## 六、V3 回归

| 段 | 结果 |
|---|---|
| 契约门（`run_contract_gate.py --strategies`） | **PASS**：契约套件 + 6 策略 api_portability 全通过，既有白名单无触发 |
| 库依赖段（3 文件） | **24 passed / 1 failed**（见下归因） |

**F6 测试失败的两因归因（逐条）**：

| # | 能力 | 归属 | 证据 |
|---|---|---|---|
| 1 | `index_constituents_pit` / `..._history_coverage` | **本件预期变化**（新增检测型） | 已按依据件 §A3 授权更新断言为「BLOCKED 且证据串含 gap」，现**通过** |
| 2 | `industry_classification_sw2021`、`sw_l1_index_daily` | **既存数据缺口**（非本件所致） | **BEFORE 侧（修复前脚本）报告里两能力同样 BLOCKED** ⇒ 旧测试在本库**改动前亦无法通过**；A 项修复后循环往下露出下一项 |

第 2 类按总调度**裁定甲**处理：**保持红 + 登记为既存缺口**（`docs/ISSUES.md` **ISS-006 / ISS-007**），不在本件扩大范围（乙案会把数据线问题伪装成门禁语义）。

## 七、窗口内自纠（1 处，如实登记）

`fail_message` 初版**未按方案条件化**——失败时仍报「no index_constituents snapshots available」，与实际「有快照但有空档」不符（**V5 证据串本身始终合规**，是 message 文案误导）。窗口内修正，并经总调度裁定**同类文案不半修**，`index_constituents_pit` 的通用三因文案一并条件化：
```
coverage → "index constituents coverage has over-threshold snapshot gaps"
pit      → "index constituents snapshot gaps over threshold (see gap_intervals in evidence)"（gaps 预初始化为 []，防异常路径 NameError）
```

## 八、回退条件（复核）

- 单文件三点回退（helper / A-1 判据 / A-2 判据）；
- **V2 或 V3 失败 ⇒ 回退并须将本缺陷重新登记为「未修」**（不得静默带过）；
- V4 失败（连续覆盖指数被误判 FAIL）⇒ 立即回退（阈值或 scope 错，属一刀切缺陷）。

## 九、结论

**V1–V5 全部达标**（V3 的 F6 残留已按裁定甲归因并登记）。本件为**新增检测型**纯增益修复：verdict 变化即目的——门禁开始报告真相。