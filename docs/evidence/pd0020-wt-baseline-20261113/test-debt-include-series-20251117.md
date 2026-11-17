# 本线测试债 · `include_*` 七项（2026-11-17）

> **来源**：DRIFT-N7-r4「七项另立本线测试债，先文档方案」。
> **状态（2026-11-17 更新）**：**已修（DRIFT-N7-r5 限定批准）**——技术选型＝**标准库 AST**；仅改 `tests/test_source_import.py` 七项断言 ＋ 最小 helper；**一次新原证：rc=0，97 passed**（`docs/evidence/n7-r5-include-debt-20261116T172257Z/`）。
> 原「文档方案、未改测试」为我方上轮状态，**已被本批批准覆盖**。
> **证据**：`docs/evidence/n7-r4-btrack-verify-20261116T161917Z/`（junit.xml／stdout.txt／stderr.txt／rc.txt／源指纹前后）。

## 一、债项清单（7 项，实测 nodeid）

| # | nodeid | 归属 |
|---|---|---|
| 1 | `tests/test_source_import.py::test_22_include_false_signature_a_unchanged` | `include_*` |
| 2 | `tests/test_source_import.py::test_23_include_false_count_first_unchanged` | `include_*` |
| 3 | `tests/test_source_import.py::test_27_shim_default_include_false` | `include_*` |
| 4 | `tests/test_source_import.py::test_32_include_mapping_signature_a_daily_explicit_unit` | `include_*` |
| 5 | `tests/test_source_import.py::test_33_include_mapping_count_first_positional_daily` | `include_*` |
| 6 | `tests/test_source_import.py::test_34_include_no_mapping_all_freqs` | `include_*` |
| 7 | `tests/test_source_import.py::test_35_include_true_no_mapping` | `include_*` |

## 二、症状

断言以**产物全文**（含注释）为判据，要求不含 `include=False`／`include=True` 等字面量；
而产物**注释中确有说明性字面量**，例如：
「…reClose 列（include=False 日线末行 close = 上一交易日收盘…」。
⇒ **断言 vs 注释文本冲突**，与被测行为无关。

## 三、根因（初步，待方案轮证实）

1. 断言范围未区分「**代码行**」与「**注释行**」；
2. 产物注释中会引用 API 参数名（`include=False`／`include=True`）作为**说明文字**；
3. 因此「全文不含某字面量」是**过强判据**，随注释演进必然破裂。

## 四、拟方案（**三选一，待裁；本轮不实施**）

| 选项 | 做法 | 优劣 |
|---|---|---|
| **甲（推荐）** | 断言前**先剥离注释行**（按行首 `#` 与行内 `#` 处理，注意字符串内 `#`），再匹配目标字面量 | 判据回归**代码语义**；改动集中在一个测试辅助函数 |
| 乙 | 改为**AST 级**断言（解析产物，检查调用实参） | 最严格，但需引入 AST 断言辅助，改动面较大 |
| 丙 | 收窄断言至**特定行区间**（如 `def initialize` 之后至首个 `#` 块前） | 改动最小，但仍脆弱（注释位置可变） |

**共同约束**：
- **不得**放宽为「不检查」或删除用例（项目纪律：不删用例凑绿）；
- **不得**顺手改 `include` 生产行为（本债**只涉断言判据**）；
- 修完须**全量**跑两文件并留 JUnit 原证于新 UTC 目录。

## 五、影响面（预估）

| 面 | 影响 |
|---|---|
| `tests/test_source_import.py` | 7 项断言所在函数（**拟改**，本轮不改） |
| 生产代码 | **零改动**（`include` 逻辑不改） |
| 回归 | 修后 rc 期望由 1 → 0（**仅在无其他既有失败前提下**） |

## 六、归因纪律（勿重犯）

- 本债**不归** B 轨（B 轨仅删一条永不命中的死条目，不触 `include` 路径）；
- **既有性证据**：`git show HEAD:tests/test_source_import.py` 跑同一选择器 ⇒ 同样 7 failed；
- 本文档**不是**修债证明，**仅登记与方案**。

---

## 七、修复记录（DRIFT-N7-r5）

| 项 | 内容 |
|---|---|
| 选型 | **标准库 `ast`**（不手工剥 `#`、不用固定行区间） |
| 改动面 | **仅 `tests/test_source_import.py`**：2 个 helper ＋ 7 项断言；**生产 include/签名零改动** |
| helper | `_history_include_literals(src, func)`（取真实调用 `include` 字面量）／`_func_kwarg_default(src, func, kwarg)`（取真实签名默认值） |
| 保留的原独立断言 | `test_22` 的 `NORM-GETHISTORY-SIG` 与 `coverage.normalized_params`；`test_27` 的 `"def get_history_batch" in out` 与 `SHIM` action |
| 正反例 | **7/7 PASS**：注释/字符串/文档串含相反 include **不误拒**；真实调用反转（含三调用中一处）**必失败**；shim 默认值反转**必失败** |
| 新原证 | `docs/evidence/n7-r5-include-debt-20261116T172257Z/`（junit 12,202 B／rc.txt **0**／stdout 302 B／stderr **0 B**／源指纹前后） |
| 回归读数 | **rc = 0**，**97 passed in 5.52s**（原 7 failed → **0 failed**） |
| 不删/skip | **未删任何用例、未 skip** |
