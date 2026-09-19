# PROJECT_STATE · QuantStudioHybrid

> 跨会话状态快照。新会话首动作：读本文件 + `docs/RUST_HYBRID_INITIATIVE.md` 恢复上下文。
> 更新纪律：每完成一个强制确认节点或迭代轮实施申报后立即刷新（簿记豁免，无需单独审批轮）。

## 当前阶段

六步①方案件起草（未开始）——前置 C（副本构建+交接）已完成。

## 已锁定基线

- 副本：`C:\QuantStudioHybrid`（真浅克隆 --depth 1 --no-local --single-branch）
- 基线提交：main@366596d
- 开发分支：feat/rust-hybrid（当前所在）
- git 身份：yangyizhu8（副本 local config，匹配 GitHub 帐号）
- 权威立项文件：`docs/RUST_HYBRID_INITIATIVE.md`（立项令原文 / 008 结论 / 三数值契约 / 函数级边界清单 / 铁律）

## 已完成里程碑

1. 2026-09-20 前置 C：副本构建（浅克隆验证：单提交历史、shallow=True、.git 7.3MB、工作树 23.0MB、生产库未入副本）
2. 2026-09-20 前置 C：交接三工件落盘（本文件 + 立项卷宗 + ISSUES.md），随首提交入库
3. 2026-09-20 分析期：008 报告（Rust 混合改造纯增益可行性，四不影响判定）——结论已固化入立项卷宗 §2

## 未闭合 Issue 清单

见 `docs/ISSUES.md`（当前 2 条候选：iterrows :2433、DataDict.__contains__ :314-328，均未立项）

## 下一步待确认事项（新会话首轮待办）

1. 起草六步①方案件（六节：①函数级迁移顺序 ②引擎级双跑对比器设计 ③三处数值契约 ④Phase 0 实测热点表 ⑤分里程碑门禁与回退 ⑥并回主仓流程），完成后呈总调度审（六步②）
2. Phase 0 前置：影子数据根落位（QUANTSTUDIO_DATA_ROOT，参照立项卷宗 §4）
3. 用户提供 GitHub 公共仓库完整 URL（owner/QuantStudioHybrid）后添加 remote

## 当前有效迭代范围

仅六步①方案件起草。任何 .rs 产码在总调度审（②）通过之后。

## 基线差异备忘

主仓工作树在 366596d 之上有 395 个未提交文件（含 quantstudio/backtest 约 16 个）——副本不含；若引擎行号与 008 报告有偏差，先核对主仓未提交改动（详见立项卷宗 §3）。
