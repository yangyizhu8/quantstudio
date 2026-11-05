# QMT 停牌判据（IsTrading 误用）修复方案 · 六步①（候②审）

> 立项：用户 2026-11-04 21:5x 裁①「订单审计行修复批照准·走完整六步」→ R311「先诊断后方案，修复方向草案待诊断结果后按六步出方案件候②审」→ **2026-11-05 15:35 二轮诊断回读 ⇒ 根因已证实**（本件）
> 关联：`docs/evidence/qmt-fidelity-step1-20261104.md`（缺口发现）｜`knowledge/qmt/QD-2026-0021.md`（根因条目）｜`docs/evidence/qmt-order-audit-acceptance-20261105.md`（④验收件）
> 知识反哺：`知识反哺：[命中] QD-2026-0017（qd/D1，2026-11-03）`——交付完整性族（设计前提与实现不一致）；`知识反哺：[命中] QD-2026-0010（qd/D1，2026-10-12）`——口径类条目范式；`知识反哺：[无命中] 停牌判据族（主库 knowledge/qd 无 IsTrading/停牌判据条目），2026-11-05`

---

## 一、问题定义与**已证实**根因

### 1.1 现象（真机实测，2026-11-05 09:27 与 15:35 两轮一致）

- QMT 客户端回测窗口 2026-06-01..2026-09-30，86 个调仓日逐日：`FUN d=… A=5224 F=0 H=0 q=0 b=0`、`QS_REBALANCE_AUDIT … selected=0 tradable=0 sell_submitted=0 buy_submitted=0` ⇒ **选股层全灭、零订单**；
- 同窗本地回测 **29 笔**（22 调仓日）⇒ 差异在选股层（早于撮合层）；
- 全程**零告警**（无 `QS_QMT_UNDETERMINED`、无 `QS_QMT_SNAPSHOT first_miss=`）。

### 1.2 根因（**已证实** —— 2026-11-05 15:35 二轮诊断真值）

诊断产物临时插桩（`output/qmt_diag2/CANSLIM_diag2_qmt.py`，sha16 `dd1fe761c828afb3`，不入库·不作基线·诊断后弃）真机回读：

```
DIAG2 filter enter n= 5224 types= ['ST', 'HALT', 'DELISTING']
DIAG2 detail keys= ['CreateDate','DownStopPrice','ExchangeCode','ExchangeID','ExpireDate','FloatVolume',
  'FloatVolumn','HSGTFlag','InstrumentID','InstrumentName','InstrumentStatus','IsRecent','IsTrading',
  'LastVolume','LongMarginRatio','MainContract','OpenDate','PreClose','PriceTick','ProductID','ProductName',
  'RzrkCode','SettlementPrice','ShortMarginRatio','TotalVolume','TotalVolumn','TradingDay','UniCode',
  'UpStopPrice','VolumeMultiple']
  name= '平安银行' status= 0 trading= False expire= 99999999
DIAG2 computed flags= {'ST': False, 'HALT': True, 'DELISTING': False} ok=True
DIAG2 DROPPED code= 000001.SZ flags= {'ST': False, 'HALT': True, 'DELISTING': False}
```

⇒ **根因**：QMT `get_instrument_detail` 在**回测语境**下对**全部标的**返回 `IsTrading = False`（bool），而 wrapper 的停牌判据含

```python
if d.get('IsTrading') is False:   # source_import_qmt.py:1116
    halted = True
```

⇒ 5224 只**全判停牌** ⇒ `F=0` ⇒ 零订单 ⇒ 且因 `ok=True` **不走「不可判定保守保留」分支** ⇒ 零告警（静默失效）。

### 1.3 文档依据（铁律 16 平台代码先查文档）

| 字段 | 文档语义（`docs/qmt/inner-api/07-行情函数.md`） | 本案真值 | 结论 |
|---|---|---|---|
| `InstrumentStatus`（:2502） | int「合约停牌状态（**<=0: 正常交易**（-1: 复牌）；**>=1 停牌天数**）」 | **0** | 按权威字段 ⇒ **正常**，应保留 |
| `IsTrading`（:2503） | bool「合约**是否可交易**」 | **False**（回测语境恒 False） | **非停牌语义**，不可作停牌判据 |

### 1.4 本地侧对照（口径本就不同）

`quantstudio/backtest/ptrade_api.py:1159`：`'HALT': suspendFlag==1 OR volume==0`（停牌）——**本地侧根本不用 `IsTrading`** ⇒ 两侧停牌口径本就不同源，QMT 侧误引 `IsTrading` 是根因，且**本地侧不受影响**（本案与本地行为差异的源头）。

