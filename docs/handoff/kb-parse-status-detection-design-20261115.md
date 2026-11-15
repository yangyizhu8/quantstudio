# 知识条目**解析状态可见化**方案（C4 修法 **v3** · 待独立②审 · 不实施）

> **版本**：**v3**（2026-11-15 按 R6 审核四项：①**区分互斥计数桶与可叠加诊断标签**；②**明确 total 守恒**；③**有效 ID 错命名必须可见、不得以 skip 隐藏**（写为硬判据）；④**14 处消费者逐件保留读取锚**，**kb-kit 差异单列**）。
> **定性**：**新增检测型**（使此前"静默漏检"的条目变得**可见**）；默认只诊断，**改变 verdict 需另裁**。
> **状态**：**设计稿 · 不实施**（不改 `kb_hook.py`、不改任何族档位、**不同步 kb-kit 副本**）。
> **配套件**：《同 id 族级兜底设计 v2》`docs/handoff/kb-duplicate-id-fallback-design-20261115.md`（§3.2 差额口径依赖本件才能闭合）。

## 1 问题（实测取证）

| 取证 | 读数 |
|---|---|
| `parse_entry("knowledge/ops/README.md")` | **返回 `keys=0`、`id=None`**（**不抛异常**） |
| `parse_entry("knowledge/ops/OPS-2026-0001.md")` | 返回 `keys=13`、`id='OPS-2026-0001'` |
| 损坏 front-matter 件（沙箱 N3） | `duplicate_id=0` 且**未报 `parse_fail`** ⇒ 计入 `checked` 但 `id` 为空 ⇒ **既不参与查重、也不触发任何报错** |

⇒ **根因**：`check_files` 只有"解析成功／抛异常"二分（`parse_entry` 抛异常才记 `parse_fail`），而"无 front-matter／损坏但未抛异常"被当作**空记录** ⇒ 归入 `checked`、不报错、不进 `id_seen` ⇒ **静默漏检**。

## 2 五类覆盖及其现状

| 类 | 定义 | 现状行为 | 现状可见性 |
|---|---|---|---|
| **A 损坏** | front-matter 结构坏（键缺/冒号错/未闭合） | 多返回**空记录**（少数字面抛异常 → `parse_fail` 进 `bad`） | **多数不可见** |
| **B 无 front-matter** | 文件无 `---` 头（如 `README.md`） | 返回空记录 `keys=0` | **不可见**（若被 glob 命中）；不命中 glob 时走 `non-entry-file` skip（可见） |
| **C 合法空字段** | 字段存在但值为空（如 `trigger: ""`） | 既有「字段为空」判据报 `X 为空` → 进 `bad` | **可见**（既有判据，非本件范围） |
| **D 未启用族**（`placeholder_gate=off`） | qd/pd/db 三族 | 不产出 `placeholder` 报告（`off` 完全不介入） | 解析/清点**照常**计入 `checked`；占位诊断**无**（设计如此） |
| **E 错命名（文件名不匹配 pattern，但 front-matter 含**有效**条目 ID）** | 如 `OPS-2026-0009 副本.md` 而 `id: OPS-2026-0009` | `entry_glob`（`OPS-*.md`）**命中** → 进 `checked`；`id` 字段合法 ⇒ `validate_record` **通过** | **完全静默**（既不 skip、也不报、placeholder 报告无 notes） |

**E 类实测（2026-11-15 沙箱，N4）**：

```
输入 glob = knowledge/ops/OPS-*.md（'OPS-2026-0009 副本.md' + 'OPS-2026-0010.md'）
total=2  checked=2  skipped=0  bad=[]
[placeholder:warn] OPS-2026-0009 副本.md  stage=entry  notes=[]     ← 无任何标记
⇒ 错命名文件（文件名不合 pattern、id 有效）现状：进 bad=0 ／被 skip=0 ／无任何标记 = True
```

## 3 方案（三面说明）

### 3.1 **正交模型：互斥计数桶 ＋ 可叠加诊断标签**（R6 第①条）

