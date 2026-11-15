# 知识条目**解析状态可见化**方案（C4 修法 **v4** · 待独立②审 · 不实施）

> **版本**：**v4**（2026-11-15 按 R7 审核：①**合法 ID 只意味着进入条目校验，不意味着直接归 `ok`**；②**文件名错误只附标签**，其它字段为空或非法仍按契约进 `empty`/`fail`；③交付**组合表及差异**；④**kb-kit 分发范围**另列）。
> **定性**：**新增检测型**（使此前"静默漏检"的条目变得**可见**）；默认只诊断，**改变 verdict 需另裁**。
> **状态**：**设计稿 · 不实施**（不改 `kb_hook.py`、不改任何族档位、**不自动同步 kb-kit 副本**、**不延长 ops 观察窗**）。
> **配套件**：《同 id 族级兜底设计 v2》`docs/handoff/kb-duplicate-id-fallback-design-20261115.md`。

## 1 问题（实测取证）

| 取证 | 读数 |
|---|---|
| `parse_entry("knowledge/ops/README.md")` | **返回 `keys=0`、`id=None`**（**不抛异常**） |
| `parse_entry("knowledge/ops/OPS-2026-0001.md")` | 返回 `keys=13`、`id='OPS-2026-0001'` |
| 损坏 front-matter 件（沙箱 N3） | `duplicate_id=0` 且**未报 `parse_fail`** ⇒ 计入 `checked` 但 `id` 为空 ⇒ **既不参与查重、也不触发任何报错** |

⇒ **根因**：`check_files` 只有"解析成功／抛异常"二分，"无 front-matter／损坏但未抛异常"被当作**空记录** ⇒ 归入 `checked`、不报错、不进 `id_seen` ⇒ **静默漏检**。

## 2 五类覆盖及其现状

| 类 | 定义 | 现状行为 | 现状可见性 |
|---|---|---|---|
| **A 损坏** | front-matter 结构坏 | 多返回**空/残记录**（少数字面抛异常 → `parse_fail` 进 `bad`） | **多数不可见** |
| **B 无 front-matter** | 文件无 `---` 头 | 返回空记录 `keys=0` | **不可见**（若被 glob 命中） |
| **C 合法空字段** | 字段存在但值为空 | 既有「字段为空」判据报 `X 为空` → 进 `bad` | **可见** |
| **D 未启用族**（`off`） | qd/pd/db | 不产出 `placeholder` 报告 | 解析/清点**照常**计入 `checked` |
| **E 非规范命名 / 命名冲突** | basename ≠ `<id>.md`；或 basename 前缀 id ≠ front-matter `id` | 现状**无任何标记**（既不报、也不 skip、placeholder `notes=[]`） | **完全静默** |

**N4 实测（2026-11-15 沙箱）**：文件 `OPS-2026-0009 副本.md`（`id: OPS-2026-0009`）⇒ `total=2 checked=2 skipped=0 bad=[]`，placeholder 报告 `notes=[]` ⇒ **彻底静默**。

## 3 方案（三面说明）

### 3.1 **正交模型：互斥计数桶 ＋ 可叠加诊断标签**（桶由**契约**决定，不由 id 决定）

| 类别 | 取值 | 语义 | 互斥性 | 是否参与计数 |
|---|---|---|---|---|
| **互斥计数桶**（解析＋内容契约状态） | `ok` / `empty` / `fail` | `ok`＝**解析出非空记录且契约校验无错误**；`empty`＝**解析空/残** **或** **契约字段为空/非法**（含 `id` 非法）；`fail`＝**解析抛异常** | 每件恰一桶 | **是**（构成 `checked`） |
| **独立集合** | `skipped` | 未进入解析（not_found / non-entry-file / no_family_match / glob_no_match） | 与 `checked` **互斥** | 是（`total` 的另一部分） |
| **可叠加诊断标签** | `name_not_canonical` / `name_id_conflict` / `id_missing` / `field_empty` / `dup_id` | 额外诊断事实 | **可与任一桶共存** | **否**（只进 `parse_detail[].tags`） |

