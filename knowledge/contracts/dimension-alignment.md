# 契约档案 · 维度对齐档案（Dimension Alignment）· 件 G

> 来源：闭环计划件 G（`docs/alignment-loop-architecture-plan.md` §件 G）；机制：连续 N≥3 新策略首跑
> S1 全绿 ⇒ 该维度宣布对齐、转维护态（只响应平台演化）。**宣布=用户确认动作**（对齐
> [`../strategy-compiler/alignment-lifecycle.md`](../../docs/strategy-compiler/alignment-lifecycle.md)「aligned 唯一硬门」语义）；
> 机检脚本 `scripts/check_dimension_alignment.py` **只产提示不自动宣布**（退出码 0 无提示／3 有候选或告警／1 异常）。
> 台账联动：[`../alignment-convergence.md`](../alignment-convergence.md) §3。

## 维度条目模板（六要素，形态同构于 [`api-semantics.md`](api-semantics.md) 建档模板）

```
### DIM-nn <维度名>
- ① 维度名 + 覆盖 API 集 + 维度归属判据（成员策略判定标准）
- ② 对齐判据口径（S1 verdict「已对齐」/「残差达标」，阈值）
- ③ 连续对齐计数（固定表格：策略 id / 首跑日期 / verdict / 证据指针）← 机检轻量机读面
- ④ 宣布状态：候选（N<3）/ 已宣布·维护态（N≥3）
- ⑤ 平台演化响应记录（宣布后遇平台变更 → graduated → re-aligned 复验）
- ⑥ 关联指针（S4 台账行 / registry 案号 / 契约档案交叉引用）
```

---

### DIM-01 ETF 日线策略域（首例候选）

- **① 维度名 + 覆盖 API 集 + 归属判据**：ETF 日线策略域——覆盖 API 集：`get_history` / `order_target_value` /
  `get_positions` / `get_position` / `get_etf_list_local`（转换期固化）。**归属判据**：标的池为 ETF
  **且**信号源为日线（`engine_profile=daily-bar-v1`）**且**消费上述 API 集者计入本维度；仅标的含 ETF
  但信号源为分钟、或不消费该 API 集者不计入。
- **② 对齐判据口径**：S1 报告 verdict「已对齐」或「残差达标」（末值偏差/总资产 < 0.5%）；
  **宣布后该维度阈值收紧**（收紧值与该宣布确认动作同批裁定——件 G 风险 4 触发锚点）。
- **③ 连续对齐计数（机读面，固定表格）**：

  | 策略 id | 首跑日期 | verdict | 证据指针 |
  |---|---|---|---|
  | 四象限ETF轮动策略 | 2026-10-07 | 残差达标（0.013%） | `docs/evidence/corp-action-etf-merge-fix-acceptance-20261007.md`；`corp02-odd-lot-sell-acceptance-20261007.md`；台账 §种子数据 |

  > **走查如实记录（G-3）**：四象限 S1 报告产物**未随案归档**（`output/` 全树无 align 相关 .json；
  > 其终判产物目录无 `alignment/` 子目录）——以**台账行 + 验收证据件**为准，标注数据源=人工清单。
  > 其余 ETF 日线策略（`etf_hot_theme_rotation`/`bbi_etf_rotation` 等）**暂无** S1 首跑记录 → 如实标
  > 「暂无」，**不虚增 N**。

- **④ 宣布状态**：**候选**（N=1 / 阈值 3）——台账 §3 原文「待 2 个新策略首跑全绿凑 N」互证。
- **⑤ 平台演化响应记录**：（空）——宣布后启用；当前无。
- **⑥ 关联指针**：台账 `alignment-convergence.md` §3（维度宣布联动）/§种子数据；契约档案
  `etf-share-merge.md`（CORP-01/02 语义边界）、`api-semantics.md`（覆盖 API 契约）；registry 案号
  DAT-16 / CORP-01 / CORP-02。

## 口径定义（脚本与档案共同依据）

- **「首跑」定义**（件 G rev2 必改 2，**待用户裁定**）：**同策略 id 只计首次合格记录**——首次合格=
  该策略首份 S1 终判为「已对齐」或「残差达标」；重跑/参数寻优轮次不重复计数。（对母计划「首跑全绿」
  的口径澄清，依据台账 §3 既有读法）
- **「连续」重置规则**：维度成员策略 S1 终判非「已对齐」或该维度立新案 → 计数**清零重计**。
- **N 阈值**：3（母计划原文 N≥3）。
- **宣布动作**：用户确认（脚本只提示）。
