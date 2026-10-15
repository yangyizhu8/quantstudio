# D-28 方案①：QMT hist 财务路径按报告期去重折叠（数据修正型）设计文档

- 版本：v0.2.1（②审计两轮通过：第一轮 PASS-with-findings 修订；第二轮增量复审 P1×2 闭环**判「进③实施」**+3 处文档级更正）
- 日期：2026-10-13
- 立项依据：`docs/ISSUES.md` D-28 裁定 (a)（2026-10-13 端测 HISTDBG3 定谳，用户批复①(a) 立项要件 4 项）
- 直接依据：`docs/qmt-d27-fetch-convergence-design.md` §9（背景与待决项完整保留）+ §9.2（折叠技术方向钉死）
- 定谳读数：`docs/evidence/qmt-d27-close-batch-20261013.md` §六·判据 1（`rows=8740 ua=79 ut=79 uk=79 uv=79|71 after=79`）
- 审计记录：第一轮 ZCode sess_c910dab0 **PASS-with-findings**（P1×2+P2×9，tokens 818555：in 778522/out 40033/cacheRead 681728）→ v0.2 逐条采纳；第二轮增量复审 sess_075aae9a **P1×2 均闭环·进③实施**（tokens 1508362：in 1479266/out 29096/cacheRead 1425856；D-P2-1+P3×2 随 v0.2.1 更正）——两轮均 E7 外部委派执行已登记 ISSUES

## 知识反哺检索留痕

- 知识反哺：[无命中] 折叠/报告期去重/m_anntime/行冗余——主库全树 grep `m_anntime|报告期去重|行折叠|hist 冗余` 零命中（检索域：06-数据与工具、11-方法论与工程体系，2026-10-13）
- 知识反哺：[命中] `端测纪律-QMT端测须盘后同窗同源黄金对照`（11-方法论与工程体系/端测纪律-QMT端测须盘后同窗同源黄金对照.md，2026-10-13 二次修正版）——采纳要点：验收端测判据的盘后/背靠背同窗/黄金对照纪律（§五判据 3 的 A/B 构造法沿用）。
- 文档正源：`docs/qmt/inner-api/07-行情函数.md:2388/:2395`（PERSHAREINDEX 原生字段）；实测形态 `docs/evidence/qmt-m5-batch2-probe-20261010.md` §77-83（m_anntime/m_timetag 存在、毫秒时间戳、**裸字段名**形态）。

## 一、问题定义

QMT `get_financial_data` 的 hist 表路径（`eps`/`growth_ability`；`profit_ability` 属 D-29 死代码）返回**日频全历史填充**帧（端测第二批读数合计 kept=9,730,178 行 ÷ 3666 只 ≈ **2655 行/只均值**——与 ISSUES D-28-b 前批 9,718,706 系不同批次读数、均 ≈2655/只；首只全史读数 **8740**——帧窗自 19901219），而本地 `ptrade_api` 契约返回 **PIT 全报告期多行**（首只全史 79 期，均值 ≈2655/110.6 ≈ **24 期/只**）——两端本就分叉（D-28-b 口径发现）：

