# ETF T+0/T+1 按代码分类执行（方案二）设计与实证文档

| 项 | 内容 |
|---|---|
| 文档版本 | v1.3（2026-08-16） |
| 状态 | **G0–G3.5 关闭；G4 执行完毕**（G4 一次性提交已落本地，**未推送**；待 ZCode G4 审核 + 用户确认后按铁律推送） |
| 变更定性 | **框架行为/正确性变更**（AGENTS.md 铁律：不得作为性能优化捆绑实施） |
| 适用范围 | 本地回测引擎 `quantstudio/backtest/backtest_engine.py` + skill 契约 + 策略生成规则 |
| 实证依据 | 2026-08-15 PTrade 平台双轮探针（策略 测试21/测试22，`ptrade/t0_t1_probe_ptrade.py` v2） |

---

## 1. 摘要（一页结论）

1. **现状缺陷**：引擎 `etf_t0` 是全局布尔——`true` 把所有 ETF 当 T+0（允许实盘不存在的"当日买入国内股票型 ETF 当日卖出"），`false` 把所有 ETF 当 T+1（错杀跨境/商品 ETF 的真实 T+0，且卖出被拒会破坏已确认的止损语义）。
2. **实证结论**（24 只标的 × 2 轮，平台行为稳定）：**19 只与本地 `etf_basic.fund_type` 分类规则完全一致**；**4 只平台偏差**（520830 沙特、161226/501018/162411 LOF——平台回测按 T+1，真实交易所规则为 T+0）；**1 只平台数据缺口**（513100，PTrade 回测分钟 bar 交易量为 0，无法成交，本地有数据）。
3. **方案**：引擎按 `fund_type` 做 per-code T+0 分类（真实交易规则为唯一真相），未知代码 fail-closed 按 T+1；策略层止损采用**"触发即锁→尝试卖出→成交 0 股→次日顺延"**的拒绝处理模式（不查类别、不读订单状态字段）。
4. **1:1 转换保证**：本次改动**不新增任何策略 API、不改变任何 API 签名**；策略止损逻辑只使用 PTrade 与本地共有且语义对齐的原语（`order()` 真值、`get_position()` 对账、防御式可卖字段读取）——**同一份本地策略源码可直接转换为 PTrade 代码，转换管线无需为此改动**。平台偏差（520830/LOF/513100）由策略的拒绝处理模式自动吸收，行为差异写入转换文档。
5. **验收红线**：修复前后现有策略黄金结果逐项对比，**唯一允许的差异 = per-code T+0 语义本身**（当日买入国内 ETF 当日止损不再成交），其余指标必须逐位一致；不满足即回退。

### 1.1 审核记录（ZCode，2026-08-15）

**第一轮（v1.0→v1.1）**：原则通过（G0 放行），附 5 项裁决 + 6 项 G1 前修订；v1.1 全部落实。

| 项 | 审核结论 | 落实位置 |
|---|---|---|
| 决策点1 平台偏差立场 | 同意（本地按真实交易所规则 T+0） | §4 / §8.4 |
| 决策点2 GUI 默认值 | **否决"默认按分类"**：双端默认统一 = 全部T+1，"按分类"须显式选择（防 3e9bb91 双端分叉复发） | §5.3 |
| 决策点3 CLI 默认值 | 同意（默认 false 不变）；`true` 语义重定义须明示为带理由的行为变更，旧"全 T+0" CLI 侧不再可达 | §5.3 / §10 |
| 决策点4 fail-closed | 同意（未知→T+1） | §4 |
| 决策点5 基线集合 | 需追加 per-code 语义载体（经核实无历史 etf_t0=true 分钟产物，载体=探针文件；bbi 日线档归入 (a) 档） | §9.2 |
| 修订1 零差异档 | 回归分 (a) 零差异 hash 档 + (b) 允许差异归因档 | §9.2 |
| 修订2 装载时机 | 仅 minute+true 装载；false/daily 零查询零 warning；走 provider 层、引擎无裸 SQL | §5.1 |
| 修订3 伪代码收紧 | buy_date 由策略自维护账本；held_shares 口径 = get_position 持仓总量 | §7 |
| 修订4 .status 边界 | 禁令仅限 skill 生成的 PTrade 可移植策略；本地专用策略不受限（与引擎 docstring 一致） | §6.3 / §10 |
| 修订5 §8.5 措辞 | 差异来源已知、次数有界、**盈亏方向不确定** | §8.5 |
| 修订6 G2 交付物 | 自动化回归脚本（hash 比对 + 逐字段 diff + 差异归因清单），禁止人工比对 | §9.2 / §11 |

**G2 复核轮（2026-08-16）**：G2 双档验收通过（证据：`docs/evidence/etf-t0-g2-regression-20260816.md`，捕获产物 `output/g2_etf_t0_regression/{pre_v2,post_v2}/`）。
- (a) 零差异档：minute 3 格 + daily 3 格全部文件 SHA-256 逐位一致 + 日志零 diff（时间戳/毫秒/导出目录名三类伪影规范化）；
- (b) per-code 档：24 只差异逐条归因，全部符合预期；
- 方法学落实 ZCode G2 注记：干净基线采用 HEAD−G1 vs HEAD 修正对（并发提交事件记录见 §14）、PYTHONHASHSEED=0、DB 快照副本、增量落盘防进程死亡；
- 新增技术债 T3（存量策略 dict/set 迭代顺序依赖，skill 生成策略须规避）。

