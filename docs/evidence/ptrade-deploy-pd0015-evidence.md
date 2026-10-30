# PD-0015 验收证据：状态文件路径去绝对化（`/home/fly` 被券商禁止）（2026-10-30）

> 方案：`docs/ptrade-deploy-statepath-design.md`（②审三裁通过：①准入 ②§11 两处反向修订 ③D4 新增检测型单列）
> ③实施：`config.py` / `templates/persistence_template.py` / `transformer.py` / `gates.py` / `contract_table.yaml` / `deliver.py` + 新测 8 用例 + 6 个 fixture 适配
> 本轮沉淀：PD-2026-0015

## 1. 根因（三层证据）

| 层 | 事实 | 证据 |
|---|---|---|
| 现象 | 上传产物即报 `更新策略失败，错误原因：/home/fly被禁止使用`，策略**存不进去** | 用户实盘 2026-10-30 20:09 |
| 代码根因 | `config.py` 默认 `DEFAULT_STATE_FILE` 硬编码研究目录；transformer 原样注入 PERSIST 两处 `open()` | 产物 L49/L913（改前） |
| 平台规则 | 官方逐字：「在部分券商里，会限制使用路径 /home/fly/，如果代码里面存在该路径字符，会提示无法保存，需要替换成：get_research_path()，效果一样」 | `knowledge/platform/ptrade/01-API文档主页/001-page.md:2573` |
| 范式 | `NOTEBOOK_PATH = get_research_path()` → `open(NOTEBOOK_PATH + 'xxx.pkl', 'wb')` | 同文 368-401 |
| 旁证 | ptrade 引擎**禁用 os 模块** ⇒ 只能字符串拼接、不引入子目录 | 同文 3341 |

## 2. 实施内容（D1–D5）

| 项 | 改动 |
|---|---|
| D1 | `config.py`：`DEFAULT_STATE_FILENAME = "{strategy_id}_state.pkl"`（纯文件名）；旧名保留一版别名 |
| D2 | `persistence_template.py`：新增 `PERSIST_NOTEBOOK_TEMPLATE`（`_PD_NOTEBOOK = get_research_path()` + `try/except` 兜底空串）；两处 `open()` 改 `_PD_NOTEBOOK + __STATE_FILE__`；模板**文档串也规避该字面量** |
| D2′ | `transformer.py`：新增 `_ensure_notebook_path()`，模块级注入解析块，带 `PERSIST` wrap 标记（P4 可整体剥离）；修正 intermediate 缺陷——模板解析为**单个 `ast.Try`**，故用 `wrap` 而非 begin/end（否则 mark 撞同一节点） |
| D3 | `config.py`：`state_file` 含 `/`、`\`、盘符或 `~` → **ConfigError**，文案指引改纯文件名（不静默改写） |
| D4 | `gates.py`：新增 `platform_path_policy` 检查 + `_PLATFORM_FORBIDDEN_PATH_PATTERNS` **六口径**（逐字 / 反斜杠 / 带空格 / 任意 `/home/<name>` / 全角 / `notebook/`），逐行全文扫描（含注释/docstring/字符串） |
| D5 | `contract_table.yaml` 补 `get_research_path()`（`backtest:true / trading:true / action:keep`，依据官方「可在回测、交易模块使用」）；`deliver.py` runbook 文案改为「研究根目录可写（文件名落在根目录下）」 |

## 3. 验收五项（方案 §5）

| # | 标准 | 结果 |
|---|---|---|
| 1 | `全球资产轮动` 重转后产物禁用路径 0 命中（六口径）+ PERSIST 含 `get_research_path()` + P0–P6 全 PASS | ✅ **status=PASS**（P0–P6 逐段 PASS）；六口径**全 0**；`_PD_NOTEBOOK = get_research_path()` 在位（L56/58）；两处 `open` 均为 `_PD_NOTEBOOK + 'strat_0c202b_state.pkl'`（L49/L919） |
| 2 | `P4_equiv = PASS`（不破等价） | ✅ **P4_equiv PASS** |
| 3 | 门禁负向：含禁用路径 → FAIL；纯文件名 → PASS | ✅ `test_platform_path_gate_flags_all_six_forms`（≥5 命中）、`test_platform_path_gate_clean_text_passes`、`test_platform_path_gate_scans_comments`（**注释命中必报**——实证坑固化） |
| 4 | 配置拒绝绝对路径 | ✅ `test_config_rejects_absolute_state_file`（文案含「纯文件名」+「get_research_path」）；`test_config_accepts_filename_only` 正向 |
| 5 | 全套件绿 + 矩阵 OK + 族机检 | ✅ deploy **129/129**（原 121 + 新 8）；契约矩阵 `OK`；PD 族 **15/15** 合规 |

## 4. 落实 §11 两处反向修订（②审裁定②）

| 修订 | 落实 |
|---|---|
| 门禁全文扫描（注释/docstring/字符串都算） | `_platform_path_findings` 按 `product_text.split("\n")` **逐行正则**扫描，无 AST 过滤；`test_platform_path_gate_scans_comments` 固化 |
| 生成注释不得含该字面量 | 模板文档串写「研究目录」而非路径；`gates.py`/`transformer.py` 的注释同样规避；重转产物六口径 0 命中为终证 |

## 5. 影响面（数据修正型 + 新增检测型，方案已声明）

- **产物代码**：仅 PERSIST 块路径写法变化（`_PD_NOTEBOOK + 文件名`），策略逻辑零变化；
- **既有 fixture**：6 个 YAML 的 `state_file` 由绝对路径改为纯文件名（**D3 生效后暴露，属预期配方适配**）；
- **旧产物**：仍含字面量者，重转后自动修复（本次重转已**覆盖掉此前手工临时件**）；
- **P5 新增 FAIL 能力**：含禁用路径的旧产物将转 FAIL ——「新增检测型」开始报告真相，②审已照准。

## 6. 六步进度

①方案 ✅ → ②审计 ✅（三裁）→ ③实施 ✅ → ④验收 ✅（本件）→ **⑤用户确认（待）** → ⑥随批统推。
