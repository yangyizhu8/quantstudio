# D-29 呈裁件 · QMT `profit_ability` 契约缺口处置（2026-10-16 组装，出件即裁）

- **性质**：候裁件（用户裁定是否立项＋选路径）；**无前置**（不依赖其他在途批次）
- **登记**：`docs/ISSUES.md` D-29 节（2026-10-13 零越权登记）｜registry 承接行 **QCAL-04**（已知分歧）
- **基线**：HEAD `0b9cfca`（2026-10-16 16:1x）

---

## 一、问题定义（实证）

| 项 | 事实 |
| --- | --- |
| **契约分叉** | 本地 `ptrade_api` **支持** `profit_ability` 表（D-19 契约段：`eps`/`growth_ability`/`profit_ability` 走 PIT 全报告期多行）；QMT wrapper 的 `_QS_QMT_FUND_FIELD_MAP` **只含** `eps`/`growth_ability`/`valuation` |
| **后果** | `_qs_get_fundamentals` 对未映射表**早退返回全 NaN 空帧**（`index=codes`、`columns=fields`）→ 消费该表的策略在 QMT 端**静默退化**（无异常、无告警） |
| **自相矛盾** | hist 分支（`table in ('eps','growth_ability','profit_ability')`）把 `profit_ability` 列为**支持表**，与映射表缺失矛盾 ⇒ 该分支对 `profit_ability` 是**死代码** |
| **消费面（4 点）** | `SG-MS-PEG:358`（`fields=['roe','end_date']`）／`F-Score:329`（`fields=['roe']`）／`低流动性:271`／`roe_probe_batch:29`（诊断）——均**不在**六策略门禁集内 ⇒ 不阻断既有推送，但**对齐缺口真实存在** |
| **与 D-28 的关系** | 折叠实现**表集无感知**（流经 `_qs_fin_to_rows_hist` 的表一律折叠）⇒ D-28 对 `eps`/`growth_ability`（实测有数据）**已成立**，**不等待 D-29**；但 D-29 决定折叠**对 `profit_ability` 有无意义** |

## 一·补 文档查证结果（2026-10-16 本轮已完成，**先查文档再动手**铁律）

**依据 Context7 `/websites/dict_thinktrader_net`**：

- `innerApi/data_function.html` ＋ `VBA/system_function.html`：`get_financial_data` 支持的**表名**＝
  `CAPITALSTRUCTURE`（股本结构）／`PERSHAREINDEX`（**关键指标**）／`ASHAREINCOME`（利润表）／
  `ASHARECASHFLOW`（现金流量表）／`ASHAREBALANCESHEET`（资产负债表）——**其中没有 `profit_ability`**；
- `dictionary/stock.html`：`report_type` 可选 `report_time`（报告期）／`announce_time`（公告日期）。

**结论（决定性）**：

1. QMT 端**不存在**名为 `profit_ability` 的原生财务表 ⇒ 本地契约的 `profit_ability`（盈利能力）
   **必须映射到等价原生表**，不能同名直取；
2. **候选原生表＝`PERSHAREINDEX`（关键指标）**——理由：wrapper 现行映射中 `eps` 与 `growth_ability`
   **已**映射到 `PERSHAREINDEX`（`s_fa_eps_basic`／`inc_revenue_rate`），本地「盈利/成长/每股」三类
   关键指标在 QMT 侧同表，命名惯例为 `s_fa_*` 系列；
3. **候选字段（待探针确证）**：本地契约字段 `roe` → 候选原生字段 **`s_fa_roe`**（惯例推断，
   **须 S2 探针实测确证，禁凭猜**）；`publ_date`/`end_date` → `m_anntime`/`m_timetag`（**M5 已实测项**，可直接复用）；
4. 若 S2 探针证明 `PERSHAREINDEX` 无 ROE 类字段 ⇒ 备选＝由 `ASHAREINCOME`／`ASHAREBALANCESHEET` **合成**
   （成本高、且需证与本地口径一致）⇒ 此时倾向裁②（登记能力边界）。

**现行映射原文（依据 `quantstudio/strategy_compiler/source_import_qmt.py` 实测）**：

