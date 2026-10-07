# 件 B · S3 案例路由器 + S4 收敛台账 · 设计方案（六步①，2026-10-07 总调度批准启动 P2-α）

> 母授权：闭环计划 A-G 全批；本件②单独审。
> 类型：**新增检测型**（纯增量报告，不改 S1 既有判定行为）。

## 1. 问题定义

S1 三态分类（已对齐/归因候选/不可归因）是**粗分类**——归因候选到具体案件类型（CORP/POS/
ENG/API/FEE/DAT）仍靠人工深挖；跨策略×时间的残差无台账（收敛速率不可见、平台期无告警）。
闭环运转要求：案件路由自动化+收敛可度量。

## 2. 改动范围

### 2.1 S3 判别特征规则库（`scripts/align_triage_rules.py` 新建 + S1 挂钩）

**规则形态**（每案闭环回灌一条，首条种子由本件写入）：

```python
RULES = [
    Rule(
        id="CORP-01-merge-band",
        pattern="nav 单日突跳 + 该日该标的 preClose 缺口(ratio<0.99) + 委托/现金无分歧",
        # 判据（机检）：|nav日环比−平台| 单日突现 且 标的 preClose/close_front 比值落在 0.5~0.99
        case_type="CORP（公司行为：份额合并/折算）",
        evidence="docs/evidence/corp-action-etf-merge-2025-09-22.md",
    ),
    Rule(
        id="CORP-02-odd-lot-tail",
        pattern="委托分歧 = 持仓非 100 倍数的尾差截断（平台全量 vs 本地整手）",
        case_type="CORP（零股卖出）",
        evidence="docs/evidence/corp02-odd-lot-sell-acceptance-20261007.md",
    ),
    Rule(id="POS-diluted-sell", pattern="卖出后成本/盈亏判定异号 → 风控级联",
         case_type="POS（成本口径）", evidence="registry POS-01"),
    Rule(id="FEE-cash-first", pattern="现金先偏（净值后偏）", case_type="FEE", evidence="registry FEE-05"),
]
```

**挂钩点**：S1 `§5 三态分类` 后追加 `§6 案件路由（S3）`——用 `--as-json` 的结构化字段
（first_div/order_first_div/cash_first_div/order_div_count/nav_dev）跑规则库匹配，输出
候选案件类型+证据链接；无匹配 ⇒ `新案（进入根因证实，铁律 L2 人审）`。

**回灌纪律**：每案六步闭环时，registry 结案行同步追加 Rule 条目（docs 一句话+方案件
②审通过即回灌——闭环第⑥环的制度化入口之一）。

### 2.2 S4 收敛台账（`knowledge/alignment-convergence.md` 新建）

| 策略 | 口径 | 日期 | 残差（末日） | 分歧组 | verdict | 产物 |
|---|---|---|---|---|---|---|
| 四象限 | diluted | 2026-10-07 | 3.88pp | 34+ | 归因候选 | （CORP-01 前） |
| 四象限 | diluted | 2026-10-07 | 2.23% | 18 | 归因候选 | 20261007_115719 |
| 四象限 | diluted | 2026-10-07 | **0.013%** | 3（尾差级） | **已对齐** | 20261007_210721 |
| 四象限 | moving_avg | 2026-10-07 | 基线 2.08% | — | 基线登记 | 20261007_211056 |

- **更新触发**：每策略每次 S1 跑（A 件 aligned 态落盘同路径自动带出）；
- **收敛指标**：残差时间序列（每策略）+ 跨策略矩阵（维度×策略）+ 新案率（registry 新立案数/月）；
- **平台期告警**：同策略连续 M=3 轮残差不动 ⇒ 台账标注「平台期：疑似未发现系统性漏洞，
  建议扩探针」；
- **维度宣布联动（G 件）**：连续 N=3 新策略首跑「已对齐」⇒ 维度候选（台账自动统计）。

### 2.3 registry 回灌格式约定

CORP 案件结案行追加 `triage_rule: <id>` 字段——S3 规则与案件账本双向可溯。

## 3. 影响面

- S1 输出多一节（§6）+`--as-json` 多字段——既有字段/判定不变（新增检测型）；
- 纯增量脚本+文档；策略/引擎零改动。

## 4. 验收标准

1. 规则库单测：四象限三轮历史 JSON（CORP-01 前/后/CORP-02 后）回放——路由结论与人工
   归因一致（CORP-01 前轮 → CORP-merge-band 命中；后轮 → odd-lot-tail 命中；末轮 → 已对齐）；
2. 台账落盘+格式校验（表头/触发脚本）；
3. S1 既有输出回归（前 5 节逐字节不变）。

## 5. 回退条件

写前快照；S1 挂钩段独立函数可单点摘除；台账/规则库纯新增文件可整体删除。