**R7 关键澄清（本版修正 v3）**：
1. **合法 ID 只意味着"进入条目校验"**（⇒ 进 `checked`、参与查重与契约校验），**不意味着桶归 `ok`**。
2. **桶由契约判据决定**：只要有任一契约字段为空/非法（含 `id` 非法或缺失）⇒ 桶 = **`empty`**（并按既有契约进 `bad`）。
3. **文件名错误只附标签**（`name_not_canonical` / `name_id_conflict`），**不改桶**——除非同时存在契约字段空/非法（那时桶由契约决定）。
4. 标签**不参与任何等式** ⇒ 增删标签不破坏守恒。

### 3.2 **total 守恒（明确式）**

```
(1) total   = checked + len(skipped)                        # 输入 → 检查流程 + 跳过
(2) checked = parse_stats.ok + parse_stats.empty + parse_stats.fail   # 桶互斥且穷尽
(3) total   = expanded + glob_misses                        # 输入侧展开口径
```

- **(1) 已被现有测试锁定**：`tests/test_kb_hook_skipped.py` **L81／L92／L131** 断言 `res["total"] == res["checked"] + len(res["skipped"])` ⇒ **不得破坏**。
- **(2) 由桶互斥性保证**；`fail` 件同时在 `bad`，但在 `checked` 内**只计一次**。
- **(3) 与兜底设计 v2 §3.2 的差额口径对接**（差额＝非条目件＋empty＋fail＋E类＋未展开）。
- **标签不进入任何等式**。

### 3.3 **条目件判定与命名标签（硬判据）**

> **硬判据 A（条目件）**：front-matter 的 `id` **存在且匹配族 `id_pattern`** ⇒ **条目件** ⇒ 进 `checked`、进条目校验、**不得被 skip 隐藏**；
> `id` 缺失/不匹配 ⇒ **非条目件**（`id_missing` 标签，或走既有 `non-entry-file` skip）。
> **合法 ID ≠ 桶为 `ok`**：仍须通过契约校验（§3.1-2）。

> **硬判据 B（命名标签，仅标签、不改桶、不 skip）**：
> - `name_not_canonical`：**basename ≠ `<id>.md`**（如 `OPS-2026-9009 副本.md`）
> - `name_id_conflict`：basename 按 `id_pattern` **前缀**抽出的 id 串 **≠** front-matter 的 `id`
>
> ⚠️ **口径修正（v3→v4）**：v3 写"basename 不匹配 `id_pattern`"——但 `id_pattern` 在实现中按 **`re.match` 前缀匹配**，
> 故 `X 副本.md`、`X.draft.md` **都会"匹配"**，该判据几乎永不触发。**v4 改用"basename ≠ `<id>.md`"**（规范命名）＋前缀 id 一致性双标签。

### 3.4 诊断（人读面 · 逐件）

每个输入文件**恰一行**（不留"其他"归类）；桶与标签并列：

```
[kb_hook] 体检 5 件（族：ops），不合规 0 件，跳过 0 件｜桶：ok=4 empty=1 fail=0｜标签：name_not_canonical=1
  [ok]        knowledge/ops/OPS-2026-0001.md
  [empty]     knowledge/ops/README.md：解析为空（无 front-matter 或内容为空），未参与任何校验
  [ok+name]   knowledge/ops/OPS-2026-9009 副本.md：桶=ok；标签=name_not_canonical（basename ≠ <id>.md；id 有效 ⇒ 可见，不 skip）
  [empty+id]  knowledge/ops/OPS-2026-9005.md：桶=empty（契约字段不合法）；标签=id_missing
```

### 3.5 payload（机读面）

**新增三键**（**既有键的值与语义一律不变**；标签在 `parse_detail[].tags`，**不新增计数字段**）：

```json
{
  "total": 5, "checked": 5, "bad": [], "skipped": [], "families_used": ["ops"], "note": null,
  "parse_stats": { "ok": 4, "empty": 1, "fail": 0 },
  "parse_detail": [
    {"file": "knowledge/ops/README.md", "status": "empty", "tags": [],
     "reason": "parse-empty-or-record-invalid"},
    {"file": "knowledge/ops/OPS-2026-9009 副本.md", "status": "ok",
     "tags": ["name_not_canonical"], "id": "OPS-2026-9009",
     "reason": "basename-not-canonical-but-id-valid"}
  ],
  "skip_detail": [{"file": "knowledge/ops/NOTES.md", "reason": "non-entry-file", "id": null}]
}
```

