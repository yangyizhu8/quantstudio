# 知识条目占位文本检测门方案 v3（新增检测型 · 草案，R1 有条件通过后的补齐稿）

> **版本**：**v3（2026-11-13 按 R1「有条件通过」四项补齐；v1 退回、v2 有条件通过）**
> **R1 补齐要求（逐条对应）**：
> ① 正式 entry 路径**不能仅加 `stage:draft` 就跳门**；草稿**必须隔离、不参与正式回哺**；**trigger 前半复现条件也须非空** ⇒ §3.1／§3.1.2／§2.2(T3)；
> ② **50% 仅作治理预警／清理立项线，不作未标定 FAIL 阈值**；**30 天作清理 SLA 提案，不自动宣告旧内容失效** ⇒ §4.3；
> ③ **明确族 off/warn/strict 与全局 `--strict` 的交互及转档权限，禁止「warn 一轮后自动升 strict」** ⇒ §3.3／§3.5；
> ④ **补齐后可做限定新增检测实现与隔离负例，所有族仍保持 off；激活范围、回退与消费者验证另审** ⇒ 本稿为实施前置，见 §11 实施边界；
> ⑤ **OPS-0003 的「引用」在非受检字段/正文，不等于「受检字段内引述」误伤已覆盖**——须补受检字段内引用的**正例**与**真占位负例** ⇒ §2.0.2／§6.2(FP6–FP8)／§6.1(P1)。
> **状态**：**草案 v3，未实施**（所有族保持 `off`；不改 `kb_hook.py`／README／族档位）。

## 0 知识反哺检索留痕

`知识反哺：[命中] OPS-2026-0003「机检绿≠内容完成」——本门存在理由（ops/D5，2026-11-13）`
`知识反哺：[命中] OPS-2026-0001～0004 已补实，正样本语法范例（ops/D5，2026-11-13）`
`知识反哺：[命中] DB-2026-0003 判据随实现漂移须回扫（db/D5，2026-11-13）`

## 1 存量基线（2026-11-13 01:50 实测，未变）

| 族 | 条目 | 含占位 | trigger 占位 | trigger 两段式 | feedback 占位 | 正文占位 |
|---|---|---|---|---|---|---|
| qd | 22 | 3 | 3 | 10 | 2 | 3 |
| pd | 22 | 8 | 3 | 15 | 4 | 7 |
| db | 44 | 42 | 39 | 0 | 42 | 40 |
| ops | 4 | 1 | **0** | **4** | **0** | 1 |
| 合计 | 92 | 54 | 45 | 29 | 48 | 51 |

`ops` 四条已由节点12 补实并经本线复核 4/4 通过（v2 §1.5，本稿不重复；R1 已采信）。

## 2 规则定义

### 2.0 作用域

**只检查字段值，不扫描正文**（正文可自由讨论"占位"）。

| 对象 | 受检 |
|---|---|
| `trigger` 值 | ✅ |
| `feedback.{contract_row,gate_rule,template_patch}` 值 | ✅ |
| 正文（`## 详情` 等） | ❌（OPS-0003 即正文引述，必须不红） |
| 其它六必填字段 | ❌（既有「非空」维持，不扩范围） |

### 2.0.2 受检字段内：「假填」vs「引述」（R1 第⑤条）

**问题**：若受检字段里合法地引述"占位"（例：`trigger: "看到报「校验通过」实为全占位；问法：这算完成了吗？"`），纯词表判据会误杀。

**判别：位置模式（不是语义猜测）**

```python
FAKE_PREFIX = re.compile(r"^(待补充|待填|TODO|TBD|FIXME|placeholder|占位)\s*([：:…\-—、，,\s]|$)", re.I)

def classify(value):
    v = (value or "").strip()
    if FAKE_PREFIX.match(v):          # 整值占位词开头 ⇒ 假填
        return "fake"
    if PLACEHOLDER_RE.search(v):      # 占位词出现在句中/句尾 ⇒ 引述
        return "mention"
    return None
```

| classify | 行为 |
|---|---|
| `fake` | **FAIL**（真占位） |
| `mention` | **PASS** + 打印 `PLACEHOLDER_MENTION[<field>]`（可见、可审计，但不阻断） |
| `None` | 正常 |

- **判据是位置的，不是猜测的**：开头式＝"用占位词代替内容"；句中式＝"在讲占位这件事"。两者机械可分、可复现。
- 已知边界（如实声明）：若有人把整段内容写成"占位……（实际有内容）"，会命中 `fake` 而误杀——**这正是我们要的从严**（以占位词开头即视为未完成，与"人读第一眼"一致）；如遇合理例外，走 §3.3 的族档位或显式 `--allow-placeholder`。

