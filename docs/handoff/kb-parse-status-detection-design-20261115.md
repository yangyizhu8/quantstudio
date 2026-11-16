# 知识条目**解析状态可见化**方案（C4 修法 **v6** · 待独立②审 · 不实施）

> **版本**：**v6**（2026-11-15 按 R9 审核：完整技术审未过——**完全失去族前缀的合法 ID 文件到不了 P1**。本版补**两级前门与 P1 可达性**、**完全错名组合的识别顺序/读取边界/预期**、**退出码真值表（新增诊断 vs 原契约错误）**。**仅改文档与静态清点**，不改 hook、不改配置契约、不升档、不接线/分发、**不延观察窗**、**未跑新沙箱**）。
> **定性**：**新增检测型**；默认只诊断，**改变 verdict 需另裁**。
> **配套件**：《同 id 族级兜底设计 v2》`docs/handoff/kb-duplicate-id-fallback-design-20261115.md`。

## 0 **候选身份（冻结标识 · 身份口径＝全文 hash）**

> **身份定义（R12 定谳）**：候选身份**以「文件全文 hash」为准**（不按"设计正文/元数据"分层）。
> 因**文档无法自含自身 hash**（写入即变，自指不可解），故身份采用**提交登记制**：
> **身份 = 该次提交的 commit SHA ＋ 由该 commit 实测得到的全文 sha256**，二者登记于下表与当轮上报块，可随时复核：
> `git show <commit>:<path>` 的输出经 `sha256` 计算，须与表中值**逐位一致**。

| 版本 | commit | 全文 sha256 | blob |
|---|---|---|---|
| v8 | `b7ade13` | `e16ca48e5a3d4ec780f2b3e50c85f869aff4103ed1c357ee34292a507f9a26a3` | `4c7e30052ae0` |
| **v9（当前身份基线）** | `61f2c39` | `18bd1ec3fa90a01419d37513cf290c62c39ddfa9845516133695dbc066acc115` | `2d03a35bcd2b` |
| v10（本轮：V1／V2 回退配方精确化） | **本轮提交后登记**（见 R13 上报块） | **同左** | — |
| v7（历史） | `e3d551d` | `b80a748ed8aba0be03753cf146fdfa35439b8bbecd0bfd370731af1cdf6cf41c` | `4143f8393311` |

| 项 | 值 |
|---|---|
| 配套件（同批送审） | 《同 id 族级兜底设计 v2》`kb-duplicate-id-fallback-design-20261115.md`，sha256 `0c84c89bf573de3bcee1090b17ac70bec2ad23bd7b56e9c306e0eeab66662322` |
| 冻结语义 | **设计候选**：已过 R5–R12 文档退修，**未授权实现**；实施须另走 §16 申请 |
| 复核命令 | `git show <commit>:docs/handoff/kb-parse-status-detection-design-20261115.md | sha256sum`（**只读，不落盘**）；v9 实测 **35912 B**（与审核主锚 35,912 B 一致） |

**状态口径（写死）**：
- **"已测基线"**＝本文所引读数来源，均产出于 **C4 实施之前**（历史实测）——**只证明改前行为**；
- **"预期"**＝C4 实施**之后**应有行为，**尚未实现、未经实测**，**不得当作实测申报**；
- **after 何时可称实测**＝实施后按 §13.3 双跑取得 `after.json` 且逐件比对 PASS ⇒ 届时另出实测表。

## 1 问题与现状取证（摘要）

| 取证 | 读数 |
|---|---|
| `parse_entry("knowledge/ops/README.md")` | **返回 `keys=0`、`id=None`**（**不抛异常**） |
| 损坏 front-matter 件（N3） | `duplicate_id=0` 且**未报 `parse_fail`** ⇒ 计入 `checked`、`id` 为空 ⇒ **不参与查重、不报错** |
| 合法 ID ＋ 错文件名（N4，带 `OPS-` 前缀） | `total=2 checked=2 skipped=0 bad=[]`，placeholder `notes=[]` ⇒ **完全无标记** |

## 2 识别链：**两级前门 ＋ 后门候选识别**（R9 核心修正）

### 2.1 现状实现（**静态清点**，未跑沙箱）

| 环节 | 实现位置 | 行为 |
|---|---|---|
| **前门① 输入展开** | `scripts/kb_hook.py:73-88` `_expand_files` | `--files` 含 `* ? [` ⇒ 按 `repo_root` glob 展开；无命中 ⇒ `glob_no_match` skip；字面路径原样保留 |
| **前门② 族归属** | `scripts/kb_hook.py:61-70` `_match_family` | **仅按族 `entry_glob` 做 `fnmatch`**（`**` 转 `*`）；**不看族 `dir`**、**不看文件名族前缀**、**不读文件内容**；未命中任何族 ⇒ `None` |
| **未命中处理** | `scripts/kb_hook.py:319-321` | `_match_family` 为 `None` ⇒ `skip(no_family_match)` ⇒ **该文件内容从不被读取**（不进解析、不进桶、不进查重） |
| **后门 候选识别**（C4 新增） | v5 §2 P1/P2/P3 | **仅对已通过前门②的文件**执行（当前 v5 方案未意识到前门②的存在） |

### 2.2 **P1 可达性前提（R9 指出的漏洞）**

> **P1（合法 ID 条目）判定只在"文件名匹配族 `entry_glob`"的文件上可达。**
> `knowledge/ops/notes.md` 即使内含 `id: OPS-2026-0007`（合法），**也匹配不到 `knowledge/ops/OPS-*.md`** ⇒
> **前门② 直接 `skip(no_family_match)` ⇒ front-matter 从未被读取 ⇒ P1 不可达、该件完全不可见。**

⇒ **v5 方案的 P1 规则只在"族前缀命名"前提下有效**；完全错名的合法 ID 件属**族外文件**，当前与目标实现**都不覆盖**。**本版把这一盲区显式化并给出处置选项（§2.4）**。

### 2.3 识别顺序（**三段，边界清晰**）

