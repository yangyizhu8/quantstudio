# A 项门禁修复方案：inspect_capabilities.py S-3a/S-3b 连续性判据（六步①，待审）

- 日期：2026-10-02｜归属：dev｜类型：**新增检测型**（依据件 §1 判定）｜期限：假期窗内
- 依据：`agent_workspace/csi300_slow_kd_reversal_r0/FIX_RECOMMENDATION.md` §2（A 节）
- 状态：**待总调度审计**（未过审不动码）

## 一、问题定义（行级，已复核）

| 子项 | 现状 | 缺陷 |
|---|---|---|
| **A-1** | `:579` `f1, f2 = _fd(snaps[0]), _fd(snaps[-1])`；`:580-587` 仅在**首尾两点**各探一次并据 `changes = set(r1) != set(r2)` 判 `pit_ok` | 只验端点 ⇒ **中段空洞位于两端之间时结构性盲区**（任何数据分布下都发现不了） |
| **A-2** | `:605` `cov_ok = bool(ic.get("present") and ic.get("indices"))`；`:609-612` 证据串只打印各指数 `snapshots` 计数与 `[min..max]` 端点 | **存在性判据替代连续性判据**；端点区间掩盖中段空档 |

**数据结构（复核确认）**：
- `ic["complete_snapshots"]`（`:271-274`）= `index_constituents_snapshot_meta` 中 `index_code=sample AND status='complete'` 的 `time` 列表，**按 time 升序**；
- `ic["indices"]` = `{code: {snapshots, min_time, max_time}}`（`time` 为 epoch ms）。

## 二、改动范围（最小，单文件）

**仅 `skills/quantstudio-strategy-compiler/scripts/inspect_capabilities.py`**：

### 2.1 新增共用 helper（模块级，两处共用）
```python
PIT_SNAPSHOT_GAP_MAX_DAYS = 45          # 阈值，可配置（见 §2.4）

def _snapshot_gap_intervals(times_ms, threshold_days=None):
    """相邻 complete 快照间隔超阈 → [(起, 止, 天数), ...]（升序）。"""
    th = float(threshold_days if threshold_days is not None
               else PIT_SNAPSHOT_GAP_MAX_DAYS)
    out = []
    for a, b in zip(times_ms, times_ms[1:]):
        days = (b - a) / 86400000.0
        if days > th:
            out.append((_fd(a), _fd(b), round(days, 1)))
    return out
```
（`_fd` 为现有内部函数，`datetime` 已在 `:574` 局部导入——helper 内按需导入，避免模块级副作用）

### 2.2 A-1：分层抽样 + 间隔检测
- **抽样点**：`_sample_idx = sorted({0, len(snaps)//2, len(snaps)-1})`（去重，`len<3` 时自动退化为 2 点/1 点——**不改 `len(snaps)>=2` 前置门槛**）；
- **探针**：首点沿用现有**双探**（`r1`/`r1b`）保 `deterministic` 语义；中/尾点各探一次；
- **`changes` 语义**：**保持首尾比较不变**（`set(r_first) != set(r_last)`）——中/尾点为**附加证据**，不参与 `changes` 判定（见 §五 决策点 1）；
- **间隔检测**：`gaps = _snapshot_gap_intervals(snaps)`；
- **判据**：`pit_ok = bool(deterministic and changes and not_union and r1 and not gaps)`；
- **证据串**追加：`sampled_dates=[...]`、各点 `n=`、`gap_intervals=[(起,止,天), ...]` 或 `gap_intervals=[]`。

### 2.3 A-2：覆盖连续性门（**按指数**，非仅样本指数）
- 探查段（`:265-276`）**扩展为按指数构建 complete 序列**：
  `SELECT index_code, time FROM index_constituents_snapshot_meta WHERE status='complete' ORDER BY index_code, time`
  → `info["complete_snapshots_by_index"] = {code: [times...]}`（**一次 SQL，无额外 provider 调用**）；
  原 `complete_snapshots`（样本指数）**保留不变**（A-1 依赖它）。
- 判据：
```python
gap_by_index = {c: _snapshot_gap_intervals(ts) for c, ts in by_index.items()}
bad = {c: g for c, g in gap_by_index.items() if g}
cov_ok = bool(ic.get("present") and ic.get("indices") and not bad)
```
- 证据串：保留现有逐指数 `snapshots` 计数，**追加空档区间明细**：
  `gap: <code> [<起>..<止>] <n> 天`（逐区间一行）；无空档指数不列（降噪）；无任何空档时附 `gap_intervals=none`。
- **FAIL 时**证据串必含**起止 + 天数**（对齐 V5）。

