# 件 A+B · 对齐生命周期门+S3/S4 · 验收证据（六步③④同轮，2026-10-07）

> 方案件 `docs/alignment-lifecycle-gate-design.md`+`docs/alignment-triage-convergence-design.md`
> （commit 176e626）；总调度 ②审计 PASS+③实施令（2026-10-07）。两件均新增检测型。

## 1. 件 A 实施（六态状态机+ALIGNMENT-GATE）

| 项 | 落点 | 验收 |
|---|---|---|
| 生命周期契约 | `docs/strategy-compiler/alignment-lifecycle.md`（六态+降级回环+豁免登记+联动） | ✓ 落位 |
| 校验规则 | `validate_agent_strategy.py` `_validate_alignment_gate`（报告缺失 WARN/verdict 已对齐或残差达标 PASS 态/存在但未达标 BLOCK） | 单测 5 项绿 |
| **实施中发现并修复的缝隙** | S1 §5「已对齐」=零偏差口径 vs 闭环判据<0.5% 脱节——四象限 0.013% 达标但 verdict=混合形态会遭误 BLOCK。修复：§6 承载残差达标语义（末值偏差/总资产<0.5% 输出「残差达标 ✅」），Gate 放行条件=已对齐 **或** 残差达标 | `test_gate_residual_ok_passes_despite_mixed_verdict` 绿+S1 实测 ✓ |
| **连带修复的既有缺陷** | 校验器 CLI 直跑必炸 `NameError: _validate_platform_fallback_handwritten`（HEAD 取证：10ce73c 把 def 挪到 `__main__` 块后；skill 编排器走 import 未踩雷）。修复：`__main__` 块移至文件末尾（纯恢复型，零行为变化） | CLI 直跑 bbi_etf_rotation：正常出报告+ALIGNMENT-GATE WARN 正确触发 ✓ |

## 2. 件 B 实施（S3 规则库+S4 台账）

| 项 | 落点 | 验收 |
|---|---|---|
| S3 规则库 | `scripts/align_triage_rules.py`：4 条种子规则（CORP-01 合并带/CORP-02 尾差/POS-ENG-API 触发链/FEE 现金先偏）+`triage(J)` 主入口（已对齐直通/命中候选/无命中立新案） | 回放单测 6 项绿 |
| S1 §6 挂钩 | `align_diff_report.py` §5 后纯追加（§1-5 逐字节不变）+J 增 `order_divs`/`triage` 字段 | S1 实测：§6 输出残差达标+候选+证据链接 ✓ |
| **回放验收（四象限三轮）** | CORP-01 前轮（nav 首偏+委托现金零分歧）→ merge-band 命中 ✓；CORP-02 轮（3296 vs 3200 整手截断）→ odd-lot-tail 命中 ✓；终轮已对齐 → 无需路由 ✓ | 与人工归因逐轮一致 |
| S4 台账 | `knowledge/alignment-convergence.md`：种子数据（3.88pp→2.23%→0.013% 全链）+收敛指标+平台期/复发双告警+维度宣布联动（首例候选 ETF 日线域） | ✓ 落位 |

## 3. 测试与回归

- `tests/test_alignment_gate_triage.py` **11 passed**（A 5 项+B 6 项）；
- 邻域回归 `test_odd_lot_sell + test_corp_action_merge` **12 passed**；
- 校验器 CLI 真实回归（bbi_etf_rotation）：输出正常，ALIGNMENT-GATE 按预期 WARN（该策略无
  alignment 报告=存量豁免登记制形态）。

## 4. 判型与回退

两件均新增检测型（新门禁/新报告只对「未对齐/未路由」报真相，不改既有行为；S1 §1-5
文本逐字节不变经实测对照）。NameError 修复为纯恢复型。回退：A 校验调用单点摘除+
规则/台账文件整删即可；快照 `bb8d522`（写前）。

## 5. 红名单

本件新增红 0（全部测试绿）；既有 BLOCK 2 项属 bbi_etf_rotation 策略自身校验问题
（非本件引入，CLI 输出可见）。