```
① 前门① 输入展开   ── 未命中 glob ⇒ skip(glob_no_match)               [内容不读]
② 前门② 族归属     ── 未匹配任何族 entry_glob ⇒ skip(no_family_match)  [内容不读]  ← R9 漏洞所在
③ 后门 候选识别(P1/P2/P3) ── 仅对通过 ①② 的文件读 front-matter，逐件判桶与标签
```

⇒ **冲突消解仍为 P1 > P2 > P3，但 P1/P2 的可达性受 ② 约束**（不可达 ≠ 不存在）。

### 2.4 完全错名组合：**读取边界 ＋ 预期 ＋ 处置选项**

**组合定义**：`knowledge/ops/notes.md`（**无 `OPS-` 前缀**）内含 `id: OPS-2026-0007`（**合法 ID**）、六字段齐备。

| 选项 | 族归属（前门②） | **读取边界** | 该件预期 | 代价/风险 | 我方建议 |
|---|---|---|---|---|---|
| **甲（维持现状＋规约）** | 仍按 `entry_glob` ⇒ 族外 ⇒ skip | **不读其内容**（现状读取边界：仅读 ①② 通过的文件，且只读 front-matter） | `skip(non-entry-file)` ⇒ **不解析、不可见**（族目录内不符 `entry_glob` ⇒ 按既有读取边界归 `non-entry-file`／`level=P3`；其 `id: null` 表示**未被读取、未知**，不表示已证明无 id）；**该合法 ID 件在本门责任域之外**（**已知盲区**） | 零改动、零成本；**风险＝合法条目因命名不规整而完全失联** | **建议采纳**，并**以命名规约明文化**（族 README／运维说明写明"条目文件必须命名为 `<族前缀>-<编号>.md`"） |
| **乙（放宽前门②）** | 改为**按族 `dir`** 路由，族目录内全部 `.md` 均进流程 | **读取面扩大**：从"族前缀文件"扩大到"族目录内全部 `.md`"（含 `README.md`／`NOTES.md`／临时件）；仍只读 front-matter、**不写文件** | `notes.md` ⇒ 读 front-matter ⇒ `id` 合法 ⇒ **P1 可见**（桶按契约、命名标签在） | 每族多解析若干文件；**族目录内非条目文件也会进统计**（P3 仍 skip，但产生 `skip_detail`）；**属路由语义变更**，需另审 | **暂不采纳**（另议） |

> **本版不新增 `id_prefix` 配置契约**（按 R9 指示）：甲方案下"族前缀"已由现有 `entry_glob` 隐含表达，**无需新键**。

### 2.5 **P1 覆盖边界（写死 · R10 要求保留）**

> **P1（合法 ID 条目）只对「文件名匹配族 `entry_glob`」的文件生效。**
>
> | 事实 | 说明 |
> |---|---|
> | **成立的** | 「**通过 `entry_glob` 的合法 ID 件，不得被 skip 隐藏**」（可见性保证） |
> | **不成立的** | 「**合法 ID ⇒ 必被发现**」——`knowledge/ops/notes.md`（无 `OPS-` 前缀、front-matter 含合法 `id: OPS-2026-0007`）**仍不可见**（前门② `skip(non-entry-file)`——族目录内不符 `entry_glob`；内容不读，`id` 未知） |
> | 兜底 | **命名规约**：条目文件必须命名为 `<族前缀>-<编号>.md`（写入族 README／运维说明）；**该盲区不由本门解决** |
> | 若要覆盖族外合法 ID | 走**乙方案**（前门②改按族 `dir` 路由）——**属路由语义变更，另审**（本方案不实施） |

## 3 方案（三面说明）

### 3.1 正交模型：**互斥计数桶 ＋ 可叠加诊断标签**

| 类别 | 取值 | 语义 | 互斥性 | 计数 |
|---|---|---|---|---|
| **互斥计数桶** | `ok` / `empty` / `fail` | `ok`＝解析出非空记录**且契约校验无错误**；`empty`＝解析空/残 **或** 契约字段为空/非法；`fail`＝解析**抛异常** | 每件恰一 | 构成 `checked` |
| **独立集合** | `skipped` | 未进入解析（前门①②未过、或 P3 非条目文档） | 与 `checked` 互斥 | 构成 `total` 另一部分 |
| **可叠加诊断标签** | `name_not_canonical` / `name_id_conflict` / `id_missing` / `parse_empty` / `field_empty` / `dup_id` | 附加诊断事实 | 可与任一桶共存 | **否** |

**桶由契约决定，不由 id 决定**：合法 `id` **只意味着进入条目校验**（P1），**不意味着桶归 `ok`**；任一契约字段为空/非法（含 `id` 缺失/非法）⇒ 桶 = `empty`（并按既有契约进 `bad`）。**文件名问题只附标签**，不改桶。

**可达性注记**：P1/P2/P3 标签**只对通过前门①②的文件**产出；族外文件（完全错名）**连标签都没有**（§2.4 甲）。

### 3.2 三条守恒式

```
(1) total   = checked + len(skipped)
(2) checked = ok + empty + fail
(3) total   = expanded + glob_misses
```
- **(1) 已被现有测试锁定**（`tests/test_kb_hook_skipped.py` L81/L92/L131）⇒ **不得破坏**。
- **标签不进入任何等式**。

### 3.3 payload（机读面）

新增三键（**既有键的值与语义一律不变**；标签在 `parse_detail[].tags`，不新增计数字段）：

```json
{
  "total": 6, "checked": 5, "bad": [], "skipped": [], "families_used": ["ops"], "note": null,
  "parse_stats": { "ok": 3, "empty": 2, "fail": 0 },
  "parse_detail": [
    {"file": "knowledge/ops/OPS-2026-9009 副本.md", "status": "ok",
     "tags": ["name_not_canonical"], "id": "OPS-2026-9009", "level": "P1"},
    {"file": "knowledge/ops/OPS-2026-9005.md", "status": "empty",
     "tags": ["id_missing"], "id": null, "level": "P2"}
  ],
  "skip_detail": [
    {"file": "knowledge/ops/README.md", "reason": "non-entry-file", "id": null, "level": "P3"},
    {"file": "knowledge/ops/notes.md", "reason": "non-entry-file", "id": null, "level": "P3"},
    {"file": "docs/notes.md", "reason": "no_family_match", "id": null, "level": "OUT-OF-FAMILY"}
  ]
}
```