```python
'eps':            ('PERSHAREINDEX',   {'eps': 's_fa_eps_basic', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}),
'growth_ability': ('PERSHAREINDEX',   {'or_yoy': 'inc_revenue_rate', 'publ_date': 'm_anntime', 'end_date': 'm_timetag'}),
'valuation':      ('CAPITALSTRUCTURE', {'float_value': 'circulating_capital'}),
# profit_ability：缺失（D-29 本体）
```

## 二、判据 4 具体内容（D-28 设计 §五 原文）

> **判据 4（SG-MS-PEG 真实对照，条件项）**：若 D-29 已裁①（补映射）→ 键恒附（§三④）使 `SG-MS-PEG:358` 帧含折叠键 → **ROE 均值前后对照随端测产出**（修正方向＝回归本地口径，要件三「准确度只升不降」）；
> D-29 未裁/裁② → 本判据由 §五-12 **真实函数合成对照代偿**并在端测报告标注。

**现状**：D-29 未裁 ⇒ 判据 4 由 `tests/test_qmt_hist_fold.py::test_12`（真实函数＋合成帧）**代偿**，已在 D-28 ④验收记录与端测报告标注。

## 三、单测 12 代偿 vs 真实条件的覆盖差异（8 维）

| # | 维度 | 单测 12 覆盖 | 真实条件（判据 4） | 差异性质 |
| --- | --- | --- | --- | --- |
| 1 | **函数** | ✅ `importlib` 直载**真实** `_roe_mean_from_fin_indicator`（非镜像等价物） | 同 | **一致** |
| 2 | **数学** | ✅ 末 5 报告期均值 ＋「折叠前＝最新报告期」错误口径实证 | 同 | **一致** |
| 3 | **折叠语义** | ✅ 同一 helper、同一组合键 `keep='last'` | 同 | **一致** |
| 4 | **数据来源** | ❌ **合成帧**（数值/日期自造） | QMT 端 `profit_ability` 真实帧（NaN 行、ffill 形态、真实报告期分布与缺失模式） | **未覆盖** |
| 5 | **装配链** | ❌ 直接喂 helper；用例 10/11 覆盖投影/完整行过滤但**面向 `eps` 表** | 经 hist 分支 ＋ 完整行过滤 ＋ 投影（`profit_ability` 路径 **D-29 未修前不可达**） | **未覆盖** |
| 6 | **键恒附触发** | ◐ 手工把键列放进帧；字段集含 `profit_ability` 仅**静态断言** | 真实键恒附端到端 | **部分未覆盖** |
| 7 | **规模** | ❌ 6 报告期 × 1 码 | 3667 码 × ≈79 期 | **未覆盖** |
| 8 | **引擎链路** | ❌ 不经 QMT 引擎与策略调仓链 | 端到端（取数→调仓→成交→净值） | **未覆盖** |

**缺口结论（一句话）**：代偿覆盖**函数与数学正确性**（1–3 维一致），**未覆盖 `profit_ability` 在 QMT 端的真实数据形态与端到端链路**（4–8 维）——该缺口**只能由 D-29 修① 后的一次真实端测闭合**。

## 四、两条候选路径（含成本与代价）

| 路径 | 动作 | 收益 | 代价/风险 |
| --- | --- | --- | --- |
| **① 补映射**（建议） | 补 `_QS_QMT_FUND_FIELD_MAP['profit_ability']` ＝ **`('PERSHAREINDEX', {'roe': 's_fa_roe'(候选，待探针确证), 'publ_date': 'm_anntime', 'end_date': 'm_timetag'})`**（文档查证已完成，见 §一·补；**字段名须 S2 探针实测确证，禁凭猜**）；键恒附（表集含它）自动覆盖；判据 4 真实化 | **闭合对齐缺口**（4 消费点在 QMT 端恢复数据）＋判据 4 从代偿升真实 | 需 1 轮文档查证＋1 轮端测探针确证；**触及 `_QS_QMT_FUND_FIELD_MAP`（在 `_QMT_HASH_SCOPE` 内）⇒ 矩阵哈希同批追认**；若探针证明 QMT 端无该原生表 → 该路径**不成立**，回退裁② |
| **② 移除表集＋告警** | 从 hist 表集移除 `profit_ability` ＋ 显式告警行（消除"静默"） | 消除自相矛盾与静默退化（可观测） | **消费面永久退化**（4 消费点的 ROE 因子在 QMT 端恒空）→ 对齐缺口**不减反增**；**同样触及模板（`_QS_QMT_FUNDAMENTALS_EXT` 在 `_QMT_HASH_SCOPE` 内）⇒ 哈希追认**；应同时登记为**平台能力边界**（与 D-31 同类） |

