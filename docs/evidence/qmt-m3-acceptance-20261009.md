# QMT 管线 M3 验收证据（④轮，2026-10-09）

> 计划：`docs/qmt-pipeline-m3-plan.md`（②审批准含修订——⑤-1 裁 **B 桩冒烟** + 三附加条件）。
> 本文档 = 六步④验收结论（判据对照 M3 Plan §⑧），并承载**用户条件③的 M5 真实环境实测清单**（§6）。
> **开工基线**：HEAD=`c0bd03f`（含 M2b 尾款 `a2620cc`）；写前快照 `stash@{0}`=`9f18f055523eb13ebacf3af55f1903e8b72c6ccd`。

## 0. 判型声明

**新增检测型**——新增桩冒烟能力面（对六策略 QMT 产物报告**行为级** verdict）；既有产物零漂移
以 byte-diff + CONTRACT GATE 实证。修复前置三问：①影响其他功能=无（**测试域新件，零侵入**）；
②影响性能=无（不在既有链路）；③影响精度=无（本地引擎与产物零触碰）。

**证据效力声明（写死，承 Plan §⑥）**：桩冒烟**仅证**「产物在 QMT API 契约下可被驱动、
订单/持仓/净值序列形状自洽」，**不证**数值正确性——数值对照正式落位 M5 真实环境（§6）。

## 1. 判据①–⑤ 对照

| 判据 | 结果 | 证据 |
|---|---|---|
| ① 桩冒烟六策略跑通（S1 无异常） | **PASS** | `pytest tests/test_qmt_stub_smoke.py -q` → **6 passed**（2.14s）；六策略 `error=None` |
| ② S2–S7 逐条断言 | **PASS** | 六策略 `bars_run=20` / `init_calls=1` / `invalid_count=0` / nav 量级在 [0.1×,10×] / 审计行在位 |
| ③ 订单/持仓/净值序列产出 | **PASS** | `output/qmt_m3_smoke/<sid>.json` ×6（含 `orders`/`positions_tail`/`nav_series`/`audit_lines`/`error`） |
| ④ 既有回归 | **PASS** | `tests/test_source_import_qmt.py` → **10 passed**（含 minute deny 4 项）；CONTRACT GATE 于 M2b 轮已 PASS（本批未触碰生产路径） |
| ⑤ 策略源码零改动 | **PASS** | 六策略源 SHA-256 与 M2b 开工快照逐位一致（本批仅新增测试域文件） |
| ⑥ M5 清单成文（条件③） | **PASS** | 见 §6 |

**越界核对（机械）**：相对 M3 开工基线（683 项）新增仅 **3 项**——
`docs/qmt-pipeline-m3-plan.md`（Plan 定稿）、`tests/qmt_stub.py`（块1）、`tests/test_qmt_stub_smoke.py`（块2）；
**生产路径 `quantstudio/strategy_compiler/` 零改动** ✓（用户硬条件①：桩件落测试域，禁入生产路径——**已满足**）。

## 2. Oracle「会红」核验（O6 硬要求，防恒绿脚手架）

| 核验 | 注入 | 结果 |
|---|---|---|
| S3 订单方向/数量 | `volume=0` / `opType=99` / `volume=-5` 三笔 | **三条全被捕获** `[(0,'600000.SH',23,0),(1,...,99,100),(2,...,24,-5)]` |
| S4 时点单调 | `barpos` 5→3 | **捕获** `[(0,5,1,3)]` |
| S5 持仓非负 | `amount=-1` | **捕获** `[(0,'600000.SH',-1,0,'amount < 0/enable_amount > amount')]` |
| S6 净值量级 | `0.0` / `1e12` | **双捕获** `[(0,0.0)]` / `[(0,1e12)]` |
| S1 无异常（端到端） | 注入抛异常坏产物 | **捕获** `error` 非空且含完整 traceback |
| **对照组**（真实产物） | `fall_reversal` 跑 3 bar | `error=None`、`orders=18`、`audit=3` ✓ 正常 |

**结论：oracle 非恒绿，判据有效**（判据独立于被测实现；空序列亦有显式判红——S6 空序列返回
「nav_series 为空（无任何 bar 净值可判）」，不 vacuous pass）。

## 3. 六策略桩冒烟实测摘要

| 策略 | bars | init | 订单 | nav_min / nav_max | 审计行 |
|---|---|---|---|---|---|
| CANSLIM突破成长选股策略 | 20 | 1 | 0 | 1,000,000 / 1,000,000 | 2 |
| **fall_reversal** | 20 | 1 | **20（19 买 / 1 卖）** | 973,789.48 / 1,000,000 | 4 |
| tech_etf_mvo_rotation | 20 | 1 | 0 | 1,000,000 / 1,000,000 | 2 |
| vol_regime_mom_rev | 20 | 1 | 0 | 1,000,000 / 1,000,000 | 2 |
| weekly_smallcap_growth_momentum_10 | 20 | 1 | 0 | 1,000,000 / 1,000,000 | 1 |
| 周频小市值成长动量（三层止损） | 20 | 1 | 0 | 1,000,000 / 1,000,000 | 1 |