- `level`：P1／P2／P3／**OUT-OF-FAMILY**（族外，含完全错名的合法 ID 件 ⇒ **显式记为族外，而非"不存在"**）。
- **`non-entry-file` ⇒ `P3`；`no_family_match` ⇒ `OUT-OF-FAMILY`**（按**既有读取边界**：族目录内不符
  `entry_glob` 的件归 `non-entry-file`，与族目录无关的件归 `no_family_match`）。
- **`id: null` 表示「未知」而非「已证明无 id」**：skip 件的内容**从未被读取**（前门①②不通过即不读），
  其 `id` 只能记 `null`。
- **未启用族**（`off`）：`parse_stats`/`parse_detail` 对所有族一视同仁；占位诊断仍无。

#### 3.3.1 消费者逐件兼容锚（14 处 · 沿用不变）

`prepush-gate.ps1:304/307/309/310/315`｜`kb-schema.yml:64`｜`check_deploy_closedloop.py:165/170/208`｜`kb_draft.py:191/213`｜`install_kb_hook.ps1:68/35`｜`kb-kit/install.ps1:70/72/94/118`｜`kb-kit/scripts/install_kb_hook.ps1:68`｜`kb-kit/scripts/kb_draft.py:191/213`｜`kb-kit/scripts/kb_hook.py:135/142/146/165-169`｜`kb-kit/templates/kb-schema.yml:64`｜`kb-kit/templates/kb-info.yaml:7`｜`check-staged-scope.py:12-13/176-183`（仅路径样例）｜`test_kb_hook_skipped.py:80/81/92/131/83/101/110/118-120`｜`test_kb_placeholder_gate.py:149-150/243-287`
⇒ **新增键均不破坏**；唯一硬约束＝**#13 的守恒等式必须保持**。

#### 3.3.2 kb-kit 分发范围（另列，沿用 v4）

跨仓分发件（`install.ps1:3`「任意 git 项目」；安装动作 `:94-95/:99/:109/:118/:123`；示例仓 `:32` `vibe-coding-cn`；**唯一同步通道 `-Sync -CanonicalRepo`** `:10`）；主仓 vs 副本：`kb_hook.py` **400 行** `ce38ed73…` vs **176 行** `dcc4df5f…`（**差 11096 B**，副本**无占位门能力**）、`kb_draft.py` **逐位相同**（`399abf2a…`）；**本轮不同步**（边界）；后果＝经 kb-kit 安装的仓其 `kb_hook` 永远无占位门能力。

## 4 影响面（获批并实施后）

| 项 | 内容 |
|---|---|
| 代码 | `scripts/kb_hook.py`：**候选识别（后门）＋桶＋标签**约 **+45 行**；`main` 文本输出改逐件；JSON 增三键；**前门①②不改**（甲方案） |
| 契约影响 | JSON 新增三键（既有键不变，向后兼容）；文本变长；**守恒 (1)(2)(3) 必须保持** |
| **不改** | **前门①②路由语义**（族归属仍按 `entry_glob`）、族档位、条目内容、既有判据与退出语义、`id_seen` 查重逻辑、**配置契约（不新增 `id_prefix`）**、**kb-kit 副本**、**ops 观察窗长度** |

## 5 验收判据（实施后）

1. **P1 合法 ID**（带族前缀）⇒ 可见：桶按契约、命名标签在、`skip_detail` 中**不出现**；
2. **P2 缺 ID**（族前缀文件名）⇒ 桶=`empty`＋`id_missing`，**不 skip**；
3. **P2 损坏 front-matter** ⇒ 桶=`empty`/`fail`＋`parse_empty`，**不 skip**；
4. **P3 非条目文档**（族目录内、族前缀不匹配、无 id）⇒ `skip_detail` 记 `non-entry-file`，**不进 `checked`、不占桶**；
5. **完全错名** ⇒ 按既有读取边界分两类：**族目录内**的错名件记 `non-entry-file` ＋ `level=P3`；**与族目录无关**的件记 `no_family_match` ＋ `level=OUT-OF-FAMILY`（**显式记为族外，不静默**）；两类**内容均不被读取**（读取边界不变），故其 `id` 一律记 `null`（**未知，非已证明无 id**）；
6. **合法 ID ≠ 桶 ok**：合法 ID 但契约字段空 ⇒ 桶=`empty`；
7. **桶互斥**、**标签可叠加**（不影响任何计数）；
8. **守恒**：`ok+empty+fail==checked`；`total==checked+len(skipped)`（**现有测试 L81/L92/L131 须仍绿**）；
9. **逐件性**：每个输入文件恰一行；**无"其他"归类**；
10. **退出码**：见 §11 真值表（新增诊断不改变 verdict）；
11. 未启用族**既有键**逐位不变。

## 6 待裁

| # | 事项 | 我方建议 | 状态 |
|---|---|---|---|
| **C4-a** | `empty` 桶是否**新增**计入 `bad` | **不新增**（**契约字段为空/非法仍按既有契约进 `bad`**，那不是本件新增） | **待裁** |
| **C4-b** | 新增诊断是否阻断 | **不阻断**（见 §11） | **待裁** |
| **C4-c** | 候选识别规则 | §2.3 三段识别链 + P1>P2>P3（P1/P2 **不得 skip**，但**可达性受前门②约束**） | **待裁（口径已写明）** |
| **C4-d** | 命名标签处置 | 诊断级**不阻断**、逐件可见；是否进 `bad` 请裁 | **待裁** |
| **C4-e** | kb-kit 副本同步 | **另立议题**，本方案**不同步** | **待裁/另议** |
| **C4-f（新）** | **完全错名（族外合法 ID）处置** | **甲（维持现状＋命名规约明文化）**；族外件在 `skip_detail` 记 `OUT-OF-FAMILY`；乙（放宽前门②按族 dir 路由）**另议** | **待裁** |

