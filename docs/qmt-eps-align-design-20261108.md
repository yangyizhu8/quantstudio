# QMT×本地 EPS 因子层对齐方案（舱二·起草件·候②审）

> 起草：2026-11-08 20:5x（+08:00）｜节点⑤（QMT转换管线项目执行会话）｜**起草≠立项**（Issue 登记随舱一结果·总调度 R345 裁）
> 上游：R339/R340 两舱判据裁定（**舱二＝D 类残余归因 EPS/基本面层**）｜QD-2026-0023（EPS 修复不收敛遗留面）｜V6 五维消融矩阵（C2＝价格层·已由甲案闭合）
> 判型：**数据修正型**（结果按正确口径变化：显式声明影响面 ＋ diff 只能归因于 EPS 口径修正行集 ＋ 准确度只升不降；出现任何其他 diff 即验收失败回退）
> 知识反哺检索留痕：`知识反哺：[命中] QD-2026-0023（qd 族/D3/QD-2026-0023.md，2026-11-06）——采纳其「消融矩阵定位到维度层」「无除权标的全同＝最强反证组」两条方法论；知识反哺：[无命中] knowledge/** 关于 EPS PIT 选取／复权后 EPS 口径的专项条目（2026-11-08）`

---

## 一、问题定义

**事实层（已确证）**：
1. 2026-11-08 嵌入批真机轮（`embedded n=5385`）后，**价格层（C2）已闭合**，但五维对照 **D 类＝27**（选股分叉未收敛）⇒ 残余差异不在价格层；
2. V6 时代消融矩阵已把主因归到**价格层（C2，占 9/13＝69.2%）**，余 30.8% 当时未定谳；**「EPS 修复不收敛」是当时就记录在案但从未定位到根因的遗留面**（QD-2026-0023 讨论节）；
3. 两侧 EPS 数据源**形态不同**（见 §二）——这是「修复不收敛」的结构性成因候选。

**待定谳（本件目标）**：EPS 因子层两侧是否因 ①主源不同 ②PIT 选取规则不同 ③本地 EPS 被回填覆盖 ④单位/累计口径不同 而产生偏差；若成立，**修复面＝EPS 取数口径对齐**（框架层，策略源码零改动）。

---

## 二、两侧源对照（实测取证，2026-11-08）

| 维度 | 本地（`quantstudio.db`·隔离副本） | QMT 侧（HISTDBG3 真机实测） |
|---|---|---|
| 存储形态 | **行存储**（按 code×报告期一行） | **宽面板**（多字段列，同一 code 一行） |
| 表/列 | `fin_indicator`（**`eps`/`diluted_eps`**/`bps`/`roe`/`pe_ttm`…）、`income_statement`（**`basic_eps`**/`operating_revenue`/`net_profit`…） | 面板 **`s_fa_eps_basic`**（字段 `m_anntime`/`m_timetag`/`s_fa_eps_basic`） |
| 时间轴 | `ann_date`（公告日·ms）＋ `end_date`（报告期·ms） | `m_anntime`＋`m_timetag`（**双时间轴**） |
| 折叠形态 | — | 8,758 行 → **折叠 57 行**（`HISTFOLD before=8758 after=57 intravar=0`）；head 为 19901219 起 NaN 段、tail 至 20261104/20261105 |
| 量级 | `fin_indicator` 135,840 行／`income_statement` 169,041 行 | 13 只×多日（诊断层） |

### 四项规格的取证结论（总调度指定四件）

| # | 规格项 | 实测结论 | 待补 |
|---|---|---|---|
| ① | **主源定位** | 本地 EPS 有**两个口径**：`fin_indicator.eps` 与 `income_statement.basic_eps`。抽样 8 例（2026-06-30 报告期）**三者全同**（`eps==basic_eps==diluted_eps`）；但**同期全库 2,346 行 `eps ≠ basic_eps`** ⇒ 两口径在**历史段分叉** | 需逐段统计分叉区间与幅度；并定 QMT `s_fa_eps_basic` 对应哪一个（须查 QMT 字段文档） |
| ② | **PIT 映射** | 本地 `ann_date − end_date` **中位 52 天、最大 1,324 天** ⇒ 公告日与报告期可差 3.6 年 ⇒ **PIT 选错即取未来数据或整段错窗**；QMT 侧为 `m_anntime`/`m_timetag` 双轴 | 需建立 `ann_date ↔ m_anntime/m_timetag` 的逐语义映射（哪一个＝公告日、哪一个＝报告期、哪一个＝入库时点） |
| ③ | **`update_flag` + `backfill_eps_source` 分布** | `update_flag`：**0→89,005 行／1→46,835 行（35% 带"已更新"标记）**；`backfill_eps_source`：**3,189 行来自 `income_statement.basic_eps` 回填**，其余 None | 需查 `update_flag=1` 的语义（更新＝上游变动覆盖？）与回填 3,189 行是否与 QMT 面板口径冲突（**「本地 EPS 被回填覆盖」＝历史多次「EPS 修复不收敛」从未定位到的层级**） |
| ④ | **单位与累计口径** | 本地 `eps` 为**当期基本 EPS**（元/股，抽样 0.4／0.862／−0.01 等）；TTM/累计口径未见独立列 | 需核 QMT 面板 `s_fa_eps_basic` 是当期值还是 TTM（若策略 EPS 因子用 TTM，则口径差直接放大） |

---

## 三、修复方案（框架层·待②审）

