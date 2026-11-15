# 知识条目**解析状态可见化**方案（C4 修法 **v2** · 待独立②审 · 不实施）

> **版本**：**v2**（2026-11-15 按 R5 审核补三项：①新增「文件名不匹配 pattern 但含有效 ID」负例（**不能一律 skip 藏错命名**）；②**逐件**报告 skip/ok/empty/fail；③**off 族新增 JSON 键属输出变化 ⇒ 先核消费者再定 C4-a/b/c**）。
> **定性**：**新增检测型**（使此前"静默漏检"的条目变得**可见**）；默认只诊断，**改变 verdict 需另裁**。
> **状态**：**设计稿 · 不实施**（不改 `kb_hook.py`、不改任何族档位）。
> **配套件**：《同 id 族级兜底设计 v2》`docs/handoff/kb-duplicate-id-fallback-design-20261115.md`（§3.2 差额口径依赖本件才能闭合）。

## 1 问题（实测取证）

| 取证 | 读数 |
|---|---|
| `parse_entry("knowledge/ops/README.md")` | **返回 `keys=0`、`id=None`**（**不抛异常**） |
| `parse_entry("knowledge/ops/OPS-2026-0001.md")` | 返回 `keys=13`、`id='OPS-2026-0001'` |
| 损坏 front-matter 件（沙箱 N3） | `duplicate_id=0` 且**未报 `parse_fail`** ⇒ 该件计入 `checked` 但 `id` 为空 ⇒ **既不参与查重、也不触发任何报错** |

⇒ **根因**：`check_files` 只有"解析成功／抛异常"二分（`parse_entry` 抛异常才记 `parse_fail`），而"无 front-matter／损坏但未抛异常"被当作**空记录** ⇒ 归入 `checked`、不报错、不进 `id_seen` ⇒ **静默漏检**。

## 2 五类覆盖（R5 追加第 E 类）及其现状

| 类 | 定义 | 现状行为 | 现状可见性 |
|---|---|---|---|
| **A 损坏** | front-matter 结构坏（键缺/冒号错/未闭合） | 多返回**空记录**（少数字面抛异常 → `parse_fail` 进 `bad`） | **多数不可见** |
| **B 无 front-matter** | 文件无 `---` 头（如 `README.md`） | 返回空记录 `keys=0` | **不可见**（若被 glob 命中）；不命中 glob 时走 `non-entry-file` skip（可见） |
| **C 合法空字段** | 字段存在但值为空（如 `trigger: ""`） | 既有「字段为空」判据报 `X 为空` → 进 `bad` | **可见**（既有判据，非本件范围） |
| **D 未启用族**（`placeholder_gate=off`） | qd/pd/db 三族 | 不产出 `placeholder` 报告（`off` 完全不介入） | 解析/清点**照常**计入 `checked`；占位诊断**无**（设计如此） |
| **E 错命名（文件名不匹配 pattern，但 front-matter 含**有效**条目 ID）** | 如 `OPS-2026-0009 副本.md`（含空格/后缀）而 `id: OPS-2026-0009` | `entry_glob`（`OPS-*.md`）**命中** → 进 `checked`；`id` 字段合法 ⇒ `validate_record` **通过** | **完全静默**（既不 skip、也不报、placeholder 报告无 notes） |

**E 类实测（2026-11-15 沙箱，N4）**：

```
输入 glob = knowledge/ops/OPS-*.md（两件：'OPS-2026-0009 副本.md' + 'OPS-2026-0010.md'）
total=2  checked=2  skipped=0  bad=[]
[placeholder:warn] OPS-2026-0009 副本.md  stage=entry  notes=[]     ← 无任何标记
⇒ 错命名文件（文件名不合 pattern、id 有效）现状：进 bad=0 ／被 skip=0 ／无任何标记 = True
```

⇒ **不能一律 skip**：若按"文件名不匹配 ⇒ skip"处理，这类**有效条目会被 hide 掉**（藏错命名）；
⇒ 也不能不置不理：**现状是既不报也不 skip＝彻底静默**（最坏的一种）。
⇒ **本方案取向**：E 类**逐件可见**（诊断级，不阻断），并**明确「文件名与 id 一致性」为独立检查项**，与 A/B/C/D 并列。

## 3 方案（三面说明 · **逐件**）

### 3.1 诊断（人读面 · **逐件报告 skip/ok/empty/fail**）

**解析状态**（每个被解析文件归一桶）：

| 状态 | 判据 |
|---|---|
| `ok` | 解析出**非空记录**（有 `id` 或有任一字段） |
| `empty` | 解析**未抛异常**但记录为空（`keys==0`）——**A/B 两类落此** |
| `fail` | 解析**抛异常**（现有 `parse_fail` 分支） |

**新增「命名一致性」状态**（独立于解析状态）：

