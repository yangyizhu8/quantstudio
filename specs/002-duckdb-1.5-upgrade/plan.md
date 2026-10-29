# Implementation Plan: duckdb 首跳升级（1.4.5 → 1.5.6）＋ PT-01 覆盖链 PK 保护

**Date**: 2026-10-29 | **Spec**: ./spec.md | **Design（六步①）**: `docs/duckdb-1.5-upgrade-design.md`

## Summary

把「声明面 3 处 + 门禁面 3 处 + 运行环境」统一到**同一个放行集合判定源**（`{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`），并在单次 ≤60 分钟窗口内把运行环境从 1.4.5 切到 1.5.6——不重建库、不转格式、不改 DDL；同批落治理条文三要素、缺陷说明更正、冒烟与回滚预案、客户自检。并行轨修 PT-01：覆盖链改为**保留/重建目标表主键**，并加一条前瞻静态守卫（passthrough 不得指向带 PK 表）。已实测的批量写劣化（A-14：1.5.6 相对 1.4.5 慢 4–6 倍）不在本期修，登记风险项 + 通知提示 + 单独立项。

## Technical Context

**Language/Version**: Python 3.11.9（运行主库的解释器：`Python311`）+ 3.12.10（在册第二解释器）；**duckdb 1.4.5 → 1.5.6**
**Primary Dependencies**: duckdb（唯一变更项）；pandas / pyarrow / PyQt6 不变
**Storage**: DuckDB 单文件主库（现 **55.32 GB**，mtime 2026-10-29 16:16:35 复测；此前快照 53.30 GB，WAL 缺失=0）；存储版本上限约束 ≤ 68（S5 判据）
**Testing**: pytest（`tests/test_duckdb_version_gate.py` 契约扩展）＋ `bash scripts/check_wal_regression.sh`（铁律 23 回归门）＋ 合成分片库冒烟 S1–S6
**Target Platform**: Windows 主运行面（PowerShell 5.1/7 + Git Bash）；`check_wal.sh` 兼顾 macOS/Linux
**Project Type**: 单仓 Python 应用（GUI + 常驻采集 daemon + 数据管线 + MCP 适配）
**Performance Goals**: 维护窗口 ≤ 60 分钟；批量写路径「≤2× 且 ≤5s」——**A-14 实测 FAIL**（见风险 R-3），故本期只登记不改码
**Constraints**: 不重建库 / 不转格式 / 不落 storage 69 / 不改表结构 / 窗口内禁一切会 `create_writer` 的入口 / 门禁三入口对等
**Scale/Scope**: 6 个门禁与声明落点 + 2 处脚本 + 1 契约文件 + 2 处 PT-01 分支 + 治理条文 1 处 + 知识纪律 2 处（只出 diff）

## Constitution Check

对照 `.specify/memory/constitution.md`（2026-10-29 版）逐条：

| 条文 | 本次是否触碰 | 结论 |
|---|---|---|
| 一、改动类型须显式声明 | 是 | **已声明**：①升级与门禁集合化 = **新增检测型**（判定口径变化，verdict 可被新版本改变）；②PT-01a = **纯恢复型**（回到「覆盖后 PK 仍在」的原态）；③A-14 = **新增检测型**（新增观测能力，不改行为） |
| 二、不得改变引擎可观察结果 | 否 | 不回测/取数/PIT/复权/撮合；`writers.py` 改动仅约束保持，不改 passthrough 语义（FR-022 双写哈希验证） |
| 三、逐位一致验收 | 是 | PT-01 改道前后同 df 双写逐位一致（列集/行数/内容哈希） |
| 四、可回滚 | 是 | 降版命令 + 单文件可回退（B-01′ 仅 `writers.py`）＋ 窗口前 stash 回退点 |
| 五、危险操作前回退点与占用声明 | 是 | 停/启 daemon 前按铁律 19 声明；窗口前 `git stash create` + `stash store` |
| 附加：运行环境「1.4.x 钉版，**不经手升级 duckdb 版本**」 | **冲突 → 已消解** | 该句与本次授权直接冲突 ⇒ **条文替换**（spec FR-011，见 Complexity Tracking ①）；**替换稿已落码**（commit `76106cf`，2026-10-30，②审通过后）⇒ D-1 关闭 |
| 附加：DDL 视为契约变更 | 否 | 本次无 DDL、无加列（铁律 24 仅窗口纪律适用） |
| 开发流程：六步 + SDD | 是 | ①`docs/duckdb-1.5-upgrade-design.md` → ②独立审核（M-2 总调度 11-02～11-03）→ ③实施不推 → ④证据落 `docs/evidence/` → ⑤用户确认 → ⑥双仓推送核对 |