### 2.1 占位词表

```python
PLACEHOLDER_TOKENS = ("待补充", "待填", "TODO", "TBD", "FIXME", "placeholder", "占位")
```

### 2.2 `trigger` 两段式（五条判据，R1 补充 T3）

```
trigger := <复现条件> <分隔符> "问法" <冒号> <口头问法>
分隔符   := "；" | ";"        冒号 := "：" | ":"
```

| # | 判据 | 实现 | 备注 |
|---|---|---|---|
| T1 | 存在分隔符 | `re.search(r"[；;]", v)` | 半角兼容 |
| T2 | 分隔符后（允许空白）紧跟 `问法`+冒号 | `re.search(r"[；;]\s*问法[：:]", v)` | |
| **T3** | **前半（复现条件）`strip()` 非空且 ≥2 字** | 截取前半计长 | **R1 新增**——v2 漏：`"；问法：怎么又掉了"` 全靠 T5 拦不住（空串不含占位词） |
| T4 | 后半（口头问法）`strip()` ≥4 字 | 截取后半计长 | |
| T5 | 两段各 `classify()` ≠ `fake` | §2.0.2 | 引述放行、假填拦下 |

> **v2 缺陷自纠**：v2 的 P5（`"；问法：怎么又掉了"`）期望 FAIL，但按 v2 四条判据（T4 只判后半、T5 只查占位词）**空的前半会 PASS** ⇒ 期望与实现不符。本稿以 T3 补齐，并保留 P5 作回归。

### 2.3 `feedback` 三键

| # | 判据 |
|---|---|
| F1 | 至少一键「非空 且 `classify()` 不为 `fake`」 |
| F2 | 任何**非空**键不得 `classify()=="fake"`（引述=mention 允许，但会打印） |

## 3 阶段·路径·标记·档位

### 3.1 正式/草稿判定（纯路径，R1 第①条强化）

| 态 | 判据（`AND`） |
|---|---|
| **正式 entry** | 路径匹配族 `entry_glob` ∧ 位于 `knowledge/<族>/` ∧ basename 匹配 `id_pattern` |
| **草稿（隔离区）** | **仅**：位于**族目录之外**（`docs/drafts/`、`tmp/`、任意 `--out`） |

> **实现口径（2026-11-13 R2 后定稿，与代码一致）**：族目录内**不认任何降级标记**——`basename` 含 `.draft.` 仍判**正式 entry**（仅打印 `DRAFT_NAME_IGNORED_ON_ENTRY_PATH`）。否则"改个文件名就跳门"是比 `stage: draft` 更隐蔽的后门。

#### 3.1.1 `stage: draft` 的效力（R1 核心修正）

- **在正式 entry 路径上：`stage: draft` 不生效**，一律按**正式态**判（`basename` 含 `.draft.` 时另打印 `DRAFT_NAME_IGNORED_ON_ENTRY_PATH`）。
  理由（R1/R2）：否则"加一行 `stage: draft`（或改个文件名）就跳门"——标记变成后门。
- **在草稿隔离区内：`stage: draft` 有效**（允许占位，打 `DRAFT` 标记）。
- 即：**路径决定身份，标记只能在隔离区内加注**，不能把正式路径降级为草稿。

#### 3.1.2 草稿不参与正式回哺（R1 第二半）

| 通道 | 草稿是否参与 |
|---|---|
| 族 `entry_glob` 机检（`kb_hook`） | **否**（不在族目录 + 不匹配 `id_pattern`，天然不命中） |
| R4 验收沉淀计数（"本轮沉淀：XXX-YYYY-NNNN"） | **否**（只认正式 entry） |
| 知识检索/回哺（人查库、`kb_search` 类消费） | **否**——口径声明：草稿仅供起草期自用，**不得被任何回哺面当条目消费** |
| 幂等/编号分配（`kb_draft` 的 max+1） | **否**（未落位不占号） |

### 3.2 两态行为

| 态 | 占位检测 |
|---|---|
| 草稿 | 允许；`kb_draft` 打印 `DRAFT: 含 N 处占位`，`--json` 出 `placeholder_hits` |
| 正式 entry | 按 §3.3 档位 |

### 3.3 档位与全局 `--strict` 的交互矩阵（R1 第③条）

**两个独立旋钮**：