**G3 复核轮（2026-08-16）**：G3 主体通过（etf-t0-rules.md / schema 两字段 / 校验器三 BLOCK 规则 / SKILL.md / 6 测试全绿 / installed 同步）；**G3.5（R5 复现性双跑门禁）补齐**：
- R5 证据升级 2.1：`reproducibility_artifacts`（第二独立进程运行的三件套）必填，两侧 SHA-256 逐位一致才 PASS；缺失 → `reproducibility_evidence_missing`（EVIDENCE_INCOMPLETE），不一致 → `reproducibility_mismatch`（R5 FAIL 并归因）；
- 落点：`user_backtest_evidence.schema.json`、`analyze_backtest_artifacts.verify_reproducibility`、`review_user_backtest_evidence`（RETURN_STAGE 两新类）、`agent-first-workflow.md` R5 节、`output-contract.md` §2（补运行层）、`SKILL.md`（规则20 + User-PyQt 节）、`etf-t0-rules.md` §3-5（set 字面量盲区 + 误拦申诉说明）、`validate_agent_strategy.py` 规则注释；测试 `test_user_pyqt_candidate_flow.py` 29 通过（含 3 新例：缺失/不一致/一致）；
- DB 快照（19.9GB）已按 ZCode 批准删除；G4 同步清单见 §10。

**G4 复核轮（2026-08-16）**：G4 一次性提交已完成（**未推送**，遵守铁律），清单逐项落实：
- 提交内容：G1 遗留测试文件（`tests/conftest.py` / `test_minute_t1.py` / `test_minute_order_execution.py`）+ skill 全量（G3/G3.5 变更）+ G2 交付物（`scripts/etf_t0_regression.py`、`docs/evidence/etf-t0-g2-regression-20260816.md`、`ptrade/t0_t1_probe_ptrade.py`）+ 文档同步（README / strategy_toolbox §3.7.1 + .status 边界 / prompt_engineering §4 / CHANGELOG 新建 / 本设计文档 v1.3）+ 本设计文档归档；
- **T2 处置（二选一已选"文档显式降级"）**：GUI 保持布尔 `etf_t0`（默认 False=全部T+1 与 CLI 一致；勾选 True=按分类）；"全部T+0 研究模式"需引擎扩展，暂不实施，降级说明已写入 README 与 §13 T2；
- **平台差异表落点**：`skills/quantstudio-strategy-compiler/references/etf-t0-rules.md`；`docs/ptrade-conversion-tab-spec.md` 为转换 tab 轨道在途未跟踪文件，本次不触碰，待其落地后由该轨道补表（§8.4 数据不缺失）；
- 引擎代码（`ea9cc8a`，他代理提前推送）与测试文件内容已复核一致，G4 提交不重复包含引擎改动；
- **待办**：ZCode G4 审核 → 用户确认 → G5 推送 → G6 新策略 R3-R5。

**复核轮（v1.1→v1.2）**：复核结论 **通过，G0 关闭**；修订全部如实落实，附 2 条 G2 执行注记 + 1 条数据事实修正（均已在本文落实）。

| 项 | 内容 | 落实位置 |
|---|---|---|
| 数据事实修正 | 513100 本地分钟数据实测：68125 bar（2025-01-02→2026-08-13），**1321 根零量 bar**，09:35 槽位 283 根中 21 根零量，**08-04 至 08-13 连续零量**（2026-08-15 独立复核一致；对照组 510300/518880 同期 09:35 正常）→ (b) 档归因措辞改为"本地零量 bar 仍成交（引擎不建模成交量门槛）" | §8.4 / §9.2(b) |
| G2 注记1 | (b) 档基线必须是**真实产物**：变更前"24 只当日卖全部成交"基线须在变更前代码（git worktree/stash）上实际运行探针产出，不得凭预期填写 | §9.2(b) |
| G2 注记2 | (a) 档"日志 diff 为空"需防时间戳假失败：回归脚本须**规范化时间戳**或只比对 WARNING 及以上级别行 | §9.2(a) |

---

## 2. 背景与问题定义

### 2.1 现状（`backtest_engine.py`）

- `:393` `self.etf_t0 = bool(etf_t0) if engine_profile == "minute-bar-v1" else False`（全局布尔，仅分钟 Profile 生效）
- `:800-813` 买入后 `can_sell` 解锁逻辑：`is_etf_t0 = self.etf_t0 and _is_etf_code(code)` → T+0 立即解锁全部、否则当日新买不增 `can_sell`
- `:828-829` 卖出拒绝：`can_sell <= 0` → 成交 0 股

### 2.2 缺陷（用户 2026-08-15 确认）

