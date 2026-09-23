# R2 Agent 组件计划（修订稿 v2 · E1 定稿后）

> 阶段：R2（Design contract and component plan）— **修订稿**
> 设计契约：`output/generated_strategies/ou_reversal_csi300_10/agent_strategy_design.json`（design_version 2.3）
> 客户中文策略名：**沪深300均值回归超跌反弹** → 发布路径 `quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py`
> 机器标识：`ou_reversal_csi300_10`
> 修订时间：2026-09-22（v1 → v2：E1 原则定稿后的时序/取数/回调收敛修订）
> 随卷凭证：`R2_E1_EVIDENCE_APPENDIX.md`（E1 双落位 + 四格实测 + 用户批复）

---

## 0. 本稿相对 v1 的变更（11 项清单 + 追加 2 条）

| # | 项 | v1 | v2 |
| --- | --- | --- | --- |
| 1 | `engine_profile.profile_id` | daily-open-close-proxy-v1 | **daily-bar-v1** |
| 2 | `engine_profile.match_price_mode` | close | **open**（显式声明；语义版本 `0.1.0-legacy`） |
| 3 | `decision_events` | run_daily(15:00) + run_daily(09:31) | **单 handle_data** |
| 4 | 时序契约 | T 日 15:00 算 → T+1 09:31 成交 | **D-1 收盘信号 → D 开盘成交（S = D-1）**；不写「信号后移」 |
| 5 | 信号取数 | include=True（A′ 豁免） | **include=False**（E1 强制，零例外） |
| 6 | 近似表 A-1 | proxy 两点合成 bar | **E1 链**表述；proxy/A′ 相关条目全部移除 |
| 7 | 择时门指数取数消费规则 | 未显式 | **消费行 = D-1**（count=need+1 → 过滤 trade_date < D → 断言 ≥ need → 末 need 行） |
| 8 | 必查 3 指数行 | 「✅」 | 「**✅（限 D-1 行消费）**」 |
| 9 | R4 断言 | 四格 | **三条新断言**（指数行 ≤ D-1 / 零 include=True 日线 / profile↔0.1.0-legacy）+ 一条待确认 |
| 10 | 旧四格断言 | 本策略断言 | 改列 **E1 实证材料** |
| 11 | QS_SIGNAL 行 | 非下单日轻量行 | 保留 |
| 追加 1 | E1 凭证 | — | `R2_E1_EVIDENCE_APPENDIX.md` 随设计稿同卷 |
| 追加 2 | §8 R5 核账口径 | 仅下单日成对输出 | 保留 + 明确 QS_SIGNAL 用于复盘重建每个决策日 |

---

## 1. 引擎 Profile 与时序契约

| 项 | 取值 |
| --- | --- |
| `profile_id` | `daily-bar-v1` |
| `bar_frequency` | `1d` |
| `match_price_mode` | `open`（订单撮合于 **D 日开盘价**：`backtest_engine.py:1298-1305` 取 `curr_data['open']`） |
| `expected_engine_semantics_version` | `0.1.0-legacy`（`backtest_engine.py:454-458`） |

**时序契约（一路到底）**

```text
D-1（信号日 S）收盘  —— 数据在此截止，之后不再变化
        │
        ▼
D 日 handle_data（唯一决策回调）
   ① get_history(count=61, frequency='1d', fq='pre', include=False)
      → 返回至 S 日（引擎锚定 prev_date = D-1）
   ② get_index_day_bar('000300', count=need+1, fields=['high','low','close'])
      → 契约含 D 日 → 【丢弃 trade_date >= D 的行】→ 只消费至 S
   ③ 计算：大盘择时门 → 五层过滤 → OU 因子降序 Top10
   ④ 先卖后买：order_target_value(code, target_value)
   → 撮合价 = D 日开盘价（match_price_mode='open'）
   → 输出 QS_SIGNAL / QS_REBALANCE_AUDIT / QS_PORTFOLIO_AUDIT
        │
        ▼
D 日收盘：NAV / 持仓估值按 D 日收盘（引擎恒定，:1296）
```

**R0 C2-A 对应关系**：原裁定「T 日收盘信号 → T+1 开盘执行」与本链为同一语义——取 **T = S = D-1**、**T+1 = D**：数据末端（S 收盘）与成交价（D 开盘）逐日一致，**信号时效零损失**，不存在「信号后移一交易日」。
**next_open 说明**：该撮合模式已于 2026-08-13 废弃（把 T+1 数据引入 T 日时间片）；本链用 `open` 模式表达同一语义且无该问题。
**A′ 撤回**：v1 提出的「proxy 15:00 日线 include=True 豁免」与 E1 冲突，总调度裁定不开此第二例外，**未进入实施（零成本撤回，登记为治理转向）**。

---

## 2. 生命周期与 API 组件计划