### 3.1 原则（对齐铁律 07）
- 修复落点**全部在框架层**：EPS 取数适配层（`source_import_qmt.py` 的 `_QS_QMT_FUNDAMENTALS_EXT` 一侧）＋ 数据访问层；**策略源码零改动**；六产物重转生效；
- 纯增益审计（铁律 18）：本项为**数据修正型**，须显式声明影响面（EPS 因子变化的选股差异属预期）＋ diff 只能归因于 EPS 口径修正行集；
- 与甲案（价格层）**不混办**：甲案已闭合，舱二独立成件、独立验收。

### 3.2 候选实现路径（候②审择一）

| 案 | 做法 | 优点 | 风险 |
|---|---|---|---|
| **甲（推荐）** | **EPS 面板同源对齐**：QMT 侧取 EPS 时**用本地 `fin_indicator.eps` 口径重建**（按 `ann_date` PIT 选行、口径=当期基本 EPS、单位元/股），**与甲案同范式**（预计算快照/常数嵌入产物，零 fileIO） | 与甲案同构、可复用已验证的嵌入式范式；口径完全可控；不依赖 QMT 面板语义 | 产物再增体积（EPS 面板 13 只×多日 vs 全市场 5,000+ 只 ⇒ 需定 universe 裁剪）；面板数据来源需先查明 |
| 乙 | **QMT 面板口径修正**：弄清 `s_fa_eps_basic` 语义后，用 `m_anntime/m_timetag` 做 PIT 选取对齐本地 | 产物体积零增 | 依赖 QMT 侧语义文档（**须按铁律 16 查 Context7 `/websites/dict_thinktrader_net`**）；若面板语义不可控则走不通 |
| 丙 | **因子层重算**：在本地侧把 EPS 因子改成与 QMT 同口径 | 反向对齐 | **违反「保真以本地为参照」既定方向**（R328 裁甲案时已否决乙案的同类逻辑）⇒ 不作 |

**我方倾向甲**：与已验证的甲案范式同构（嵌入式、零 fileIO、可复现），且不依赖平台语义黑盒。

### 3.3 实施前置（未完成不得进入③）
1. **查 QMT 面板语义**（铁律 16）：`s_fa_eps_basic` 的口径（当期/TTM）＋`m_anntime`/`m_timetag` 各自语义 —— **文档依据须标注**，查不到则如实声明并按甲案绕开；
2. **本地 EPS 主源定谳**：`fin_indicator.eps` vs `income_statement.basic_eps` 在历史段的 2,346 行分叉逐段量化，选定口径并说明理由；
3. **`update_flag=1`（35% 行）与回填 3,189 行的语义取证**：确认是否覆盖了「与 QMT 面板同源」的历史值；
4. **EPS 因子实际取数路径定位**：策略的 EPS 因子在产物中走哪条 API（`_QS_QMT_FUNDAMENTALS_EXT` 哪一段）、取的是哪个字段、窗口多长（PIT 截止 D-1）；
5. **universe 裁剪方案**：EPS 面板若走甲案（嵌入产物），需定「哪些 code 需入面板」的判据（建议：策略候选池或按 EPS 可用性裁剪，避免全市场 5,000+ 只撑爆产物体积）。

---

## 四、影响面

| 面 | 评估 |
|---|---|
| 本地回测 | **零影响**（不改本地取数；甲乙丙三案均只动 QMT 侧） |
| QMT 产物 | EPS 面板嵌入 ⇒ 产物体积再增（增量取决于 §三-5 裁剪方案）；六产物 SHA16 换锚（v1.8）＋矩阵追认 |
| 选股 | EPS 因子变化 ⇒ 选股差异**属预期**（数据修正型）；判据＝**EPS 层归因后 D 类残余可解释**（不是「D≤5」这种单点计数） |
| 实盘 | 同甲案：实盘运行机不部署嵌入数据（无部署语义＝产物自带） |
| 与甲案关系 | **不混办**：甲案（价格层）已闭合，舱二独立验收；若舱二与甲案同批重转，须分两次换锚便于 diff 归因 |

---

## 五、验收标准（V1-V8 候②审）

| 门 | 判据 |
|---|---|
| V1 | §三-3 五项前置全部有据可查（QMT 面板语义带文档标注；本地口径分叉量化；回填语义确认；EPS 取数路径定位；universe 裁剪方案定稿） |
| V2 | EPS 面板快照导出脚本实跑（产出 panel 常量源：code×日期→EPS 值，含 PIT 锚） |
| V3 | **PIT 正确性单测**：给定 ann_date 序列，断言取到的行 `ann_date ≤ D-1`（禁未来函数）；含「公告日距报告期 1,324 天」极端样本 |
| V4 | **回填覆盖守卫单测**：`update_flag=1` 与 `backfill_eps_source` 非空行，断言行为明确（要么排除、要么显式对齐） |
| V5 | 六策略重转 ALL PASS（enc=gbk）＋ portability 0 ＋ 矩阵追认（v1.8） |
| V6 | 真机 FACTDBG 轮：**EPS 相关字段两侧逐位对照**（打印精度下限判），差异归零或落在已量化口径差内 |
| V7 | **五维对照 D 类复测**：EPS 层归因后 D 类残余**逐只可解释**（不设单一数值阈值——原 D≤5 阈值已被证伪为「价格层修复⇒选股收敛」的错误假设） |
| V8 | 回归：既有 QMT 测试面全绿＋甲案快照层不受影响（嵌入双层互不覆盖） |