| 模式 | 国内股票型 ETF（真 T+1） | 跨境/商品/债券/货币 ETF（真 T+0） |
|---|---|---|
| `etf_t0=true` | ❌ 允许实盘不存在的当日买当日卖 | ✅ 正确 |
| `etf_t0=false` | ✅ 正确 | ❌ 当日止损卖单被拒→成交 0 股，破坏"触发即锁当日必卖"语义 |

**两类缺陷都必须修**：不是简单翻转开关，而是按代码分类。

### 2.3 定性

按 AGENTS.md 铁律：本变更**改变回测可观察结果**（特定标的的当日卖出订单从成交变为拒绝），属于**框架行为/正确性变更**，必须：单独立项 → 用户确认 → 实现 → 回归验收（差异逐项归因）→ README/docs 同步 → 双仓库推送。禁止与任何性能优化捆绑。

---

## 3. 实证证据（2026-08-15 平台双轮探针）

### 3.1 方法

- 探针文件：`ptrade/t0_t1_probe_ptrade.py`（v2，两轮独立：Day1-2 轮1，Day3 轮2；1 分钟频率回测）
- 协议：09:35 买入 100 股 → 10:05 补买 → 10:30 记录持仓+平台可卖字段 → 14:30 当日卖出尝试（`order(code, -100)`）→ 14:50 核对 → 次日 10:00 清理卖出
- 判定：`order()` 返回 None/-1 = 拒单；持仓清零 = 成交；次日卖成 = T+1 次日解锁

### 3.2 结果表（两轮完全一致，平台分类稳定）

| 类别 | 标的 | 当日卖 14:30 | 可卖字段 10:30 | 判定 |
|---|---|---|---|---|
| equity 5 | 510300/510500/159915/512480/159995 | **拒单**（"股票可卖持仓数量为0, 委托取消"） | 0.0 | ✅ T+1，次日卖成 |
| qdii 6 | 513500/513520/513030/159920/513050/513180 | **受理并成交** | 100.0 | ✅ T+0 |
| gold 2 | 518880/518800 | 受理并成交 | 100.0 | ✅ T+0 |
| commodity 2 | 159985/159980 | 受理并成交 | 100.0 | ✅ T+0 |
| bond 2 | 511010/511260 | 受理并成交 | 100.0 | ✅ T+0 |
| money 2 | 511990/511880 | 受理并成交 | 100.0 | ✅ T+0 |
| **520830 沙特**（qdii） | | **拒单** | 0.0 | ❌ 平台偏差（真实规则 T+0） |
| **LOF 3** | 161226/501018/162411 | **拒单** | 0.0 | ❌ 平台偏差（真实规则 T+0） |
| **513100 纳指**（qdii） | | 买入未成交（09:35/10:05 bar 交易量=0，两轮如此） | 0.0 | ⚠️ 平台数据缺口，无法验证 |

**统计：19/24 与本地规则一致；4 只平台偏差（两轮稳定复现）；1 只平台数据缺口。**

### 3.3 平台机制确认（写入契约的事实）

1. PTrade 回测**真实执行 T+1**：拒单提示固定为"股票可卖持仓数量为0, 无法卖出, 委托取消"，`order()` 返回 **None**；
2. PTrade 回测持仓对象暴露**可卖字段**（探针防御链命中 `enable_amount`/`closeable_amount`）：T+1 当日新买 = 0，T+0 = 全量；**次日盘前自动解锁**（Day2 全部卖成）；
3. 被受理的当日卖单在 bar 收盘即成交（下单后 `get_open_orders()` 为空）。

### 3.4 本地引擎契约确认（1:1 关键）

本地 `Order` 类（`backtest_engine.py:92-127`）：`__bool__ = filled>0 or filled_amount>0`——**拒单/零成交的 Order 为 falsy**，与 PTrade 拒单返回 None 在**布尔真值语义上等价**：

| 场景 | PTrade 实测 | 本地引擎 |
|---|---|---|
| 当日卖 T+1 标的 | 返回 None（拒单） | Order(status=rejected, filled_amount=0) → **falsy** |
| 当日卖 T+0 标的 | 返回 order_id 字符串 | Order(filled_amount>0) → **truthy** |
| 受理但未成交（如 513100 零量 bar） | 返回 order_id 但持仓不变 | Order(filled_amount=0) → falsy |

⚠️ 注意第三行：**"受理但未成交"两个平台返回值真值不一致**（PTrade truthy / 本地 falsy）——因此策略**不得把返回值当作"已成交"**，必须以持仓对账为唯一事实。

---

## 4. 目标语义（要编码的真实规则）

> `etf_basic.fund_type ∈ {qdii, gold, commodity, bond, money}` → **T+0**（买入当日 `can_sell` 即时解锁）
> `fund_type = equity` → **T+1**（当日新买 `can_sell = 0`，卖出返回 0 股，次日盘前解锁）
> 未知代码（不在 etf_basic，如 LOF）→ **T+1**（fail-closed，宁可保守拒绝，不可越权放行）

---

## 5. 引擎层改动设计