> **两路径均需矩阵哈希同批追认**（`_QS_QMT_FUND_FIELD_MAP` 与 `_QS_QMT_FUNDAMENTALS_EXT` 均在 `_QMT_HASH_SCOPE` 20 常量清单内——已实测确认）。
> **路径判定顺序建议**：先做**文档查证＋端测探针**（低成本、决定性）→ 若 QMT 端存在该表原生形态 ⇒ 走①；若确证无 ⇒ 走② 并登记能力边界。**禁止在未确证前实施任一路径**（避免凭猜补映射＝制造新的静默错误）。

## 五、修① 后「一次真实端测」的闭环计划（判据 4 真实化）

| 步 | 动作 | 读数/判据 |
| --- | --- | --- |
| S1 | ~~文档查证~~ **已完成（2026-10-16）**：依据 Context7 `/websites/dict_thinktrader_net`——QMT 财务表集无 `profit_ability`；候选＝`PERSHAREINDEX`（见 §一·补） | ✅ 候选表名/字段名＋文档出处在卷 |
| S2 | **端测探针确证**（盘后同窗，~25 min）：QMT 端跑 `roe_probe_batch`（诊断策略，D-29 消费面之一）——**先以候选字段 `s_fa_roe` 试**，空则换候选名再试（≤3 次），全空则转裁② | `QS_QMT_HISTDBG3 t=profit_ability rows>0` ／ `QSFUNDHIST t=profit_ability kept>0`（**帧非空**＝字段名确证） |
| S3 | **补映射实施**（六步流水线，框架层）：`_QS_QMT_FUND_FIELD_MAP` 增 `profit_ability` 行＋hist 表集一致性核对 | 单测＋契约门＋六策略重转 ALL PASS；**矩阵哈希同批追认** |
| S4 | **判据 4 真实对照**（盘后端测，~25 min）：跑 `SG-MS-PEG`，读其 ROE 均值 | **折叠前＝最新报告期 ROE**（错误口径）／**折叠后＝末 5 报告期均值**（本地口径恢复）——方向须与单测 12 合成结果**同向** |
| S5 | **闭环条件**（全满足才算判据 4 关闭） | ①`profit_ability` 帧 `kept>0`（非空）②ROE 均值前后对照与本地口径一致（准确度只升不降）③六策略零回归 ④矩阵哈希同批追认 ⑤registry **QCAL-04** 由「已知分歧」→「已锚定」＋QD 条目（新增 `QD-2026-0007`） |

**端测成本**：S2＋S4 各一轮盘后端测（≈25 min/轮，可合并为一轮两读数）——按件 E 端测配额规则，**新数据面接入属「必端测」三边界之一**（照准口径）。

## 六、呈裁请求

请裁定：
1. **是否立项 D-29**（六步流水线，独立一轮）；
2. **路径**：建议「**先文档查证＋端测探针确证 → 按结果走①（建议）或②**」（禁止未确证即实施）；
3. 若裁②：是否同时登记为**平台能力边界**（与 D-31 同类，入 registry QCAL 面）。

> 裁定后即按六步①出方案（含 S1–S5 计划与哈希追认条款），本呈裁件作为方案附件。

## 七、②审意见（2026-10-16，随专轮呈用户裁定）

| # | ②审意见 | 状态 |
| --- | --- | --- |
| ① | **立项建议照准** | 待用户裁定 |
| ② | **路径建议＝先 S2 探针确证（≤3 候选字段名）→ 按结果走①（补映射；`PERSHAREINDEX`/`s_fa_roe` 候选有据：同表 `s_fa_*` 命名惯例 ＋ M5 两键 `m_anntime`/`m_timetag` 已实测）或②（移除＋告警）**；**禁未确证即实施** | ✅ 照准 |
| ③ | **若裁② ⇒ 登记平台能力边界（与 D-31 同类）** | 待用户裁定 |
| ④ | 两路径均触 `_QMT_HASH_SCOPE` ⇒ **矩阵哈希同批追认** | ✅ 照准 |
| ⑤ | **文档前置查证（Context7 三页）铁律满足** | ✅ 确认 |