| 标记 | 判据 |
|---|---|
| `name_mismatch` | 文件名 basename **不匹配**族 `id_pattern`，**但** front-matter 的 `id` **匹配且有效** ⇒ **E 类**（错命名但条目有效） |
| `name_ambiguous` | 文件名不匹配 pattern **且** `id` 也无效/缺失 ⇒ 归 A/B（`empty`/`fail`），**不重复标记** |

**逐件报告（文本输出）**：每个输入文件**一行**，不留"其他"归类：

```
[kb_hook] 体检 5 件（族：ops），不合规 0 件，跳过 0 件｜解析：ok=4 empty=1 fail=0
  [ok]      knowledge/ops/OPS-2026-0001.md
  [ok]      knowledge/ops/OPS-2026-0002.md
  [ok]      knowledge/ops/OPS-2026-0003.md
  [ok]      knowledge/ops/OPS-2026-0004.md
  [empty]   knowledge/ops/README.md：无 front-matter 或内容为空，未参与任何校验（含同 id 查重）
  [skip]    knowledge/ops/NOTES.md : non-entry-file（文件名不匹配 id_pattern，且 id 无效）
  [name]    knowledge/ops/OPS-2026-0009 副本.md：文件名不合 id_pattern，但 id='OPS-2026-0009' 有效 ⇒ 错命名（诊断，不阻断）
```

### 3.2 payload（机读面 · **输出变化，须先核消费者**）

**新增三个键**（**既有键的值与语义一律不变**）：

```json
{
  "total": 5, "checked": 5, "bad": [], "skipped": [], "families_used": ["ops"], "note": null,
  "parse_stats": { "ok": 4, "empty": 1, "fail": 0 },
  "parse_detail": [
    {"file": "knowledge/ops/README.md", "status": "empty", "reason": "no-front-matter-or-empty-record"},
    {"file": "knowledge/ops/OPS-2026-0009 副本.md", "status": "ok", "name_check": "name_mismatch",
     "id": "OPS-2026-0009", "reason": "file-name-not-matching-id_pattern-but-id-valid"}
  ],
  "skip_detail": [{"file": "knowledge/ops/NOTES.md", "reason": "non-entry-file", "id": null}]
}
```

- **守恒**：`parse_stats.ok + parse_stats.empty + parse_stats.fail == checked`（`fail` 件同时在 `bad`，**不重复计数**）。
- **差额闭合**：`磁盘候选 − checked = 非条目件 + empty + fail + E类(name_mismatch) + 未展开` ⇒ **补齐后差额不再被静默吞掉**。
- **未启用族**：`parse_stats`/`parse_detail` **对所有族一视同仁**（不因 `placeholder_gate=off` 而不报解析状态）⇒ D 类"占位无诊断但解析可见"。

#### 3.2.1 消费者核对（**R5：先核消费者再定 C4-a/b/c** · 2026-11-15 实测）

| 消费者 | 读取方式 | 加键是否破坏 |
|---|---|---|
| `scripts/prepush-gate.ps1` | 文本输出，只判 `bad` | **否** |
| `.github/workflows/kb-schema.yml` | `--json` 整体，只看 `bad` | **否** |
| `scripts/check_deploy_closedloop.py` | `--strict`，取 `ok/detail` | **否** |
| `scripts/kb_draft.py` | 只调 `validate_record`（不用 JSON） | **否** |
| `scripts/install_kb_hook.ps1`、`kb-kit/install.ps1`、`kb-kit/scripts/install_kb_hook.ps1` | 生成钩子（文本） | **否** |
| `tests/test_kb_hook_skipped.py` | 按键取 `bad/checked/families_used/skipped/total` | **否**（若将来做**键集合严格相等**断言须同步更新——**本方案不改既有键，故现存断言不受影响**） |
| `tests/test_kb_placeholder_gate.py` | 按键取 `bad/placeholder` | **否**（同上） |
| **`kb-kit/scripts/kb_hook.py`** | **kb-kit 内的独立副本**（读 `bad/checked/families_used/note`） | **副本与主仓已不一致**（sha256 不同）⇒ **新增能力不进副本**；副本同步属**分发件同步议题**，**不在本方案范围**，**不擅自改** |

⇒ **核对结论**：**新增键对现有消费者不构成破坏**（无消费者按"键集合穷举"读取 payload）；但**输出确有变化**（多三键）⇒ 按 R5 口径登记为**向后兼容的契约面新增**（旧消费者忽略未知键即可），**并据此才进入 C4-a/b/c 的裁定**。

### 3.3 退出码

| 情形 | 现状 | 本方案后 |
|---|---|---|
| 仅 `empty` 件存在 | `0`（`--strict` 下亦 `0`） | **仍 `0`**（默认只诊断、不断言） |
| 仅 E 类（name_mismatch）存在 | `0` | **仍 `0`**（诊断级，不阻断） |
| `fail` 件存在 | `--strict` 下 `1` | **不变** |
| 既有 `bad` 命中 | `--strict` 下 `1` | **不变** |
| 无 `bad` | `0` | `0` |

