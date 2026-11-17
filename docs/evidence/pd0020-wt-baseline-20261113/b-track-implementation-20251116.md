# B 轨限定实施 · 交付件（DRIFT-N7-r3 · 2026-11-16）

> **批准依据**：DRIFT-N7-r3「**本批准 B 轨限定实施**」（A 轨仍为候选）。
> **范围**：清除不可达字面量条目 ＋ 三断言改按真实 KEEP 行为 ＋ 两份文档三处陈旧表述更正 ＋ 聚焦回归。
> **未做**：不改 A 轨 JSON／注册／skill 执行器；未平台探针、未联网、未 D5、未订单、未证据迁移、**未提交推送**。

## 一、四文件最小 diff

### 1.1 `quantstudio/strategy_compiler/source_import.py`（删 1 行 ＋ 加 3 行注释）

```diff
 _REWRITE_LITERALS: dict[str, str] = {
     "set_backtest": "None",
-    "is_trade": "False",
 }
+# B 轨收尾（2026-11-16·DRIFT-N7-r3 限定批准）：原 `"is_trade": "False"` 条目已删除。
+# 该条目自 2026-11-11 A 项公有化后不可达——分派要求 name 在 DENY_REMOVE 内才进档 2 改写，
+# 而 is_trade 已移入 PTRADE_REGISTERED_WARN（KEEP）。保留 set_backtest 改写不变。
```

**不可达性依据（实测）**：消费点仅 `L4408` `elif name in _REMOVE_ALLOW_INLINE:` → `L4410` `literal = _REWRITE_LITERALS[name]`；
`_REMOVE_ALLOW_INLINE = frozenset(_REWRITE_LITERALS.keys())`；而 `_scan_calls` 只在 `name in DENY_REMOVE` 时才走删除/改写分支，
`is_trade` 自 2026-11-11 起**已不在 `DENY_REMOVE`** ⇒ 该键**永不命中**。
**`set_backtest` 改写保持不变**（仍在 `DENY_REMOVE` ＋ 映射 `"None"`）。

### 1.2 `tests/test_source_import.py`（08a/08b，共三条断言）

```diff
-    # is_trade() 内嵌 → False（档 2，字面量已知）
+    # is_trade() 内嵌 → KEEP（2026-11-11 A 项公有化后不再改写；B 轨按真实行为审）
 ...
     body = out[out.find("def initialize"):]
-    assert "is_trade(" not in body
-    assert "g.trade_mode = False" in body
-    assert "g.flag = None" in body
+    assert "g.trade_mode = is_trade()" in body       # KEEP：公有 API 不被改写
+    assert "g.trade_mode = False" not in body        # 不再固化为 False
+    assert "g.flag = None" in body                   # DENY_REMOVE 档 2 仍改写 set_backtest
@@ test_08b
-    assert "g.mode = False" in result.converted_code
+    assert "g.mode = is_trade()" in result.converted_code   # KEEP：不再固化为 False
+    assert "g.mode = False" not in result.converted_code
```

**三断言逐一对应**：①`test_08_remove_banding:214`（原 `"is_trade(" not in body`）→ **改为 KEEP 正向断言 ＋ 反向断言**；②同函数 `:215`（原 `g.trade_mode = False in body`）→ **改为 not in**；③`test_08b_rewrite_literal_inline:229`（原 `g.mode = False in ...`）→ **改为 KEEP 正向＋`False` 反向**。
⚠️ **未反改生产逻辑**——`source_import.py` 的转换行为一行未变（仅删死条目），断言按**真实 KEEP 行为**改写。

### 1.3 `skills/quantstudio-strategy-compiler/SKILL.md`（两处）

| 行 | 更正要点 |
|---|---|
| `:30`（规则 13） | 拆分为两符号：`set_backtest()` ＝ **本地扩展**（转换改写为 `None`）；`is_trade()` ＝ **PTrade 公有 API**（文档载回测/交易均可用）且**转换器 KEEP 不改写**；并**明确注记**：「signature registry 仍将 `is_trade` 列在 `local_only_symbols`，skill 侧校验器**仍 BLOCK**——**这些检查尚未解除（A 轨待办）**」 |
| `:474`（兼容规则） | 同上拆分；**明确**「registry（`local_only_symbols`）与 skill 侧校验器**仍按 local-only 处理并阻断**，**尚未解除**」 |

### 1.4 `skills/.../references/ptrade-runtime-compatibility.md`（1 处，实为 2 行改写）

| 行 | 更正要点 |
|---|---|
| `:78`（原「blocks **both** calls」） | 改为「**blocks `set_backtest` calls**」；**新增一行**说明 `is_trade()` 自 2026-11-11 A 项放宽后**已 KEEP**，并**注明 A 轨待办**：registry 仍列 `local_only_symbols`、skill 校验器**仍 BLOCK、未解除** |
| `:79`（原「guard 本身也不在平台上」） | 改为「**因 `set_backtest` 在真实平台缺席**（**而非** `is_trade` 缺席）⇒ 产物须省去 guard 的 local-only 侧」 |

⇒ **三处陈旧表述＝`SKILL.md:30`／`SKILL.md:474`／`ptrade-runtime-compatibility.md:78-79`**，
**未把仍存在的 skill BLOCK 写成已解除**（三处均显式标注「NOT yet lifted / A 轨待办」）。

## 二、四文件 hash（改后）

| 文件 | 字节 | sha256 |
|---|---|---|
| `quantstudio/strategy_compiler/source_import.py` | 298,775 | `5dc1877f864e871b583ce244dd8e56b3e7311a676d763b638688247bc8ee40ed` |
| `tests/test_source_import.py` | 48,591 | `b50f310657a360f0476c4d7c49a511cdb5e6850a28c5df5e0aef4e63e53f5f8f` |
| `skills/.../SKILL.md` | 76,807 | `9e4824f79e5596a4b36680e9ff770a0bca2c6c6558269efa5d997a5bfc6ad117` |
| `skills/.../ptrade-runtime-compatibility.md` | 16,199 | `cddb75a941071245bc1be88e7fd00ccbfa977af750cf5baec27fc95102e41fa6` |

## 三、聚焦回归（实测）

| 项 | 命令 | 结果 |
|---|---|---|
| **08a/08b 定向** | `pytest tests/test_source_import.py -k "08_remove_banding or 08b_rewrite"` | **2 passed**（原为红灯 ⇒ **现转绿**） |
| **两文件合并** | `pytest tests/test_source_import.py tests/test_api_portability_rules.py -q` | **7 failed, 90 passed**（6.54s） |
| **`test_api_portability_rules.py` 独立** | 同上单文件 | **40 passed 全绿** |
| **负向拦截定向** | `-k "file_db or FILE_DB or io_access or local_symbol or registered_warn or 08_remove"` | **3 passed** |

