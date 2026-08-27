# 双端回测对齐修复计划 v2（定稿 · 框架层 · 纯增益 · 六步流水线）

- **状态**：Step 1 方案审计通过 + v2 复审通过（三条硬约束逐条核对确认）；**Step 2 = 本文件落盘即生效**（2026-08-26，门禁解除后落盘）。
- **门禁核验**：2026-08-26 01:5x —— quantstudio-plus main == quantstudio main == HEAD == `a5399c6`，交付推送完成、双仓库一致，Step 0 门禁解除。
- **会话基线**：`docs/handoff/baseline-20260826-dual-end-phase0.md`（本轮仅新建文件，不触碰工作树既有 M/D 改动）。
- **证据基线**：6 策略双端对齐分析（2026-08-25/26，只读）+ 三项补充核查（E-1/E-2/E-3）。
- **关联登记**：P-D11（持仓视图归一 / WP-A）、P-D12（target 语义双端失效 / WP-B）待建；P-A3 二期为既有已批工作（EPS 回填单一事实源，本计划引用不重做）。

---

## 0. Step 0：交付日门禁（已完成解除）

- 交付四件套（基线终态核对 → 交付推送 → P-A3 推送 → WP8 判据）完成 + 两远程核对一致 → 门禁解除（已核验，见文件头）。
- 本计划与交付链完全隔离：门禁期内零落盘，解除后首轮写入全部为新建文件。

## 1. 背景与证据基线

6 策略双端对齐分析完成（只读），三项补充核查升级归因：

| 证据 | 内容 | 影响 |
|---|---|---|
| E-1 | 转换管线 `order_target_value` 包装 `_qs_split_order` 直接 `n=value/px` 不扣现持（转换产物 L364-372） | ptrade 端金字塔根因，转换管线 bug |
| E-2 | 本地引擎 `_immediate_execute` 有 delta 逻辑（backtest_engine.py L695-711），但 tech_etf 7-27 已持 40,600 股（现值 42,955）时对目标 43,562 全额买入 42,400 股（trades.csv 实证，20260825_220017） | 本地引擎存量调仓路径缺陷，机理待最小复现（B2） |
| E-3 | 策略源码正确调用 `order_target_value`（tech_etf L399/L408） | 金字塔=双端框架层 target 语义失效，非策略缺陷（原 SK2 上收为 FE8/CP4） |

保真基建：`PTradeFidelityConfig` 三开关（P-A0 宇宙快照 / P-A1 ST 过滤 / P-A2 eps 口径）默认全关，本地语义权威；转换管线已有 get_history / filter_stock_by_status / get_fundamentals 包装，**get_positions/get_position 零包装**。

### 双端差异总览（6 策略，2026-07-01~07-31，10 万本金）

| 策略 | 本地 | ptrade | 差值 | 主导根因 |
|---|---|---|---|---|
| CANSLIM突破成长 | -7.79% | -17.40% | -9.61pp | ptrade basis=-1 → 7.5% 初始止损失效，退化为 -20% 移动止损深亏（持仓键双体系） |
| fall_reversal | +2.66% | +2.60% | -0.06pp | 三误差抵消巧合（撤单/退市强平/换仓分叉） |
| tech_etf_mvo_rotation | -19.74% | -26.63% | -6.89pp | 首日 no_price 拒单 + 双端 target 语义失效金字塔 + 评分 ±1/7 微差反向操作 |
| vol_regime_mom_rev | -23.78% | -14.73% | +9.05pp | q 分位窗口语义不等价（0.9333 vs 0.9000）→ 第 5 槽选股分岔（其余 4 只逐位一致） |
| weekly_smallcap_growth | -9.18% | -10.84% | -1.66pp | 宇宙差 ~310 只 + EPS 覆盖差 4~6 只 → top-10 分岔、换手 20 vs 28 笔 |
| 周频小市值成长动量（三层止损） | -8.16% | -13.89% | -5.73pp | 持仓枚举/成本价不可用 → 三层止损全程静默 → 满仓 → halt 冻结两周 |

## 2. 目标与非目标

**目标**：6 策略对齐问题在框架层（引擎/注入 API/数据源/skill/转换管线）消解；存量 6 策略文件零改动（重转+复跑生效）；通用、纯增益。
**非目标**：不修 ptrade 平台显示 bug（对账工具口径修正替代）；不改策略代码；不默认改变本地引擎语义（撮合/费用/生命周期类一律保真开关 opt-in）。

## 3. 修复项总表（6 工作包，21 项）