| 回调 | 用途 |
| --- | --- |
| `initialize` | `set_benchmark('000300.SS')` + `set_commission(commission_ratio=0.00324, min_commission=5.0)` + 状态初始化 |
| `handle_data` | **唯一决策回调**（见 §1 步骤 ①-④） |
| `after_trading_end` | 幂等状态收尾 + 每日审计行 |

**不使用 `run_daily`**：daily-bar-v1 下 `run_daily` 仅允许 `time='15:00'` 或不给 time；`09:31` 会被 `PROFILE-SCHEDULE-MISMATCH` BLOCK（`validate_agent_strategy.py:1177-1181`）。本策略无 intraday 调度需求，单回调时序依赖面最小。

**required_apis（11 项）**：`set_benchmark` · `set_commission` · `get_index_stocks` · `get_stock_info` · `get_stock_status` · `get_history` · `get_index_day_bar` · `get_positions` · `get_position` · `order_target_value` · `log.info`

**E1 合规四要点**
1. 信号取数一律 `include=False`；源码零 `include=True` 日线调用（E1-1）。
2. `match_price_mode='open'` 显式声明（E1-2）。
3. **不使用 `data[code]` 任何字段**（不读 close/high/low/open/volume/preclose）——涨跌停经前复权 close 比值、停牌经 `get_stock_status`，彻底杜绝 raw/前复权混基（E1-3）。
4. `get_index_day_bar` 消费行 = D-1（E1-4）。

---

## 3. 因子定义（全部内联实现）

| 因子 | 公式 | 取数（signal day = S = D-1） |
| --- | --- | --- |
| 过滤①上市 | S − `listed_date` > 100 自然日 | `get_stock_info` |
| 过滤①板块 | 剔除 688/689、920/430/83x/87x | 代码规则 |
| 过滤①停牌 | S 日 suspendFlag==1 或 volume==0 | `get_stock_status(query_type='HALT')` |
| 过滤①涨跌停 | \|close_S/close_{S-1} − 1\| 达板块限幅 | `get_history(count=2, fq='pre')` 前复权比值 |
| 过滤①跌幅 | Σ`pctChg[S−2..S]` < −8% | `get_history(field=['pctChg'])` |
| 过滤②波动率 | `std(returns[S−59..S], ddof=1)·√250` < 30% | 61 根前复权 close |
| 过滤③价格 | S 日收盘 > 2 元 | 前复权 close |
| 过滤④均额 | mean(`amount[S−59..S]`) > 1,000 万元 | `field=['money']`（元） |
| 过滤⑤个股 ADX | Wilder(14) ADX > 20 | 61 根 high/low/close |
| OU 因子 | (MA60 − Close_S)/Close_S 降序 | 61 根前复权 close |
| 大盘择时 | 市场 ADX(14) > 25 且 CSI300 收盘 > BOLL20 中轨 且 中轨_S > 中轨_{S-1} | `get_index_day_bar`（消费至 S） |

**取数批量化**：四个 count（2/3/60/61）统一以 `count=61` 一次性多代码批量取数（`security_list=[...300]`，`is_dict=True`），本地切片；字段 `['close','high','low','pctChg','money','trade_date']`，一律经 `np.asarray(...)` 归一（rule 17）。

---

## 4. 组合与资金契约

| 字段 | 值 |
| --- | --- |
| `sizing_mode` | `runtime_total_value` |
| `allocation_mode` / 分母 | `equal_weight` / `actual_selected_count`（C3-B） |
| `target_holdings` | 10 |
| `gross_exposure_target` / `cash_buffer_ratio` | 0.97 / 0.03 |
| `per_position_target_weight` / `max_single_weight` | 0.097 / 0.11 |
| `allow_leverage` | false |

单只目标市值 = 运行时总资产 × (1 − 0.03) ÷ N（N = 当日实际候选数 ≤ 10）。
**融资契约**：`requires_same_cycle_sell_proceeds=true` + `sell_then_buy_immediate` + `match_price_mode='open'` → 对齐 `execution-funding-matrix` 的 `open` 行（即时顺序执行，同批卖出所得可用于买入），无 BLOCK。

---

## 5. 近似契约附表（13 项）