### 5.1 数据装载（无管线改动，装载时机收紧）

- **装载触发条件（ZCode 修订2）**：`SELECT etf_basic` 仅当 `engine_profile == 'minute-bar-v1' and etf_t0` 时执行；**daily profile 与 `etf_t0=False` 路径不产生查询、不产生 warning**（warning 也是可观察输出，daily/默认档黄金基线要求零改动）；
- **数据通道**：必须经 provider/数据访问层注入（与 `get_etf_list_local` 同一数据源），**引擎内不得裸 SQL、不直接打开 DuckDB**；
- 装载内容：一次性执行 `SELECT code, fund_type FROM etf_basic WHERE status='L'`，构建 `{code: is_t0}` 内存缓存；
- 查询失败 / 表缺失 → **空缓存 + warning**（全量 fail-closed 按 T+1），绝不静默放行；
- 分类规则写死为常量集合：`T0_FUND_TYPES = {'qdii', 'gold', 'commodity', 'bond', 'money'}`（与探针实证一致；`fund_type` 与 `etf_type` 同值，1606/1606 全覆盖，ZCode 实测 0 行不一致）。

### 5.2 判定与执行路径

```python
# 引擎内部（无新增策略 API）
def _is_t0(self, code: str) -> bool:
    return self._t0_cache.get(code, False)      # 未知代码 fail-closed → False

# 买入路径（:800-813 唯一改动点）
is_etf_t0 = self._is_t0(code)                   # 替换 self.etf_t0 and _is_etf_code(code)
# 卖出路径（:828-829）不变：can_sell 机制天然实现 T+1 拒绝
# 盘前解锁（:801 注释"盘前解锁全证券一致"）不变
```

### 5.3 参数语义迁移（兼容红线，含 ZCode 裁决 2/3）

| 入口 | 现状 | 改后 |
|---|---|---|
| CLI `--etf-t0` | `true`=全 T+0 / `false`=全 T+1（默认 false） | **默认 false 不变**（脚本化运行行为不漂移）；`true` 语义**重定义**：从"全 T+0"改为"**per-code 分类**"。**旧"全 T+0"模式 CLI 侧不再可达**（该语义本身错误——允许实盘不存在的同日买卖），README/CHANGELOG 必须将其明示为**带理由的行为变更**，不得只写在迁移表里 |
| GUI 回测 | `etf_t0` 可选布尔 | **v1.3 处置（G4 文档显式降级，见 §13 T2）**：保持布尔 `etf_t0`（默认 False=全部T+1，与 CLI 默认一致，**双端默认统一**，杜绝 3e9bb91 同类 CLI/GUI 分叉隐患；勾选 True=按分类 per-code，须显式选择）；"全部T+0 研究模式"需引擎扩展，**暂不实现**，降级说明见 §13 T2 |
| `daily-bar-v1` | `etf_t0` 强制 False | **不变**（per-code 仅 minute-bar-v1 生效，日线行为逐位不变） |

**运行时开启链路自洽**：skill 契约要求 design JSON 显式声明 `etf_t0_enforcement`（§6.2）→ 回测时显式传 `--etf-t0 true`（或 GUI 选"按分类"）→ 引擎装载分类。未声明/未显式开启 = 全部 T+1，与现状一致。

**兼容红线**：不改变 `BacktestEngine.__init__` 参数名/默认值（`etf_t0: bool = False` 原样保留）；不改变撮合价、费用、滑点、涨跌停、T+1 之外的一切撮合规则；不改变 daily profile 任何行为。

---

## 6. Skill 层契约（同步更新）

### 6.1 新建 `references/etf-t0-rules.md`

内容：分类规则表（§4）、引擎行为契约（§5）、**策略必守模式**（§7）、平台差异表（§9.4）、R5 证据解释（`insufficient_sellable` 计数：每个止损事件最多 1 次拒绝，属预期行为，不是部署失败）。

### 6.2 `agent_strategy_design.json` 新增字段（schema 同步）

```json
"market_data_contract": {
  "signal_price_adjustment": "pre",
  "execution_price_basis": "raw_trade_price",
  "etf_t0_enforcement": "engine_per_code",        // 枚举: engine_per_code | all_t1
  "stop_deferral_semantics": "trigger_lock_defer_next_sellable_day"  // 枚举，必填
}
```

### 6.3 校验器（`validate_agent_strategy.py`）

- 策略源码使用分钟频率（`frequency='1m'`）且含止损逻辑时，设计 JSON **必须**声明 `stop_deferral_semantics`，否则 BLOCK；
- 静态检查：策略源码**不得访问 `order()` 返回值的 `.status`/`.reason` 等本地专属字段**（仅允许真值判断 + 持仓对账），违者 BLOCK；
- **边界澄清（ZCode 修订4）**：上述 `.status` 禁令**仅适用于 skill 生成的 PTrade 可移植策略**；本地专用策略不受限（引擎 `Order` docstring"新策略可检查 status 感知失败"继续有效）。该边界必须在 README / docs/strategy_toolbox.md 中与禁令**同时写明**，避免文档自相矛盾。