**回退**：任一门红 ⇒ revert 本批 commit（甲案 v1.6 产物不受影响——两案分 commit 换锚，diff 归因清晰）。

---

## 六、风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| R1：QMT 面板语义不可查（Context7 无该字段文档） | 中 | 按铁律 16「查不到即如实说明」⇒ 走甲案（不依赖平台语义） |
| R2：EPS 面板嵌入使产物体积超 QMT 接受度 | 中 | §三-5 裁剪＋必要时 zlib+base64（R330 ②审修订②已备 B 案） |
| R3：本地 EPS 被回填覆盖（35% update_flag=1 + 3,189 回填行）⇒ 「以本地为参照」本身带噪 | **高** | V4 守卫；若确认覆盖 ⇒ **基线口径需重定谳**（超出舱二 ⇒ 升总调度裁决） |
| R4：EPS 因子在产物中走多条路径（fundamentals 段 vs 策略自取）⇒ 改不全 | 中 | §三-3 第 4 项先定位路径，再定改动面 |
| R5：EPS 层修完仍不收敛（第三层差异，如成分股/财务状态） | 中 | V7 要求**逐只可解释**而非数值阈值 ⇒ 残余自然浮出 |

---

## 七、本轮（起草轮）已完成与未完成

**已完成**：问题定义（事实层四条）｜两侧源对照表｜**四项规格全部取得实测数据**（主源双口径+2,346 行分叉／PIT 落差中位 52 天·最大 1,324 天／`update_flag` 35% 标记+3,189 回填行／当期 EPS 单位元·股）｜三案对比＋我方倾向（甲）｜五项实施前置｜V1-V8 验收｜五项风险｜判型声明。

**未完成（下一轮可做，且**不需要**等舱一）**：§三-3 的 ①（查 QMT 面板文档·铁律 16）与 ②③④⑤（本地侧只读取证续做）——**均属只读取证，无写权风险**；**Issue 登记按总调度指示随舱一节奏**（起草件先行 ≠ 立项）。

---

知识反哺：[命中] QD-2026-0023（qd 族/D3/QD-2026-0023.md，2026-11-06）——采纳「消融矩阵定位到维度层」「反证组最强」两条方法论；本件新增方法论候选：「**分层验收（目标域逐位 + 残余归因）优于单一计数阈值**」——原 D≤5 阈值隐含「价格层修复⇒选股收敛」假设，已被 2026-11-08 实测证伪，建议入 QD-2026-0023 反馈段。
本轮沉淀：QD-2026-0023（讨论节追加 EPS 层四规格取证结论·待②审后正式化）


---

## 十一、实施规格 v1（2026-11-09 02:0x · R350 后补·前置①④⑤ 闭合）

### 11.1 前置结论（四项，附证据）

| # | 前置 | 结论 | 证据来源 |
|---|---|---|---|
| ① | **QMT 面板语义**（Context7·铁律 16） | `get_raw_financial_data`／`get_financial_data` 在 **>1 股 × >1 时段 ⇒ pandas.Panel（items=stock, major=time, minor=field）**；字段表（`nativeApi/xtdata.html`·PershareIndex 每股指标）：**`s_fa_eps_basic`＝基本每股收益（当期）**、`s_fa_eps_diluted`＝稀释、`adjusted_earnings_per_share`＝扣非；**`m_anntime`＝公告日**、**`m_timetag`＝报告截止日**；**无 TTM EPS 字段** | Context7 `/websites/dict_thinktrader_net`（2 次查询：`innerApi/data_function.html`＋`nativeApi/xtdata.html`）；⚠️`m_timetag` 在股票字典表被标为「Change Date」、在每股指标表标为「报告截止日」——**以逐字段注释表为准**，出入如实登记 |
| ①' | **两侧 EPS 不同源（差异主机制）** | **QMT 面板比本地库新一个季度**：002568 本地最新报告期＝**2026-03-31**（ann=2026-04-29，eps=0.21）**无 2026-06-30 期**；QMT 面板 `m_timetag→2026-06-29`、`m_anntime→2026-08-04`、**s_fa_eps_basic=0.46** ⇒ **0.46 ∉ 本地该股任一报告期值** | 本地隔离副本库 `fin_indicator` 查询 ＋ 真机 HISTDBG3 |
| ④ | **EPS 取数路径** | 产物侧走 **`C.get_financial_data(fieldList, stockList, startDate, endDate, report_type=…)`**，fieldList 含 `s_fa_eps*`；返回 Panel 形态与真机 `raw=Panel levels=2 idx_names=['major','minor']` 吻合 | 产物 grep（`get_financial_data` 12 处、`s_fa_eps` 3 处）＋真机 HISTDBG2/HISTDBG3 |
| ⑤ | **universe 与体积** | 本地 `fin_indicator`：**distinct code 5,211／行数 135,840／同(code,end_date) 重复 0／全部行有 ann_date**；库内近窗全市场 code 5,552 | 隔离副本库查询 |
| ② | **本地口径分叉** | `fin_indicator.eps` 与 `income_statement.basic_eps` **当期全同**（抽样 8 例），但全库 **2,346 行双向分叉**（最大年 2025 平均差 0.0393）⇒ **取 `basic_eps`**（与 QMT 面板 `basic` 语义严格对齐），分叉行需在 §11.3 规则内消解 | R344/R346 取证 |
| ③ | **回填语义** | `update_flag` ＝ **来源/重采标记**（非 PIT 修正标记；两类公告滞后中位 56 vs 48 天同分布）；`backfill_eps_source` 3,189 行（2.3%）分散无集中期 | R346 核实 |