### 3.1 7 项失败的**既有性核证**（非我方引入）

| 项 | 证据 |
|---|---|
| 失败项 | `test_22_include_false_signature_a_unchanged`、`test_23_include_false_count_first_unchanged`、`test_27_shim_default_include_false`、`test_32_include_mapping_signature_a_daily_explicit_unit`、`test_33_include_mapping_count_first_positional_daily`、`test_34_include_no_mapping_all_freqs`、`test_35_include_true_no_mapping` |
| 失败原因 | 均断言产物**注释文本**不含 `include=False`，而注释里确有「…include=False 日线末行 close…」字样 ⇒ 断言 vs **注释**冲突，**与 `is_trade` 无关** |
| **如何核证** | 用 `git show HEAD:tests/test_source_import.py` 取**不含我方改动**的 HEAD 版另存为临时件，跑同一 `include` 选择器 ⇒ **同样 7 failed, 7 passed** ⇒ **既有失败** |
| 结论 | **我方改动未新增失败**；7 项属**既有遗留**，不在 B 轨批准范围，**未修**（不越界） |

### 3.2 负向拦截保持（审核要求「保」）

| 负向面 | 保障点 | 状态 |
|---|---|---|
| **`set_backtest` 改写** | `08a` 的 `assert "g.flag = None" in body`（仍为档 2 改写） | **保持 ✓** |
| **未登记 API BLOCK** | `08a` 的 `assert result.errors`（`get_strategy_events` 档 3 BLOCK） | **保持 ✓** |
| **真实 IO 负向拦截** | `test_api_portability_rules.py` 全 **40 passed**（含 `FILE_DB_*` 检测点） | **保持 ✓** |

## 四、A 轨 provenance **结构说明**（并行交付，不挂住 B 轨）

### 4.1 ⚠️ 结构事实（实测，纠正我方此前表述）

| 项 | 实测 |
|---|---|
| `runtime_corrections` 值类型 | **8 键全部为 `str`**（`get_open_orders`、`source_checked_on`、**`set_backtest_is_trade`**、`log_warn`、`numpy_pandas_injection`、`core_stock_api_profile` 等） |
| 消费者 | **仅 1 件**：`ptrade-api-signatures.json` **自身**（本仓 `quantstudio/`／`scripts/`／`tests/` **无 Python 代码读取该键**） |

⇒ **据此纠正**：`set_backtest_is_trade` **当前是字符串**，**不能**直接宣称「往其中添加对象字段」
（会把 `dict[str,str]` 变成 `dict[str,Any]`，属**结构变更**，而**不是**在原字符串内加字段）。

### 4.2 拟采结构（**保原字符串**）

| # | 做法 | 说明 |
|---|---|---|
| **必守** | **`set_backtest_is_trade` 的原字符串一字不改、类型仍为 `str`** | 保护旧转述（审核明示「**不连带撤回 `set_backtest` 旧转述**」） |
| **补录方式（拟）** | **新增独立键**（仍在 `runtime_corrections` 内，**值类型仍 `str`**）：如 `is_trade_doc_basis` 记为「文档基准登记、**未目标版本实测**、不作跨版本签名保证；取代来源＝`001-page.md:523/:3299`＋`002-index.md:523/:783/:3299`＋`portability_rules.py:26-29`」 | **不改**任何既有键的值类型与内容 |
| **或（备选）** | 顶层新增**并行对象键**（如 `symbol_basis`）承载结构化 provenance | 与 `runtime_corrections` 平级，**不动**其子结构 |
| **禁止** | 把 `set_backtest_is_trade` 改成 dict／list | 破坏「保原字符串」与旧转述完整性 |

### 4.3 消费者兼容

| 情形 | 结论 |
|---|---|
| 现状 | 无 Python 消费者读 `runtime_corrections` ⇒ 新增**字符串型**键**零破坏** |
| 若未来有消费者 | 须按 `str` 读取；**新增键若为对象**需先声明并改消费者 ⇒ **故本方案首选「仍为 str」** |
| A 轨落地前提 | 结构方案须在 A 轨方案文档中**先写定**（含消费者盘点），**本轮不实施** |

## 五、本轮边界遵守

| 项 | 状态 |
|---|---|
| A 轨 JSON／注册／skill 执行器 | **未改**（`git status` 三面**空**） |
| 平台探针／联网／D5／订单 | **未做** |
| 证据迁移 | **未做** |
| 提交／推送 | **未做** |
| 未在范围内的 7 项既有失败 | **未修**（不越界） |
| 最终④ | **不通过** |
| A 轨 | **仍为候选**；**B 轨通过不自动放行 A 轨** |

---

# 六、非目标文义丢失 —— 修回记录（2026-11-16 · 依 DRIFT-N7-r3 复核）

> 审核指出我上轮**整句替换 `SKILL.md` 规则 13 时把原 `log.warn` 禁用句一并删除**，属**非目标文义丢失**。
> **我方自查另发现第二处同类丢失**（L474 的 wrap 禁令句）。**两处均已在原位置补回**。

| # | 位置 | HEAD 原句成分 | 我方上轮丢失 | 处置 |
|---|---|---|---|---|
| **G-1** | `SKILL.md:30`（规则 13） | 「PTrade logging uses `log.debug/info/warning/error/critical`; **`log.warn` is BLOCKED**.」 | **整句被删**（审核点名） | **已在原位置补回**（追加于该行末） |
| **G-2** | `SKILL.md:474` | 「**Do not wrap one local-only call with another.**」 | **整句被删**（**我方自查发现**，审核未点名） | **已在原位置补回**（追加于该行末） |

**修回后实测**：`SKILL.md` = **76,937 B**／sha256 `23191a16ff9f9c91d6b4573f5c563f9bd58e93793c656e8a97c3263ca9294b1a`
（修回前 76,807 B／`9e4824f7…`；**净增 130 B ＝ 两句原文长度**）。

**性质定性**：属**我方编辑过失**（整句替换时覆盖了非目标句），**不是**有意的语义变更；
两句均为**与 A/B 轨无关的既有纪律**（logger 方法白名单、local-only 调用不得互相包裹），**恢复原文、不改措辞**。

⚠️ **对上轮回归读数的影响**：`SKILL.md` 为**文档**，**不参与 pytest 执行** ⇒ 上轮
`2 passed`／`40 passed`／`7 failed, 90 passed`／`3 passed` 读数**仍然有效**，**未因本次修回而变化**；
**本轮未重跑**（依指示「不重复运行补造历史原证」）。

---

# 七、已有测试**原输出**的实际位置（ld 实测）

## 7.1 已有历史原输出（**保留在仓，可回溯**）

