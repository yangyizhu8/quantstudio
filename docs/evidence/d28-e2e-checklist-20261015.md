# D-28 端测操作手册（A/B 背靠背黄金对照）· 2026-10-15

> 判型＝**数据修正型**；判据来源＝`docs/qmt-hist-fold-design.md` v0.2.1 §五（端测 E2E 四判据）。
> 本手册由 DSH 侧出，QMT 侧执行由用户完成；读数回抄后由 DSH 侧做判定与归档。

---

## 〇、纪律前置（**先读**）

1. **判据取证必须盘后同窗**：QMT 端测须在**盘后数据静止窗（≥15:30）**执行——理由见项目纪律「端测纪律-QMT端测须盘后同窗同源黄金对照」（供应商推送窗口 + 盘中实时读数漂移会污染对照）。
2. **盘中可跑「冒烟轮」**：若要在盘中先确认粘贴可跑、读数格式正确，请**明确标注为冒烟轮**（仅验证脚本可运行），**不作为判据取证**，其读数不入验收结论。
3. **A/B 必须背靠背**：同一天、同区间、同参数、同设备；两轮间隔**尽量短**（D-27b 先例 <17 分钟），中间不得跨供应商推送窗口。
4. **两轮配置必须完全一致**：回测区间、初始资金、撮合设置、标的池、参数全部相同——**唯一差异只能是粘贴的产物（A 或 B）**。
5. **A 先 B 后**（顺序固定，便于与历史读数对齐）。

---

## 一、材料（已由 DSH 侧产出并通过 canonical 门禁）

| 件 | 路径 | sha16 | 大小 | 编码 | 门禁 |
| --- | --- | --- | --- | --- | --- |
| **A（折叠前）** | `docs/evidence/d28-e2e-pair-20261015/weekly_smallcap_A_prefix.py` | `9d00de2fc2d58d4a` | 101,999 B | gbk（无 BOM） | portability=PASS, selfcheck=PASS |
| **B（折叠后）** | `docs/evidence/d28-e2e-pair-20261015/weekly_smallcap_B_fold.py` | `9d54605aeb83d556` | 106,423 B | gbk（无 BOM） | portability=PASS, selfcheck=PASS |

**隔离性（关键）**：A 取自 HEAD `5045212`（D-28 未入库，故 HEAD 内无折叠代码）；B 取自当前工作树（含 D-28 折叠）。两件**只差 D-28 一项**——D-30 快照层在两件中都存在（已提交、默认关＝零漂移）。A 件折叠标记＝0，B 件＝5（`_qs_hist_fold` / `QS_QMT_HISTFOLD`）。
**产物 diff 逐行归因**：`ADD=113 / REM=31 / 5 hunks`，hunk 区间 `1246→1247+78`、`1386-30→1464+14`、`1438→1501+6`、`1440→1508`、`1482→1551+14`——**全部落在 wrapper 注入段（行 1246–1564）**，不触及策略逻辑段。

---

## 二、操作步骤（QMT 侧）

1. 打开大 QMT → 内置 Python 策略（沿用历次端测同一通道/同一策略文件）。
2. **第 1 轮 · A**：把 `weekly_smallcap_A_prefix.py` **全文**粘贴覆盖 → 设定回测区间与参数（与下轮完全一致）→ 运行到底。
3. 运行结束后，**原样复制**：①全部 `[QS-QMT log.info]` 行；②漏斗与指标读数行（或回测结果面板中的同名指标）。
4. **第 2 轮 · B**：把 `weekly_smallcap_B_fold.py` **全文**粘贴覆盖 → **同区间同参数** → 运行到底。
5. 同样复制 B 轮读数，回贴给 DSH 侧（建议两轮读数各贴一段，勿混）。

> 若粘贴后 QMT 报编码/语法错：立即停止并回报（两件均已通过 GBK 编码契约自检，不应发生；若发生属新问题，须先定谳再续跑）。

---

## 三、回抄清单（逐项，缺项即视为取证不完整）

