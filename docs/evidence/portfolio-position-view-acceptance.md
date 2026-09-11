# 验收证据：持仓视图契约修复（2026-09-04）

- 方案：`docs/portfolio-position-view-contract-design.md`（审计通过：5 项实施条件 + 4 项打磨项）
- 取证：`docs/evidence/portfolio-position-view-defect.md` · 影响面：`docs/evidence/portfolio-position-view-impact.md`
- 写前快照（零副作用回退点）：`git stash create -u` → `d479d95d3bc5ad1faa7a8f49bba4ff8350f2a68e`（stash@{0}，覆盖改动清单全部代码文件）
- 会话备份：`%TEMP%/pv_fix/{ptrade_api.py, backtest_engine.py, test_strategy_alignment_regressions.py}`

## 零、数据源与运行配置（出处纪律）

| 用途 | 数据源绝对路径 | 配置 |
|---|---|---|
| 验收 ①⑤ 全部 pytest | 不适用（单测/桩对象） | 受控测试文件清单 |
| **验收 ③ 小市值隔夜重跑** | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db` | 窗口 2026-07-01 ~ 2026-07-31；引擎 daily-bar-v1；match_price=close；capital=100,000；成本=框架默认 |
| 验收 ④ 6 策略转换 | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db`（仅 etf_basic 查询） | `--no-smoke --engine-profile daily-bar-v1` |

## 一、改动清单（精确文件清单）

| # | 文件 | 修复后 SHA-256(16) | 改动性质 |
|---|---|---|---|
| 1 | `quantstudio/backtest/ptrade_api.py` | `ED093FC7CE55E705` | `_engine()` 单次解析；`positions` 委托适配器；`get_positions`/`get_position` 传价；`Position.__init__` 增可选 `enable_amount` |
| 2 | `quantstudio/backtest/backtest_engine.py` | `98932494661B5935` | `_get_ptrade_positions` 传 `enable_amount = can_sell − pending_sell_shares`；docstring 对齐唯一适配器契约 |
| 3 | `tests/test_position_view_contract_parity.py` | 新增（`B1A3930D3F3D63EF`） | 双侧 parity（常量 dict 驱动） |
| 4 | `tests/test_position_view_engine_contract.py` | 新增（`0A7484B46F3C0F4E`） | 三入口一致性 / 只读快照 / 空仓语义 / 键归一含 BJ / 无引擎分支同形状 / 取价两态 |
| 5 | `tests/test_strategy_alignment_regressions.py` | 修改（`081FE7EA31C56F60`） | 两条既有红测试转绿 + `_api` 单例跨测试残留的测试隔离修复 |
| 6–8 | `docs/portfolio-position-view-contract-design.md` / `docs/evidence/portfolio-position-view-acceptance.md` / `docs/evidence/portfolio-position-view-impact.md` | 新增 | 方案 / 本文件 / 影响面 |
| 9 | `README.md` / `docs/strategy_toolbox.md` / `docs/prompt_engineering.md` | 修改 | 契约同步 |

## 二、验收 ① · 既有红测试转绿

```
python -m pytest tests/test_strategy_alignment_regressions.py -q
4 passed in 1.28s
```

修复前该文件为 `2 failed, 2 passed`（`test_portfolio_position_suffixes_match_ptrade_exact_container_semantics`、
`test_etf_momentum_keeps_ptrade_exact_membership_regression` 两条长期红灯）。**转绿** ✅

**根因补充**：两条用例本身断言正确，失败源于 `_api` 模块级单例被其他测试残留 `_engine` 污染；
本次一并补测试隔离（用本用例自己的引擎并在 `finally` 恢复）。

## 三、验收 ② · 最小复现转绿

`agent_workspace/dividend_defense_smallcap_5d/probe_position_view.py`：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 元素类型 | `backtest_engine.Position` | **`ptrade_api.Position`** |
| `amount` | `<MISSING>` | **100** |
| `enable_amount` | `<MISSING>` | **100** |
| `cost_basis` | `<MISSING>` | **0.86** |
| `last_sale_price` | `<MISSING>` | **0.86** |
| `sid` | `<MISSING>` | **`159870.SZ`** |
| 策略判定 | 「空仓 / 不可卖」（误判） | **「有持仓 / 可卖」** |

**转绿** ✅

## 四、验收 ③ · 小市值隔夜恢复正常买卖回合（**正确性变更关单**）

同窗口 2026-07-01 ~ 2026-07-31，主库 `data/quantstudio.db`：

| 观测面 | 修复前 | 修复后 |
|---|---|---|
| `trades.csv` action 分布 | buy 20 / **sell 0** | **buy 65 / sell 63** |
| `round_trips.csv` | 5 字节空文件（0 回合） | **3,438 字节 / 64 行（63 回合）** |
| 回合样例 | — | `2026-07-03 → 2026-07-06 / 600281.SH / pnl=-264.28 / hold_days=3` |
| 策略侧 `Post-close held=[]` | **23 / 23 全空** | **7 / 23**（其余 16 个交易日正常持有） |
| `daily_stats.positions` | 07-03=2 → 07-18 递增（引擎侧） | 0/1/2/3/4/5/7 正常分布 |

**关单口径**：本项为**正确性变更** —— 修复后策略行为符合 PTrade 契约（能读持仓、能按时出场），
**不以「修复前后一致」关单**。修复前的「只买不卖」正是缺陷表现。