1. **契约分叉**：QMT 端把每个报告期的值**前向填充到每个交易日**（m_anntime 亦 ffill——D-27a 之谜：`notna` 过滤恒为空操作）；本地口径是「每报告期一行（最新公告版）」。
2. **性能损耗**：9.7M 行的展开/映射/序列化（**处理段**，端测 226s）；折叠后处理行量 ≈ 0.09M（24 期 × 3666 只）——冗余消除倍数 ≈110×（要件④的 ~10s 预期属**处理段**，见 §五判据 2 的段口径披露）。
3. **潜在行为偏差**：行多重集/位置敏感消费点（SG-MS-PEG `_roe_mean_from_fin_indicator` 的 `pairs[-5:]` 均值）在日频形态下语义错误（「末 5 行」=同一报告期 5 个日频行）；当前因 D-09 映射缺失**不可达**，属潜在命中（ISSUES :151 更正定谳）。
4. **折叠生效的结构前提（v0.2 · 审计 P1-1 新增；v0.2.1 实例更正）**：返回列=请求列（`one_shape=(8739,3)` 实证：weekly 请求 3 字段得 3 列）——消费点不请求 `publ_date` 时帧缺 `m_anntime`，折叠无从生效。**真实缺键实例**：SG-MS-PEG:358 `fields=['roe','end_date']`、F-Score:329 `fields=['roe']`（均消费 profit_ability——D-29 修① 后生效；v0.1 曾引 F-Score:77 系**空引**：该处 `_latest_statement` 消费 income/balance/cashflow_statement 三张非 hist 表，`:1611` 早退空帧、根本不存在可折叠的 hist 帧，v0.2.1 复审 D-P2-1 更正）。**对策（§三⑥）**：wrapper 对 hist 表请求**恒附两键原生字段**（框架层通用落点），辅助键经既有投影自动剥离。

**定谳前提已满足**（裁定 (a)）：HISTDBG3 `uk=79 ≪ rows=8740`，`ua=ut=uk` 完全一致，`after=uk`——组合键 `(m_anntime, m_timetag)` 非日频填充、一一对应报告期；`uv=79|71` 证明值列不可作键（eps 跨期重复 8 期）。

## 二、目标与非目标

**目标**：hist 路径每码行集合收敛为**每报告期一行**（`drop_duplicates(subset=['m_anntime','m_timetag'], keep='last')`，§9.2 钉死；**经 §三⑥ 键字段恒附，对全部 hist 消费点无条件生效**）——QMT 端回归本地契约口径（数据修正型），同时消除 ~110× 行冗余（处理段 226s→~10s 预期）。

**非目标**：① D-29（profit_ability 映射缺口）——独立裁定，本方案**不耦合**（折叠对表集无感知，哪张表有数据流经就折叠哪张）；② 财务快照路径（valuation/CAPITALSTRUCTURE）——不经 `_qs_fin_to_rows_hist`，零接触；③ 本地回测端——零改动（wrapper 仅渲染进 QMT 产物）；④ **取数拉取段**（get_financial_data 服务端扫描/传输，端测证据 §八 46-77s/表）——折叠不削减拉取量（拉取行集由服务端形态决定），收益限于处理段，如实披露不虚报。

