# B·因子等价识别（rebase 预检判据升级）方案骨架（firefly 案·专项 B，2026-10-21）

- 状态：骨架（待 ②审后细化实施）｜总调度 2026-10-21 裁定「B 先行 + C 评估 + A 终态登记」
- 判型：**行为修正型（数据正确性）**——解水位自锁的同一把钥匙（W-1/41× 链共用）
- 上游证据：[rebase-factor-drift-spectrum-20261021.md](../evidence/rebase-factor-drift-spectrum-20261021.md)

## 0. 知识反哺检索留痕

```
知识反哺：[命中] 已知错误-ST数据层-对齐回填缺口（12-客户运维/已知错误/已知错误-ST数据层-对齐回填缺口.md，2026-10-21）→ 采纳要点：数据层判据问题须"先取证后改判据"，改动须附量级谱证据
知识反哺：[命中] 案例-CASE-006交付质量案 → qfq 窗口守卫族问题的框架层修复方法学（负向对照+复现矩阵）
知识反哺：[无命中] 12-客户运维/已知错误（因子等价识别 / factor equivalence / 复权因子版本漂移主题），2026-10-21
```

## 1. 问题（一句话）

`_check_daily_raw_align` / `_check_minute_cov_raw` 要求 fresh raw 与库内 raw **逐位 |Δ|≤1e-9**，
但库内 raw 隐含"写入时因子快照"、fresh 隐含"现因子快照"；上游因子历史被持续微调
（实证：513530 fund_adj 跳变 1347 个/大跳 889 个/同日成对往返）⇒ 判据永不可能满足 ⇒ rebase 自锁。

## 2. 方案核心（两级判据）

```
第一级（原判据，不变）：fresh raw vs 库内 raw 逐位 |Δ|≤1e-9 → 通过
第二级（新增 B）：第一级失败时，做因子等价识别——
    对每个不一致 bar，构造解释比 r_t = af_now(t) / af_write(t)（两侧因子序列均可取：
    qfq_aux::fund_adj/adj_factor 现值；写入时快照经 C 路径留存，B 阶段先用
    「候选比值收敛法」：对不一致行集求 r̂ = median(fresh/db)，若所有不一致行的
    |fresh/db − r̂·k_t| ≤ 1e-9（k_t∈{1} 或按 af 跳变点分段），判定为因子版本差异
    → 不 block；标记 factor_equivalent=True，照常进入 rebase（front 本就会用现因子重算）
不满足比值解释（真正的数据漂移/串码/截断）→ 仍按原判据 block（防放宽）
```

**不放宽面（红线）**：传输完整性保护保留——覆盖检查（缺行/多行）不变；NULL/NaN/Inf/≤0 检查不变；
比值解释不成立 ⇒ 维持 blocked；因子等价放行仅改「raw 逐位」这一项。

## 3. 落点与改动面

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/qfq_reanchor_engine.py` | `_check_daily_raw_align` / `_check_minute_cov_raw` 增第二级；`ReanchorBlocked` 增 `factor_equivalent` 旁路标记 |
| `quantstudio/pipeline/qfq_resident_orchestrator.py` | 单元 outcome 透传 `factor_equivalent`（观测/审计，不影响 gate 单元判定） |
| `tests/test_qfq_factor_equivalence.py`（新） | 判据矩阵（见 §5） |

不改：gate 结构、watermark 提交语义、FP-6 判定域、FP-7 配额、任何表结构（与 C 解耦）。

## 4. 观测期联动（总调度裁定②的条件沿用）

首周每轮输出：`factor_equivalent` 放行数 vs 仍 blocked 数 vs 水位推进量，并列留痕；
出现「比值解释掩盖真实漂移」迹象 → 一键回退 env `QS_QFQ_FACTOR_EQUIVALENCE=0`（默认 1，双轨回退门，同 FP-6 模式）。

## 5. 验收判据（骨架级，②审后细化）

| # | 判据 | 形态 |
|---|---|---|
| A40 | 构造「qfq 一致 + 因子版本差」的 fresh/库内对 → 第二级放行，factor_equivalent=True | 正例 |
| A41 | 构造「真实数据漂移」（改一个 bar 的 close 且非比值可解释）→ 仍 block | **负向（防放宽）** |
| A42 | 覆盖检查不变：缺行/多行仍 block | 回归 |
| A43 | env=0 时行为与现状逐位一致 | 回退门 |
| A44 | 既有 reanchor 测试全绿 + 本批 46 例全绿 | 回归门 |
| A45 | 生产观察窗：blocked 单元显著转 factor_equivalent，watermark_held→committed 出现 | 生产取证 |

## 6. 与 C / FP-5 的关系

- B 不依赖 C（先用比值收敛法，不需要写入时快照）；C 落地后 B 的 r_t 可用精确快照替代收敛估计（增强非依赖）。
- C 与 FP-5 的 2.2 契约窗口协同评估（一次停启），A 另立终态。