- **全局 `--strict`**（CLI 开关）：管**既有判据**（六必填/枚举/域/`recurrence`/`feedback 非空`/`status`）的宽严口径——沿用现状语义，本门不改变它。
- **族 `placeholder_gate`**：管**本门**（占位检测）在该族的启用强度。

| 族档位 ＼ 全局 `--strict` | 未加 `--strict` | 加了 `--strict` |
|---|---|---|
| `off`（默认） | 本门**不介入**（不打印、不计入） | 本门**仍不介入**（族级 off 优先），仅输出 `placeholder: off(family-not-enabled)` 一行说明 |
| `warn` | 检出 → 打印 `PLACEHOLDER[warn]`，verdict 不变 | 同上（verdict 仍不变）——**warn 不因全局 strict 而升为 FAIL** |
| `strict` | 检出 → **FAIL** | 检出 → **FAIL**（与全局无关，本来就是 FAIL） |

**结论**：最终 verdict ＝ `既有判据 FAIL` **或** `(族档位=strict ∧ 检出)`；**全局 `--strict` 永不隐式提档**。

> **契约差异（2026-11-13 二轮审核要求核清）**：本门 `strict` 档＝"命中并入 `bad`"，CLI `--strict`＝"有 `bad` 即 `rc=1`"，两者**不同轴**；warn 档命中**不并入 `bad`** ⇒ **即使带 `--strict` 也不产生 `rc=1`**（但既有 schema/`duplicate_id` 错误的退出语义**完全保留**）。
> 澄清表见 `docs/handoff/ops-warn-activation-plan-20261113.md` §7；**不得**把"warn 不新增失败"表述为"CLI 恒 `rc=0`"。

### 3.4 转档权限（R1 第③条第二半）

| 变更 | 权限 | 留痕 |
|---|---|---|
| `off → warn`、`warn → strict` | **须独立审/总调度批准**（书面） | `kb-info.yaml` 的 `placeholder_gate_since` 必填 + `docs/decisions-pending.md` 登记 |
| `strict → off/warn`（**放宽检测**） | **不得笼统视为"安全方向"**：放宽会立即减少拦截面，须按**已批准的回退配方**（§8 回退项①）执行；无对应配方时一律走既有审批（R2 纠正，2026-11-13） | 同样须更新 `placeholder_gate_since` + 登记，不得以"急停"为由免审 |
| 任何自动化 | **禁止** | **不得出现"warn 观察一轮后自动升 strict"逻辑**（v2 §3.4 的推进表已按本表改写，见下） |

**推进表（改为"候选"性质，非时间表）**：

| 族 | 当前档位 | 升档前置（均须批准） |
|---|---|---|
| ops | `off` | 独立审批准 + 激活范围另审（§11） |
| qd / pd | `off` | 同左 + 存量（各 3 件）补实 |
| db | `off` | 同左 + 存量清理立项（42 件） |

### 3.5 强制边界（沿用 v2，重申）

`off` **只关本门**，六必填/枚举/`domain`/`recurrence`/`feedback 非空`/`status` **照常生效**（§6.3 OFF1–OFF2 断言）。

## 4 历史存量冻结

### 4.1 登记结构（同 v2，字段不变）

`path → {sha256, fields[], placeholder_snapshot, owner, target_round, registered_at, last_verified_at}`；`cleanup_log` 记 `cleaned|renewed|dropped`。

### 4.2 G1–G4（同 v2）

G1 sha256 主判据（禁 mtime）；G2 指纹变 ⇒ `PLACEHOLDER_REGISTRATION_STALE`（strict 档 FAIL）；G3 **字段级边界**（同文件未登记字段出现占位照常判）；G4 必带 `owner`+`target_round`。

### 4.3 两项降级（R1 第②条）

| 机制 | v2（已撤） | **v3（本稿）** |
|---|---|---|
| 50% 数量闸 | `PLACEHOLDER_BACKLOG_MAJOR` **FAIL** | **仅打印治理预警**：`PLACEHOLDER_BACKLOG_MAJOR[warn]`（warn/off 档一律不 FAIL；strict 档也不据此 FAIL）——它的用途是**触发"清理立项线"**，不是判据。附：db 42/44≈95%，据此**建议**立项清理（§5） |
| 30 天 | `PLACEHOLDER_REGISTRATION_EXPIRED` **FAIL** | 改为**清理 SLA 提醒**：`PLACEHOLDER_SLA_DUE[warn]`，**不宣告旧内容失效、不移除登记效力**——登记继续有效，直到人工复核（`cleaned/renewed/dropped` 之一）为止 |