| 文件 | 字节 | 日期 |
|---|---|---|
| `docs/evidence/pd0020-full-regression-20261113/junit.xml` | 896,628 | 2026-11-13 |
| `docs/evidence/pd0020-full-regression-20261113/pytest-full.log` | 268,570 | 2026-11-13 |
| `docs/evidence/pd0020-wt-baseline-20261113/baseline-junit/batch3-d81.xml` | 213,999 | 2026-11-14 |
| `docs/evidence/delivery-acceptance-junit-20261031.xml` | 618,420 | 2026-10-31 |
| `docs/evidence/delivery-l3-junit-20261031.xml` | 608,592 | 2026-10-31 |
| `output/conv-test-20261103/pytest_full.log` | 427,796 | 2026-11-03 |
| `docs/evidence/_pytest_after_20261029.log` | 310,504 | 2026-10-29 |
| `docs/evidence/_pytest_baseline_20261029.log` | 310,504 | 2026-10-29 |
| `output/_v5_pytest_full.txt` | 149,466 | 2026-10-07 |

（仓内 junit/pytest 类产物合计 **26 个**，上列为按字节排序前 9 个。）

## 7.2 ⚠️ **本轮（2026-11-16）测试输出 —— 未保存**（如实说明）

| 项 | 状态 |
|---|---|
| 本轮跑的 `2 passed`／`40 passed`／`7 failed, 90 passed`／`3 passed` | **仅控制台输出，未落盘**（未生成 junit／未重定向到文件） |
| 是否可用仓内文件回溯 | **否** —— 本轮读数**无文件级原证**，仅本交付件的记录 |
| 是否重跑补造 | **未重跑**（遵「不重复运行补造历史原证」） |
| 后续补救 | 若需文件级原证，**须另获授权**以 `--junitxml=<path>` 落盘后重跑；**本轮不做** |

⇒ **诚实结论**：本轮回归读数**为会话内观测**，**不构成仓内可回溯原证**；历史全量回归以 §7.1 三件为准。

---

# 八、测试债（7 项 `include_*` 失败 —— **另列，不归本批**）

| 项 | 内容 |
|---|---|
| 失败项 | `test_22_include_false_signature_a_unchanged`／`test_23_include_false_count_first_unchanged`／`test_27_shim_default_include_false`／`test_32_include_mapping_signature_a_daily_explicit_unit`／`test_33_include_mapping_count_first_positional_daily`／`test_34_include_no_mapping_all_freqs`／`test_35_include_true_no_mapping` |
| 共同原因 | 断言「产物**注释文本**不含 `include=False`」，而注释确有该字样 ⇒ **断言 vs 注释**冲突 |
| 既有性证据 | 取 `git show HEAD:tests/test_source_import.py`（**不含我方改动**）跑同一 `include` 选择器 ⇒ **同样 7 failed, 7 passed** |
| **归因结论** | **不归本批**——本批仅**删除一条死条目**（`_REWRITE_LITERALS["is_trade"]`，该键永不命中），**不触碰 `include` 相关任何代码路径** |
| 处置 | **不入本批修复**（依「不顺手修 include 行为」）；**登记为测试债，另案** |
| 债的准确描述 | 「`include=False/True` 系列断言以**注释文本**为判据，与产物注释中的说明性字面量冲突；须改为按**代码语义**断言（如排除注释行后再匹配）」 |

---

# 九、四文件（＋交付件）精确**拟 Git 范围**（**仅拟，未 stage／未 commit／未 push**）

| # | 路径 | 字节 | sha256（前 16） | git 状态 |
|---|---|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import.py` | 298,775 | `5dc1877f864e871b` | `M` |
| 2 | `tests/test_source_import.py` | 48,591 | `b50f310657a360f0` | `M` |
| 3 | `skills/quantstudio-strategy-compiler/SKILL.md` | 76,937 | `23191a16ff9f9c91` | `M` |
| 4 | `skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md` | 16,199 | `cddb75a941071245` | `M` |
| 5 | `docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md` | 9,547 | `cd327e6161789586` | `??` |

**拟用门1 白名单（分号分隔，逐一路径，无通配）**：

```
quantstudio/strategy_compiler/source_import.py;tests/test_source_import.py;skills/quantstudio-strategy-compiler/SKILL.md;skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md;docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md
```

⚠️ **本范围仅为「申请材料」，不是提交动作**：**未 stage、未 commit、未 push**；
**提交请求不等于批准**（依审核明示）；须你方**另行明示授权**后方可执行，
且执行时须 `git -c core.quotepath=false` ＋ `STAGED_SCOPE_ALLOW` 上列值 ＋ 提交后立即清理环境变量。

⚠️ **工作树另有他线大量 `M`/`D`/`??` 文件**（非本轮产物，未触碰）；若授权提交，**只 add 上列 5 路径**，
并在 commit 前以 `git diff --cached --name-only` 核**整个 index**。

---

# 十、A 轨 provenance：独立字符串键方向（**仍不批准 JSON 改动**）

| 项 | 内容 |
|---|---|
| 方向（审核已认可为最小设计） | **新增独立字符串 provenance 键**（不改 `set_backtest_is_trade` 原字符串、不改其类型） |
| **必须如实保留的三项** | ①**未目标版本实测**（`measured_on_target=false`）②**旧转述**（`set_backtest_is_trade` 原字符串一字不改）③**剩余 BLOCK**（`local_only_symbols` 仍列 `is_trade`、skill 三处校验器**仍 BLOCK，未解除**） |
| 本轮状态 | **未改 JSON**（`git status` 对 `ptrade-api-signatures.json` **空**）；A 轨**仍为候选** |
| B 轨与 A 轨关系 | **B 轨完成不代 A 轨通过**；**亦不代最终④通过** |

---

# 十一、DRIFT-N7-r4：**一次新时点当前状态验证**（留证）

> **性质**：**新时点验证留证**，**不是补造过去**（审核明示「准一次新时点当前状态验证，明确不是补造过去」）。
> **命令（未增未减）**：
> `python -B -m pytest tests/test_source_import.py tests/test_api_portability_rules.py -q -p no:cacheprovider --junitxml=<新目录>/junit.xml`
> **环境**：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`（关插件 autoload）＋ `PYTHONDONTWRITEBYTECODE=1`／`-B`（关 pyc）＋ 已核隔离解释器。

## 11.1 证据目录（**新 UTC**）

`docs/evidence/n7-r4-btrack-verify-20261116T161917Z/` ＝ **2026-11-16T16:19:17Z**（本地 2026-11-17 00:19 +08:00）