### 11.2 PIT 映射（定谳）

```
本地 fin_indicator.ann_date  ↔  QMT m_anntime   （**可见性锚**：公告日）
本地 fin_indicator.end_date  ↔  QMT m_timetag   （**报告期**：报告截止日）
```

⇒ 嵌入值按 **ann_date ≤ D-1** 取「最近一期」的 (end_date, eps) ⇒ 与 QMT 面板的可见性规则逐位对齐。

### 11.3 实施设计（甲案·嵌入式·零 fileIO·零平台语义依赖）

1. **数据源**：`fin_indicator`（主源，口径＝`eps`，与 `basic_eps` 当期同值）＋`income_statement.basic_eps`（分叉行择优）＋`ann_date`/`end_date`；
2. **导出**：新脚本 `scripts/export_eps_snapshot.py`（仿 `export_qfq_factor_snapshot.py`）：产出
   `_QS_QMT_EPS_SNAP_DATA = {'002568': ((end_d8, ann_d8, eps), …), …}`（**repr 全精度**、**禁时间戳**、code 升序幂等、GBK/ASCII 安全）；
3. **注入**：与 QFQ 快照同机制——`convert(qfq_embed=…, eps_embed=…)` 将常量块拼入产物（**参数独立·缺省零漂移**）；
4. **消费**：QMT 侧 EPS 因子取数后（或直接替换取数调用），**用 `_QS_QMT_EPS_SNAP_DATA` 按 (code, ann_date≤D-1) 取值**覆盖面板值 ⇒ 两侧 EPS 同源；面板取数保留为**兜底与对照**（面板值与嵌入值不一致时打印一行 `QS_QMT_EPS_MISMATCH code=… local=… panel=…`，fail-loud 便于归因）；
5. **universe 裁剪**：EPS 计算范围 = **策略实际对 EPS 求因子的 code 集合**（见 §11.4 侦察结果）；若该集合仍大 ⇒ 再按「近 N 期」裁剪；**体积上限 1.5 MB**，超限则走 **zlib+base64 压缩嵌入**（R330 ②审修订② B 案，3.6.8 stdlib 可用）；
6. **回填与更新_flag**：以**本地值为参照**（既定方向「保真以本地为参照」）；`update_flag=1` 行不排除（已证其非 PIT 语义），但**分叉行以 `basic_eps` 为准**（对齐 QMT `basic` 语义）；
7. **不改策略源码**（铁律 07）：全部落点＝导出脚本 ＋ wrapper 层 ＋ 单测。

### 11.4 待补（下一轮随实施一并做）

- EPS 计算 universe 的**精确 code 集合**（侦察见 §11.5 实测输出）；
- 体积实测（按裁剪后 universe 导出后统计）；
- 单测：PIT 正确性（ann_date≤D-1）、回填覆盖守卫、分叉行择优、embedded/absent/broken 三态、面板对照 fail-loud。

### 11.5 侦察实测（EPS 取数调用点·见脚本输出）

- `get_financial_data` 调用点：**产物 L6750/6879/6892/6971/7056/7310/7316/7321**（8+ 处）；
- `s_fa_eps` 字段：**产物 L6755/6763/7254**；
- `_compute_eps_factors`：**产物 L12（垫片注释引用）/L7891/L8051**；
- ⚠️**面板侧的 `get_raw_financial_data` 命中 0** ⇒ 产物走的是 `get_financial_data`（两者返回 Panel 形态在 >1×>1 时一致）——**修订方案件 §二「面板 get_raw_financial_data」的表述**（以本节为准）。

---

知识反哺：[命中] QD-2026-0023（qd 族/D3/QD-2026-0023.md，2026-11-06）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）


---

## 十二、R1 开工留痕（2026-11-09 23:5x · C1 甲案实施批开工前）

**知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-09）**
——采纳要点：①「分层验收（目标域逐位＋残余归因）优于单一计数阈值」⇒ EPS 层验收沿用「逐只可解释」判据（原 D≤5 已被 08 日实测证伪）；②「单一事实源的可机读块必须带自校验」⇒ EPS 嵌入常量块须带 embedded/absent/broken 三态自检；③「上游可变因子一致性判据（铁律 25）」⇒ 本地库 EPS 与 QMT 面板 EPS 属「结构化版本差」，非逐位判据，须分层放行。
**知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-09）**
——另采：④「禁时间戳·可复现性铁律」⇒ EPS 快照产物禁含转换时刻戳，观测锚＝`n+base`。

**实施批边界（C1 甲案·zcode 委派）**
- 委派范围＝**新脚本 `scripts/export_eps_snapshot.py` ＋ 新单测文件 `tests/test_qmt_eps_snapshot.py`**（wrapper 消费层与六产物重转由 DSH 侧执行，审计禁区不委派）；
- **策略源码零改动**（铁律 07）；**wrapper 改动仅限 EPS 消费点**（`_QS_FMT_FUNDAMENTALS_EXT` 字段映射段），且须同 commit 追认 `check_fund_matrix --check --reverify`；
- 判型＝**新增能力型**（新增 EPS 面板对齐能力，既有行为在 `eps_embed` 缺省时逐位不变）；
- 体积护栏：嵌入后产物 > 1.5 MB ⇒ 走 **zlib+base64 压缩嵌入**（C2 已预授权）；
- O5 验收五步在 DSH 侧执行（①diff 范围 ②构建 ③静态 ④契约 ⑤测试），测试**不由 ZCode 自写自测**。

