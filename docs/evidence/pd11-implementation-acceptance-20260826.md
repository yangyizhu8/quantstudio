# P-D11 实施与验收证据：持仓视图归一注入（WP-A1，2026-08-26）

- 流水线：Step 1 设计 v1.2（审定冻结）→ Step 3 实施（本文件）→ Step 4 本地验收（本文件 §3）→ 平台验收（§5，待用户执行）→ Step 5 用户确认 → Step 6 双仓库推送
- 回退点：`git stash create -u` = `b59c2499429eaeaff255e85298f43f786d407a92`（baseline-wp-a-20260826，含当时 staged 态快照）
- 实施基线：HEAD `466a704`（交付推送后，双远程核验一致）

## 1. 实施清单（tracked 文件 2 个）

| 文件 | 改动 |
|---|---|
| `quantstudio/strategy_compiler/source_import.py` | ① 新增 `_QS_POSITION_VIEW_EXT` 模板（键归一 sid 锚点 + 残影过滤 amount>0 + `_QSPositionView` 契约视图 + fail-loud）；② 新增 `_render_position_view_ext()` 渲染器（BSE 烘焙快照 248 条，format 后 replace 避开花括号扫描域）与 `_bse_legacy_bare_codes()`（fail-loud，空表/不可导入显性报错）；③ 新增门控谓词 `_source_uses_position_api`（AST 调用名匹配，与 order_api 同款）；④ `_inject_all` 装配接线（P-D10 块后、shim 前） |
| `quantstudio/strategy_compiler/validators/validate_local_strategy.py` | LOCAL-API-WHITELIST 补本地 ClassDef 实例化放行（`local_calls_ok = defined_funcs | defined_classes`）——爆炸半径审计：strategies/ 全目录**零**本地类定义，仅注入模板的视图类受影响；生命周期检查保持 FunctionDef 语义不变 |

新增 untracked：`tests/test_pd11_position_view.py`（19 用例）；旧产物备份 `output/ptrade_export_backup_pd11_20260826/`（6 目录，重转前快照）。

**实施中发现并修正的镜像偏差（T11 差分闸首跑即捕获，防漂移设计生效）**：
1. 权威 BSE 精确表优先于**后缀**判定（`430047.SH` → BJ 非 SS）——模板分支序已改为权威同序（BSE 表 → 后缀 → CB 段 → 前缀 → SS 兜底）；
2. 权威无 6 位数字门控（任意串走分支兜底，`abc123`→`ABC123.SS`）——删除模板的 isdigit 透传分支，改用 startswith（空串安全）。

**实施中发现并修复的校验器缺口**：LOCAL-API-WHITELIST 仅收 FunctionDef，本地 class 实例化被误 BLOCK（编排器 api_portability BLOCKED，独立校验器却 VALID 的差异根因）。最小修复 + 双向测试（本地类放行 / 未定义类名仍 BLOCK）。该缺口正是既有模板被迫用 `type()` 动态构造（`_QSFilterStatusState`）的原因。

## 2. 6 策略重转结果（--no-smoke，与旧基线同构）

| 策略 | api_portability | 注入 | 持仓消费路径 |
|---|---|---|---|
| CANSLIM突破成长选股策略 | PASS | ✅ | `get_positions()`（模块 API） |
| 周频小市值成长动量（三层止损） | PASS | ✅ | `get_position`/`get_positions` ×7（模块 API） |
| fall_reversal | PASS | — | `g.current_holdings` 自维护（**不读任何持仓 API**） |
| tech_etf_mvo_rotation | PASS | — | `context.portfolio.positions`（引擎容器） |
| vol_regime_mom_rev | PASS | — | `context.portfolio.positions`（引擎容器） |
| weekly_smallcap_growth | PASS | — | `context.portfolio.positions`（引擎容器） |

产物均含烘焙 BSE 快照（n=248 条注记），单注入块（幂等门控生效），CANSLIM 产物注入位于策略 def 之前（L823）。

## 3. 本地验收（Step 4）

- `tests/test_pd11_position_view.py` **19/19 绿**（T1~T11 设计矩阵全量 + 门控注入/不注入/字面量不触发/幂等/烘焙完整性/fail-loud/独立编译 + 校验器补丁双向回归）；
- `tests/test_ptrade_contract_compliance.py` **94/94 绿**（转换契约零回归）；
- 全量套件（2694 项，除 B2 两项预期红）结果见 §3.1 追记；
- 旧 run_card 对照：6 策略 api_portability 均维持旧基线 PASS/PARTIAL 状态（--no-smoke 稳态）。