### WP-A 平台持仓视图归一（转换管线，P0，零本地影响）
- **A1**：注入 `get_positions`/`get_position` 包装——键后缀 XSHE/XSHG→.SS/.SZ（bare_code 兜底）、volume=0 残影行过滤、Position 属性对齐本地契约（amount/volume/cost_basis）。
- 消解：CANSLIM `basis=-1.0000` 止损失效、周频三层止损静默、fall_reversal positions 虚报 + 23 条 0 股废单（同根）。
- 前置探针 P-POS（`ptrade/probe_positions_ptrade.py`，平台 10 分钟）：打印原样键/残影/字段集，实证后才写映射（禁臆测）。
- 验收：CANSLIM `basis>0` 且止损日与本地一致；周频 tier1 触发非零；fall_reversal audit=实况、废单归零。

### WP-B 目标市值订单语义修复（双端框架 bug，P0，最高风险）
- **B1（CP4）**：转换 wrapper delta 修复：`delta = value − get_position(code).value`（依赖 A1），delta 路由 `order()`；`value=0` 保持原生清仓路径不动（保底）；`order_target_percent` 同步复核。
- **B2（FE8）**：本地引擎存量调仓 delta 缺陷——**先最小复现单测定位根因**（疑点：positions 键未命中 / 路径绕过 / min_rebalance_pct 交互），根因报告先行后改码；单测转永久回归。
- **B3（CP3①）**：0 股委托前置校验 + 显式告警（代码/金额/参考价/最小 1 手价值），只告警不吞单。
- **B4（SK5）**：审计计数改订单回执后统计；注入 `qs_order_stats()` 辅助计数。
- 验收：tech_etf 本地 7-27 不再金字塔、ptrade 加仓≈delta；511260 告警带上下文。

### WP-C 数据层对齐（P1）
- **C1（DS1）**：宇宙差 diff（5511 vs 5205，疑北交所构成）→ P-A0 快照口径收尾或本地范围开关；FUNNEL 加板块构成统计。
- **C2（DS2，硬约束 2 修订）**：~~本地 EPS 缺口回填~~ **已删除——该内容即已批的 P-A3 二期（方案/审计/一期/推送授权完成），本计划仅引用，禁止双轨**。C2 仅保留：①`get_fundamentals` wrapper 映射表新增 `eps→basic_eps`（探针三实证 Δ=0.0000）；②PIT 截面一致性巡检报告。**验收依赖 P-A3 二期完成后的合并基线重验（§5）**。
- **C3（DS3）**：①分位窗口审计行（两端 get_history 打印实际起止日+样本数——vol_regime q 0.9333/0.9000 单点故障定位器）；②复权因子快照日巡检（tech_etf ±1/7、CANSLIM 0.769 比例差归入报告）。
- **C4（DS5/FE7）**：停牌 volume=0 与退市强平 → 本地保真开关（编号与今日 P-A3 推送区分，**落地时以登记表最终编号为准**）；退市日可买入矛盾 → wrapper 下单前状态校验（只告警）+ 报平台方。

### WP-D 引擎撮合/费用保真（opt-in，P1）
- **D1（FE2）**：撮合实证方案（`ptrade/撮合机制实证与修复方案_v2.md`）P1-1/1-2/1-3/1-5 落地为保真开关（raw close 撮合/估值、preClose 原始化、pctChg 涨跌停联动）。
- **D2（FE4）**：费用 ptrade 档参数化（万8/最低5元/印花税0.1%/规费——weekly CSV 逐笔实证），保真开关挂载。
- **D3（FE3）**：首日 no_price 修复（**默认修复**，正确性问题）；golden 举证受影响差异全部来自 day1 成交。

### WP-E 审计与对账基建（纯增益，P1）
- **E1（FE5）**：本地代码级订单/成交日志（等价平台「生成订单」行：时间/代码/方向/数量/参考价/成交价/费用/拒单原因）。
- **E2（FE6）**：`scripts/dual_end_reconcile.py` 统一公式重算指标（胜率=平仓口径、盈亏比、索提诺/年化），平台 UI 指标降级参考；差异归因分解表。
- **E3**：对账材料归档规范（trades.csv/round_trips/持仓明细强制随附）。

### WP-F skill 模板升级（P2，只影响新生成策略）
- **F1**：模板修订——①止损基准优先 `get_position().cost_basis`（双端可移植）；②halt 冻结保留防御性卖出通道；③审计三件套（REBALANCE/PORTFOLIO/FILL）强制埋点；④资金常量从 context 派生；⑤换仓缓冲带；⑥默认 `order_target_value` 补差语义（B1/B2 修复后可依赖）。
- 同步义务：README + `docs/strategy_toolbox.md` + `docs/prompt_engineering.md`。