**本轮沉淀**：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）


---

## 十三、R357 补记（委派交付关键发现 ＋ 四项限制回应 ＋ 嵌入形态裁定）

### 13.1 关键发现：`fin_indicator` 时间轴＝**北京午夜锚**（必须入案）

- **现象**：`fin_indicator.end_date`/`ann_date` 的毫秒 epoch **锚在北京午夜（+08:00 00:00）**；首版按 **UTC** 换算会使**全库 d8 早一天**。
- **实证**：修复（固定 +8）后 `002568` 尾期 = `(20251231, 20260429, 0.62)` / `(20260331, 20260429, 0.21)`，与 **§11.1-①'** 逐位一致；`base = 20260801`。
- **守卫**：委派方已加 **T9 回归**（时区固定 +8 断言）⇒ 后续任何时区回归会被单测拦截。
- **族属**：与库「**CST 午夜 epoch ms**」数据陷阱清单**同族**（库内时间轴不是 UTC 语义）。
- **我方采纳**：EPS 消费端（wrapper）**同样必须按 +8 换算**，禁按 `utcfromtimestamp`；写 PIT 判定（`ann_date <= D-1`）时 d8 一律走北京墙钟。

### 13.2 四项限制裁定回应

| # | 限制 | 我方裁定 |
|---|---|---|
| ① | **`eps` NULL 但 `basic` 可用（真库 5 行）** | **维持任务包字面口径：跳过＋计数，不回退 `basic_eps`**。理由：QMT 面板 `s_fa_eps_basic` 是**单一字段**，若我方对缺失行回退 `basic`，则两侧口径在「面板有值/本地无值」处产生**不可归因的分叉**，会污染舱二「逐只可解释」归因。5 行占比 0.004%，不影响因子。**如后续归因发现这 5 行恰好落在诊断标的上，再按分叉路径改（改动点＝main 扫描循环 `eps is None` 分支，先于分叉判定，委托方已给定位）。** |
| ② | `--since` ＝ **公告日窗**语义 | **采信**（docstring 已写；与 PIT「ann_date 为可见性锚」一致） |
| ③ | `income_statement` 缺失 ⇒ **exit 2** | **照准从严**（分叉判定依赖该表；缺失时静默降级会让分叉行统计失真） |
| ④ | 全量 emit **3.96 MB** | **见 §13.3 嵌入形态裁定** |

### 13.3 嵌入形态裁定（C2 预授权承接 · 两级策略）

**裁定：甲＝「先 `--recent-periods` 裁剪，再必要时 zlib+base64」两级策略；不采用 `--universe-file` 裁剪。**

| 级 | 手段 | 参数 | 体积预估 | 启用条件 |
|---|---|---|---|---|
| **一级（首选）** | `--recent-periods` 裁剪 | **16 期** | ≈ 3.96 MB × (16/26 期) ≈ **2.4 MB** | 默认启用 |
| **二级（保障）** | zlib+base64 压缩嵌入 | — | 文本字典压缩比 ~3~4×，再 ×1.33 ⇒ ≈ **0.8~1.1 MB** | 一级后产物 **> 1.5 MB** 即启用（C2 已预授权） |

**理由**：
1. **16 期口径依据**：策略 `_compute_eps_factors` 算 **C（当季 EPS 加速）** 与 **A（3 年 EPS CAGR）** ⇒ **A 需 3 年≈12 期**，16 期留 33% 余量；当前回测窗（2026-06~09）对应覆盖 2023Q2 起 ⇒ **足够**；
2. **不按 universe 裁**：EPS 因子对**全市场**算 C/A，按 universe 裁剪会漏股 ⇒ 因子与本地不一致（违背甲案「同源」目的）；
3. **二级压缩的必要性**：v1.7 单产物 ~1.04 MB 已真机验证可加载；**>2 MB 未验证** ⇒ 用压缩把总量压回已验证量级；
4. **⚠️ 重导约束（必须入案）**：`--recent-periods` 是**按回测窗起点的相对裁剪**——**若将来回测窗更早，必须按新窗起点重算期数并重导**（否则 A 因子缺历史 ⇒ 静默降级）。此条写入 wrapper 消费端的 fail-loud：嵌入面板若覆盖不到所需期数，打印 `QS_QMT_EPS_SPAN_SHORT code=… need=… have=…`。

### 13.4 下一步（O5 已过）

1. wrapper EPS 消费层（`_QS_FMT_FUNDAMENTALS_EXT` 字段映射段后接 EPS 覆盖逻辑；按 +8 换算做 PIT 选取；面板对照 fail-loud `QS_QMT_EPS_MISMATCH`；期数不足 fail-loud）；
2. 六产物 v1.8 重转（EPS 嵌入换锚 + `qmt_wrapper_hash` 追认）＋ PTrade 五件重转（`wrapper_hash` 追认·**分立禁串**）；
3. 真机复跑验 EPS 面板对齐（判据：逐只可解释）。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制·**北京午夜锚**）


### 13.3-补 压缩体积实测（2026-11-10 · 形态裁定收口）

> 环境：**生产库被 `quantstudio.pipeline.daemon --mode forever` 独占持锁**（PID 49060，启于 03:02:59，wal 08:40 仍在写）
> ⇒ 依铁律 19（daemon 生命周期占用）**不擅动**；本实测在**隔离副本库** `data/backup_import_20261101_040935/quantstudio.db` 上预演，**最终件须生产库重导**（无锁窗后）。