| 文件 | 字节 | sha256 |
|---|---|---|
| `junit.xml` | 25431 | `13e8b4bbb8e0e6e725ca092e1df9cbe4d169a33e4867e2927baea27bebfc81d4` |
| `rc.txt` | 2 | `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865` |
| `source-fingerprint-after.json` | 932 | `3207bbe1eb666f4059ad3352ba44f6799fee9021e4b1aa493f15fa9b1eadb6a2` |
| `source-fingerprint-before.json` | 1114 | `2fd1c478044e3c22ac5d6d79831f8bf3e874c8b9ca9d82c564e8f3e9182c6cd7` |
| `stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `stdout.txt` | 10518 | `752a328b804c7a4a1662edd8f6616b1f93853d51a39d4e7ae5a14a9ed88cf608` |

## 11.2 执行读数（**分别报告，不改称全套 PASS**）

| 项 | 值 |
|---|---|
| **原 rc** | **1** |
| junit `tests` | **97** |
| junit `failures` | **7** |
| junit `errors` | **0** |
| junit `skipped` | **0** |
| 通过 | **90** |
| 耗时 | 7.78s |
| `stderr.txt` | **0 B**（无异常输出） |
| 解释器 | `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`（**3.11.9**，已核隔离链） |
| `git HEAD` | `8c16351d6cbe7ecbe700bd1ae4d0835dd66633b2` |

⚠️ **rc=1 不得改称「全套 PASS」**：本组存在**已知 7 项既有失败**，92.8%（90/97）通过 ≠ 全绿。

## 11.3 失败集合对照（junit 权威解析 ＋ 归一化）

| 项 | 结果 |
|---|---|
| junit 失败 nodeid | **7 项** |
| 已知集合 | **7 项**（`test_22/23/27/32/33/34/35_include_*`） |
| **新增差异（实测有、已知无）** | **无（0 项）** |
| **已知缺项（已知有、实测无）** | **无（0 项）** |
| **结论** | **失败集合与已知 7 项完全一致 —— 无新增差异** |

⚠️ **比对过程如实申报**：首次比对脚本的 nodeid 归一化有误（junit `classname` 为 `tests.test_source_import` **无 `.py`**，而已知集含 `.py`），
曾误报「7 项差异」；**修正归一化后重比对**（**仅解析既有 junit，未重跑 pytest**）⇒ **完全一致**。
⇒ 教训：跨格式 nodeid 比对须先对齐前缀形态，否则会把**格式差**误报为**语义差**。

## 11.4 源指纹**不变**核验

| 项 | 结果 |
|---|---|
| 运行前 5 文件指纹 | 已存 `source-fingerprint-before.json` |
| 运行后 5 文件指纹 | 已存 `source-fingerprint-after.json` |
| **前后逐项一致** | **是**（`bytes` ＋ `sha256` 全同） |
| 说明 | 本次运行**未改动任何被策源文件**（`-B` 亦未生成 pyc） |

## 11.5 本轮新增文档（**未提交**）

| 文件 | 字节 | 说明 |
|---|---|---|
| `docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md` | — | **七项测试债方案**（甲/乙/丙三选一，**未实施**；遵「先文档方案」） |
| `docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md` | — | **A 轨 provenance 草案**（独立字符串键方案甲/乙，**JSON 未改**） |

## 11.6 拟 Git 范围（**仍仅拟，未 stage／commit／push**）

### 核心五文件（B 轨实施集）

| # | 路径 | 状态 |
|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import.py` | `M` |
| 2 | `tests/test_source_import.py` | `M` |
| 3 | `skills/quantstudio-strategy-compiler/SKILL.md` | `M` |
| 4 | `skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md` | `M` |
| 5 | `docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md` | `??` |

### 本轮新增（**另列，是否入库待你裁**，不与核心五件混同）

| # | 路径 | 说明 |
|---|---|---|
| 6 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/`（**6 文件**） | 新 UTC 原证 |
| 7 | `docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md` | 测试债方案 |
| 8 | `docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md` | A 轨草案 |

⚠️ **提交请求不等于批准**；本轮**零 stage／零 commit／零 push**。

---

# 十二、DRIFT-N7-r5：**七项 include 测试债独立修复**（AST 选型）

> **批准**：DRIFT-N7-r5「独立修七项 include 测试债，技术选型采用标准库 AST，仅改 `tests/test_source_import.py` 七项断言和最小 helper」。
> **边界遵守**：**不改生产 `include`／签名／平台行为**；不联网、不 D5、不订单、**不提交推送**。

## 12.1 写前快照与归属

| 项 | 值 |
|---|---|
| `tests/test_source_import.py`（写前） | **48,591 B**／sha256 `b50f310657a360f0476c4d7c49a511cdb5e6850a28c5df5e0aef4e63e53f5f8f` |
| `git status` | `M tests/test_source_import.py` |
| **归属** | **本会话**（DRIFT-N7-r3 B 轨改动，未提交） |
| 快照时刻（UTC） | **2026-11-16T17:19:49Z** |

## 12.2 失败根因（**探测确证，非推断**）

| test | naive `"include=False" in out` | naive `"include=True" in out`／count | **AST 真实调用 `include`** |
|---|---|---|---|
| t22 | True | **True**（本该 not in）← 失败 | `False` ✓ |
| t23 | True | **True** ← 失败 | `False` ✓ |
| t27 | True | **True** ← 失败 | shim 签名默认 `include=False` ✓ |
| t32 | True | **True** ← 失败 | `False` ✓ |
| t33 | True | **True** ← 失败 | `False` ✓ |
| t34 | count=**8**（期望 3）← 失败 | **True** ← 失败 | `[False, False, False]` ✓ |
| t35 | **True**（本该 not in）← 失败 | True | `True` ✓ |

⇒ **根因确证**：断言作用于**产物全文（含注释／字符串）**，注释中「…include=True…」等**说明性文字**被计入 ⇒ **真实调用完全正确时依然误拒**。

## 12.3 最小 diff（**仅 `tests/test_source_import.py`**）

| 项 | 内容 |
|---|---|
| 新增 helper | **2 个**：`_history_include_literals(source, func_name="get_history")`／`_func_kwarg_default(source, func_name, kwarg)` |
| 改写断言 | **7 项**（t22/23/27/32/33/34/35） |
| 变更行数 | **+71 −14**（含 helper 文档串） |
| 文件 | 48,591 B → **51,100 B**／sha256 `8eaefee89c39445f69fec1f9fd007fdbee0d40fbef01018753adc6e3dde33f59` |
| **AST 核** | **PASS**（改后 `ast.parse` 可解析） |
| **未删用例／未 skip** | ✓ |

### 逐项断言替换

| test | 原断言 | 新断言 |
|---|---|---|
| t22 | `"include=False" in out` ＋ `"include=True" not in out` | `_history_include_literals(out) == [False]` **＋ 保留** `NORM-GETHISTORY-SIG`、`coverage.normalized_params >= 1` |
| t23 | 同上 | `_history_include_literals(out) == [False]` |
| t27 | 同上 | `_func_kwarg_default(out, "get_history_batch", "include") is False` **＋ 保留** `"def get_history_batch" in out`、`SHIM` action |
| t32 | 同上 | `_history_include_literals(out) == [False]` |
| t33 | 同上 | `_history_include_literals(out) == [False]` |
| t34 | `out.count("include=False") == 3` ＋ `"include=True" not in out` | `_history_include_literals(out) == [False, False, False]` |
| t35 | `"include=True" in out` ＋ `"include=False" not in out` | `_history_include_literals(out) == [True]` |

## 12.4 正反例（**7/7 PASS**，静态验证，不跑 pytest）

| # | 例 | 输入特征 | 期望 | 结果 |
|---|---|---|---|---|
| 1 | **正** | 注释含 `include=True`，真实调用 `include=False` | `[False]` | **OK** |
| 2 | **正** | **字符串字面量**含 `include=True`，真实调用 `include=False` | `[False]` | **OK** |
| 3 | **正** | **文档串**含 `include=True`，真实调用 `include=False` | `[False]` | **OK** |
| 4 | **反** | **真实调用反转**为 `include=True` | `[True]` ≠ `[False]` ⇒ **断言必失败** | **OK** |
| 5 | **反** | 三调用中**一处**反转 | `[False, False, True]` ≠ `[F,F,F]` ⇒ **必失败** | **OK** |
| 6 | **正** | shim 签名默认 `include=False` | `False` | **OK** |
| 7 | **反** | shim 签名默认被反转 `include=True` | `True` ⇒ **必失败** | **OK** |

## 12.5 一次新原结果（新 UTC 留证）

`docs/evidence/n7-r5-include-debt-20261116T172257Z/`（＝ **2026-11-16T17:22:57Z**）

| 文件 | 字节 | sha256 |
|---|---|---|
| `junit.xml` | 12202 | `04c937f2d6d8379e7286a950fe7114bccf6791585cd5cb33c55c181c83b8009b` |
| `rc.txt` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` |
| `source-fingerprint-after.json` | 366 | `ead3251db725e18ced123aa2cb06a4d5787ab264bd933c8b2b06c6fc333e120a` |
| `source-fingerprint-before.json` | 446 | `5b499abc6e27017cccee217e192baa10700c7926056040f41d8546a4c6357420` |
| `stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `stdout.txt` | 302 | `e77d4c502bc365c2d76228e781a78967240765c6fd2ae9ebcee9e1079792ca6e` |

| 项 | 值 |
|---|---|
| **rc** | **0** |
| 读数 | **97 passed in 5.52s**（**0 failed / 0 error / 0 skipped**） |
| 对照 | 原 `rc=1`、`7 failed / 90 passed` ⇒ **7 项债全清** |
| `stderr.txt` | **0 B** |
| 源指纹前后一致 | **是** |
| 命令 | `python -B -m pytest tests/test_source_import.py tests/test_api_portability_rules.py -q -p no:cacheprovider --junitxml=<新目录>/junit.xml` |
| 环境 | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` ＋ `PYTHONDONTWRITEBYTECODE=1`／`-B` ＋ 已核隔离解释器（3.11.9） |
| 首失败停报 | **未触发**（无失败） |

