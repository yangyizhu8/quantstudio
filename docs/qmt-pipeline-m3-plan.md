# QMT 管线 M3 · Plan-Mode 计划定稿（2026-10-09，②审批准）

> **批准信息**：M3 Plan 经②审**批准（含修订）**——⑤-1 裁定 **B（桩冒烟）**，附加三条件：
> ① 桩件落**测试域**（`tests/` 下，**禁入 `strategy_compiler/` 生产路径**）；
> ② 形状判据具体化入本 Plan 定稿（§4 checklist）；
> ③ M3 产出须含 **M5 真实环境实测清单**（用户域实测清单化，数值对照正式落位 M5）。
> **前置件**：minute deny 负例补实现已依令完成（10 passed / 六策略零误杀 / 产物回归 6/6），
> 挂工作区随 M3 批次提交。
> **开工基线**：HEAD=`c0bd03f`（含 M2b 尾款 `a2620cc`）；写前快照 `stash@{0}`=`9f18f055523eb13ebacf3af55f1903e8b72c6ccd`；
> 基线落盘 `docs/handoff/baseline_m3_20261009-085748.txt`（683 项）。**他线在途勿卷入**。

## ① 阶段

M2b 六步全闭（①–⑥，尾款 `a2620cc` 已随批上远程）；M3 立项 → ②审批准（含三条件）→ **本轮=③实施** →
④验收 → ⑤确认 → ⑥随批。六步流水线全程。

## ② 本轮唯一核心目标

以 oracle **L1 桩冒烟**（方案 B）把六策略横验证从静态产物级升级为**行为级**：
桩驱动 QMT 产物跑通、产出订单/持仓/净值序列，并按**形状判据 checklist** 判定；
**不做数值对照**（桩自模拟撮合/复权/停牌 ⇒ 差异无法归因「桩失真 vs 策略缺陷」，M1 §3.5 原文「不强求」），
数值对照正式落位 **M5 真实环境**，本轮须产出 M5 实测清单。

## ③ 严格角色分工

| 角色 | 承担方 | 产出 |
|---|---|---|
| 主导 / 验收判定 / 台账 | 本会话（PDO 载体） | 本 Plan、④证据文档、M5 清单 |
| ②审 | ZCode | 已批准（含三条件） |
| 编码实施 | `zcode_code` 委派（E7） | 桩件 + 驱动器 + 形状断言测试 |
| 机检审计（构建/静态/测试/diff 核对） | 本会话（**C4 审计禁区**） | 五判据实测输出 |
| 终审 | 用户 | ⑤确认 |

**委派纪律（承 M2b 教训）**：拆 ~500 行块；任务包**头部显式禁止委派方尝试执行 python**（ZCode 侧无执行权限）；
要求**先落盘后自测**；自测由 DSH 侧承担。

## ④ 事项清单（含允许写入清单）

| # | 事项 | 落点 | 说明 |
|---|---|---|---|
| 1 | `tests/qmt_stub.py`（新建） | **测试域** | ContextInfo 最小桩 + 合成历史数据 + passorder/get_trade_detail_data 记录面 |
| 2 | `tests/test_qmt_stub_smoke.py`（新建） | **测试域** | 六策略桩冒烟驱动器 + 形状判据 checklist 断言 |
| 3 | `docs/evidence/qmt-m3-acceptance-20261009.md`（新建） | docs/evidence | ④验收证据 + **M5 真实环境实测清单**（条件③） |
| 4 | `docs/qmt-pipeline-baseline.md`（改） | docs | M 系列台账刷新（M3 状态） |
| 5 | `quantstudio/strategy_compiler/source_import_qmt.py`（改·**已改待提交**） | 生产路径 | minute deny（前置件，随本批提交） |
| 6 | `tests/test_source_import_qmt.py`（改·**已改待提交**） | 测试域 | minute deny 4 测试（随本批提交） |

**禁止触碰**：`quantstudio/strategy_compiler/` 下**除 `source_import_qmt.py` 已改部分外**任何文件
（尤其 `source_import.py` / `orchestrator.py` / `portability_rules.py` / `cli.py`——M2b 已定稿）；
六策略源文件；`ptrade_api.py` / `backtest_engine.py`。

**Out of scope**：数值对照（落 M5）；真实数据源接入；QMT GUI 集成；ETF 动态池固化 QMT 化；分钟域。

## ⑤ 桩件规格（供委派）

**落点**：`tests/qmt_stub.py`（测试域，**禁入生产路径**——用户条件①）。

**必须模拟的 QMT API 面**（六策略产物实测消费面，逐策略一致）：