**GATE 结论：通过**（唯一违反项 = 条文冲突，已由评估链 v9.1 + 六裁授权，并作为本期交付物 A-04 显式替换，**非默认豁免**；替换稿已落码 commit `76106cf` ⇒ 本条不再处于违反态）。

## Project Structure

### 本功能制品（`specs/002-duckdb-1.5-upgrade/`）

```
spec.md                     # 已完成（6 US / 26 FR / 8 SC / 9 Edge）
plan.md                     # 本文件
research.md                 # 技术决策（R-01…R-09）
data-model.md               # 判定/约束/基线实体
contracts/version-gate-cli.md   # 单一判定源的 CLI 契约（对外）
quickstart.md               # 窗口执行 + 回滚 + 客户自检
checklists/requirements.md  # 需求质量清单（自检已填，评审项留空）
tasks.md                    # 执行清单（T001–T057）
```

### 源码与脚本落点（真实路径）

```
quantstudio/pipeline/duckdb_version_policy.py    # 【新增】唯一判定源（无包内依赖，可被 ps1/sh 单文件直调）
quantstudio/pipeline/duckdb_version_gate.py      # 薄封装：取版本 → 调策略 → 放行/拒启（保留 SystemExit(3) 与逃生阀）
quantstudio/pipeline/writers.py                 # 仅 passthrough 两分支（1575-1628 / 1672-1762）
quantstudio/pipeline/config_lint.py             # 新增规则：passthrough 不得指向带 PK 表（_lint_one_task）
tests/test_duckdb_version_gate.py               # 契约：9 契约点 + 5 类判据 + 形态分层 9 例（引用同一判定源）
check_wal.ps1                                   # 版本判定改为调用 CLI，删除本地正则
check_wal.sh                                    # 同上（保留 ${VAR} 花括号纪律）
scripts/check_wal_regression.sh                 # 同步（铁律 23 回归门）
requirements.txt                                # :1 pin、:2 注释更正
pyproject.toml                                  # :20 / :50 pin
.specify/memory/constitution.md                 # :38 条文替换（三要素）
AGENTS.md / knowledge/iron-rules/23-*.md        # 旧口径同步（只出 diff，经批准后落码）
docs/duckdb-1.5-upgrade-design.md               # 六步①书面方案
docs/evidence/*                                  # A-14 读数、S1–S6、回滚演练、E7 前后
```

**Structure Decision**：单一内核 + 双薄壳沿用现状——判定逻辑只在 `duckdb_version_policy.py` 一处（`duckdb_version_gate.py` 为**薄封装**：取版本→调策略→放行/拒启），`.ps1` / `.sh` / pytest 全部作为**薄壳调用**该内核，不做第二份规则实现。

## Phase 划分（对应 tasks）

| 阶段 | 内容 | 交付判据 | 窗口 |
|---|---|---|---|
| **A（P1）判定源与落点** | **判定源下沉单文件** `duckdb_version_policy.py`（无包内依赖，ps1/sh 单文件直调）+ `gate.py` 薄封装；`requirements.txt` / `pyproject.toml` 改**单版本精确锁定 `duckdb==1.5.6` + 注释承载集合**；两脚本改调用；契约 9 点；**全仓调用点兼容核查**（实施期发现：纯 `-m` 形态会令回归门 T2–T9 连锁失败，故改单文件直调） | 三判定面一致率 100% + 声明面 pin 落于集合内；`check_wal_regression.sh` 全绿（实测 13/13）；兼容核查表落证据 | 11-05 |
| **B（P2）治理与说明** | constitution:38 三要素替换；`requirements.txt:2` 更正；`AGENTS.md`/iron-rules 只出 diff | 三要素可定位；全仓 grep「必须 1.4.x」零命中；diff 呈批 | 11-06 |
| **C（P2）验证与回滚** | S1–S6 冒烟（含三入口正反向）；**S1–S3 写入落独立临时库/独立文件、S4 主库只读、S5 文件头只读**；回滚预案演练 | 6/6 PASS；回滚成功且旧版可读现库 | 11-08 窗内 |
| **D（并行轨）PT-01** | **F2 = `TRUNCATE` + `INSERT`（事务包覆）为基线**（2026-10-29 实验已验：PK 1→1、`ON CONFLICT` 可用、两版本无 FATAL）；F1（重建同构 PK）留作回退；**列集一致为前提**（不一致走 DDL 路径）；lint 前瞻守卫；F4 接入哨兵（**已由④于 2026-10-29 02:11 先行完成**：哨兵第 4 步 + 首轮全绿） | 覆盖后 PK 1→1；双写哈希一致；lint 负向必拦、现状零告警 | 11-07（lint）/ 11-08（F2） |
| **E（收尾）** | 观测六项 + 首周哨兵关键词；客户通知稿；daemon 以 `schtasks /Run` 拉起；E7 前后基线 | 读数齐全注来源；通知过闸门；daemon 形态与计划任务一致 | 11-08 窗末 |