## 12.6 拟提交范围（**按文件计数，不把目录算一件**）

| # | 文件 | 状态 |
|---|---|---|
| 1 | `tests/test_source_import.py` | `M`（本轮 AST 改造） |
| 2 | `quantstudio/strategy_compiler/source_import.py` | `M`（B 轨删死条目） |
| 3 | `skills/quantstudio-strategy-compiler/SKILL.md` | `M`（G-1/G-2 修回） |
| 4 | `skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md` | `M` |
| 5 | `docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md` | `??` |
| 6 | `docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md` | `??` |
| 7 | `docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md` | `??` |
| 8–13 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/` 下 **6 个文件**（逐个计） | `??` |
| 14–19 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/` 下 **6 个文件**（逐个计） | `??` |

⇒ **共 19 个文件**（4 改 ＋ 15 新增；**目录不计为一件**）。
⚠️ **仍仅拟**：**零 stage／零 commit／零 push**；**提交请求不等于批准**。

---

# 十三、DRIFT-N7-r6：**三份文档同步（三层关系）＋ 精确提交范围**

> **批准**：DRIFT-N7-r6「剩余关闭判据：在以下三份文档同步已核事实」。
> **边界遵守**：**仅文档同步**；**不 stage／commit／push**；**不改生产转换行为、签名、Validator**；不联网、不 D5、不订单。
> **采样时刻（UTC）**：**2026-11-17T00:46:28Z**（size／sha256／mtime 三件套**同刻齐打**）。

## 13.1 最小文档 diff（3 文件）

### ① `README.md`（L186 起，**+6 −1**）

| 改动 | 内容 |
|---|---|
| 原句 | 「…`set_backtest()` 与 `is_trade()` **仅属 QuantStudio 本地扩展**，双端/PTrade 代码会被 Validator 阻断。」 |
| 现结构 | 保留「生命周期分层」半句；**新增分层说明三项**：①**平台文档层**——`is_trade()` 列为**回测/交易均可用**公有 API（`001-page.md` L523／L3299，载**无参数**、**返回 bool**），`set_backtest()` **平台文档未收录**、仍按本地扩展；②**转换器层**——`is_trade()` **既有 KEEP**，本次仅**删除不可达死映射**（`"is_trade": "False"` 自公有化后永不命中），**属清理死条目、不是新放行**；③**尚未解除／未证**——registry（`local_only_symbols`）与 skill 端校验器**仍 BLOCK**，**T2 仍未证** |
| **显式禁令句** | 「⇒ **不得**表述为『两函数均已平台放行』」 |

### ② `docs/strategy_toolbox.md`（L95，**+1 −1**）

| 改动 | 内容 |
|---|---|
| 原句 | 「`set_backtest()`、`is_trade()` 仅供 QuantStudio 本地单端策略；双端/PTrade 源码不得调用。」 |
| 现句 | `set_backtest()` **仍仅供本地单端**（平台文档未收录）；**`is_trade()` 情况不同**——平台文档列为回测/交易均可用、转换器**已 KEEP**、本次仅删**不可达死映射**；**但 registry 与 skill 端 BLOCK 尚未解除、T2 未证** ⇒ 解除前双端源码仍不应依赖。`log.warning` 禁令**原文保留** |

### ③ `docs/prompt_engineering.md`（L62-63，**+3 −2**）

| 改动 | 内容 |
|---|---|
| 原 L62 | 「…双端/PTrade 源码禁止调用本地扩展 **is_trade()/set_backtest()**。」 |
| 现 L62 | 「…不得调用本地扩展 **set_backtest()**。」（移除并称） |
| 现 L63 | `set_backtest()` 条目**保留**「仅本地单端／真实平台无此 API」，**补注「平台文档未收录」** |
| 现 L64（新增） | `is_trade()` **分层行**：平台文档列为公有 API（无参数、返回 bool）／转换器**已 KEEP**／本次仅删**不可达死映射**／**registry 与 skill 端 BLOCK 未解除**／**T2 未证** ⇒ 解除前不应依赖 |