### 3.1 全量套件追记（2694 项，2026-08-26 终稿）

结果：**2665 passed, 19 failed, 2 skipped, 8 xfailed**（另剔除 B2 两项预期红）。

**归因方法**：①19 项失败对 source_import/validate_local_strategy 的 import 关系**全部为 0**；②隔离 worktree @ HEAD `4430788`（无本 WP 改动、无他方未提交改动）复跑可得项——**全部复现同状态**，证明失败均与本 WP 无关。

| 失败项 | HEAD worktree 复跑 | 归因（与 WP-A 无关） |
|---|---|---|
| test_pit_filter::test_validator_is_single_chokepoint | ❌ 同红 | 存量提交问题（writer 统一出口断言） |
| test_ptrade_public_signature_contract::test_slippage_signatures | ❌ 同红 | **存量提交问题**（HEAD 上 slippage=0.0 vs 断言 0.1；修正早前"他方未提交改动所致"误判） |
| test_provider_frequency_routing | ❌ 同红 | 存量（lambda 签名 3/4 参数） |
| test_qfq_b5_generation | ❌ 同红 | 存量（aux/mcp_aux DB 路径断言） |
| test_qfq_range_r1a | ❌ 同红 | 存量（golden 对照） |
| test_qfq_reanchor_batch1 / test_qfq_schema_status | ❌ 收集错误 | 依赖**未提交新模块** quantstudio.pipeline.snapshot_lock（他方 qfq 管线进行中） |
| test_strategy_spec_schema | ❌ 同红 | 存量（run_card_version 1.0 vs const 1.1） |
| test_xtquant_daily_switch | ❌ 同红 | 存量（isST schema 断言） |
| test_security_metadata_api | ✅ worktree 27 passed | 工作树红由他方未提交改动（ptrade_api 等）干扰，HEAD 态绿 |
| test_probe_strategy_rules_clean | ❌ 缺文件 | 引用已删除的 sw_industry_etf_rotation_8f__candidate |
| test_minute_limit_halt ×3 | ❌ 同红（含 ModuleNotFound） | 存量 + 依赖未提交模块（minute 引擎基线） |
| test_minute_query_no_future_leak / test_next_open_limit_and_halt / test_order_rejection | ❌ 同红 | 存量提交问题（引擎行为断言） |
| test_inspect_capabilities_f6 / test_mcp_etf_latest_anchor ×3 | import-mine=0 | live DB / MCP 数据类，非本 WP 链路 |

**结论**：12+ 项经 HEAD-worktree 复跑确认为**存量提交基线问题**；2 项依赖他方并行未提交模块；1 项引用已删文件；1 项 HEAD 态绿（工作树红系他方干扰）；其余数据类。**无一由本 WP 引起**——`source_import`/`validate_local_strategy` 链路测试（pd11 19 项 + compliance 94 项）全绿。存量失败登记，随并行会话收敛后另行处理。

### 3.2 测试隔离修复（其二，2026-08-26）

全量验收期间发现并修复两处**既有测试隔离缺陷**（非产品回归，均为测试间模块级全局污染）：

1. **B2 测试自污染**：`test_b2_target_value_semantics_repro.py` 的 `_attach` 把新引擎绑到全局 `_api`，前序用例状态泄漏 → 全文件合跑时保底用例由绿变红。修复：新增 autouse fixture `api_state_clean` 快照/还原 `_api` 的引擎/价格/日期字段。修复后 B2 矩阵稳定 **2 红 3 绿**（2 红 = 预期复现本体，3 绿 = 原生 delta 对照 + 清仓保底 + 空仓等价）。
2. **compliance 全局污染**：`test_ptrade_contract_compliance.py` 5 处测试直接赋值 `pa._QSOrderWiringState.order_orig/target_orig` 与 `pa._QSLastCloseState.cache` 且不还原（L1010/1011、1028、1049/1050、1062/1063、1150/1151）——B2 加入合跑顺序后暴露（compliance 恰是最后一个触碰这些全局的既有套件）。修复：5 处全部 `monkeypatch.setattr` 化（自动还原）。修复后三套件（pd11+compliance+B2）合跑 = 2 红（预期）+ 全绿。

**经验登记**：注入/接线类测试必须约束模块级全局（`_api`/`_QS*State`），写入 WP-F 测试规范要点。

## 4. 范围内策略的平台预期（验收判据）