- **理由**（R1）：未标定的阈值不得直接当 FAIL；SLA 到期描述的是"该复核了"，不是"内容无效"。
- 两者均可在日后**经标定后**再申请升为 FAIL（须独立审批，且须给出标定依据）。

## 5 清理路径

| 族 | 件数 | 路径 |
|---|---|---|
| ops | 0 | — |
| qd / pd | 3 / 3 | 机会式补实 |
| db | 42 | **建议单独立项**（依据 §4.3 预警），优先补「trigger 可由 symptom 推导」的件 |

收敛判据：`N_i=0` 连续 3 轮且 `C_i ≥ 3/轮`。

## 6 负例与正例

### 6.1 应红（True Positive）

| # | 输入 | 期望 |
|---|---|---|
| **P1** | `trigger: "待补充：最小复现触发条件"`（开头式假填） | **FAIL**（T1/T2 缺 + classify=fake） |
| P2 | `trigger: "重启后不活"` | FAIL（T2 无问法段） |
| P3 | `trigger: "条件；问法："` | FAIL（T4 后半空） |
| P4 | `trigger: "条件；问法：怎"` | FAIL（T4 <4 字） |
| **P5** | `trigger: "；问法：怎么又掉了"`（前半空） | **FAIL（T3，R1 新增）** ← v2 会漏 |
| P6 | `trigger: "就两个字；问法：怎么又掉了"`（前半 <2 字，仅示意） | FAIL（T3） |
| F1 | 三键全 `"待补充：…"` | FAIL（F1+F2，classify=fake） |
| F3 | `contract_row: "待补充：registry 新增行"`（其余 null） | FAIL（F2 fake） |
| R1 | 已登记件 sha256 不符 | FAIL `PLACEHOLDER_REGISTRATION_STALE`（strict 档） |
| R2 | 已登记 `fields=[trigger]`，`feedback.contract_row` 出现占位 | FAIL（G3 边界） |

### 6.2 应绿（含 R1 要求的受检字段引述正例）

| # | 输入 | 期望 | 依据 |
|---|---|---|---|
| FP1 | 正文含「占位」、字段干净（OPS-0003 实样） | **PASS** | §2.0 不扫正文 |
| **FP6** | **`trigger: "看到报「校验通过」实为全占位；问法：这算完成了吗？"`（受检字段内引述）** | **PASS + `PLACEHOLDER_MENTION`** | §2.0.2 classify=mention |
| **FP7** | **`feedback.contract_row: "禁止用占位文字充当已填内容；须写实质判据"`（受检字段内引述）** | **PASS + mention** | 同上 |
| FP2 | `feedback: {contract_row: null, gate_rule: "例", template_patch: null}` | PASS | F1 满足 |
| FP3 | `trigger: "条件; 问法: 怎么又掉了"`（半角） | PASS | §2.2 兼容 |
| FP4 | `trigger: "条件；问法：怎么又掉了"` | PASS | 正样本 |
| **FP5** | **正式 entry 路径 + `stage: draft` + 含占位** | **FAIL + `STAGE_DRAFT_IGNORED_ON_ENTRY_PATH`**（**不再 PASS**） | §3.1.1 R1 修正 |
| **FP8** | **草稿隔离区文件（`docs/drafts/X.draft.md`）+ `stage: draft` + 含占位** | **PASS（DRAFT 标记）** | §3.1.1 另一半 |

> **FP5 由"应绿"改为"应红"**：v2 把「正式路径 + `stage:draft`」列为 PASS，正是 R1 指出的后门，本稿纠正。

### 6.3 off / warn / strict × 全局 --strict 断言

| # | 场景 | 期望 |
|---|---|---|
| OFF1 | 族 `off` + 缺 `root_cause`（不加 `--strict`） | FAIL（原必填） |
| OFF2 | 族 `off` + `fix_layer` 越界 | FAIL（枚举） |
| OFF3 | 族 `off` + 占位 trigger（不加 / 加 `--strict` 各测一次） | **两次都 PASS**（本门确未介入） |
| **W1** | 族 `warn` + 占位 trigger + **加 `--strict`** | **PASS**（verdict 不变）+ 打印 `PLACEHOLDER[warn]` |
| **S1** | 族 `strict` + 占位 trigger（不加 `--strict`） | **FAIL** |

## 7 文档-实现一致性