## 13.2 影响说明

| 面 | 影响 |
|---|---|
| **生产转换行为** | **零** —— 三份均为文档；`source_import.py` 仅 r3 删死条目（r5/r6 未再改） |
| **签名 / Validator** | **零** —— `ptrade-api-signatures.json` 与 `validate_agent_strategy.py` 未改 |
| **测试** | **零** —— 本轮未跑、未改 |
| **运行时可观察行为** | **零** |
| **文档一致性** | **改善**：三处原「并称本地扩展」→ **三层关系分述**；`set_backtest` **保持本地扩展**口径不变 |
| **残留风险** | 见 §13.5（`prompt_engineering.md` 另有 4 处并称残留，**未改**） |

## 13.3 逐文件 hash 清单（**改后，22 件**）

> **按文件计数，目录不计为一件**。原 19 件 ＋ 本轮新增 **3 份文档** ⇒ **22 件**

| # | 文件 | 状态 | 字节 | sha256 | 说明 |
|---|---|---|---|---|---|
| 1 | `README.md` | `M` | 100316 | `579beadf4119bd5e3f1176d4fa1c01dbd9eae9507e633b547db6dec6f5859a54` | 三文档同步：L186 拆为三层关系 |
| 2 | `docs/strategy_toolbox.md` | `M` | 88405 | `bee1b0d920f4b1c9965cfd9a083e6684baa74e4b3737b1ec59f8250a7acbf8fc` | L95 红线拆为三层关系 |
| 3 | `docs/prompt_engineering.md` | `M` | 67860 | `00194b850183ef0b27eefa6f37bfe09eaf6b5c68c5db49a60bc3c2ebd9956013` | L62/L63 拆为三层关系 ＋ 新增 is_trade 分层行 |
| 4 | `tests/test_source_import.py` | `M` | 51100 | `8eaefee89c39445f69fec1f9fd007fdbee0d40fbef01018753adc6e3dde33f59` | r5 AST 改造七项断言 |
| 5 | `quantstudio/strategy_compiler/source_import.py` | `M` | 298775 | `5dc1877f864e871b583ce244dd8e56b3e7311a676d763b638688247bc8ee40ed` | r3 删不可达死条目 |
| 6 | `skills/quantstudio-strategy-compiler/SKILL.md` | `M` | 76937 | `23191a16ff9f9c91d6b4573f5c563f9bd58e93793c656e8a97c3263ca9294b1a` | r3 两处陈旧表述更正 ＋ G-1/G-2 修回 |
| 7 | `skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md` | `M` | 16199 | `cddb75a941071245bc1be88e7fd00ccbfa977af750cf5baec27fc95102e41fa6` | r3 L78-79 更正 |
| 8 | `docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md` | `??` | 27633 | `005d7531786e10fcfbd68c42619f1cb62b16979a7f6247fdcca81e5c0133c10c` | B 轨交付件（本件） |
| 9 | `docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md` | `??` | 4669 | `f7b53c32c180b3defe47e432d74e741acd31c9e5ac845ffb6eeacbce46deacf1` | 测试债方案（已修为记录） |
| 10 | `docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md` | `??` | 4756 | `fcdc027c52295434cce0d007ded90648f30be67dfc27eebc8ed3b3fe65c4ff72` | A 轨 provenance 草案（甲方向） |
| 11 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/junit.xml` | `??` | 25431 | `13e8b4bbb8e0e6e725ca092e1df9cbe4d169a33e4867e2927baea27bebfc81d4` | 证据件（r4/r5 原证） |
| 12 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/rc.txt` | `??` | 2 | `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865` | 证据件（r4/r5 原证） |
| 13 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-after.json` | `??` | 932 | `3207bbe1eb666f4059ad3352ba44f6799fee9021e4b1aa493f15fa9b1eadb6a2` | 证据件（r4/r5 原证） |
| 14 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-before.json` | `??` | 1114 | `2fd1c478044e3c22ac5d6d79831f8bf3e874c8b9ca9d82c564e8f3e9182c6cd7` | 证据件（r4/r5 原证） |
| 15 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stderr.txt` | `??` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 证据件（r4/r5 原证） |
| 16 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stdout.txt` | `??` | 10518 | `752a328b804c7a4a1662edd8f6616b1f93853d51a39d4e7ae5a14a9ed88cf608` | 证据件（r4/r5 原证） |
| 17 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/junit.xml` | `??` | 12202 | `04c937f2d6d8379e7286a950fe7114bccf6791585cd5cb33c55c181c83b8009b` | 证据件（r4/r5 原证） |
| 18 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/rc.txt` | `??` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | 证据件（r4/r5 原证） |
| 19 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-after.json` | `??` | 366 | `ead3251db725e18ced123aa2cb06a4d5787ab264bd933c8b2b06c6fc333e120a` | 证据件（r4/r5 原证） |
| 20 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-before.json` | `??` | 446 | `5b499abc6e27017cccee217e192baa10700c7926056040f41d8546a4c6357420` | 证据件（r4/r5 原证） |
| 21 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/stderr.txt` | `??` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 证据件（r4/r5 原证） |
| 22 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/stdout.txt` | `??` | 302 | `e77d4c502bc365c2d76228e781a78967240765c6fd2ae9ebcee9e1079792ca6e` | 证据件（r4/r5 原证） |

**合计：22 件（改 7 ＋ 新增 15）**