## 7 边界

**仅改文档与静态清点**：不改 `kb_hook.py`、不改配置契约、不升档、不接线、不分发、**不延观察窗**、**未跑新沙箱**；不改试点档位／每日扫描义务／人工回 `off` 条件；**未批准前不实施**。

## 8 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-15 03:40 | v1：取证＋四类覆盖＋三面说明＋C4-a/b/c。 |
| 2026-11-15 14:30 | v2（R5）：E 类＋N4 实测＋逐件报告＋消费者核对＋C4-d/e。 |
| 2026-11-15 16:40 | v3（R6）：桶/标签正交＋三守恒式＋E 类硬判据＋14 消费者锚＋kb-kit 差异单列。 |
| 2026-11-15 17:50 | v4（R7）：桶由契约决定＋组合表与差异＋kb-kit 分发范围；修正 v3 两处口径错误。 |
| 2026-11-15 22:30 | v5（R8）：统一候选识别规则 P1/P2/P3＋交叉表＋优先级表与 diff。 |
| **2026-11-15 22:50** | **v6（R9 · 完整技术审未过）**：①**§2.1 静态清点**（`_match_family` L61-70 **仅按 `entry_glob`**、**不读内容**；`check_files` L319-321 族外 ⇒ `skip(no_family_match)`）⇒ **P1 可达性前提＝文件名匹配族 `entry_glob`**；②**§2.2 P1 可达性前提**＋**完全错名（`notes.md` 含合法 ID）不可达**显式化；③**§2.3 三段识别链**（前门①输入展开／前门②族归属／后门候选识别）；④**§2.4 完全错名组合的读取边界＋预期＋甲/乙处置**（**本版不新增 `id_prefix` 配置契约**）；⑤**§11 退出码真值表**（新增诊断 vs 原契约错误）；⑥`skip_detail` 增 `level=OUT-OF-FAMILY`；⑦新增 **C4-f**（完全错名处置）；⑧验收增至 11 条。**本版仅改文档与静态清点，未跑新沙箱。状态：待独立②审。** |

## 9 交叉表（三类 ＋ 合法ID+空字段 ＋ **完全错名** × 五段链）

> 读数＝**现状实测**（2026-11-15 沙箱，前轮）；目标＝C4 实施后。**完全错名一行为本轮静态清点推得（未跑沙箱）**。

| 件类 | 具体构造 | 识别 | 解析 | 桶（目标） | 标签（目标） | 退出码（现状→目标） |
|---|---|---|---|---|---|---|
| **P1 合法 ID** | `OPS-2026-9001.md`（齐备） | P1（id 匹配） | 非空记录 | `ok` | — | 现状 0 → 目标 0 |
| **P1 合法 ID ＋ 错文件名（带前缀）** | `OPS-2026-9002 副本.md`（齐备） | P1 | 非空记录 | `ok` | `name_not_canonical` | 现状 **0（完全无标记）** → 目标 0（但逐件可见） |
| **P1 合法 ID ＋ 空字段** | `OPS-2026-9003 副本.md`（trigger 空） | P1 | 非空记录 | **`empty`**（字段空，**不因 id 合法而 ok**） | `name_not_canonical`＋`field_empty` | 现状 `--strict` 1 → 目标 1（不变） |
| **P1 合法 ID ＋ 空字段（文件名规范）** | `OPS-2026-9004.md`（trigger 空） | P1 | 非空记录 | **`empty`** | `field_empty` | 现状 1 → 目标 1（不变） |
| **P2 缺 ID** | `OPS-2026-9005.md`（字段齐备、无 id） | **P2**（族前缀特征） | 非空记录（keys=12） | **`empty`**（`id` 缺失 ⇒ 契约不合规） | `id_missing` | 现状 1（`id 非法…：None`）→ 目标 1（不变） |
| **P2 损坏 front-matter** | `OPS-2026-9006.md`（`id: [broken`） | **P2** | 空/残记录（keys=2，**未抛异常**） | **`empty`**（**非 `fail`**） | `parse_empty` | 现状 1 → 目标 1（不变） |
| **P2 损坏＋抛异常** | 极端坏 YAML | **P2** | **抛异常** | `fail` | `parse_empty` | 现状 1（`parse_fail`）→ 目标 1（不变） |
| **P3 非条目文档** | `README.md`（无族前缀、无 id） | **P3** | **不解析** | 不适用 | 不适用 | 现状 skip（非阻断，0）→ 目标 0（`skip_detail` 留痕） |
| **完全错名（族外）** | **`knowledge/ops/notes.md`（无 `OPS-` 前缀，含合法 `id: OPS-2026-0007`）** | **前门② 未过 ⇒ 族外** | **不解析**（内容不读） | 不适用 | `level=OUT-OF-FAMILY` | 现状 **skip(no_family_match)，0** → 目标 **0（显式记族外）**；**该合法 ID 件不可达（已知盲区，甲方案）** |

**三守恒式核验（上表 6 件可解析组合 + 1 件族外）**：

| 式 | 目标值 | 现状 |
|---|---|---|
| (1) `total == checked + len(skipped)` | `7 == 6 + 1` ✔ | **已成立**（现有测试锁定） |
| (2) `checked == ok + empty + fail` | `6 == 2 + 4 + 0` ✔（ok=P1 齐备×2；empty=P1 空字段×1＋P2×2；fail=0） | 不可计算（现状无桶） |
| (3) `total == expanded + glob_misses` | `7 == 7 + 0` ✔ | 不可计算（现状无展开分项） |

## 10 优先级表（**含前门层**）

