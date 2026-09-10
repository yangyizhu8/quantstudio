# 验收证据：design_metadata 可信解析层（docs/design-metadata-auto-profile-design.md v4）

> 日期：2026-09-09 · 六步流水线步骤4 · 终审条件通过（审计 5 + 复审 3 + 终审 2 全并入）
> 状态：IMPLEMENTED_ACCEPTED —— 待用户确认（步骤5）后提交+双仓库推送+trading 同步门

## 一、实施清单（终审第五节全项）
| 文件 | 改动 | 性质 |
|---|---|---|
| quantstudio/strategy_compiler/design_metadata.py（新增） | 可信解析层：候选归属链（STRATEGY_ID 定位 + quantstudio_output 路径匹配）；生命周期四字段校验；路径安全（resolve 防逃逸）；canonical SHA 校验；三字段一致性；schema 固定源（仓库 skills/.../schemas/agent_strategy_design.schema.json）；profile 组合一致性校验；结构化 DesignMetadataResolution（9 状态枚举） | 纯加法，只读无副作用 |
| source_import.py | SourceImportResult + design_metadata_resolution 字段；convert_source 构造前解析（消费者条件执法：仅 profile-sensitive API 才要求可信；RESOLVED 权威/显式冲突 BLOCK/NOT_FOUND_LEGACY+显式可用） | 纯加法 |
| orchestrator.py | _assemble_source_report + _build_run_card 输出 design_metadata_resolution；_build_run_card 加参数 | 纯加法（可选字段） |
| schemas/source_import_report.schema.json | 新增可选 design_metadata_resolution 属性（additionalProperties 未收紧——终审第四节） | 纯加法 |
| schemas/run_card.schema.json | 新增可选 design_metadata_resolution 属性（顶层 additionalProperties=false 需显式声明） | 纯加法 |
| publish_agent_strategy.py | agent-managed 发布 state.update 补 formal_publish_allowed=True（框架缺口修复，防台账不一致） | 修复 |
| ptrade_export_tab.py | 转换前自动解析 design：RESOLVED 自动带出+显示来源；异常提示；legacy 下拉 | 纯加法 |
| cli.py / worker | engine_profile 未指定 → convert_source 自动解析（零改动即受益） | 透传 |
| tests/test_design_metadata.py（新增） | 10 用例（终审 §六/§七全项） | 新增 |
| panic 档案纠错 | ledger formal_publish_allowed false→true（含 ledger_corrections 记录）；design 删 510760 旧段（A-11 保留） | 档案纠错，策略源码零改动 |

## 二、验收结果（全项）
1. **新单测 10/10 PASS**：RESOLVED（可信链全通过）/ NOT_FOUND_LEGACY / STRATEGY_ID ledger 损坏→INVALID_JSON（含 SID 上下文）/ 无关损坏 ledger 不污染 / 生命周期不一致（fpa=False+PUBLISHED）→LEDGER_MISMATCH / profile 组合矛盾（daily+1m）→INVALID_PROFILE / legacy 无 profile-sensitive 零 BLOCK / 显式冲突→ENGINE_PROFILE_METADATA_CONFLICT+BLOCK / panic 自动→RESOLVED+SHIM / publish 修复回归（fpa=True 落值）
2. **6 canonical 策略重转产物 SHA-256 逐位一致**：ALL_BYTE_IDENTICAL: True（无 profile-sensitive API 策略产物零变化——消费者条件执法主证据）
3. **全套转换测试**：183 passed / 7 failed（7 个全部为 HEAD 既有 include 测试，先前已对照归因——非本批回归）
4. **check_fund_matrix --check**：PASS
5. **端到端**：orchestrate_source(panic, 不传 profile) → RESOLVED daily-bar-v1 → 转换成功（run_card + source_import_report 均记录 resolution，BLOCK=[]）
6. **schema 正反用例**：synthetic valid design（panic 模板）过 schema → RESOLVED；daily+1m 组合矛盾 → INVALID_PROFILE
7. **四类入口一致**：convert_source / orchestrator / CLI（透传 None → 自动解析）/ PyQt tab（自动识别+显示来源）——已验证 convert+orchestrator，CLI/tab 经相同代码路径

## 三、关键决策记录
- **消费者条件执法**（复审阻断 A）：仅源码用 get_index_day_bar 时 engine_profile 才须可信；否则 metadata 缺失/legacy 不阻断——6 策略字节级一致的前提
- **可信 design 权威**（终审阻断 2）：显式 profile 与可信 design 冲突 → BLOCK（防 minute 门禁绕过）；NOT_FOUND_LEGACY 才允许显式
- **formal_publish_allowed 框架缺口**（终审补强 B）：publish_agent_strategy.py agent-managed 发布未置 True 是台账错误根因（panic 实证）；已修复发布流程 + 定向纠错 panic ledger
- **schema 事实**：run_card.schema.json 顶层 additionalProperties=false；source_import_report.schema.json 未设置——本次仅新增可选属性，不收紧后者（终审第四节）
- **lbdt HASH_MISMATCH**（未处置，登记）：lbdt ledger canonical_sha256 与当前发布文件不一致（历史台账陈旧，源码无 STRATEGY_ID）——候选归属链正确拦截为 HASH_MISMATCH（安全）；另行立项处置

## 四、回退条件
- 撤销 design_metadata.py + source_import/orchestrator/schema/publish_agent_strategy/tab 改动 + 新单测 + panic 档案纠错
- 纯加法 + 可选字段，显式 legacy 路径保留，回退零风险；共享核心纪律已执行（stash 回退点 ff3af8b、edit 后 diff 自检）


