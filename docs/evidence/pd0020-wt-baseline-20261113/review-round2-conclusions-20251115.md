# 独立②审结论（两件 · 2026-11-15）

> 两件**分别**由独立审查方（ZCode，只读、未改文件、未跑测试、未触生产库）出具。
> 本件为**结论落盘**；执行方**不自审、不自放行**。

---

# 第一件：订单修复（预算不可得 vs 真实零预算）—— **通过**（附三项生效条件）

## 逐项结论

| 项 | 结论 | 关键依据 |
|---|---|---|
| ① 三态核验 | **通过** | `_qs_cash_budget` 产出域只有 `{None} ∪ ℝ`；收窄后 `None` 落 `_qs_split_order(cash_avail=None)`⇒`budget=value`（L926/L934），**与基线执行路径逐语句一致**；`0`/负 ⇒ 拒单三件套同时成立（L1500-1506/L1708-1714）；`>0` ⇒ `min(cash,value)` 且勾稽 ≤ 预算×缓冲。**NaN 不可达**（三路探测均以 `> 0` 为返回条件 L1134/L1153/L1163）⇒ **无第三未定义态** |
| ② 卖出路径 | **通过** | `_qs_ov_buy` 守卫使 `value<=0` 短路不可达（L1703/L1706）；`order_target_value` 的 `_delta<=0` 在 L1477-1486 早于预算取数 L1494 return；`_qs_ledger_release`（L1332-1386）与 `_cash`/拒单分支**零数据依赖**；双端本地侧 `ptrade_api.py:3193-3196` 减仓走原生、不咨询预算 |
| ③ 双端逐笔一致 | **通过**（**判据措辞须修正**） | 本地接线层 `_qs_wire_order_*` **无拒单分支、无 `_qs_cash_budget`**，`cash_avail=None` ⇒ 与模板同走 fail-open ⇒ 收窄后双端恢复同构；`test_wire_local_vs_template_homology` 的 `assert len(t)==3` **本身已拒绝「两边都空」** |
| ④ 最小差分 | **通过**（附两项随附要求） | `is None or _cash` 全文件仅 L1498/L1706 两处；无花括号／无新 `_QS_`／无 `globals().get` 持引用／零 API 增删；RECONCILE 外部引用仅 `transformer.py:891` 一处，计数器保留 |

## 三项生效条件（并入④验收清单）

