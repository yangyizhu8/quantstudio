# QMT 管线 M3.1 验收证据（④段一，2026-10-09）

> 方案：`docs/qmt-pipeline-m3.1-plan.md`（②审通过——总调度直审，含**两处修订**：
> ① 两层同时命中代码形态亦判 UNDETERMINED；② 段一补判据＝QMT 产物 diff 面按**预期面核对**
> （byte-diff 硬门仅对 PTrade 侧成立，不得笼统写「byte-diff 不变」）。
> **实施**：③就地完成（<150 行，O1 阈值内不委派）。
> **写前快照**：`stash@{0}`=`a8a41d858c4e1d1b7e983cdec75da01702d2b113`；备份 `docs/handoff/backup-m3.1-20261009-164820/`。

## 0. 判型声明

**数据修正型**——结果按正确口径变化。**影响面**＝产物侧 fundamentals 取数面（`_qs_fin_to_rows`
及其唯一调用点 `_qs_get_fundamentals` 内传参行）；**diff 只能源于修正行集**（见 §2 实测）；
**准确度只升不降**（现状纯 dict 形态 → 全 NaN（错）→ 修复后正确取值或显式 `UNDETERMINED`（可区分））。

## 1. 改动（3 件，均在本轮允许写入清单内）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `quantstudio/strategy_compiler/source_import_qmt.py` | `_qs_fin_to_rows` 双轨重写 + 调用点传参一行（**仅此两处**） |
| 2 | `tests/test_source_import_qmt.py` | +8 测试（轨 A×2 / 轨 B×2 / 不确定 / **歧义** / `to_dict` 回归 / None·非 dict） |
| 3 | `docs/evidence/qmt-m3.1-acceptance-20261009.md` | 本文件 |

**实现要点**：轨 A 显式声明（`shape='code_major'|'field_major'`，零猜测）；轨 B 证券代码模式识别
（`^六位数字\.(SH|SZ|BJ)$`，抽样 ≤5 键）；**两层均不命中或两层同时命中（歧义）→ 返回 `{}` +
`QS_QMT_FIN_SHAPE_UNDETERMINED` 审计行，绝不猜**；`to_dict('index')` 路径不动。
`re` 采用**函数内局部 import**（不依赖产物头部 import 面——产物仅 `import pandas`）。

## 2. 段一验收对照（7 条，全 PASS）

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| ① | 三形态单测全绿（含**歧义判 UNDETERMINED**） | **PASS** | `pytest tests/test_source_import_qmt.py` → **18 passed, 0 warnings**（M2b 6 + minute deny 4 + F-1 8） |
| ② | `to_dict` 路径回归不变 | **PASS** | `test_fin_to_rows_dataframe_path_unchanged`（07:1889 投影） |
| ③ | **桩冒烟回归**（M3 基线不破） | **PASS** | `pytest tests/test_qmt_stub_smoke.py` → **6 passed** |
| ④ | 既有回归全绿 | **PASS** | 同上 18 passed（含 minute deny 4 项） |
| ⑤ | 六策略 QMT 产物过白名单 | **PASS** | `validate_qmt_portability` **6/6** + gbk 解码 + `ast.parse` |
| ⑥ | 策略源码零改动 | **PASS** | 六策略 SHA-256 vs M2b 开工快照 → **UNCHANGED**（零变更） |
| ⑦ | **QMT 产物 diff 面按预期面核对**（②审修订） | **PASS** | 见下 |

**⑦ 实测方法**：`git worktree add D:\tmp\qs-m31-base e2faba3`（改动前转换器）× 主侧（改动后），
**同源终证法**（同一份六策略源同步至双侧），双侧重生成 QMT 产物后逐行 diff：

- **PTrade 侧 byte-diff**：六策略 sha12 **全 IDENTICAL**（`949679f23708` / `265865048619` /
  `a884eac2c690` / `6b1516fd7ddf` / `98b25cd100cc` / `3d29403e2506`）——**硬门成立**；
- **QMT 侧 diff 面**：唯一差异 **57 行，全部落在 `_qs_fin_to_rows` 函数段内**（`_code_major` /
  `_field_major` / `inner_is_code` / `code_re` / 审计行文案 / 调用点传参），**无任何函数段外 diff**
  ——符合数据修正型约束「diff 只能源于修正行集」。

**过程发现（就地修复，属目标文件内部阻断性缺陷豁免）**：EXT 模板串内正则的 `\d` 触发
`DeprecationWarning: invalid escape sequence`（模板为普通字符串）→ 模板内加倍为 `\\d`（产物得到
单反斜杠 `r'^\d{6}\.(SH|SZ|BJ)$'`，实测核对在位）。修复后 **0 warnings**。

## 3. 段二（挂 M5-1，合并验收口径）

**触发条件**：M5-1（`C.get_financial_data` 真实返回形态实测）钉死后执行。

1. 以**轨 A 显式声明**接入（`_qs_get_fundamentals` 内 `shape=` 改传实测形态）→ fundamentals
   端到端取值正确（三策略消费面：CANSLIM / weekly_smallcap / 周频小市值）；
2. 若真实形态非既有两形态 → 扩展解析分支 + 回归，**且不得放宽段一第①条**（不确定仍保守）；
3. 段二结论写入 M5 证据文档 + 回填本文件 → **合并验收关闭**。

## 4. 结论

**段一 7 条全 PASS**：18 passed / 0 warnings、桩冒烟回归 6 passed、六策略白名单 6/6、
源码零改动、PTrade byte-diff 6/6 IDENTICAL、**QMT diff 面 57 行全在修正行集内**。
**判型（数据修正型）成立**。段二挂 M5-1；本批（3 件）**入下批推送**（本会话不推送）。