**① 审计行（B 轮新增，A 轮应无 HISTFOLD）**

```
[QS-QMT log.info] QS_QMT_HISTFOLD t=<表> code=<首只> before=<n> after=<n> intravar=<k>
[QS-QMT log.info] QS_QMT_HISTDBG3  t=<表> code=<n> rows=<n> ua=<n> ut=<n> uk=<n> uv=<n> after=<n>
[QS-QMT log.info] QSFUNDHIST t=<表> kept=<n> drop=<n> cc=<n> n=<n> raw=<形态>
```
（期望：`t=eps` 与 `t=growth_ability` 两行齐；`before` ≈ 首只全史行数、`after` ≈ 报告期数、`intravar=0`）

**② 性能口径（判据 2 的仪器）**

```
[QS-QMT log.info] QS_QMT_PROGRESS func=fin_to_rows_hist ... phase=begin / phase=end elapsed=…
[QS-QMT log.info] QS_QMT_PROGRESS func=get_financial_data ... phase=begin / phase=end elapsed=…
```
（**只认 `fin_to_rows_hist` 处理段**；`get_financial_data` 拉取段（46–77s/表）**不因折叠削减**，如实登记不虚报）

**③ 漏斗与结果（判据 3 的 14 项）**

`L0 / L1 / L2 / L3 / L3v` · `L4 / L5 / L6 / R / R_selected` · `selected / tradable / sell / buy` · `positions / cash_ratio / cash` · `total_value`

---

## 四、判定表（DSH 侧据此判）

| 判据 | PASS 条件 | 备注 |
| --- | --- | --- |
| **判据 1 · 折叠读数** | B 轮 `HISTFOLD` 两表齐；`before`≈8740（首只全史）、`after`≈79（报告期）、`intravar=0` | `intravar>0` → 披露并触发判据 3 归因 |
| **判据 2 · 性能（处理段口径）** | `fin_to_rows_hist` 处理段 **226s → ~10s 量级** | 性能是伴生收益；**不达标不构成回退条件**（本体是契约修正） |
| **判据 3 · 数据修正型·可达面零漂移** | A/B **14 项读数逐位一致**（含 QSFUNDHIST kept/drop/cc） | 唯一实质门禁：diff 即验收失败（按 §五处置） |
| 判据 4 · SG-MS-PEG 真实对照 | **本轮不判**（D-29 未裁，QMT 端该表恒空帧） | 由 `tests/test_qmt_hist_fold.py::test_12`（真实函数合成对照）代偿，已在 ④验收记录 |

---

## 五、若判据 3 出现 diff（处置次序，写死）

1. **先复跑**（同窗同参）——排除一次性抖动/数据推送污染；
2. 复跑仍 diff 且归因数据漂移（如 L1/L2 变动＝合约信息表新推送）→ **换窗重跑**（两轮同换）；
3. 归因**折叠行集**（即 diff 由 B 的折叠行为解释）→ **验收失败，走回退**：
   - 代码回退：`git stash` 快照 `17040123be1fd32c5ed24c0fb349254d440a1222`（或按路径单文件提取）；
   - 矩阵哈希回追认：`python scripts/check_fund_matrix.py --check --reverify`；
   - **六策略 canonical 重转**＋**QMT 端已部署产物替换**（回退不重转＝端上仍跑折叠版）。

---

## 六、归档要求

回抄读数落地为 `docs/evidence/d28-e2e-run-20261015.md`（由 DSH 侧汇总），须含：两轮时间戳、粘贴件 sha16、全部审计行原文、14 项读数并列对照表、判据 1/2/3 判定与差异归因（若有）。

## 七、封存纪律

- A/B 两件为**时点性产物**（2026-10-15 基线）：实施/推送后如 wrapper 再变，须重新出对（不得复用旧件做跨版本对照）。
- 本手册随 `docs/evidence/d28-e2e-pair-20261015/SHA256SUMS.txt` 一同归档；校验以该清单为准。
