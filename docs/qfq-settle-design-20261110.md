# QFQ settle（历史冻结）实施方案 —— 丙方案全文

> 起草：2026-11-10 08:55 ｜ 会话节点⑳
> 依据：审核方 2026-11-10 裁定「丙 + 乙组合，丙先行」；总调度裁 (a)(b)
> 关联：`docs/rebase-history-exempt-design-20261109.md`（v2，回填方案）、`docs/settle-pending-gate.md`（闸门，commit `e929f4c`）
> **状态：方案件，未实施**。实施须过 `settle-pending-gate.md` 的 P1-P3 三条件。

---

## 零、本方案要解决什么（一句话）

**让 rebase 的「全历史严格一致」判据，在数学上变成可满足的**——方法是承认「源端从未有过的那段历史」不构成「库内数据不可信」，从而解除 `finalized_held`。

---

## 一、问题定义（五层因果，全部实测）

### 1.1 现象

```
11:49:24 [qfq_orch] B 观察期：factor_equivalent=0  blocked=60  units=4164  放行率=0.0%
11:49:26 [DaemonLifecycle] QFQ post-ingest 未完全通过：
         status=finalized_held  error=None  held=4（水位按 hold_until_consistent 保持）
```

四表水位 gap：`stock_minutes` +21d / `etf_minutes` +28d / `stock_daily` +64d / `etf_daily` +63d。

### 1.2 判据现状（代码实证）

| 表类 | 函数 | 位置 | 硬条件 |
|---|---|---|---|
| **日线** | `_check_daily_coverage_strict` | `qfq_reanchor_engine.py:1019` | `L1055 missing_target = target_in_window - matched`；`L1061 if missing_target != 0 or extra != 0: BLOCK` |
| **分钟** | `_check_minute_cov_raw` | `:1215` | `L1246 missing_target = target_count - matched`；`L1249` 全 0 才过；`L1253` `allow_partial` 只放宽 `extra > 0` |

**关键事实**：
- **日线连 `allow_partial` 都没有**（函数名就是 `strict`）
- 分钟的 `allow_partial` **只放宽「多出」方向**（`extra > 0`），**不放宽「缺少」方向**

### 1.3 设计者已知的认知（docstring 原文，`L1222-1226`）

```python
``allow_partial=True``（仅 fresh_authoritative_rebase 在 bootstrap/存量重锚场景启用）：
当 fresh ⊃ target（fresh 多出历史行 = **库内 minutes 历史缺失，属采集配置缺口而非
数据不可信**）时，**不 BLOCK**...库内已有行必须全部匹配（missing_target==0），
否则仍 BLOCK（fresh 自身缺失不可信）。**日线覆盖检查不受影响（始终严格）**。
```

**⇒ 设计者**已经认识到**「库内历史缺失属采集配置缺口而非数据不可信」，但**只在「fresh 多出」方向实现了放宽**，在「库内缺少」方向仍是硬 BLOCK。**

**⇒ 本方案的本质：把同一条认知，补到「库内缺少且源端也无」这个方向。**

### 1.4 数据现状（可补性探测，13,168 对）

| 类别 | 对数 | 占比 |
|---|---|---|
| ✅ 可补（QuestDB 有） | 7,182 | 54.5% |
| ❌ QuestDB 无 | 5,986 | 45.5% |

**其中 5,986 对的细分（Q4 验证，2026-11-10 03:40）**：

| 类别 | 对数 | 说明 |
|---|---|---|
| **早于该 code 的源端首日** | **5,816（97.2%）** | ✅ 边界豁免可覆盖 |
| **晚于源端首日** | **170（2.8%）** | ❌ 真缺口，需白名单 |

**⇒ 170 对已逐对探测（裁 a）：QuestDB 全部 0 行 ⇒ 已入 `data/qfq_known_violations.json`（137 条目）。**

---

## 二、🔴 P0 前置验证（**本方案成立的关键，尚未做**）

### 2.1 必须先回答的问题

**rebase 的 `fresh` 来自 MCP（远程 `https://124.223.159.234/mcp`），不是 QuestDB。**