⚠️ **空文件说明（更正我方打印缺陷）**：两个 `stderr.txt` 均为 **0 B 空文件**（sha256 应为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`）；
本轮清单脚本把 `len(b)==0` 误显示为「缺失」，**是打印逻辑缺陷**，**不是文件缺失**——已实测两文件均存在且为 0 B。

## 13.4 三层关系覆盖自检（8/8 OK）

| 检查项 | 结果 |
|---|---|
| ① 平台文档列有 `is_trade` | **OK**（「回测/交易均可用」） |
| ② 转换器既有 KEEP | **OK** |
| ② 此次删除**不可达死映射** | **OK**（「不可达死映射」） |
| ③ registry **未解除** | **OK**（`local_only_symbols`） |
| ③ skill 端 BLOCK **未解除** | **OK**（「尚未解除」） |
| ③ 目标版本 **T2 未证** | **OK**（「T2 仍未证」） |
| `set_backtest` **仍本地扩展** | **OK** |
| **未写**「两函数均已平台放行」 | **OK**（仅作**禁令**出现） |

## 13.5 ⚠️ 残留并称（**已登记，未擅改**，提请裁定）

`docs/prompt_engineering.md` 内经只读核，**另有 4 处**仍把两符号并称「本地扩展」：

| 行 | 现文（摘） |
|---|---|
| **L128** | 「4) 双端/PTrade 目标禁止调用 `set_backtest/is_trade`；仅 QuantStudio 本地单端目标才可使用**这些本地扩展**。」 |
| **L192** | 「# 双端/PTrade 代码不得调用 `is_trade()/set_backtest()`；本地专属设置仅留在 QuantStudio-only 源码中。」 |
| **L255** | 「…本地扩展 `set_backtest`/`is_trade` **仅限** QuantStudio…」 |
| **L364** | 「\| 4 \| 双端/PTrade 禁止 `set_backtest`/`is_trade`；仅本地单端可用 \| 真实 IQEngine 无**这些本地扩展**，Validator 必须 BLOCK \|」 |

⇒ **未改**：审核本批指定「[prompt_engineering.md (line 62)]」**具体一行**，按「**零越权：计划外只登记不擅动**」我**只登记**。
⚠️ 但**如实提示**：这 4 处与本轮 §13.1③ 的分层表述**并存**，同一文档内仍有口径不一致；若要与「统一说明三层关系」完全自洽，**需一并更正**——**请裁定**。

## 13.6 拟提交范围（**仅拟，未 stage／commit／push**）

- **文件数：22**（改 7 ＋ 新增 15）；**目录不计**
- **`STAGED_SCOPE_ALLOW` 拟值**（分号分隔，逐一路径，无通配）：

```
README.md;docs/strategy_toolbox.md;docs/prompt_engineering.md;tests/test_source_import.py;quantstudio/strategy_compiler/source_import.py;skills/quantstudio-strategy-compiler/SKILL.md;skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md;docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md;docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md;docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/junit.xml;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/rc.txt;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-after.json;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-before.json;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stderr.txt;docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stdout.txt;docs/evidence/n7-r5-include-debt-20261116T172257Z/junit.xml;docs/evidence/n7-r5-include-debt-20261116T172257Z/rc.txt;docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-after.json;docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-before.json;docs/evidence/n7-r5-include-debt-20261116T172257Z/stderr.txt;docs/evidence/n7-r5-include-debt-20261116T172257Z/stdout.txt
```

⚠️ **提交请求不等于批准**；须你方**明示授权**后执行，且执行时：
`git -c core.quotepath=false` ＋ 上列白名单 ＋ 提交后立即 `Remove-Item Env:\STAGED_SCOPE_ALLOW`；
commit 前须 `git diff --cached --name-only` 核**整个 index**（工作树另有他线大量 `M`／`D`／`??`）。

## 13.7 本轮状态

| 项 | 值 |
|---|---|
| 本轮 | **仅文档同步（3 文件）** |
| 生产/签名/Validator | **零改动** |
| 联网／D5／订单 | **零** |
| stage／commit／push | **零** |
| A 轨 | **仍为候选**；甲方向（独立字符串 provenance 键）继续设计；**不等于批准改 JSON、不等于解除 BLOCK** |
| 「幻影纠正」 | 撤回已收；**不单开知识条目任务**（依审核） |
| 最终④ | **不通过** |

---

# 十四、DRIFT-N7-r7：**残留并称统一（5 处）＋ 22 件精确清单**

> **授权依据**：**b152 已授权同义文更**（非仅 L62）⇒ 本轮**直接修**，不再请示。
> **边界遵守**：**只文档**；不改转换行为／签名／Validator；**不测试**；不联网、不平台操作；**不 stage／commit／push**。

## 14.1 最小 diff（`docs/prompt_engineering.md`，**5 处编辑 / 10 变更行**）

| # | 行 | 原表述 | 统一后 |
|---|---|---|---|
| 1 | **L128** | 「双端/PTrade 目标**禁止调用** `set_backtest/is_trade`；仅本地单端才可使用**这些本地扩展**」 | 「**禁止依赖**」＋ `set_backtest` 属本地扩展（平台文档未收录）＋ `is_trade` 三层事实（公有 API／已 KEEP／仅删不可达死映射／registry·skill BLOCK 未解除／T2 未证）＋ 保留「仅本地单端可使用本地扩展」 |
| 2 | **L192**（示例代码注释） | 「不得**调用** `is_trade()/set_backtest()`」 | 「不得**依赖** `set_backtest()`（本地扩展）与 `is_trade()`（平台公有，但 registry/skill 侧 BLOCK 未解除、T2 未证）」——**单行保持** |
| 3 | **L255** | 「**本地扩展 `set_backtest`/`is_trade`** 仅限 QuantStudio-only」 | 逐符号分述：`set_backtest` 属本地扩展（未收录）、仅限本地；`is_trade` 三层事实 |
| 4 | **L264** | 「`create_dir`/`get_research_path`/**`is_trade`** 属本地扩展，双端/PTrade 禁止」 | **`create_dir`/`get_research_path` 保持原判不动**；`is_trade` **单独**三层事实 |
| 5 | **L364**（表格） | 「禁止 `set_backtest`/`is_trade`；真实 IQEngine 无**这些本地扩展**，Validator 必须 BLOCK」 | 「**禁止依赖**」＋ 分述：`set_backtest` 本地扩展（真实 IQEngine 无此 API，平台文档未收录）；`is_trade` 三层事实＋**解除前仍必须 BLOCK／不得依赖** |

**文件**：`docs/prompt_engineering.md` 67,860 B → **69,059 B**／sha256 `00194b850183ef0b…` → **`b9c511b094b3e4a1edbf834fb8f283f17ad48e7bc1c03d33f74e1a42490d5e2f`**

## 14.2 回扫三文档（精确判据）

| 文件 | 明文并称 | 结果 |
|---|---|---|
| `docs/prompt_engineering.md` | **0** | 已统一 |
| `README.md` | **0** | 已统一（L186-191 三层关系） |
| `docs/strategy_toolbox.md` | **0** | 已统一（L95） |
| **合计** | **0** | **三文档无明文并称残留** |

⚠️ **我方判据纠错申报**：首轮回扫用**过粗判据**（`含"本地扩展" 且 无"均可用"/"KEEP"` ⇒ 判未统一），
把**我方新写的统一句**（`prompt_engineering.md` L192、`README.md` L191 禁令句）**误判为残留**。
已改用**明文并称判据**（「这些/均/都 本地扩展」「本地扩展 set_backtest/is_trade」「禁止调用 set_backtest/is_trade」
「set_backtest/is_trade 属本地扩展」「仅属 QuantStudio 本地扩展」）重扫 ⇒ **0 处**。
**教训**：一致性回扫的判据须**指向并称句法**，不能只按关键词存在性判定，否则会把自己刚写对的句子判成违规。

## 14.3 统一后的三层事实（三文档口径一致）

1. **平台文档列有 `is_trade`** —— `001-page.md` L523／L3299：「仅在回测、交易模块可用」，载**无参数**、**返回 bool**。
2. **转换器既有 KEEP，此次仅删除不可达死映射** —— `_REWRITE_LITERALS` 的 `"is_trade": "False"` 自公有化后永不命中 ⇒ 清理死条目，**非新放行**。
3. **registry、skill 仍 BLOCK，目标版本 T2 未证** —— `local_only_symbols` 仍列 `is_trade`；`validate_agent_strategy.py:762/995/1005` 仍 BLOCK；**无目标版本实测记录**。
- **`set_backtest` 仍按本地扩展处理**（平台文档未收录）；**「当前禁止依赖」的要求保留**；**未改成「两函数均已平台放行」**。

## 14.4 22 件当前 hash（三件套同刻；**0B 为空件，不是缺失**）

| # | 文件 | 字节 | sha256 | mtime |
|---|---|---|---|---|
| 1 | `README.md` | 100316 | `579beadf4119bd5e3f1176d4fa1c01dbd9eae9507e633b547db6dec6f5859a54` | 2026-11-17 08:46:28 |
| 2 | `docs/strategy_toolbox.md` | 88405 | `bee1b0d920f4b1c9965cfd9a083e6684baa74e4b3737b1ec59f8250a7acbf8fc` | 2026-11-17 08:46:28 |
| 3 | `docs/prompt_engineering.md` | 69059 | `b9c511b094b3e4a1edbf834fb8f283f17ad48e7bc1c03d33f74e1a42490d5e2f` | 2026-11-17 09:56:23 |
| 4 | `tests/test_source_import.py` | 51100 | `8eaefee89c39445f69fec1f9fd007fdbee0d40fbef01018753adc6e3dde33f59` | 2026-11-17 01:22:38 |
| 5 | `quantstudio/strategy_compiler/source_import.py` | 298775 | `5dc1877f864e871b583ce244dd8e56b3e7311a676d763b638688247bc8ee40ed` | 2026-11-16 21:51:17 |
| 6 | `skills/quantstudio-strategy-compiler/SKILL.md` | 76937 | `23191a16ff9f9c91d6b4573f5c563f9bd58e93793c656e8a97c3263ca9294b1a` | 2026-11-16 23:21:27 |
| 7 | `skills/quantstudio-strategy-compiler/references/ptrade-runtime-compatibility.md` | 16199 | `cddb75a941071245bc1be88e7fd00ccbfa977af750cf5baec27fc95102e41fa6` | 2026-11-16 21:51:17 |
| 8 | `docs/evidence/pd0020-wt-baseline-20261113/b-track-implementation-20251116.md` | 39799 | `ffd0d1b0372e2abd09a9d83f67ccb9b4be26614d9fa6485b389b3ce90e1a83df` | 2026-11-17 08:47:17 |
| 9 | `docs/evidence/pd0020-wt-baseline-20261113/test-debt-include-series-20251117.md` | 4669 | `f7b53c32c180b3defe47e432d74e741acd31c9e5ac845ffb6eeacbce46deacf1` | 2026-11-17 01:23:46 |
| 10 | `docs/evidence/pd0020-wt-baseline-20261113/a-track-provenance-draft-20251117.md` | 4756 | `fcdc027c52295434cce0d007ded90648f30be67dfc27eebc8ed3b3fe65c4ff72` | 2026-11-17 01:24:23 |
| 11 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/junit.xml` | 25431 | `13e8b4bbb8e0e6e725ca092e1df9cbe4d169a33e4867e2927baea27bebfc81d4` | 2026-11-17 00:19:32 |
| 12 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/rc.txt` | 2 | `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865` | 2026-11-17 00:19:32 |
| 13 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-after.json` | 932 | `3207bbe1eb666f4059ad3352ba44f6799fee9021e4b1aa493f15fa9b1eadb6a2` | 2026-11-17 00:19:32 |
| 14 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/source-fingerprint-before.json` | 1114 | `2fd1c478044e3c22ac5d6d79831f8bf3e874c8b9ca9d82c564e8f3e9182c6cd7` | 2026-11-17 00:19:17 |
| 15 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 2026-11-17 00:19:32 |
| 16 | `docs/evidence/n7-r4-btrack-verify-20261116T161917Z/stdout.txt` | 10518 | `752a328b804c7a4a1662edd8f6616b1f93853d51a39d4e7ae5a14a9ed88cf608` | 2026-11-17 00:19:32 |
| 17 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/junit.xml` | 12202 | `04c937f2d6d8379e7286a950fe7114bccf6791585cd5cb33c55c181c83b8009b` | 2026-11-17 01:23:04 |
| 18 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/rc.txt` | 2 | `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa` | 2026-11-17 01:23:04 |
| 19 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-after.json` | 366 | `ead3251db725e18ced123aa2cb06a4d5787ab264bd933c8b2b06c6fc333e120a` | 2026-11-17 01:23:04 |
| 20 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/source-fingerprint-before.json` | 446 | `5b499abc6e27017cccee217e192baa10700c7926056040f41d8546a4c6357420` | 2026-11-17 01:22:57 |
| 21 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 2026-11-17 01:23:04 |
| 22 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/stdout.txt` | 302 | `e77d4c502bc365c2d76228e781a78967240765c6fd2ae9ebcee9e1079792ca6e` | 2026-11-17 01:23:04 |

