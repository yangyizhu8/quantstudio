# 占位门限定实现·验收证据（2026-11-13）

> 提交：`18ca553`（实现）／`e90c6dd`（初版 OPS-0005）／本件随补正提交
> 方案正本：`docs/handoff/kb-placeholder-gate-design-20261112.md` v3
> 状态：**限定实现 · 四族档位全为 `off` · 未激活**

## 0 验收声明纠正（2026-11-13 二轮审核后，**以下三条取代此前表述**）

| # | 此前（错误/不完整） | 纠正 |
|---|---|---|
| 1 | "off 档纯增益／全能力等价" | **只可称"对当前库内容无观测差异"**。`duplicate_id` 属**新增检测型**：它**会让此前 PASS 的场景变 FAIL**；"93 条 `duplicated=0`" 只证**当前库内容**未受影响，**不证**能力等价。off 关的是占位判定，**不关**重复身份检测 |
| 2 | "warn ⇒ CLI 恒 `rc=0`" | **只可说「占位 warn 本身不新增失败」**。既有 schema 错误、`duplicate_id` **保留原退出语义**（演练③：warn 档 + 缺 `root_cause` + `--strict` ⇒ **`rc=1`**） |
| 3 | "warn + `--strict` 也不 FAIL"（笼统） | 该说法**仅对占位项成立**；本门 `strict` 档命中会并入 `bad` 从而在 `--strict` 下产生 `rc=1`。两者契约见激活计划 §7 |

> 另：本门 warn 档**即使 `fake=0` 也会输出一行摘要**，故"告警被消费"的判据须看 `fake` 数，不能看"有无 `[placeholder:` 行"。

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

## 5 `.draft.` 路由冲突诊断（R3 第④条 / R4 追加：必须影响结果）

冲突本质：族 `entry_glob`（`OPS-*.md`）会命中 `OPS-2026-0001.draft.md`，而 `id_pattern`（`OPS-\d{4}-\d{4}`）经 `re.match` 是**前缀匹配** ⇒ 该文件与 `OPS-2026-0001.md` **同 id**。

### 5.1 只打印不够（R4 指出）

若只在 `placeholder` 报告里留 `diagnostics`，`kb_hook` 仍会把两个文件都当 `checked` 且各自 `validate_record` 通过（`bad=[]`）⇒ 下游（R4 沉淀计数 / 检索 / 任何按 id 取条目的消费者）**会把同一 id 当两个有效条目消费**。这是**结果污染**，不是提示问题。

### 5.2 处置（已实现，与族档位无关）

| 层 | 行为 |
|---|---|
| **判据** | 族目录内 `.draft.` **不认降级**（`entry_stage` 判 `entry`，防"改名跳门"） |
| **诊断** | `placeholder` 报告出 `diagnostics: DRAFT_NAME_ON_ENTRY_PATH`（`entry_glob_matched` / `id_match_mode="prefix"` / `id_value` / `duplicate_of` / `action`）；CLI 文本打印 `! <code>: …` 行 |
| **结果（新增）** | `check_files` 跨文件收集 `id → [rel…]`，**同 id ≥2 ⇒ 每个涉事文件都并入 `bad`**，错误文案 `duplicate_id：id 'OPS-2026-0001' 另有 <other> —— 同 id 多条会被下游当多个有效条目消费，须人工处置`；`--strict` 下退出码 1 |
| **轴独立** | 该检测**不受 `placeholder_gate` 档位影响**（off 档同样生效）——它属结构完整性，与"占位"是两条轴 |

### 5.3 纯增益核验

现库实测（2026-11-13）：**93 条 entry，`unique_ids=93`、`duplicated=0`、族目录内无 `.draft.` 文件** ⇒ 新增检测对今日任何族的 verdict **无影响**（全库仍 `bad=0`）；只有真的出现同 id 双条时才会红——即"开始报告真相"而非"制造噪声"。

### 5.4 用例

`tests/test_kb_placeholder_gate.py::test_duplicate_id_affects_result`（off 档下造 `OPS-2026-0001.md` + `OPS-2026-0001.draft.md` ⇒ 两条都进 `bad` 含 `duplicate_id`、无 `placeholder` 键、`--strict` 退出码 1）＋ `test_draft_name_conflict_diagnostic_not_silently_skipped`（诊断字段完整）。

## 6 OPS-2026-0005 实质复核（R3 第⑤条）

**复核发现（不靠机检计数）**：`e90c6dd` 提交的 OPS-0005 是 `kb_draft` 生成的**模板骨架**——`trigger` = 「待补充：最小复现触发条件」、`feedback.contract_row` = 「待补充：registry/矩阵新增行描述（或 null）」、正文三节全为「（待补充）」。而 `kb_hook` 报 **5/5 PASS**。

**这正是本线自己批判的形态**：机检只判非空 ⇒ 占位串通过。R3 的"不以机检计数自动关案"在本条上被实证。

**处置**：已补实（trigger 改为两段式实质问法、`contract_row` 改为可执行判据、正文三节补「复现步骤（v2 P5 的逐判据走过过程）／影响面／讨论」）。复核后读数：该条 `classify_placeholder(trigger)=None`、`feedback.contract_row` 无占位词、正文无「待补充」。

## 7 本轮未做（边界）

- 未激活任何族档位（四族 `off`）；未建存量登记 JSON；未实现 `--allow-placeholder` 豁免通道；未改 pre-push 挂载。
- 激活范围、回退验证、消费者验证另审。