- **守恒**：`ok+empty+fail == checked`；(1) `total == checked+len(skipped)`（现有测试锁定）。
- **未启用族**：`parse_stats`/`parse_detail` **对所有族一视同仁**（不因 `off` 而不报解析状态）。

#### 3.5.1 消费者逐件兼容锚（14 处 · R6 收，**本版沿用不变**）

| # | 消费者（文件:行号） | 读什么 | 加键是否破坏 |
|---|---|---|---|
| 1 | `scripts/prepush-gate.ps1:304`（调用）／`:307`（判 `bad`）／`:310`（遍历 `bad`）／`:309,315`（`checked`） | `bad`、`checked` | **否** |
| 2 | `.github/workflows/kb-schema.yml:64` | `--json` 整体 → `bad` | **否** |
| 3 | `scripts/check_deploy_closedloop.py:165`／`:170`／`:208` | `ok`/`detail` | **否** |
| 4 | `scripts/kb_draft.py:191`／`:213` | 仅 `validate_record` | **否** |
| 5 | `scripts/install_kb_hook.ps1:68`／`:35` | 文本＋退出码 | **否** |
| 6 | `kb-kit/install.ps1:70`／`:72,94,118` | 文件级引用 | **否**（见 §3.5.2） |
| 7 | `kb-kit/scripts/install_kb_hook.ps1:68` | 文本＋退出码 | **否** |
| 8 | `kb-kit/scripts/kb_draft.py:191`／`:213` | 仅 `validate_record` | **否** |
| 9 | `kb-kit/scripts/kb_hook.py:135`／`:142,146`／`:165-169` | 自身 payload | **否**（副本不含本方案能力） |
| 10 | `kb-kit/templates/kb-schema.yml:64` | CI 骨架 | **否** |
| 11 | `kb-kit/templates/kb-info.yaml:7` | 说明 | **否** |
| 12 | `scripts/check-staged-scope.py:12,13`／`:176-183` | **仅路径样例**，与 payload 无关 | **否** |
| 13 | `tests/test_kb_hook_skipped.py:80`／`:81,92,131`（**守恒断言**）／`:83,101,110`／`:118-120` | 既有键＋**守恒等式** | **否**（**守恒式必须保持**） |
| 14 | `tests/test_kb_placeholder_gate.py:149-150`／`:243-287` | `bad`、`placeholder` | **否**（若将来做键集合穷举断言须同步） |

⇒ **新增键对 14 处消费者均不构成破坏**；唯一硬约束＝**#13 的守恒等式必须保持**。

#### 3.5.2 **kb-kit 分发范围（另列）**

| 项 | 内容 |
|---|---|
| **性质** | **跨仓分发件**：`kb-kit/install.ps1:3`「把 KB 闭环装进**任意 git 项目**」；`-Target <项目根>`（README L28/L36/L39/L42/L48；`install.ps1:36,88`） |
| **安装动作** | `install.ps1:94-95` 拷 `scripts/{kb_preflight,kb_hook,kb_draft,install_kb_hook}.py/ps1` 到 Target；`:99` 生成 `knowledge/kb-info.yaml`；`:109` 拷 templates；`:118` 装 pre-push 钩子；`:123` 装 CI（`-InstallCI`） |
| **示例目标仓** | `install.ps1:32` ⇒ `C:\vibe-coding-study\vibe-coding-cn`；README L45 另示 `-Sync -CanonicalRepo D:\miniQMT策略实盘\QuantStudio` |
| **主仓 vs 副本差异（实测）** | `kb_hook.py`：主仓 **400 行** `ce38ed739ab19504…` vs 副本 **176 行** `dcc4df5f910c9b7d…`（**差 11096 B**，副本**无占位门能力**）；`kb_draft.py`：主仓与副本**逐位相同**（`399abf2aa81ff102…`，各 219 行） |
| **同步通道（唯一）** | `install.ps1:10` ⇒ `install.ps1 -Sync -CanonicalRepo <主仓根>`（**维护者同步**）；`-Upgrade`（L42）只覆盖 Target 的 `scripts/` |
| **本轮处置** | **不同步副本、不改副本**（R6/R7 边界）；副本与主仓的能力差异**单列于此**，同步属**另立议题**（C4-e），**无承接方** |
| **分发范围影响** | 若不处理：凡经 kb-kit 安装的仓，其 `kb_hook` **永远无占位门能力**（即使主仓已具备）⇒ 主仓的新增检测能力**不会**跨仓扩散 |