**合计：22 件**

⚠️ **空件更正（再次明确）**：两个 `stderr.txt` 均为 **0 B 存在空件**，
sha256 = `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空内容标准值）——**不得记为「缺失」**。
（r6 清单脚本因 `len(b)==0` 打印为「缺失」，属**我方打印缺陷**，已在本件更正。）

## 14.5 文档影响说明

| 面 | 影响 |
|---|---|
| 生产转换行为 | **零**（纯文档） |
| 签名 / Validator | **零** |
| 测试 | **零**（本轮**未跑**，依「不重跑已收 97 项」） |
| 运行时可观察行为 | **零** |
| 文档一致性 | **提升**：三文档 **明文并称 0 处**；`set_backtest` 本地扩展口径不变；「禁止依赖」保留 |
| 范围纪律 | `get_research_path` **未擅改**（其公有化属 **A-2** 范围，非本批）；仅把 `is_trade` 从该并称列表中单独分出说明 |

## 14.6 本轮状态

| 项 | 值 |
|---|---|
| 本轮 | **仅文档（`docs/prompt_engineering.md` 5 处）** |
| 测试 | **未跑**（97 项保持关闭，不重跑） |
| 生产/签名/Validator | **零改动** |
| 联网／平台／D5／订单 | **零** |
| stage／commit／push | **零** |
| A 轨 | 甲方向（独立字符串 provenance 键）**可继续出键名/措辞草案**；**JSON 与解除 BLOCK 仍未批准** |
| 最终④ | **不通过** |