| 优先级 | 层/级 | 判据 | 必做动作 | 禁止 |
|---|---|---|---|---|
| **P0** | **前门② 族归属** | 文件名匹配族 `entry_glob` | 归族 | **不读族外文件内容**（甲方案读取边界） |
| **P1** | **P0 后 候选 P1 合法 ID 条目** | front-matter `id` 存在且匹配 `id_pattern` | 进 `checked`、进条目校验、参与查重、桶按契约、命名问题仅标签 | **禁止 skip**（不得因错名隐藏） |
| **P2** | **候选 P2 族特征候选** | basename 以族前缀开头但 P1 不成立 | 进 `checked`、产出**可见错误**（桶 `empty`/`fail`＋标签） | **禁止 skip** |
| **P3** | **候选 P3 非条目文档** | 无有效 id、basename 无族前缀 | `skip_detail` 记 `non-entry-file` | 不占桶计数 |
| **P4（末位）** | **族外文件** | 未匹配任何族 `entry_glob` | `skip_detail` 记 `no_family_match`＋`level=OUT-OF-FAMILY` | 不读内容、不占桶；**不得当作"不存在"** |

⇒ **可见优先于跳过**：P1/P2 > P3；**族外（P4）** 与 P3 的区别是"**为何进不来**"——P3 有族但非条目，P4 连族都没有。

## 11 退出码与回归判据（**分单件/整次两口径 · R10 修正**）

### 11.1 单件口径

| 来源 | 是否进既有 `bad` | 该件 verdict |
|---|---|---|
| **新增诊断**：桶（`ok`/`empty`/`fail`）、标签（`name_not_canonical`/`id_missing`/`parse_empty`/…）、`parse_stats`、`parse_detail`、`skip_detail`、`level` | **否** | **不改变**该件的 verdict |
| **原契约错误**：六必填为空、字段非法、`fix_layer` 越枚举、`domain` 越词表、`recurrence` 非法、`feedback` 全空、`status` 非法、`id` 非法/缺失、`parse_fail` | **是**（**现状行为，不变**） | 该件 verdict **与现状相同**（`--strict` 下仍贡献 `rc=1`） |

### 11.2 整次运行口径（**纠正 v6 的"恒 0"歧义**）

> **整次运行的退出码由"本次输入中是否存在既有 `bad`"决定**——**不是恒 0**：
>
> | 本次输入 | 现状 `--strict` | 目标 `--strict` |
> |---|---|---|
> | **全部干净**（既有 `bad` 为空） | **0** | **0**（不变） |
> | **含既有错误**（如 `parse_fail`／字段非法／`id` 非法） | **1** | **1**（不变） |
>
> ⇒ **禁止把"含旧错误的整次运行"概称恒 0**。C4 的承诺仅是：**新增诊断不改变整次运行的 rc**（该 1 仍 1、该 0 仍 0）。

### 11.3 回归判据（**写死 · 唯一口径**）

> **同输入、同配置下，既有 per-file verdict 与整次退出码逐项不变。**
>
> - **同输入**＝同一批 `--files`（含 glob 展开后的集合，逐个文件可比）
> - **同配置**＝同一 `kb-info.yaml`（含各族 `placeholder_gate` 与 `since`）
> - **逐项**＝①每件的既有 `bad` 错误集合**文本不变**（不新增、不删改、不改序语义）；②整次 `--strict` 退出码不变
> - **允许的变化（仅此）**：新增 `parse_stats`／`parse_detail`／`skip_detail`／`level`／`tags` 等**只增信息字段**；**既有键的值与既有 `bad` 内容一律不得改变**
> - **禁止的表述**：「实施后恒 rc=0」「empty 一律不阻断整次运行」——均为**对含旧错误运行**的错误概称

## 12 diff（相对现状，实施后新增）

| 维度 | 现状 | 目标（v6） |
|---|---|---|
| 前门① 输入展开 | glob 展开 + `glob_no_match` | **不变** |
| **前门② 族归属** | 仅 `entry_glob` 匹配 | **不变**（甲方案；不读族外内容） |
| 候选识别 | **无显式阶段** | **新增后门 P1/P2/P3**（+ 族外显式记 `OUT-OF-FAMILY`） |
| P1 可见性（带前缀错名） | **静默** | **逐件可见**（桶＋标签） |
| P2 可见性 | 可见但粒度粗 | 桶=`empty`＋分类标签 |
| 族外合法 ID 件 | skip(no_family_match) | **不变**，但 `skip_detail` 显式记 `level=OUT-OF-FAMILY`（**不当作不存在**） |
| 桶 / 标签 | **不存在** | 新增 `parse_stats` / `parse_detail[].tags` |
| 既有键 | — | **全部不变** |
| 守恒 (1) | 已成立且被测试锁定 | **必须保持** |
| **退出码** | — | **零变化**（§11：只增诊断，不增失败） |
| 配置契约 | — | **不变**（**不新增 `id_prefix`**） |
| 族档位／条目内容／`id_seen`／kb-kit 副本／观察窗 | — | **全部不变** |


## 13 同输入回归表（**分层：已测基线 / 未测目标**）

> **⚠️ 分层声明（R11 要求，写死）**
> - **「现状」列 = 已测基线**：产出于 C4 **实施之前**的历史实测（来源见各集标注），**只证明改前读数**；
> - **「目标」列 = 预期（未实现）**：C4 **尚未实施**，after 读数**不存在**；本列仅为**设计预期**，**不得当作实测**；
> - 历史 **93 件**与 **6 组合**的读数**只证明改前读数**，**不能把尚未实现的 after 写成实测**；
> - **after 何时可称实测**：实施后按 §13.3 双跑取得 `after.json` 且逐件比对 PASS ⇒ **另出实测表**并更新本节。


### 13.1 输入集 A：真实库四族全量（93 件）　`现状=已测基线` ／ `目标=预期`

- **输入**：`knowledge/qmt/QD-*.md`（22）＋`knowledge/ptrade-deploy/PD-*.md`（22）＋`knowledge/db/DB-*.md`（44）＋`knowledge/ops/OPS-*.md`（5）
- **配置**：qd/pd/db `placeholder_gate="off"`、ops `"warn"`（`since=2026-11-13T20:56:02+08:00`）
- **读数来源**：2026-11-15 15:41／22:02 两轮实测（同一输入同配置）

