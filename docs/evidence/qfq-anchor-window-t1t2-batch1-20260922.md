# 错误一批一验收（T1/T2）· 生效观察判据表随批 · 2026-09-22

- 拆批依据：总调度加速令——T1（检查器锚同源+UNKNOWN 占比）+T2（阻断规则+skip 硬计数）
  系客户日志 CRITICAL 误报的**直接修复**，独立成笔不等 T3/T5。
- **本 commit 范围 = 仅 T1/T2**；T5（aligner fallback）与 T3 不在本笔（工作区在途，二批呈交）；
  T4 排水/T6 读窗复验留「计划空档窗」。
- 回退锚点：fa8e64f 后 HEAD；写前快照 `4afd5930`（baseline-err2-impl-20260922_111757）。

## 一、T1 实现（检查器锚同源 + UNKNOWN 三面可见）

| 件 | 内容 |
|---|---|
| `qfq_invariant.py` | 签名 `anchor_source="caller"`；`adj_i` **行内 adj_factor 列优先**（=写入公式同一值），缺失才 aux 回退并计 `cross_source_adj_i`——消除修订窗「行内旧因子 vs aux 新因子」跨源误报（520550 ratio=1.0352 实型）；`r3_reload` 路径行级偏离→**unknown 不计 bad 不计 streak**；no_anchor 行 skipped（旧语义）+unknown（新维度）；`unknown_rate` 收尾计算 |
| `daemon.py` 调用方 | R3 兜底传 `anchor_source="r3_reload"`（9-22 06:44 实录路径根治）；ERROR 行附 `unknown=/xsrc=`；**mass_unknown WARNING**（占比>20% →「检查器覆盖退化，须排查 map 传递链」——过审硬约束，防 UNKNOWN 沦为吞异常后门）；`qfq_selfcheck_log` PRAGMA 探测 + ALTER 扩 `unknown_rows/anchor_source` 两列落库（旧库失败降级仅日志，不扰主路径） |
| 向后兼容 | 旧返回键全保留——test_6（空 map→skipped）/test_8（口径 A+R3 反证）**零修改原样绿** |

## 二、T2 实现（阻断规则修订）

- `len(batches) >= 3 or rate > 0.05` → **`len(batches) >= 3 or (rate > 0.20 and len(batches) >= 2)`**
- 依据：9-06 33 次 CRITICAL（5.38%~19.7%）全部落在锚演进/重载误报窗——单批 5% 即拦=以偏概全；
  真错写快通道（双批且>20%）保留；test_d（rate=5.00% 走三批路径）零修改兼容。
- skip 硬计数：skip 日志行**代码原已存在**（daemon L448-451），全历史 0 命中=阻断从未撞上下一轮
  （事实如实入册）；本笔未新增 skip 观测面（G7 判据验证其未来生效性）。

## 三、验收证据（实测）

| # | 证据 | 值 |
|---|---|---|
| 1 | **一批仓库态套件**（时间序铁证：65 passed 跑于 T5 落盘**之前** = T1/T2+旧 aligner 组合） | `test_qfq_invariant/alignment/global_snapshot/bootstrap_gates/mcp_etf/event_discovery` **65 passed** |
| 2 | 本批新用例 | T1×2（各含反证：caller 路径照常 bad）+ T2×1，**3 例绿** |
| 3 | 本批提交前复跑 | invariant+alignment+event_discovery **31 passed**；py_compile 全过 |
| 4 | daemon.py 叠加核查 | 8 hunks 全在防线 1 两区（2645-2690/2714-2792），他线零卷入 |

## 四、生效观察判据表（换代际/客户机拉取后各自首个运行窗核验）

**错误二（`fa8e64f`，已上远程）——用户点名两项 + 连带**：

| # | 判据 | 观测方法（grep 日志/查库） | 期望 |
|---|---|---|---|
| G1 | **脏码 skip 计数** | `[qfq_event] * contract pre-filter skip` 或 `[qfq_orch]` 行 | 本机换代际后首窗：命中≥0（aux FIXTEST 存量触发）；**不再伴随周期异常** |
| G2 | **冷启动零误触** | `线1 因子冷启动触发` 行含 TEST999/FIXTEST 样例 | 修复后 = **0 次**（合法新码触发不算误） |
| G3 | **orch 不再被崩** | `[qfq_orch] 周期异常` | 连续 ≥3 周期 **0 行** |
| G4 | **revision_alert 非空**（T3 解锁自证） | aux `SELECT count(*) FROM qfq_factor_revision_alert WHERE status='pending'` + 后续 consumed | 首个正常周期后 >0（9-15 型修订补记账） |

**错误一批一（本 commit，上线后代际核验）**：

| # | 判据 | 观测方法 | 期望 |
|---|---|---|---|
| G5 | **单批误报阻断消失** | `CRITICAL.*QFQ-Invariant.*单批\|rate.*>5%`（旧文案）→新文案「双批且」；客户机 9-2x 窗 | 旧式单批 5% CRITICAL **=0**；mass_unknown 可见但不推 streak |
| G6 | **UNKNOWN 落库可见** | `batch_audit.db` `SELECT status,unknown_rows,xsrc FROM qfq_selfcheck_log` 新列 | r3/缺锚行 unknown_rows>0 且 bad 不涨；xsrc 计数有行 |
| G7 | **skip 硬生效** | `QFQ 写入自检阻断.*本轮跳过` | 未来真阻断时 >0（补历史 0 命中观测洞） |

**判据执行位点**：本机=daemon 换代际（重启）后首个含 mcp_etf_daily/stock_daily 任务窗；
客户机=拉取 `fa8e64f`+本批后代际后首个运行窗——两机各自回报，判据表逐项对账。

## 五、回退与二批

- 回退：本笔单 commit revert 即可（三文件+docs 无交织）；
- 二批（T5）：aligner.py + test_qfq_global_snapshot.py T5 用例 + 总证据件，随快审后呈。