## 三、改动范围（允许写入清单）

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py`（`_QS_QMT_FUNDAMENTALS_EXT` 区） | ① 新增模块级 helper `_qs_hist_fold(_one, table)`：`_one` 为 DataFrame 且含 `m_anntime`+`m_timetag` 两列 → 折叠（keep='last' 保留该报告期**末行**=最新填充值，与本地「最新公告版」语义一致，§9.2）；缺列/非 DataFrame/异常 → 回落 `dropna(how='all')`（D-26d 既有保护语义）；② **取代** `:1536-1549` D-27a 自保护收敛块（`m_anntime notna` 过滤——HISTDBG3 已证 ffill 使其恒空操作，删除不改变行为；`dropna(how='all')` 降为回落路径）；③ 单码 DataFrame 路径（`:1570-1578`，现**无任何收敛**——不对称）同款接入折叠；④ **键字段恒附（v0.2 · P1-1）**：`_qs_get_fundamentals` 内 hist 表（`table in ('eps','growth_ability','profit_ability')`）的 `native_fields` 计算后，恒确保 `{native_table}.m_anntime`/`{native_table}.m_timetag` 在列（未请求亦附）——**辅助键在返回帧自动剥离**：`:1660-1677` 既有投影只遍历请求 `fields`（`frame columns=fields`）且完整行过滤仅查请求字段，返回契约零变化；内部帧列数 +2（one_shape 读数变化，披露）；⑤ 折叠审计行（`_QS_QMT_ONCE['histfold:'+table]` 每表一次）：`QS_QMT_HISTFOLD t=<table> code=<首只> before=<n> after=<n> intravar=<k>`——`intravar`=**组内值方差哨兵（v0.2 · P1-2）**：首只折叠前帧上逐 `(m_anntime,m_timetag)` 组检查值列（排除两键）组内是否全等（NaN 视相等），不等组数计数（切片执行、零热路径、异常打 `-1`）——>0 即披露（§四前提的运行时哨兵）；与 HISTDBG3（折叠前原始读数）两行并存互证（探针保留不删）；⑥ QSFUNDHIST 汇总行（`kept=`）语义随行集变化——**kept 现为折叠后完整行数**（`_tot` 为折叠后行数合计，drop 语义同步，披露见 §四） |
| 2 | `tests/test_qmt_hist_fold.py`（新建） | 单测 12 例（见 §五，明拆清单） |
| 3 | `docs/evidence/fundamentals-contract-matrix.{yaml,md}` | FUNDAMENTALS 区渲染常量变更 → 同 commit 追认 `qmt_wrapper_hash`（M5.2 纪律） |
| 4 | 本文档 + `docs/ISSUES.md` + `docs/PROJECT_STATE.md` | 簿记 |

**不动**：`_qs_fin_to_rows`（非 hist 单行语义）、`_qs_get_fundamentals` 取数参数/映射表（除 §三④键恒附外）、HISTDBG2/HISTDBG3 探针、`_QS_QMT_FUND_FIELD_MAP`（D-29 域）、策略源码（`quantstudio/backtest/strategies/**` 零改动——消费端差异经**重转产物**生效，落点纯框架层）。

## 四、影响面与判型

- **判型（用户裁定钉死）＝数据修正型**：结果按正确口径变化——QMT 端 hist 行集合从「日频填充」回归「PIT 全报告期」本地契约口径；三项证据验收（§五）。
- **折叠不变式的明示前提（v0.2 · P1-2）**：**同 `(m_anntime, m_timetag)` 组内值列恒等**。语义论证：组合键 = 同一公告（m_anntime）×同一报告期截止（m_timetag）⟹ 组内各行系**同一公告行的日频 ffill 副本**；现有读数（`uv=71<79` 与组内恒等相容）不能证伪 ⟹ 增设 `intravar` 运行时哨兵（§三⑤）+ 单测组内突变用例声明预期语义（若前提被违反，keep='last' 取组内**末行**值——CANSLIM 严格 `>` 语义下原取组内首行，此时其结果可能变化；哨兵>0 触发端测判据 3 逐位归因兜底）。
- **三问前置审计**：①影响其他功能——**当前 QMT 端真实可达 hist 消费点仅 3 个**（`weekly_smallcap`/`周频三层`（均消费 `growth_ability`+`eps`）、`CANSLIM`（**仅消费 `eps`**——v0.2.1 复审 P3-2 精确化））且**全部属性式取行**，逐一论证：weekly 族 `sort_values(['code','end_date','publ_date'], kind='mergesort')`（稳定）+`drop_duplicates(subset='code', keep='last')` 每 code 取 (end,publ) 最大组内**输入序末行**——折叠 keep='last' 恰保留每键组末行，**被选行即被保留行**（行级同一）；CANSLIM `if ed not in best or p > best[ed][0]`（严格 `>`）同 publ_date 组内取首行——经§四前提（组内恒等）首末行值相等 ⟹ 不变；F-Score `sort_values('end_date').iloc[-1]`（**属第四模式**，当前不可达——消费 profit_ability）末行=最大 end_date 组末行=折叠保留行；`SG-MS-PEG`/`F-Score:329`/`低流动性:271`/`roe_probe_batch:29` 消费 `profit_ability` 在 QMT 端**当前恒空帧**（D-29）→ 折叠对其零影响；②回测性能——处理段 226s→~10s（要件④；§二非目标④拉取段披露）；③回测精度——可达消费点结果**逐位不变**（不变式+端测判据 3）；潜在消费点（SG-MS-PEG ROE 均值）**语义修正**：从「同一报告期 5 个日频行均值（≡该期 ROE）」→「末 5 个报告期均值」**回归本地口径**——**经 §三④键恒附，该修正在 D-29 修①后即真实生效**（SG-MS-PEG:358 帧将含折叠键），本方案附**真实函数前后对照**（§五-12，importlib 加载策略文件原函数）+ 端测队列真实对照（依赖 D-29 裁定）。
- **影响面声明（数据修正型要件一，穷举）**：变化面＝①hist 表每码行集合（均 ~2655→~24 行/只，keep='last'）；②QSFUNDHIST 审计行 kept/drop 计数（折叠后口径）；③HISTFOLD 新审计行+intravar 哨兵；④取数请求列恒 +2 键原生字段（内部帧列数 +2；返回帧经投影不变）；⑤（条件项，D-29 修①后）SG-MS-PEG ROE 均值口径修正；⑥单码 DataFrame 路径缺键列帧回落 `dropna(how='all')` 的二阶变化（v0.2 · P2-9：此前全 NaN 行存活到策略；**当前无可达单码 hist 调用**，实害为零，披露入册）。**不变面**＝三可达消费点选股结果、漏斗 L0-L6/R/持仓/净值（端测判据 3 逐位核对）、返回帧列集/完整行判定（投影自动剥离辅助键）、valuation/非 hist 路径、本地回测端、拉取段行为。
- **矩阵哈希**：`_QS_QMT_FUNDAMENTALS_EXT` ∈ `_QMT_HASH_SCOPE` → 同 commit 追认。

## 五、验收标准（可复核判据）

单测 `tests/test_qmt_hist_fold.py`（**12 例明拆**，渲染产物 exec 模式，与既有 QMT 测试同构）：
1. 折叠语义：合成帧（多报告期×多日频行，m_anntime 含 ffill 形态）→ 每报告期恰一行且取**末行**值（keep='last'）；
2. NaN 口径：`drop_duplicates` NaN 视相等（组合键全 NaN 行折叠为一行）；
3. 回落保护：缺 m_timetag 列 → `dropna(how='all')` 路径；折叠表达式异常 → 回落不抛出；
4. 单码 DataFrame 路径同款折叠（不对称消除）+ 缺键列帧回落 dropna 的二阶行为断言；
5. weekly 族消费点不变式：合成数据折叠前后 `_latest_by_code`（sort+keep-last）结果逐位一致；
6. CANSLIM 消费点不变式：per-end_date publ_date-max（严格 `>`）结果逐位一致（含同 publ_date 多行）；
7. F-Score 模式不变式：`sort_values('end_date').iloc[-1]` 结果一致（属性最大行保留性）；
8. **组内值突变预期语义（v0.2 · P1-2）**：同键组内值列不等的合成帧 → 折叠取组内**末行**值（预期语义声明，非不变式）；intravar 哨兵读数 >0；
9. 空帧/零行边界：空输入返回空、不抛错；
10. HISTFOLD 审计行每表恰一次、字段完整（t/code/before/after/intravar）；HISTDBG3 探针读数不变（折叠前打印；**键恒附后探针覆盖面扩展**——此前缺键静默失败的调用方（KeyError→except→once 未置位）将转为正常打印，属正向副作用，v0.2.1 复审 P3-1 披露）；键恒附后内部帧列数 +2 断言；
11. QSFUNDHIST kept 语义=折叠后完整行数（含键恒附不影响完整行判定——仅查请求字段）；
12. **SG-MS-PEG 真实函数前后对照（要件③，v0.2 · P2-8）**：importlib 直接加载 `quantstudio/backtest/strategies/SG-MS-PEG-….py` 的 `_roe_mean_from_fin_indicator` **原函数**（测试侧 import 不违「策略源码零改动」——零改动约束的是编辑产物与源文件），合成含键日频帧（模拟键恒附后的真实形态）跑同一函数——折叠前均值≡最新报告期 ROE（错误口径实证）、折叠后＝末 5 报告期均值（本地口径恢复），**diff 恰源于修正行集**（数据修正型要件二）。

回归门禁（**邻接套件全量**，v0.2 · P2-5）：既有六套件全绿零衰减——`test_qmt_instrument_snapshot`/`test_source_import_qmt`/`test_fund_matrix_qmt_gate`/`test_qmt_close_batch`/`test_qmt_hist_vectorize`/`test_qmt_progress_audit`（`test_qmt_hist_vectorize` 直测 `_qs_fin_to_rows_hist` 黄金对照——fixture 两行异键大概率存活，**存活须被证明而非被省略**；黄金若需随行为更新，按 D-27b §四「验收期局部阻断修复申报」先例显式披露）+ 六策略重转 api_portability ALL PASS + canonical 产物 diff 仅限 FUNDAMENTALS 模板插入段（确定性文本 diff，逐行归因）+ 矩阵哈希同 commit 追认。

端测 E2E（盘后，随用户端测排期队列——与 D-23/D-24/D-30 判据同队，背靠背同窗纪律）：
- **判据 1（折叠读数）**：`QS_QMT_HISTFOLD t=eps/growth_ability before=<首只全史行数> after=<期数> intravar=0`（两行齐；intravar>0 即披露并触发判据 3 归因）；
- **判据 2（性能·处理段口径，v0.2 · P2-2）**：仪器=`QS_QMT_PROGRESS`（`get_financial_data` begin/end + `fin_to_rows_hist` begin/end）；预期＝`fin_to_rows_hist` 处理段 226s→~10s 量级；**取数拉取段（46-77s/表）不因折叠削减**，总段收益=处理段差额，如实登记不虚报；
- **判据 3（数据修正型·可达面零漂移；A/B 构造法 v0.2 · P2-3）**：**盘后背靠背 A/B**（D-27b §八先例构造法）：A=折叠前态产物（detached worktree 自折叠前 commit 单路径提取 wrapper → canonical 重转，sha16 基线）vs B=折叠后产物，同窗连跑，L0-L6/R_selected/buy_submitted/positions/total_value **逐位一致**（三可达消费点不变式的端测实证）。**窗污染处置次序**：出现 diff → 先复跑确认（同窗数据推送污染优先排除）→ 复跑仍 diff 且归因数据漂移 → 换窗重跑；归因折叠行集 → **验收失败回退**（要件二）。注：D-30 快照层只锚合约信息通道、**不锚财务 hist 通道**，不作本判据同窗锚（v0.1 表述已撤回）；
- **判据 4（SG-MS-PEG 真实对照，条件项）**：若 D-29 已裁①（补映射）→ 键恒附（§三④）使 SG-MS-PEG:358 帧含折叠键 → ROE 均值前后对照随端测产出（修正方向=回归本地口径，要件三「准确度只升不降」）；D-29 未裁/裁② → 本判据由 §五-12 真实函数合成对照代偿并在端测报告标注。

## 六、风险与回退

| 风险 | 缓解 |
| --- | --- |
| keep='last' 依赖行序（索引升序=时间序） | QMT 返回为全历史升序帧（D-26c one_shape 实测+head/tail 探针可证）；HISTFOLD before/after 读数即运行时哨兵 |
| §四前提（组内值恒等）被违反 | intravar 哨兵运行时披露（§三⑤）+ 单测 8 预期语义声明 + 端测判据 3 逐位归因兜底（三层防线） |
| 折叠改变 QSFUNDHIST 审计行语义 | 设计披露（§四影响面②）；判读文档随证据更新 |
| 键恒附改变内部帧形态 | 返回帧投影自动剥离（§三④）；单测 10 列数断言；one_shape 读数变化入披露 |
| D-29 联动（profit_ability 若入表集） | 折叠表集无感知+键恒附表集同步（`table in ('eps','growth_ability','profit_ability')` 与 :1653 既有 hist 集一致）；D-29 修①后自动覆盖；裁②则无交集 |
| 与 D-30 快照层叠加 | 正交（不同数据通道：合约信息表 vs 财务 hist 行集）；判据 3 不互为同窗锚（各自独立 A/B） |
| 残余性能不达预期 | 判据 2 处理段口径如实登记；性能是伴生收益，契约修正是本体——不构成回退条件 |

**回退条件与手段（v0.2 · P2-7 补两端）**：①验收失败（单测红/六策略 FAIL/邻接套件衰减/端测判据 3 归因折叠行集）＝回退——`git revert` + 矩阵哈希同 commit 回追认 + **六策略重转复核 + 产物 sha16 对照 + QMT 端已部署产物替换**（回退不重转=端上仍跑折叠版）；②运行时无开关（数据契约修正不设模式开关，与 D-26d dropna 同族定位）；③写前快照 stash 基线 `git reset --hard`（实施轮建立）；④**提交顺序前提**：D-27b/D-30 先过⑤确认提交入库，D-28 方可作为独立 revert 单元（当前三者共居未提交态）。

## 七、与 D-29 的关系（呈裁点，不阻塞本方案）

折叠实现**表集无感知**（流经 `_qs_fin_to_rows_hist` 的表一律折叠）——D-28 对 `eps`/`growth_ability`（实测有数据）已成立，**不等待 D-29 裁定**。D-29 两条路径均兼容：修①（补映射）→ 折叠自动覆盖 profit_ability，且键恒附使 SG-MS-PEG 判据 4 真实化；裁②（移除 hist 表集）→ 无交集。**建议**：D-28 先行实施，D-29 独立裁定（ISSUES :152 前置表述按此理解执行——「先修或先移除」决定的是折叠**对 profit_ability 有无意义**，不决定折叠对 eps/growth_ability 的可实施性）。

## 变更日志

| 日期 | 版本 | 内容 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | 初稿（DSH 起草）：按 ISSUES D-28 裁定 (a) 立项要件 4 项 + qmt-d27 方案 §9.2 钉死技术方向成文 |
| 2026-10-13 | v0.2 | ②审计第一轮（sess_c910dab0，PASS-with-findings）P1×2+P2×9 逐条采纳：**P1-1** 键字段恒附（§一4/§三④/§四影响面④——返回列=请求列结构发现，SG-MS-PEG/F-Score 缺 publ_date 折叠不生效的链条闭合：恒附+投影自动剥离）；**P1-2** 组内恒等前提明示+intravar 哨兵+单测 8（§四/§三⑤/§五-8）；P2-1 行数算术更正（9.7M 合计/均 2655/首只 8740/折叠后 ~0.09M）；P2-2 判据 2 改 QS_QMT_PROGRESS 处理段口径+拉取段不削披露（§二④/§五判据 2）；P2-3 判据 3 背靠背 A/B 构造法+窗污染处置次序+撤回 D-30 同窗锚错误表述；P2-4 三可达点模式表述更正（F-Score 属第四模式不可达）；P2-5 邻接三套件入门禁（hist_vectorize 黄金存活须证明）；P2-6 单测 12 例明拆+空帧边界；P2-7 回退补产物重转/sha16/QMT 端替换+提交顺序前提；P2-8 §五-12 改 importlib 加载真实函数；P2-9 单码路径二阶变化入影响面（§四⑥） |
| 2026-10-13 | v0.2.1 | ②审计第二轮（sess_075aae9a 增量复审：P1×2 均闭环·**进③实施**；无行号漂移/未偏离 §9.2/立项要件 4 项完整承载）随簿记 3 处更正：**D-P2-1** §一4 F-Score:77 空引更正为 F-Score:329（:77 消费非 hist 三表早退空帧）；**P3-1** §五-10 补探针覆盖面扩展披露（键恒附后缺键静默失败调用方转为正常打印，正向副作用）；**P3-2** §四 CANSLIM 仅消费 eps 精确化+§一 kept 双批次读数标注（9,730,178 端测第二批 vs ISSUES 9,718,706 前批，均 ≈2655/只）。复审附提请知悉项：§七「D-29 不阻塞 eps/growth_ability 可实施性」排期口径须 ⑤用户显式认可 |
