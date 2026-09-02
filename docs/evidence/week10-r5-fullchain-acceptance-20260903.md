# Week10 R5→R5.5→R5.4 全链·总体验收报告（2026-09-03 终稿）

- 策略：wsgm10v2（weekly_smallcap_growth_momentum_10_v2 / 周频小市值成长动量三层止损，design 2.2，PUBLISHED 品零改动——canonical sha256 d5e554fe…）
- 执行令：week10 R5 主运行启动令 → R5.5 窗口裁定（路径甲）→ fold 双 bug 修复 diff 审批 → 本报告

## 1. 执行链终态（三段）

### R5 主运行（w15 扩展窗口 2025-01-01~2026-03-31，299 交易日）
- A 组（基线滑点）双跑：收益 **+76.54%** / 回撤 11.70% / 夏普 2.71 / 超额 +60.05%；
- B 组（固定滑点 0.02 压力）双跑：收益 +53.21% / 回撤 10.06% / 夏普 2.55（压力衰减 -1.31pp，鲁棒）；
- **G3.5 复现：A/B 双组 PASS**（config/daily_stats/trades SHA-256 逐位一致）。
- manifests：`r5_logs/w15_{A,B}_{run1,run2}_manifest.json`（hash-bound）。

### R5.5 鲁棒性套件（六门全绿零保留）
| 门 | 值 | 阈值 |
|---|---|---|
| G1 盈利韧性 | 0.6152 | >0 |
| G2 回撤 | -0.1170 | >-0.25 |
| G3 夏普 | 2.2682 | >0.5 |
| G4 胜率（56 RT） | 0.5893 | >0.4 |
| **G5 折叠** | **0.80（4/5 折正超额）** | >0.6 |
| **G6 MC 置信** | **p=0.0010** | <0.01 |

**overall=PASS failed_gates=none insufficient=none**。
证据：`robustness/robustness_report_iter0.json` + fold 产物（engine 真实成交审计行在卷）。

### R5.4 寻优
**NOT_APPLICABLE**（设计态正确）：design 2.2 无 `parameter_optimization_contract`（Phase 1 行为，脚本显式裁定；设计全文核查无该字段——非配置遗漏）。

## 2. 过程价值项

### 2.1 窗口裁定（路径甲）
w10 首轮（243 天）：G5 INSUFFICIENT（窗口<250）+ G6 边缘（p=0.025）→ 用户裁定扩展 299 天 → **G5 前提满足 + G6 p 0.025→0.001 转绿**。w10 证据保留作对照（4 manifests + w10_r5_evidence.json）。

### 2.2 R5.5 fold 链双 bug 修复（框架层，diff 已审 +33/-12）
- bug① strategy_file basename 化 → fold runner cwd 相对解析 FileNotFoundError → **workspace→project_root 锚定解析**；
- bug② run_backtest 输出目录内部生成（{stamp}_strategy）≠ 调用方 fold_dir → **返回真实 output_dir，orchestrator 从真实目录读产物**；
- 实证：修复前 valid folds 0<3（NO_TRADE 误判）→ 修复后 **4/5 折正超额 G5=0.80 PASS**。
- 设计：`docs/r55-fold-chain-fix-design.md`；回退点 `b1ed785`。

## 3. 遗留
- `_run_engine` 修复的单元测试补充（轻量，随下个 skill 线 commit）；
- R5.4 契约为 Phase 2 opt-in——若未来 wsgm10v2 启用寻优需先在 design 增补契约并重走 R2.5 确认。

## 4. 结论

**week10 R5→R5.5→R5.4 全链闭环**：主运行四跑零失败+双组复现逐位一致；鲁棒性六门全绿（含折叠 4/5 正超额）；寻优按设计态 N/A。策略质量三面（盈利/风险/统计显著性）全部实证。