| 项 | 现状（**已测基线**） | 目标（**预期 · 未实现**） | 判据 |
|---|---|---|---|
| `total` | 93 | 93 | 不变 |
| `checked` | 93 | 93 | 不变 |
| `skipped` | 0 | 0 | 不变 |
| `bad` | **0** | **0** | 不变（既有键值） |
| **整次 `--strict` rc** | **0** | **0** | **不变** |
| 新增键 | 无 | `parse_stats`／`parse_detail`／`skip_detail` | **只增** |
| 每件桶/标签 | 无 | 93 件逐件给桶（预期全 `ok`） | **只增** |

### 13.2 输入集 B：组合沙箱 6 件（含**既有错误**）　`现状=已测基线` ／ `目标=预期`

- **输入**：C1–C6（见 §9）
- **配置**：ops 族 `placeholder_gate="warn"`
- **读数来源**：2026-11-15 17:50 实测（同输入同配置）

| 件 | 现状 `bad`（**已测基线**） | 现状 rc（**已测基线**） | 目标桶 / 标签（**预期**） | 目标 `bad`（**预期**） | 目标 rc（**预期**） | 差异 |
|---|---|---|---|---|---|---|
| C1 合法ID+合法名+齐备 | — | 0 | `ok` | — | 0 | 仅增 `status` |
| **C2 合法ID+错名+齐备** | **—（静默）** | **0** | `ok` / `name_not_canonical` | **—** | **0** | **仅增标签**（现状静默→可见，**verdict 不变**） |
| C3 合法ID+错名+空字段 | `trigger 为空` | 1 | `empty` / `name_not_canonical`+`field_empty` | `trigger 为空` | **1** | 仅增桶/标签 |
| C4 合法ID+合法名+空字段 | `trigger 为空` | 1 | `empty` / `field_empty` | `trigger 为空` | **1** | 仅增桶/标签 |
| C5 无ID+齐备 | `id 非法…None` | 1 | `empty` / `id_missing` | `id 非法…None` | **1** | 仅增桶/标签 |
| C6 损坏 front-matter | `id 非法…；六字段空` | 1 | `empty` | 同左 | **1** | 仅增桶 |
| **整次运行** | `bad=4` ⇒ **rc=1**（**已测基线**） | — | — | `bad=4` ⇒ **rc=1**（**预期**） | — | **整次 rc 预期不变（=1，非 0）** |

> **本表正是 R10 要求的反例**：输入集 B **含既有错误** ⇒ 整次 `--strict` **已测基线 1、预期 1**——**不是恒 0**（**该 1 为预期值，尚未实测**）。
> C4 只把"原本静默的 C2"变得**可见**，**不改变任何件的 verdict、也不改变整次 rc**。

### 13.3 回归执行方式（获批实施后）

1. 以**同一批 `--files` ＋ 同一 `kb-info.yaml`** 在实施前跑一次，落 `baseline.json`（含每件 `bad` 文本 + 整次 rc）；
2. 实施后再跑一次，落 `after.json`；
3. 逐件比对：`bad` 文本**逐一相同**、整次 rc **相同** ⇒ PASS；否则 FAIL 并回退（`git revert`）；
4. 允许的差异**仅**：新增字段（`parse_stats`/`parse_detail`/`skip_detail`/`level`/`tags`）。

## 14 最终候选汇总（**下一读数交付物**）

| 级 | 判据 | **可达性** | 动作 | 禁止 |
|---|---|---|---|---|
| **P1 合法 ID 条目** | front-matter `id` 存在且匹配 `id_pattern` | **仅当文件名匹配族 `entry_glob`**（§2.5 边界） | 进 `checked`、进条目校验、参与查重、桶按契约、命名问题仅标签 | **禁止 skip 隐藏**（限于可达者） |
| **P2 族特征候选** | basename 以族前缀开头但 P1 不成立 | 同 P1（须先过 `entry_glob`） | 进 `checked`、**可见错误**（桶 `empty`/`fail`＋标签） | **禁止 skip** |
| **P3 非条目文档** | 有族（过 `entry_glob`）但无有效 id、basename 无族前缀 | 同左 | `skip_detail` 记 `non-entry-file` | 不占桶 |
| **P4 族外文件** | 未匹配任何族 `entry_glob` | **不读内容** | `skip_detail` 记 `no_family_match`＋`level=OUT-OF-FAMILY` | 不读、不占桶；**不得当作不存在** |
| **边界（写死）** | **含合法 ID 的 `notes.md`（无族前缀）属 P4，仍不可见** | — | 由**命名规约**兜底 | 不得宣称"合法 ID 必被发现" |

## 15 变更记录（补）

| 日期 | 内容 |
|---|---|
| **2026-11-15 23:10** | **v7（R10）**：①新增 **§2.5 P1 覆盖边界（写死）**——P1 **只覆盖通过现有 `entry_glob` 的文件**，含合法 ID 的 `knowledge/ops/notes.md` **仍可能不可见**；成立的是"可达者不得被 skip 隐藏"，**不成立"合法 ID 必被发现"**；②**§11 重写为单件/整次两口径**，纠正 v6 的"恒 0"歧义——**整次 rc 由是否含既有 `bad` 决定**（干净 0／含旧错误 1），**禁止把含旧错误的整次运行概称恒 0**；③**§11.3 回归判据写死**＝"**同输入、同配置下既有 per-file verdict 与整次退出码逐项不变**"（允许仅新增字段）；④新增 **§13 同输入回归表**（输入集 A 真实库 93 件／输入集 B 组合 6 件，**基于既有实测、本轮未跑新测试**）＋**执行方式**（baseline/after 双跑逐件比对）；⑤新增 **§14 最终候选汇总**（P1/P2/P3/P4＋边界）。**仅文档，未改路由/配置契约/kb_hook、未接线分发、未跑新测试、未延观察窗。** |

## 16 实施申请与回退纪律（**R11：按原流程；回退须针对本会话明确路径与版本**）

### 16.1 实施申请须附（缺一不予受理）