### 6.4 R2.5 确认项（strategy_semantics 修订，需用户正式确认）

> 止损语义从"当日必卖"修订为：**触发即锁定卖出意图；当日可卖（T+0 或非当日买入）→ 当日成交；当日不可卖（T+1 当日买入）→ 顺延至次日首个可卖窗口成交；挂起期间意图不撤销**。

### 6.5 首个消费者迁移提示

CodeBuddy 进行中的 `etf_double_pool_momentum_rotation`（`output/generated_strategies/etf_double_pool_momentum_rotation/agent_strategy_design.json`）当前声明旧字段 `"etf_t0": true`——契约生效后须迁移为 `"etf_t0_enforcement": "engine_per_code"` + `"stop_deferral_semantics": "trigger_lock_defer_next_sellable_day"`，止损逻辑按 §7 模式实现。

---

## 7. 策略层止损状态机（拒绝处理模式，可移植）

**核心原则：策略不查 ETF 类别、不读订单状态字段，用"尝试→对账→顺延"统一处理所有平台。**

```python
# 状态账本：策略自维护（随 g 持久化）。PTrade Position 无 buy_date 字段，严禁读 pos.buy_date
#   g.stop_state[code] = {'avg_cost': float, 'buy_date': 'YYYY-MM-DD',
#                         'stop_locked': bool, 'stop_pending': bool}
#   buy_date 在买入成交确认（get_position 持仓>0）时写入账本；avg_cost 用成交价/引擎成本价
# 每个止损窗口（09:40-10:29 / 10:40-11:30 / 13:00-14:57）执行：
held = _attr_number(get_position(code), ('amount', 'total_amount'), 0)   # held_shares 口径=get_position 持仓总量
if price < st.avg_cost * 0.95 and not st.stop_locked:
    st.stop_locked = True                       # 触发即锁，价格回升不撤销
if st.stop_locked and not st.stop_pending:
    result = order(code, -held)                 # 返回值只做真值提示，不读字段
    # 事实以对账为准（下次检查点重新 get_position 核对）：
    #   T+0 标的 → 当日成交，持仓清零 → 状态清除
    #   T+1 当日新买 → 拒单（PTrade None / 本地 falsy Order）→ stop_pending=True
    #   受理但未成交（零量 bar）→ 持仓未变 → stop_pending=True（不得依赖返回值）
if st.stop_pending and st.buy_date < today:     # 次日盘前已解锁
    result = order(code, -held)                 # 次日首个窗口重试，成交即清除
```

- **每事件最多 1 次被拒**（同日不重复下单），R5 的 `insufficient_sellable` 计数有界、可解释；
- 状态机确定性、无路径依赖，本地 / PTrade 回测 / 实盘同一份代码；
- 平台偏差标的（520830/LOF）本地按 T+0 成交、PTrade 回测拒单顺延——**由本模式自动吸收，策略源码零分支**。

---

## 8. PTrade 1:1 转换兼容性论证（核心章节）

### 8.1 结论

**转换管线（source_import / PyQt 转 PTrade tab）无需为本变更做任何改动**：方案二没有新增策略 API、没有改变任何策略可见的 API 签名、没有引入任何本地专属调用进入策略源码。同一份 skill 生成的本地策略代码可直接 1:1 转换为 PTrade 代码。

### 8.2 语义对照矩阵（本地 vs PTrade，全部实测/源码核实）

| 维度 | 本地引擎（per-code 改后） | PTrade 平台（2026-08-15 实测） | 策略代码要求 |
|---|---|---|---|
| `order()` 返回值 | Order 对象，`__bool__=filled>0` | 受理=order_id 字符串；拒单=**None** | 只做真值判断，**不读 `.status`/`.reason`**，不比较类型 |
| 当日卖 T+1 标的 | 成交 0 股（falsy） | 拒单（None），提示"股票可卖持仓数量为0" | `if not result:` → 标记顺延 |
| 当日卖 T+0 标的 | 成交（truthy） | 受理并成交（truthy） | 一致 |
| 受理但未成交（零量 bar） | Order(filled=0) falsy | 返回 order_id 但持仓不变（truthy）⚠️ | **持仓对账为准，返回值只当提示** |
| 持仓可卖字段 | `Position.can_sell`（本地名） | `enable_amount` / `closeable_amount`（探针命中） | 防御链 `_attr_number(pos, ('enable_amount','closeable_amount','can_sell'))` |
| T+1 次日解锁 | 盘前 `can_sell=volume` | 次日可卖（探针 Day2 全成交） | 次日首个窗口重试 |
| 动态池 | `get_etf_list_local`（本地专用） | 转换时冻结为 `ETF_POOL_STATIC` | 既有转换规则，**与本变更正交** |
| 审计行 `QS_REBALANCE_AUDIT` 等 | 本地日志 | 转换后为普通 log 行（无害） | 既有规则 |

### 8.3 与转换管线的接口面

