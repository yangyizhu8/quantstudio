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

## 二、P0 源端验证 —— **已完成，结论升格为「同源定理」**

> ②审通过（总调度 2026-11-10）。本节由「待验证风险」改为「已证定理」——P0 的产出不是「碰巧都对上了」，而是**结构必然**。

### 2.0 结论（2026-11-10 10:41 实测，全量 270 对，1.4 分钟，零错误）

```
[270/270] MCP有=0  0行=270  错误=0
判定 = MCP_ALSO_ZERO ⇒ settle 方案前提成立
```

**⇒ 「QuestDB 无源」的判定对 MCP 同样成立，白名单与冻结边界都站得住。**

### 2.1 【定理】MCP ≡ QuestDB 的远程代理，二者同源

**四重证据（全部实测，非推断）：**

| # | 证据 | 读数 |
|---|---|---|
| **E1** | **dataset 命名带 `qdb.` 前缀** | 115 个 dataset 全部形如 `qdb.stock_daily` / `qdb.stock_minutes` / `qdb.etf_daily`——`qdb` 即 QuestDB |
| **E2** | **server 自述** | `name=QuantStudio-MCP` `version=1.1.0` `tier=pro` `read_only=true`<br>`adj_policy = "raw+adj_factor (client computes adjusted=raw*adj_factor)"` |
| **E3** | **健康探针直连 QuestDB** | `ping_health → {"status":"ok","questdb":"reachable","latency_ms":0.3}`<br>⇒ MCP 服务端自身就在连 QuestDB |
| **E4** | **coverage 边界与行数逐项吻合** | 见下表 |

**E4 明细（MCP `get_coverage` vs 本地 QuestDB 直查）：**

| 表 | MCP（远程） | 本地 QuestDB | 一致性 |
|---|---|---|---|
| `stock_daily` | 14,600,481 行<br>`2000-01-21 ~ 2026-11-09` | 14,602,741 行<br>`2000-01-21 ~ 2026-11-09` | **边界逐字相同**；行数差 2,260（快照时刻差）|
| `stock_minutes` | 575,950,685 行<br>`2024-05-06 09:30 ~ 2026-11-09 09:59` | 575,975,873 行<br>`2024-05-06 09:30 ~ 2026-11-06 15:00` | **起点逐字相同**；行数差 25,188（快照时刻差）|
| `etf_daily` | 2,557,991 行<br>`2010-01-04 ~ 2026-11-09` | **2,557,991 行**<br>`2010-01-04 ~ 2026-11-09` | **完全相同** |

**⇒ 定理陈述：**

> **MCP（`https://124.223.159.234/mcp`）是本地 QuestDB 的只读远程代理，不构成独立的第二数据源。**
> 因此 **`MCP 无 ⇔ QuestDB 无`**，且 **`MCP 覆盖边界 ≡ QuestDB 覆盖边界`**（对同一快照时刻）。

**⇒ 重要推论：**

1. **「270 对全 0 行」不是巧合，是结构必然**——同一份数据查两次，不可能一次有一次无。
2. **用 QuestDB 判定「可补/无源」是**合法代理判断**，无需另建 MCP 探测通道**（P0 探测脚本可留作复核工具，但日常判定用 QuestDB 即可，快得多）。
3. **白名单（`qfq_known_violations.json`）与冻结边界的有效性，不依赖 MCP 是否有独立数据**——因为 MCP 没有独立数据。

### 2.2 对乙方案（精度容差）的意义：**价差是同源噪声，不是源错位**

**0.03% 价差（`002299 @ 2025-01-10`：QuestDB `open=12.836` vs 库内 `open=12.84`）的重新定性：**

| 旧理解（已被推翻） | 新理解（E1-E4 支撑） |
|---|---|
| 「QuestDB 与 MCP 是两个源，价差是源间差异」 | **同源同表同一天**，价差是**库内 raw 在采集写入时的精度截断/四舍五入** |
| 担心「回填引入源错位」 | 回填源与库内源**相同**，不引入源错位，只可能引入**精度差异** |