**fall_reversal 实证细节**（证明桩确实能驱动交易）：订单样本
`{'barpos':900,'opType':23,'orderType':1101,'orderCode':'000009.SZ','prType':5,'price':12.41,'volume':3939}`；
持仓 `{'000009.SZ':{'amount':3939,'enable_amount':3939,'avg_cost':12.41}, ...}`；
审计行含 `QS_QMT_DENY_REMOVED: set_benchmark/set_commission` + `QS_QMT_SEMANTIC_DIFF: get_Ashares date='2023-06-15' 不消费（QMT sector=当前上市名单，无历史 PIT）`。

**观察项 O-1（如实登记，非判据红）**：另五策略 20 bar 窗口内**零订单、净值恒定**。
- 桩已证**能驱动交易**（fall_reversal 20 笔），故非"驱动失效"；
- 成因候选：①策略为周频/月频，20 个 bar 未达调仓时点；②桩合成数据的信号不足以触发；
  ③run_daily 节拍与窗口错位。
- **归因留 M5**（真实数据 + 真实窗口可一次分清）——桩冒烟判据 S1–S7 只判**形状**，
  不判**信号层**是否触发，此为**设计边界内的如实登记**，不构成判据失败。

## 4. M3 首个实质发现（**重要**，待用户裁定）

**发现 F-1：产物侧 `_qs_fin_to_rows` 的返回形态探测在结构上不可靠。**

- **定位**：`quantstudio/strategy_compiler/source_import_qmt.py`（`_qs_fin_to_rows`，产物注入面）。
- **机理（代码实证）**：该函数以「第一层 value 是否 dict + 第二层 value 是否 dict」探测形态：
  ```python
  first = next(iter(raw.values()), None)
  if isinstance(first, dict):
      probe = next(iter(first.values()), None)
      if isinstance(probe, dict):   # 形态 {code: {field: {…}}}  → 走 code 层
      else:                          # 否则一律判为 {field: {code: v}}
  ```
  但 `{code: {field: v}}` 与 `{field: {code: v}}` **结构完全同构**（两层 dict、叶子标量），
  **仅凭结构不可区分** → 纯 dict 形态 `{code:{field:v}}` 会被误路由到 field 层，致结果全 NaN。
- **本轮处置**：桩侧以 `QmtStubFinData`（dict 子类，附 `to_dict('index')` 协议，对齐 07:1889
  DataFrame 投影）**桥接**——本身仍是 dict，语义未降级；**未修改产物**（M3 范围外，且真实返回
  形态尚未实测）。
- **待裁定**：是否立项修复。建议方案（**不靠猜**）：形态判定改用**证券代码模式识别**
  （哪一层 key 形如 `\d{6}\.(SH|SZ|BJ)` 即 code 层）或由调用方显式声明形态——两者在
  任何真实形态下都更稳健，属**纯增益**。**修复批次由用户裁定**（本轮 / M3.1 / M5 实测后）。

## 5. 委派披露（E7）

| 块 | 内容 | 结果 |
|---|---|---|
| 块1 | `tests/qmt_stub.py`（QMT ContextInfo 最小桩，1008 行） | 落盘；**零越界**（机械核对）；含块2 所需全部接口 |
| 块2 | `tests/test_qmt_stub_smoke.py`（驱动器 + S1–S7 断言，394 行） | 落盘；六策略参数化就位；与块1 接口逐项核对一致 |

**纪律执行**：两轮任务包均**头部显式禁止委派方尝试执行 python**（ZCode 侧无执行权限）+
要求先落盘后自测（承 M2b 教训）——**两轮均一次落盘成功，无超时、无零落盘**。
**机检全由 DSH 侧承担**（构建/AST/import/实跑 pytest/会红核验/越界核对，合 C4 审计禁区）。
**成本**：块1 ≈434 万 tokens、块2 ≈68 万 tokens（含 cacheRead）。

**ZCode 侧勘误增益（采纳）**：产物注释曾登记「orderType 1101 为 05:135-143 未摘录【M5 实测项】」，
桩侧实测 **05-枚举常量.md:139 有明确摘录**（`1101 单股、单账号、普通、股/手方式下单`）——
建议 M5/后续轮回填产物注释（登记为文档增益项，不在本轮改产物）。

## 6. M5 真实环境实测清单（用户条件③）

