# 占位门限定实现·验收证据（2026-11-13）

> 提交：`18ca553`（实现）／`e90c6dd`（初版 OPS-0005）／本件随补正提交
> 方案正本：`docs/handoff/kb-placeholder-gate-design-20261112.md` v3
> 状态：**限定实现 · 四族档位全为 `off` · 未激活**

## 1 规则 → 用例映射（R3 第①条）

| 规则 | 位置 | 对应用例 |
|---|---|---|
| **T1** 存在分隔符 `[；;]` | `_trigger_errors` | `test_trigger_missing_question_part`（无分隔符）· `test_trigger_semicolon_halfwidth_ok` |
| **T2** 分隔符后紧跟 `问法[：:]` | 同上 | `test_trigger_missing_question_part` |
| **T3** 前半（复现条件）非空且 ≥2 字 | 同上 | `test_trigger_head_empty_rejected` · `test_trigger_head_one_char_rejected` |
| **T4** 后半（口头问法）≥4 字 | 同上 | `test_trigger_tail_short_rejected` |
| **T5** 两段 `classify` ≠ `fake` | `classify_placeholder` | `test_field_fake_prefix_rejected` · `test_classify_fake_vs_mention` |
| **F1/F2** feedback 至少一项非占位、非空项不得假填 | `_feedback_placeholder_errors` | `test_feedback_fake_rejected` · `test_feedback_null_allowed` |
| **§3.1** 正式 entry 纯路径判据 | `entry_stage` | `test_entry_stage_in_family_dir` · `test_draft_outside_family_dir_is_draft` |
| **§3.1.1** `.draft.`/`stage:draft` 不得跳门 | `entry_stage` + `placeholder_report` | `test_entry_stage_draft_name_inside_family_dir_is_still_entry` · `test_stage_marker_has_no_effect_on_entry_path` |
| **§3.1.1 诊断** 同 id 冲突须显式报 | `placeholder_report.diagnostics` | `test_draft_name_conflict_diagnostic_not_silently_skipped` |
| **§3.3** off/warn/strict × 全局 `--strict` | `placeholder_report` + `check_files` + `main` | `test_gate_off_returns_none` · `test_gate_warn_reports_fake_but_not_none` · `test_check_files_off_no_placeholder_key` · `test_check_files_warn_reports_but_passes` · `test_check_files_strict_fails_on_fake` · `test_off_with_global_strict_still_passes_placeholder` |
| **§3.5** off 只回退新门、原必填照常 | `validate_record` 未改 | `test_off_does_not_disable_原有必填` |
| **§2.0** 只检字段不扫正文 | `placeholder_report` | `test_check_files_strict_passes_on_mention` |
| **§2.0.2** fake（开头式）/ mention（句中） | `classify_placeholder` | `test_classify_fake_vs_mention` · `test_field_mention_is_not_fake` |

用例总数 **23**（22 + 新增诊断 1）。

## 2 off 档 payload 完整逐位对比（R3 第②条）

对比对象：**改前**（`18ca553^:scripts/kb_hook.py`，从 git 提取到 tmp，`PYTHONPATH=scripts`）+ **改后**（工作区 HEAD），同一批文件（`knowledge/{qmt,ptrade-deploy,db,ops}` 全 93 条，含 OPS-0005）。

| 形态 | 改前 | 改后 | 判定 |
|---|---|---|---|
| `--json` | sha256 `d59bb1eb4a01da7e…`，len 147 | **同** | **BYTE_IDENTICAL=True** |
| 文本输出 | sha256 `d392fe3d9c6e52f1…`，len 49 | **同** | **BYTE_IDENTICAL=True** |
| `--strict` 退出码 | 0 | 0 | **IDENTICAL** |

键集两侧均为 `['bad','checked','families_used','note','skipped','total']`；`total=93 checked=93 bad=0`。

> 结论修正（R3 指出）：此前"`bad=0` 且无新 `placeholder` 键"**只是部分兼容特征**；本节才是完整 payload 逐位相同的证据。

## 3 真库 warn 抽查留痕（R3 第③条）

