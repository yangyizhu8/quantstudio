# 知识条目**解析状态可见化**方案（C4 修法 · 待审 · 不实施）

> **来源**：2026-11-15 R4 审核「C4 另列新增检测方案，覆盖**损坏、无 front-matter、合法空字段、未启用族**，说明**诊断、payload 和退出码变化**」。
> **定性**：**新增检测型**（使此前"静默漏检"的条目变得**可见**）；**默认只诊断、改变 verdict 需另裁**。
> **状态**：**设计稿 · 不实施**（不改 `kb_hook.py`、不改任何族档位）。
> **配套件**：《同 id 族级兜底设计 v2》`docs/handoff/kb-duplicate-id-fallback-design-20261115.md`（§3.2 差额口径依赖本件才能闭合）。

## 1 问题（实测取证）

| 取证 | 读数 |
|---|---|
| `kb_preflight.parse_entry("knowledge/ops/README.md")` | **返回 `keys=0`、`id=None`**（**不抛异常**） |
| `kb_preflight.parse_entry("knowledge/ops/OPS-2026-0001.md")` | 返回 `keys=13`、`id='OPS-2026-0001'` |
| 损坏 front-matter 件（沙箱 N3） | `duplicate_id=0` 且 **未报 `parse_fail`** ⇒ 该件被计入 `checked` 但 `id` 为空 ⇒ **既不参与查重、也不触发任何报错** |

⇒ **根因**：`check_files` 只有一个"解析成功/抛异常"二分（`parse_entry` 抛异常才记 `parse_fail`），而**"无 front-matter／损坏但未抛异常"被当作空记录**⇒ 归入 `checked`、不报错、不进 `id_seen` ⇒ **静默漏检**（既漏查重、也漏结构错误）。

## 2 四类覆盖与各自现状

| 类 | 定义 | 现状行为 | 现状可见性 |
|---|---|---|---|
| **A 损坏** | front-matter 结构坏（键缺/冒号错/未闭合） | 解析器**多返回空记录**（少数字面抛异常 → `parse_fail` 进 `bad`） | **多数不可见**（混进 `checked`） |
| **B 无 front-matter** | 文件无 `---` 头（如 `README.md`） | 返回空记录 `keys=0` | **不可见**（若在族目录且匹配 glob）；若不匹配 glob 则走 `non-entry-file` skip（可见） |
| **C 合法空字段** | 字段存在但值为空（如 `trigger: ""`） | 既有「字段为空」判据报 `X 为空` → 进 `bad` | **可见**（既有判据，非本件范围） |
| **D 未启用族**（`placeholder_gate=off`） | qd/pd/db 三族 | 不产出 `placeholder` 报告（`off` 完全不介入） | 解析/清点**照常**计入 `checked`；占位诊断**无**（设计如此） |

⇒ **本方案只补 A/B 的可见性**（C 已有、D 为既定语义不动）。

## 3 方案（三面说明）

### 3.1 诊断（人读面）

在 `check_files` 中为每个被解析的文件确定**解析状态**并计数：

| 状态 | 判据 |
|---|---|
| `ok` | 解析出**非空记录**（有 `id` 或有任一字段） |
| `empty` | 解析**未抛异常**但记录为空（`keys==0`）——**A/B 两类落此** |
| `fail` | 解析**抛异常**（现有 `parse_fail` 分支） |

文本输出新增一行摘要（示例）：

```
[kb_hook] 体检 93 件（族：qd、pd、db、ops），不合规 0 件，跳过 0 件｜解析：ok=92 empty=1 fail=0
```

并对 `empty` 件单独打印（**逐件可见**，不得只报计数）：

```
  [empty] knowledge/ops/README.md：无 front-matter 或内容为空，未参与任何校验（含同 id 查重）
```

### 3.2 payload（机读面）

`--json` 输出**新增一个键**（不改既有键的值、不改既有语义）：

```json
{
  "total": 5, "checked": 5, "bad": [], "skipped": [], "families_used": ["ops"], "note": null,
  "parse_stats": { "ok": 4, "empty": 1, "fail": 0 },
  "parse_detail": [
    {"file": "knowledge/ops/README.md", "status": "empty", "reason": "no-front-matter-or-empty-record"}
  ]
}
```