## Complexity Tracking

| 违反/复杂度 | 为何需要 | 为何拒绝更简替代 |
|---|---|---|
| ① constitution「不经手升级 duckdb 版本」被**替换** | 条文与 v9.1 授权直接冲突；不改则本期交付物从第一天起就违规 | 「加一条例外」会让条文自相矛盾，且下次 EOL 仍无判据与复核装置 |
| ② 引入**单一判定源 + 双等价入口**（单文件直调 + `-m` 转发） | `.ps1`/`.sh` 无法 import Python；不引入 CLI 则必然三处各写正则（历史事故成因） | 「三处同步维护」是人工纪律，无法机检；一致性只能靠人记住。**实施期形态调整**：纯 `-m` 形态会令 WAL 回归门 T2–T9 连锁失败（脚本被复制到临时目录 ⇒ 项目包不可导入），故判定源下沉为**无包内依赖的单文件**，两入口共用同一实现 |
| ③ F2（`TRUNCATE+INSERT`）**已验采用**，F1 留作回退 | 2026-10-29 实验（`docs/evidence/pt01-f2-truncate-20261029.md`）：`TRUNCATE` 两版本 PK 1→1、`ON CONFLICT` 可用、无 FATAL/invalidated、耗时与现状同量级 ⇒ 无需承担「取约束 + 重建 PK」的额外机制 | 初稿曾以「`TRUNCATE` 与事故语形同族」为由拒绝 F2——该担忧已被读数否定；保留 F1 用于列集变更或 `TRUNCATE` 不可用的场景 |
| ④ A-14 超阈但本期不改写入方式 | 改 `index_constituents_meta.py` 的写入形态属**纯性能型新范围**，不在本授权内 | 擅自扩面违反零越权；且该路径为辅助表刷新，3.9s（百量级）不影响正确性 |

## 风险登记

- **R-1 窗口内他线写者**：GUI 任务 / `--mode once` / 离线回填工具任一启动即破窗口纪律 → 窗口前 grep 进程 + 停 daemon + 群内占用声明。
- **R-2 计划任务形态**：`QS_DaemonForever` 为 Boot+手动 Run+IgnoreNew，窗口末必须 `schtasks /Run`（禁 Start-Process）；任务定义若携带 `QS_DUCKDB_VERSION_GATE=0` 须先纠正。
- **R-3 批量写劣化（已实测 FAIL）**：A-14 中位 300 行 6.14×、1000 行 4.04×、5000 行超时 → 升级风险项，通知提示；修复单独立项。
- **R-4 覆盖链改动波及面**：`writers.py` 两分支 + 辅助方法（改道后 +167/−12，改前两分支 145 行），须限在 passthrough 语义内；`write_passthrough_chunked` 唯一调用方为离线回填工具，改动仍需双写验证。**新增三条约束（F2 实验得出）**：①`TRUNCATE+INSERT` 不能加列 ⇒ 列集不一致必须走 DDL 路径（`ALTER ADD COLUMN` 或回退 `DROP+RENAME`）；②`TRUNCATE` 与 `INSERT` 必须同一事务，否则中途失败留空表；③chunked 分支的 staging + ledger 语义须保留。另登记文档缺陷：`:1573-1581` docstring 声称 `CREATE OR REPLACE`、实为 `DROP+RENAME`（`:1618-1619`），实施期一并更正。
- **R-5 混版并存**：`trading-battle-back\venv_miniQMT` 仍为 1.5.3（不升，登记只读消费者），若其开启写者入口将被门禁拒绝——需在通知与环境地图追加节里写明。
- **R-6 窗口撞交易日链**：11 月已入实盘准备期，daemon 停服须避交易日盘前链 ⇒ 窗口钉 **11-07 / 11-08 周末凌晨**，并**与 dev 线联办**（协调回执附证据）+ 按铁律 19 声明占用窗；未取得 dev 线确认前不得开窗。