## 4. 实施顺序

```
Step 0 门禁：已解除（2026-08-26 交付推送完成 + 双仓库核对一致）
Phase 0（探针/复现，零框架代码改动）：P-POS 平台探针 → B2 最小复现单测 → C1 宇宙 diff → C2 PIT 巡检
Phase 1：WP-A（持仓归一）+ WP-E（审计/对账基建）——观测手段先行
Phase 2：WP-B（依赖 A1）+ WP-C（C2② 引用 P-A3 二期进度）
Phase 3：WP-D + WP-F
合并基线重验（§5）→ 各 WP 收尾验收
```

每 WP 独立 `docs/*-design.md` 走 ZCode 审计；B2/D3 引擎项先根因报告后改码。

## 5. 基线重验合并原则（硬约束 3）

**P-A3 二期（EPS 回填）+ B2（target 语义）+ D3（day1 成交）三者均改变回测结果——基线重验合并为一次**：

1. **合并时点**：三者全部落地后统一双跑（修复前 golden vs 修复后）+ 逐项归因（EPS 回填 / delta 修正 / day1 成交三类分解）。
2. **过渡判据**（合并重验前各 WP 验收用）：「保真开关关 = 与修复前一致」等价判据——B2 用最小复现单测逐笔举证（变化全部来自 delta 修正）；D3 举证差异全部来自 day1 成交；P-A3 沿自身已批验收路径。
3. **合并重验报告**落盘 `docs/evidence/`，为三者共同 golden 终态凭证；此后基线以合并重验结果为准。
4. 过渡期内任何不可解释差异 = 验收失败，立即停止回退。

## 6. 验收标准（总）

**A 级**（6 策略双端复跑 + E2 对账）：CANSLIM/周频 basis>0 止损日一致；fall_reversal audit=实况废单归零；tech_etf day1 建仓 + 双端 delta 语义 + 511260 告警；vol_regime q 分位一致；weekly/周频 L6 EPS 差≤1（依赖 P-A3 二期 + 合并重验）。
**B 级**：剩余净值差由 E2 逐项归因到已登记微差清单，无未解释残差。
**回归安全**：golden 双跑（保真开关 on/off 各一次）入证据；例外仅 B2/D3/P-A3 二期三类，合并重验统一举证；任何不可解释差异=验收失败停止回退。
**单元测试**：B2 复现转永久回归；A1 键归一/残影过滤；B3 零股校验；A1/B1 wrapper 同构测试矩阵（P-D10 三道防线模式）。

## 7. 风险防护

| 风险 | 防护 |
|---|---|
| 交付日混入 | Step 0 零落盘门禁（已履行） |
| EPS 回填双轨 | C2② 已删除，单一事实源 = P-A3 二期 |
| 平台 API 臆测 | P-POS 探针先行，映射按实证写 |
| B1 依赖平台持仓准确性 | value=0 清仓不经 delta 路径保底；A1 验收先行 |
| D 系污染黄金基线 | 默认 off；golden 双跑入证据 |
| EPS 映射代理语义 | 只译列名不代理本地契约；basic_eps==本地 eps 已实证 |
| 0 股校验吞单 | 只告警不吞单，不吞平台自动降量 |
| skill 波及存量 | 模板版本化，存量零改动 |
| 数据回填不可逆 | update_flag 标记可重放排除 |
| C4 退市语义误杀 | 保真开关 opt-in + 只告警不拦截 |
| 多会话共享工作树 | 每 WP 实施前 stash 回退点 + 只改本 WP 范围文件；backtest_engine/ptrade_api/source_import 现有他人未提交改动，触碰前先协调 |

## 8. 回退条件

每 WP 实施前 `git stash create -u` 回退点（hash 入 design doc，写前快照纪律）；保真开关类回退=config 回退；B2 引发不可归因 golden 漂移→回退重审；数据回填标记排除后重跑验证无残留。

## 9. 交付物清单

1. 本文件（master-plan v2）+ 各 WP `docs/*-design.md` 与 `docs/evidence/*.md`；
2. 问题登记 P-D11（持仓视图归一）、P-D12（target 语义双端失效）等，延续 P-Dxx 证据文档模式；
3. 探针脚本（`ptrade/probe_positions_ptrade.py` 等）+ `scripts/dual_end_reconcile.py`；
4. 6 策略重转产物 + 双端复跑对账报告；
5. 合并基线重验报告（P-A3 二期 + B2 + D3）；
6. README + strategy_toolbox + prompt_engineering 同步（WP-F 触发）。