| ID | 近似 |
| --- | --- |
| A-1 | **执行时序**：S = D-1 收盘信号 → D 开盘成交（include=False + match open）；与 C2-A 同链，无信号后移 |
| A-2 | 涨跌停以前复权 close 比值推导（不用 raw preClose；raw 口径噪声 9,869/2,271,094 ≈ 0.43%） |
| A-3 | ST/停牌绑定 is_st_reliable / suspendFlag（经 get_stock_status 读 `_prev_day_data` = S 日快照）；isST 全 NULL 不可用 |
| A-4 | 成本往返等效 0.00324（0.700%），单边偏差买 +0.025% / 卖 −0.025% |
| A-5 | 基准 2026-08-03 单日缺口（D4-A）——受影响执行日 = 2026-08-04（信号日 08-03 缺失 → 消费末行 07-31） |
| A-6 | 窗口收窄 2025-07-01..2026-09-01，前 117 交易日未覆盖（D2-A） |
| A-7 | 候选不足 10 只按实际数量等权满仓（C3-B） |
| A-8 | 上市日绑定 `stock_basic.list_date`（2018 后上市 1,988 只中 2 只不同日，最大 8 天） |
| A-9 | 涨跌停限幅按板块静态档位（主板 ±10% / 创业 ±20%）；ST ±5% 档已剔除 |
| A-10 | 前复权取值口径由 `fq='pre'` + `include=False` 共同锁定；未做逐行数值断言 |
| A-11 | 清仓后次一交易日立即重建，无冷却期（C4-A） |
| A-12 | 拒单走框架默认拦截，不强制平仓/递补（C10-A） |
| A-13 | **基准/择时指数行含 D**：`get_index_day_bar` daily 契约含 D，必须按 E1-4 显式丢弃（非可选），R4 断言 A4-1 锁定 |

**D3-C 成本三案对照（原样）**

| 方案 | 设置 | 实际买 | 实际卖 | 单次往返 |
| --- | --- | --- | --- | --- |
| A 对齐买入 | `commission_ratio=0.003` | 0.301% | 0.351% | 0.652% |
| B 对齐卖出 | `commission_ratio=0.0035` | 0.351% | 0.401% | 0.752% |
| **C 往返等效（D3-C 裁定）** | `commission_ratio=0.00324` | 0.325% | 0.375% | **0.700%** |

---

## 6. R4 断言清单

**新增必查（3 条）**

| # | 断言 |
| --- | --- |
| A4-1 | 择时门指数行上界：消费的每行 `trade_date ≤ S = D-1`（实现断言 + 静态检查：`get_index_day_bar` 调用后必须出现 `trade_date < current_date` 过滤或 `[-2]` 取法） |
| A4-2 | 源码零 `include=True` 日线调用（AST 扫描；校验器 `NO-LOOKAHEAD-INCLUDE` 现行 BLOCK 已覆盖，R4 复验） |
| A4-3 | `engine_profile` = {daily-bar-v1, 1d, open} 且 R5 `config.csv` 的 `engine_semantics_version == 0.1.0-legacy` |

**待审核方确认（1 条）**

| # | 断言 | 理由 |
| --- | --- | --- |
| A4-4 | `handle_data` 内不得出现 `data[...]` 字段读取 | 本策略零 data 依赖；静态断言可杜绝 raw/前复权混基（E1-3 的机器化） |

**E1 实证材料（不再作为本策略断言）**：四格实测 `probe_include_semantics2.py` → 归档于 `R2_E1_EVIDENCE_APPENDIX.md`。

---

## 7. 窗口契约与预热

- 主跑窗口：**2025-07-01 → 2026-09-01**（287 个交易日，全部命中真实月度 complete 快照）。
- 收窄理由（原文）：**complete 快照空档 2021-03-31 → 2025-07-01（2025-01-02..2025-06-30 共 117 交易日 as-of 回落至 2021-03-31 快照，总调度与审核方双独立实测在卷）；前 117 交易日未覆盖。**
- 预热：`get_history(count)` 无下界（`duckdb_provider.py:82`）；首个执行日 D 的信号日 S 落在窗口起点之前，history 不截断故可用；实证样本在 2025H1 有 ≥108 根前序 K 线、300/300 ≥ 61 根。
- 基准：`set_benchmark('000300.SS')`；库内 000300 覆盖 2018-01-01..2026-09-20，窗口内 403/404 日（缺 2026-08-03，A-5）。

## 8. R5 机器核账口径

本策略**每个交易日**都重算目标集合，但仅在「目标 ≠ 当前持仓」或择时门状态切换时产生订单。
- `QS_REBALANCE_AUDIT` / `QS_PORTFOLIO_AUDIT`：**只在当日实际下单时成对输出**（同一 `rebalance_id`），供 `r5_deployment_invariants` 按 rebalance 核账。
- `QS_SIGNAL date=... targets=... changed=0/1`：每个决策日输出（轻量，**不入正式审计对**），供 R5 复盘重建每个决策日。

## 9. 随卷材料

- R2 设计契约：`agent_strategy_design.json`（本修订稿）
- **E1 凭证**：`R2_E1_EVIDENCE_APPENDIX.md`
- R0 裁定链：`agent_workspace/ou_reversal/workspace_state.json`
- R1 证据：`agent_workspace/ou_reversal/R1_capability_evidence.md` + `capability_report.r1.json`
- 探针脚本 21 个（`agent_workspace/ou_reversal/`）+ 校验脚本 4 个
