# 门1（提交范围门）＋ 门2（legacy 逐件指纹 / 探针隔离）实施＋验收证据 · 2026-11-11

> **派单**：节点⑥ 批五十一自纠（今日 11-11 内完成），两门作为**下次推送硬前置**；完成后回执⑫。
> **六步轻量**：①方案＝§1 → ②⑫复核 → ③实施（不推送）→ ④验收＝§2 → ⑤⑥随出仓批。
> **R1 留痕**：`知识反哺：[命中] DB-2026-0003（db/D5/knowledge/db/DB-2026-0003.md，2026-11-11）`——制品判据随改道失准须回扫；本次两门即「判据侧」加固，验收按正负向双向核。

## 1 方案（两门）

### 门1 `scripts/check-staged-scope.py`（新）

| 项 | 设计 |
|---|---|
| 目的 | 比对 **git index 变更集** ⊆ 本会话声明白名单；不符即拒（根治「他线已 staged 被他线 commit 带走」） |
| 白名单来源（优先级） | ①`--allow`（可重复）②`--allow-file` ③`$STAGED_SCOPE_ALLOW`（`;`/换行分隔）④仓库根 `.session-scope.txt` |
| 匹配语义 | `*`＝全员放行（显式、审计可见）／精确路径／目录前缀（`tests/`）／glob（`docs/handoff/*.md`） |
| 判据 | 未声明白名单 ⇒ **拒**（fail-closed）；有越界 ⇒ 拒并列出越界清单；**index 为空 ⇒ 放行**（避免无内容时过度阻断） |
| 可测性 | `--index-file <清单>` 离线注入 index；`--self-test` 内置 6 例（含派单负例） |
| 挂载 | `.githooks/pre-commit`（hooksPath 已是 `.githooks`）；找不到 python / 检查脚本 ⇒ **fail-closed 拒提交**（与 pre-push「未找到 pwsh 即拒推」同纪律） |
| 退出码 | 0 通过｜1 越界或未声明（拒）｜2 环境错误 |

### 门2 `scripts/check-evidence-naming.py` 补丁

| 项 | 设计 |
|---|---|
| ① legacy 逐件内容指纹 | 原实现只 `legacy += sum(len(fs))`（**数件数**）⇒ 内容改写时 violations 仍 0（静默放过）。新增 `LEGACY_FINGERPRINTS`（`basename → {sha256, mtime, why}`，与 `KNOWN_VIOLATIONS` 同机制）＋ `verify_legacy()`：**sha256 为主判据**（mtime 仅 drift 告警，遵「mtime 不可作可复用性判据」教训）。四态：`mutated`（内容变，必报）／`unregistered`（未登记新文件）／`missing`（登记但缺失）／`mtime_drift`（仅 WARN） |
| ② 探针写路径物理隔离 | `verify_probe_isolation()`：静态扫 `docs/probe/**` 内 `.py/.sh/.ps1` 的写调用首参字面量，命中 `docs/evidence`／`legacy` ⇒ `probe-writes-archive`（归档只可由「移入」写入）。默认 WARN、`--strict` 下 FAIL |
| 接线 | legacy 违规**追加进既有 `bad` 列表** ⇒ 免费继承既有 verdict 语义（`if bad or mutated: FAIL(strict)/WARN`），零改动主流程 |
| 维护入口 | `--legacy-fingerprints <json>` 注入登记表（测试/维护）；`--probe-dir <dir>` 指定探针目录 |

## 2 ④验收读数

| # | 项 | 命令 | 读数 | 判定 |
|---|---|---|---|---|
| V1 | 门1 内置自测 | `check-staged-scope.py --self-test` | **6/6 PASS**（含 ★`add-A-with-foreign-file → FAIL` 越界 `review-kb/daily/...`） | ✅ |
| V2 | 门1 单测 | `pytest tests/test_check_staged_scope.py -q` | **6 passed** | ✅ |
| V3 | 门1 钩子 fail-closed | 无白名单跑钩子 | exit=1，打印三选一修法＋越界标记 | ✅ |
| V4 | 门2 legacy 单测 | `pytest tests/test_evidence_naming_legacy.py -q` | **6 passed**（★改写内容 ⇒ `mutated` 必报；未登记⇒`unregistered`；mtime 变⇒仅 drift） | ✅ |
| V5 | 门2 既有回归 | `check-evidence-naming.py --self-test` | **5/5 PASS**（未破坏既有判据） | ✅ |
| V6 | 门2 隔离分支 | tmp 造 `open("docs/evidence/x.json","w")` 探针 | 非 strict ⇒ `RESULT: WARN(non-strict: probe-isolation)` exit 0；`--strict` ⇒ `FAIL(strict: probe-isolation)` **exit 1** | ✅ |
| V7 | 门2 真实仓隔离扫描 | `verify_probe_isolation(docs/probe)` | **violations=0**（现役 27 件探针无写归档痕迹） | ✅ |