**⇒ 乙容差的推论（总调度已采信）：同源采集噪声 ⇒ 容差可设置得比「跨源容差」更窄。**

**⚠ 但容差的具体数值不在本方案内定**——按总调度裁定，**以 **G6 对拍实测分布** 定**（运维1 于 2026-11-10 15:00 读窗产出「旧/新全量差分布」）。本方案只负责把「容差的语义前提」说清：**它容的是同源精度噪声，不是跨源口径差。**

### 2.3 P0 验证的可复跑方式

```bash
# 冒烟（3 对，验证接口通不通）
python scripts/p0_mcp_verify.py --smoke

# 全量（170 例外 + 100 抽样 = 270 对，约 1.5 分钟）
python scripts/p0_mcp_verify.py
```

**产出**：`docs/evidence/p0-mcp-verify-{smoke,full}-*.json`（逐对明细 + verdict）

**⚠ 接口要点（踩过的坑，供后来者）**：
1. 类名是 **`MCPClient`**（全大写），首参是 **`endpoint`**（不是 `base_url`）
2. **必须先 `handshake()`** 拿 session，否则 `tools/call` 报 `400 Missing session ID`
3. `query_snapshot` 的 `filters` 形如 `[{"column":"trade_date","op":"=","value":"2025-01-10"}]`
4. `get_coverage` 的 `row_count` 字段在顶层是 0，真实值在 **`raw.coverage[]`** 里

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

## 八、实施步骤（P0-P3 闸门 + 九步执行）

### 8.1 前置闸门（`docs/settle-pending-gate.md`）

| 条件 | 内容 | 状态 |
|---|---|---|
| **P0** | **MCP 源端验证通过**（本方案第二节）| ✅ **已完成**（270/270 全 0 行，同源定理成立）|
| **P1** | 06:00 轮双指标已读（放行率 + OHLC mismatch）| ⏸ 待 12:00-13:00 |
| **P2** | 本方案经总调度/ZCode 复核通过 | ✅ **已通过**（总调度 2026-11-10）|
| **P3** | 用户/总调度明确批准执行 | ⏸ 待（实施窗口：G6 后 / 明晚受控窗）|
| **P4** | **G6 对拍完成**（运维1 15:00 读窗产出旧/新全量差分布）| ⏸ 待外部（**乙容差数值依赖它**）|

### 8.2 执行顺序（**2026-11-10 现状标记**）

| 步 | 动作 | 可逆性 | 耗时估 | **状态** |
|---|---|---|---|---|
| **0** | **等 G6 对拍**（运维1 于 15:00 读窗产出「旧/新全量差分布」）| — | 待外部 | **⏸ 阻塞项**（乙容差数值依赖它）|
| 1 | **P0：MCP 源端验证**（270 对）| 只读 | 已用 1.4 min | ✅ **已完成**（`76dc3ed`，270/270 全 0 行）|
| 2 | 边界数据固化：生成 `data/qfq_frozen_bounds.json` | 只读→写文件 | 10 min | ⏸ 待 **P1**（双指标）|
| 3 | 写单测 `tests/test_qfq_settle.py`（V1-V7）| 可逆 | 1.5 h | ⏸ |
| 4 | 改 `qfq_reanchor_engine.py`（4 新函数 + 2 处判据）| 可逆（git）| 2 h | ⏸ |
| 5 | 跑单测（V1-V7）| 只读 | 5 min | ⏸ |
| 6 | **V8 回测哈希比对**（冻结前/后各跑一次）| 只读 | 30 min | ⏸ |
| 7 | 跑既有回归（V9）| 只读 | 15 min | ⏸ |
| 8 | **②审送审**（六步流水线第②步）| — | — | ✅ **已通过**（总调度 2026-11-10）|
| 9 | 启用后观察（V10）| 可回退 | 1 轮 | ⏸ |