| 项 | 内容 |
|---|---|
| 执行时间 | **2026-11-13 约 10:0x（Asia/Shanghai）**，在本线 R3 实施轮的验收脚本内 |
| 做了什么 | 临时把 `knowledge/kb-info.yaml` 的 **ops** 族 `placeholder_gate` 由 `"off"` 改为 `"warn"`，跑 `kb_hook --files "knowledge/ops/OPS-*.md" --json`，随后 `finally` 写回原文 |
| 读数 | `checked=4 bad=0`；四条均 `level=warn stage=entry fake=0 mention=[] notes=[]` ⇒ **OPS-0003 正文 7 处「占位」零误伤**（本门不扫正文） |
| **当时文件 hash** | **未记录**（如实申报：脚本只在内存保留原文、未落盘 sha256）——这是留痕缺陷，已在 §4 纪律中整改 |
| 还原方式 | `try/finally`：把读入的原文整体写回（`encoding="utf-8", newline="\n"`） |
| **还原回执（事后实测）** | `knowledge/kb-info.yaml` sha256 = `55003e5267eb1aa17c1bfb22352c699b87421fbc82a38a454d121448c643d72d`；`git status --porcelain -- knowledge/kb-info.yaml` 为**空**（与 HEAD 逐位一致）；重读四族档位 = `[('qd','off','str'),('pd','off','str'),('db','off','str'),('ops','off','str')]` |

## 4 纪律整改（R3 第③条后句）

**自本件起：任何档位/配置试验一律用 fixture，不再修改共享真库配置。**
做法：`tmp_path` 内造 `kb-info.yaml` + 条目（`tests/test_kb_placeholder_gate.py::_sandbox` 已如此实现），需要真库样本时只**读**文件内容、在 fixture 里复刻，不改真库。
本次真库改动是最后一次；后续若必须验证真库响应，走"复制到 tmp 再改副本"。

## 5 `.draft.` 路由冲突诊断（R3 第④条）

冲突本质：族 `entry_glob`（`OPS-*.md`）会命中 `OPS-2026-0001.draft.md`，而 `id_pattern`（`OPS-\d{4}-\d{4}`）经 `re.match` 是**前缀匹配** ⇒ 该文件与 `OPS-2026-0001.md` **同 id**。

处置：
- 判据上**不认降级**（族目录内 `.draft.` 仍判 `entry`，防"改名跳门"）；
- **但不再"仅记一条 note"**：新增 `diagnostics` 结构化诊断 `DRAFT_NAME_ON_ENTRY_PATH`，含 `entry_glob_matched` / `id_pattern` / `id_match_mode="prefix"` / `id_value` / `duplicate_of` / `action`（人工处置指引）；
- CLI 文本输出新增 `[placeholder:…]` 段并打印 `! <code>: …` 诊断行（warn/strict 档可见）。

## 6 OPS-2026-0005 实质复核（R3 第⑤条）

**复核发现（不靠机检计数）**：`e90c6dd` 提交的 OPS-0005 是 `kb_draft` 生成的**模板骨架**——`trigger` = 「待补充：最小复现触发条件」、`feedback.contract_row` = 「待补充：registry/矩阵新增行描述（或 null）」、正文三节全为「（待补充）」。而 `kb_hook` 报 **5/5 PASS**。

**这正是本线自己批判的形态**：机检只判非空 ⇒ 占位串通过。R3 的"不以机检计数自动关案"在本条上被实证。

**处置**：已补实（trigger 改为两段式实质问法、`contract_row` 改为可执行判据、正文三节补「复现步骤（v2 P5 的逐判据走过过程）／影响面／讨论」）。复核后读数：该条 `classify_placeholder(trigger)=None`、`feedback.contract_row` 无占位词、正文无「待补充」。

## 7 本轮未做（边界）

- 未激活任何族档位（四族 `off`）；未建存量登记 JSON；未实现 `--allow-placeholder` 豁免通道；未改 pre-push 挂载。
- 激活范围、回退验证、消费者验证另审。
