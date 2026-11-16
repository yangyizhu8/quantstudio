# firefly 修复批次 · 本地可审交付包（DRIFT-N17-r5 · 仅文档）

> 用途：终审/落仓/下发**三闸各自的审批依据**集于一处。本轮**未** stage、**未** commit、**未** push、**未**通知客户、**未** ATTACH 任何库、**未**跑 prepare/pip/新 run。

> 生成时间：2026-11-16 21:5x（本地）· 生成方式：脚本实测各文件字节与 sha256，非人工誊抄。


## 一、交付总索引

### 1.1 客户交付件（下发对象）

| 路径 | 字节 | sha256 | 说明 |
|---|---|---|---|
| `docs/handoff/firefly-site-recovery-procedure-20261114.md` | 92,633 | 19ACA564BF85FB680342678F82C56C6EB8791D35239FC8456709FD86EDEE0E60 | **v15** 客户附件：现场处置流程 |
| `docs/handoff/customer-notice-firefly-defect-batch-20261027.md` | 31,445 | AFE1CB0BF8410FF0322C8A7480235B1883E96964CD9BB597C8B3AAA13483A8F5 | **rev4.3** 客户通知（rev4.2→rev4.3 仅 3 处差量） |

### 1.2 内部证据件（内部审阅，不下发）

| 路径 | 字节 | sha256 | 说明 |
|---|---|---|---|
| `docs/evidence/firefly-emptydb-synthetic-run-20261116.md` | 20,523 | 3E3AE03CC8C925DE15A29B56801A0E4E7060676420C7BFD5413C2F45C18F4FA9 | 空库判据合成验证读数（§9–§12：v15 方案/一致性/偏离 D1–D5/候裁） |
| `docs/evidence/firefly-limited-action-request-20261116.md` | 9,928 | 595A7C59096B1255E9E842796B76925ECB417730DF2C2B3F1702C024DF372780 | 有限动作呈批件 LAR-20261116-01（已批准+首轮执行） |
| `docs/evidence/firefly-synthetic-validation-request-20261116.md` | 32,392 | A02CBD552539AE17E6B12E7E1FA7FFA8DB0B80514BED0E9573ADD54800579A6B | **rev4** 合成验证主锚申请单 |
| `docs/evidence/firefly-site-procedure-isolation-validation-20261114.md` | 44,594 | E319F1CFF2FB2F9BC51839A12FFBF9E629A501B799FC4D006BF10A537B08C26D | 流程件隔离实测证据（备份/重建/恢复/退出码） |
| `docs/evidence/firefly-v15-merge-20261116.md` | 13,530 | 3EA9294B133243189EED33A956AEA6C39372696A5D750A0FF79BFC6F650EDA77 | v15 合并说明（完整 diff / hash / 客户剩余项 D1–D9） |
| `docs/evidence/firefly-site-procedure-review-index-20261115.md` | 43,697 | 01F7846E032323702054E2B3760A64AF4F0DC8E70888256FBCBAE26540183778 | 终审对照索引（v20）——**其自身 sha 在此外部登记**；值为**生成时点快照**，索引登记本包后会变，最新请现场实测 |
| `docs/evidence/firefly-site-recovery-procedure-v14-frozen-20261116.md` | 90,003 | 7EBDE2EE60C6D387F2C9B7D2F5FCAA138FAEDFFC959F2750822386888CB06702 | **v14** v14 冻结副本（原文件从未提交，唯一实物回退点） |
| `docs/evidence/firefly-v14-to-v15-diff-20261116.patch` | 8,143 | 398344850C1DC60DA60491ED20C6898A2E082FD22F0DF3F605A8C508AB4E2F9D | 流程件 v14→v15 完整 unified diff |
| `docs/evidence/firefly-notice-rev4.3-diff-20261116.patch` | 16,279 | 19B532F1D8BF54F825922D865A8D972842C658D26787907435473DE84203A594 | 通知 rev4.2→rev4.3 完整 unified diff |