| 项 | 要求 |
|---|---|
| **允许写入清单** | 逐文件精确路径（如 `scripts/kb_hook.py`、`tests/test_kb_parse_status.py`），**禁通配**；清单外改动一律违约 |
| **范围外声明** | 明写"不动"：族档位／`knowledge/kb-info.yaml`／路由 `_match_family`／`entry_glob`／kb-kit 副本／观察窗 |
| **回归命令（原样可复制）** | ①实施前：`python scripts/kb_hook.py --repo-root . --files <同一清单> --json > baseline.json`；②实施后同命令 `> after.json`；③逐件比对（`bad` 文本逐一相同 ＋ 整次 rc 相同 ＋ 只允许多出 `parse_stats`/`parse_detail`/`skip_detail`/`level`/`tags`）；④`python -m pytest tests/test_kb_hook_skipped.py tests/test_kb_placeholder_gate.py -q` |
| **回退方式** | 见 16.2 |
| **证据落点** | `docs/evidence/` 单文件：命令 ＋ 关键输出 ＋ 两 JSON 的 sha256 |

### 16.2 **回退纪律（写死 · 字节保真 · 内容身份比对）**

#### A. 前置：只针对本会话路径判脏

| 情形 | 处置 |
|---|---|
| **目标路径**（允许写入清单内的文件）存在**非本会话** `M`/`??` | **同文件混属 ⇒ 停手先协调**，禁止自行摘出、禁止强回退 |
| **其他无关路径** dirty | **不阻断**本会话的**独立只读工作**（只读侦察/文档核验照常进行）；仅当需触及其路径时才协调 |
| 目标路径干净 | 正常进入回退 |

> （← 修正 v8 表述：v8 写"含非本会话 `M`/`??` ⇒ 判脏树停手"过宽，会把无关 dirty 也当阻断。）

#### B. **N6-V1：写前必须保存「实际原字节 ＋ SHA」**（`HEAD:path` 不足以作回退源）

> **为什么 `HEAD:path` 不够**：真实写前内容可能是 ① **dirty**（工作树有未提交改动）② **untracked**（HEAD 里根本没有该路径，`rev-parse HEAD:<path>` 直接报错）③ **部分暂存**（index 与工作树不同）。
> 三种情形下 `HEAD:path` 都**不等于**真实写前内容 ⇒ **回退源只能是「实际原字节快照」**。

**写前动作（实施开工那一刻，与 `baseline.json` 同批执行）**

| 步骤 | 命令（字节保真） | 产出 |
|---|---|---|
| ① **原字节快照**（**唯一权威回退源**） | `python -c "import shutil,hashlib,os;src=r'<path>';dst=r'<SNAP>';shutil.copyfile(src,dst);print(hashlib.sha256(open(dst,'rb').read()).hexdigest(),os.path.getsize(dst))"`<br>或 pwsh `Copy-Item <path> <SNAP> -Force` 再 `Get-FileHash -Algorithm SHA256` | `<SNAP>` 文件 ＋ **`SNAP_SHA256`** ＋ `SNAP_SIZE` |
| ② 快照完整性自检 | 复算 `sha256(<SNAP>)` **必须 ==** 记录值 | 不等 ⇒ 快照无效，**不得开工** |
| ③ 状态登记（**仅辅助，不作回退源**） | `git status --porcelain -- <path>`（记录 dirty/untracked 情形）<br>`git rev-parse HEAD:<path>`（**失败即说明 untracked/新增**，如实记录"HEAD 无此路径"）<br>`git diff -- <path>` / `git diff --cached -- <path>`（差异文本快照） | `.meta.json`：`path / snap / sha256 / size / mtime / dirty_state / index_state / head_blob_or_absent` |

**快照落点与命名**：`docs/evidence/rollback-snapshots/<rel 路径中的 / 换 __>.pre-<UTC yyyymmddThhmmss>.bin` ＋ 同名 `.meta.json`。
**留存**：保留至该轮验收通过后**不少于 7 天**；期间禁止删除或覆盖。

#### C. **N6-V2：恢复用「定向原字节写回」，不用 `git checkout`**（保 index 原态）

> **为什么不用 `git checkout <pin> -- <path>`**：
> ① 它**会写 index**（等价于把该路径内容 stage 进去）⇒ **破坏 index 原态**，在多线共享工作树里可能把不该暂存的内容带进别人的提交；
> ② 它走 **working-tree 转换链**（`core.autocrlf` / `.gitattributes` 的 `text`/`filter`/`ident` 等）⇒ 落盘字节**可能不等于 blob 字节**，回退后再算 sha256 与 blob 不一致。

**恢复动作（从快照写回）**

| 步骤 | 命令 | 说明 |
|---|---|---|
| ① 快照自检 | `sha256(<SNAP>) == SNAP_SHA256` | 不等 ⇒ 快照被破坏，**停止恢复**并上呈 |
| ② **定向写回**（主径） | `python -c "import hashlib;d=open(r'<SNAP>','rb').read();assert hashlib.sha256(d).hexdigest()==r'<SNAP_SHA256>';open(r'<path>','wb').write(d)"` | **`wb` 字节写**，不经文本层、**不碰 index** |
| ② 定向写回（备径 pwsh） | `Copy-Item <SNAP> <path> -Force` 后 `Get-FileHash` 比对 | `Copy-Item` 是**字节复制**，不碰 index |
| ③ 若必须从 git 取内容 | `python -c "import subprocess,sys;r=subprocess.run(['git','cat-file','blob','<blob>'],capture_output=True);sys.exit(1) if r.returncode!=0 else open(r'<path>','wb').write(r.stdout)"` | **必须核 `returncode==0`** 再写盘；`cat-file` **不碰 index**、**不过 filter** |
| **禁用** | `git checkout <pin> -- <path>`／`git restore --source` | 改 index ＋ 过转换链 |
| **禁用** | PS 5.1 `>` ／ `Out-File` ／ `Set-Content -Encoding UTF8` | 重编码（UTF-16LE+BOM / 写 BOM） |

#### D. 验证：**按「真实写前内容」判定，不以相对 HEAD 的 status 为据**

