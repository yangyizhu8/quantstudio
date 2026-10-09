# M2a QMT 七模块实施会话基线（2026-10-08）

## 写前核对（铁律③：多会话共享工作区防护）

- `git status --porcelain -- quantstudio/strategy_compiler/ skills/quantstudio-strategy-compiler/`
  → **空输出 = 两域干净**（无其他会话在途改动叠加）。
- `output/generated_strategies/etf_hot_theme_rotation/` → 干净（自查用 spec 所在域）。

## 本会话待改文件清单（仅此清单，超出即违规）

| 文件 | 性质 |
| --- | --- |
| `quantstudio/strategy_compiler/templates/qmt_daily.py.j2` | 新 |
| `skills/quantstudio-strategy-compiler/templates/qmt_daily.py.j2` | 新（skill 回退目录双放） |
| `quantstudio/strategy_compiler/render_qmt.py` | 新 |
| `quantstudio/strategy_compiler/render.py` | 改（加法式：_PROFILE_TEMPLATE_MAP + 分发） |
| `quantstudio/strategy_compiler/orchestrator.py` | 改（gbk 写盘/target/portability 汇总/run_card） |
| `quantstudio/strategy_compiler/publish.py` | 改（QMT 发布分支） |
| `quantstudio/strategy_compiler/portability_rules.py` | 改（_QMT_API_WHITELIST + validate_qmt_portability） |
| `quantstudio/strategy_compiler/cli.py` | 改（package --target 透传） |
| `quantstudio/strategy_compiler/schemas/run_card.schema.json` | 按需扩（additionalProperties 限制时同批扩） |

## 硬约束备忘

- PTrade/quantstudio 既有行为零改变（byte-diff 验收）。
- 不碰 `source_import.py`（M2b 域）；不动 `_QS_FUNDAMENTALS_EXT`/`_QS_INDUSTRY_EXT`（矩阵哈希域）。
- 不提交不推送（六步流水线第 3 步实施；验收+用户确认后置）。