**导出读数（副本库·预演）**

| 口径 | 标的数 | 导出行数 | emit 体积 | zlib(9)+base64 | 压缩率 | 单产物预估（本体 1,042,486 B） |
|---|---|---|---|---|---|---|
| **全量** | 5,206 | 134,202 | 3,959,465 B | **792,460 B** | **20.0%** | 明文 4.77 MB ／ 压缩 **1.75 MB** |
| **recent-16** | 5,206 | 79,874 | 2,393,132 B | **460,996 B** | **19.3%** | 明文 3.28 MB ／ 压缩 **1.43 MB** |

**统计闭合校验**：扫描 135,840 ／ annNULL 排除 0 ／ epsNULL 跳过 **1,638** ／ epsNaN 0 ／ **分叉 2,346（basic_eps 取值）** ／ 非法 code 0 ／ 无效时间轴 0 ／ 重复 0 ／ 冲突 0
⇒ **134,202 = 135,840 − 1,638 精确闭合**；recent-16 裁剪 54,328 行（134,202 − 79,874）✓ 与委派回执及总调度抽验读数**逐项一致**。

**★裁定收口（两级策略经实测确认）**

```
一级 recent-periods=16  ＋  二级 zlib+base64  ⇒  单产物 1.43 MB  < 1.5 MB 上限  ✅
（仅一级明文：3.28 MB 超限 ｜ 仅全量压缩：1.75 MB 仍超限 ⇒ 两级并用才达标）
⇒ v1.8 嵌入形态 = recent-16 裁剪 + zlib(9)+base64 压缩嵌入
⇒ 较 v1.7（1.04 MB）仅 +0.39 MB ⇒ 真机粘贴/加载风险低
```

**JSON/emit 形态（实读）**：顶层键 = `enabled` / `base` / `since` / `data`；`data` 为 `{code: [[end_d8, ann_d8, eps], …]}`（三元组，按 end_date 升序）；`base = 20260801`。

**消费端实现要求（wrapper 落点）**：
1. 读常量 → `zlib.decompress(base64.b64decode(...))` → `ast.literal_eval` 或 `json.loads`（**QMT 3.6.8 stdlib 均有**）；
2. PIT：`ann_date <= D-1` 取最近一期，**d8 一律北京墙钟（固定 +8）**，禁 `utcfromtimestamp`；
3. fail-loud 三式：`QS_QMT_EPS_SNAP absent/broken`（常量缺失/解析失败）、`QS_QMT_EPS_MISMATCH code=… local=… panel=…`（面板对照）、`QS_QMT_EPS_SPAN_SHORT code=… need=… have=…`（期数不足）；
4. **策略源码零改动**（铁律 07），全部落 wrapper 层。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制·北京午夜锚·压缩形态实测）


---

## 十四、段二实施规格定谳（2026-11-10 · 落点图 ＋ 对齐策略修正）

### 14.1 落点归属实测（模板串内外定谳）

| 模板串变量 | 起行 | 说明 |
|---|---|---|
| `_QS_QMT_HISTORY_EXT` | **L608–713**（105 行） | **EPS 快照层所在**（段一已插入；EPS 标记字符位 @2572，先于 QFQ @8538） |
| `_QS_QMT_FUNDAMENTALS_EXT` | **L1575 起** | **EPS 消费点所在**：归一函数 L1707 ／ 全历史行收集 L1886 ／ 字段映射表 `_QS_QMT_FUND_FIELD_MAP` L2083 ／ 取数主体 L2152（`C.get_financial_data(native_fields, codes, '19900101', …)`） |
| 其它 | `LOG_EXT` L250／`CONTEXT_VIEW_EXT` L331／`DATA_VIEW_EXT` L498／`POSITION_EXT` L1077／`ORDER_EXT` L1173／`ASHARES_EXT` L1279／`TRADE_DAYS_EXT` L1314／`STOCK_INFO_EXT` L1353／`WRAPPER_ZONE` L2313／`ALIAS_ZONE` L2487／`MODULE_HEADER` L2502 | — |

**关键结论**：四个 EPS 落点（1707/1886/2083/2152）**全部在 `_QS_QMT_FUNDAMENTALS_EXT` 模板串内**（产物侧运行期执行）；而段一落的 `_qs_eps_*` 三函数在 `_QS_QMT_HISTORY_EXT` 内。两串最终**拼接进同一产物文件、同属模块级作用域 ⇒ 运行期互相可见**，无需调整位置。

### 14.2 ★对齐策略修正（本轮实读推翻我方原设计）

- **原设计（错）**：`_qs_eps_pick(code, D-1d8)` 按「公告日 ≤ D-1 取最近一期」覆盖 ⇒ **会改变面板返回的行结构/期数**，且与「面板本身已按 PIT 给出历史各期」的既有语义冲突。
- **修正（对）**：**按 `(code, end_d8)` 同键逐期替换 `eps` 数值**——面板返回历史各期、本地快照亦为历史各期 ⇒ 逐期同键对齐才是「同源」；`publ_date`/`end_date` **不替换**（PIT 可见性锚保持面板原样）⇒ 行结构、期数、PIT 语义**全部不变**，只有 eps 数值对齐。
- **保留**：`_qs_eps_pick` 退居辅助（未来若需「按 D 取最近一期」语义再用），主路径改走**同键对齐**。

### 14.3 段二实现清单（下一轮一次性完成）