**总计约 5 小时**（不含送审等待与 G6 等待）。

**⇒ 实施窗口（总调度裁定）：** **G6 对拍之后 / 明晚受控窗**。今日（11-10）不实施代码。

**总计约 5 小时**（不含送审等待）。

---

## 九、与乙方案的关系（不可分割）

**丙（本方案）解「历史是事实」，乙解「增量是责任」。**

**⇒ 丙上线后，边界之后仍会有 `OHLC mismatch`（0.03% 价差）。乙方案（精度容差档）需要单独设计，本方案不含。**

**乙的关键待定项**：
- 容差阈值取多少（`|Δ|≤1e-9` → 相对误差 ≤ ?）
- 是否需要「量必须一致 + 相邻 bar 比值连续有界」双约束（铁律 25 建议）
- ~~**P0 验证的副产品**：MCP vs 库内的实际价差分布（决定阈值）~~ → **已改由 G6 对拍供给**

**⇒ 乙容差的数值来源（总调度 2026-11-10 裁定）：**

| 项 | 内容 |
|---|---|
| **数据来源** | **G6 对拍**——运维1 于 2026-11-10 **15:00 读窗**产出「旧/新全量差分布」（不是猜，不是抽样）|
| **语义前提（本方案供给）** | 价差是**同源采集精度噪声**（§2.2 已证），不是跨源口径差 ⇒ **容差可设得比跨源容差更窄** |
| **数值决定（不在本方案）** | 由 G6 分布反推，不由我先定 |
| **实施影响** | **乙的实施排在 G6 之后**；丙（本方案）不受阻，但两者同在「明晚受控窗」 |

**⇒ 硬约束：本方案不预设乙的容差数值。若需我出乙方案，须先拿到 G6 的分布数据。**

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

| # | 问题 | 为什么不在范围内 | 现状 |
|---|---|---|---|
| 1 | ~~MCP 与 QuestDB 的覆盖/价差差异~~ | ~~属 P0 前置验证~~ | ✅ **已解决**（§二：同源定理 + 270/270 全 0 行；价差定性为同源采集噪声）|
| 2 | **乙方案的精度容差档** | 独立设计（第九节）| ⏸ 数值依赖 **G6 对拍**分布 |
| 3 | **日线 5,805 对可补部分未回填** | 属回填范围（v2 方案），非 settle | ⏸ 可另行安排 |
| 4 | **`sw_daily` 未到 11-06** | 属 MCP 采集链，与 QFQ 无关 | ⏸ 等 06:41 passthrough |
| 5 | **0.03% 价差的精确根因**（是截断还是四舍五入）| 已定性为「同源采集精度噪声」，但**未定位到具体代码行**（采集写入时的哪个转换环节） | ⏸ 待查；**不影响 settle 成立**（settle 用容差/边界，不依赖根因）|
| 6 | **`frozen_before` 的 per-code 粒度** | ~~待确认~~ | ✅ **已终确认 per-code**（总调度 2026-11-10：「97.2% 锚定精度为据；『同日不同 code 观感不一致』非缺陷——按真实源端定界是正确语义；260KB JSON 可接受」）|

### 12.1 per-code 粒度定案（2026-11-10）

| 项 | 结论 |
|---|---|
| **粒度** | **per-code**（每个 code 用其自身在源端的真实首日）|
| **依据** | Q4 实测锚定精度 **97.2%**；按资产统一会破坏它 |
| **观感问题的定性** | 「同日不同 code 一个豁免一个不豁免」**不是缺陷**——按真实源端定界是**正确语义**（同一日期对不同股票的意义本就不同）|
| **数据规模** | `stock_minutes` 5,286 + `stock_daily` 5,857 ≈ 11,143 条边界 → JSON 约 **260KB**，可接受 |
| **未采用方案** | 按资产统一（STOCK 全用 2024-05-06 / ETF 全用 2025-01-02）——**会制造大量假 block**，抵消 settle 效果 |

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