**②审结论**：呈裁件**就绪**，随专轮呈用户裁定；**裁定前不实施任何路径**（S2 探针亦待裁定后执行）。

**裁定包构成（供用户一次决策）**：本呈裁件（§一 问题定义／§一·补 文档查证／§二 判据 4 原文／§三 8 维代偿差异／§四 两路径成本／§五 S1–S5 闭环计划／§六 呈裁请求）＋ 本节 ②审意见 ＋ **§八 S2 定谳结果**。

## 八、S2 探针定谳结果（v4 生产口径复刻版，2026-10-16 23:48 端测）

| 对照 | 字段 | valid_rows | total_rows | value_cols | sample |
|---|---|---|---|---|---|
| **POS** 正对照 | `PERSHAREINDEX.s_fa_eps_basic` | **9383** | 17486 | `['s_fa_eps_basic']` | **0.27** |
| **NEG** 负对照 | `PERSHAREINDEX.__probe_invalid__` | **0** | 17486 | `['__probe_invalid__']` | None |
| CAND1 | `PERSHAREINDEX.s_fa_roe` | **0** | 17486 | `['s_fa_roe']` | None |
| CAND2 | `PERSHAREINDEX.roe` | **0** | 17486 | `['roe']` | None |
| CAND3 | `PERSHAREINDEX.s_fa_roe_weighted` | **0** | 17486 | `['s_fa_roe_weighted']` | None |

`VERDICT=NO_ROE_FIELD: 三候选全 0 且对照成立`

### 8.1 确证事实（可引用）

1. **判据前提全部成立**＝探针可区分：正对照 `s_fa_eps_basic` 有 **9383** 有效行（值域合理 sample=0.27），
   负对照 `__probe_invalid__` **0** 有效行 ⇒ v4 判据链完整生效（v1–v3 三轮仪器迭代的收敛产物）。
2. **`PERSHAREINDEX`（QMT 关键指标表）中不存在 ROE 类字段**：`s_fa_roe`／`roe`／`s_fa_roe_weighted`
   三候选**全为全 NaN**（Panel 同形同维、列名正确回显、仅值缺失）
   ⇒ **路径①（`PERSHAREINDEX` 补映射）被否证**，无需再裁。
3. **探针形态全貌（v2＋v4 合并，候选回灌主库形态库）**：
   ①`get_financial_data` 返回 **`pandas.core.panel.Panel`**（D-9 形态轨 Panel 轨实测复现）；
   ②维度 `2 items[代码] × 8743 major_axis[日期 19901219..20261016] × 1 minor_axis[字段]`，
   子对象 `total_rows=17486`（＝2 码 × 8743 日）；③**无效字段亦返回同形同维 Panel**，
   形态/维度/标签**均不可区分**，**唯一判别信号是值**（有效=有值／无效=全 NaN）；
   ④取子与取值须走**生产口径**：`raw[_code]` → 按其自身 `columns` 取非键列（排除 `m_anntime`/`m_timetag`）。

### 8.2 未确证部分（诚实标注）

v4 **只否证了「`PERSHAREINDEX` 无 ROE 字段」**，**未**证明「QMT 端全无 ROE 数据」：

- `ASHAREINCOME`（利润表）／`ASHAREBALANCESHEET`（资产负债表）**未测**——理论上存在**合成路径**
  （净利润 ÷ 股东权益），但①本地 `profit_ability` 契约另含 `roa/roic/net_profit_margin/
  gross_profit_margin/operating_profit_margin/expense_to_revenue/operate_profit_to_profit` 等**十余字段**，
  ②合成口径与本地契约的**定义一致性未评估**（加权 vs 期末净资产、年化与否），③成本与回归风险均未量化。

### 8.2·补 v4 结论更正（2026-10-17，终裁裁②′后查证所得）