- 本变更**不触碰** `source_import.py` 的 API 白名单/签名映射——策略源码中出现的所有调用（`order` / `get_position` / `get_open_orders` / `get_history` / `get_etf_list_local`）均已有既定转换规则；
- 止损状态机只使用 PTrade 注册契约内 API（`order`、`get_position`、`get_open_orders`，见 `ptrade-api-signatures.json`），无新增依赖；
- `set_backtest`/`is_trade` 等本地扩展不进入止损路径（既有剥离规则覆盖）。

### 8.4 平台差异记录（写入转换文档与 `etf-t0-rules.md`）

| 标的 | 本地引擎（真实规则） | PTrade 回测 | 吸收机制 |
|---|---|---|---|
| 520830.SS（qdii） | T+0 放行当日卖 | T+1 拒单 | 策略拒单→顺延次日 |
| 161226/501018/162411（LOF，不在 etf_basic） | fail-closed T+1 | T+1 | 两侧一致 |
| 513100.SS（qdii） | T+0；本地有分钟数据（68125 bar），但存在 1321 根零量 bar、09:35 近期连续零量——**引擎不建模成交量门槛，零量 bar 仍成交** | 分钟 bar 交易量=0，订单无法成交 | 本地成交 vs 平台不成交 = **已知撮合近似**；持仓对账未成交→顺延；转换文档标注数据缺口 |

### 8.5 转换后行为差异矩阵（三环境）

| 场景 | 本地回测 | PTrade 回测 | PTrade 实盘/模拟 |
|---|---|---|---|
| 当日买入国内 ETF → 当日止损 | 拒单→次日卖 | 拒单→次日卖 | 柜台拒单→次日卖 |
| 当日买入 520830 → 当日止损 | 当日卖（真实 T+0） | 次日卖（平台 T+1） | 当日卖（交易所 T+0） |
| 当日买入 LOF → 当日止损 | 次日卖（fail-closed） | 次日卖 | 当日卖（交易所 T+0） |

> 策略在三个环境的**收益差异仅来自止损执行时点（当日 vs 次日）**，且全部由拒绝处理模式自动决定，不存在"策略逻辑分叉"。**差异来源已知（T+0/T+1 分类与平台差异）、次数有界（每个止损事件 ≤1 次顺延），但盈亏方向不确定（次日成交价可高可低）**，属可接受近似（写入 R2.5 execution_approximations）。

---

## 9. 测试计划与回归验收

### 9.1 引擎单测（新增 ~10 例）

| 用例 | 期望 |
|---|---|
| 跨境 ETF（513100 qdii）当日买→当日卖 | 成交（can_sell 即时解锁） |
| 商品/黄金/债券/货币（518880/159985/511010/511990）当日买→当日卖 | 成交 |
| 国内股票型（510300 equity）当日买→当日卖 | 成交 0 股（rejected, falsy） |
| 昨日买入 equity 今日卖 | 正常成交 |
| 520830（qdii，平台偏差标的） | 本地按 T+0 放行（真实规则） |
| LOF（161226，不在 etf_basic） | fail-closed T+1（0 股） |
| etf_basic 查询失败 | 全 T+1 + warning，不崩 |
| daily-bar-v1 | 行为与现状一致（per-code 不生效） |
| `--etf-t0 false` | 全 T+1，与现状一致 |
| 盘前解锁 | T+1 持仓次日 can_sell=volume |

### 9.2 黄金结果回归（铁律验收，双档钉死，ZCode 修订1/6）

**回归分两档，全部自动化**：G2 交付 `scripts/etf_t0_regression.py`（hash 比对 + 逐字段 diff + 差异归因清单输出），**禁止人工比对**。

**(a) 零差异档（hash 相等，不是"归因"）**
- 载体：`ETF动量.py`、`tech_etf_mvo_rotation_quantstudio.py`、`etf_theme_rotation_quantstudio.py`，以 `--etf-t0 false`（默认值）分别按 daily profile 与 minute-bar-v1 复跑；
- 标准：修复前后 `config.csv / daily_stats.csv / trades.csv` **SHA-256 逐位一致**；同时守护 §5.1"零查询零 warning"要求——**运行日志 diff 也必须为空**；
- **日志比对须防时间戳假失败（ZCode G2 注记2）**：运行日志含时间戳，回归脚本须**规范化时间戳**（正则替换时间列）或只比对 WARNING 及以上级别行，否则 (a) 档必然误报；
- 任何字节级差异 = 验收失败，立即回退。