1. **注释同步**：L1495-1497 / L1699-1701 两处注释**必须同 commit 重写** ——
   现注释逐字写着「零/**不可得**预算 ⇒ 显式拒单」「原 fail-open 按目标全额放行 = 超买根因」，
   收窄后即成**假陈述**（这正是本次缺陷的成因类别：码-注自相矛盾）。
   建议一并简化死掉的 `'unavailable' if _cash is None else 'zero_budget'` 三元式。
2. **§4.2 判据措辞修正**：**真零态双端合法地产出零订单**
   （模板：拒单三件套；本地：`cash_avail=0.0` ⇒ `budget=0` ⇒ `([],0)` ⇒ no-op）。
   ⇒ 真零态判据改为「**双端皆空 ＋ 模板侧拒单三件套 ＋ 本地侧 no-op 证据**」；
   「序列非空」要求**仅**适用于不可得/正常两态。
3. **R1 残余通道登记 ＋ R2 知识条目措辞同步**（见下）。

## 三项风险登记

| # | 风险 | 定性与处置 |
|---|---|---|
| **R1（实质）** | 「**账户真零现金**」仍会被 fail-open 吞掉：`_qs_cash_avail` 三路探测**全部以 `> 0` 为返回条件** ⇒ context 在位但 `portfolio.cash == 0.0`（满仓/资金耗尽）时不返回 0，继续探测后归入 None ⇒ fail-open ⇒ 全额下单 | **既有契约的已知限制、非本方案引入**；但方案三态表「`_cash == 0` ⇒ 真零拒单」**实际只覆盖 `cash − reserved == 0`** 这一条可达路径 ⇒ **方案 §三影响面表须补记该残余通道**；后续**独立立项**（方向示例：`context` 在位即返回 `float(cash)` 含 0）——**改变 M2 契约语义，禁止夹带本轮** |
| **R2（复发源）** | 知识条目 `knowledge/ptrade-deploy/PD-2026-0020.md` L5 `fix_target` 逐字含「…**None fail-closed**」⇒ 与在码契约（`_qs_cash_avail` docstring「**禁止：取不到禁下单**」、L1487-1489 M2 fail-open）**正面冲突**；而该条目讨论节 D1-D5 **均未**把「None ⇒ fail-closed 拒单」立为已裁定语义 | **a-2 缺陷极可能正源于此**；不改则未来按 KB 实施者会**再次复刻同缺陷** ⇒ ③实施同 commit（或随批）修订该措辞为「None fail-open（钳制不生效）＋真零(≤0)拒单」并在讨论节补记本次判据（属**知识面动作**，不违最小差分） |
| **R3（登记）** | 真零态双端**观测面不对称**：模板侧显式拒单带计数/日志，本地侧 noop 无计数（返回值双端一致皆 None、序列皆空） | 属 PD-0018/PD-2026-0020 模板侧增强的既定差异，随判据措辞修正登记即可；正预算态双端预算源不同（模板 `cash−reserved` vs 本地裸 `account.cash`）亦为 PD-0018 F3(a) 既定保守设计，**非本次引入** |

---

# 第二件：A-1 契约同步（`is_trade()` 公有化后的断言同步）—— **不通过（暂缓）**

## 逐项结论

| 项 | 结论 | 关键依据 |
|---|---|---|
| ① `initialize` 调用上下文 ＋ 执行级验证设计 | **通过**（附两项边界要求） | `_scan_calls` if/elif 链：`is_trade` 已不在 DENY_REMOVE ⇒ 落 L4328 `PTRADE_REGISTERED_WARN` → `_normalize_call`，而 `NORMALIZE_RULES`（rules:259-264）仅含 `get_history/get_price` 的 `fq` 规则 ⇒ **零参数动作、调用原样保留**；档 2 改写唯一入口 `_remove_call`（L4408）**仅由 DENY_REMOVE 触发** ⇒ 「`if not False:` 折叠」不可能发生。执行级 stub 设计**足以且必要**替代文本断言（可区分 KEEP／改写 False／改写 True 三种形态） |
| ② 两断言差分（方向/强度/成对/保留） | **有条件通过**（**颗粒度缺口须补全**） | 方向正确、强度不降、两用例均保留 ✓；但 test_08 实际有**两条** is_trade 断言，**送审稿漏列 tests:215**；test_08b 拟改只写「不再生成旧形态」半边，「成对含新形态」未落到行 |
| ③ 公开 API 依据 | **不通过** | 见下 |

## ③ 公开 API 依据：**证据冲突未处置**

| 证据 | 内容 | 复核结果 |
|---|---|---|
| **乙（文档）** | 上游 `kay-ou/ptradeapi` 仓库 `docs/api-reference-en/utilities.md` 逐字含：`is_trade()` — Environment Check … *"Returns `True` in trading environment, `False` in backtest environment."* | **真实，非幻觉** ⇒ A-1 引文成立 |
| **甲（仓内实证）** | `skills/quantstudio-strategy-compiler/references/ptrade-api-signatures.json` **:789** `local_only_symbols` **仍登记 `"is_trade"`**（该 json 被 `portability_rules.py:62-63` 称为 LOCAL_ONLY 分类的**权威登记源**）；同文件 **:820**（`runtime_corrections.set_backtest_is_trade`）逐字：*"Runtime traceback supplied on **2026-07-25** (platform timestamp 2026-07-26) proves set_backtest/is_trade are QuantStudio-local extensions, **not PTrade backtest public APIs**."* | **实证级**（真实平台 NameError traceback），限定词恰是 **backtest** |

⇒ **两者直接矛盾**：回测模块若无此 API，**无从「返回 False」**。

⚠️ **最大风险**：若证据甲仍真，A-1 后产物部署 IQEngine **回测模块**将 NameError；
而拟改断言 `'is_trade(' in out` 恰把这个**崩溃形态锁进契约** ⇒ 测试越绿、产物越崩。
**这不能靠断言写法修复**，只能靠平台事实裁定。

⚠️ 另注：同一 `utilities.md` 也登记 `create_dir()`／`convert_position_from_csv()`，
而本仓仍分别以 DENY_REMOVE（rules:22）／DENY_BLOCK（rules:205）收紧
⇒ **「上游文档存在」在本仓从来不是 KEEP 的充分条件**。

## ② 颗粒度缺口：③实施清单须逐行落实

| 位置 | 现断言 | ③实施硬要求 |
|---|---|---|
| `tests/test_source_import.py:214` | `"is_trade(" not in body` | → `"is_trade(" in body` |
| `tests/test_source_import.py:215` | `"g.trade_mode = False" in body` | → `"g.trade_mode = is_trade()" in body` **＋** `"g.trade_mode = False" not in body`（**送审稿漏列**） |
| `tests/test_source_import.py:229` | `"g.mode = False" in converted_code` | → `"g.mode = is_trade()" in`（**成对**）**＋** `"g.mode = False" not in`；并建议统一改用 `body` 切片（tests:213 模式），防头部审计注释文本误判 |
| `tests/test_source_import.py:210/216/228` | errors 非空／`g.flag = None`／errors 空 | **保持不变**（`set_backtest` 仍在 DENY_REMOVE；`get_strategy_events` 仍档 3 BLOCK ⇒ 旁证不受影响） |

## ① 两项边界要求（须写入实施要求）

1. **平台可用性边界声明**：stub 注入**绕过**了「`is_trade` 在目标平台是否存在」
   ⇒ 只能证明「*若平台有 is_trade，产物语义正确*」；
   平台可用性**不在 stub 证明范围内**，测试与送审稿**不得将其表述为已证**。
2. 执行范围建议覆盖**产物全文 exec/import**（不只 `initialize` 体片段），
   或显式引用既有语法层测试作为已有覆盖，防注入区与 KEEP 调用交互破坏可执行性成为盲区。

## 另登记：`_REWRITE_LITERALS` 死条目

`source_import.py:3671-3674` 的 `_REWRITE_LITERALS` 仍含 `"is_trade": "False"`（L3673），
经 L3678 `_REMOVE_ALLOW_INLINE = frozenset(_REWRITE_LITERALS.keys())` 派生。
当前为**死条目**（不可达、无害），但它是「**改写静默复活**」的种子
——若未来有人把 `is_trade` 加回 DENY_REMOVE，档 2 改写将**无告警恢复**。
拟改断言恰能捕获该回归，但**条目本身应随契约同步清理或加 A-1 注记**。

## 裁定：**暂缓（不批现稿直接实施）**

断言同步本身**必要且方向正确**，但③放行前置补三件：

1. **证据冲突呈裁定**（平台事实，审查方无法远程裁定）：
   - 甲＝**平台探针复测** `is_trade` 在 IQEngine 回测/交易模块的现状（D4 序列）；
   - 乙＝裁定「产物仅面向交易部署、**回测模块 NameError 已知接受**」；
   - 丙＝**收回 A-1** 恢复改写 ＋ 保留 PD-0012 还原兜底。
   裁定结果**落入 evidence 文档后**方可实施。
2. **登记源同步**（随本批，非 tests 改动）：
   `ptrade-api-signatures.json` 的 `local_only_symbols` 移出 `is_trade` 或加注 A-1 更正与裁定依据；
   清理 `source_import.py:3673` 死条目（或加注「A-1 后不可达」）；
   确认 profile-contract 文档同步状态（审查中该文件在主仓**未能定位**，如实呈报不作违规断言）。
3. **实施清单补全**：按上表逐行落实（含 tests:215 翻转、test_08b 成对断言、`body` 切片统一），
   执行级 stub 用例同时携带 §① 两项边界声明。

> 在补件 1-3 完成前，**两个用例维持红灯**（送审稿承诺的「不静默改、不跳过」纪律正确，应继续遵守）。