现 README §2／§3.1 标「trigger 两段式 ✅ 机检强制」，与实现不符。**获批后**改为「§2.2 语法，随 `placeholder_gate` 生效，当前档位见 `kb-info.yaml`」；若方案被否 ⇒ 立即改「约定（非机检）」。**文档不得先于实现宣称已强制。**

## 8 族归属建议（R1：「不因 git 就入 db、不因工具就入 qd」）

| 候选教训 | 本文建议归属 | 判据（按落点/入口，不按关键词） |
|---|---|---|
| 「占位不可作合规证据／正文引述≠字段假填」 | **ops / guard**（知识库运维门禁，落点是校验器与门档位） | 其 `fix_target` 是"机检门的判据与档位"，属**运维门禁**，非数据库引擎行为——**不因出现"判据"二字就归 db artifact_rule** |
| 「BOM／编码不得改变判定结果」 | **db / artifact_rule**（可议）或 **ops / probe** | 落点是"外来制品的编码容错"；若最终判定发生在**制品校验器层**⇒ db artifact_rule；若发生在**通用读取工具层**⇒ ops probe。**建议由条目起草方按 `fix_target` 实际文件定，族归属在条目里写清依据**，不由关键词自动路由 |

> 结论：族归属以 **`fix_target` 的实际落点** 为准；关键词（git/db/工具）不构成路由依据。

## 9 影响面与回退

| 项 | 内容 |
|---|---|
| 改动面（获批后） | `kb_hook.py`（+约 65 行）+ `kb-info.yaml`（每族两键，默认 off）+ `kb_draft.py`（DRAFT 打印/`--json`）+ `scripts/data/kb-placeholder-baseline.json` + 测试 + 证据件 + README 措辞 |
| 不改 | 六必填/枚举/`status_vocab`/`recurrence`/既有条目内容/既有 29 条正样本 |
| 回退 | ①族 `off`＝仅新门回退（原必填仍在，OFF1/OFF2 断言）；②`git revert` 单笔；③删登记 JSON＝清冻结豁免 |
| 验收 | §6 全部用例；ops `strict` 后 `bad=[]`；**未启用族 verdict 与今日逐位一致**；OPS-0003 在 strict 下仍 PASS（正文引述）＋FP6/FP7（受检字段引述）PASS |

## 10 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-12 | v1（全角唯一；草稿/正式＝过机检＝循环定义）。 |
| 2026-11-13 | v2（R4 五条：路径判据/指纹报警/半角兼容/字段边界/误伤守卫）。 |
| **2026-11-13** | **v3（R1 四项补齐）**：①`stage:draft` 在正式 entry **不生效**（FP5 由绿改红）＋草稿须隔离且不参与正式回哺（§3.1.2 四通道）＋**T3 前半非空≥2 字**（并自纠 v2 的 P5 会漏）；②50% 降为**治理预警**、30 天降为**SLA 提醒**（均不 FAIL、不宣告失效）；③§3.3 档位 × 全局 `--strict` **交互矩阵**＋§3.4 **转档权限**（禁自动升档）；④§2.0.2 受检字段「fake/mention」位置判别＋FP6/FP7 正例（受检字段内引述）＋P1/P6 真占位负例；⑤新增 §8 族归属建议（按 `fix_target` 落点，不按关键词）。**状态：待 R1 复核，仍不实施（全族 off）。** |

## 11 实施边界（R1 第④条：补齐后可做的限定实现，本轮不做）

> 本稿为**实施前置**。R1 已允许「补齐后可做**限定**新增检测实现与隔离负例」，但明确**所有族保持 `off`**，且 **激活范围、回退验证、消费者验证另审**。
>
> **限定实现范围（下一轮拟做，届时按本稿 §2/§3/§4/§6 落码）**：
> 1. `kb_hook.py`：新增 `PLACEHOLDER_TOKENS`／`classify()`／`_trigger_errors()`／`_feedback_placeholder_errors()`／`_entry_stage()`／族档位读取；**默认全部 off ⇒ 现有 verdict 不变（纯增益）**。
> 2. `kb-info.yaml`：每族写 `placeholder_gate: off` + `placeholder_gate_since: null`（登记但未启用）。
> 3. 测试：§6 的 P1–P6／F1·F3／R1·R2／FP1–FP8／OFF1–OFF3／W1／S1 **全量落为隔离负例**（tmp 沙箱或临时条目，跑后即删）。
> 4. **不做**：不改任何族档位、不改 README 断言（等激活审核）、不建存量登记 JSON（等存量处置立项）。