**(b) 允许差异档（per-code 语义载体，差异逐条归因）**
- 载体：`ptrade/t0_t1_probe_ptrade.py`（24 只探针，本地引擎可直接运行——仅用 `order/get_position/get_open_orders` 等双平台共有 API），以 `--etf-t0 true --profile minute-bar-v1` 复跑；
- **基线必须是真实产物（ZCode G2 注记1）**：变更前"24 只当日卖全部成交"的基线，必须在**变更前代码**（git worktree/stash 检出）上实际运行探针产出并留存 hash，**不得凭预期填写**；
- 预期差异（= per-code 语义本身）：变更前 24 只当日卖**全部成交** → 变更后 **equity 5 只拒单（falsy）**、其余 19 只成交（520830/LOF 按真实规则 T+0 放行）；**513100 归因措辞（ZCode 数据事实修正，2026-08-15 独立复核一致）**：本地存在零量 bar（68125 bar 中 1321 根零量、09:35 近期连续零量）但引擎不建模成交量门槛故**本地仍成交**，与 PTrade 零量不成交形成**已知撮合近似**——差异清单据此逐条归因并与平台探针结果交叉印证；
- 标准：差异仅限上述语义及其下游净值；任何超出范围差异（撮合价/费用/其他订单/其他日期/持仓结构）= 验收失败；
- 载体选择依据（已核实 2026-08-15）：output/ 下**无任何历史 `etf_t0=true` 分钟运行产物**；bbi_etf_rotation 为 `run_daily(15:00)` 日线驱动策略，无法触发 per-code 同日买卖路径——故 (b) 档采用探针文件；如仍需"策略级 true 档"，可补跑 bbi_etf_rotation 的 true 档并归入 (a) 档（预期零差异，仅验证无影响）。

### 9.3 回退条件

引擎改动单文件可逆（`_is_t0` + 缓存 + 一行调用点）；回退 = 恢复 `is_etf_t0 = self.etf_t0 and _is_etf_code(code)`，回归对比自动通过。

---

## 10. 文档同步与发布（AGENTS.md 铁律，含 ZCode G2 条件 3）

同一变更、一起提交、一起推送（G4 一次性提交）：

1. 代码：`backtest_engine.py`（+ 单测）；
2. **G1 遗留测试文件（ZCode 硬性要求，不得遗漏）**：`tests/conftest.py`、`tests/test_minute_t1.py`、`tests/test_minute_order_execution.py`（G1 引擎改动已在 ea9cc8a 提交，测试文件仍未提交，须随 G4 一并提交）；
3. skill：`SKILL.md`（PTrade compatibility rules 增补 T+0 章节 + design JSON 字段模板）、`references/etf-t0-rules.md`（新建）、`schemas/agent_strategy_design.schema.json`（etf_t0_enforcement / stop_deferral_semantics）、`scripts/agent_skill_common.py`（etf_t0_contract_errors）、`scripts/validate_agent_strategy.py`（STOP-DEFERRAL-SEMANTICS-MISSING / ORDER-RETURN-FIELD-READ / NONDETERMINISTIC-ITERATION 三条 BLOCK 规则）、`tests/test_ptrade_agent_validator.py`（新增 6 例）；installed 副本已同步；
4. **G2 交付物**：`scripts/etf_t0_regression.py`、`docs/evidence/etf-t0-g2-regression-20260816.md`、`output/g2_etf_t0_regression/`（捕获证据，其中 `quantstudio_g2.db` 19.9GB **不得进入 git**，G3 复核完成后删除）；
5. 文档：`README.md`（per-code 语义 + T2 降级说明）、`docs/strategy_toolbox.md`（§3.7.1 per-code 节 + §6.3 .status 禁令边界澄清）、`docs/prompt_engineering.md`（§4 订单返回行边界澄清 + ETF T+0 分类行）、`CHANGELOG.md`（新建，**明示 `--etf-t0 true` 语义由"全 T+0"重定义为"per-code"**，附理由；旧"全 T+0"CLI 不可达）、本设计文档归档 `docs/etf-t0-per-code-design.md`；**平台差异表落点 = `skills/.../references/etf-t0-rules.md`**（`docs/ptrade-conversion-tab-spec.md` 为转换 tab 轨道在途未跟踪文件，本次不触碰，待其落地后由该轨道补表）；
6. 推送：`git push origin`（多 push URL 双仓库同步）。

**未经 ZCode 审核通过 + 用户确认，不实施、不推送。**

---

## 11. 实施步骤（WBS，含门禁）

| 阶段 | 内容 | 门禁 |
|---|---|---|
| G0 | 本文档 ZCode 审核 | 审核通过 |
| G1 | 引擎实现（§5）+ 单测（§9.1） | 单测全绿 |
| G2 | 黄金结果回归（§9.2，自动化脚本 `scripts/etf_t0_regression.py`；(b) 档基线须在变更前代码 git worktree/stash 上实际运行产出；(a) 档日志比对须规范化时间戳/只比对 WARNING+） | (a) 档 hash 逐位一致（含日志零 diff）+ (b) 档差异清单全部归因到 per-code 语义 |
| G3 | skill 契约（§6）+ 校验器（含 T3 确定性 BLOCK 规则）+ 测试 + **G3.5 R5 复现性双跑门禁（证据 2.1，reproducibility_artifacts 三件套 hash 一致）** | validate 全绿 + quick_validate PASS + 测试全绿 |
| G4 | 文档同步（§10）：README / strategy_toolbox（§3.7.1 + .status 边界）/ prompt_engineering（§4）/ CHANGELOG（新建，`--etf-t0 true` 重定义明示）/ 设计文档 v1.3 / T2 GUI 降级说明；一次性提交（含 G1 测试文件与 G2 交付物），**未推送** | README/docs/skill 一致性检查 + ZCode G4 审核 + 用户确认 |
| G5 | 双仓库推送 | 用户确认 |
| G6 | 新策略（ETF 三阶段动量）R3-R5 用 per-code 模式跑通 | R5 PASS（hash 证据） |