| API | 实测调用数/策略 | 桩职责 |
|---|---|---|
| `C.get_market_data_ex(fields, codes, period, count, dividend_type, ...)` | 4 | 返回 `{code: DataFrame}`（合成确定性序列；支持 `count`/`start_time`/`end_time` 形态） |
| `C.get_financial_data(fieldList, stockList, startDate, endDate, report_type)` | 1 | 返回 `{code: {field: value}}`（含 `表.字段` 前缀键；`m_anntime`/`m_timetag` 毫秒时间戳） |
| `C.get_stock_list_in_sector(sectorname, realtime)` | 1 | 返回合成票池（如 20 只） |
| `C.get_trading_dates(stockcode, start, end, count, period)` | 1 | 返回合成交易日列表（**注意 init 内不可用**——桩须如实抛错或按 API 语义返回） |
| `C.get_instrument_detail(stockcode, iscomplete)` | 3 | 返回 dict（`InstrumentName`/`OpenDate`/`ExpireDate`/`UpStopPrice`/`DownStopPrice`/`PreClose`/`FloatVolume`/`InstrumentStatus`/`IsTrading`） |
| `passorder(opType, orderType, accountID, orderCode, prType, price, volume, C)` | 由 wrapper 调用 | **记录**订单（opType 23=买/24=卖；记录时点、代码、数量、价格） |
| `get_trade_detail_data(accountID, 'STOCK', 'POSITION')` | 由 wrapper 调用 | 返回持仓对象序列（`m_nVolume`/`m_nCanUseVolume`/`m_dOpenPrice`），由桩内账户状态维护 |

**ContextInfo 属性面**：`barpos`（桩驱动递增）、`stock_list`（策略 init 写入）、`accountID`（占位 'testS'）、
`get_bar_timetag(barpos)`（若产物引用）。

**驱动方式**：按 bar 循环调用产物的 `init(C)` 一次 → `handlebar(C)` N 次（N=合成交易日数，如 20），
每根 bar 前更新桩的"当前时点"与行情切片。

## ⑥ 形状判据 checklist（用户条件②；具体化，④验收逐条断言）

| # | 判据 | 具体判定（可机检） |
|---|---|---|
| S1 | **无未捕获异常** | 六策略 `init` + N 次 `handlebar` 全程无 raise（桩捕获并报 traceback） |
| S2 | **驱动节拍正确** | `handlebar` 实际执行次数 == 合成交易日数 N；`init` 恰 1 次 |
| S3 | **订单方向合理** | 每笔订单 `opType ∈ {23, 24}`；**数量 > 0**（无零数量/负数量订单）；买(23)与卖(24)分别统计均 ≥ 0 |
| S4 | **时点单调** | 订单序列的 bar 索引**非递减**（`t_0 ≤ t_1 ≤ …`）；无"未来 bar 下的单" |
| S5 | **持仓非负** | 任意 bar 结束时 `持仓量 ≥ 0`；且 `可用量 ≤ 持仓量`；无负持仓 |
| S6 | **净值量级合理** | 每 bar 账户总价值 > 0，且 ∈ [0.1×, 10×] 初始资金（防量级错乱/归零） |
| S7 | **审计行在位** | 产物内 `QS_QMT_*` 审计行（DENY 剥除 / SEMANTIC_DIFF / 迁移登记）在桩运行日志中可见 |

> S1–S7 全绿 = 桩冒烟 PASS。**任一红即 BLOCK**，差异须逐项归因（归因表入证据文档）。
> **证据效力声明（写死）**：桩冒烟**仅证**「产物在 QMT API 契约下可被驱动、序列形状自洽」，
> **不证**数值正确性——数值对照归 M5 真实环境。

## ⑦ 合规约束

六步流水线；平台代码前置查询纪律（桩内模拟的每个 API 语义须引 `docs/qmt/inner-api/` 行级依据，
**无标注视为未查**）；策略源码零改动；写前快照（已建）；精确 add（禁 `-A`，他线在途勿卷入）；
**禁门不放宽**（第四次承接）；编码委派 O4 七件套 + O5 五步验收；桩件**只读**使用产物（不修改产物）。

## ⑧ 验收判据 + 回退 + 判型

**判据**：
1. 桩冒烟六策略跑通（S1 无异常）；2. S2–S7 逐条断言通过；3. 桩运行产出订单/持仓/净值序列（结构化输出落证据文档）；
4. 既有回归全绿（`CONTRACT GATE` + 全部单测，含 minute deny 10 passed）；5. 策略源码零改动（hash 基线核对）；
6. **M5 真实环境实测清单**成文（条件③）。

**回退**：桩件整删（`tests/qmt_stub.py` + `tests/test_qmt_stub_smoke.py`，均为新建零侵入）；
写前快照 `stash@{0}`；备份落 `docs/handoff/`。

**失败判定**：S1–S7 任一红且无法归因 / 桩改动触碰生产路径（**违反用户条件①即失败**）/ 回归红非登记既有红。

**⑧·判型声明**：**新增检测型**——新增桩冒烟能力面（对六策略 QMT 产物报告**行为级** verdict）；
既有产物零漂移以 byte-diff + CONTRACT GATE 实证。修复前置三问：①影响其他功能=无（测试域新件，零侵入）；
②影响性能=无（不在既有链路）；③影响精度=无（本地引擎与产物零触碰）。

**⑧·影响面图**：本轮新增挂载点（测试域桩件）→ 触发；按用户裁定以 mermaid 呈报，archify HTML 不强制。
**权威方向**：允许写入清单（§④）仍是机检闸，图仅为其可视化依据；冲突以清单为准。

---

**暂停语义**：本 Plan 定稿落盘后即进入 ③实施（已获批准）；实施按委派纪律执行，④验收后呈用户确认。