**桶与标签是两条正交轴，不可混为一谈**：

| 类别 | 取值 | 语义 | 互斥性 | 是否参与计数 |
|---|---|---|---|---|
| **互斥计数桶**（解析状态） | `ok` / `empty` / `fail` | 该件**被解析后的结果** | **每件恰落入一个桶**（`ok` ⊕ `empty` ⊕ `fail`） | **是**（构成 `checked`） |
| **独立集合**（未进检查流程） | `skipped` | 该件**未进入解析**（not_found / non-entry-file / no_family_match / glob_no_match） | 与 `checked` **互斥** | 是（构成 `total` 的另一部分） |
| **可叠加诊断标签** | `name_mismatch` / `id_invalid` / `field_empty` / `dup_id` | 该件**额外**带上的诊断事实 | **可与任一桶共存**（一桶 + 0..n 标签） | **否**（只进 `parse_detail[].tags`，**不改任何计数**） |

**关键区分**：
- **`empty`（桶）** ≠ **`name_mismatch`（标签）**：前者说"**解析不出内容**"，后者说"**内容解析出来了，但命名与 id 不一致**"。
- **标签不参与任何等式** ⇒ 增标签**不会**破坏 `total`/`checked` 守恒（§3.2）。
- 同一件可同时带 `ok` + `name_mismatch` + `dup_id`（E 类且撞 id 时），**桶仍只算一次**。

### 3.2 **total 守恒（明确式）**（R6 第②条）

**三条等式（缺一即口径不闭合）**：

```
(1) total    = checked + len(skipped)                      # 输入 → 检查流程 + 跳过
(2) checked  = parse_stats.ok + parse_stats.empty + parse_stats.fail   # 桶互斥且穷尽
(3) total    = expanded + glob_misses                      # 输入侧展开口径
```

- **(1) 已被现有测试锁定**：`tests/test_kb_hook_skipped.py` **L81／L92／L131** 断言 `res["total"] == res["checked"] + len(res["skipped"])` ⇒ **本方案不得破坏该等式**（新增键与标签均不影响）。
- **(2) 由桶互斥性保证**；`fail` 件同时出现在 `bad` 中，但**在 `checked` 内只计一次**（不重复计数）。
- **(3) 是输入侧口径**，与 (1) 的差值即为"未被 glob 展开"的部分 ⇒ 与兜底设计 v2 §3.2 的差额口径对接。
- **标签不影响任何等式**（(2) 的三桶、(1) 的两侧都不含标签计数）。

### 3.3 **E 类硬判据：有效 ID 错命名必须可见，不得以 skip 隐藏**（R6 第③条）

> **硬判据**：**front-matter 的 `id` 匹配族 `id_pattern` 且非空 ⇒ 该件是"有效条目"**，
> **无论文件名如何**，必须出现在 `parse_detail` 中并带 `name_mismatch` 标签；
> **禁止**用 `skip`（`non-entry-file`）把有效条目移出检查流程（那等于用"隐藏"代替"报告"）。

**实现要点**：
1. `entry_stage`/路由判定**不以文件名为准**（文件名仅用于 `name_mismatch` 标签）；**`id` 合法 ⇒ 仍按条目件处理**（进 `checked`、参与查重）。
2. 真正的非条目件判定：**`id` 缺失或不合 pattern**（如 `README.md`）⇒ 走 `non-entry-file` skip（既有语义，不变）。
3. `name_mismatch` 的产生条件：**basename 不匹配 `id_pattern`** ∧ **`id` 匹配且非空** ⇒ 只加标签，**不改桶**。

### 3.4 诊断（人读面 · **逐件报告 skip/ok/empty/fail ＋ 标签**）

每个输入文件**恰一行**（不留"其他"归类）：