### 3.6 退出码

| 情形 | 现状 | 本方案后 |
|---|---|---|
| 仅 `empty` 件存在 | `0` | **仍 `0`** |
| 仅命名标签存在 | `0` | **仍 `0`** |
| `fail` 件存在 | `--strict` 下 `1` | **不变** |
| 既有 `bad` 命中 | `--strict` 下 `1` | **不变** |

⇒ **退出码零变化**是刻意设计；是否阻断列 **C4-b**，**不自行决定**。

## 4 影响面（获批并实施后）

| 项 | 内容 |
|---|---|
| 代码 | `scripts/kb_hook.py`：`check_files` 增加**桶判定（契约驱动）＋命名标签（可叠加）**（**约 +45 行**）；`main` 文本输出改**逐件**；JSON 增三键 |
| 契约影响 | **JSON 新增三键**（既有键不变，向后兼容）；文本输出变长（消费者只判 `bad`）；**守恒 (1)(2)(3) 必须保持** |
| **不改** | 族档位、条目内容、既有判据与退出语义、`id_seen` 查重逻辑、**kb-kit 副本**、**ops 观察窗长度** |

## 5 验收判据（实施后）

1. **A 类**（损坏）⇒ 桶=`empty`（未抛异常时）或 `fail`（抛异常时），**不计入 `ok`**；
2. **B 类**（无 front-matter）⇒ 同上；
3. **C 类回归**：字段为空 ⇒ 仍由既有「X 为空」报 `bad`（行为不变），且桶=`empty`；
4. **D 类回归**：qd/pd/db（`off`）⇒ `parse_stats` 正常给出、**无 `placeholder` 键**；
5. **E 类** ⇒ 桶由**契约**决定，命名问题**只作标签**、逐件可见、**不被 skip 隐藏**；
6. **合法 ID ≠ 桶 ok**：合法 ID 且字段空 ⇒ 桶=`empty`（**必须如此**）；
7. **桶互斥**：任一件恰在一个桶内；
8. **标签可叠加**：标签数不影响任何计数；
9. **守恒**：`ok+empty+fail==checked`；`total==checked+len(skipped)`（**现有测试 L81/L92/L131 须仍绿**）；
10. **逐件性**：每个输入文件恰一行（桶或 skip），**无"其他"归类**；
11. **退出码零变化**：仅 `empty`／仅标签存在时 `--strict` 仍 `0`；
12. 未启用族的**既有键**逐位不变。

## 6 待裁

| # | 事项 | 我方建议 | 状态 |
|---|---|---|---|
| **C4-a** | `empty` 件是否计入 `bad` | **不计入**（诊断与契约错误分开；但**契约字段为空/非法仍按既有契约进 `bad`**——那不是本件新增） | **待裁** |
| **C4-b** | `empty`／标签是否阻断 | **不阻断** | **待裁** |
| **C4-c** | **"什么算条目件"** | 依 §3.3 硬判据 A：**`id` 存在且匹配 `id_pattern` ⇒ 条目件**；否则非条目件。**桶仍按契约判** | **待裁（口径已写明）** |
| **C4-d** | **命名标签的处置** | 诊断级**不阻断**、**逐件可见**；是否进 `bad` 请裁 | **待裁** |
| **C4-e** | **kb-kit 副本同步**（400 vs 176 行、差 11096 B、无占位门能力；`kb_draft` 副本逐位相同；唯一同步通道 `-Sync -CanonicalRepo`） | **另立议题**，本方案**不同步** | **待裁/另议** |

## 7 边界

- **不改 `kb_hook.py`（本轮）**、不升档、不接线、不新增长期义务、不删他线条目、**不自动同步 kb-kit 副本**、**不延长 ops 观察窗**；
- **不改试点档位/每日扫描义务/人工回 `off` 条件**；
- 本件与《兜底设计 v2》**一并送独立②审**，**未批准前不实施**。

## 9 组合表与差异表（2026-11-15 实测 · 沙箱，真库零改动）

**构造**：`kb_hook --files "knowledge/ops/OPS-*.md"`（含 `.draft` 模式），族 `ops`，`placeholder_gate=warn`；**读数现状**＝未实现 C4 时的真实行为；**目标**＝C4 实施后的桶/标签。