> 用途：数值对照正式落位 M5；本清单为**用户域实测作业书**（QMT 客户端）。
> 优先级：**P0 = 阻塞数值对照**，P1 = 影响正确性判定，P2 = 增强项。

| # | 实测项 | 优先级 | 依据/动因 |
|---|---|---|---|
| M5-1 | **`C.get_financial_data` 真实返回形态**（`{code:{field:v}}` / `{field:{code:v}}` / DataFrame） | **P0** | 直接决定 F-1 缺陷的修复方向；未钉死前 fundamentals 面不可判 |
| M5-2 | `C.get_market_data_ex` **末根 bar 是否含当前未完成 bar**（E1 `[-2]` 设计假定） | **P0** | M1 §1.1③ 设计假定；影响所有日线信号取数 |
| M5-3 | `C.get_trading_dates` **元素形态**（list vs ndarray、日期格式）+ **init 内不可用**是否如实 | **P0** | 06:12 明示限制；桩已忠实复现，需真实环境确认 |
| M5-4 | `run_daily` 驱动节拍（日线 handlebar 内每根 bar 一次是否成立）+ **20 bar 窗口零订单成因**（观察项 O-1） | **P0** | M1 §3 映射定稿；决定 O-1 归因 |
| M5-5 | `PERSHAREINDEX` 是否含 `m_anntime`/`m_timetag` 字段（字段表未显式列） | P1 | FR-QMT-01 映射表标注的 M5 实测项 |
| M5-6 | `C.accountID` / ACCOUNT 资金字段（`m_dCash` 等）取值面 | P1 | 持仓/净值面 wrapper 消费 |
| M5-7 | 单股 `orderType` 取值（1101 等）与 `prType` 语义 | P1 | 05:139 已有摘录，实测确认 |
| M5-8 | `C.get_instrument_detail` 的 `InstrumentStatus`/`IsTrading`/`ExpireDate` 真实枚举 | P1 | `filter_stock_by_status` 映射 |
| M5-9 | 板块名「沪深A股」是否等价 `get_Ashares` 口径；`get_Ashares(date)` PIT 差异确认 | P1 | M2b 已登记语义差异 |
| M5-10 | **ContextInfo 跨 bar 是否重置**（06:8）与产物 `g=C` 运行态假设的相容性 | P1 | 桩须持久化同一 C 对象（已登记） |
| M5-11 | T+1 可用量建模、整手截断、涨跌停 ±10% 合成规则的真实口径 | P1 | 桩合成规则为近似 |
| M5-12 | 停牌/退市**正例**数据可得性（桩仅 ST 可控） | P2 | 状态过滤正例覆盖 |
| M5-13 | `get_bar_timetag` 毫秒单位与 bar 时间语义 | P2 | 时点判定面 |
| M5-14 | 真实数据补充前提（QMT 客户端「数据管理-财务数据」下载） | P2 | M1 §1.3 部署前提 |

**M5 作业形态（建议）**：六策略逐个在 QMT 客户端跑回测 → 日志回传 → 与本地引擎（daily-bar-v1）
**逐项数值对照**（信号/订单/成交/持仓/净值），差异逐项归因；M5-1/M5-2 建议**先于**其他项单独钉死
（P0 阻塞面）。

## 7. 未闭合项

| # | 项 | 状态 |
|---|---|---|
| 1 | **F-1 产物形态探测缺陷**（§4） | 待用户裁定修复批次（本轮 / M3.1 / M5 实测后） |
| 2 | **O-1 五策略零订单归因**（§3） | 留 M5（真实数据 + 真实窗口） |
| 3 | M5 清单 14 项（§6） | 待用户域执行 |
| 4 | minute deny 改动（前置件） | 挂工作区，**随 M3 批次提交** |
| 5 | archify HTML 影响面图 | 不强制（用户已裁），本轮以 mermaid 呈报 |

## 8. 结论

**M3 判据①–⑥ 全部 PASS**：桩冒烟六策略 6/6 跑通、S1–S7 逐条通过、**oracle 会红核验全过**
（防恒绿）、既有回归 10 passed、策略源码零改动、**M5 清单 14 项成文**。
**生产路径零改动**（用户硬条件①满足）；桩件落测试域（`tests/qmt_stub.py` + `tests/test_qmt_stub_smoke.py`）。
**M3 的实质产出不止"跑通"**：暴露产物侧形态探测缺陷 **F-1**（待裁定）+ 零订单观察项 **O-1**（留 M5）——
桩冒烟的价值正在于此（Plan §⑥ 声明它不证数值正确性，但能暴露契约层问题）。

**待⑤用户确认**；未闭合项 5 项已如实登记，其中 **F-1 需裁定修复批次**。