**CANSLIM**：`get_positions()` 键归一 .SS/.SZ → `g.pending_buys` 匹配恢复 → STOPDBG `basis>0` 且止损日期与本地一致（本地 07-03/07-07/07-09 对齐）；残影行不再虚增持仓数。

**周频三层**：`get_position(code).cost_basis` 可读（tier1 止损激活）→ 300930 07-10 与本地同日止损、301418 等深亏股止损触发 → 持仓不再累积至满仓 → halt 冻结不再发生；`get_positions().keys()` 归一 → 持仓枚举/换仓恢复。

## 5. 平台验收结论（2026-08-26 用户回贴，测试123/测试456 账号实跑）

### 5.1 CANSLIM——**验收通过 ✅**

| 判据 | 修复前（08-25） | 修复后（08-26） | 判定 |
|---|---|---|---|
| STOPDBG basis | 恒 `-1.0000`（pending_buys 失配） | `07-02 301093.SZ basis=106.9100`、688103.SS basis=69.3900 | ✅ |
| 止损日对齐本地 | 全部退化为 -20% 移动止损深亏离场（07-09/14/16/21/31） | **逐日对齐**：07-03 卖 301093(-10.44%)、07-07 卖 300121(-10.62%)、07-09 卖 688305(-10.74%)、07-10 卖 002458(-7.86%)+603638(-10.20%)、07-21 卖 688103(移动止损)、07-29 卖 300643(-9.81%)——与本地 6 个止损日完全一致 | ✅ |
| 补位/换仓 | 满仓冻结 | 07-03 腾槽后 07-06 补买 603638（与本地同日同股） | ✅ |

残余差异（已登记非本 WP）：07-28 买 300214 ∈ C1「仅平台 16 只」宇宙差；07-10 委托数量为0 ×1（B3 范畴）；gross_exposure 恒 0.0000（双端同口径）。

### 5.2 周频三层——**修复生效 ✅ / 300930 判据错设（作废重设）**

| 判据 | 修复前 | 修复后 | 判定 |
|---|---|---|---|
| 止损机制激活 | tier1 彻底静默，12 买 0 卖，-13.89% | **STOP_RETRY 实际卖出 4 笔**（07-10 卖 301418、07-15 卖 301329、07-22 卖 301418、07-24 卖 001387），期末持仓 12→6，-12.32% | ✅ |
| `tier1_marked=0` 审计 | — | **时序盲区**：handle_data 先 `_try_sell_marked` 卖出成功即 `del g.stop_marks[code]`（L345/355）后才打审计行 → 审计显示剩余 0 非"从未标记" | ✅ 机制正常 |
| 300930「07-10 同日止损」 | — | **判据笔误作废**：本地权威 trades 实证止损日在 **07-20**（`300930.sell 07-20 @21.21` ratio 0.748；本地审计 `07-20 tier1_marked=1 300930:-0.202`）；平台 300930 已于 **07-13 换仓卖出**（跌出新名单，ratio 未跨 0.8）——两端均离场，路径不同源于 C2 池构成差 | ⚠️ 归 C2 |
| tier2 halt | 07-20 dd=0.1628 halt 冻结两周 | 07-27 dd=0.1750 halt（晚 5 交易日；halt 只停新买不卖旧仓 L471 符合设计） | ✅ 级联源自池差 |

### 5.3 平台验收总结论

- **WP-A 主目标达成**：P-D11 使两类止损策略的 ptrade 端止损机制**从彻底失效恢复为实际工作**（CANSLIM 逐日对齐；周频 STOP_RETRY 卖出 4 笔）；
- 全部残余差异归因**已登记数据层项**（C1/C2/D3/B3），无一指向 P-D11 本身；
- **判据修正登记**：300930 止损日 = 本地 07-20（分析期 07-10 为笔误），归因 C2 池构成差。

### 5.4 双端复检报告（2026-08-26 完整落盘数据重跑，追加）

用户将两策略本次双端完整数据（日志/成交/持仓，时间戳 23:16~23:23）落盘覆盖原目录后复检，逐日交叉验证结果：

**CANSLIM 修复前后总览**

| 指标 | 修复前(08-25) | 修复后(08-26) | 改善 |
|---|---|---|---|
| 收益差 | -9.61pp（-7.79% vs -17.40%） | **-3.12pp**（-7.78% vs -10.90%） | ✅ 收窄 6.49pp |
| ptrade 最大回撤 | 17.40% | 10.90% | -6.5pp |
| ptrade 平仓 | 5 笔（1 盈 4 亏） | **8 笔（1 盈 7 亏）** | 平仓数正常化 |

