# 校验器误报修复证据：len(context.portfolio.positions) 误 BLOCK（2026-08-22）

## 1. 问题定义

`validate_local_strategy.py` 的 semantics 契约（PORTFOLIO-POSITIONS-EXACT-MATCH）将
`len(context.portfolio.positions)` 误判为"将 positions 包装进别名词典容器"，BLOCK 阻断
新策略转 PTrade。实测复现（直接调用契约检查路径）：

- `agent_workspace/wsgm10/strategy.py` L311、L370 → BLOCK
- 同文件 L319 `list(context.portfolio.positions.keys())` → 放行
- 同文件 L381 `len(positions)`（先赋值）→ 放行

同一语义仅因语法形态不同而一拦一放，坐实误报。

## 2. 根因

`_check_semantics_contract` 的内置白名单 `("dict", "list", "set", "tuple")`（L238-239）
只覆盖"容器包装类"内置，漏掉只读内置 `len()`。`len()` 返回 int，不可能包装/别名化容器，
不破坏 exact_key_match 语义——恰恰是门禁允许的"把 positions 当普通 dict 用"的标准写法。

讽刺性佐证：QS_PORTFOLIO_AUDIT 审计日志是项目自定契约（
`skills/quantstudio-strategy-compiler/scripts/validate_agent_strategy.py` L633-680 强制要求该行及字段；
`review_user_backtest_evidence.py` 视其为权威证据），策略按规范写审计反而被自家门禁拦截。

先例：commit `36756ac`（2026-08-11，zcode 阻断修复）修过同一规则的"子串匹配误伤"（
`tests/test_source_import.py` L512-513 记载 ETF动量.py `.XSHG` 被误 BLOCK 背景）；
validate_local_strategy.py L29-31 亦记载 2026-08-11 硬编码白名单缺 all/any 的同类误报修复。
本次属同类问题（白名单不全 → 合法写法误拦）。

## 3. 审计与裁定（六步流水线第 2 步）

- ZCode 审核意见（方案 B：修校验器白名单补 len）：**核实通过**，采纳时修正一处引用：
  "S1-6 持仓审计扩展点"系误引（issue_registry 中相关项实为 S1-9"basket drain 拒单未纳入
  QS_FILL_AUDIT 采集"，且性质是扩展点而非审计规范出处）；正式出处为上文两个 skill 脚本。
- 影响面补充（原意见未覆盖）：`len(context.portfolio.positions)` 直写形态全仓库 6 个真实
  文件、12+ 处（见 §6 清单），进一步否定"改 skill 模板绕开写法"（选项 A），肯定选项 B。
- 实施约束（审计通过附带，三条约）：①白名单只加 `len`，不做 sum/any/all/sorted 投机扩展
  （sum(dict) 本为 TypeError，无真实用例；保持单行 revert 性质）；②L237 注释同步为
  "容器包装 + 只读内置"；③负例测试断言到具体 rule_id 且 `ok is True`，对齐 Checkpoint 5/6 风格。
- 实施时点：F4 周末代码冻结（至周一 01:05，SNAP_003 周六 23:30 create 窗口在跑）期间，
  **用户裁定豁免（选项 A）**：立即实施、仅落工作树，不 commit/推送（提交待用户确认后执行）。
  第 5 步确认时用户明示**追认本次冻结期实施**——豁免依据：冻结范围=各任务代码分支，本改动与其
  零交集（不触碰快照/verify/SEGMENT-2 路径）；且仅工作树改动、冻结窗口内零提交。

## 4. 实施内容（六步流水线第 3 步）

文件 `quantstudio/strategy_compiler/validators/validate_local_strategy.py`：

```diff
-            if name in ("dict", "list", "set", "tuple"):
-                continue  # plain builtin re-wrap is fine
+            if name in ("dict", "list", "set", "tuple", "len"):
+                continue  # plain builtin re-wrap / read-only builtin is fine
```

（注释同步说明 len 为只读内置、不触发该门禁；语义契约与 alias_aware_apis 均未改动。）

文件 `tests/test_pr6a_validators_negative.py`：`TestValidateLocalNegative` 新增
`test_len_on_positions_is_read_only`——断言 `len(context.portfolio.positions)` 下
`ok is True` 且不出现 PORTFOLIO-POSITIONS-EXACT-MATCH。

## 5. 验收记录（六步流水线第 4 步）

