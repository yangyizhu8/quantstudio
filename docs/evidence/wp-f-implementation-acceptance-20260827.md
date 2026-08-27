# WP-F 实施与验收证据：skill 模板升级 F1 六项 + B4（2026-08-27）

- 流水线：Step 1 设计 → Step 2 审计通过（两验收要求并入）→ Step 3 实施 → **Step 4 验收**
- 纪律：版本化 0.7.1→0.8.0 + 存量零触碰 + 双处模板同步（skill 源 + 包内运行时副本）

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `skills/.../SKILL.md` | release `0.8.0-f1-six-fold`；新增规则 R27-32（F1 六项 + B4 归并） |
| `skills/.../templates/quantstudio_daily.py.j2` | 头部 F1 行为框架注释（①②③④⑤⑥） |
| `skills/.../templates/ptrade_daily.py.j2` | 同上（双端模板一致） |
| `quantstudio/strategy_compiler/templates/*.j2` | **包内运行时副本同步**（渲染实际使用——`_PKG_TEMPLATES` 优先；审计发现包内优先未同步=缺口，已同步） |
| `docs/strategy_toolbox.md` | F1 六项正文（铁律同步义务） |

## 2. 审计验收要求落实

### 要求①（端到端冒烟为验收主体）
**真实 IR 渲染冒烟**（`output/wp-f-smoke/`）：载入已发布策略 `ashare_manual_pool_2d_momentum_top2` 的 strategy_ir.json → `render_quantstudio`/`render_ptrade` → 产物理式六项行为断言：
```
quantstudio F1: PASS（cost_basis/halt/资金派生/缓冲带/补差/审计版本化 全命中）
ptrade     F1: PASS（同上——双端模板一致）
```
**关键发现**：首跑 FAIL 3 项——根因 = `_PKG_TEMPLATES`（包内副本）优先于 skill 目录，我改的 skill 模板未生效；**同步包内副本后 PASS**（实施处置：双处模板保持同步）。

### 要求②（③④已有项复核证据）
| 项 | 复核实测 | 结论 |
|---|---|---|
| ③审计三件套 | SKILL R21 规则固化 ✅；**模板层 QS_REBALANCE_AUDIT = 0 处（缺口）** | 缺口小修 = 模板 F1③ 注释框架（行为由规则驱动；模板不硬编审计行） |
| ④资金派生 | 模板 total_value/portfolio.cash 4 处 ✅（R18 runtime_total_value 已固化） | 已满足，无需改动 |

## 3. 验收结果

| 项 | 结果 |
|---|---|
| F1 六项渲染断言（双端） | **全 PASS**（真实 IR 产物） |
| 相关套件回归 | **185/186**（1 失败 = test_eps_backfill 存量 TypeError 已登记，非本改动） |
| ptrade/quantstudio 模板一致性 | ✅ skill == 包内（Copy 同步） |
| 存量零触碰 | ✅ 6 策略文件未动（仅 skill/模板/文档） |
| 版本化 | ✅ 0.8.0（回退 = git checkout skill 目录） |
| 文档同步 | ✅ strategy_toolbox F1 正文（README/prompt 已有 QS_FILL_AUDIT 等基础引用） |

## 4. 回退

- 版本化回退：git checkout skill 目录（0.7.1 保留）+ 包内模板还原；
- 存量零触碰 → 无引擎/策略副作用。

## 5. 遗留登记

- 端到端 R0-R6 全流程生成（含 R5 回测）作为补充验收待用户实跑（冒烟已达渲染级）；
- prompt_engineering.md 未追加 F1 专段（已有 QS_FILL_AUDIT 引用；如审计要求可补）。