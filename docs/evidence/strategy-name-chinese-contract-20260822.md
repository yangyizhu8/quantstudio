# 证据文档：生成策略 Skill 本地策略名一律中文（中文命名契约 2026-08-22）

- 变更类别：skill 层（agent-first R0-R6 管线命名契约），**不触碰框架内核**（engine / ptrade_api / provider / lifecycle 零改动）。
- 方案：用户批准（含审核 4 条补充：①Windows 尾点/尾空白正则陷阱 ②中文全流程回测走查 ③stem 冲突前置检查 ④implementation-status.md 核对）。
- 基线快照：`docs/handoff/baseline-strategy-name-chinese-20260822-202723.md`（实施前 git status / diff --stat）。

## 一、改动清单

### Skill（skills/quantstudio-strategy-compiler/）
| 文件 | 改动 |
| --- | --- |
| `schemas/agent_strategy_design.schema.json` | `strategy_name` 增加 pattern `^(?![_\s])(?!.*[.\s]$)(?=.*[\u4e00-\u9fa5])[^\\/:*?"<>|]{1,50}$`（≥1 汉字；不以 `_`/空白开头；**不以 `.`/空白结尾**——补充①；禁非法字符；≤50 字符） |
| `scripts/agent_skill_common.py` | 新增 `STRATEGY_NAME_PATTERN`、`strategy_naming_errors`（`STRATEGY-NAME-CONTRACT`）、`published_quantstudio_filename`（`<strategy_name>.py` 单一收口）、`strategy_name_conflict_errors`（`STRATEGY-NAME-CONFLICT`，补充③） |
| `scripts/create_agent_workspace.py` | `workspace_state.quantstudio_output` 改用中文文件名；state 记录 `strategy_name`；脚手架 docstring/`STRATEGY_NAME` 常量带中文名 |
| `scripts/user_backtest_flow.py` | `candidate_path(root, id, strategy_name=None)`（中文名候选 `<strategy_name>__candidate_quantstudio.py`，保留 ASCII 回退）；`ensure_candidate_path_is_safe` 同步 |
| `scripts/prepare_user_backtest_candidate.py` | 候选路径用中文名；发布前 stem 冲突前置检查；state 写入 `strategy_name` |
| `scripts/publish_agent_strategy.py` | quantstudio 正式目标 = `quantstudio/backtest/strategies/<strategy_name>.py`（纯中文无后缀）；发布前冲突前置检查；ptrade 目标维持 `<strategy_id>_ptrade.py` |
| `scripts/validate_agent_strategy.py` | R4 门禁加入 `STRATEGY-NAME-CONTRACT`/`STRATEGY-NAME-CONFLICT` BLOCK；CLI 新增 `--project-root`（否则自策略文件向上自动发现 strategies 目录；不可定位=无冲突对象，不 BLOCK） |
| `scripts/review_user_backtest_evidence.py` | R5 身份集合 `{strategy_id, strategy_name, candidate_stem}`；候选路径校验传中文名 |
| `scripts/retire_ptrade_runtime_evidence.py` | 候选路径校验经 state→design 双回退取中文名 |
| `SKILL.md` | 绝对规则 26（中文命名契约）；R2 增补命名契约段落；R5/R6 路径表述更新 |
| `references/agent-first-workflow.md`、`references/output-contract.md` | §6/§7 发布与候选路径更新为中文命名（§1 遗留编译器构建结构按范围裁定保留 ASCII） |

### 项目文档（AGENTS.md 同步纪律）
- `README.md`（AI 写策略段落：候选/正式输出路径 + 中文命名契约说明）
- `docs/strategy_toolbox.md`（用户PyQt候选文件段落）
- `docs/prompt_engineering.md`（输出分流 L19、落盘规则 L116/L369）
- `docs/strategy-compiler/implementation-status.md`（追加"Agent-first Skill 中文命名契约（2026-08-22）"变更记录——补充④；该文档此前无命名表述，已核实）

### 测试
- fixture 中文化：`test_agent_first_strategy_skill.py`（智能体通用轮动策略）、`test_target_aware_strategy_skill.py`（本地动态ETF轮动策略）、`test_ptrade_agent_validator.py`、`test_agent_portfolio_contract.py`、`test_agent_ptrade_history_runtime_shapes.py`、`test_ptrade_profile_registered_stock_apis.py`
- 文件名断言更新：上述文件中 `<id>_quantstudio.py` → `<中文名>.py`、候选文件名同步
- 新增 `tests/test_strategy_name_chinese_contract.py`：18 个非法名 BLOCK 用例（含补充①尾点/尾空格/尾全角空格/尾换行/前导空格/前导下划线/全部非法字符/超长）、冲突检查（手工中文文件、ASCII 存量、overwrite 豁免、目录缺失不 BLOCK）、R4 冲突 BLOCK、发布中文名 + 撞名前置拦截、R5 中文正式名 stem identity PASS

## 二、验收证据

