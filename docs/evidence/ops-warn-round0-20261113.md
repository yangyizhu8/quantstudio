# ops warn 限定试点·首轮回执（B89-U1 甲裁）

> 裁定：B89-U1「甲」（2026-11-13，允许限定试点，无须再请激活许可）
> 本件＝首轮回执（审核指定为下一汇合点），含**实际生效范围、完整扫描清单、T3 及期末时间**

## 1 实际生效范围

| 族 | gate | since | 说明 |
|---|---|---|---|
| **ops** | **`warn`** | **`2026-11-13T20:56:02+08:00`** | 本轮限定试点（唯一变更族） |
| qd | `off` | `null` | 未变 |
| pd | `off` | `null` | 未变 |
| db | `off` | `null` | 未变 |

- 改动方式：**按字段定向修改**（仅 ops 段 `placeholder_gate` 与 `placeholder_gate_since` 两行）；
  `git diff` 实测仅这两行，共享配置中**他线内容零触碰**（`git status` 中他线 `M/??` 未处置）。
- 配置指纹：`knowledge/kb-info.yaml` `55003e52…` → `ef2c3dfc…`（改前/改后 sha256 前 8）。

## 2 时间三分（精确，带时区）

| 口径 | 时刻 | 依据 |
|---|---|---|
| **T1 工作树生效** | **`2026-11-13T20:56:02+08:00`** | 文件写入完成时刻，与 `since` 字段同值 |
| **T2 提交生效** | **`2026-11-13T20:56:49+08:00`** | commit `35a2b7c`（1 文件） |
| **T3 首次真实人工消费** | **`2026-11-13T20:56:50+08:00`** | 首轮全量扫描输出由本人读取并逐条判定 |
| **窗止（T3 + 7×24h）** | **`2026-11-20T20:56:50+08:00`** | 到期即按已批配方处置，**不自动延长** |

> 三者非同一时刻，逐项记录；观察窗**自 T3 起算**。

## 3 完整扫描清单（首轮全量，含新增件与 `.draft` 件）

`knowledge/ops` 目录实际文件 6 个：`OPS-2026-0001.md`／`0002`／`0003`／`0004`／`0005`／`README.md`。
- 进入 `--files` 的**条目件 5 件**（`OPS-*.md` 模式，含 `.draft.` 模式匹配）；`README.md` 属非条目件（既有 `non-entry-file` 规则跳过）。
- **`.draft` 件：0 件**（当前库无）。
- **`checked=5` 与输入条目数 5 一致** ✅（`total=5 checked=5`）。

| 文件 | sha256（前 16） | level | stage | fake | mention | duplicate |
|---|---|---|---|---|---|---|
| OPS-2026-0001.md | `bd5a8e0ba88e9a98` | warn | entry | 0 | — | 0 |
| OPS-2026-0002.md | `15ce0e9ef7532d69` | warn | entry | 0 | — | 0 |
| OPS-2026-0003.md | `b9a511522cbb6fc8` | warn | entry | 0 | — | 0 |
| OPS-2026-0004.md | `ab534816a3197b4d` | warn | entry | 0 | — | 0 |
| OPS-2026-0005.md | `b898193c980aa966` | warn | entry | 0 | **`contract_row`**（引述：内容为「…前半/后半/空串/占位四态…」，属描述占位概念） | 0 |

- **`fake = 0`（5/5）**：无字段假填。
- **`mention = 1`（0005.contract_row）**：位置规则判定为引述，**按 §5「mention 仅记录不处置」**处理，不改条目。
- **`duplicate_id = 0`**。
- `bad=0`；`--strict` 未用于本轮（warn 档不并入 bad，占位不新增失败）。

## 4 指纹留证（代码／配置／条目／环境）

| 项 | 值 |
|---|---|
| HEAD（试点前） | `04d9157` |
| HEAD（T2 提交后） | `35a2b7c` |
| `knowledge/kb-info.yaml`（改前） | `55003e5267eb1aa17c1bfb22352c699b87421fbc82a38a454d121448c643d72d` |
| `knowledge/kb-info.yaml`（改后） | `ef2c3dfc718e831a…`（完整值见 §1 落盘文件） |
| `scripts/kb_hook.py` | `ce38ed739ab19504bd1066a6d3bca3f5d55b9b2c2c52aa33c87f2f102bca265f` |
| `scripts/kb_preflight.py` | `7728b9d4f539b106b54ac25361f8b2ca3e8cee65e1c794364d0b921f6b3cb204` |
| Python | 3.11.9 |
| 条目 5 件 | 见 §3 表（与试点前快照逐一相同 ⇒ 期间条目未变） |

## 5 原始输出（落盘，可直接复核）

| 文件 | 内容 |
|---|---|
| `docs/evidence/ops-warn-round0-20261113.json` | 首轮 `--json` 原始输出（含 `placeholder[]` 5 条） |
| `docs/evidence/ops-warn-round0-20261113.txt` | 首轮文本原始输出 |
| `docs/evidence/ops-warn-baseline-off-json.txt` | **隔离配置**取得的 `off` 预期基线（同代码/依赖/条目/参数，配置在 tmp，真库未切档） |
| `docs/evidence/ops-warn-baseline-off-text.txt` | 同上（文本形态） |

### 5.1 跨档对照（R5 口径：剥离 `placeholder` 键后比较）

- `off_core`（剥离后）sha256 = `09c43ef55f5d5a10f9d5f9f1246a7b5418e3ff314fb3e542ef5e12c9d7ed18e0`
- `cur_core`（剥离后）sha256 = `09c43ef55f5d5a10f9d5f9f1246a7b5418e3ff314fb3e542ef5e12c9d7ed18e0`
- **CORE_IDENTICAL = True**
- text 形态剥离 `[placeholder:…]` 行后：**TEXT_CORE_IDENTICAL = True**
- 键集差异：`off` = `['bad', 'checked', 'families_used', 'note', 'skipped', 'total']`；`warn` = `['bad', 'checked', 'families_used', 'note', 'placeholder', 'skipped', 'total']`（**warn 多 `placeholder` 键，两者原始 SHA 本就不同，不作原始字节比较**）

## 6 后续执行（已批配方）

- **频次**：每日 ≥1 次**全 ops 全量**；条目／检测代码／配置任一变更后**立即复核**。
- **记录**：每次按「时间｜配置锚(HEAD+kb-info sha256)｜文件清单逐名｜档位｜fake/mention/duplicate_id｜处置」落本目录后续轮次文件。
- **处置**：`fake>0` 当日补实并呈**节点⑫复核**；`duplicate_id>0` 取证＋核属主后处置，**不为清零删他线条目**；`mention` 仅记录；误伤按五步（≥2 例才提修订，禁临时降档）。
- **回退（已批配方）**：严重误伤／噪声不可用／无法履行每日全量／明确停止指令／期满 ⇒ **仅 ops 回 `off`、清空 `since`**、留证；**不自动延长、不升 strict、不新增自动任务或接线**。
- **窗止**：`2026-11-20T20:56:50+08:00`。

## 7 首轮结论

**试点启动正常**：生效范围仅 ops、扫描清单与 `checked` 一致（5/5）、`fake=0`、`duplicate_id=0`、唯一 `mention` 为合法引述（不处置）；跨档对照剥离 `placeholder` 键后与 `off` 基线**逐位一致**，未出现任何既有判据漂移。
