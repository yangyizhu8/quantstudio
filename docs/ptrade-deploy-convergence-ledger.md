# qs-deploy 收敛台账（总调度批复修订②——主仓仅存台账与指针）

> vault 实体：**QuantStudio-trading 仓** `vault/knowledge/ptrade-deploy/`
> （含实盘部署细节，不入公开仓——M1 §2.2/§8.1 边界声明）。
> 本台账：公开可审计的收敛过程——仅 N_i/R_i 计数、轮次记录与 vault 指针。
> 格式参照 `knowledge/alignment-convergence.md`（S4 先例）。
> 更新触发：每轮闭环（修复+证据+条目+回哺四同步完成）后追加。

## 1. 轮次主表

| 轮次 | 日期 | 触发场景 | 新发现 N_i | 复发 R_i | 条目 | 状态 |
|---|---|---|---|---|---|---|
| 1（基线） | 2026-10-10 | M3 内核实施·固件横验证 | 4 | 0 | PD-2026-0001（GUARD 剥离形态）/ 0002（g.__ 文本还原误伤）/ 0003（契约表种子缺漏）/ 0004（未知 API 豁免口径） | 全闭环：修复+测试固化（25/25）+条目入库+回哺机制生效 |
| 2 | 2026-10-10 | M7 横验证·真实转换产物（V6） | 2 | 0 | PD-2026-0005（豁免口径三层缺口——closed）/ PD-2026-0006（run_daily 源形态能力边界——open，待立项呈总调度） | 0005 闭环（注记 17/4/16→1/3/7+25/25 维持）；0006 登记不挂账（能力扩展走六步新方案件，V6 以 fall_reversal 补位 3/3） |

### V6 横验证选型登记（修订③：日线×3 替代理由）

- 池中 canonical 6 策略全为日线/周频（无适用分钟策略）；分钟信号候选
  （断板反包/连板打板/隔夜头皮）不在 canonical 池 → 按批复以日线×3 执行；
- 选型：CANSLIM突破成长选股（基本面）/ fall_reversal（下跌反转）/
  vol_regime_mom_rev（波动体制）——场景差异化覆盖；tech_etf_mvo 因
  PD-0006 能力边界换位（fail-closed 拦截行为正确）。

## 2. 收敛指标（判据：M1 §8.5）

- **收敛目标**：连续 3 轮 R_i=0 且 N_i 单调递减趋零；
- 当前：轮 1 基线 N₁=4，R₁=0——待轮 2/3 验证趋势；
- 回哺链路：P0 预检读 vault（同机直读，不可达降级 degraded——总调度裁定①照准）。

## 3. 复发警报（R_i>0 = 流程违规）

- 同 root_cause 条目再次命中 → 复发：先归因（知识库缺条目 / 回哺链断裂 /
  修复落点不合规）→ **先修管线再修漏洞**；
- 平台期告警：同策略连续 3 轮 N_i 不降 ⇒ 评估扩探针（参照 S4 先例）。

## 4. vault 指针与登记簿

| 条目 | 状态 | fix_layer | commit |
|---|---|---|---|
| PD-2026-0001 | closed（轮 1） | kernel（equiv.py） | aee3e77（主仓本地 commit 2026-10-11；推送候令下批统出） |
| PD-2026-0002 | closed（轮 1） | kernel（equiv.py + runner.py） | aee3e77 |
| PD-2026-0003 | closed（轮 1） | contract_table | aee3e77 |
| PD-2026-0004 | closed（轮 1） | gate（gates.py） | aee3e77 |
| PD-2026-0005 | closed（轮 2） | gate（gates.py） | aee3e77 |
| PD-2026-0006 | closed（轮 3，⑤过 2026-10-15） | profiler+config+transformer+equiv | 38087eb（主仓本地 commit 2026-10-15；推送候令随批统出） |

> 回填记录：五条目 commit sha 已于 M8 本地轮回填（aee3e77，2026-10-11；trading 仓条目 fix_target 字段与本表同步落盘）；PD-0006 条目 sha 已于轮 3 回填（38087eb，2026-10-15）。推送后 sha 不变，双推完成即本表口径生效。

## 4.1 轮 3（PD-0006 run_daily 源形态，2026-10-11）

- **N₃=1**（新发现）：上游 convert-pipeline 在途把 hv_canslim/hv_vol_regime 源重写为混合形态（handle_data stub + run_daily 真逻辑挂载并存）——挂载对象歧义，P2 显式拒绝（fail-closed）；混合形态支持待上游形态稳定后单独立项（vault 观察项）。
- **R₃=0**（无复发）：轮 1-2 五条目修复无复发；既有 25 用例全绿（1 处断言演进放宽归因他线合法叠加）。
- **能力增量**：hv_tech_etf_mvo 复现件转正（7 阶段全 PASS）；deploy 套件 35/35；证据 `docs/evidence/ptrade-deploy-pd0006-evidence.md`。
- 收敛判据对照：R 连续 3 轮 =0 ✓（轮 1/2/3）；N 序列 4→2→1 单调递减 ✓——**收敛闭环判据达成**（工程侧；真实使用周期继续验证）。

## 5. 降级记录（trading 仓不可达时的 degraded 登记）

- 2026-10-10（M6 之前）：P0 vault 回哺降级（vault 未建，预期状态）；
- M6 起 vault 建立后应无降级——若再现按 M1 §14.6 登记。