## 3 本轮发现（如实，三项）

| # | 发现 | 性质 | 处置 |
|---|---|---|---|
| F1 | 本机 `bash` 解析到 **WSL bash**（stderr 有 `wsl:` 提示）⇒ 手动 `bash .githooks/pre-commit` **不等于** git 用自带 sh 跑钩子的真实环境（WSL 看不到 Windows 侧 `STAGED_SCOPE_ALLOW`，故③首测报"未声明"） | **测试方法失真**，非钩子缺陷 | 改用**真实 `git commit`** 验证门（见 §4）；已在本件留痕，避免后续误判 |
| F2 | `check-evidence-naming.py docs/evidence` 报 **111 处 `name-not-conforming` 存量违规** | **既有存量债**（非本轮引入：cutoff=2026-11-08 前后既有名） | 不在本轮范围（门2 只补 legacy/隔离机制）；**已登记待治理**，见 §5 |
| F3 | 首次实现中 `index=0` 也判拒（过度阻断） | 设计瑕疵 | 已修：空 index ⇒ 放行（自测第 6 例锁死） |

## 4 门1 真实提交验证（用本笔自身）

本笔提交即门1 首次真实生效：`STAGED_SCOPE_ALLOW` 声明本笔 5 路径 → `git add` → `git commit` 触发 pre-commit。
- **期望**：通过并落库（同时证明「白名单声明」路径可用）
- **反向已单独验**：派单负例（他线文件混入 ⇒ 拒）由 V1 自测＋V2 单测双覆盖

## 5 待治理登记（不阻断本笔）

| 项 | 内容 |
|---|---|
| evidence 命名存量 | 111 处 `name-not-conforming`（`docs/evidence/**`：archify 产物子目录、push-authorization、restart-* 快照、minutes_direct_backfill_* 等）——建议：或升名、或走 `KNOWN_VIOLATIONS` 登记（冻结 mtime），由总调度裁口径；**在裁编号 R-20261111-01** |
| legacy 登记表 | `LEGACY_FINGERPRINTS` 当前为空（`docs/evidence/legacy` 目录尚不存在）；首次出现 legacy 内容时须登记，否则报 `legacy-unregistered`（design intent） |

## 6 派单判据逐条对照

| 派单要求 | 实测 |
|---|---|
| 门1 比对 index ⊆ 白名单，不符即拒 | V1/V2/V3 ✅ |
| 门1 负例：`git add -A` 带入他线文件 ⇒ 必拒 | V1 `add-A-with-foreign-file → FAIL` ✅ |
| 门1 挂 pre-commit 或 pre-push | `.githooks/pre-commit` ✅（fail-closed；本笔真实触发见 §4） |
| 门2 legacy 逐件内容指纹（sha256+mtime，与 known_violations 同机制） | V4 ✅（`LEGACY_FINGERPRINTS`＋`verify_legacy`） |
| 门2 负例：改写 legacy 内任一文件 ⇒ 必报 | V4 `test_t2_negative_content_rewrite_reported` ✅ |
| 门2 探针写路径与归档目录物理隔离 | V6/V7 ✅（`probe-writes-archive`，strict 下 FAIL） |

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-11 | 建件：两门方案＋V1–V7 读数＋三项发现（WSL bash 测试失真／111 处存量债／空 index 过度阻断已修）＋R-20261111-01 |


## 7 乙案落地（R-20261111-01 裁＝乙，2026-11-11 追加）

**裁定内容**：KNOWN_VIOLATIONS 冻结登记 + 渐进清理；与 24 件 legacy 指纹补登同批；硬期限＝命名门禁挂 pre-push 之前完成。