> **充分判据（唯一）**：落盘内容的 **sha256 == `SNAP_SHA256`**（逐字节等同于写前原字节）。
> **`git status --porcelain -- <path>` 非空 ≠ 恢复失败**：恢复旧内容后，若 HEAD 已含新内容，相对 HEAD 自然显示为"已修改"——**这是预期现象**，不构成失败理由。

| # | 检查 | 判据 | 权重 |
|---|---|---|---|
| ① | `sha256(<path>) == SNAP_SHA256` | 必须相等 | **充分判据** |
| ② | `size(<path>) == SNAP_SIZE` 且与快照逐字节比对为空（`fc /b` 或 `cmp`） | 必须一致 | **加强判据** |
| ③ | **index 原态**：`git diff --cached --name-only -- <path>` 与写前 `.meta.json` 的 `index_state` **一致** | 必须一致 | **加强判据**（证明未误 stage） |
| ④ | `git status --porcelain -- <path>` | **仅记录，不作判据** | 参考（非空允许） |

- ① 成立即**恢复成功**，结论表述写「已恢复到写前原字节 `SNAP_SHA256`」；
- ① 不成立 ⇒ **恢复失败**，继续排查（快照损坏／写入被并发覆盖／路径错），**不得宣告已回退**；
- ③ 不一致 ⇒ **index 被改动**（可能误用 checkout 或误 add）⇒ 按共享工作区纪律**先上呈**，不得自行摘出。

#### E. 留证

复原前后 `sha256` 与 `hash-object` 对照、所用 pin 与本轮 pin 记录一并写入 `docs/evidence/` 单文件，并在上报块申报。

## 17 变更记录（补）

| 日期 | 内容 |
|---|---|
| **2026-11-16 02:20** | **v9（R12：回退配方定点修正）**：①**§0 身份口径统一为「全文 hash」**（R12："以当前全文 hash 为身份"）——文档**不自含自身 hash**（自指），改**提交登记制**：身份＝`commit SHA ＋ 该 commit 实测全文 sha256`，附 v8/v7 登记行与复核命令 `git show <commit>:<path> | sha256sum`；②**§16.2 重写**：(A) **判脏收窄**为"仅目标路径同文件混属才停手"，**无关 dirty 不阻断独立只读工作**；(B) **写前必须留 pin**（`PIN_BLOB`＋`PIN_SHA`，未记录不得开工）；(C) 回退**主径 `git checkout <PIN> -- <path>`**（git 原生字节保真）、备径 python bytes 写盘，**明禁 PS5.1 `>`／`Out-File`／`Set-Content -Encoding UTF8`（重编码风险）**、禁共享树整笔 revert；(D) **验证＝对写前 pin 比对三绿**（`hash-object==PIN_BLOB` ∧ `sha256==PIN_SHA` ∧ `status --porcelain` 为空），**明写"不得把相对 HEAD 的 diff 为零当作恢复成功"**；(E) 留证要求。**仅文档；未改 kb_hook／路由／配置、未跑新测试、未分发、未延观察窗。** |
| **2026-11-16 01:35** | **v8（R11：收文档退修 · 冻结候选身份）**：①新增 **§0 候选身份**——**冻结对象＝v7 正文**（commit `{head}`，sha256 `{c4_sha}`、blob `{c4_blob}`），配套件 hash；**冻结语义**（未授权实现、任何改动即新版本须重新冻结报审）；**状态口径写死**（已测基线＝实施前实测／预期＝未实现不得当实测／after 何时可称实测）；②**§13 分层改造**——标题「分层：已测基线／未测目标」＋**分层声明**（历史 93 件与 6 组合**只证明改前读数**，**after 不存在、不得写成实测**）＋表头列名改「现状（已测基线）／目标（预期 · 未实现）」＋13.1/13.2 标题与反例行同步标注；③新增 **§16 实施申请与回退纪律**——申请须附允许写入清单（禁通配）／范围外声明／**原样可复制回归命令**／证据落点；**回退写死**（禁在共享 dirty 树机械 revert 整笔；`git show <pin>:<path>` **针对本会话明确路径与版本**逐文件复原＋反向验证＋sha256 留证＋串入他线先上呈）。**仅文档；未改 kb_hook／路由／配置、未接线分发、未跑新测试、未延观察窗。** |

| **2026-11-16 03:00** | **v10（R13 · DRIFT-N6-r1：V1／V2 精确配方）**：①**§0 回填 v9 身份**（`61f2c39` / sha256 `18bd1ec3fa90a01419d37513cf290c62c39ddfa9845516133695dbc066acc115` / blob `2d03a35bcd2b` / 实测 **35912 B**，与审核主锚 35,912 B 一致）＋ v10 待登记行；②**§16.2-B 改写为 N6-V1**——`HEAD:path` **不足以作回退源**（dirty／untracked／部分暂存三情形下均 ≠ 真实写前内容）⇒ 写前**必须保存实际原字节快照 + `SNAP_SHA256` + `SNAP_SIZE`**（`shutil.copyfile` 或 `Copy-Item` 字节复制），并做**快照完整性自检**，状态登记仅作辅助（含"HEAD 无此路径"的如实记录）、快照落点与≥7 天留存；③**§16.2-C 改写为 N6-V2**——**禁用 `git checkout <pin> -- <path>`**（会写 index ⇒ 破坏 index 原态；且过 `core.autocrlf`/`.gitattributes` filter ⇒ 落盘字节 ≠ blob），改为**定向原字节写回**（`wb` 写盘／`Copy-Item`／必要时 `git cat-file` 且**核 `returncode==0`**），全程不碰 index；④**§16.2-D 验证改按「真实写前内容」**——**唯一充分判据 = `sha256==SNAP_SHA256`**，另加 size/逐字节/cmp 与 **index 原态一致**为加强判据；**明写 `git status --porcelain` 非空 ≠ 恢复失败**（HEAD 已含新内容时为预期现象）。**本轮未执行任何 checkout/reset/revert；未改 kb_hook／路由／档位、未跑新测试、未分发。** |