### 2.4 阈值可配置（三选一，见 §五 决策点 3）
1. 模块常量 + 环境变量 `QS_PIT_SNAPSHOT_GAP_MAX_DAYS` 覆盖（**荐**：零 CLI 面变化，不改调用方）；
2. CLI 参数（需改 `argparse` 与调用方）；
3. 仅模块常量（不可配置——**不符**依据件「可配置」要求）。

## 三、影响面

| 面 | 判断 |
|---|---|
| **verdict 变化 = 预期且为目的** | 修复后凡**存在中段空洞的指数**由 READY 变 **FAIL/BLOCKED**（属「新增能力开始报告真相」） |
| 既有语义 | A-1 的 `deterministic` / `not_union` / **`changes`（首尾）** 判定**逐条保留**；`len(snaps)>=2` 门槛不变；A-2 的 `present and indices` 为**新增合取项**（不改原判定，仅加严） |
| 既有测试 | **需逐条归因**任何断言 READY 的用例——若其依赖旧宽松判据，属**测试需同步更新**，非回归（依据件 §A3 已列） |
| 性能 | A-1 探针由 3 次 provider 调用 → 最多 4 次（首点双探 + 中 + 尾）；A-2 **零新增 provider 调用**（纯 SQL 聚合） |
| 矩阵哈希 | **无需追认**（不触 wrapper 模板） |
| 契约/数据/策略 | **零改动**（见 §六） |

## 四、验收标准（照依据件 §A4，V1–V5）

| # | 判据 |
|---|---|
| V1 | **单测**：构造含中段空洞的快照序列 → 两能力均 FAIL；构造连续序列 → 均 PASS |
| V2 | **现场复现**：对本策略窗口跑 `inspect_capabilities.py`，两能力由 READY 变 FAIL（**附前后对照输出**） |
| V3 | **回归**：相关测试套件全绿 + `run_contract_gate.py --strategies` PASS + 6 策略 api_portability 无衰减 |
| V4 | **边界**：连续覆盖的指数仍判 **PASS** —— 证明不是「一刀切 FAIL」 |
| V5 | **FAIL 时证据串必含空档区间（起止 + 天数）**，可读可追溯 |

## 五、回退条件

- 单文件**单点回退**（helper + A-1 判据 + A-2 判据三处）；
- **V2 或 V3 失败 ⇒ 回退，且须将本缺陷重新登记为「未修」**（不得静默带过——依据件 §A5）；
- V4 失败（连续覆盖指数被误判 FAIL）⇒ 立即回退（说明阈值或 scope 错，属「一刀切」缺陷）。

## 六、明确不改（依据件 §A2 末行）

- ❌ as-of 解析逻辑（已实测证明正确）；
- ❌ `index_constituents` / `..._snapshot_meta` 表契约；
- ❌ provider 行为（`get_index_constituents` 签名/语义/fallback）；
- ❌ 任何策略源码（铁律：修复仅限框架层）。

## 七、纯增益审计登记（三型判定）

| 三问 | 结论 |
|---|---|
| ① 影响其他功能？ | **会**——verdict 由 READY 变 FAIL（**这正是修复目的**） |
| ② 影响性能？ | **否**——探针最多多 1 次 provider 调用；A-2 零新增调用 |
| ③ 影响精度？ | **否**——不触任何计算/信号/回测 |

**判定 = 新增检测型**（verdict 可被新证据改变，属「新增能力开始报告真相」，**非既有行为漂移**）——**单列登记，不与纯恢复型混办**。

## 八、待审决策点（呈裁，各附我的建议）

1. **`changes` 语义**：中/尾点是否参与 `changes` 判定？
   - **荐：不参与**（保持首尾比较）；理由：若改为「任一对不同即 changes=True」，是**放宽方向**（首尾相同、中段不同时由 FAIL 变 PASS），与「新增检测型只加严」相悖；中/尾点作为**附加证据**仍发挥诊断作用。
2. **A-2 scope**：连续性门判**全部指数**（荐）还是仅样本指数？
   - **荐：全部指数**；理由：A-2 语义是「覆盖」，只判样本指数会留同类盲区；代价仅一次 SQL 聚合、零 provider 调用。**若审方要求严格最小，可退为样本指数**。
3. **阈值配置方式**：模块常量 + 环境变量（**荐**）／CLI 参数／仅常量。
4. **A-1 是否也判「全指数」间隔**？
   - **荐：否**——A-1 是 **PIT 探针**（针对被探样本指数），A-2 才是**覆盖**门；分工清晰，避免两处重复告警。

## 九、实施纪律承诺

- 单文件、非共享核心件；仍按**写前快照（create+store）→ 每次 edit 后 `git diff` 自检 → 精确文件清单提交（禁 `add -A`）**；
- 提交前必核 `git diff --cached --name-status`（本项目既有事故教训）；
- 验收证据落 `docs/evidence/a-gate-continuity-acceptance-*.md`；V2 现场复现须附**前后对照输出**。