### 7.1 三项落地

| # | 动作 | 结果 |
|---|---|---|
| ① | **24 件 legacy 指纹补登** | 定位＝`agent_workspace/perf_eval_rperf01/legacy/`（DEFAULT_DIRS 内 legacy 子目录，非 `docs/evidence/legacy`）；逐件 sha256+mtime+why 写入 `scripts/data/legacy-fingerprints.json`（**24 件**，5,937 B）；脚本经 `_load_json_table` 加载 |
| ② | **111 件 naming 存量冻结登记** | 外置 `scripts/data/naming-known-violations.json`（**101 唯一 basename**，19 KB）；**+ 目录级冻结** `KNOWN_VIOLATION_DIRS`（14 个批量生成目录：archify-*／p1-repair-snapshot-*／restart-*／qfq_raw_admission_*／double_run／push-authorization／backtest-timer-smoke-*）补上 **10 件同名冲突**（8 个重复 basename 跨 10 个额外路径，basename 键机制无法表达「同名不同 mtime」） |
| ③ | **命名门挂 pre-push** | `.githooks/pre-push` 在 `pwsh` 探测段之前插入 `[evidence-naming]` 段（25 行）：`check-evidence-naming.py --strict`，**fail-closed**（无 python／缺脚本 ⇒ exit 9；rc≠0 ⇒ 中止推送），与铁律预算门同纪律 |

### 7.2 验收读数（乙案后）

| 范围 | 命令 | 读数 | 判定 |
|---|---|---|---|
| **pre-push 所挂范围**（`DEFAULT_DIRS`＝`agent_workspace/perf_eval_rperf01`） | `check-evidence-naming.py --strict` | `files=21 compliant=11 exempt=7 known=3 mutated=0 violations=0 legacy_frozen=24` → **RESULT: PASS** exit 0 | ✅ |
| `docs/evidence` 全量 | `check-evidence-naming.py --strict docs/evidence` | `files=111 known_violations=111 mutated=0 violations=0` → **RESULT: PASS** exit 0 | ✅ |
| 既有自测（无回退） | `--self-test` | **PASS (5/5)**（含 D-2 覆写必须 FAIL、M-1 冻结期覆写必须报） | ✅ |
| 门2 单测（无回退） | `pytest tests/test_evidence_naming_legacy.py` | **6 passed** | ✅ |

### 7.3 乙案的三条语义边界（必读，防误读为「全放行」）

1. **冻结 ≠ 免检**：登记项仍逐条打印 `KNOWN_VIOLATION`（可见性保留）；**mtime 一旦与冻结值不符 ⇒ `KNOWN_VIOLATION_MUTATED` 仍报**（根目录散件守卫未削弱，既有 D-2/M-1 自测仍红即证）。
2. **目录级冻结仅限 14 个批量生成目录**，且以**路径前缀**匹配——新增同类生成目录须显式登记，否则仍判 `name-not-conforming`（不自动蔓延）。
3. **渐进清理路径未关闭**：升名后把条目从 JSON 移除即可（下次 strict 若报则说明登记残留），故乙案是「停止日日红」而非「放弃治理」。

### 7.4 实施中的两处自纠（如实）

| # | 现象 | 归因 | 处置 |
|---|---|---|---|
| G1 | 首轮生成 JSON 后 strict 仍报 24/111 | **只生成表未接线**：脚本仍读内嵌空表 | 加 `_DATA_DIR`/`_load_json_table` 并在 `KNOWN_VIOLATIONS` 定义后 `update`、`LEGACY_FINGERPRINTS` 改为加载 |
| G2 | evidence strict 报 `10 frozen-record mutations` | **basename 键机制局限**：8 个重复 basename 跨 10 个额外路径，`KNOWN_VIOLATIONS` 只能记一份 mtime | 引入 `KNOWN_VIOLATION_DIRS` 目录级冻结（对批量生成物更贴切），10 件归零 |

### 7.5 变更记录（追加）

| 日期 | 内容 |
|---|---|
| 2026-11-11 | 乙案落地：24 legacy 指纹 + 101 外置登记 + 14 目录冻结 + pre-push 挂载；两范围 strict 双 PASS；G1/G2 自纠留痕 |