**⇒ 我用 QuestDB 判「无源」，但 rebase 比的是 MCP。若 MCP 有那段数据，则：**
- 「170 对无源」的结论**错**
- 它们不该入白名单，而应**用 MCP 回填**
- 甚至 5,986 对里的一部分也可能可补

### 2.2 P0 验证步骤（**必须在 settle 实施前完成**）

```python
# 伪代码
for (code, day, table) in [170 对例外 + 抽样 100 个「早于源端首日」的对]:
    mcp_rows = mcp_client.export(table, codes=[code], start=day, end=day)
    if mcp_rows > 0:
        mark_as("MCP_HAS_DATA", 可回填)
    else:
        mark_as("TRULY_ABSENT", 白名单)
```

**判据**：
- 若 MCP 全部 0 行 ⇒ 结论成立，白名单有效，settle 可实施
- 若 MCP 有数据 ⇒ **本方案的 5,986/170 数字全部要重算**，且优先走「MCP 回填」而非「冻结」

**⇒ 在此之前，settle 的另一半（乙方案精度容差）也依赖同一问题：0.03% 价差是 QuestDB vs 库内，那 MCP vs 库内是多少？**

**⚠ 本方案以下所有内容，都以「P0 验证通过（MCP 也无数据）」为前提。**

---

## 三、settle 的核心设计

### 3.1 冻结边界定义

```
frozen_before(code, asset_type) = min(该 code 在 MCP 源端可得的最早交易日)
```

**⚠ 注意：是 MCP 的源端首日，不是 QuestDB 的**（这是 P0 验证要确认的）。

**若 P0 发现 MCP 与 QuestDB 覆盖边界一致** ⇒ 可直接用 QuestDB 的首日（已有 5,286/5,857 个 code 的实测值）。

### 3.2 判据改动（精确到行）

#### 改动 1：日线 `_check_daily_coverage_strict`（`:1019`）

```python
# 现状（L1055-1067）
missing_target = target_in_window - matched
if missing_target != 0 or extra != 0:
    raise ReanchorBlocked("daily_coverage_mismatch", ...)

# 改后（伪代码）
frozen_before = _resolve_frozen_before(code, asset_type)   # 新函数，见 3.3
if frozen_before is None:
    # fail-closed：拿不到边界 ⇒ 走原严格路径（不豁免）
    missing_target = target_in_window - matched
else:
    # 只统计 frozen_before 之后的目标行
    target_after = _count_target_after(conn, table, code, frozen_before)
    matched_after = _count_matched_after(conn, table, code, frozen_before)
    missing_target = target_after - matched_after
    logger.warning(
        "[qfq_settle] %s daily history_exempt frozen_before=%s "
        "missing_before_after_diff=%d", code, _ms_to_date(frozen_before),
        (target_in_window - matched) - missing_target)
if missing_target != 0 or extra != 0:
    raise ReanchorBlocked("daily_coverage_mismatch", ...)
```

#### 改动 2：分钟 `_check_minute_cov_raw`（`:1215`，`allow_partial` 分支）

```python
# 现状（L1246-1261）
missing_target = target_count - matched
if missing_target == 0 and extra == 0: pass
elif allow_partial and missing_target == 0 and extra > 0: partial pass
else: raise

# 改后
frozen_before = _resolve_frozen_before(code, asset_type)
if frozen_before is not None:
    target_count = _count_target_after(conn, minute_table, code, mfreq, frozen_before)
    matched = _count_matched_after(conn, minute_table, code, mfreq, frozen_before, view)
# 其余逻辑不变
```

**⇒ 关键：两处都只改「missing_target 的分子分母」，不改 `extra` 逻辑、不改 raw 对齐、不改 front UPDATE。**

### 3.3 新函数 `_resolve_frozen_before`（三处 fail-closed）

```python
def _resolve_frozen_before(code: str, asset_type: str) -> int | None:
    """返回该 code 的冻结边界（epoch ms）；拿不到 ⇒ None（调用方走严格路径）。

    数据源优先级：
      1. data/qfq_frozen_bounds.json（settle 生成的边界快照）
      2. 环境变量 QS_QFQ_FROZEN_BEFORE：'0' ⇒ 全局禁用（返回 None）
    三条 fail-closed：
      · 文件不存在 ⇒ None（不豁免）
      · code 不在文件内 ⇒ None（不豁免）
      · 环境变量为 '0' ⇒ None（一键退回严格模式）
    """
    if os.environ.get("QS_QFQ_FROZEN_BEFORE", "1") == "0":
        return None
    bounds = _load_frozen_bounds()      # 模块级缓存，只读一次
    if bounds is None:
        return None
    return bounds.get((asset_type, code))
```

