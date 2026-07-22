# QuantStudio Skills — 策略编译 Skill 使用说明

本目录包含 **quantstudio-strategy-compiler** Skill：把策略想法 / Spec 编译成
经验证、可回测的 QuantStudio + PTrade 双平台代码。

> **Skill 版本**: 0.2.0-pr6b1（2026-07-23）。支持 Spec → IR → 双平台渲染 →
> 7 项验证 → run_card 全链路；本地冒烟回测在能力就绪时自动执行。

---

## 1. 前置条件（移交前请确认）

Skill 依赖整个 QuantStudio 项目，**不是独立可运行**的。客户机器需具备：

| 依赖 | 说明 | 检查命令 |
|---|---|---|
| Python ≥ 3.9 | 推荐 3.11 | `python --version` |
| QuantStudio 包（editable 安装） | 提供编译器、回测引擎、DuckDB 适配器 | `cd QuantStudio && pip install -e .` |
| DuckDB 数据库 | 含 stock_daily / etf_daily / stock_minutes 等行情数据 | DB 路径见 `quantstudio/_paths.py` |
| PyYAML | quick_validate 解析 SKILL.md frontmatter（已写入 pyproject 依赖） | `pip install pyyaml` |
| 一个 AI 编程智能体 | ZCode / Claude Code / Codex / CodeBuddy 等，用于调用 Skill | — |

**首次安装**（项目根目录）：
```bash
pip install -e .
```

---

## 2. Skill 目录结构

```
skills/quantstudio-strategy-compiler/
├── SKILL.md              # Skill 操作手册（智能体读这个决定如何工作）
├── scripts/
│   ├── inspect_capabilities.py   # R-1 能力探测（DB 是否就绪）
│   ├── validate_strategy_spec.py # Spec 校验（schema + 5 条 timing 规则）
│   ├── install_skill.py          # 安装 Skill 到智能体目录（多客户端支持）
│   └── quick_validate.py         # SKILL.md 结构校验（内置，无外部依赖）
├── templates/            # 4 个 Jinja2 模板（daily/minute × QS/PTrade）
├── schemas/              # 3 个 JSON Schema（strategy_spec / capability_report / run_card）
└── references/           # 11 份契约快照（按需加载，勿全读）
```

---

## 3. 安装 Skill 到你的智能体

不同智能体从不同目录读取 Skill。**任选一种方式**：

### 方式 A：用安装脚本（推荐）

```bash
# 自动探测已安装的智能体目录
python skills/quantstudio-strategy-compiler/scripts/install_skill.py

# 或指定目标智能体
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent claude
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent codex
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent zcode
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent codebuddy
```

各智能体的安装目标目录：

| 智能体 | 用户级目录（--agent） | 项目级目录（手动复制） |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `<项目>/.claude/skills/` |
| Codex | `~/.codex/skills/` | — |
| ZCode | `~/.agents/skills/` | — |
| CodeBuddy | `~/.codebuddy/skills/` | — |

> Claude Code 用户推荐**项目级**安装：把整个 `skills/quantstudio-strategy-compiler/`
> 复制到 `<项目>/.claude/skills/quantstudio-strategy-compiler/`，这样 Skill 跟着
> 项目走，不依赖用户主目录。

### 方式 B：手动复制

直接把 `skills/quantstudio-strategy-compiler/` 整个目录复制到你智能体的
skills 目录即可（跳过 install_skill.py）。复制后无需 quick_validate（项目源
已是 PASS 状态）。

### 验证安装成功

```bash
python skills/quantstudio-strategy-compiler/scripts/quick_validate.py <你安装到的目录>
# 应输出 "Skill is valid!"
```

---

## 4. 编译一个策略（完整流程）

Skill 的工作分 5 个阶段（智能体会引导你走完）：

1. **R0 想法解析**：你用自然语言描述策略，智能体拆解为 股票池 / 指标 / 买卖条件。
2. **R-1 能力探测**：运行 `inspect_capabilities.py` 检查 DB 数据是否就绪。
   - `READY` → 继续；`BLOCKED/PLANNED` → 停下，报告缺什么数据。
