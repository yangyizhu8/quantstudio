# 验收证据：get_fundamentals(valuation) 的 date PIT 语义修复（B2）

- 方案：`docs/valuation-date-pit-fix-design.md`（2026-09-04 审计通过 + 四项钉死）
- 实施日期：2026-09-04
- 写前快照（零副作用回退点）：`git stash create -u` -> `bd03d51773a22eb58aed9c58f62713752d53ebb9`，已 `git stash store` 持久化为 stash@{0}
- 证据脚本：`agent_workspace/dividend_defense_smallcap_5d/verify_b2_local.py`（分界 + as-of 自断言）、
  `verify_turnover_series.py`（近 20 日换手率序列可用性）、`%TEMP%/b2_verify/{b2_probe.py,conv6.py,panic_gold.py}`

## 零、数据源与运行环境（R5 级证据纪律：记录出处）

| 用途 | 数据库绝对路径 | 说明 |
|---|---|---|
| §二 分界测试 / §三 as-of 对拍 | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db` | 主库（35.31 GB），index_daily 至 2026-08-02 |
| §七.1 黄金对比 smallcap | `D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db` | 同上；窗口 2026-07-01 ~ 2026-07-31 |
| §七.2 黄金对比 恐慌抄底 | `D:\miniQMT策略实盘\QuantStudio\data\staging\prehandover_20260905-223621\quantstudio.db` | **与 R5 主跑同源的客户批准副本**（21.15 GB，index_daily 至 2026-09-04）；窗口 2026-01-01 ~ 2026-09-04；capital=1,000,000 |
| 引擎配置 | daily-bar-v1 / match_price=close / 默认成本 | 与 R5 一致 |

## 一、改动清单（精确文件清单，仅本会话改动）

| 文件 | 修复后 SHA-256(16) | 改动性质 |
|---|---|---|
| `quantstudio/backtest/ptrade_api.py` | `7DF2444ABBA1F4BC` | C1 改动点 A：date 分界路由 |
| `quantstudio/backtest/providers/duckdb_provider.py` | `7C0D8A30F6C6C640` | C2 改动点 B：`force_as_of` 开关（默认关闭） |
| `quantstudio/backtest/providers/base.py` | `D248F2C6D9AB7AA2` | C3 抽象签名 + 契约文档 |
| `tests/test_valuation_date_pit.py` | 新增 | **C4 常设回归测试（审计 A2）** |
| `README.md` | 新增章节 | D1 文档同步 |
| `docs/strategy_toolbox.md` | 新增契约段 | D2 文档同步 |
| `docs/prompt_engineering.md` | 新增契约条 | D3 文档同步 |
| `docs/strategy-compiler/ptrade-profile-contract.md` | 新增 2026-09-04 章节 | D4 文档同步 |
| `docs/valuation-date-pit-fix-design.md` | 新增 | D5 方案 |
| `docs/evidence/valuation-date-pit-acceptance.md` | 新增 | D6 本文件 |

改动前 `git status --porcelain` 确认三个代码文件均为 ` M`（无其他会话叠加改动）。

## 二、验收项 1 · 分界测试（硬门）

方法：同一工作树内先跑修复前（临时回退）再跑修复后，输入完全相同，对返回 DataFrame 取排序无关 SHA-256。

| 用例 | 输入 date | 修复前 hash | 修复后 hash | 判定 |
|---|---|---|---|---|
| A | 未传 | `4eb582511d096bd5` | `4eb582511d096bd5` | **逐位一致** |
| B | = T（2026-07-30） | `4eb582511d096bd5` | `4eb582511d096bd5` | **逐位一致** |
| C | = T-1（2026-07-29） | `4eb582511d096bd5` | `4eb582511d096bd5` | **逐位一致** |
| D | = T-7（2026-07-23） | `4eb582511d096bd5`（快照值，错误数据） | `db3b6742986347a9`（真值） | **按设计变化** |

字段集、列序、dtype 在 A/B/C 三例修复前后完全一致。

## 三、验收项 2 · as-of 正确性

用例 D（date = 2026-07-23）修复后返回值与 `stock_daily_valuation` 真值逐值对拍：

| 标的 | 修复后 turnover_ratio | DB 真值（2026-07-23） | 判定 |
|---|---|---|---|
| 600519.SS | 0.2713 | 0.2713 | 一致 |
| 000060.SZ | 2.6914 | 2.6914 | 一致 |
| 000001.SZ | 0.5647 | 0.5647 | 一致 |

**观测记录（非语义差异）**：as-of 分支的行序来自 SQL 返回序，不保证稳定；与快照分支（预加载帧序）可能不同。
取值正确，**消费方须按索引/代码对齐，不得按位置取值**。按"最小改动"原则未加行序归一化
（加它会改动既有 as-of 分支的行为，违反纯增益）。

## 四、验收项 3 · 序列原语

**N/A** —— 按审计钉死 1 范围缩减，`get_valuation_series` 已删除，**不新增任何注入 API**。
策略侧按日循环 `get_fundamentals(pool_list, 'valuation', fields, date=D_i)` 取序列，无新 API 面。

## 五、验收项 4 · 6 策略转换产物 SHA-256 逐位一致（审计加严项）

| 策略 | 转换产物 | 修复前 | 修复后 | 判定 |
|---|---|---|---|---|
| CANSLIM突破成长选股策略 | `CANSLIM突破成长选股策略_ptrade.py` | `949679f2370890e448bfe974` | `949679f2370890e448bfe974` | **SAME** |
| fall_reversal | `fall_reversal_ptrade.py` | `77aaa7a7f1ebc67feeffaaaf` | `77aaa7a7f1ebc67feeffaaaf` | **SAME** |
| tech_etf_mvo_rotation | `tech_etf_mvo_rotation_ptrade.py` | `a884eac2c6909d90aa095058` | `a884eac2c6909d90aa095058` | **SAME** |
| vol_regime_mom_rev | `vol_regime_mom_rev_ptrade.py` | `6b1516fd7ddf3ae6d3849fe7` | `6b1516fd7ddf3ae6d3849fe7` | **SAME** |
| weekly_smallcap_growth_momentum_10 | `weekly_smallcap_growth_momentum_10_ptrade.py` | `98b25cd100cc0b9345aa30de` | `98b25cd100cc0b9345aa30de` | **SAME** |
| 周频小市值成长动量（三层止损） | `周频小市值成长动量（三层止损）_ptrade.py` | `3d29403e25062c5a79e935e5` | `3d29403e25062c5a79e935e5` | **SAME** |

**对照组（非确定性排除）**：侧车 JSON（`run_card.json` / `source_import_report.json`）修复前后不同，
但**同一代码版本连跑两次亦不同**（post vs post2 = False）→ 差异源自 JSON 内时间戳字段，**非本次改动引入**。

## 六、验收项 5 · 既有功能零衰减 + 常设回归（C4）

1. **16 个涉及 fundamentals/valuation 的测试文件**：修复前后**失败集完全相同（9 项，逐项一致）**，
   零新增失败（370 passed / 9 failed / 1 skipped）。为排除跨工作树环境差异混淆，采用**同树临时回退法**
   复核：在主工作树内临时回退后跑同一子集，得到**同样的 9 项失败**。结论：既有失败，与本次修复无关。
2. **契约回归门**：`python scripts/run_contract_gate.py --strategies` → **CONTRACT GATE : PASS**。
3. **矩阵哈希（审计钉死 4 事实表述）**：本修复未触 `source_import.py` wrapper 模板，故无需 matrix reverify；
   `check_fund_matrix.py --check` 已作保险运行通过。
4. **C4 常设回归测试（审计 A2 新增）**：`tests/test_valuation_date_pit.py` —— **7 passed**，覆盖：
   - `test_boundary_unspecified_T_and_T_minus_1_are_identical`：三例逐位一致；
   - `test_future_date_falls_back_to_snapshot_path`：date > T 落快照路径（无未来函数泄漏）；
   - `test_history_date_returns_true_asof_values`：date < T-1 与库内真值对拍；
   - `test_history_date_differs_from_snapshot`：**判别式**——若 date 再被丢弃，此断言立即失败；
   - `test_invalid_date_fail_soft_unchanged`：非法 date 维持现行 fail-soft（空表不抛异常）；
   - `test_empty_string_date_treated_as_snapshot`：空串等价未传；
   - `test_query_cache_key_contains_date`：缓存键含 date（运行期断言，防串味）。

   **措辞更正**：方案 §三 改动点 A 曾写"date 解析失败维持现状**抛异常**"，实测定谳为**不准确** ——
   现行行为是吞异常 + warning 日志 + 返回空 DataFrame（fail-soft 早已存在），空串则视为未传。
   本修复**不新增也不移除** fail-soft；方案文档已同步更正。

## 七、验收项 6 · 黄金结果等价（G3.5）

方法：同一工作树内先跑修复后 → 文件级备份回退 → 跑修复前 → 文件级恢复并核对 SHA-256，逐文件比对。

### 7.1 主证据 · 唯一每日消费 `date=T-1` 的策略

`smallcap_overnight_scalp_7_quantstudio.py`（`before_trading_start` 每日调用
`get_fundamentals(stocks, 'valuation', fields=[...], date=previous_api_date)`，previous_api_date ≡ T-1）；
数据源 `data/quantstudio.db`；窗口 2026-07-01 ~ 2026-07-31；**23 个交易日、21 天有持仓、trades.csv 21 行**。

| 产物 | 修复前 SHA-256(20) | 修复后 SHA-256(20) | 判定 |
|---|---|---|---|
| config.csv | `16ABBA0FEA2477703BDE` | `16ABBA0FEA2477703BDE` | **IDENTICAL** |
| daily_stats.csv | `34B2DE24E44AB8DFBA48` | `34B2DE24E44AB8DFBA48` | **IDENTICAL** |
| **trades.csv** | `504707DA4A54F9E93623` | `504707DA4A54F9E93623` | **IDENTICAL** |
| round_trips.csv | `F01A374E9C81E3DB89B3` | `F01A374E9C81E3DB89B3` | IDENTICAL（**空文件，无检出力，见 §九**） |
| ptrade_metrics.csv | `6A6FA74CD462B9E385C4` | `6A6FA74CD462B9E385C4` | **IDENTICAL** |
| ptrade_metrics.json | `B4E526890D239AEDCD5E` | `B4E526890D239AEDCD5E` | **IDENTICAL** |
| benchmark.csv | `1DE2AF3E000717AFCB8A` | `1DE2AF3E000717AFCB8A` | **IDENTICAL** |

**有效列 6/6 逐位一致**（round_trips 空文件单列剔除）→ 信号 / 订单 / 成交 / 持仓 / 净值 / 指标等价。

### 7.2 客户指定对象 · 恐慌抄底事件驱动逆向策略（**已按审计 A1 重做**）

- **作废声明**：本节此前记录的「窗口 2025-01-01 ~ 2026-07-31，未触发选股」**措辞错误且无检出力，已作废**。
  真实原因是该次运行的数据源为**主库**，与 R5 实证所用副本库不同源，故未复现 2026-07-17 的信号触发。
- 重做配置：数据源 `data/staging/prehandover_20260905-223621/quantstudio.db`（**与 R5 主跑同源的客户批准副本**）；
  窗口 2026-01-01 ~ 2026-09-04（164 交易日）；capital=1,000,000；引擎 daily-bar-v1 / match_price=close。
- 策略源码核对：`agent_workspace/panic_bottom_fishing/strategy.py` 与发布产物
  `quantstudio/backtest/strategies/恐慌抄底事件驱动逆向策略.py` **SHA-256 逐位一致**（`6DDAE987BF8B0B6DB4D0BF14`）。

| 产物 | 修复前 SHA-256(20) | 修复后 SHA-256(20) | 行数 | 判定 |
|---|---|---|---|---|
| config.csv | `5CD893D33B9F3814394C` | `5CD893D33B9F3814394C` | 2 / 2 | **IDENTICAL** |
| daily_stats.csv | `DE503A280368DF44A321` | `DE503A280368DF44A321` | 165 / 165 | **IDENTICAL** |
| **trades.csv** | `E3736BE84543C65D5B1F` | `E3736BE84543C65D5B1F` | 83 / 83 | **IDENTICAL** |
| **round_trips.csv** | `B97BE8CEDD0DAE17BEC0` | `B97BE8CEDD0DAE17BEC0` | 42 / 42 | **IDENTICAL（非空）** |
| ptrade_metrics.csv | `0136EAF77F9E01DA45ED` | `0136EAF77F9E01DA45ED` | 2 / 2 | **IDENTICAL** |
| ptrade_metrics.json | `D2BC443471E34F322102` | `D2BC443471E34F322102` | 316 / 316 | **IDENTICAL** |
| benchmark.csv | `4E47C0C55FBB6D834B1E` | `4E47C0C55FBB6D834B1E` | 165 / 165 | **IDENTICAL** |

**7/7 逐位一致，且两侧均为真实成交（trades.csv 83 行 = 82 笔，round_trips 42 行 = 41 回合），
与 R5 证据文档记录的 82 笔 / 41 回合吻合。**

**交叉印证**：本次重跑的 `daily_stats.csv`（`de503a280368df44a3214125a276e194…`）与
`trades.csv`（`e3736be84543c65d5b1f9c01d93ffdaf…`）与 R5 证据文档 §二 记录的哈希**逐位吻合**，
独立证明本次复现的环境/数据源/参数与 R5 主跑一致（config.csv 不同源于策略源文件路径不同，属预期）。

## 八、附带观察（既有缺陷，非本次引入，未在本批修复）

`quantstudio/backtest/run_ptrade_strategy.py` 的 `_check_data_readiness` 以内含 `✓` / `❌` 的
`print` 输出诊断信息，在 GBK 控制台下触发 `UnicodeEncodeError` 并使回测入口直接崩溃。
- 复现：不设 `PYTHONIOENCODING` 时运行 `python -m quantstudio.backtest.run_ptrade_strategy ...`
- 规避：`PYTHONIOENCODING=utf-8`
- 定性：与 B2 无关的既有缺陷；按"修复须拆分、不得捆绑"纪律**未并入本批**，另行立案处置。

## 九、小核对 · round_trips.csv 同哈希成因（审计第四节）

§7.1 与 §7.2 的 round_trips.csv 哈希分别为 `F01A374E…` 与 `B97BE8CE…`，**并非同哈希**；
但 §7.1 的 smallcap 两侧与旧版 §7.2 的恐慌两侧**四处** round_trips.csv 曾同为 `F01A374E…`。

实测：该文件在这四处**均为 5 字节、内容仅一个换行（空文件）** —— 即输出为空 DataFrame 且无列。
**结论：该列在 §7.1 属空文件，其 IDENTICAL 判定无检出力，已从有效列中剔除（有效列 6/6）。**
§7.2 重做后 round_trips.csv 为 42 行实体数据，该列恢复有效（7/7）。
小注：smallcap 有 21 行成交却输出空 round_trips（隔夜日内回合未配对的既有输出行为），
属引擎指标侧独立现象，**不在本批范围**，另行登记待查。

## 十、流程告诫（审计第三节 · 写入教训）

**黄金对比不得在共享工作树内对他人也在改的核心文件做批量 `git checkout HEAD --`。**

本次黄金对比的第一次尝试采用了「在共享工作树内 `git checkout HEAD -- <3 个共享核心文件>` 临时回退」，
与 2026-08-17 事故为同型操作形态。**本次因先建有文件级备份、且事后逐文件 SHA-256 核对而零损失**
（事故经过与本文件同步登记）。今后一律改用：
1. **文件级 `cp` 备份 + 恢复**（本次第二次起已采用，并在脚本内做恢复后哈希核对）；或
2. **`git worktree` 独立检出**；
**禁止**在共享树内对他人可能同时在改的核心文件执行批量 checkout / reset / revert。
批量破坏性 git 操作前必须 `git stash create -u` + `git stash store` 建持久化回退点。

## 十一、实施过程事故登记（自愈完成，无残留）

第一次黄金对比的 PowerShell 脚本内的中文绝对路径因脚本无 BOM 被 Windows PowerShell 5.1 按 ANSI 读取而乱码，
导致 `git checkout HEAD --` 执行后**恢复步骤失败**，三个目标文件一度停留在 HEAD 版本。
处置：立即从会话内自建备份恢复并核对 SHA-256 与改动前完全一致
（`7DF2444ABBA1F4BC` / `7C0D8A30F6C6C640` / `D248F2C6D9AB7AA2`）。
后续脚本改为**参数传入路径**（ASCII 脚本体 + 运行期参数）后重跑成功。无改动丢失、无残留。

## 十二、回退条件核验

未触发任何回退条件：分界测试三例逐位一致、as-of 对拍一致、6 策略产物逐位一致、
回归零新增失败、两个黄金对比各 7/7（smallcap 有效列 6/6）逐位一致。
回退点 `bd03d51773a22eb58aed9c58f62713752d53ebb9`（stash@{0}）保持有效。

## 十三、剩余待办（本批范围外）

1. 推送 + 双远程 HEAD 核对（待用户确认）；
2. 推送后**同工作周期执行 QuantStudio-trading 同步门**（`git fetch origin && git merge origin/main` →
   check-drift 九项 → ci-smoke 共享层回归）并登记台账 —— B2 触及 `quantstudio/backtest/*` 共享层，不可豁免；
3. `run_ptrade_strategy.py` GBK 崩溃（§八）与 smallcap round_trips 空输出（§九小注）另行立案。
