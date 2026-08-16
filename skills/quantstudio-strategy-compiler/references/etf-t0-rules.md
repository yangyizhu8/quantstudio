# ETF T+0/T+1 per-code 执行契约（skill 参考）

> 依据：`docs/etf-t0-per-code-design.md`（v1.3，ZCode G0 关闭 / G2 双档验收通过 / 2026-08-16）
> 实证：2026-08-15 PTrade 平台双轮探针（24 只标的）+ 2026-08-16 本地引擎 G2 双档回归
> 适用：所有 skill 生成的、可能交易 ETF 的本地策略（PTrade 可移植面）

## 1. 分类规则（真实交易所规则，唯一真相）

| etf_basic.fund_type | 交易制度 | 说明 |
|---|---|---|
| `qdii` / `gold` / `commodity` / `bond` / `money` | **T+0** | 买入当日 `can_sell` 即时解锁 |
| `equity` | **T+1** | 当日新买 `can_sell=0`，卖出成交 0 股，次日盘前解锁 |
| 未知代码（不在 etf_basic，如 LOF、退市） | **T+1**（fail-closed） | 宁可保守拒绝，不可越权放行 |

- 引擎装载仅当 `engine_profile='minute-bar-v1' and etf_t0=True`（懒装载，`_is_t0` 短路）；
- 装载经 provider/数据访问层（`query_etf_fund_types`），失败/空 → 全 T+1 + warning；
- `etf_t0=False`（默认）/ daily profile 恒 T+1，零查询零 warning。

## 2. 引擎行为契约

- 买入路径 `is_etf_t0 = self.etf_t0 and self._is_t0(code)`：T+0 立即解锁 `can_sell`；
- 卖出路径：`can_sell<=0` → 成交 0 股（本地 Order `status=rejected`、布尔 falsy；PTrade 拒单返回 None）；
- 盘前解锁：每交易日开始 `can_sell = volume`（T+1 持仓次日可卖）；
- CLI `--etf-t0`：默认 false 不变；`true` 语义 = per-code（旧"全 T+0"CLI 不可达）；
- GUI：三态控件（按分类/全部T+1/全部T+0），默认全部T+1，"按分类"需显式选择。

## 3. 策略必守模式（生成代码必须遵守）

1. **止损/卖出状态机 = "触发即锁 → 尝试卖出 → 持仓对账 → 未成交挂起 → 次日重试"**：
   - 触发即锁（价格回升不撤销）；
   - 当日可卖（T+0 或非当日买入）→ 当日成交；当日不可卖（T+1 当日买入）→ 顺延次日首个可卖窗口；
   - 每事件最多 1 次被拒（同日不重复下单），R5 `insufficient_sellable` 计数有界可解释。
2. **订单返回值只做真值判断**（`if not result:`），**禁止读取本地 Order 字段**（`.status`/`.reason`）——
   本地返回 Order 对象（`__bool__ = filled>0`）、PTrade 返回 order_id/None；
   边界：本禁令仅限 skill 生成的 PTrade 可移植策略，本地专用策略不受限。
3. **事实以持仓对账为准**：受理但未成交（如零量 bar）时返回值真值两平台不一致
   （PTrade truthy / 本地 falsy），必须用 `get_position()` 复核。
4. **买入日期由策略自维护状态账本**（`g.stop_state[code] = {buy_date, avg_cost, ...}`）——
   PTrade Position 无 buy_date 字段，禁止读取。
5. **确定性要求（T3）**：禁止 `set()`/`frozenset()` 及依赖哈希迭代顺序的决策逻辑
   （跨进程结果不稳定）；使用 `list` + `sorted()`。**set 字面量同样禁止用于迭代定序**
   （`for x in {'a','b'}` 的哈希顺序同样随进程随机——校验器机械规则拦不住此类，契约文本约束
   + R5 复现性双跑门禁兜底）；`.status`/`.reason` 等本地字段读取禁令可能误伤同名属性——
   **宁可误拦，申诉走人工 review**。
6. 持仓可卖字段防御链：`_attr_number(pos, ('enable_amount', 'closeable_amount', 'can_sell'), 0)`。

## 4. 平台差异记录（2026-08-15 双轮实证）

| 标的 | 本地引擎（真实规则） | PTrade 回测 | 吸收机制 |
|---|---|---|---|
| 520830.SS（qdii 沙特） | T+0 放行当日卖 | T+1 拒单 | 策略拒单→顺延次日 |
| 161226/501018/162411（LOF） | fail-closed T+1（不在 etf_basic） | T+1 | 两侧一致 |
| 513100.SS（qdii 纳指） | T+0；本地有分钟数据（68125 bar，含 1321 根零量 bar、09:35 近期连续零量）——引擎不建模成交量门槛故仍成交 | 分钟 bar 交易量=0，订单无法成交 | **已知撮合近似**：本地零量 bar 仍成交 vs 平台零量不成交；持仓对账未成交→顺延 |

三环境行为差异：收益差异仅来自止损执行时点（当日 vs 次日），来源已知、次数有界
（每止损事件 ≤1 次顺延）、盈亏方向不确定，属可接受近似（写入 execution_approximations）。

## 5. R5 证据说明

- `insufficient_sellable` 拒绝计数 = T+1 当日买入止损顺延的预期行为，每个事件 ≤1 次；
  归因清单须注明（标的/日期/原成交→顺延/净值影响），不得视为部署失败。

## 6. 校验器规则索引（validate_agent_strategy.py）

| rule_id | 触发 | 级别 |
|---|---|---|
| `ETF-T0-ENFORCEMENT-ENUM` | etf_t0_enforcement 不在枚举 | BLOCK |
| `STOP-DEFERRAL-SEMANTICS-MISSING` | engine_per_code 未声明 stop_deferral_semantics | BLOCK |
| `STOP-DEFERRAL-SEMANTICS-ENUM` | stop_deferral_semantics 不在枚举 | BLOCK |
| `ORDER-RETURN-FIELD-READ` | 源码读取 `.status`/`.reason` | BLOCK |
| `NONDETERMINISTIC-ITERATION` | 源码使用 `set(`/`frozenset(` | BLOCK |