| # | 动作 | 落点 | 判据 |
|---|---|---|---|
| 1 | 新增 `_qs_eps_align_rows(rows_by_code, table)` | `_QS_QMT_HISTORY_EXT`（EPS 层内） | 按 `(code,end_d8)` 同键覆盖 eps；返回 `(rows, stats)`；`stats={'overridden','mismatch','span_short','unmatched'}` |
| 2 | 接线：`table=='eps'` 时在行收集后调用 | `_QS_QMT_FUNDAMENTALS_EXT`（L1886 全历史行收集 与/或 L1707 归一之后） | 面板原值保留为对照；`eps` 被本地值覆盖 |
| 3 | fail-loud 双式 | 同上（stats 打印） | `QS_QMT_EPS_MISMATCH n=<数> sample=<code>@<end_d8>:panel=<v>,local=<v>`（面板≠本地，打印前 3 例）；`QS_QMT_EPS_SPAN_SHORT n=<数> sample=<code>@<end_d8>`（本地缺该期，保留面板值） |
| 4 | `convert(…, eps_embed=…)` 注入参数 | `convert()` 签名与 QQF 注入同构（L3373±，随 `qfq_embed` 一并处理） | 明文/ZB64 双形态；缺省零漂移 |
| 5 | 重转一产物验三态 | 产物 | 产物首段出现 `QS_QMT_EPS_SNAP embedded n=<…> base=<…> mode=<plain|zlib>`；absent/broken 亦打对应行 |
| 6 | 矩阵再追认 | `fundamentals-contract-matrix.{md,yaml}` | `qmt_wrapper_hash` 再变；`wrapper_hash` 不变（分立） |
| 7 | 回归 | QMT 面 17 件 + EPS 导出器 9 件 | 全绿；策略源码零改动 |

### 14.4 读者须知（分片实施的边界）

- **段一**（已成笔 `67d6a82`）：快照层三函数落地，**未接线、不生效**；
- **段二上**（本轮）：落点定谳 ＋ 对齐策略修正（**设计层面纠错**，避免带着错误设计编码）；
- **段二下**（下一轮）：按 §14.3 清单一次性完成 1–7，**不做半成品接线**。
- 边界申报：**EPS 覆盖当前完全未生效**，不得据此宣称舱二对齐。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制·北京午夜锚·对齐策略修正）


### 13.3-补2 正式件形态切换（2026-11-10 · R363 裁定落地）

**裁定**：正式件改用**压缩形态**（zlib+base64），明文件降级为对照件。

**执行**（窗内·生产库·14:5x/15:5x 两次窗均 READABLE）：

| 路径 | 形态 | 体积 | sha16 | 说明 |
|---|---|---|---|---|
| `output/qmt_snapshot/eps_snap_embed.py` | **zlib+base64（正式）** | **477,486 B** | **`cd400bd092e2ef66`** | 生产库导出·`--compress` |
| `output/qmt_snapshot/eps_snap_embed_z.py` | zlib+base64（副本库实测件） | 477,486 B | `cd400bd092e2ef66` | **与正式件逐位同 ⇒ 两库 EPS 数据一致（第二次实证）** |
| `output/qmt_snapshot/eps_snapshot.json` | 明文 JSON（数据面） | 2,366,946 B | `97ca32401a93061d` | 保留（不可压缩替代，供核对用） |

**⚠️ 如实申报**：原明文 emit-py（2,393,132 B／sha16 `ce17c9fa198e03bb`）因**输出路径同名被直接覆盖**，**未保留成独立对照件**（`output/` 为 gitignored，无版本备份）。影响：**无功能影响**——缺省（不带 `--compress`）随时可重导同内容明文件；但「降级为对照件」的**存档意图未达成**，如需长期对照留档，应重导一份改名件（建议 `eps_snap_embed_plain.py`，下一轮随段二第 4+5 项一并补）。

**单产物体积账**：本体 1,042,486 B ＋ 正式件 477,486 B ＝ **1.45 MB < 1.5 MB 上限** ✅（较 v1.7 的 1.04 MB 仅 +0.39 MB）。

**消费端口径不变**：`_qs_eps_snap_state()` 双形态自适应（先看 `_QS_QMT_EPS_SNAP_DATA`，缺则解 `_QS_QMT_EPS_SNAP_ZB64`）⇒ 压缩件无需改消费端代码。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制·北京午夜锚·压缩形态实测·正式件切换）

**补记（2026-11-10 16:4x · R364）**：明文对照档已补——`eps_snap_embed_plain.py` = **2,393,132 B／sha16 `ce17c9fa198e03bb`**，**与此前被同名覆盖丢失的明文件 sha16 逐位相同** ⇒ **无信息损失**，存档承诺完整兑现（重导字节一致＝可复现性铁律的又一次实证）。
当前 `output/qmt_snapshot/` 四件：`eps_snap_embed.py`（压缩正式件 477,486 B／`cd400bd092e2ef66`）／`eps_snap_embed_plain.py`（明文对照 2,393,132 B／`ce17c9fa198e03bb`）／`eps_snap_embed_z.py`（副本库实测 477,486 B／`cd400bd092e2ef66`）／`eps_snapshot.json`（数据面明文 2,366,946 B／`97ca32401a93061d`）。


---

## 十五、段二第 4+5 项 · 下轮编辑蓝图（2026-11-10 · 只读备料）

> 落点行号基于当前 HEAD 的 `source_import_qmt.py`（**3,593 行**）；**下轮动手前须重新核对**（本文件已多次行漂移，铁律：改前实读）。