### 1.5 已排除的候选（排除法链，完整保留）

| 候选 | 结论 | 依据 |
|---|---|---|
| 产物 ≠ 仓内／模板多版本共存 | **排除** | 产物内 `_qs_filter_stock_by_status`／`_qs_status_flags` 各仅 1 处定义且与仓内逐字一致 |
| 快照层（D-30 冻结 PROBE 旧形态） | **排除** | `_qs_snapshot_load` 非 absent 态必印 `QS_QMT_SNAPSHOT mode=…`，日志无此行 ⇒ 走 live |
| 别名字段／四键缺失 | **排除** | DIAG2 `detail keys` 显示四键齐全，`InstrumentName='平安银行'`、`ExpireDate=99999999`（永续标记，D-13 口径生效） |
| `_qs_status_flags` 未被实际调用 | **排除** | DIAG2 全部命中（`computed flags` / `DROPPED`）⇒ 该函数确在 handlebar 语境被调用 |
| D-13 ExpireDate 误判退市 | **排除** | `expire=99999999` ⇒ 不过期；`DELISTING=False` 实测 |

> **⚠️ 我方一处误判的自纠（照录）**：2026-11-05 09:45 我曾用「init 语境 30 秒诊断码」判定「R307 的 `IsTrading` 恒 False 假设**被推翻**」——**该结论错误**，原因是**证伪手段测错语境**（init 时 `d` 为空 dict ⇒ 四值全 None；真机 filter 发生在 handlebar 语境）。**真机路径下 R307 假设成立**（DIAG2 已证）。本条一并写入 `QD-2026-0021` 讨论节留痕。

---

## 二、改动范围（精确到行）

| 序 | 文件 | 位置 | 改动 | 行量 |
|---|---|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | `_QS_QMT_STATUS_EXT` 模板串内 `_qs_status_flags`（:1088-1117） | **移除 `IsTrading` 作为停牌判据**（主选，见 §三-A）；备选见 §三-B | −2 ~ +4 |

**现实现（:1111-1118）**：
```python
    halted = False
    try:
        halted = int(d.get('InstrumentStatus') or 0) >= 1            # :2502
    except (TypeError, ValueError):
        halted = False
    if d.get('IsTrading') is False:                                   # :2503  ← 缺陷点
        halted = True
    flags['HALT'] = halted
```

**禁触面**：撮合/费用/生命周期/取数逻辑零改动；`_QS_QMT_HASH_SCOPE` 20 常量清单零改动（模板内容变更 ⇒ 哈希**必然**漂移 ⇒ 走 V4b 同 commit 追认）；策略源零改动（修复经重转生效，铁律 07）；`scripts/qmt_align_check.py`（冻结判据件）零改动；本地 `ptrade_api.py` 零改动。

---

## 三、修复方案两选一（**候②审裁定**）

### A（我推荐）：`IsTrading` **完全退出**停牌判据

```python
    halted = False
    try:
        halted = int(d.get('InstrumentStatus') or 0) >= 1            # :2502 权威字段（唯一停牌判据）
    except (TypeError, ValueError):
        halted = False
    flags['HALT'] = halted
```
- **依据**：文档 :2502 将 `InstrumentStatus` 定为**停牌状态权威字段**；`IsTrading`（:2503「是否可交易」）在回测语境恒 False ⇒ 语义与停牌不同源；
- **与本地侧口径同源**（本地按 `suspendFlag==1 OR volume==0`，与 `IsTrading` 无关）；
- **代价**：QMT 侧**真实停牌股不再被本层滤除**（见 §四 风险 R1）。

### B（保守）：`IsTrading` 降级为「仅当权威字段缺失时兜底」

```python
    if 'InstrumentStatus' not in d and d.get('IsTrading') is False:
        halted = True
```
- **优点**：老行为在「取不到 InstrumentStatus」的异常语境完全保留；
- **缺点**：判据复杂化，且 A 的两条依据（权威字段/本地同源）未变。

**我倾向 A**：缺陷的本质是「把一个回测语境恒 False 的非停牌字段当停牌判据」，A 直接消除该误用面；B 保留了一个仍可能误判的分支。

---

## 四、影响面与风险