| 验收项 | 修复前 | 修复后 | 结论 |
|---|---|---|---|
| ① 真实策略文件 0 BLOCK | 4 文件 7 处 BLOCK（全部为 len 误报） | 4 文件 `ok=True` 0 BLOCK | ✅ |
| ② 负例套件全绿 + 新用例 | 聚合 50 passed（负例文件 13） | **聚合 51 passed（负例文件 14）** | ✅ |
| ③ AliasDict 包装仍 BLOCK | BLOCK | 仍 BLOCK（`test_alias_aware_positions_rejected` 通过） | ✅ |
| ④ 存量复跑对比 | — | 前后 diff = 仅消除 7 处 len 误报，无新增拦截 | ✅ |

**计数口径（2026-08-22 ZCode 复验更正，消除歧义）**：上文 ② 为三文件聚合计数，逐文件实测：

- `tests/test_pr6a_validators_negative.py`：**13 → 14** passed（增量恰为新用例 1 个；即 ZCode 独立复验的 14 collected / 14 passed 口径）
- `tests/test_pr6b1_validators.py`：17 passed（未变）
- `tests/test_pr6a_case1_e2e.py`：20 passed（未变）
- 聚合 13+17+20=50 → 14+17+20=**51**，增量可完整归因到新用例，无隐藏计数

补充回归：

- `tests/test_source_import.py`：40 passed（含 2026-08-11 同规则误报背景集成用例）。
- 四形态实测矩阵：L311/370 形态（直调 len）修复前后 拦→放；L319 形态（list(keys)）持续放行；
  L381 形态（先赋值）持续放行；AliasDict 包装持续双 BLOCK（LOCAL-API-WHITELIST +
  PORTFOLIO-POSITIONS-EXACT-MATCH）。

验收①执行路径：经 `validate_local_strategy`（spec/IR 为 case1 基准，semantics 读取
`config/strategy_fidelity_gates.json`）对真实策略代码直接校验。

## 6. 影响面清单（含 len 直写形态的存量文件）

| 文件 | 位置 | 修复后 |
|---|---|---|
| agent_workspace/wsgm10/strategy.py | L311、L370 | 0 BLOCK |
| quantstudio/backtest/strategies/weekly_smallcap_growth_momentum_10_quantstudio.py | L311、L370 | 0 BLOCK |
| agent_workspace/wsgm10v2/strategy.py | L425、L487 | 0 BLOCK |
| agent_workspace/wsgm10v2/周频小市值成长动量（三层止损）.py | L425、L487 | 0 BLOCK（同构） |
| quantstudio/backtest/strategies/周频小市值成长动量（三层止损）.py | L425、L487 | 0 BLOCK（同构） |
| ptrade/probe_commission_ptrade.py | L54 | 0 BLOCK |

## 7. 文档同步核查

- `docs/strategy-compiler/strategy-ir-contract.md` L307（PORTFOLIO-POSITIONS-EXACT-MATCH 契约
  描述）为通用表述，未枚举白名单 → **无需变更**。
- 全 docs/ 检索 `dict.*list.*set.*tuple` / `alias-aware wrapping` / `wrapping the public
  positions` → 零命中，无任何文档文本因本次白名单扩展而失真。
- README/docs 策略工具箱/提示词工程章节不涉及该校验器白名单细节 → 无同步负担。

## 8. 回退条件

将 L238 白名单还原为 `("dict", "list", "set", "tuple")` 并删除对应负例测试即完全回退
（单行 + 单用例）。回退不影响任何真实包装拦截（len 与别名容器无交集）。

## 9. 流水线状态

- 第 1 步 方案：ZCode 产出，经本证据文档所载审计修正后成立 ✅
- 第 2 步 审计：DSH 核实通过（含 3 条实施约束 + S1-6→S1-9 引用修正）✅
- 第 3 步 实施：已完成（工作树，未 commit/推送）✅
- 第 4 步 验收：全部通过（本文档）✅
- 第 5 步 用户确认：✅ **完成（2026-08-22）**——用户明示**追认冻结期实施**，并授权周一 01:05
  解冻后执行提交与双仓库推送
- 第 6 步 双仓库推送：⏸ **待执行（已排定）**——周一 01:05 解冻后首批动作：commit
  **严格限定 3 文件**（validate_local_strategy.py、test_pr6a_validators_negative.py、
  docs/evidence/validator-len-false-positive-20260822.md）→ push 双远程（quantstudio-plus /
  quantstudio）→ 核对两远程一致。工作区存在大量其他线未提交改动，禁止连带。