> 终审对照索引的**自身 sha 由外部登记**（见 §1.2 末行），**索引内 §一 的自身行标 EXEMPT 只是 checker 豁免**——写进去即改变自身字节，故**索引不可能自证其 sha**。

> 双向登记必然使至少一方成为**时点快照**：索引登记本包 ⇒ 索引随后变化；本包登记索引 ⇒ 该值随索引更新而过时。**校验一律以现场 `Get-FileHash` 实测为准**，不得用任一方宣称自证。

### 1.3 验证工程件（可入库小文本；不含二进制）

| 路径 | 字节 | sha256 | 说明 |
|---|---|---|---|
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/01-probe-macros-scalar.diff` | 730 | EBFDF07FA0C0E67B05202D15C688739C64F6F67BB0E9370A7AE5C576CAF014CE | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/02-probe-macros-table.diff` | 603 | 3BEC1D4D74112AB0D68E525742EE8D5562C5BEFE680B32B549D8B93151CD2DED | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/03-matrix-macros-scalar.diff` | 824 | 68CDE63844604978FB1601DAA210454E75C01CD6AD583EE4D724AF4E77D500E4 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/04-matrix-macros-table.diff` | 667 | 996A97186AE65FD5301A4B1507440756418C9B4C784788A10179F419918B6740 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/MANIFEST.json` | 3,847 | 5C92A51EB9C50DAF7BC327CD0BD2161251F7305465ABDD92BA59B25DF6962304 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/MANIFEST.rev2.json` | 5,796 | C3EE5D36AF7B507CA9B9DF62F1CC1110D783960E5383132AC0447767DB946732 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/PLAN.md` | 7,027 | A60886CD76EAD5BB6FEFADB175D3CAC23ACA73C7B23F54A075FE925F4D1CDD52 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/ROUTING-FIX.rev2.diff` | 1,938 | B9141A1E0FC453B420F74541EC43965F50821991DF6D596F94597676AE7F1007 | v15 候选伴随件（静态） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/_iso_empty_db_matrix_v15.py` | 10,825 | F88A759B7CC96AA5D434D24A43E70A0BD523A18C537A4AA33E5C4D01D028E227 | **已运行**：两版各 1 次（驱动 11 项断言） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/_prepare_probe_v15.py` | 8,144 | C296046FEA596CBFB6A4736103CDC067E9C723DF688CE7B45991F9319FD22BFA | **未运行**（本轮未执行 prepare；按授权单列） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/restore_probe_v15.py` | 7,407 | BA22F6084394ECEA6F35FEBE417C5A906670598A8CEE14A91B16E539F5D5B3CA | **已运行**：两版各 1 次（156 / 145，各 11/11 MATCH） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/SUMMARY.txt` | 3,931 | 2FCDC10402EFED1142D8EED8E7E9F506FE5701C84C7FFC9963B62761FF8BC332 | 两版实测汇总（11/11 MATCH × 2） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/MANIFEST.rev3.validation.json` | 2,732 | 3B869B5F510443D9439E6085D34E08DFBE5ED401438484846F1B042A1CCBF2D5 | 两版结果机读件 |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/console.log` | 2,326 | 6B7BC74C7EDB3B1D369D879638B5DAECA525322878FF69657DFB7908AE2855F1 | 156 控制台（**UTF-16LE**） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/rc.txt` | 9 | 08A6B6A0C481B0405800119C8C06794269EC7C499CF6A686ACF9FD06E4EA875D | rc=0 退出码（两版均为 0） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/runs/20261116T120635_3e7d/summary.json` | 1,751 | C86255B78FB17E8310722C6C3DD55CBB8B869C4F32EA3DA66630C2CBAB3CDEAB | 156 run summary |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/console.log` | 2,326 | 3791D506C20D2F475C316735C2494257DBCFAF104317C6471C0F8872D5684CAF | 145 控制台（**UTF-16LE**） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/rc.txt` | 9 | 08A6B6A0C481B0405800119C8C06794269EC7C499CF6A686ACF9FD06E4EA875D | rc=0 退出码（两版均为 0） |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/runs/20261116T120723_a93d/summary.json` | 1,751 | 851698D9AF4DFC2026A7BBB9A0ED22F8271A6B23853E828552CB5D732074FA53 | 145 run summary |

### 1.4 明确排除（不入库）

| 路径 | 说明 | 合计字节 |
|---|---|---|
| `.specify/bugs/firefly-engine-defects-20261025/.venv_156` | Python 虚拟环境（many 文件） | 61,985,488 |
| `.specify/bugs/firefly-engine-defects-20261025/.venv_145` | Python 虚拟环境（many 文件） | 59,962,033 |
| `.specify/bugs/firefly-engine-defects-20261025/_iso_empty_db_156` | v14 失败 run（156） 下 *.db（合成库二进制，1 个） | 12,288 |
| `.specify/bugs/firefly-engine-defects-20261025/_iso_empty_db_145` | v14 失败 run（145） 下 *.db（合成库二进制，1 个） | 12,288 |
| `.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z` | v15 验证目录 下 *.db（合成库二进制，22 个） | 3,940,352 |

> 排除理由：虚拟环境与合成库二进制（`.db`）**不可入库**（体积、可执行体、非文本）；它们只在本地复算用。


## 二、版本与自检要求

### 2.1 生产准入判据（**唯一**，非本文自定）

| 项 | 值 |
|---|---|
| 目标版本 | **1.5.6**（当前生产目标） |
| 回退位 | **1.4.5** |
| 放行集合 | `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}` |
| 判定源（已发布代码） | `quantstudio/pipeline/duckdb_version_policy.py` 7,675 B · `CB9599B35DBC71E5D53CF38496ACCAC8C98B7406786F6518EDB5AC9D9C901A18` |
| 集合外版本 | **拒启（退出码 3，由该判定源实现）** |

> **放行与实测是两件事**：集合外版本**即使**在副本上重测命令全部通过，**也不因此获得生产放行**——生产准入只由上面的判定源给出；副本重测的作用仅仅是回答「这些命令在该版本上能不能跑」。

### 2.2 流程命令的实测范围（**不是**放行依据）

| 项 | 范围 |
|---|---|
| 已实测版本 | **仅 1.5.6 与 1.4.5**（合成库、隔离环境、`cluster`/备份/重建/恢复侧命令） |
| 已实测内容 | 两版各 1 次空库判据合成验证（11/11 MATCH、`rc=0`）＋ 流程命令隔离实测 |
| **未覆盖** | 其他任何 DuckDB 版本、客户现场数据规模、生产库形态 |
| 结论上限 | 只能说「在 1.5.6 / 1.4.5 的合成场景下成立」，**不得外推**为跨版本或生产等价 |

### 2.3 自检要求（三条，缺一不得放行）

| # | 自检 | 做法 | 失败后果 |
|---|---|---|---|
| S-1 | **版本自查（生产准入）** | 客户现场核实际 DuckDB 版本是否落在放行集合内 | 集合外 ⇒ **生产不放行**（判定源拒启）；副本重测**只回答命令能否跑，不产生放行** |
| S-2 | **空库判据负例自检**（换版本时必做） | 在副本上跑两个负例：自建标量宏 `CREATE MACRO m1(x) AS x+1`、自建表宏 `CREATE MACRO tm1(x) AS TABLE SELECT x AS c`，均应判 **FAIL（非空）** | 若判成「空库通过」⇒ 该版本把用户宏也标 `internal` ⇒ **v15 判据在该版本失效**，不得放行 |
| S-3 | **证据完整性** | `PASS_EMPTY_DB` 必须带 `marker_line` + `scope` + `objects=0` + `by_source`（六源分列） | 缺项 ⇒ 结论不成立（不得口头补齐） |

> S-2 的依据：v15 的 `AND NOT internal` 依赖「用户宏 `internal=False`」；该前提**仅在两版实测成立**（2026-11-16 两版各一次、11/11 MATCH，`C5f`/`C5g` 均 `FAIL`），**不是跨版本保证**。


## 三、现场证据与 D1–D9 归属

| # | 事项 | 归属方 | 当前状态 | 证据/指针 |
|---|---|---|---|---|
| **D-1** | 《修复完成通知 N-20261027-01》下发 | **用户**（确认闸）→ 客户运维（登记回执） | **未下发** | 通知顶部状态行；本包 §一 1.1 |
| **D-2** | v15 文档 + rev4.3 通知的**终审与落仓** | **用户/总调度**（终审闸）→ 本方执行 | 未终审、未提交 | 本包 §四、§六 |
| **D-3** | 客户侧 **DuckDB 版本自查** | **客户** | 待客户执行 | 流程件「版本与命令适配」；本包 §二 S-1 |
| **D-4** | 客户回传读数（probe 输出 / 逐库 sha256 / WAL 指纹） | **客户** | 待现场 | 流程件 §2.4.1、§2.4.3 |
| **D-5** | 跨版本适用界（`internal` 语义） | **本方**（已写入）→ 客户（自检义务） | 已写入 v15 段④；实测两版成立 | `docs/evidence/firefly-emptydb-synthetic-run-20261116.md` §9.5；本包 §二 S-2 |
| **D-6** | 未测面（`DOUBLE/REAL` 精确恢复不可证、内存峰值、并发写、只读打开痕迹面已测） | **本方**（结论上限） | 长期未测 | 流程件 §2.4.5/§2.4.6/A.4；索引 §四 |
| **D-7** | 无键表排除须客户**书面接受** | **客户** | 条件触发 | 流程件 A.3；通知 rev3.4 ⑤ |
| **D-8** | §3.6 用户例外申请 | **客户业主本人**（书面） | **零批准先例** | 流程件 §3.6；通知 rev4.0 ③ |
| **D-9** | 方案 B（同版本空库基线差集） | **本方**（内部备选） | 仅登记未实施 | `MANIFEST.rev2.json` `plan_b`；**不得自动切换** |


## 四、精确拟 Git 范围（落仓闸用）

### 4.1 拟入库（逐路径，精确 add；禁 `git add -A` / 禁整目录 add）

共 **31** 个文件：

```
docs/handoff/firefly-site-recovery-procedure-20261114.md
docs/handoff/customer-notice-firefly-defect-batch-20261027.md
docs/evidence/firefly-emptydb-synthetic-run-20261116.md
docs/evidence/firefly-limited-action-request-20261116.md
docs/evidence/firefly-synthetic-validation-request-20261116.md
docs/evidence/firefly-site-procedure-isolation-validation-20261114.md
docs/evidence/firefly-v15-merge-20261116.md
docs/evidence/firefly-site-procedure-review-index-20261115.md
docs/evidence/firefly-site-recovery-procedure-v14-frozen-20261116.md
docs/evidence/firefly-v14-to-v15-diff-20261116.patch
docs/evidence/firefly-notice-rev4.3-diff-20261116.patch
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/01-probe-macros-scalar.diff
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/02-probe-macros-table.diff
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/03-matrix-macros-scalar.diff
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/04-matrix-macros-table.diff
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/MANIFEST.json
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/MANIFEST.rev2.json
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/PLAN.md
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/ROUTING-FIX.rev2.diff
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/_iso_empty_db_matrix_v15.py
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/_prepare_probe_v15.py
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/restore_probe_v15.py
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/SUMMARY.txt
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/MANIFEST.rev3.validation.json
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/console.log
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/rc.txt
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/156/runs/20261116T120635_3e7d/summary.json
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/console.log
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/rc.txt
.specify/bugs/firefly-engine-defects-20261025/candidate-v15-20261116T090251Z/validation-20261116T120602Z/145/runs/20261116T120723_a93d/summary.json
docs/evidence/firefly-delivery-package-20261116.md
```

### 4.2 明确排除（即便在拟入库目录下也不 add）

- `.specify/bugs/firefly-engine-defects-20261025/.venv_156/` · `.venv_145/`（虚拟环境）
- 上述两目录下全部 `*.db`（合成库，含 v14 失败 run 与 v15 验证 run）
- `.specify/bugs/firefly-engine-defects-20261025/_iso_*.py` 与 `_patch_*.py`（历轮一次性脚本，未纳入交付）

### 4.3 未跟踪状态实测（落仓前必读）

`git status --porcelain` 实测（摘要，非全量）：
- 本批相关**未跟踪**：`.specify/bugs/`（**整个目录从未入库**）、`docs/design-*.md` 等他线产物若干；
- 本批相关**已修改未提交**：`docs/handoff/customer-notice-firefly-defect-batch-20261027.md`、`docs/handoff/dispatch-graph.md` 等他线文件；
- ⇒ **落仓必须**：①精确路径 `git add <路径>`（逐个）；②`STAGED_SCOPE_ALLOW` 声明同批白名单；③`git -c core.quotepath=false`；④commit 前 `git diff --cached --name-only` 核**整个 index**（防他线 staged 串车）；⑤写后核 `git log -1 --format="%s"` 与 `git show --name-only`。


## 五、相关已发布代码依据

| 依据 | 作用 | 指纹 |
|---|---|---|
| `quantstudio/pipeline/duckdb_version_policy.py` | 版本放行集合唯一判定源（`1.4.5 ≤ x < 1.5` ∪ `1.5.6`）；集合外拒启 | 7,675 B · `CB9599B35DBC71E5D53CF38496ACCAC8C98B7406786F6518EDB5AC9D9C901A18` |
| `scripts/check-staged-scope.py` | 门1 提交范围机器闸（**由 `.githooks/pre-commit` L23 调用**；`STAGED_SCOPE_ALLOW` 未声明＝必拒） | 11,265 B · `7500583C0FC835F5817C8FA340CD84CB762CFBAAF830F8FA165B707E7BEE38F0` |
| `5655fb4` / `2707414` | 通知「适用版本」要求客户版本同时包含的两笔修复（祖先验证） | 见通知第三节第 3 步 |
> 注：本文档只引用，不改动上述已发布代码。


## 六、待用户批准的**具体动作**（三闸分列，互不代替）

| 闸 | 具体动作 | 影响面 | 不可逆性 | 建议 |
|---|---|---|---|---|
| **闸① 文档通过** | 批准流程件 v15（`19ACA564…`）与通知 rev4.3（`AFE1CB0B…`）为定稿；如需修改则回退到本轮 diff（`39834485…` / `19B532F1…`） | 仅文本 | 可逆 | 先过闸① |
| **闸② 落仓** | 按 §四 精确清单 `git add` → `STAGED_SCOPE_ALLOW` → commit（message 无 BOM、uuid 文件名）→ 写后核标题与文件清单 | 仓库历史（**`.specify/bugs/` 首次入库**） | 可逆（本地 commit 未推送） | 闸① 通过后执行 |
| **闸③ 客户下发** | 由用户转发通知 rev4.3 + 附件 v15 给客户；随后客户运维登记下发回执（客户/日期/通道/目标版本） | **对外** | **不可逆** | 闸② 且客户侧准备就绪后 |


> **本轮仅完成「供审批」的准备工作**：三闸均**未**启动。§四 的清单是**拟定**，`git add` 一条未执行。


## 七、本轮未做（边界自查）

未客户恢复、未 `ATTACH`（真实/备份/客户库）、未跑新 run、未 `prepare`、未 `pip`、未清理、未 stage、未 commit、未 push、未直接通知客户。

## 八、如实申报

1. 通知本轮**改了 3 处**（标题版本、rev4.3 段前置、附件版本号 `v2`→`v15`）——属「必要差量对齐」，**未重写流程、未新增测试**。
2. `git status` 显示工作区**大量他线未提交改动**（`dispatch-graph.md`、`lessons-dev-line.md` 等）；本轮**未触碰**，落仓时须按 §四 3. 的姿势执行。
3. 交付包内所有字节/sha 均**脚本实测**；`console.log` 为 UTF-16LE（PS `*>` 默认），sha 按原始字节。
4. §四 拟入库清单含 `SUMMARY.txt` 与 4 个 `.diff/.patch`；若审核方不希望入库一次性 diff，可整组剔除而不影响其他件。