### 1. quick_validate
```
$ python skills/quantstudio-strategy-compiler/scripts/quick_validate.py skills/quantstudio-strategy-compiler
Skill is valid!
```

### 2. 测试（2026-08-22 20:4x）
```
$ python -m pytest tests/test_strategy_name_chinese_contract.py tests/test_target_aware_strategy_skill.py \
    tests/test_user_pyqt_candidate_flow.py tests/test_agent_first_strategy_skill.py \
    tests/test_ptrade_agent_validator.py tests/test_agent_portfolio_contract.py \
    tests/test_agent_ptrade_history_runtime_shapes.py tests/test_ptrade_profile_registered_stock_apis.py \
    tests/test_agent_execution_funding_contract.py tests/test_pr6b1_install_skill.py -q
164 passed, 8 xfailed in 5.05s
```
（8 个 xfail 为既有 A4 is_dict 设计矛盾标记，与本次无关。）
守护测试：`test_reverse_spec.py`、`test_delivery_flow.py`、`test_pr6b1_install_skill.py` 全绿（遗留编译器链路零回归）。

### 3. 中文策略全流程回测走查（补充②，审核必做项）
脚本：`output/walkthrough_tmp/run_walkthrough.py`；日志：`output/walkthrough_tmp/walkthrough_log.txt`；退出码 **0**。
链路与断言：
1. R4 校验 PASS（命名 + 冲突门禁）；
2. R6 发布 → `quantstudio/backtest/strategies/中文命名走查策略.py`（走查临时工程目录）；
3. 真实 DuckDB（`data/quantstudio.db`）全流程回测：2026-06-01 ~ 2026-07-13，30 个交易日，close 撮合，逐日 `QS_FILL_AUDIT`（每日 2 持仓目标）；
4. 结果目录 `output/backtest_results/20260822_204431_中文命名走查策略` 创建成功（中文 stem 目录）；
5. 三件套读写正常：`config.csv` 的 `strategy_file=中文命名走查策略.py` ✓；`daily_stats.csv` 30 行 ✓；`trades.csv` 58 行 ✓（另有 benchmark/ptrade_metrics/round_trips 正常产出）；
6. PNG 报表导出（Qt offscreen）成功且非空：`回测收益曲线.png`、`基本信息.png`、`交易记录.png`、`日收益.png`、`绩效分析-收益分布.png`、`绩效分析-月度收益热力图.png`（中文文件名，位于中文结果目录）；
7. GUI 回测面板同款 glob 列表可见 `中文命名走查策略.py`（不以下划线开头不被过滤）。

> 注：走查脚本以 `os._exit(0)` 收尾，规避 Qt offscreen 平台解释器拆卸期的非零退出码（与走查结论无关，日志完整）。

## 三、存量失败归因（与本次改动无关，均已留证）
1. `tests/test_ptrade_profile_registered_stock_apis.py` 两个用例在 HEAD 即失败（fixture 使用已废弃 `execution_price_basis: pre_adjusted_price`，违反 2026-08-14 已提交的 schema 常量 `raw_trade_price`；已用 HEAD 版测试文件复现同样失败）。本次作为受影响测试的**顺带 fixture 对齐修复**（改回 `raw_trade_price`，见文件内注释），非行为变更。
2. `tests/test_strategy_spec_schema.py::test_all_frozen_schema_examples_validate`：遗留编译器 `run_card.example.json` 与 schema 常量（`run_card_version` 1.1）不匹配，HEAD 版测试文件同样失败；遗留 Spec/IR 路径按用户范围裁定不在本次改动内，保留现状待立项。

## 四、范围外（明确不做）
- 遗留 Spec/IR 编译器（`quantstudio/strategy_compiler/`）命名保持 ASCII（仅显式要求 legacy 复现时使用）；
- 存量已发布 ASCII 策略文件（`fall_reversal_quantstudio.py` 等 8 个）不重命名、不迁移（保住既有证据 hash 与引用）；
- PTrade 转换管线（PyQt tab / CLI）命名不扩大：中文 stem 已有 `rev_<md5>` 兜底 + `test_reverse_spec.py` 覆盖。

## 五、后续
- 用户已于 2026-08-22 验收通过并批准推送（约束：staging 白名单逐文件显式 add、严禁混入其他会话未提交改动；存量 ASCII 策略不迁移；PTrade 转换侧命名不扩大；存量失败按归因接受登记待立项；单一主题 commit）。
- 混合文件处理：8 个白名单文件在实施前已含其他会话未提交改动（README/SKILL.md/3 测试文件/prompt_engineering/strategy_toolbox/implementation-status），采用 `HEAD + 仅本次编辑` 的 index blob 构造（`git hash-object -w` + `git update-index --cacheinfo`），确保提交内容零混入。
- 推送前回退点：`git stash create` hash `0a176352f02f96ed4aeeef88bb0360fdc96c71a9`（零副作用）。
- 新增契约测试对共享 fixture 的 `execution_price_basis` 字段自包含对齐（`_cn_design`/`_align_basis`），不依赖其他会话尚未提交的 fixture 修复。