```
[kb_hook] 体检 5 件（族：ops），不合规 0 件，跳过 0 件｜解析桶：ok=4 empty=1 fail=0｜标签：name_mismatch=1
  [ok]      knowledge/ops/OPS-2026-0001.md
  [ok]      knowledge/ops/OPS-2026-0002.md
  [ok]      knowledge/ops/OPS-2026-0003.md
  [ok]      knowledge/ops/OPS-2026-0004.md
  [empty]   knowledge/ops/README.md：无 front-matter 或内容为空，未参与任何校验（含同 id 查重）
  [skip]    knowledge/ops/NOTES.md : non-entry-file（id 缺失或不合 id_pattern ⇒ 非条目件）
  [ok+name] knowledge/ops/OPS-2026-0009 副本.md：桶=ok；标签=name_mismatch（文件名不合 id_pattern，但 id='OPS-2026-0009' 有效 ⇒ **可见，不 skip**）
```

- 标签可多个：`[ok+name+dup]` 形式并列，**桶仍只计一次**。

### 3.5 payload（机读面）

**新增三个键**（**既有键的值与语义一律不变**；标签在 `parse_detail[].tags` 内，**不新增计数字段**）：

```json
{
  "total": 5, "checked": 5, "bad": [], "skipped": [], "families_used": ["ops"], "note": null,
  "parse_stats": { "ok": 4, "empty": 1, "fail": 0 },
  "parse_detail": [
    {"file": "knowledge/ops/README.md", "status": "empty", "tags": [],
     "reason": "no-front-matter-or-empty-record"},
    {"file": "knowledge/ops/OPS-2026-0009 副本.md", "status": "ok",
     "tags": ["name_mismatch"], "id": "OPS-2026-0009",
     "reason": "file-name-not-matching-id_pattern-but-id-valid"}
  ],
  "skip_detail": [{"file": "knowledge/ops/NOTES.md", "reason": "non-entry-file", "id": null}]
}
```

- **守恒**：`parse_stats.ok + parse_stats.empty + parse_stats.fail == checked`（§3.2 (2)）；`total == checked + len(skipped)`（§3.2 (1)，**现有测试已锁定**）。
- **差额闭合**：`磁盘候选 − checked = 非条目件 + empty + fail + E类(name_mismatch) + 未展开` ⇒ 配合兜底设计 v2 §3.2。
- **未启用族**：`parse_stats`/`parse_detail` **对所有族一视同仁**（不因 `placeholder_gate=off` 而不报解析状态）⇒ D 类"占位无诊断但解析可见"。

#### 3.5.1 消费者逐件读取锚（14 处 · R6 第④条）

| # | 消费者（文件:行号） | 读什么 | 加键是否破坏 |
|---|---|---|---|
| 1 | `scripts/prepush-gate.ps1:304`（调用）／`:307`（判 `bad` 非空）／`:310`（遍历 `bad`）／`:309,315`（`checked`） | 文本/JSON 的 `bad`、`checked` | **否** |
| 2 | `.github/workflows/kb-schema.yml:64` | `--json` 整体，只看 `bad`（L8 注释述升阻断） | **否** |
| 3 | `scripts/check_deploy_closedloop.py:165`（调用）／`:170`（存 `kb`）／`:208`（读 `ok`/`detail`） | `--strict` 结果与 `ok`/`detail` | **否** |
| 4 | `scripts/kb_draft.py:191`（`validate_record`）／`:213`（打印） | **不用 JSON**（只调函数） | **否** |
| 5 | `scripts/install_kb_hook.ps1:68`（生成钩子调用 `__STRICT__`）／`:35`（文档示例） | 文本 + 退出码 | **否** |
| 6 | `kb-kit/install.ps1:70`（拷贝 `kb_hook.py`）／`:72,94,118`（拷贝/安装） | 文件级引用 | **否**（但见 §3.5.2） |
| 7 | `kb-kit/scripts/install_kb_hook.ps1:68` | 文本 + 退出码 | **否** |
| 8 | `kb-kit/scripts/kb_draft.py:191`／`:213` | 只调 `validate_record` | **否** |
| 9 | `kb-kit/scripts/kb_hook.py`（**独立副本**，`:135` `checked`／`:142,146` `bad`／`:165-169` 文本） | 自身 payload | **否**（副本不含本方案能力，见 §3.5.2） |
| 10 | `kb-kit/templates/kb-schema.yml:64` | 模板（CI 骨架） | **否** |
| 11 | `kb-kit/templates/kb-info.yaml:7`（注释述消费者） | 模板说明 | **否** |
| 12 | `scripts/check-staged-scope.py:12,13`（docstring 示例）／`:176-183`（自测用例里把 `kb_hook.py` 当**路径样例**） | 与 payload **无关** | **否** |
| 13 | `tests/test_kb_hook_skipped.py:80`（`skipped`）／`:81,92,131`（**total 守恒断言**）／`:83,101,110`（skip reason）／`:118-120`（bad ⊆ checked） | 既有键 ＋ **守恒等式** | **否**（但**守恒式必须保持**，见 §3.2 (1)） |
| 14 | `tests/test_kb_placeholder_gate.py:149-150`（off ⇒ report 为 None）／`:243-287`（集成断言 `bad`/`placeholder`） | `bad`、`placeholder` | **否**（若将来做**键集合穷举**断言须同步） |