### 3.4 白名单（`qfq_known_violations.json`）的角色

**⚠ 与 settle 是两个独立机制**：

| 机制 | 作用 | 已建立？ |
|---|---|---|
| **冻结边界**（settle） | 早于边界的**整段**豁免 | ❌ 待建 |
| **白名单**（known_violations） | 边界之后的**特定 (code,日期)** 豁免 | ✅ 已建（170 对 / 137 条目）|

**⇒ 两者在判据里串联：**
```python
missing_target = (target_after_frozen - matched_after_frozen)      # 冻结边界已处理
known = _count_in_whitelist(code, table, after=frozen_before)       # 白名单再减
missing_target = max(0, missing_target - known)
```

**⇒ 白名单的 fail-closed**：文件不存在 ⇒ 不减（按原值 BLOCK）。

---

## 四、改动范围（铁律 01/24 合规）

### 4.1 文件清单

| # | 文件 | 改动 | 类型 |
|---|---|---|---|
| 1 | `quantstudio/pipeline/qfq_reanchor_engine.py` | 新增 `_resolve_frozen_before` / `_load_frozen_bounds` / `_count_target_after` / `_count_matched_after` / `_count_in_whitelist`；改 `_check_daily_coverage_strict` + `_check_minute_cov_raw` 两处 `missing_target` 计算 | 框架层（六步流水线）|
| 2 | `data/qfq_frozen_bounds.json` | 新建（边界快照，**不入 git**）| 数据 |
| 3 | `data/qfq_known_violations.json` | **已存在**（170 对）| 数据 |
| 4 | `tests/test_qfq_settle.py` | 新建单测（见第六节 V1-V7）| 测试 |
| 5 | `docs/qfq-settle-design-20261110.md` | 本文件 | 文档 |

### 4.2 铁律 24（schema 指纹）——**本方案不触发表结构**

**⇒ 不建新表、不加列、不改类型。边界与白名单都走独立 JSON 文件。**

**⇒ 这是刻意选择**：避免触发六件套契约变更（`qfq_schema_contracts` + `SCHEMA_VERSION` + `detect_schema_status` + `writers` 安全闸 + 迁移 runner + 测试）。

**风险评估**：JSON 文件不适合并发写、没有事务。但 settle 是**一次性低频率**操作，且写入由脚本串行完成，风险可接受。**若将来需高频更新，再考虑入表（届时走铁律 24 六件套）。**

---

## 五、影响面评估（铁律 18 纯增益三问）

| 问 | 答 |
|---|---|
| **影响其他功能？** | 只影响 `qfq_reanchor_engine` 的 `missing_target` 计算。`extra` 逻辑、raw 对齐、front UPDATE、postcheck 全部不变。 |
| **影响回测性能？** | 每次 rebase 多一次 JSON 读取（模块级缓存，只读一次）+ 两次范围计数查询。相比本来就要做的全表比对，增量可忽略。 |
| **影响回测精度？** | **不降**。settle 只放宽「校验口径」，**不动数据、不动复权计算、不动 front 列**。原始 K 线永不删除/修改。 |

### 5.1 定性（铁律 18 分型）

**⇒ 「新增检测型」+「数据修正型」的混合**：
- 对「早于边界」的历史区间：**新增能力开始报告真相**（此前误判为「数据不可信」）
- 对「边界之后」的数据：**判据不变**（仍严格）

**⇒ 必须在方案阶段单列，不得与「纯恢复型」混办。** ✅ 本节即为此。

### 5.2 关键风险：**放行率异常升高**

铁律 25 警告：「放行率异常高（>90%）须回查比值是否掩盖真实漂移」。

**⇒ 本方案的风险是：若 `frozen_before` 设得过宽（如错设为「今天」），会让所有缺口都被豁免 ⇒ 放行率跳到 100% 而数据实际很烂。**

