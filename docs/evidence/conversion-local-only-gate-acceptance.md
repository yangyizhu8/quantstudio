# 验收证据：转换管线通用门禁（local_only_symbols fail-closed BLOCK）

- 日期：2026-09-08
- 方案：docs/conversion-local-only-gate-design.md（审计通过 + 终审 2 项机械补强并入）
- 流水线：步骤3实施 ✅ → 步骤4验收（本文档）→ 步骤5用户确认 → 步骤6推送+trading 跟随

## 一、实施内容（§三逐项）

| 文件 | 改动 | 性质 |
|---|---|---|
| quantstudio/strategy_compiler/portability_rules.py | +13 行：LOCAL_ONLY_PASSTHROUGH_BLOCK = {get_index_day_bar} | 纯加法（人工 curated 集合） |
| quantstudio/strategy_compiler/source_import.py | +70 行：_block_call reason 参数（默认空=既有行为逐字不变）；_scan_calls 末尾 LOCAL_ONLY 分支 + 通用 else（仅裸 Name，_ELSE_BLOCK_EXCLUDED 排除）；convert() 前置 _collect_defined_names（补强1完整配方：FunctionDef/ClassDef/Import/Store-Name/arg）+ _collect_local_only_refs（补强2 Load 引用级）+ 引用级 BLOCK（按位置去重防双计） | 纯加法 |
| tests/test_conversion_gate.py（新增） | 8 用例：d/e/f/f2/h/i/i2/g | 终审 §四 + 补强测试 h/i |

## 二、终审三项限定落实
1. 通用 else 仅裸 Name（isinstance(node.func, ast.Name)）；Attribute 不进 else 维持现状 ✅
2. 裸 Name 排除集 = dir(builtins) ∪ defined_names（FunctionDef/ClassDef/Import/Store-Name/arg 全配方，全局集合宁多排不误杀，取舍写入代码注释）✅
3. LOCAL_ONLY_PASSTHROUGH_BLOCK 精确匹配对裸 Name 与 Attribute 双形式生效（obj.get_index_day_bar() 被拦）✅
_block_call reason 参数默认空=既有行为逐字不变 ✅

## 三、验收结果（§四全项）

1. **门禁测试 8/8 PASS**：d 误杀面零新增 / e 未知属性方法不 BLOCK / f+f2 双写法 BLOCK（消息含 API 名+本地专用原因）/ h 高阶模式（形参调用/lambda/for 绑定/walrus/except-as）零误杀 / i+i2 别名引用与传参 BLOCK / g 注册表一致性常设测试
2. **最强零衰减（主证据）**：6 canonical 策略修复前后重转产物 SHA-256 **逐位一致**（_sha_fixed.json vs _sha_head.json，ALL_BYTE_IDENTICAL: True）
3. **矩阵哈希**：check_fund_matrix.py --check PASS（OK: 契约矩阵键全部哈希一致 + MD 一致）
4. **回归套件**：test_source_import.py 50 passed / 7 failed（7 个为 HEAD 既有 include 失败，先前已 HEAD 对照证明）；test_ptrade_contract_compliance.py 全绿；test_conversion_gate.py 8 passed
5. **测试契约更新（显式记录）**：test_45（动态 date 表达式）断言更新——未定义裸名 _qs_day() 由旧"透传无错误"改为新契约"BLOCK 预期"；核心断言（date-wrap 逻辑/幂等/AST 合法）保留。此为 fail-closed 新契约的合法变更，非掩盖
6. **端到端**：恐慌抄底策略 PyQt 转 PTrade → BLOCK（行 99，消息含 API 名+本地专用原因+D4 指引），不再生成静默空仓产物 ✅

   **真实流程实证（2026-09-08 18:29+ 用户实跑）**：用户在 PyQt「转 PTrade」实际执行转换，弹窗
   「转换未通过」确认：run_card status=BLOCKED；BLOCK: get_index_day_bar() 无法自动转换：
   QuantStudio 本地专用 API（PTrade 平台不存在；探针未过前禁止转换，D4 序列）；含间接引用
   （别名赋值/传参）（行 99）。门禁在真实转换链路生效，未生成静默空仓产物（用户截图为证）。

## 四、配套处置
1. 既有缺陷产物退役：output/ptrade_export/恐慌抄底事件驱动逆向策略/ 三文件 *.RETIRED_DO_NOT_UPLOAD ✅
2. 平台"测试1"空仓产物：报用户停止/删除（用户执行）✅ 汇报
3. 两项独立登记（docs/pipeline-tech-debt.md，带推进状态）：%s 日志格式平台差异（排期推进）；fail-soft except 吞 NameError 教训（立项评估 fail-loud 边界）✅

## 五、文档同步
README.md / docs/strategy_toolbox.md / docs/prompt_engineering.md 补转换门禁表述；docs/get-index-day-bar-design.md §3.3 状态更新（门禁已落地，重写规则保留为探针后解锁路径）✅

## 六、副本跟随
QuantStudio-trading 同步 portability_rules.py + source_import.py + test_conversion_gate.py（SHA-256 逐位一致），commit c959de8 ✅

## 七、回退条件
撤销 portability_rules.py 常量 + source_import.py 三处新增 + test_conversion_gate.py（git revert 级）；纯加法，回退零风险。