| 项 | 内容 |
|---|---|
| **会变的行为** | QMT 侧 `filter_stock_by_status` 的 HALT 判据 ⇒ 正常股不再被误剔 ⇒ 候选集恢复 ⇒ 选股/下单恢复（**这正是本次要修的目标**） |
| **不会变的行为** | 撮合/费用/滑点/T+1/生命周期/取数口径；**本地侧零改动**（`ptrade_api` 的 HALT 判据不动） |
| **R1 风险（须明示）** | 方案 A/B 下，QMT 侧**真实停牌股不再被 wrapper 层滤除** ⇒ 可能对停牌股发起下单 ⇒ 依赖 **QMT 端自身拒单/挂起**。本地侧仍按 `suspendFlag/volume==0` 滤停牌 ⇒ **两端口径差异显性化**（本地更严）。**兜底选项（候②审）**：QMT 侧改用「停牌权威字段 ∨ 行情字段 `volume==0`」双判（需取当日行情，代价=每 bar 取数量上升），或维持现状并依赖 QMT 端拒单 |
| **判型** | **数据修正型**（停牌判据归位；结果按正确口径变化） |
| **共享层** | `source_import_qmt.py` 属共享核心（铁律 10）⇒ 写前快照四步＋精确 add＋整 index 核对 |
| **连带成本** | 六产物 SHA16 **必然换锚**（模板渲染面变更）＋ `meta.qmt_wrapper_hash` 漂移 ⇒ 同 commit `check_fund_matrix --check --reverify` 追认（V4b） |

---

## 五、验收标准（V1-V8）

| 门 | 判据 | 方法 |
|---|---|---|
| **V1** | 新增单测：构造 detail 字典三例——`{InstrumentStatus:0, IsTrading:False}` ⇒ **HALT=False**；`{InstrumentStatus:1}` ⇒ HALT=True；`{IsTrading:False}`（缺权威键）⇒ 按裁定分支（保守保留或兜底） | `pytest tests/test_qmt_status_flags.py -q`（新建件） |
| **V2** | 14 件 QMT 测试面全绿 | `pytest tests/test_qmt_*.py -q` |
| **V3** | 六策略重转 ALL PASS ＋ **SHA16 新六值入册**（旧六值作废留痕：基线件变更日志） | `scripts/qmt_reconvert_six.py` |
| **V4** | **wrapper_hash 同 commit 追认** | `check_fund_matrix.py --check --reverify`（`--pool`/`--head` 不涉） |
| **V5** | **真机复跑冒烟（判据级）**：同窗同金 ⇒ 期望 `F>0`、出现 `QS_QMT_ORDER_TARGET_NATIVE` 行、`FUN … F=<非0>`、产生交易明细 | 用户侧 QMT 客户端跑一次 |
| **V6** | **真机对照报告**：本地 29 笔/22 调仓日 vs QMT 侧订单事件，五维对照＋归因三分 | `scripts/qmt_align_check.py --qmt-log … --local-trades … --out …` |
| **V7** | 全量回归对**最新 HEAD** 零新增 | `pytest tests -q`（V6 复跑同口径） |
| **V8** | 知识反哺：`QD-2026-0021` 更新至已修＋`kb_hook --strict` 不合规 0 件 | `kb_hook.py --files knowledge/qmt/QD-2026-0021.md --strict` |

---

## 六、回退条件

| 触发 | 手段 |
|---|---|
| V1/V2 红 | 铁律 03 写前快照 stash 单文件定向还原（`git show <hash>:<path>`） |
| V5 真机仍 `F=0` | 立即回退代码；保留诊断产物继续取证（**不回退诊断件**——它在 gitignored 域，不影响基线） |
| V5 真机 `F>0` 但订单为 0（下一层仍断） | **不回退**——登记为下一层缺口（选股后链路），另立小批；本案范围仅到 HALT 判据 |
| V7 出现新增失败 | 单文件还原＋登记 ISSUES |

---

## 七、六步推进状态与待裁项

| 步 | 状态 |
|---|---|
| ① 方案 | **本件**（候总调度②审） |
| ② 审 | 待裁：**A（移除）／B（降级兜底）** ＋ **R1 兜底策略（依赖 QMT 端拒单／加 volume==0 双判）** |
| ③ 实施 | 候②审后（实现型编码 ⇒ 委派 `zcode_code`，O4 七件套＋写前快照四步） |
| ④ 验收 | V1-V8 |
| ⑤ 用户确认 | 呈批 |
| ⑥ 双仓推送 | ⑤后随出仓窗；共享层 ⇒ 副本门过门 |

**待裁两项（②审）**：①A vs B；②R1 兜底策略。