**⇒ 防护（三重）**：
1. **边界必须来自实测的最小首日**，不得手填
2. **实施后第一轮必须核对**：`missing_before_after_diff`（豁免掉的行数）应与探测的 5,816 量级吻合，不应是 1,000,000+
3. **放行率 > 90% 时自动告警**（写入日志 WARNING，人工复核）

---

## 六、验收标准

| # | 判据 | 通过条件 |
|---|---|---|
| **V1** | 单测·边界前有缺 ⇒ 豁免且 pass | 构造 fixture（`frozen_before=D`，`D-1` 缺行）⇒ `missing_target=0` ⇒ pass |
| **V2** | 单测·**边界后有缺 ⇒ 不豁免** | fixture（`D+1` 缺行）⇒ `missing_target>0` ⇒ BLOCK（**防回归核心**）|
| **V3** | 单测·拿不到边界 ⇒ 不豁免 | mock `_load_frozen_bounds` 返回 None ⇒ BLOCK |
| **V4** | 单测·白名单命中 ⇒ 减 | fixture（边界后缺行 + 白名单有条目）⇒ 减后 0 ⇒ pass |
| **V5** | 单测·白名单文件缺失 ⇒ 不减 | 删文件 ⇒ BLOCK |
| **V6** | 单测·`QS_QFQ_FROZEN_BEFORE=0` ⇒ 全退回严格 | 环境变量置 0 ⇒ 所有豁免失效 |
| **V7** | 单测·豁免必留 WARNING | 断言日志含 `history_exempt` |
| **V8** | **回测哈希逐位一致**（审核方指定） | 冻结前后同回测产物哈希相同 |
| **V9** | 既有回归全绿 | `pytest tests/` 不低于基线 |
| **V10** | **实跑：放行率 > 0% 且 < 90%** | 至少若干单元转 pass；不出现 100% 满放行 |

---

## 七、回退条件与方式

| # | 触发 | 动作 |
|---|---|---|
| R1 | V2 失败（边界后缺行被误豁免）| **立即回退**，不得上线 |
| R2 | V8 失败（回测哈希不一致）| 立即回退（说明冻结影响了回测）|
| R3 | 实跑放行率 = 100% | 立即回退，检查边界是否设错 |
| R4 | 实跑后出现新数据异常 | 立即回退 |
| **回退方式** | `QS_QFQ_FROZEN_BEFORE=0` 环境变量 | 一键退回严格模式（无需改代码）|
| 辅助回退 | 删除 `data/qfq_frozen_bounds.json` | 边界失效 ⇒ 全部走严格路径 |

**⇒ 原始数据永不删除/修改，settle 只改校验口径，回退成本极低。**

---

## 八、实施步骤（P1-P3 闸门 + 九步执行）

### 8.1 前置闸门（`docs/settle-pending-gate.md`）

| 条件 | 内容 |
|---|---|
| **P0** | **MCP 源端验证通过**（本方案第二节，**尚未做**）|
| **P1** | 06:00 轮双指标已读（放行率 + OHLC mismatch）|
| **P2** | 本方案经总调度/ZCode 复核通过 |
| **P3** | 用户/总调度明确批准执行 |

### 8.2 执行顺序

| 步 | 动作 | 可逆性 | 耗时估 |
|---|---|---|---|
| 1 | **P0：MCP 源端验证**（170 对 + 100 抽样）| 只读 | 20 min |
| 2 | 边界数据固化：生成 `data/qfq_frozen_bounds.json` | 只读→写一个文件 | 10 min |
| 3 | 写单测 `tests/test_qfq_settle.py`（V1-V7）| 可逆 | 1.5 h |
| 4 | 改 `qfq_reanchor_engine.py`（4 新函数 + 2 处判据）| 可逆（git）| 2 h |
| 5 | 跑单测（V1-V7）| 只读 | 5 min |
| 6 | **V8 回测哈希比对**（冻结前/后各跑一次）| 只读 | 30 min |
| 7 | 跑既有回归（V9）| 只读 | 15 min |
| 8 | **②审送审**（六步流水线第②步）| — | 待排 |
| 9 | 启用后观察（V10）| 可回退 | 1 轮 |

