# QuantStudio Skills — 策略编译 Skill 使用说明

本目录包含 **quantstudio-strategy-compiler** Skill：把策略想法 / Spec 编译成
经验证、可回测的 QuantStudio + PTrade 双平台代码，并自动交付完整策略包。

> **Skill 版本**: 0.3.0-mvp（2026-07-24）。G1-G4 Hermetic MVP 路线完成。
> 支持 Spec → IR → 双平台渲染 → 7 项验证 → run_card → **strategy package 全链路**。
> Skill 自动编排 orchestrator 验证 + qs-compile 交付，普通用户无需手动串联命令。

---

## 0. 普通用户使用方式（最简单）

1. 安装 QuantStudio wheel：`pip install quantstudio-0.3.0+mvp-py3-none-any.whl`
2. 安装 Skill 到你的 AI 智能体（见 §3）
3. 向 AI 说："帮我生成一个双均线策略。"
4. AI 自动完成：理解 → 能力检查 → Spec → 展示 → 等你确认 → 验证 → 交付包
5. 拿到 `validation/` + `package/` + `DELIVERY_REPORT.md`

**普通用户不需要手动执行 orchestrator 或 qs-compile。** Skill 会自动串联两者。

高级用户/自动化脚本可直接使用 CLI（见 §4）。

---

## 1. 前置条件

| 依赖 | 说明 | 检查命令 |
|---|---|---|
| Python ≥ 3.9 | 推荐 3.11 | `python --version` |
| QuantStudio wheel | `pip install quantstudio-0.3.0+mvp-*.whl` | `qs-compile --help` |
| DuckDB 数据库（12 GB） | 压缩包另行分发 | 解压到 `data/quantstudio.db` |
| 一个 AI 编程智能体 | ZCode / Claude Code / Codex 等 | — |

### 独立虚拟环境安装（推荐）

```bash
python -m venv qs-env
qs-env\Scripts\activate
pip install quantstudio-0.3.0+mvp-py3-none-any.whl jinja2 jsonschema pyyaml
qs-compile --help  # 验证安装成功
```

---

## 2. 三层架构

| 层 | 角色 | 谁用 |
|---|---|---|
| **Skill** | AI 工作流：理解自然语言 → Spec → 确认 → 编排验证 + 交付 | 普通用户（通过 AI） |
| **orchestrator** | 验证引擎：Spec → IR → 双 Renderer → 7 validators → run_card | Skill 自动调用 |
| **qs-compile** | CLI 交付：Spec → IR → dual Renderer → strategy package | Skill 自动调用；高级用户直接用 |

---

## 3. 安装 Skill 到你的智能体

```bash
python skills/quantstudio-strategy-compiler/scripts/install_skill.py
# 或指定智能体
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent zcode
```

验证：`python skills/quantstudio-strategy-compiler/scripts/quick_validate.py <安装目录>`

---

## 4. 高级用户 CLI 直接使用

```bash
# 完整交付（验证 + 包）
qs-compile package <spec.json> --out <dir> [--g2-frozen-dir <dir>]

# 仅验证（orchestrator）
python -m quantstudio.strategy_compiler.orchestrator <spec.json> [--start] [--end] [--no-smoke]
```

Python API：
```python
# Skill-local delivery script (not in the released wheel; lives in Skill scripts/)
# python skills/quantstudio-strategy-compiler/scripts/deliver_strategy.py spec.json --out output/strategy_deliveries
```

---

## 5. 完整流程（R0-R5）

| 阶段 | 动作 | 谁做 |
|---|---|---|
| R0 | 理解自然语言策略 | Skill (AI) |
| R-1 | 能力检查（inspect_capabilities） | Skill (AI) 自动 |
| R2 | 生成 Spec + 校验 | Skill (AI) 自动 |
| R2.5 | 展示 Spec → **用户确认（硬门）** | 用户 |
| R3 | orchestrator 验证 | Skill (AI) 自动 |
| R4 | qs-compile 生成 strategy package | Skill (AI) 自动 |
| R5 | 返回交付摘要 + DELIVERY_REPORT | Skill (AI) 自动 |

### 交付目录结构

```
output/strategy_deliveries/<strategy_id>/
├── validation/              ← orchestrator 产物
│   ├── run_card.json
│   ├── strategy_ir.json
│   ├── capability_report.json
│   ├── variant_consistency_report.json
│   ├── <id>_quantstudio.py
│   └── <id>_ptrade.py
├── package/                 ← qs-compile 策略包
│   └── <id>__0_3_0-mvp/
│       ├── manifest.json
│       ├── <id>_quantstudio.py
│       ├── <id>_ptrade.py
│       └── ...
└── DELIVERY_REPORT.md       ← 统一交付摘要
```

---

## 6. data digest 与真实 Fidelity 后置说明

- `data_digest_status = blocked`：真实市场数据 digest 未完成（Hermetic MVP）
- 真实 Fidelity/Reference 验证：**deferred**（后置）
- 本次 MVP 不接入真实市场数据 / live QMT / resident daemon
- 以上后置项**不影响** Hermetic pipeline 的编译、验证和包交付

---

## 7. 相关文档

- Skill 操作手册：`skills/quantstudio-strategy-compiler/SKILL.md`
- 用户指南：`docs/strategy-compiler/USER_GUIDE.md`
- 发布说明：`quantstudio/strategy_compiler/release/RELEASE_NOTES.md`
- 项目状态：`docs/strategy-compiler/implementation-status.md`