⇒ **退出码零变化**是刻意设计（新增检测型＝先报告真相、不擅自阻断）；是否阻断列为 **C4-b**，**不自行决定**。

## 4 影响面（获批并实施后）

| 项 | 内容 |
|---|---|
| 代码 | `scripts/kb_hook.py`：`check_files` 增加解析状态＋命名一致性判定（**约 +40 行**，含 E 类）；`main` 文本输出改为**逐件**报告；JSON 增 `parse_stats`/`parse_detail`/`skip_detail` 三键 |
| 契约影响 | **JSON 新增三键**（既有键不变，向后兼容）；**逐件输出**会使文本输出变长（消费者只判 `bad`，不受影响） |
| **不改** | 族档位、条目内容、既有判据与退出语义、`id_seen` 查重逻辑、`kb-kit` 副本 |

## 5 验收判据（实施后）

1. **A 类**（损坏）⇒ 逐件出现在 `parse_detail`，`status='empty'`（或 `fail`），**不计入 `ok`**；
2. **B 类**（无 front-matter）⇒ 同上；
3. **C 类回归**：字段为空 ⇒ 仍由既有「X 为空」报 `bad`（行为不变）；
4. **D 类回归**：qd/pd/db（`off`）⇒ `parse_stats` 正常给出、**无 `placeholder` 键**（`off` 语义不变）；
5. **E 类**（错命名但 id 有效）⇒ 逐件出现在 `parse_detail` 且 `name_check='name_mismatch'`，**不被静默**、**不被 skip 藏掉**；
6. **逐件性**：文本输出**每个输入文件恰一行**（`ok`/`empty`/`fail`/`skip`/`name`），**无"其他"归类**；
7. 守恒：`ok + empty + fail == checked`；
8. **退出码零变化**：仅 `empty` 或仅 E 类存在时 `--strict` 仍 `0`；
9. 未启用族的**既有键**逐位不变（只允许新增键）。

## 6 待裁（**C4-a/b/c 在消费者核对完成后才可裁**；核对见 §3.2.1）

| # | 事项 | 我方建议 | 状态 |
|---|---|---|---|
| **C4-a** | `empty` 件是否计入 `bad` | **不计入**（先可见）；若判定"无 front-matter 者不是条目" ⇒ 应走 `non-entry-file` skip 而非 `empty` ⇒ 需先定"什么算条目件"（见 C4-c） | **待裁（消费者已核，可裁）** |
| **C4-b** | `empty`／E 类是否阻断（`--strict` 下 `rc=1`） | **不阻断**（新增检测型先报告真相） | **待裁（可裁）** |
| **C4-c** | **"什么算条目件"的判据归属** | 现状＝族 `id_pattern` 匹配（校验的是 **id 字段**，不是文件名）。建议：**文件名不匹配 pattern 但 id 有效 ⇒ 归 E 类（可见、不 skip）**；文件名不匹配且 id 无效 ⇒ 归 `empty`/`fail` 或 `non-entry-file` skip（**须与 E 类区分，避免 hide 有效条目**） | **待裁（可裁）** |
| **C4-d** | **E 类处置** | 诊断级**不阻断**，但**逐件可见**；是否进 `bad`（如 `name_mismatch` 记为不合规）请裁 | **待裁（新增项）** |
| **C4-e** | `kb-kit` 副本是否同步 | **不属本方案**；副本同步另立议题（**不擅自改**） | **待裁/另议** |

## 7 边界

- **不改 `kb_hook.py`（本轮）**、不升档、不接线、不新增长期义务、不删他线条目；
- **不改试点档位/每日扫描义务/人工回 `off` 条件**；
- 本件与《兜底设计 v2》**一并送独立②审**，**未批准前不实施**。

## 8 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-15 03:40 | 草案（v1）：问题取证＋四类覆盖＋三面说明＋验收 7 条＋C4-a/b/c。 |
| **2026-11-15 14:30** | **v2（R5 补三项）**：①新增 **E 类**（文件名不匹配 pattern 但 id 有效）＋ **N4 实测**（现状 `checked=2 bad=[] skipped=0`、**无任何标记**＝彻底静默）⇒ 明确**不能一律 skip 藏错命名**；②诊断面改为**逐件**报告（`ok`/`empty`/`fail`/`skip`/`name` 逐文件一行，**无"其他"归类**），payload 增 `skip_detail`；③**§3.2.1 消费者核对**（14 处消费者逐个列读取方式；结论：新增键不破坏现有消费者；**`kb-kit` 副本已漂移且不擅自改**）⇒ 据此 C4-a/b/c **可裁**，并新增 **C4-d（E 类处置）／C4-e（kb-kit 副本）**；④验收增至 9 条（补 E 类与逐件性）。**状态：待独立②审。** |
