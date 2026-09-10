# 方案 v4（最终版）：转换管线自动解析 engine_profile（可信 design 元数据解析层）

> 六步流水线步骤1 · 审计通过方向 + 复审通过方向 + 终审条件通过（两项补强已并入）
> 方案文档：docs/design-metadata-auto-profile-design.md（v4，审计 5 + 复审 3 + 终审 2 + 框架缺口 1 全并入）

## 一、问题定义
get_index_day_bar PTrade 重写 shim 用 include=True，安全性依赖日线收盘路径；转换器须知 engine_profile 做机器门禁。
source_import 源码直转丢失设计层元数据——engine_profile 是"转换管线感知设计元数据"通用能力的第一消费者。

## 二、已通过设计（审计+复审确认，不变）
- 权威解析链：受控一层扫描 agent_workspace/*/workspace_state.json；ledger 正式输出路径 + canonical_sha256 + 同目录 design 可信链；strategy_name/id 仅作一致性验证；
- 可信 design profile 不得被显式覆盖（冲突 BLOCK）；结构化状态；profile 组合校验；metadata 模块只读；清理 panic design 510760 旧表述（策略源码零改动）。

## 三、终审 2 项补强（本稿新增）

### 补强 A：损坏 ledger 的"属于当前策略"判定规则（候选归属链）
- 先 AST 提取当前策略源码静态 STRATEGY_ID="<literal>"；
- 若存在该字面量 → 仅检查受控路径 agent_workspace/<STRATEGY_ID>/workspace_state.json：
  - 路径存在但 JSON 损坏 / design 缺失 / schema 失败 → 返回对应结构化错误（INVALID_JSON/SCHEMA_UNAVAILABLE 等），**不得降级为 legacy**；
  - 路径不存在 → 再以所有可正常解析 ledger 的 quantstudio_output 精确路径匹配；
  - 两种方式都无候选 → 才返回 NOT_FOUND_LEGACY；
- STRATEGY_ID 仅用于定位候选，**不构成可信证据**；最终仍须过 path/hash/design/ledger 全链校验；
- 动态赋值/重复赋值/非字符串 STRATEGY_ID 不可信 → profile-sensitive 场景返回结构化失败，不猜测。
（取证：panic/canslim/low_turnover/lbdt 源码均含静态 STRATEGY_ID 字面量——候选归属链可行）

### 补强 B：formal_publish_allowed 生命周期一致性（+ 框架缺口修复）
- 可信正式 ledger 须四字段全绿：stage==PUBLISHED 且 publish_status==PASS 且 quantstudio_output_status==GENERATED 且 formal_publish_allowed==True；
- 不一致组合（如 PUBLISHED/PASS/GENERATED 但 formal_publish_allowed=false）→ **LEDGER_MISMATCH**（不是 NOT_FOUND_LEGACY），禁止忽略字段让 panic 自动解析成功；
- **panic 现状取证**：formal_publish_allowed=false 是 agent-managed 发布流程的台账落值缺口——publish_agent_strategy.py:317-329 发布成功 state.update 未置 True（该字段仅 user_pyqt 模式 review 置 True，create_agent_workspace:245 骨架默认 False）；发布实际已完成（PUBLISHED/PASS/GENERATED + canonical SHA 与发布文件逐位一致 6ddae987 + 已推送 10ce73c）；
- **处置两件套**：
  1. 定向修正 panic ledger formal_publish_allowed=false → true + 记录档案纠错原因（不改策略源码）；
  2. **框架缺口修复**：publish_agent_strategy.py agent-managed 发布分支在 state.update 中补 formal_publish_allowed=True（防未来 agent 策略重蹈台账不一致）；此为框架层缺陷修复，纳入本批（六步流水线协同）。

## 四、schema 事实修正 + 状态枚举扩展（终审第四节）
- run_card.schema.json 顶层 additionalProperties=false（属实）；source_import_report.schema.json 顶层**未显式设置**该约束——本次只显式新增 design_metadata_resolution 属性，**不顺带收紧** source report 的 additionalProperties（避免混入无关契约行为变化）；
- design_metadata_resolution.status 枚举覆盖全部实际状态：
  RESOLVED / NOT_FOUND_LEGACY / AMBIGUOUS / INVALID_JSON / HASH_MISMATCH / LEDGER_MISMATCH /
  INVALID_PROFILE / SCHEMA_UNAVAILABLE / UNSUPPORTED_DESIGN_VERSION；
- 字段可选 → 报告版本不变；对象出现时 status/source_sha256/reason 必填，其余按状态允许 null。

## 五、实施范围（终审第五节：实际是完整五层 + 附加项）
PyQt tab → PtradeExportWorker → orchestrate_source → convert_source → SourceConverter；
另含：design_metadata.py；SourceImportResult；两个报告组装器；两份 schema；CLI；新测试；panic design 档案纠错；
**+ publish_agent_strategy.py formal_publish_allowed 框架缺口修复（补强 B）**。实施与回退清单按实际文件逐项列出。

## 六、验收（v3 全部 + 终审 §六八项）
1. 新单测 tests/test_design_metadata.py（v3 列表 + 终审追加）：
   - 当前 STRATEGY_ID 对应 ledger 损坏 → INVALID_JSON，显式 profile 不得绕过；
   - 其他 workspace 损坏 ledger → 不影响当前 legacy 策略；
   - STRATEGY_ID 与 ledger/design ID 不一致 → LEDGER_MISMATCH；
   - 无 STRATEGY_ID 无精确输出路径候选 → NOT_FOUND_LEGACY；
   - formal_publish_allowed=false + PUBLISHED/PASS → LEDGER_MISMATCH；四字段全绿 → RESOLVED；字段缺失 → 结构化失败；
   - profile-sensitive API 使用损坏本策略 ledger 不能通过显式 profile 绕过；
   - 无关损坏 ledger 不污染 legacy 转换；
   - metadata resolution 的 source/design/schema SHA-256 与实文件逐位一致；
   - PyQt/CLI/orchestrator/direct convert_source 四类入口行为一致；
2. 回归：6 canonical 策略重转产物 SHA-256 逐位一致 + 全套转换测试全绿（既有失败与 HEAD 对照归因）；
3. 端到端：orchestrate_source(panic, 不传 profile) → RESOLVED daily-bar-v1 → 转换成功（ledger 纠错后）;
4. publish_agent_strategy.py 修复单测：agent-managed 发布后 formal_publish_allowed==True；
5. 文档同步 + check_fund_matrix --check PASS。

## 七、边界
不改引擎/策略/shim；不递归扫描；不"取最新"消歧；不为 legacy 猜 profile；不读用户级 skill 副本作运行时依赖；
design_metadata 只读不写回；自动解析只服务 engine_profile 门禁；panic design 只删 510760 旧段（A-11/确认原文/时间戳/参数不变）。

## 八、回退
撤销 design_metadata.py + 五层接入 + 两 schema/两报告扩展 + publish_agent_strategy.py 修复 + 新单测 + panic 档案纠错；
纯加法 + 可选字段，回退零风险。实施纪律：stash create+store、改共享核心文件后即时 git diff 自检、精确 add、验收+用户确认后才提交+双仓库推送+trading 同步门。

## 九、流程
本稿 v4（全部并入）→ 您批准即实施（步骤3）→ 验收（步骤4）→ 用户确认（步骤5）→ 推送+trading 跟随（步骤6）。
