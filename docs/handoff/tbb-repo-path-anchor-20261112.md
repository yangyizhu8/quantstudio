# TBB 物理路径锚登记件（2026-11-12 · 节点21）

> **为什么有本件**：历轮裁定均以「TBB」指称本线工作仓，但主图从未登记其物理路径；
> 而 R14 我把 G1 输入包落在 TBB、未给绝对路径，导致总调度按「主仓 + trading 两仓」检索 **0 命中**、
> 判为「申报与实态不符」。**根因不是文件不存在（实测存在且在提交内），是交付面错仓、且未给可核对路径。**
> 本件登记 TBB 物理路径锚与交付件绝对路径，供跨会话核对；**跨会话交付件一律带绝对路径**（本次整改立规）。

## 一、三仓关系与物理路径（实测）

| 代号 | 绝对路径 | git 分支 | remote | 角色 |
|---|---|---|---|---|
| **主仓** | `D:\miniQMT策略实盘\QuantStudio` | — | — | 共享核心层；**⑱/节点1 的消费面**；六步流水线与双推落位仓 |
| **trading（副本）** | `D:\miniQMT策略实盘\QuantStudio-trading` | — | — | 副本层（共享层零本地改动，跟随后步） |
| **TBB（本线工作仓）** | `D:\miniQMT策略实盘\trading-battle-back` | **master** | `https://github.com/yangyizhu8/trading-battle-back.git` | **节点21（㉑）日常落位仓**：采集 ETL、分钟/日线复权、门禁运维脚本、证据与方案件 |

- TBB 登记的 HEAD（本件落笔时）：`7c344496b9f7c8c0aa559c55b904d202d99d9d30`
  （`docs(qfq): T3 三日复核台账填 11-12 读数（真跑 YELLOW、自跳 0）+ 交付侧 cycle 97.5%% 与积压随附读数（节点21）`）
- **注意**：TBB 分支名为 **master**（非 main）；核对「是否已推」须以实际分支名取远端引用，不可默认 `origin/main`。

## 二、节点21 交付件绝对路径清单（含内容指纹，便于跨会话核对）

> 全部位于 TBB 仓；`sha256(16)` 为本件落笔时实测，**内容变更即指纹变化，便于识别漂移**。

| TBB 仓相对路径 | 字节 | sha256(16) | 用途 |
|---|---|---|---|
| `scripts/qfq_reanchor_minutes.py` | 45574 | `a77cf14f909afeef` | 段级计划器/无偏普查工具（--census / --plan） |
| `docs/design-l2-minute-reanchor-20261111-v2.md` | 23234 | `a1214cdbac924d0d` | L2 存量回放定稿方案（含 §十 裁定落定、G1 适配设计、归属更正） |
| `docs/design-r1-missing-factor-semantics-20261111.md` | 22334 | `d63714506c07ca6e` | T4+T5 方案件（②审通过；§七之三 含 T5-B 触发条件更正） |
| `docs/evidence/sample300-drift-census-20261111-v2.md` | 12254 | `4bc554a7bab94712` | 300 码无偏普查更正件（判据方向更正） |
| `docs/evidence/l2-step6-dense-sampling-20261112.md` | 3807 | `e7ab0f8c32fdc3f6` | L2 步⑥ 逐段加密抽样实测（64 段 / 1.22M 行 / 排除 840 天） |
| `docs/evidence/t3-skip-recheck-ledger-20261112.md` | 5211 | `d06727f364b9cedc` | T3 三交易日自跳复核台账（11-12 = 1/3） |
| `docs/evidence/p5-timeslot-calibration-20261111.md` | 7954 | `039213215e3ba818` | T1 核实 + T3 P5 时点定档 |
| `docs/handoff/g1-reanchor-changepoint-input-20261112.md` | 5855 | `70881ba56ee1a2dc` | G1 实施输入包（本已镜像至主仓同名路径） |
| `docs/handoff/register-main-repo-aligner-qfq-gap-20261111.md` | 3619 | `82876d021e6cf759` | 主仓 aligner 同族缺口转⑱登记 |
| `docs/batch4-submission-20261111.md` | 17281 | `bbfa32652aa24a2f` | 第四批入库预案 + 呈批件 + §1.4b 实际入库口径 |

**绝对路径前缀** = `D:\miniQMT策略实盘\trading-battle-back\`

## 三、交付纪律（本次整改立规）

1. **跨会话交付件必须带绝对路径**——只写仓相对路径（尤其只写「TBB」这类代号）等同不可核对；
2. **交付面按消费方定仓**：消费方在主仓（如 ⑱、节点1）⇒ 交付件**落主仓** `docs/handoff/`；
   仅本线自用的证据/方案可留 TBB，但呈报时**仍须给绝对路径**；
3. **镜像件须带来源锚头**（原件绝对路径 + 原件 sha256(16) + 说明「以哪一份为准」）；
4. **申报「已落盘」前必须回读存在性**（`Test-Path` / `ls` 实测），且**在消费方所在仓回读**——
   在自己的工作仓回读后即宣布完成，是本轮被指认的直接原因。

## 四、本件自身可核对锚

- 本件：`D:\miniQMT策略实盘\QuantStudio\docs\handoff\tbb-repo-path-anchor-20261112.md`（主仓，消费方可见）
- 同批镜像：`D:\miniQMT策略实盘\QuantStudio\docs\handoff\g1-reanchor-changepoint-input-20261112.md`
- 两件均落主仓 `docs/handoff/`；节点1 与本线共享同一工作树即可直接打开。
