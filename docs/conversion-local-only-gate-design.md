# 转换管线缺陷修复方案：local_only_symbols API 未在 source_import 门禁生效（get_index_day_bar 透传致 PTrade 空仓）

> 日期：2026-09-08 · 六步流水线步骤1（方案）
> 触发：恐慌抄底策略转 PTrade 后回测全程空仓（QS_INDEX_BAR_FAIL code=%s err=%s，每日重复）

## 一、问题定义

用户将本地策略「恐慌抄底事件驱动逆向策略」经 PyQt 转 PTrade（策略名"测试1"）后，真实 PTrade 平台回测日志显示：
- 每日 15:00 输出 `QS_INDEX_BAR_FAIL code=%s err=%s`（%s 未替换 = 平台 log 不兼容 printf 风格双参）
- 信号永不触发 → selected=0 → 全程空仓（2026-07-01~07-31 无任何交易）

**根因（取证确认）**：转换管线 source_import 的 API 分类（portability_rules.py）对 `get_index_day_bar` 无任何处理——
不在 DENY_REMOVE / DENY_SHIM / PTRADE_REGISTERED_WARN / INJECTED_WRAPPER_NAMES / MYTT / ASHARE / _BLOCK_API_NO_FUNCTION 任何集合
→ 调用**原样透传**到 PTrade 产物（产物第 2475 行仍直接调用 get_index_day_bar）。
该 API 为 QuantStudio 本地注入 API（docs/get-index-day-bar-design.md，2026-09-08 框架修复新增，已登记 skill local_only_symbols 27 项之一），
**PTrade 平台不存在** → 平台 NameError → except 捕获 → QS_INDEX_BAR_FAIL → 永不触发信号。

**为何校验器没拦**：validate_agent_strategy.py 的 TARGET-LOCAL-EXTENSION-BAN 在设计/源码层生效，但 **PyQt「转 PTrade」tab 直接调 source_import 转换管线，不经过该校验器**。转换管线自身的门禁（portability_rules）未包含 get_index_day_bar。

## 二、改动范围（框架层，通用修复，策略零改动）

### 落点：quantstudio/strategy_compiler/portability_rules.py（转换管线 API 分类权威源）

**方案 A（采纳）**：新增转换级"本地专用 API 拦截"集合 `LOCAL_ONLY_API_PASSTHROUGH_BLOCK`（或复用命名），
将 `get_index_day_bar` 纳入，转换时对该类调用 **fail-closed BLOCK**（拒绝转换并输出明确错误：该 API 为本地专用，
PTrade 转换需走 D4 探针 + 重写映射，见 docs/get-index-day-bar-design.md §3.3）。

**实现要点**：
1. portability_rules.py 新增 `LOCAL_ONLY_PASSTHROUGH_BLOCK: frozenset = {"get_index_day_bar"}`（与 skill local_only_symbols 的登记一致，注释引用同源）；
2. source_import.py `_scan_calls` 分类链中，在 DENY_REMOVE 之前插入该集合检查 → `self._block_call(node, name)`（复用既有 BLOCK 机制，拒绝转换 + 明确消息）；
3. 单测：新增用例验证含 get_index_day_bar 的源转换 → BLOCK + 错误消息包含 API 名与原因；同时验证既有策略（fall_reversal 等 6 策略）转换不受影响（零回归）。

**不做**（明确边界）：
- 不为 get_index_day_bar 写 PTrade shim/重写（该 API 无平台等价物，探针未过前禁止转换——终审 §3.3③）；
- 不修改策略源码（恐慌抄底策略是 QuantStudio-only，转 PTrade 本就不应发生；转换门禁将使其显式失败而非静默空仓）；
- 不涉及 %s 日志问题（独立小项，见 §四）。

## 三、同步范围（铁律：README + docs 引用文档）

- README.md 策略工具箱/转换章节：补充"本地专用 API 转换门禁"表述；
- docs/strategy_toolbox.md：get_index_day_bar 条目补注"转换 fail-closed BLOCK（local_only_symbols 门禁）"；
- docs/prompt_engineering.md：提示词工程补注"本地专用 API 不可转 PTrade，转换器 BLOCK"；
- docs/get-index-day-bar-design.md：§3.3 从"登记为后续项"升级为"转换门禁已落地"状态更新；
- QuantStudio-trading 副本：按跟随制同步 portability_rules.py + source_import.py。

## 四、附：%s 日志格式问题（独立小项，另案处理）

真实 PTrade 平台 log 不兼容 `log.info('fmt %s', arg)` printf 双参风格（产物日志显示 %s 字面未替换）。
转换产物中策略源码的 log 调用未被改写。此项为独立缺陷（影响日志可读性、不影响交易逻辑），
单独立项（转换器对 log 调用做格式改写或文档说明平台约定），本次不混入主修项（铁律：修复不捆绑）。

## 五、验收标准

1. 新单测：含 get_index_day_bar 的源 → source_import 转换 BLOCK（错误消息含 API 名 + 原因）；PASS
2. 回归：6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频三层止损）重转全 PASS 或 BLOCK 均为既有预期；
3. 相关测试套件全绿（test_source_import / test_ptrade_contract_compliance 等）；
4. 恐慌抄底策略在 PyQt 转 PTrade 时显式失败并给出可操作错误（而非静默生成空仓产物）；
5. 文档同步完成（README + 3 docs + trading 副本跟随）。

## 六、回退条件

- 回退 = 撤销 portability_rules.py + source_import.py 改动（两文件，git revert 级）；新增单测一并撤销；
- 纯加法改动（新增集合 + 新增分支 + 新单测），不触碰既有分类逻辑与任何策略源码，回退零风险；
- 回退点：git stash create -u + store（共享核心纪律）。

## 七、流程声明

本方案 → 审计（ZCode/用户）→ 实施 → 验收（§五全项）→ 用户确认 → 双仓库推送 + trading 副本跟随。