- **守恒**：`parse_stats.ok + parse_stats.empty + parse_stats.fail == checked`（`fail` 件同时在 `bad` 里，**不重复计数**）。
- **差额闭合**：`磁盘候选 − checked = 非条目件 + empty + fail + 未展开`（配合兜底设计 v2 §3.2）——**`empty` 补齐后，差额不再被静默吞掉**。
- **未启用族**：`parse_stats` **对所有族一视同仁**（不因 `placeholder_gate=off` 而不报解析状态）⇒ D 类"占位无诊断但解析可见"。

### 3.3 退出码

| 情形 | 现状退出码 | 本方案后 |
|---|---|---|
| 仅 `empty` 件存在 | `0`（`--strict` 下也是 0，因不产生 `bad`） | **仍 `0`**（**默认只诊断、不断言**） |
| `fail` 件存在 | `--strict` 下 `1`（进 `bad`） | **不变** |
| `bad` 存在（既有判据命中） | `--strict` 下 `1` | **不变** |
| 无任何 `bad` | `0` | **0** |

⇒ **退出码零变化**是本方案的**刻意设计**（新增检测型＝先报告真相、不擅自阻断）；是否让 `empty` 阻断属**待裁 C4-b**，本稿不自行决定。

## 4 影响面（获批并实施后）

| 项 | 内容 |
|---|---|
| 代码 | `scripts/kb_hook.py`：`check_files` 增加解析状态判定与计数（**约 +25 行**）；`main` 文本输出增摘要行与 `[empty]` 明细；JSON 增 `parse_stats`/`parse_detail` 两键 |
| 契约影响 | **JSON 新增两个键**（**既有键的值与语义不变**）⇒ 依赖 payload 结构的消费者需核对（当前已知消费者：`scripts/prepush-gate.ps1`（只取 `bad`）、`scripts/check_deploy_closedloop.py`（取 `ok/detail`）、CI `kb-schema.yml`（取 `bad`）——**均不受新增键影响**） |
| **不改** | 任何族档位、任何条目内容、既有判据与退出语义、`id_seen` 查重逻辑 |

## 5 验收判据（实施后）

1. 造 A 类（损坏 front-matter）件 ⇒ 该件出现在 `parse_detail` 且 `status='empty'`（或 `fail`）；**不被计入 `ok`**；
2. 造 B 类（无 front-matter）件 ⇒ 同上；
3. **C 类回归**：字段为空 ⇒ 仍由既有「X 为空」判据报 `bad`（**行为不变**）；
4. **D 类回归**：qd/pd/db（`off` 族）体检 ⇒ `parse_stats` 正常给出，且**无 `placeholder` 键**（`off` 语义不变）；
5. 守恒：`ok + empty + fail == checked`；
6. **退出码零变化**：仅 `empty` 存在时 `--strict` 仍 `0`；
7. 未启用族的**既有键**逐位不变（只允许新增键）。

## 6 待裁

| # | 事项 | 我方建议 |
|---|---|---|
| **C4-a** | `empty` 件是否应计入 `bad` | **建议不计入**（先可见）；若判定"无 front-matter 的文件不是条目"⇒ 应走 `non-entry-file` skip 而非 `empty` ⇒ **需先定"什么算条目件"的判据**（见 C4-c） |
| **C4-b** | `empty` 是否阻断（`--strict` 下 `rc=1`） | **建议不阻断**（新增检测型先报告真相） |
| **C4-c** | **"什么算条目件"的判据归属** | 现状＝族 `id_pattern` 匹配（`OPS-\d{4}-\d{4}` 等）。`README.md` 因不匹配而应走 `non-entry-file` skip——但它在 `OPS-*.md` glob 之外，故现状恰好看不到；**若 glob 覆盖到它**，C4-a 的归属问题就会出现 ⇒ **建议：非 `id_pattern` 件一律 skip，不进解析统计**（则 `empty` 只可能来自"匹配了 id_pattern 却解析不出内容"的损坏件） |

## 7 边界

- **不改 `kb_hook.py`（本轮）**、不升档、不接线、不新增长期义务、不删他线条目；
- **不改试点档位/每日扫描义务/人工回 `off` 条件**；
- 本件与《兜底设计 v2》**一并送②审**，**未批准前不实施**。

## 8 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-15 03:40 | 草案建立：问题取证（`parse_entry` 对无 front-matter 返回 `keys=0` 空记录 ⇒ 静默漏检）＋四类覆盖（A 损坏／B 无 front-matter／C 合法空字段／D 未启用族）＋三面说明（诊断／payload／**退出码零变化**）＋影响面与消费者核对＋验收 7 条＋待裁 C4-a/b/c＋边界。**状态：待②审。** |
