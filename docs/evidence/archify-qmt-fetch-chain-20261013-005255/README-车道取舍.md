# 3 车道取舍说明（影响面图 · 工具约束依据）

> 生成时点：**2026-10-13 00:52** ｜ 基线版本：**`e269fc8`**（含 D-27a 工作树改动）
> 载体：`index.html`（archify v3.0.1，workflow / readable-v2 / showcase）
> 关联：`index.finalize.json`、`index.browser-check.json`、`visual-check/index.visual-check.json`

---

## 1. 为什么最终是 3 个车道（而不是 Plan 初稿的 6 个）

初稿按「hist / valuation / history / info / audit / gates」6 车道设计（30 节点）。在 `--quality showcase` 下被三个**联立**硬约束挡住，最终压到 3 车道（17 节点）：

| # | 门 | 约束 | 实测读数 |
| --- | --- | --- | --- |
| ① | `deliver/composition/viewport-height` → `browser-check/viewer/viewport-overflow` | canvas 宽高比须 **≥ 1.55**，否则 Reader 不能收窄，页面纵向溢出（任何视口都判失败） | 6 车道时 viewBox 872×920 → 比例 0.95；页面 1546px vs 视口 900px（overflow 646px） |
| ② | `validate/composition/desktop-readability` | canvas 宽须 **≤ 930px**（否则 6px 源字投影 < 6px 不可读） | 显式 `viewBox:[1426,920]` 时 scale=0.652 → 投影 3.91px < 6px，被反噬 |
| ③ | ① ∧ ② | 宽 ≤ 930 ∧ 宽/高 ≥ 1.55 ⟹ **高 ≤ 600** | workflow 单车道实测 164–176px ⟹ **最多 3 车道**（3 道 = 550 ✓；4 道 ≈ 652 ✗） |

**推论（可复用规律）**：archify 的 workflow 类型在 showcase 质量下，**车道数上限为 3**；6 车道必挂，与图内容质量无关。

## 2. 官方示例实测复现（旁证：非本图特有问题）

对技能自带的官方示例原样复跑（无任何改动）：

```
node bin/archify.mjs finalize workflow examples/incident-response.workflow.json \
     examples/incident-response.html --quality showcase --json
→ failedStage=deliver  code=composition/viewport-height
  "This 720x900 canvas (ratio 0.8) declares no intrinsic-height fit and is below the 1.55
   wide ratio ... a certain visual-check failure."
```

即 **官方 6 车道示例自身也栽在此门**——该约束是工具契约，不是本图的排版缺陷。

## 3. 内容取舍（哪些语义离开了主图、去了哪里）

| 内容 | 去处 | 理由 |
| --- | --- | --- |
| 本轮两处改动落点（`hist 收敛` D-27b、`逐只现价` D-27②） | **保留在主图**（hist 车道 / valuation 车道） | 二者是本轮允许写入清单的唯一两处落点，须在图上可见 |
| 审计落点（D-23/D-24/D-26/D-27 审计行） | **保留**（独立「审计落点与自检」车道） | D-24/D-26 卷补件的核心 |
| history（E1 行情）路径、info（D-23 状态）路径 | 节点 tag + 卡片文字 | 不在本轮写入清单内，边际收益低 |
| 六步流水线闸门（写前快照 / 六策略重转 / 矩阵哈希 / 端测判据） | 「闸门与回退」卡片 | 属本轮 Plan ⑧ 验收判据，不属取数链结构面 |

## 4. 六轮诊断修复链（成本记录，供下次直接对齐）

| 轮 | 报错码 | 处置 |
| --- | --- | --- |
| 1 | `repository-evidence/root-required` | 带行号证据须加 `--repo-root` |
| 2 | `repository-evidence/origin-mismatch` | `meta.repository.url` 须与该 checkout 的 git origin 一致（本仓 origin=quantstudio-plus） |
| 3 | `workflow/column-capacity` | 改用 `schema_version: 2`（readable-v2）；v1 固定列距 80/70px 容不下 92px 节点 |
| 4 | `workflow/node-overlap` ×16 | 一个 (lane, col) 只能一个节点 → 按车道重排、全部独占格位 |
| 5 | `proper-crossing` / `ambiguous-corridor` / `layout/constraint` ×33 | 全道内横链 + 仅同列竖直跨道；压缩标签（≤6 汉字）与副标签（≤23 字符） |
| 6 | `route-preset-conflict` | 向下跨道用 `route:"drop"`（bottom→top）；向上跨道预设常不可满足 → 删边、语义移入 tag |

## 5. 权威方向声明（§10 写死，不因出图而放松）

- **允许写入清单仍是机检闸**；本图只是该清单的**可视化依据**。图与清单不一致 → **以清单为准并触发重画**。
- 图是**时点性审核对象、非活文档**；实施中若发生结构变更（新增挂载点、组件边界变化）→ **工单级变更须重画重审**。
- 图与文本工件冲突 → **以文本工件为准**。
- **不替代机检**：包名/服务名体检、允许写入清单核对、源码双侧核对照旧执行。
- 人看不可省：四门全过只证明机器可读，**不证明「一眼看懂影响面」**——该判断由用户目视 `index.html` 后给出。