### 15.1 精确落点（现状）

| 落点 | 当前行 | 现状文本（节选） |
|---|---|---|
| `_inject_qfq_embed` | **L3344–3362** | `def _inject_qfq_embed(self, qfq_embed: str) -> str:` … `return _QS_QMT_HISTORY_EXT.replace(_QS_QMT_QFQ_EMBED_MARK, embed_src)` |
| `_assemble` 签名 | **L3364–3365** | `def _assemble(self, body_src: str, *, pep263_replaced: bool, diag: bool = False,` / `qfq_embed: str | None = None) -> str:` |
| 注入链 | **L3380–3382** | `history_ext = _QS_QMT_HISTORY_EXT` / `if qfq_embed is not None:` / `history_ext = self._inject_qfq_embed(qfq_embed)` |
| `convert` 签名 | **L3445** | `def convert(self, diag: bool = False, qfq_embed: str | None = None) -> QmtSourceResult:` |
| `convert` 调 `_assemble` | **L3469** | `qfq_embed=qfq_embed)` |

### 15.2 编辑 1：新增 `_inject_eps_embed`（插在 L3363 之后、`_assemble` 之前）

```python
    def _inject_eps_embed(self, eps_embed: str, base_src: str) -> str:
        """EPS 嵌入式快照常量注入（QD-2026-0023-E1 段二，2026-11-10）。

        与 _inject_qfq_embed 同范式：读 emit-py **文本原样**（newline='' 防 CRLF
        翻译；encoding='ascii' 严格——emit 契约纯 ASCII，混入非 ASCII 即 fail-closed），
        替换 HISTORY_EXT 内 EPS 层标记行（字面量 '# @@QS_QMT_EPS_SNAP_EMBED@@'）。
        base_src 为**已注入 QFQ 的 history_ext**（EPS 层与 QFQ 层同在 HISTORY_EXT，
        故在 QFQ 注入结果上继续替换；不传则从原模板起）。
        读取失败 / 标记缺失（模板漂移）-> RuntimeError（经 convert 改写链失败通道入
        errors，不静默产出无嵌入产物——标记被吞则产物恒 absent 态，正是本防线靶点）。
        """
        try:
            with open(eps_embed, "r", encoding="ascii", newline="") as f:
                embed_src = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("eps_embed 读取失败 %r: %s" % (eps_embed, exc))
        mark = _QS_QMT_EPS_EMBED_MARK
        if mark not in base_src:
            raise RuntimeError("EPS 嵌入标记缺失（HISTORY_EXT 模板漂移）：%r" % (mark,))
        return base_src.replace(mark, embed_src)
```

并在模块级常量区（`_QS_QMT_QFQ_EMBED_MARK` 同处）新增：

```python
_QS_QMT_EPS_EMBED_MARK = '# @@QS_QMT_EPS_SNAP_EMBED@@'
```

### 15.3 编辑 2：`_assemble` 签名 + 注入链

签名加参（L3364-3365）：

```python
    def _assemble(self, body_src: str, *, pep263_replaced: bool, diag: bool = False,
                  qfq_embed: str | None = None,
                  eps_embed: str | None = None) -> str:
```

注入链改为（L3380-3382 三行）：

```python
        history_ext = _QS_QMT_HISTORY_EXT
        if qfq_embed is not None:
            history_ext = self._inject_qfq_embed(qfq_embed)
        if eps_embed is not None:
            history_ext = self._inject_eps_embed(eps_embed, history_ext)
```

### 15.4 编辑 3：`convert` 签名 + 传参

签名（L3445）：

```python
    def convert(self, diag: bool = False, qfq_embed: str | None = None,
                eps_embed: str | None = None) -> QmtSourceResult:
```

传参（L3469 区域）：

```python
                   qfq_embed=qfq_embed,
                   eps_embed=eps_embed)
```

### 15.5 验证判据（下轮一次性执行）

```text
① py_compile rc=0
② 重转一产物（CANSLIM，eps_embed=output/qmt_snapshot/eps_snap_embed.py 压缩正式件）
③ 产物首段出现：QS_QMT_EPS_SNAP embedded n=5206 base=20260801 mode=zlib
④ 产物内 _QS_QMT_EPS_SNAP_ZB64 在位、ASCII 编码通过（portability PASS）
⑤ 矩阵 --check --reverify：qmt_wrapper_hash 再变（同 commit 追认）；wrapper_hash 不变
⑥ 回归：QMT 面 17 件 + EPS 导出器 9 件全绿
⑦ 不传 eps_embed 时产物与既版 byte-identical（缺省零漂移）
```

### 15.6 风险与回退

- 风险①：**行号漂移**（本文件已被他线/我方多次改动）⇒ 动手前**必须重新定位**，禁按本文行号盲改；
- 风险②：EPS 标记 `# @@QS_QMT_EPS_SNAP_EMBED@@` 在 `_QS_QMT_HISTORY_EXT` 模板串内（段一已验在位）；
- 风险③：压缩件 477,486 B 注入后产物约 **1.45 MB**（v1.7 1.04 MB 已验证可加载，1.45 MB 需真机复验）⇒ 若真机受阻，回落明文对照件（会破 1.5 MB 线，故仅作诊断用）；
- 回退：`git checkout -- quantstudio/strategy_compiler/source_import_qmt.py`（该文件当前与 HEAD 一致，无他线叠加）。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制·北京午夜锚·压缩形态·编辑蓝图）
