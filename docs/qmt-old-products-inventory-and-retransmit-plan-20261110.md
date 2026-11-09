# OLD 产物清点 ＋ 重转排期（2026-11-10 · 节点⑤）

> 触发：R353 ⑦「转来 5 个 OLD 产物风险面裁归你域 ⇒ 出 OLD 产物清点+重转排期件」
> 性质：**清点＋排期**（不涉实施）；风险面＝PD-0019 期1 预算口径缺陷（`_qs_cash_budget` 用 `portfolio.cash` 总现金而非可用资金，R-20261109-01）——**OLD 产物缺该修复符号 ⇒ 仍走旧口径 ⇒ 有超额委托风险**。

## 一、风险面机理与判定符号（实读，非转述）

- 判定符号：**`_qs_cash_budget`**（含该符号＝已含预算口径修复；缺＝OLD 候选）。
- 实读结论：**该符号不在 `output/qmt_import/` 域**（18 个 QMT 产物全部命中数＝0，QMT 侧本无下单预算段）⇒ **OLD 产物域＝部署产物域 `Demo_account_Real_account/`**（PTrade 侧 `_trade.py` / `strat_*_trade.py`）。
- 五 sha 指认情况（如实）：`f37fe8d6` / `531dab84` **仓内未检得**（非 `output/qmt_import` 产物 sha16；疑为部署域产物 sha16 或 commit，需总调度补指认口径）；`6b1516fd` / `949679f2` / `a884eac2` 命中 `agent_workspace/_wt_*/docs/evidence/{portfolio-position-view,valuation-date-pit}-acceptance.md`（即**在证据文档中被引用的 commit/产物标识**）。

## 二、部署域清点（实读 `Demo_account_Real_account/`）

| 策略目录 | 代表产物 | `_qs_cash_budget` | `_qs_order_budget` | QFQ 快照 | EPS 快照 | 判定 |
|---|---|---|---|---|---|---|
| `F-Score选股RSRS择时` | `Demo_account_Real_account/F-Score选股RSRS择时/F-Score_RSRS_149655_trade.py` | 4 | 0 | 0 | 0 | **已含修复** |
| `全球资产轮动` | `Demo_account_Real_account/全球资产轮动/strat_0c202b_trade.py` | 4 | 0 | 0 | 0 | **已含修复** |
| `四象限ETF轮动策略` | `Demo_account_Real_account/四象限ETF轮动策略/ETF_f926a7_trade.py` | 4 | 0 | 0 | 0 | **已含修复** |
| `四象限ETF轮动策略-ISO-P1` | `Demo_account_Real_account/四象限ETF轮动策略-ISO-P1/ETF_f926a7_trade.py` | 8 | 0 | 0 | 0 | **已含修复** |
| `小市值策略ptrade` | `Demo_account_Real_account/小市值策略ptrade/ptrade_286d0d_trade.py` | 0 | 0 | 0 | 0 | **缺·OLD 候选** |

- **含修复符号（已消解）**：4 个代表产物；
- **缺修复符号（OLD 候选·须重转）**：1 个代表产物 —— 见下表；
- **归档目录**（`_archive/v0xx/`）：**不列入重转**（历史版本·归档留痕·禁改）。

### OLD 候选清单（缺 `_qs_cash_budget` ⇒ 须重转）

| `小市值策略ptrade` | `Demo_account_Real_account\小市值策略ptrade\ptrade_286d0d_trade.py` | 9881 B | 重转（v1.8 同批） |

## 三、重转排期（**不新增窗口：并入舱二 v1.8 重转同批**）

**排期结论：与舱二 EPS 实施批的六产物重转（v1.8 换锚）同批执行 ⇒ 零额外窗口。**

| 序 | 动作 | 落点 | 批次 | 验收判据 |
|---|---|---|---|---|
| 1 | 部署域 OLD 产物**重转**（含 `_qs_cash_budget` 修复） | `Demo_account_Real_account/**`（**归档目录除外**） | **舱二 v1.8 重转批**（同批） | 产物含 `_qs_cash_budget` ≥1 次且 sha16 变更登记；`git status` 变更集 ⊆ 允许写入清单 |
| 2 | 重转后**部署域回读核** | 同上 | 同批 | 实读确认符号在位＋逐产物 sha16 与基线登记一致 |
| 3 | 五 sha 指认口径澄清 | 台账 | 候总调度补口径 | `f37fe8d6`/`531dab84` 的归属（产物 sha16 or commit）与目标文件一一对应 |
| 4 | 归档目录（`_archive/`）**禁改**声明 | 方案/台账 | 已在册 | 重转脚本对 `_archive/` 路径拒写并打印跳过原因 |

**顺序理由**：舱二 v1.8 本就要重转六产物（EPS 面板嵌入换锚）⇒ 部署域重转在同一 commit 批内完成可**一次矩阵追认**（`check_fund_matrix --check --reverify` 同批），避免两批追认互扰。

**风险与回退**：
- 风险①：部署域产物是**客户/实盘侧交付物** ⇒ 重转须经**六步 ⑤用户确认**后方可替换（禁直接覆盖）；
- 风险②：归档目录若被误改 ⇒ 回退＝按写前快照四步（铁律 03）定向还原；
- 风险③：同批 commit 体量增大 ⇒ 拆成「框架层重转（我域）」与「部署域产物落地（需用户确认）」两笔，**仅前者由我域成笔**。

