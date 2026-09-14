# R4 报告 + R5 计划 · 股息防守小市值五日轮动

- 阶段：**R3 生成完成 → R4 静态校验 PASS** → 本文件（R4 报告 + R5 计划），待报送
- 设计：`agent_strategy_design.json`（design 2.3，schema VALID，R2.5 已关闭，五键齐备）
- 产物：`agent_workspace/dividend_defense_smallcap_5d/strategy.py`（R3 生成，538 行）

## 一、R4 静态校验结果

```
validate_agent_strategy.py --design <design> --project-root <repo> <strategy.py>
status: PASS | profile_validation_status: PASS
runtime_validation_status: NOT_VERIFIED | deployment_status: NOT_DEPLOYABLE
block_count: 0 | warning_count: 1
```

**唯一 WARN**：`HARDFILTER-LIMIT`（「PTrade 回测无公开 check_limit；说明由订单拒单/价格字段承接涨跌停语义」）——
该 WARN 属文档性提示，已在策略源码订单路径处显式注释：回测源**禁止**调用 `check_limit`（trading-context API），
涨停不买 / 跌停不卖由**引擎订单层** `is_price_limit_blocked` 原生阻断（`limit_up_blocked` / `limit_down_blocked`），
策略只提交订单并列账；被阻断买单自然保留现金（不递补），被阻断卖单进入 `pending_exits` 逐日顺延重试。

### 1.1 生成期间按 R4 反馈修正的三处（均已回归 PASS）

| # | 规则 | 问题 | 修正 |
|---|---|---|---|
| 1 | `NONDETERMINISTIC-ITERATION` ×6 | 四处 `set()` 用法（持仓集合、pending 去重、交集） | 全部改为 list + `sorted` / `dict.fromkeys`，确定性容器 |
| 2 | `RUNTIME-STATE-IDEMPOTENCE` ×5 | `_ensure_runtime_state` 以单一 flag 守卫 | 改为**逐属性 `hasattr` 守卫**（重复调用不重置任何状态） |
| 3 | `PORTFOLIO-RUNTIME-VALUE-MISSING` | 缺 runtime 总资产读取入口 | 新增 `_portfolio_total_value(context)`（portfolio_value → total_value → total_asset → cash+market_value 兜底），两处调用点统一走它 |
| 附 | `DESIGN-CODE-API` WARN | 设计声明 `get_positions()` 但代码未调用 | `_audit` 改经 `get_positions()` 读取持仓视图（设计 13 API 全部落地） |

### 1.2 rule 17（runtime-shape fixture）义务免除的依据（审计要求显式记录）

**R3 实测三形态**：
- `get_history(..., is_dict=True)` → `{code: item}`（key 正确映射）
- `get_history(..., is_dict=False)` **多标的** → DataFrame 仅 `close` 一列，**无 code 列且 index 恒为 `[-1,-1,-1]`** → 不可映射，实际不可用
- `get_history(..., is_dict=False)` **单标的** → DataFrame(1,1)，干净可用

**实现选择**：价格一律走**逐标的 `is_dict=False`**（返回普通 DataFrame，非 mapping item）→
**不产生 `is_dict=True` 绑定变量** → rule 17 的 `_extract_history_field` 义务与 runtime-shape fixture 均**不适用**。
（校验器实现复核：rule 17 检查以 `_is_dict_history_call` 追踪的变量为作用域；本策略零命中 → 义务不成立。）

## 二、R3 自检清单（8 条纪律逐条核对）

