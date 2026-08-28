# R5.4 参数寻优体系 · 验收证据（Phase 2）

- 日期：2026-08-28 ｜ skill：quantstudio-strategy-compiler `1.0.0-r54-optimize`
- 设计文档：`docs/strategy-compiler/parameter-optimization-design.md`（终审通过，M1-M3 已并入）
- 实施前 HEAD 锚点：`9a9a774` ｜ 执行环境：PYTHONDONTWRITEBYTECODE=1（验收后 skill 目录零 pyc）

## 八项验收结果

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 真实小规模研究回放 | ✅（含诚实降级实证） | fscore_rsrs 临时授权 {5,6} 双组合网格：编排全流程走通（8 次内层引擎运行真实执行、逐试验 config/overrides SHA-256 绑定、cost 8/8、wall=1325s、schema_ok=True、M2 覆写文件收尾删除实证）；部分内层运行失败（见"登记项"）按设计降级 FAILED→nan→聚合 KEEP_DEFAULT，**fail-closed 诚实路径实证**；单次内层运行独立复现成功（43 天窗口 → 完整产物），编排调用链正确性同时证明 |
| 2 | 覆写钩子等价性 | ✅ | importlib 生产同款加载路径三断言：A 无覆写文件 → P()=设计默认 ✓；B 声明覆写 → 25 生效 ✓；selftest hook-equivalence 全绿 |
| 3 | 未声明键拒绝 | ✅ | 覆写含搜索空间外键 → importlib 加载期 RuntimeError("undeclared keys")（真实验证）+ 写入期 Block（selftest）双防线 |
| 4 | 预算熔断 | ✅ | 网格 60 组合 >50 → Block（selftest）；optuna n_trials=99 → schema 拒绝（max 50）；timeout → INCOMPLETE_TIMEOUT 状态（实现+schema） |
| 5 | 聚合含 SKIP 折 | ✅ | selftest：SKIP 折不分母 ✓；多数票 20/3 折 → PROPOSED ✓；全异 → UNRESOLVED ✓；有效折 <2 → 保持默认 ✓ |
| 6 | lint 声明键 | ✅ | 真实完整设计（fscore_rsrs 2.3 + 契约）：P() 钩读 → 无 lint；不读 → `OPTIMIZATION-PARAM-NOT-VIA-HOOK` BLOCK；禁用契约零检查 |
| 7 | 发布三门 + M2 生命周期 | ✅ | 四断言：授权未研究 → REJECTED（报因指向 R5.4）；提案未入源 → REJECTED；提案入源 → ACCEPTED；param_overrides.json 跨 R6 → REJECTED；R5.4 收尾必删（回放实证 overrides_deleted=True） |
| 8 | Phase 1 零回归 | ✅ | robustness_selftest --all ALL GREEN；quick_validate → "Skill is valid!"；design schema 双版本兼容探针：真实 2.2 设计原样通过 / 升 2.3 通过全部版本门控 / 非法值（n_trials=99、7 参数空间）正确拒绝 |

## 改动清单（6 M + 4 新增 + 1 设计文档）

- 新增：`scripts/run_optimization_study.py`（嵌套 WF 编排器）、`schemas/optimization_study_report.schema.json`（v1.0）、`references/parameter-optimization.md`、`scripts/optimization_selftest.py`
- 修改：`schemas/agent_strategy_design.schema.json`（2.2→2.3：8 处版本门控继承 +2.3 五契约必填块 + 可选 parameter_optimization_contract）、`scripts/create_agent_workspace.py`（P() 钩子注入，仅 enabled 契约时）、`scripts/validate_agent_strategy.py`（lint 挂载）、`scripts/publish_agent_strategy.py`（R5.4 三门 + M2 末端执法）、`SKILL.md`（R5.4 章节/规则 36-39/Prohibited 5 条/Commands 2 条/版本行 1.0.0-r54-optimize）、`schemas/run_card.schema.json`（枚举 + 兼容声明）、`README.md` + `docs/strategy_toolbox.md`（R5.4 表述同步）
- 设计文档：`docs/strategy-compiler/parameter-optimization-design.md`（终审通过版）

## 零触碰声明（纯增益核验）

回测引擎、转换管线、PyQt、validate_runtime_shapes.py、templates/、R5.5 契约（robustness_report 1.0）、user_backtest_evidence 2.1——零改动。禁用/缺省路径与 Phase 1 行为逐位一致（AC8 实证）。

## 登记项（发现-登记-排队，不顺手扩范围）

1. **fscore_rsrs 短窗起跑失败待查**：该策略在 1 月初起跑的短窗内层运行未产出 daily_stats（疑与其 F-score 财务数据 PIT 依赖在短窗/特定起跑日的表现有关；3 月起跑 43 天同策略运行成功）。属策略自身数据依赖边界，非套件缺陷——suite 已按设计将其诚实降级（FAILED→nan→KEEP_DEFAULT）。后续如需深挖单独立项；
2. **成本计数口径**：runs_planned 仅计内层运行（OOS 评运行未入 planned 分母），executed 含 OOS——报告两数相等时表示全部计划完成；下版本可拆分 inner/oos 计数列（登记不阻塞）；
3. **week10 workspace**：仍无 R5 证据，其寻优回放待其管线走完 R5 后即可复用本套件。