⇒ **结论**：**新增键对 14 处消费者均不构成破坏**；唯一硬约束是 **#13 的守恒等式必须保持**（新增标签/键不得改变 `total`/`checked`/`skipped` 的计数关系）。

#### 3.5.2 **kb-kit 差异（单列）**（R6 第④条后一半）

| 项 | 主仓 | kb-kit 副本 | 差异 |
|---|---|---|---|
| `kb_hook.py` | `scripts/kb_hook.py`，**400 行**，sha256 `ce38ed739ab19504…` | `kb-kit/scripts/kb_hook.py`，**176 行**，sha256 `dcc4df5f910c9b7d…` | **差 11096 字节**；副本**不含占位门能力**（查 `placeholder_gate` ⇒ **False**） |
| `kb_draft.py` | `scripts/kb_draft.py`，219 行，sha256 `399abf2aa81ff102…` | `kb-kit/scripts/kb_draft.py`，219 行，sha256 `399abf2aa81ff102…` | **逐位相同**（无漂移） |

⇒ **处置（边界内）**：**本方案不同步副本、不改副本**（R6 边界明载"不同步副本"）；副本仅作分发件，其与主仓的能力差异**单列于此**，作为**另立议题**（C4-e）；主仓新增能力**不会**自动进入副本。

### 3.6 退出码

| 情形 | 现状 | 本方案后 |
|---|---|---|
| 仅 `empty` 件存在 | `0` | **仍 `0`**（默认只诊断） |
| 仅 E 类（`name_mismatch` 标签）存在 | `0` | **仍 `0`**（标签不阻断） |
| `fail` 件存在 | `--strict` 下 `1` | **不变** |
| 既有 `bad` 命中 | `--strict` 下 `1` | **不变** |
| 无 `bad` | `0` | `0` |

⇒ **退出码零变化**是刻意设计（新增检测型＝先报告真相、不擅自阻断）；是否阻断列 **C4-b**，**不自行决定**。

## 4 影响面（获批并实施后）

| 项 | 内容 |
|---|---|
| 代码 | `scripts/kb_hook.py`：`check_files` 增加**桶判定（互斥）＋标签判定（可叠加）**（**约 +45 行**，含 E 类）；`main` 文本输出改为**逐件**报告；JSON 增 `parse_stats`/`parse_detail`/`skip_detail` 三键 |
| 契约影响 | **JSON 新增三键**（既有键不变，向后兼容）；**逐件输出**使文本输出变长（消费者只判 `bad`，不受影响）；**守恒等式 (1)(2)(3) 必须保持** |
| **不改** | 族档位、条目内容、既有判据与退出语义、`id_seen` 查重逻辑、**kb-kit 副本** |

## 5 验收判据（实施后）