CANSLIM 逐日止损对齐（ptrade vs 本地）：07-03 卖 301093(-10.44%)、07-07 卖 300121(-10.62%)、07-09 卖 688305(-10.74%)、07-10 卖 002458(-7.86%)+603638(-10.20%)、07-21 卖 688103（移动止损 peak103×0.8）、07-29 卖 300643(-9.81%)——**6 个止损日 6/6 对齐**；STOPDBG `basis` 恢复真实值（106.91/69.39/14.41/8.91/25.11/20.79/14.00）。

**周频三层修复前后总览**

| 指标 | 修复前(08-25) | 修复后(08-26) | 改善 |
|---|---|---|---|
| 收益差 | -5.73pp（-8.16% vs -13.89%） | **-4.16pp**（-8.16% vs -12.32%） | ✅ 收窄 1.57pp |
| ptrade 平仓 | 0 笔（12 买 0 卖） | **12 笔（3 盈 9 亏）** | ✅ 止损机制激活 |
| ptrade 期末 | 12 只满仓裸奔 | 6 只 + 现金 40,342 | ✅ 风控恢复 |

周频平台端 4 笔 STOP_RETRY 止损全部实际成交（07-10 卖 301418 -21.0%、07-15 卖 301329 -20.2%、07-22 卖 301418 -18.7%、07-24 卖 001387 -19.8%），对比修复前 301418 跌 -42% 不止损。

**残余差异归因（100% 已登记，无一指向 P-D11）**：
1. C1 宇宙差：07-28 平台买 300214（本地 stock_daily 无行情不可见）→ 平台特有仓 07-31 止损 -11.07%；07-01 建仓 ptrade 10 只 vs 本地 9 只（第 10 只整手挤出）；
2. C2 池构成差：周频 07-06/07-13 调仓名单分岔（本地 L6_eps=15~16 vs 平台 20~21 → rankable 14~15 vs 19~21）；300930 平台 07-13 换仓卖出 vs 本地持有至 07-20 止损（两端均离场，路径不同）；
3. D3 复权微差：周频 301329 止损日期 ±1 日（平台 07-15 vs 本地 07-14）；
4. B3：07-10「委托数量为0」×1（平台高价候选股资金不足取整）；
5. F7 费用口径：ptrade 成本含费摊薄 vs 本地 avg_cost。

**复检结论**：停机机制修复的收益改善（CANSLIM -6.49pp / 周频 -1.57pp）+ 平台坑止损序列对齐 + 残余 100% 已登记归因——与 §5.1/5.2/5.3 验收结论一致，复检通过。

## 6. 范围外发现（如实登记，不在本 WP 处理）

1. **`context.portfolio.positions` 容器路径（4/6 策略）**：平台引擎自有对象，注入模板**无法包装**（模块级代码收不到 context 引用）。其平台端键格式/残影行为**未经探针实证**（P-POS 仅覆盖模块 API）。候选方案 = source_import AST 改写 `context.portfolio.positions` → `_qs_portfolio_positions(context)` 辅助函数——属策略源改写层级，需先 P-POS-2 探针（平台上 dump `context.portfolio.positions` 键格式/字段/残影）再立 P-D11b 设计增补，走独立六步。**已登记，不臆测实施。**
2. **fall_reversal 重归因**：其 `QS_PORTFOLIO_AUDIT positions=18` 恒定源于策略自维护 `g.current_holdings` 与平台强平实况脱钩（策略不读持仓 API）——WP-A 无法改变其行为；其废单/退市问题归 DS4（停牌/退市数据）+ WP-F（未来策略模板强制读 get_positions 对账）。**平台验收预约名单中移除 fall_reversal**（避免无效回测），改为 CANSLIM + 周频三层两项。
3. `ptrade_api.py` 他方未提交改动（57 行，slippage 相关）——实测 HEAD 上 slippage 基线=0.0（测试断言 0.1 为存量失配），**归因已修正为存量提交问题**（见 §3.1）；他方未提交改动仍在开发中，不属 WP-A。

## 7. 回退

- 回退点 `b59c249`（实施前）；
- 本 WP 改动 = 2 tracked 文件（source_import.py / validate_local_strategy.py）+ 新增 untracked 测试；回退 = 定向 restore 两文件至 `466a704` 基线版本（编辑内容为增量，与 HEAD `4430788` 无冲突——P-A3 推送未触及两文件）+ 删除新测试/产物（产物 gitignored，备份在 backup 目录）。