## 五、验收 ④ · 6 策略转换产物逐位一致

基线 = B2 推送后 / 本修复前（`%TEMP%/b2_verify/conv_post`）；对照 = 本修复后（`%TEMP%/pv_fix/conv_pv`）。

| 策略 | 转换产物 SHA-256(24) | 判定 |
|---|---|---|
| CANSLIM突破成长选股策略 | `949679f2370890e448bfe974` | **SAME** |
| fall_reversal | `77aaa7a7f1ebc67feeffaaaf` | **SAME** |
| tech_etf_mvo_rotation | `a884eac2c6909d90aa095058` | **SAME** |
| vol_regime_mom_rev | `6b1516fd7ddf3ae6d3849fe7` | **SAME** |
| weekly_smallcap_growth_momentum_10 | `98b25cd100cc0b9345aa30de` | **SAME** |
| 周频小市值成长动量（三层止损） | `3d29403e25062c5a79e935e5` | **SAME** |

**6/6 逐位一致** ✅ —— 证实本修复未触转换管线（§3.1「不下沉、不触模板」成立）。

## 六、验收 ⑤ · 全套回归绿 + 契约门

1. **受控测试子集（12 文件）**：`274 passed / 7 failed`；
   7 项失败**与修复前逐项一致**，全部是 `tests/test_source_import.py` 的 `include` 语义既有失败
   （`test_22 / 23 / 27 / 32 / 33 / 34 / 35`）→ **零新增失败** ✅
2. **本修复新增/修改测试全绿**：
   - `tests/test_position_view_contract_parity.py` **4 passed**
   - `tests/test_position_view_engine_contract.py` **7 passed**
   - `tests/test_strategy_alignment_regressions.py` **4 passed**
   - `tests/test_valuation_date_pit.py`（B2 常设回归）**7 passed**
3. **契约回归门**：见 §八 终核结果。

### 6.1 测试断言更新清单（终审打磨项③ —— 与回归失败口径分离）

| 测试 | 旧断言 | 新断言 | 依据 |
|---|---|---|---|
| `test_portfolio_position_suffixes_match_ptrade_exact_container_semantics` | 无引擎状态下断言 `portfolio.positions`（依赖 `_init_positions`） | 同断言 + **显式挂载本用例引擎并 `finally` 恢复**；新增 `amount/enable_amount/cost_basis/last_sale_price` 契约断言 | 契约修复 + `_api` 单例隔离 |
| `test_etf_momentum_keeps_ptrade_exact_membership_regression` | 同上 | 同上（键精确匹配断言不变） | 同上 |

**无任何既有测试被改写为「迁就实现」**：两条用例的原始语义断言（exact-match 键、非 alias-aware）**原样保留**，
仅补测试隔离与契约字段断言。

## 七、验收 ⑥ · 只读快照语义 + 热路径微基准

**只读快照**：`test_positions_is_read_only_snapshot` 断言「清空返回容器 + 塞入垃圾键」后引擎 `account.positions` 不变 ✅

**微基准**（`agent_workspace/dividend_defense_smallcap_5d/bench_position_view.py`，400 次取 P50/P99，单位 µs）：

| 持仓数 | 命中路径 P50 / P99 | 兜底路径 P50 / P99 |
|---|---|---|
| 5 只 | 11.3 / 38.6 | 7.0 / 30.9 |
| 20 只 | 20.5 / 71.4 | 22.0 / 59.9 |
| 60 只 | 57.2 / 150.7 | 56.2 / 112.8 |

**同口径对照（20 只）**：`get_positions()` P50 = **32.8 µs** ｜ `context.portfolio.positions` P50 = **33.7 µs**（**+2.7%**）。

**劣化判定**：修复前的 `dict(acc.positions)` 浅拷贝更快，但它返回的是**非契约对象（错误数据）**，
不能作为有效基线；有效基线是**等价的正确路径** `get_positions()` → 本修复与之一致（+2.7%，远小于 2 倍阈值）
→ **不触发回退条件④**。绝对耗时（60 只 57 µs）对日频策略可忽略。

## 八、契约门终核

```
python scripts/run_contract_gate.py --strategies
OK: 契约/矩阵门禁通过（哈希一致 + MD 一致）
契约套件（pytest，受控文件清单 + 既有失败白名单）：全部通过；白名单无触发
矩阵门禁（check_fund_matrix --check）：无输出（通过）
6 策略 api_portability 冒烟（同受控清单 -k 子集）：全部通过；白名单无触发
===== CONTRACT GATE : PASS =====
```

**PASS** ✅（本修复未触 wrapper 模板 → 矩阵哈希无需 reverify，`--check` 作保险亦通过）

## 九、回退条件核验

未触发：验收 ④ 逐位一致、⑤ 零新增失败、`get_positions()` 语义无声明外变化、⑥ 与正确基线同量级。
回退点 `d479d95d3bc5ad1faa7a8f49bba4ff8350f2a68e`（stash@{0}）保持有效。

## 十、Backlog（同影响面附表 §四）

1. 6 文件存量回测结论作废并重跑；2. 乙类 3 文件重验；3. `ETF平滑动量轮动.py:82` 的 `.value` 独立缺陷；
4. `check_fund_matrix.py` 覆盖范围文档缺口；5. 市值口径观察（`Account.market_value`/`total_asset`、`Portfolio.market_value` 静默回落）。