## 五、shim v2 平台实跑修复（2026-09-09 21:32 平台日志归因 + 修复）

**平台实跑新错误（不再是 QS_INDEX_BAR_FAIL/NameError）**：
- 错误：KeyError: 'pctChg'；idx = Empty DataFrame（Columns: []，仅 Index 含日期）
- 根因：shim v1 内部自合成 pctChg 找平台原始列名 preclose（重命名为 _qs_preclose），
  但转换产物中 get_history 是注入的 _QS_HISTORY_WRAPPER（2026-09-01 平台实证），已把
  preclose→preClose、money→amount 归一为本地列名 → shim 找不到 _qs_preclose → pctChg
  未合成 → fields=['pctChg'] 过滤后列全空 → 策略 idx['pctChg'] KeyError。
- 修复（shim v2，框架层模板，策略源码零改动）：shim 请求**本地字段**（含 pctChg/amount），
  由 wrapper 完成 amount→money 映射 + pctChg 剔除 + preclose 基列注入 + (close/preClose−1)×100
  返回合成 + 列名归一；shim 只消费 wrapper 归一结果，消除第二事实源（审计原则）。
- 验证：
  1) 本地等价仿真（wrapper 归一后 DataFrame）→ shim 返回 pctChg 列 [-0.2911, -1.8498, -3.046]
     （07-16/17 与探针双端一致）；index/trade_date 正确；
  2) 6 canonical 策略重转 SHA-256 逐位一致（shim2 vs v3base，ALL_BYTE_IDENTICAL: True）；
  3) 全套测试 184 passed / 7 failed（7 个 HEAD 既有 include，先前对照归因）；
  4) check_fund_matrix --check PASS；
  5) panic 新产物重建：run_id ...20260909215126、dmr=RESOLVED daily-bar-v1、shim v2 特征全对。
- 测试契约更新：test_f_daily_handle_data_shims / test_get_index_day_bar_shim_homology
  断言更新为 v2 契约（field 含 pctChg/amount、无 _qs_preclose 自合成）——合法契约变更。
- 回退条件：shim v1 分支存档 _shim_v1_branch.txt；恢复 v1 即回退（不推荐——平台已证伪 v1）。


## 六、get_index_stocks date 归一修复（2026-09-09 21:54 平台日志归因）

**平台实跑新错误（shim v2 后，07-17 信号触发但选股空仓）**：
- 07-17 QS_SIGNAL 正常（shim v2 修复生效，指数日线读取成功）；
- 但 get_index_stocks fail + note=empty_after_filters counts={'L1_constituents': 0} → selected=0；
- 根因：策略 get_index_stocks('000905.SS', date=prev) 中 prev=context.previous_date 为
  datetime.date 对象，平台契约要求 YYYYmmdd → 调用异常 → except 吞掉 → 成分股空。
- 修复（框架层转换管线参数归一，策略源码零改动）：_normalize_ptrade_contract_calls 增加
  get_index_stocks 分支 _rewrite_index_stocks_date——复用 _asharess_date_normalized_value 模式：
  date 归一为 YYYYmmdd（str → replace('-','')；date/datetime/动态 → strftime('%Y%m%d') 三元包装）；
  NORM-INDEX_STOCKS-DATE action。
- 验证：panic 重转产物 get_index_stocks(_INDEX_CSI500,
  date=prev.replace('-','') if isinstance(prev,str) else prev.strftime('%Y%m%d')) ✅；
  全套测试 184 passed / 7 failed（7 个 HEAD 既有 include）；panic 新产物重建 run_id ...20260909221006。
- 回退条件：撤销 _rewrite_index_stocks_date + 分支（纯加法）。


## 七、PTrade 平台实跑闭环（2026-09-10，真实平台验证）

**运行**：策略"测试1"（恐慌抄底转换产物，含 shim v2 + get_index_stocks date 归一），
平台回测 2026-07-01~07-31，初始资金 100 万（R0 确认）。

**逐项对照（平台 vs 本地 R5）**：
- 07-17 信号触发：QS_SIGNAL 正常（指数日线 shim v2 读取成功）✅
- 选股：selected=50 tradable=50（get_index_stocks date 归一生效）✅
- 买入：buy_submitted=50 -> 成交 41 只（9 只 QS_ZERO_ORDER delta_below_one_lot 高价股不足一手拒单；
  与本地"9 只涨停跳过"殊途同归）→ **positions=41 gross=0.7114 与本地完全一致** ✅
- 锁仓：20 交易日纪律正确（07-18~07-20 到期才清）✅
- 清仓：07-20 sell_submitted=31 + 10 只跌停顺延 -> 07-21 全清 positions=0（与本地同构）✅

**本轮平台差异 4 处全部解决（框架层，策略源码零改动）**：
1. 转换门禁：本地 API fail-closed BLOCK（get_index_day_bar 无重写前禁转）；
2. shim 消费 wrapper 归一：shim v2 请求本地字段，由 _QS_HISTORY_WRAPPER 完成字段映射/合成/归一；
3. get_index_stocks date 归一：date 对象/YYYY-MM-DD -> YYYYmmdd（PTrade 契约）；
4. 回测资金：平台初始资金须按策略设计（100 万）——10 万时 50 只每只 1940 < _BUY_VALUE_FLOOR(2000) 全 skip；
   这是平台回测参数配置（非代码），探针 BR-B4 floor=50 实证 + 资金改 100 万后 BR-B3 buy=50 验证。

**闭环状态**：PTrade 平台实跑与本地回测行为一致（41 只持仓/锁仓/到期清仓/跌停顺延全对齐）——
D4 探针序列（指数日线等价物 + preclose）与转换重写规则全链实证完成。