> **v4 的 `NO_ROE_FIELD` 结论作废**：v4 候选（`s_fa_roe`/`roe`/`s_fa_roe_weighted`）系**跨族猜名**。
> 依据 Context7 `/websites/dict_thinktrader_net`（`nativeApi/xtdata.html`），QMT `PERSHAREINDEX`
> **比率类字段无 `s_fa_` 前缀**：`du_return_on_equity`（净资产收益率）、`equity_roe`（加权 ROE）、
> `net_roe`（摊薄 ROE）、`total_roe`（摊薄 ROA）、`gross_profit`（毛利率）、`net_profit`（净利率）。
> **路径①未被否证**——v5 以文档明列名重测（`docs/probe/qmt_profit_ability_probe_v5-20261017.py`）。
> 终裁＝**裁②′（先探合成）**，2026-10-17。

## 九、六步①方案 v1.0 成稿（2026-10-17 01:1x，候总调度②审）

**方案件**：`docs/qmt-profit-ability-mapping-design.md`

| 项 | 内容 |
| --- | --- |
| 判型 | **数据修正型**（QMT 端 hist 行集回归本地契约口径；diff 只能归因补映射后非空帧；准确度只升不降） |
| 改动清单（允许写入清单） | ①`source_import_qmt.py::_QS_QMT_FUND_FIELD_MAP` 增 `profit_ability` 行（`roe`→`du_return_on_equity` ＋ `net_profit_margin`→`net_profit`，**两映射行独立登记**）；②契约矩阵 yaml/md `qmt.*` section 增契约行；③`tests/test_qmt_fund_field_map.py`（新建）；④`docs/probe/` v1–v5 保留为仪器迭代证据链；⑤`knowledge/qmt/QD-2026-0007.md`（新建） |
| 明确不在范围 | `roa`/`roic`/`gross_profit_margin`/…其余十项字段；`total_roe`/`gross_profit`（v5 已证 0 命中） |
| 哈希纪律 | 触 `_QMT_HASH_SCOPE` ⇒ `check_fund_matrix --check --reverify` 产物（yaml `meta.qmt_wrapper_hash`＋md）**同 commit 追认** |
| 验收七判据 | ①矩阵哈希同批追认 ②六策略零回归（CONTRACT GATE）③消费点可达性（`roe_probe_batch` 读数 `rows>0`/`kept>0`）④口径 A/B 对照三分支 ⑤阈值档对照（或记不适用＋理由）⑥契约矩阵登记 ⑦纯增益（eps/growth/valuation 三映射零变化） |
| 回退四分支 | 判据③/⑦失败 ⇒ 单 commit revert＋哈希随退；判据④判「无值」⇒ 回退并转裁②或合成（独立立项）；判「单位差/口径差」不可定案 ⇒ 不扩范围、登记 ISSUE 呈裁 |
| 知识反哺留痕 | 3 条命中（审计方法论-先证可达性／ROE因子／行情因子适配+工作流）＋ 1 条无命中（QMT PERSHAREINDEX 命名族 ⇒ **本轮新增形态知识须回灌**） |

**六步推进**（用户令）：方案落盘 ✅ → **总调度②审** → 实施（哈希同批追认）→ 验收（A/B＋六策略＋CONTRACT GATE）→ ⑤用户确认 → 双推＋同步门。

### 8.3 终裁请求（路径①已被否证，剩二择一）

| 选项 | 内容 | 收益 | 代价 |
|---|---|---|---|
| **裁②（推荐）** | 移除 hist 表集中的 `profit_ability` ＋ wrapper 显式告警行 ＋ **登记平台能力边界（与 D-31 同类）** | 消除「静默退化」与死代码矛盾；缺口**显性化** | 4 消费点在 QMT 端**永久无 ROE 数据**（对齐缺口保留但可见）；触 `_QS_QMT_FUNDAMENTALS_EXT` ⇒ 矩阵哈希同批追认 |
| **裁②′（先探合成）** | 再一轮探针：`ASHAREINCOME` 净利润 ÷ `ASHAREBALANCESHEET` 股东权益，确证口径可对齐后再走合成 | 可能恢复 QMT 端 ROE 数据 | ＋1 轮端测；本地 ROE **定义未确证**（加权/期末/年化）；十余契约字段的合成口径工作量与回归风险未量化 |

**建议裁②**（先让缺口可见、可度量、可台账跟踪；合成留作独立议题按需立项）。