**总计约 5 小时**（不含送审等待）。

---

## 九、与乙方案的关系（不可分割）

**丙（本方案）解「历史是事实」，乙解「增量是责任」。**

**⇒ 丙上线后，边界之后仍会有 `OHLC mismatch`（0.03% 价差）。乙方案（精度容差档）需要单独设计，本方案不含。**

**乙的关键待定项**：
- 容差阈值取多少（`|Δ|≤1e-9` → 相对误差 ≤ ?）
- 是否需要「量必须一致 + 相邻 bar 比值连续有界」双约束（铁律 25 建议）
- **P0 验证的副产品**：MCP vs 库内的实际价差分布（决定阈值）

**⇒ 建议：丙上线后跑一轮，用实测的 mismatch 分布反推乙的阈值，而不是先定阈值。**

---

## 十、知识反哺留痕（铁律 21）

`知识反哺：[命中] 上游可变因子下的一致性判据（QD/判据/25-上游可变因子判据.md，2026-11-10）`
- **采纳 ①**「闸门自锁」机理——本方案正是解自锁（被拦下的恰是唯一能修正它的操作）
- **采纳 ②**「判据设计三原则」中的「证据量下限」——本方案对拿不到边界的情形 fail-closed
- **采纳 ③**「分层语义：放行 ≠ 提交」——本方案只放宽 `missing_target`，不跳过 postcheck
- **采纳 ④**「放行率异常高（>90%）须回查」——本方案 5.2 节设三重防护

`知识反哺：[命中] 修复前置纯增益审计（QD/判型/18-纯增益审计.md，2026-11-10）`
- **采纳**：第四节按「新增检测型 + 数据修正型」单列，不与纯恢复型混办

`知识反哺：[无命中] knowledge/** 的 db 族与判据族，未发现「历史冻结 / frozen_before / known_violations」既有条目`

---

## 十一、本轮沉淀（铁律 22 R4）

**本轮沉淀：N/A**（理由：方案件阶段，未实施；待实施验收后按 QD/判据族入册「QFQ 历史冻结」条目）

---

## 十二、如实划界：本方案**不解决**的问题

| # | 问题 | 为什么不在范围内 |
|---|---|---|
| 1 | **MCP 与 QuestDB 的覆盖/价差差异** | 属 P0 前置验证，本方案只标注风险 |
| 2 | **乙方案的精度容差档** | 独立设计（第九节）|
| 3 | **日线 5,805 对可补部分未回填** | 属回填范围（v2 方案），非 settle |
| 4 | **`sw_daily` 未到 11-06** | 属 MCP 采集链，与 QFQ 无关 |
| 5 | **0.03% 价差的根因**（为什么 QuestDB 与库内不同）| 未查，可能是复权方式/精度截断 |
| 6 | **`frozen_before` 的 per-code 粒度是否过细** | 5,286 个 code 各有边界，可能造成「同一天不同 code 一个豁免一个不豁免」的不一致观感。**若要简化，可考虑按「资产类型」统一边界（如 STOCK 全用 2024-05-06）**，但会牺牲精度。**本方案选 per-code，请审核方确认** |

---

## 附：与本方案相关的全部实测证据

| 证据 | 路径 | 关键读数 |
|---|---|---|
| 可补性探测 | `docs/evidence/qfq-gap-probe-20261110.json` | 7,182 可补 / 5,986 无源 |
| Q4 锚定验证 | `docs/evidence/q4-anchor-verification-20261110.json` | 97.2% 早于源端首日 |
| 170 对例外探测 | `docs/evidence/exception-170-probe-20261110.json` | 全部 0 行（QuestDB）|
| 白名单 | `data/qfq_known_violations.json` | 137 条目 / 170 对 |
| 回填执行 | `docs/evidence/qfq_gap_backfill2_*.json` | 416 批全成功 |
| 死信基线 | `docs/evidence/p0d-archive-register-20261109.json` | 18,585 行 |
| 闸门 | `docs/settle-pending-gate.md` | P1-P3 |
| 判据源码 | `qfq_reanchor_engine.py:1019-1071`（日线）/ `:1215-1261`（分钟）| — |