1. **A 类**（损坏）⇒ 桶=`empty`（或 `fail`），**不计入 `ok`**；
2. **B 类**（无 front-matter）⇒ 同上；
3. **C 类回归**：字段为空 ⇒ 仍由既有「X 为空」报 `bad`（行为不变）；
4. **D 类回归**：qd/pd/db（`off`）⇒ `parse_stats` 正常给出、**无 `placeholder` 键**；
5. **E 类**（错命名但 id 有效）⇒ 桶=`ok` ＋ 标签 `name_mismatch`，**逐件可见**、**不被 skip 隐藏**；
6. **桶互斥**：任一件恰在一个桶内（`ok⊕empty⊕fail`）；
7. **标签可叠加**：E 类且撞 id 的件 ⇒ 桶计 1 次、标签 2 个，**计数不变**；
8. **守恒**：`ok+empty+fail==checked`；`total==checked+len(skipped)`（**现有测试 L81/L92/L131 须仍绿**）；
9. **逐件性**：文本输出**每个输入文件恰一行**（桶或 skip），**无"其他"归类**；
10. **退出码零变化**：仅 `empty` 或仅 E 类存在时 `--strict` 仍 `0`；
11. 未启用族的**既有键**逐位不变（只允许新增键）。

## 6 待裁（**C4-a/b/c 在消费者核对完成后才可裁**；核对见 §3.5.1）

| # | 事项 | 我方建议 | 状态 |
|---|---|---|---|
| **C4-a** | `empty` 件是否计入 `bad` | **不计入**（先可见）；"什么算条目件"见 C4-c | **待裁** |
| **C4-b** | `empty`／E 类是否阻断（`--strict` 下 `rc=1`） | **不阻断**（新增检测型先报告真相） | **待裁** |
| **C4-c** | **"什么算条目件"的判据归属** | 依 §3.3 硬判据：**`id` 合法 ⇒ 条目件（文件名只产标签，不产 skip）**；`id` 缺失/不合法 ⇒ 非条目件（skip） | **待裁（硬判据已写明，可裁）** |
| **C4-d** | **E 类处置** | 诊断级**不阻断**、**逐件可见**；是否进 `bad` 请裁 | **待裁** |
| **C4-e** | **kb-kit 副本**（已量化差异：400 vs 176 行、差 11096 B、无占位门能力；`kb_draft` 副本逐位相同） | **另立议题**，本方案**不同步** | **待裁/另议** |

## 7 边界

- **不改 `kb_hook.py`（本轮）**、不升档、不接线、不新增长期义务、不删他线条目、**不同步 kb-kit 副本**；
- **不改试点档位/每日扫描义务/人工回 `off` 条件**；
- 本件与《兜底设计 v2》**一并送独立②审**，**未批准前不实施**。

## 8 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-15 03:40 | 草案（v1）：问题取证＋四类覆盖＋三面说明＋验收 7 条＋C4-a/b/c。 |
| 2026-11-15 14:30 | v2（R5）：新增 **E 类**＋**N4 实测**（彻底静默）；诊断改**逐件**；**§3.2.1 消费者核对**；新增 C4-d/e；验收 9 条。 |
| **2026-11-15 16:40** | **v3（R6 四项）**：①§3.1 **正交模型**——**互斥计数桶**（`ok⊕empty⊕fail`，构成 `checked`）／**独立集合**（`skipped`，与 `checked` 互斥）／**可叠加诊断标签**（`name_mismatch`/`id_invalid`/`field_empty`/`dup_id`，**不参与计数**）；②§3.2 **total 守恒三条等式**，并标注 **(1) 已被 `tests/test_kb_hook_skipped.py` L81/L92/L131 锁定**；③§3.3 **E 类硬判据**（`id` 合法 ⇒ 条目件；文件名只产标签、**不得 skip 隐藏有效条目**）；④§3.5.1 **14 处消费者逐件读取锚**（文件:行号＋读什么＋影响）＋§3.5.2 **kb-kit 差异单列**（`kb_hook` 400 vs 176 行、差 11096 B、无占位门能力；`kb_draft` 副本逐位相同）；验收增至 11 条。**状态：待独立②审。** |