## 10. 明确不做

不修 ptrade 平台显示 bug；不改 6 策略存量代码；不默认改变本地撮合/费用/退市语义（保真开关 opt-in）。

---

**流水线状态**：Step 1 ✅（v2 审计+复审通过）→ Step 2 ✅（本文件落盘即生效，2026-08-26）→ Phase 0 启动（P-POS 探针 / B2 复现 / C1 diff / C2 巡检）。每 WP 进入实施前独立走自身六步。

---

## Phase 0 进度（2026-08-26 更新）

| 项 | 状态 | 产出 |
|---|---|---|
| P-POS 探针 | ✅ 平台回贴 | `ptrade/probe_positions_ptrade.py` + `docs/evidence/pd11-pos-probe-20260826.md`（F1~F7 契约事实；键双体系/残影/`.SH` 崩溃/sid 锚点实证） |
| B2 最小复现 | ✅ 2 红 3 绿 | `tests/test_b2_target_value_semantics_repro.py` + `docs/evidence/b2-target-value-semantics-20260826.md`（根因=接线层 L2310-2318，引擎 delta 本身正确；减仓变加仓亦复现） |
| C1 宇宙 diff | ✅ | `scripts/c1_universe_diff.py` + `docs/evidence/c1-universe-diff-20260826.json`（仅本地 322 = 100% 北交所 920xxx；仅平台 16 只本地缺行情，含 605081/688121） |
| C2 PIT 巡检 | ✅ | `scripts/c2_pit_inspect.py` + `docs/evidence/c2-pit-inspect-20260826.md`（L6 多剔主因=Q1 缺口+负年报回退，7/16 缺 Q1、3/16 负 eps；eps↔basic_eps 三方一致；仅平台 16 只=行情侧缺口） |
| WP-A 设计 | ✅ v1.2（v1.1 实质审核+复核通过；v1.2=复核两必改并入+自查 CB 段，增量待审计确认） | `docs/pd11-position-view-normalization-design.md`（六步 Step 1；两条件维持；v1.2：SZ 补 "2"、BJ 精确表优先（920 前缀收紧）、CB 段 110/111/113/118↔123/127/128、后缀优先序、T11 差分防漂移闸） |
| WP-A 实施 | ✅ Step 3-4 完成（2026-08-26） | `docs/evidence/pd11-implementation-acceptance-20260826.md`：2 tracked（source_import.py 注入模板+门控 BSE 烘焙；validate_local_strategy.py 本地 ClassDef 放行）；6 策略重转 api_portability 全 PASS；pd11 19 项 + compliance 94 项全绿；全量 2665/19 经 HEAD-worktree 归因无一源于本 WP |
| WP-A 平台验收 | ✅ 审核通过（2026-08-26） | 证据落盘 §5.1~5.4：CANSLIM 止损 6/6 逐日对齐（basis 恢复）+ 周频止损机制恢复（0→12 笔平仓）+ 判据修正链完整（300930 本地 07-20）；复检落盘 §5.4：收益差 CANSLIM -9.61→-3.12pp、周频 -5.73→-4.16pp，残余 100% 已登记归因（C1/C2/D3/B3/F7）。Step 5 ✅ 用户确认（授权推送） |
| WP-A 推送 | ✅ Step 6 完成（f0c0bd7，2026-08-26） | 独立 commit `f0c0bd7`（13 文件：3 tracked + 10 untracked 精确 add，零他线混入）；双远程核对一致（plus main == qs main == f0c0bd7）；回退点 `a0fb83f`。**WP-A 正式关闭** |
| WP-B 设计 | ✅ Step 2 审计通过（2026-08-26，两处细化并入） | `docs/pd12-target-value-semantics-design.md`（B1+B2+B3：双端接线层 delta 修复同构方案；D1~D8 + T13 三面等价 + D5 fail-open 继承语义登记） |
| WP-B 实施 | ✅ Step 3-6 全部完成（2026-08-27，**WP-B 关闭**） | `docs/evidence/pd12-implementation-acceptance-20260827.md`：B2+B1+B3 双端 delta 修复；本地 132/132 + tech_etf 平台验收通过（金字塔消除/降仓 50%/B3 上下文/收益差 -6.89→-1.37pp）+ 周频零影响；**commit `8e543fd`（7 文件 +901/-7，ptrade_api hunk 级选择性暂存——slippage 九块零进入）双远程推送一致**；回退点 `ae2594a`；他线 slippage 工作树改动完整保留 |
| WP-C 实施 | ✅ Step 3-6 完成（2026-08-27，**WP-C 关闭**） | commit `cd57a6a`（8 文件 +475/-13，ptrade_api 两块选择性暂存 + fidelity_config 他线拆 P-D13c + C4a/b 拆 P-D13b）；五套件 158/158；6 策略重转全 PASS；D2 前置证据入 evidence（双跑冒烟+量化）；双远程推送一致 |
| WP-D 设计 | ✅ Step 1-2 通过（根因 DB 实证 + 最小修复 + engine 雷区解除，两细化并入） | `docs/pd14-d3-firstday-noprice-design.md` + 根因证据（etf_daily 07-01 双 time 值 00:00/08:00；单日精确匹配漏 08:00 组 → no_price；窗口匹配修复 + 去重护栏 + 预取缓存当日聚合；同型消费者"仅此一处"证明；数据域 K-001 known issue 登记 + P-D14b 管线根治排队） |
| **WP-D 实施** | ✅ Step 3-4 完成（2026-08-27） | **`docs/evidence/pd14-implementation-acceptance-20260827.md`**：窗口匹配 + 去重护栏 + 预取缓存修复（T6 暴露既有字节级不一致）；P-D14 6/6 + 八套件 180/180；**tech_etf 本地重跑 07-01 买 33,900@1.334 与平台逐位一致（D3 核心验收）**；回退点 `b8718cc`。**D3 落地 = 合并基线重验窗口开启（四元 P-A3+B2+D2+D3 统一双跑待总调度协调）** |
| P-POS-2 探针 | ✅ 平台回贴（2026-08-27 02:02），**P-D11b 关闭** | `docs/evidence/pd11b-container-probe-20260827.md`：容器键=.SS/.SZ 已与本地一致（非 XSHG 双体系）→ 键归一 AST 改写**无需实施**（探针证伪假设）；membership 精确匹配与本地一致；残影与 get_positions 同款（P-D11 已覆盖）；enable_amount T+1 语义正确。剩余登记：容器残影 audit 计数虚增（weekly 12 vs 10，仅审计口径）+ fall_reversal 自维护 g 已另归因 |
| **排队项（新铁律合规标注）** | —（合法例外，非挂账） | **P-D14b** `BLOCKED(用户裁定)`：**设计 Step 1 已起草** `docs/pd14b-etf-daily-time-normalize-design.md`（根因代码级+数值级定位：08:00=`pd.Timestamp("2026-07-01 08:00:00")` CST 产物→to_ms_timestamp 字符串路径，格式混用；修复三块=写入点归零+存量 1974 行可逆 UPDATE 备份+验收；实施期探针 P1 钉死 08:00 原始形态；**实施锁定=合并基线重验归档后**）；**P-D13b** `BLOCKED(外部依赖)`：C4a/b 停牌/退市保真，解除=backtest_engine 他线 11 hunk 收敛；**P-D13c** `BLOCKED(外部依赖)`：fidelity_config 默认值一行，解除=他线提交 fidelity_config（现为 his untracked）；**AGENTS.md 铁律独立提交** `BLOCKED(外部依赖)`：解除=他线 AGENTS.md M 混排收敛。**容器残影 audit 计数虚增**：判定为平台行为差异（P-D11 已覆盖交易路径，仅观测口径）非框架缺陷，保留登记豁免于"立即解决" |
| **P-D14b 实施** | ✅ Step 3-4 完成（2026-08-27，范围扩展批准 + 退避重试 5 次） | `docs/evidence/pd14b-implementation-acceptance-20260827.md`：写入点归零双表（daemon）+ 存量清洗 etf 8244/stock 16619（time-28800000，备份表照建）+ 正向断言（distinct_mods=1 @57600000）+ D3 6/6 + P-D14b 8/8 + 相关 124/124 + 回滚可行；技术债门禁 S1（时间戳归一）落地、S2/S3 登记。回退点 `6666cb0`。**Step 5 用户确认待明示** |

### 合规记录（2026-08-26）

- 门禁时点：用户直接授权（「解除 Step 0 门禁 → 落盘 + 启动 Phase 0」），审核方追认；本会话核验深度缺陷（ls-remote 判据无法区分交付推送本体）已如实登记；
- tracked 零触碰三重验证通过（ls-files / diff / cached 交集均空）；11 个 untracked 产出文件清单已报审核方排除；
- B2 证据持久锚点：接线代码引入提交 `6f263c3`（2026-08-22），工作树行号引用以该提交为准；
- 基线冻结窗口（基线发车期间）：仅 untracked 文档完善，无任何 tracked 写入。