---

## 12. 决策点与 ZCode 裁决（2026-08-15）

| # | 决策点 | ZCode 裁决 | 本文落实 |
|---|--------|-----------|---------|
| 1 | 平台偏差标的立场（520830/LOF） | **同意推荐**：本地按真实交易所规则 T+0（实盘行为与本地一致；与 PTrade 回测差异由拒绝处理模式吸收） | §4 / §8.4 |
| 2 | GUI 三态控件默认值 | **否决"默认按分类"**：双端默认必须一致 = 全部T+1；"按分类"放控件首位但须显式选择（防 3e9bb91 双端分叉复发；design JSON 显式声明后运行时显式开启，链路自洽） | §5.3 |
| 3 | CLI 默认值 | **同意**：默认 false 不变；补充注记——`true` 语义从"全 T+0"重定义为"per-code"是本变更核心修正（旧语义本身错误），README/CHANGELOG 明示为带理由的行为变更；旧"全 T+0"CLI 侧不再可达，仅 GUI 保留研究用 | §5.3 / §10 |
| 4 | fail-closed 方向 | **同意**：未知→T+1（自动覆盖 LOF 与退市标的） | §4 |
| 5 | 验收基线集合 | **需追加**：经核实无历史 etf_t0=true 分钟产物，要求 per-code 语义载体——载体采用探针文件（本地可移植、与平台结果交叉印证）；bbi 日线档归入 (a) 零差异档 | §9.2 |

---

## 13. 技术债与后续项（ZCode 记录项，2026-08-15）

| # | 项 | 处置要求 | 状态 |
|---|----|---------|------|
| T1 | 私有属性访问：引擎经 `providers.market._data` 触达 `DuckDBDataAccess`（`_load_etf_t0_cache`），属私有访问。功能正确且 fail-closed | 建议 G3 或后续小项改为 provider 公共方法 / capability 接口（如 `market.get_etf_t0_map()`），并在 skill 能力清单登记 | 待办 |
| T2 | GUI 三态控件缺口：当前 GUI 布尔 `etf_t0`（默认 False=全部T+1、勾选=per-code）已满足裁决2 双端默认统一契约；但 §5.3 承诺的三态控件（含"全部T+0 研究模式"）**必须在 G5 推送前补齐，或在 §10 文档同步时显式降级说明——二选一，不得无声遗漏** | G5 前二选一 | **已处置（2026-08-16，G4 选择"文档显式降级"）**：GUI 保持布尔 `etf_t0`（True=按分类）；"全部T+0 研究模式"暂缓实施（需引擎扩展，另行立项），降级说明已写入 README（per-code 条目）与本设计文档 §5.3/§13 |
| T3 | 存量策略（ETF动量/etf_theme_rotation 等）存在依赖 dict/set 迭代顺序的决策与日志逻辑（跨进程结果不稳定，G2 回归已用 PYTHONHASHSEED=0 固定） | skill 生成策略必须避免此类逻辑（确定性要求），建议在 skill 契约/校验器明示 | 待办 |

## 14. 并发提交事件记录（2026-08-16，需用户知悉）

G2 执行期间（2026-08-16 凌晨至上午），其他代理（CodeBuddy/workbuddy）提交并推送了引擎改动：
`005da48`（ETF 除权补正）、`72182ee`/`68f2f60`（ETF 现金分红入账）、`ea9cc8a`（本方案 G1，内容与 G1 产出逐行一致，提交信息明确标注归属本方案）。

- `ea9cc8a` 的提交**早于 G2 通过与用户确认，违反 AGENTS.md 铁律**（框架层改动须确认后提交推送）；
- 影响：首轮 (a) 对照（f69462e vs HEAD）daily 格被分红/除权改动污染（真实交易差异），已通过"HEAD−G1 vs HEAD"修正对隔离并验证通过（G2 报告 §1.2/§4）；
- 处置建议：因内容恰为已审 G1 且 G2 现已通过，**保留该提交**；G4 文档同步与最终推送仍按流程补办，勿再发生未确认推送。

---

## 附录 A：证据文件索引

- 探针源码：`ptrade/t0_t1_probe_ptrade.py`（v2，2026-08-15 22:31 运行，策略"测试22"）
- 平台日志：2026-08-15 会话内 `[T0PROBE]` 日志（测试21 v1 首轮 + 测试22 v2 双轮）
- 本地数据分类依据：`data/quantstudio.db` → `etf_basic.fund_type`（8/13 备份实测：equity 1300 / qdii 250 / money 27 / gold 13 / bond 13 / commodity 3，1606 全覆盖）
- 引擎现状代码：`backtest_engine.py:92-127, 393, 800-813, 828-829`；`ptrade_api.py:951-983`