| # | 构造 | 目标桶 | 目标标签 | 现状（实测） | 差异 |
|---|---|---|---|---|---|
| **C1** | 合法 ID ＋ 合法文件名 ＋ 字段齐备 | `ok` | — | `checked`，`bad=0`，`fake=0` | 仅多出 `status=ok` 与桶计数 |
| **C2** | **合法 ID ＋ 错文件名** ＋ 字段齐备 | `ok` | `name_not_canonical` | `checked`，**`bad=0`，`fake=0`，`notes=[]` ⇒ 完全无标记** | **新增标签（现状不可见）** |
| **C3** | **合法 ID ＋ 错文件名 ＋ 空字段(trigger)** | **`empty`**（字段空，按契约） | `name_not_canonical` | `checked`，`bad=[trigger 为空]`，`fake=1` | 桶=`empty`（**不因 ID 合法而 ok**）；标签新增 |
| **C4** | 合法 ID ＋ 合法文件名 ＋ 空字段(trigger) | **`empty`** | — | `checked`，`bad=[trigger 为空]`，`fake=1` | 桶=`empty` |
| **C5** | **无 ID** ＋ 字段齐备（文件名合法） | **`empty`**（`id` 非法/缺失 ⇒ 契约不合规） | `id_missing` | `checked`，`bad=[id 非法…：None]` | 桶=`empty`（**不得因"其它字段齐备"而 ok**） |
| **C6** | 损坏 front-matter | **`empty`**（未抛异常；`id='[broken'` 且六字段空） | — | `checked`，`bad=[id 非法…；symptom 为空；…]`，`fake=1` | 桶=`empty`（**损坏未抛异常 ⇒ 不是 `fail`**） |

**三条守恒式核验（目标值）**：

| 式 | 目标值（本次 6 件组合） | 现状 |
|---|---|---|
| (1) `total == checked + len(skipped)` | `6 == 6 + 0` ✔ | **已成立**（现有测试锁定） |
| (2) `checked == ok + empty + fail` | `6 == 2 + 4 + 0` ✔（ok=C1,C2；empty=C3,C4,C5,C6；fail=0） | **不可计算**（现状无桶字段） |
| (3) `total == expanded + glob_misses` | `6 == 6 + 0` ✔ | **不可计算**（现状 payload 无展开分项） |

**本次实测暴露的两处口径错误（已在本版修正）**：
1. **「文件名不匹配 `id_pattern`」几乎不可触发**——`id_pattern` 实现为 `re.match` **前缀匹配**，`X 副本.md`／`X.draft.md` **都"匹配"** ⇒ v3 的该判据失效；v4 改用「**basename ≠ `<id>.md`**」＋「前缀 id 与 front-matter `id` 是否一致」双标签。
2. **`id` 不在 `required_fields` 内** ⇒ 「其它字段齐备」不能推出 `ok`；合法 ID 但 `id` 缺失/非法 ⇒ 桶=`empty`（C5）。

## 10 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-15 03:40 | v1：问题取证＋四类覆盖＋三面说明＋验收 7 条＋C4-a/b/c。 |
| 2026-11-15 14:30 | v2（R5）：E 类＋N4 实测＋逐件报告＋消费者核对＋C4-d/e。 |
| 2026-11-15 16:40 | v3（R6）：桶/标签正交＋三条守恒式＋E 类硬判据＋14 消费者逐件锚＋kb-kit 差异单列。 |
| **2026-11-15 17:50** | **v4（R7）**：①§3.1 桶改由**契约**决定（合法 ID **只意味着进入条目校验**，**不意味桶 ok**）；②命名问题**只附标签**，不影响桶；③新增 **§9 组合表与差异表**（C1–C6 六组合实测＋目标桶/标签＋现状＋差异＋三条守恒式核验）；④修正 v3 两处口径错误（**`id_pattern` 为前缀匹配 ⇒ "basename 不匹配"判据失效**，改用「basename ≠ `<id>.md`」＋前缀 id 一致性；**`id` 不在 `required_fields` ⇒ 不得因其它字段齐备而 ok**）；⑤§3.5.2 **kb-kit 分发范围**另列（任意 git 项目／安装动作／示例仓／唯一同步通道 `-Sync -CanonicalRepo`／不自动同步的后果）；⑥验收增至 12 条。**状态：待独立②审。** |