3. **R2 Spec 起草**：生成 `strategy_spec.json`，用 `validate_strategy_spec.py` 校验。
4. **R2.5 用户确认（硬门）**：**展示 Spec 给你，经你明确确认后才生成代码。**
5. **R3 编译 + 验证**：确认后运行 orchestrator 端到端编译。

### R3：运行编译管线

```bash
python -m quantstudio.strategy_compiler.orchestrator <你的spec.json> \
    --start 2026-01-01 --end 2026-04-29
```

产物写入 `output/generated_strategies/<strategy_id>/`：

| 文件 | 说明 |
|---|---|
| `strategy_spec.json` | 输入 Spec（回显） |
| `strategy_ir.json` | 中间表示（Spec→IR） |
| `<id>_quantstudio.py` | QuantStudio 平台代码 |
| `<id>_ptrade.py` | PTrade 平台代码 |
| `capability_report.json` | 能力探测报告 |
| `variant_consistency_report.json` | QS vs PTrade 14 维一致性 |
| `run_card.json` | **总验收卡**（含 stage / status / 各验证结果） |

`run_card.json` 的 `stage` 字段反映管线进度：
- `SPEC_ONLY`：Spec 校验失败（IR 未构建）。
- `STATIC_VALIDATED`：IR + 渲染 + 静态验证已跑（无论通过/阻断）。
- `SMOKE_EXECUTED`：冒烟回测已跑（或被能力门禁诚实阻断）。

`status` = `PASS`（全绿且冒烟过）/ `BLOCKED`（有验证阻断）/ `FAILED`（冒烟引擎失败）/ `PARTIAL`（静态过、冒烟未跑）。

---

## 5. 能力边界（诚实标记）

**支持**（PR6b-1）：
- 操作：`ma`、`pct_change`、`cross`、`rank`、`top_n`、`bottom_n`
- 股票池：`single_stock`、`manual_list`（多标的，3+ 只）
- 引擎 Profile：`daily-bar-v1`（READY）、`minute-bar-v1`（READY）
- 7 项验证：schema/timing、lookahead、hard_filters、API 可移植性、双版本一致性、冒烟回测

**未支持**（PR6b-2 范围，调用时会明确报错，不静默降级）：
- `index_constituents` 指数成分股票池
- Factor ops（`zscore` / `winsorize` / `neutralize` / `combine`）
- 成本透传（Spec.costs 未渲染进 set_commission/set_slippage，用引擎默认）
- 止损止盈（无 stop_loss/take_profit）
- tick 回测（能力模型不变量：tick 在 v1 永不 READY，PR9 范围）

---

## 6. 常见问题

**Q: 安装时报 "quick_validate.py not found"？**
A: 不应发生——quick_validate.py 已内置在 Skill 的 scripts/ 里。若仍报错，确认
你是从项目根目录运行，且整个 `skills/quantstudio-strategy-compiler/` 目录完整。

**Q: orchestrator 跑到冒烟回测时卡住很久？**
A: 冒烟回测在真实 DuckDB 上跑引擎，大数据集可能需要几分钟。可用 `--no-smoke`
跳过冒烟只做静态验证。

**Q: 冒烟回测 status=BLOCKED 但我以为是 READY？**
A: 检查 `capability_report.json` 的 `overall_execution_status`。若 ≠ READY，
orchestrator 按 R6 规则不调引擎，诚实标记 BLOCKED（不是失败）。

**Q: 想覆盖一个已生成的策略？**
A: 确认其 `strategy_id` 不在黄金保护名单（`etf_momentum` / `smallcap_guard` /
`dual_ma_sample`）——这些有冻结基线，禁止自动覆盖。非保护 ID 改 Spec 重跑即可。

---

## 7. 相关文档

- Skill 操作手册：`skills/quantstudio-strategy-compiler/SKILL.md`
- 实现报告：`docs/strategy-compiler/pr6b1-implementation-report.md`
- 契约真源：`docs/strategy-compiler/`（权威文档，references/ 是其快照）
- 已知限制：`skills/quantstudio-strategy-compiler/references/known-limitations.md`