| # | 纪律 | 落位 | 状态 |
|---|---|---|---|
| 1 | `_ensure_runtime_state()` 为每回调首语句 | `initialize` / `handle_data` / `after_trading_end` 首行 | ✅ |
| 2 | 全 T-1 PIT；T 日仅执行；`fq='pre'`+`include=False`；`.SS/.SZ` | 价格读取唯一出口 `_t1_prev_close` 带字面两参；`_portable` 归一 | ✅ |
| 3 | 持仓六字段白名单 + 按 code 索引对齐；禁 `.value`/位置取值/哈希序 | `_position_amount`/`_position_enable`/`_position_market_value` 只用白名单字段；全文零 `set(` | ✅ |
| 4 | 股息游标增量 + 事件缓存；等价性硬测独立载体 | `_scan_dividends` + `verify_dividend_cache_equivalence.py` | ✅（见 §三） |
| 5 | 参数冻结零寻优 | 文件头常量块集中声明，注释标注来源 | ✅ |
| 6 | 执行层三项仅执行层 | 涨停/跌停由引擎订单层承接；停牌换出保留 `pending_exits` 重试；**均不进入过滤池与排序函数** | ✅ |
| 7 | 每期 `QS_REBALANCE_AUDIT` + `QS_PORTFOLIO_AUDIT`；候选 <5 标 A-9 原因 | `_audit()`，同 `rebalance_id` 一一对应；note 含 `candidates_below_5_legal(A-9)` / `target_below_5_legal(A-9)` / `exit_deferred:N` | ✅ |
| 8 | R5 预备（见 §四） | — | 待 R5 |

**过滤顺序说明（性能，非逻辑）**：合取条件顺序不影响结果池，故把最贵的**股息逐日扫描**放在全部谓词之后；
且「股息率>0」过滤**不需要价格**（Σ bonus_ps > 0 ⟺ 股息率 > 0，分母恒正），价格仅在防守月排序时逐标的取。

## 三、等价性硬测（A-7 性能契约①）· 首次执行记录

```
python agent_workspace/dividend_defense_smallcap_5d/verify_dividend_cache_equivalence.py
asof 序列: ['20260724', '20260727', '20260728', '20260729', '20260730']
比较组数: 40 | 标的数: 8 | asof 数: 5
事件缓存条目总数: 15
比较组失败数: 0
RESULT: PASS - 游标增量与全窗逐日扫描逐值相等
```

- **执行时间点**：2026-09-12（R3/R4 阶段首跑）；**R5 前须复跑一次**，结果与本节一并入验收证据
- **断言**：① 十二个月股息合计逐值相等（`abs(diff) <= 1e-9`）；② 窗口内事件列表（ex_date, bonus_ps）逐条相等

## 四、R5 计划

| 项 | 值 |
|---|---|
| 主跑窗口 | 2020-01-01 ~ 2026-07-31 |
| init_cash | 100,000 |
| 基准 | `set_benchmark('000300.SS')` |
| 引擎 | daily-bar-v1 / match_price=close |
| 数据源 | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db` |
| **000300 水位复核** | 见 §4.1 |
| G3.5 复现门 | 双进程独立复跑，config/daily_stats/trades 三件套 SHA-256 逐位一致 |
| 分段报告 | 样本内 2020-01-01~2024-12-31 / 样本外 2025-01-01~2026-07-31 |
| R5.5 | 门控**已启用（无豁免）**：WF 5 折 + MC 1000 + G1-G6 独立呈现 |

### 4.1 000300 水位复核结果（2026-09-12 实测）

| 表 | 最新日期（北京时间） |
|---|---|
| `index_daily` code=000300（基准） | **2026-07-31** |
| `stock_daily`（个股） | 2026-09-04 |

**结论**：基准 000300 仍未补齐 8–9 月数据（与 R0/R1 阶段实测一致）。
**主跑窗口维持 2020-01-01 ~ 2026-07-31**；按 R0 C2 附条件「若已补齐 8–9 月数据则是否顺延由客户届时裁定」——
当前**未补齐，故无顺延空间**，窗口不变，无需再请裁定。

### 4.2 墙钟预声明（A-7，防误判挂起）

| 阶段 | 预期墙钟 | 依据 |
|---|---|---|
| R5 主跑 | **约 16 分钟量级** | 股息游标增量缓存生效；若无缓存约 114 分钟 |
| R5.5 五折重扫 | **约 80+ 分钟** | 五折各扫一次股息窗口（缓存不跨运行持久化） |
| 等价性硬测 | 秒级 | 8 标的 × 5 asof |

> 运行说明须写明上表，避免被误判为卡死。
