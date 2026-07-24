# QuantStudio Strategy Compiler — 用户指南 (0.3.0-mvp)

## 1. 5 分钟安装

```bash
# 创建独立虚拟环境
python -m venv qs-env
qs-env\Scripts\activate

# 安装 wheel
pip install quantstudio-0.3.0+mvp-py3-none-any.whl jinja2 jsonschema pyyaml packaging

# 验证
qs-compile --help
```

## 2. Skill 安装

```bash
python skills/quantstudio-strategy-compiler/scripts/install_skill.py --agent zcode
```

验证：`quick_validate.py` 应输出 "Skill is valid!"

## 3. 如何向 AI 提出策略需求

直接用自然语言描述：
> "帮我生成一个双均线策略：5日均线和10日均线金叉买入、死叉卖出，标的 600570.SH，日线。"

AI 会自动：
1. 理解你的策略（R0）
2. 检查数据能力（R-1）
3. 生成 Strategy Spec（R2）
4. 展示 Spec + 假设 + 风险给你确认（R2.5）
5. **等你确认后** → 自动验证（R3）+ 生成策略包（R4）
6. 返回完整交付物（R5）

## 4. Spec 确认流程

AI 会展示：
- 策略 Spec 全文（标的内容、信号条件、调仓逻辑）
- 关键假设和近似项
- 数据能力边界（READY vs BLOCKED）
- 风险提示

**你必须明确回复"确认"后，AI 才会继续生成代码和交付包。**

## 5. 自动验证 + 自动 package 流程

用户确认后，Skill 自动串联：
- **orchestrator**（R3）：Spec → IR → 双 Renderer → 7 项验证 → run_card
- **qs-compile**（R4）：Spec → IR → dual Renderer → strategy package

用户不需要手动运行任何命令。

## 6. qs-compile 高级用户用法

```bash
# 直接生成策略包
qs-compile package spec.json --out output/packages

# 带 G2 reference linkage
qs-compile package spec.json --out output/packages --g2-frozen-dir tests/strategy_references/frozen

# 仅验证（不生成包）
python -m quantstudio.strategy_compiler.orchestrator spec.json --no-smoke
```

Python API:
```python
# Skill-local delivery script (not in the released wheel; lives in Skill scripts/)
# python skills/quantstudio-strategy-compiler/scripts/deliver_strategy.py spec.json --out output/strategy_deliveries
```

## 7. Spec 示例

见 `quantstudio/strategy_compiler/examples/case1_dual_ma_spec.json`（双均线策略）。

## 8. 输出文件解释

| 文件 | 说明 |
|---|---|
| `validation/run_card.json` | 验证总验收卡（stage + status + 各项验证结果） |
| `validation/capability_report.json` | 数据/执行能力就绪报告 |
| `validation/variant_consistency_report.json` | QS vs PTrade 14 维一致性 |
| `package/<id>__<ver>/manifest.json` | 包清单（版本、入口、artifact SHA-256） |
| `package/<id>__<ver>/<id>_quantstudio.py` | QuantStudio 平台策略代码 |
| `package/<id>__<ver>/<id>_ptrade.py` | PTrade 平台策略代码 |
| `DELIVERY_REPORT.md` | 统一交付摘要 |

## 9. manifest 和 digest 解释

- `manifest.json` 记录每个 artifact 文件的 SHA-256 digest
- manifest 自身不含 digest（自引用不可解）
- 可用 `sha256_file()` 逐项校验
- G2 linkage 用逻辑 artifact ID + sha256（可跨机器移植）

## 10. 错误码

| 场景 | exit code | 说明 |
|---|---|---|
| success | 0 | 策略包生成成功 |
| invalid/missing spec | 2 | Spec 文件不存在或 JSON 格式无效 |
| Golden Protection | 3 | 策略 ID 在黄金保护名单中 |
| G2 frozen linkage incomplete | 4 | G2 frozen artifact 缺失 |
| Traceback | 不应出现 | 所有已知错误均有稳定的 exit code + stderr |

## 11. data_digest_status = blocked 说明

本 MVP 版本的 `input_data_digest=null` / `data_digest_status=blocked`，含义：
- 使用 Hermetic/Synthetic 场景验证 pipeline 正确性
- 真实市场数据 digest **尚未完成**
- **不等于**真实数据 Fidelity 或真实行情 Reference 已验证
- 不影响 Hermetic pipeline 的编译、验证和包交付功能

## 12. 真实 Fidelity/Reference 后置说明

以下能力在 MVP 中**不可用**（deferred）：
- 真实市场数据回测的 Fidelity 对比
- 真实行情数据的 Reference 产物验证
- live QMT 连接
- resident daemon 数据接入

这些能力将在后续独立增量中实现，不影响本次 MVP 交付。

## 13. 用户交付检查清单

- [ ] `DELIVERY_REPORT.md` 显示 DELIVERED
- [ ] `validation/run_card.json` status = PASS 或 BLOCKED（已知后置）
- [ ] `package/` 目录含 manifest.json + 双 Renderer .py
- [ ] manifest artifact_digests 全部匹配实际文件
- [ ] 双 Renderer .py 可 ast.parse + compile
- [ ] data_digest_status = blocked（诚实标记）

## 14. 常见问题

**Q: qs-compile 不在 PATH？**
A: 确认已 `pip install quantstudio-0.3.0+mvp-*.whl`。开发环境可用 `python -m quantstudio.strategy_compiler.cli` 作为 fallback。

**Q: 冒烟回测 BLOCKED？**
A: 检查 `capability_report.json`。若能力 ≠ READY，orchestrator 诚实标记 BLOCKED（不是失败）。

**Q: 想覆盖已生成策略？**
A: 确认 strategy_id 不在黄金保护名单（etf_momentum / smallcap_guard / dual_ma_sample）。

## 15. 本 MVP 不包含

- 真实市场数据接入（live DuckDB 数据）
- live QMT 连接
- resident daemon 数据采集
- 真实 Fidelity 对比
- PR7（自动 Fidelity 闭环）
- GUI 集成
- 部署产品化（Docker/systemd/NSSM）
