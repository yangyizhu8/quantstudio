# kb_hook 改造（skipped 字段 + --files glob 展开 + README 显式 skip）— ③实施＋④验收证据 · 2026-11-10

> **派单**：2026-11-10 节点⑥（三项：①`skipped:[{file,reason}]` 输出＋守恒判据 ②`--files` glob 展开修 PS 假空 ③`knowledge/db/README.md` 显式 skip 或纳入校验，择一）。
> **六步**：①方案＝本节 §1（轻量，随件落盘）→②审计＝交⑫复核（本件即复核材料）→③实施（不改产物、不推送）→④验收（§2 读数）→⑤用户确认 →⑥推送。
> **R1 留痕**：`知识反哺：[命中] DB-2026-0003（db/D5/knowledge/db/DB-2026-0003.md，2026-11-10）`——「制品判据随改道失准必须回扫」同族：本次三项改动同属「读数口径/判据」面，验收即按该条目要求做正负向双向核对。

## 1 方案（三项设计）

| # | 项 | 设计 | 判据 |
|---|---|---|---|
| ① | `skipped` 字段 | `check_files` 返回值增 `total` / `skipped[]`；每条 `{file, reason}`，reason 枚举：`glob_no_match` / `not_found` / `non-entry-file` / `no_family_match` / `kb_info_unavailable` | `total == checked + len(skipped)`；`bad` 的 file ∈ checked 集合（bad ⊆ checked） |
| ② | glob 展开 | 新增 `_expand_files`：入参含 `* ? [` ⇒ 按 repo_root `pathlib.glob` 展开（PS 5.1 **不替外部程序展开**通配符，原样传会静默得空集＝`checked=0` 假空）；字面路径原样；去重保序；glob 零命中记 `glob_no_match` 不静默丢 | `--files "knowledge/db/DB-*.md"` ⇒ checked＝实际条目数（非 0） |
| ③ | README 显式 skip | **择「显式 skip」**：新增 `_skip_reason`——文件落在某族 `dir` 下但不符合该族 `entry_glob` ⇒ `non-entry-file`；其余不相关文件 ⇒ `no_family_match`。**不硬编码 README 文件名**，自动覆盖 qmt/pd/db 三族 README | `knowledge/db/README.md` ⇒ skipped 一条 `non-entry-file`，checked=0、total=1 |

**兼容性**（改动前已核调用方）：`kb_draft.py` 仅用 `validate_record`（函数体未改）✓；`check_deploy_closedloop.py` 走 subprocess 读 returncode＋末行 ✓；`--strict` 退出码语义不变（仅 `bad` 非空才 1）✓；JSON 新增键为**增量**（既有键全保留）。

## 2 ④验收读数（可复核）

| # | 项 | 命令 | 读数 | 判定 |
|---|---|---|---|---|
| V1 | 新增单测 6 项 | `pytest tests/test_kb_hook_skipped.py -q` | **6 passed**（T1 守恒／T2 glob／T3 README skip／T4 glob 零命中／T5 bad⊆checked／T6 真实库回归） | ✅ |
| V2 | CLI 混合态 | `kb_hook.py --files knowledge/db/README.md knowledge/db/DB-2026-0001.md` | `体检 1 件（族：db），不合规 0 件，跳过 1 件` ＋ `[skip] knowledge/db/README.md : non-entry-file` | ✅ |
| V3 | glob 真实展开 | `kb_hook.py --files "knowledge/db/DB-*.md" --json` | `total=41 / checked=41 / bad=[] / skipped=[] / families=["db"]`（**修复前此命令 checked=0**） | ✅ |
| V4 | 既有回归（KB 预检） | `pytest tests/test_qmt_kb_preflight.py -q` | **22 passed** | ✅ |
| V5 | 调用方回归 | `check_deploy_closedloop.py` ③ | `族机检 : PASS — 体检 20 件（族：pd），不合规 0 件，跳过 0 件` | ✅ |
| V6 | 守恒判据实测 | V3 读数 | `total(41) == checked(41) + skipped(0)` | ✅ |

> V5 附注：closedloop 脚本整体以 WARN 收尾（⑤ L2 未提供材料 ⇒ 其自述「退出码 2」），**与本次改动无关**（③ 段 PASS）。

## 3 实施中两处失败与归因（如实留痕）

| # | 首轮失败 | 归因 | 处置 |
|---|---|---|---|
| F1 | T1 断言 `not-exist.md → not_found` 失败，实得 `non-entry-file` | **代码语义顺序缺陷**：存在性检查置于族路由之后 ⇒ 族目录内的缺失路径被前缀规则吞成 non-entry-file | 修 `check_files`：`is_file()` 前置（存在性优先于族路由），并加注释 |
| F2 | T6 断言 `checked==3` 失败，实得 41 | **测试假设过时**（非代码 bug）：db 族 10-30 建族时 3 条，11-10 已增至 **41 条**（⑲线持续落条）；glob 展开本身正确 | 改 T6 为**动态断言**（`expected = len(glob("DB-*.md"))`，判 `checked==expected`＋`bad==[]`＋守恒），避免随条目增长失效 |

> 教训归口：F1 属「判据优先级」类（存在性 > 分类）；F2 属「不写死会增长的计数」类——两者均已固化为测试注释，后续改 kb_hook 复用。

## 4 ④验收判据对照（派单原文）

| 派单判据 | 实测 |
|---|---|
| `skipped:[{file,reason}]` 字段 | ✅ §2 V2/V3 |
| `len(files)==checked+len(skipped)` | ✅ V6（`total` 即展开后 files 数，含 glob 零命中项） |
| `bad ⊆ checked` | ✅ T5 |
| `--files` glob 展开修复假空 | ✅ V3（0 → 41） |
| README 显式 skip（reason=non-entry-file） | ✅ V2/T3，且**通用化到三族**（非硬编码单文件） |

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-10 | 建件：三项方案＋V1–V6 读数＋两处失败归因（F1 代码语义／F2 测试假设过时） |