## 四、新旧基线漂移实证（裁一甲直接收益·支撑「重转消除风险面」）

- 新基线（2026-11-09 重跑·生产库当前态）：`output/qmt-fidelity-20261109/local_baseline_canslim_20260601_20260930.json`（4,319 B／sha16 `22cf33f453124484`／29 笔／22 调仓日／15 标的）；
- 与 11-04 旧基线比对：**交易集合逐笔完全一致**（仅旧 0／仅新 0／共有 29；日期、代码、方向全同）⇒ **选股与择时路径稳定**；
- 但**成交价有漂移**：价格不同 22 笔（占 75.9%），相对差中位 0.277%、最大 3.166%；数量不同 4 笔 ⇒ 集中于除权/复权重算股（002568、300121、300307、300321、300643、301051、301376、301418、600027、600228、600546、600971、601066、603638）⇒ **直接实证「旧基线反映的是历史数据时代口径」**（R3 结论），**裁一甲（基线重建）必要且已完成**。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）


---

---

## 五、策略集重对（**R354 ⑦ 纠正·域判定已推翻重做**·实读·未改任何产物）

> **R354 ⑦ 指出**：我上轮清点的五个**代表产物**（F-Score选股RSRS择时／全球资产轮动／四象限ETF轮动策略 ×2／小市值策略ptrade）**≠ ⑦ 指名的五策略**。
> **本轮实读结论（含我方前轮域判定错误纠正）**：
> 1. **域判定纠正**：`output/qmt_import/`（QMT 产物）**不含** `_qs_cash_budget`（18 产物命中全 0，QMT 无下单预算段）；**真正的 OLD 产物域＝转换产物域 `output/ptrade_export/` ＋ `output/deploy/`**（PTrade 侧产物）⇒ 我上轮扫的 `Demo_account_Real_account/` 是**另一套（已含修复的）部署账户产物**，其 4/5 含符号、1 个缺（小市值 286d0d）——**该结论仍成立但与⑦ 风险面不是同一批**；
> 2. **五 sha 全部定位到**（sha16 前 8 位全仓实算，非文档转述）⇒ **五策略现行产物全部 `budget=0`（缺 `_qs_cash_budget`）⇒ 确为 OLD 候选，转述成立**；
> 3. `_archive/` 命中项一律**排除**（归档历史·禁改）。

| ⑦ sha 前8 | 策略 | 现行产物路径 | sha16 | 体积 | `_qs_cash_budget` | QFQ 快照 | 判定 |
|---|---|---|---|---|---|---|---|
| `f37fe8d6` | 沪深300慢KD | `output/ptrade_export/沪深300慢KD超卖均值修复策略/沪深300慢KD超卖均值修复策略_ptrade.py` | f37fe8d66cddd4da | 83111 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `531dab84` | etf_hot_theme_rotation | `output/ptrade_export/etf_hot_theme_rotation/strategy/strategy_ptrade.py` | 531dab841a0d9bc6 | 92630 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `6b1516fd` | vol_regime_mom_rev | `output/ptrade_export/vol_regime_mom_rev/vol_regime_mom_rev_ptrade.py` | 6b1516fd7ddf3ae6 | 92769 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `6b1516fd` | vol_regime_mom_rev | `output/_bytecheck_main/vol_regime_mom_rev/vol_regime_mom_rev_ptrade.py` | 6b1516fd7ddf3ae6 | 92769 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `949679f2` | CANSLIM(待核) | `output/ptrade_export/CANSLIM突破成长选股策略/CANSLIM突破成长选股策略_ptrade.py` | 949679f2370890e4 | 157604 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `949679f2` | CANSLIM(待核) | `output/_bytecheck_main/CANSLIM突破成长选股策略/CANSLIM突破成长选股策略_ptrade.py` | 949679f2370890e4 | 157604 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `a884eac2` | tech_etf_mvo_rotation(待核) | `output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py` | a884eac2c6909d90 | 67390 | 0 | 0 | ****OLD 候选（缺预算修复）**** |
| `a884eac2` | tech_etf_mvo_rotation(待核) | `output/_bytecheck_main/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py` | a884eac2c6909d90 | 67390 | 0 | 0 | ****OLD 候选（缺预算修复）**** |

### 排期（不变·并入 v1.8 同批）

- **重转动作**：五策略现行产物**重转**（PTrade 转换产物域）⇒ 产出入 `_qs_cash_budget`（PD-0019 期1 预算口径修复）⇒ 消除超额委托风险面；
- **并批**：与舱二 **v1.8 六产物重转**同批（**QMT 侧**）——⚠️ **注意两域产物由不同管线生成**（QMT＝`quantstudio/strategy_compiler/source_import_qmt.py`；PTrade＝`source_import.py`／ptrade 转换器）⇒ **同批成笔、分别追认矩阵**（`qmt_wrapper_hash` 与 `wrapper_hash` 分立·禁串）；
- **禁改**：`_archive/` 一律不动；
- **落地闸**：PTrade 部署产物属交付物 ⇒ 替换前须经**六步⑤用户确认**；
- **待⑦ 确认**：五 sha 与策略的对应（上表已给出，核对后如有不符以⑦ 为准）。

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）

---

知识反哺：[命中] QD-2026-0023（qd 族／D3 域／`knowledge/qmt/QD-2026-0023.md`，2026-11-10）
本轮沉淀：QD-2026-0023（EPS 层前置结论·PIT 映射·两侧不同源机制）
