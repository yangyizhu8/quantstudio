# 窗口实施卡：duckdb 1.5.6 首跳升级（2026-11-07 周六凌晨 · ≤60 分钟）

- **日期**：2026-10-30｜**执行方**：duckdb 数据库升级执行会话（节点⑲）｜**联办方**：主仓 dev 会话（节点⑤）｜**依据**：dev 回执 `docs/handoff/duckdb-window-dev-receipt-20261030.md`（四项全确认 ＋ 附加三条件）
- **窗口日**：**2026-11-07（周六，首选，dev 线已确认）**；**顺延位 2026-11-08（周日）**｜**硬约束**：总时长 **≤60 分钟**（SC-003）；**硬上限**：1.4 线 EOL **2026-11-17**
- **分工（dev 回执①④）**：**daemon 停机由 dev 线执行并随窗值守**；**W8 拉起后 dev 线复核采集链恢复**（首轮 daemon 采集读数 + 巡检 `LastTaskResult` + 新 stderr 分文件续写）；**本方（升级线）复核门禁与库状态**（双版本门禁 + 库尺寸/WAL 读数）；双方读数各留一份供 T048 归档。

## 零、T−1 日（2026-11-06）开窗前置清单（任一不满足 ⇒ 不开窗）—— 2026-10-30 21:1x 现况：**P1✓ P2✓ P3✓ P6✓ P7✓**，余 **P4（窗前 2h 占用声明）/ P5（T−30 回退点）**为窗前时点动作

| # | 前置 | 判据 | 当前实况（2026-10-30 17:00） |
|---|---|---|---|
| P1 | dev 线书面回执到件 | 四项全确认 | ✅ 已到件（`duckdb-window-dev-receipt-20261030.md`） |
| P2 | #12 出仓（我方 4 笔） | 远端含 `82c174d` | ✅ **已闭合**（2026-10-30 17:40 实测：我方 4 笔 `fdca384`/`8149565`/`53a6afd`/`82c174d` **全部在远端**，远端 HEAD `ce637d8`） |
| P3 | ⑩副本同步门 | 副本含 `82c174d` **且**工作树干净 | ✅ **已闭合（2026-10-30 晚⑩批门回执，总调度 21:1x 转达）**；经过实录：20:29 总调度口径更正（我方此前误报「同步已完成」实为 fetch 层读数）→ ⑩晚批完成门全序（merge + check-drift + ci-smoke）→ 闭合。副本侧另证：工作副本 HEAD 含 `82c174d`（merge-base 实证）+ 三件（脚本/日历/接入说明）在位 + **副本提醒件 mtime 20:31:37**（副本环境真实执行物证，执行者/退出码待④回执）。副本工作树余 4 项他线在途由⑩牵头各线清理，不阻塞窗口动作 |
| P4 | 铁律 19 占用声明已发 | 时段 + 操作类型 + 预期恢复时点 + 影响面 + 回退点 | ⏳ 窗口前 2 小时发（dev 线抄送） |
| P5 | 回退点已建 | `git stash create` + `stash store` 持久化 + hash 记入窗口记录 | ⏳ 窗口 T−30 分钟 |
| P6 | 冒烟脚本就绪 | S1–S6 脚本在副本可执行 | ✅ 随 P2/P3 同步完成（副本已含 `82c174d` 的证据与脚本件） |
| P7 | PT-01a 已实施且回归通过 | `writers.py` F2 + `config_lint.py` 守卫 + 回归门全绿 | ✅ 已实施（`fdca384`）；回归门 **PASS=13 FAIL=0** |

## 一、时间轴（≤60 分钟，逐分钟预算）

| 步 | 相对时刻 | 动作 | 命令 / 动作 | 判据 | 读数落点 | 失败即 |
|---|---|---|---|---|---|---|
| **W0** | −2 min | **发占用声明**（铁律 19） | 抄送 dev 线 | 声明含时段/操作类型/恢复时点/影响面/回退点 | 窗口记录 §声明 | 不开窗 |
| **W1** | 0 min | 关 GUI | 人工 | 进程列表无 GUI | 窗口记录 | 继续 |
| **W2** | +2 min | **停 daemon（精确 PID）** | `Stop-Process -Id <PID>`（**当前 32032**；**排除自身 `$PID`**；**不依赖 `stop.request`**） | `data/daemon_status.json` 的 pid 不再存在；进程列表无 `daemon`（**双形态正则**：`-m quantstudio.pipeline.daemon` 与 `…\pipeline\daemon.py`） | 窗口记录 §W2 | 回滚 |
| **W3** | +8 min | 记录基线 | 库尺寸（GB）、WAL 探测（**探测优先，不以阈值为唯一判据**）、t0 | 库尺寸读数（此前 **≈55.3 GB**）；WAL 预期**无文件** | `docs/evidence/duckdb-1.5-upgrade-window-evidence.md` | — |
| **W4** | +15 min | 装包 | `pip install "duckdb==1.5.6"`（**显式版本命令**；**禁 `pip install -U duckdb`**） | `python -c "import duckdb;print(duckdb.__version__)"` ⇒ `1.5.6` | 窗口记录 §W4 | 回滚 |
| **W5** | +20 min | 门禁自检 | `python -m quantstudio.pipeline.duckdb_version_gate --check` | **`PASS 1.5.6`**，退出码 **0** | 窗口记录 §W5 | 回滚 |
| **W6** | +25 min | **S1–S6 冒烟** | `python docs/evidence/smoke_s1_s5.py` ＋ S6（脚本以 quickstart 为准） | **6/6 PASS**；写入只落**独立临时库**，主库仅只读点查与文件头读数；S5 storage_version ≤ 68 | 窗口记录 §W6 | 回滚 |
| **W7** | +40 min | E7 升级后基线 | 按 quickstart | 与升级前基线成对比对 | 窗口记录 §W7 | 回滚 |
| **W8** | +45 min | **拉起 daemon** | **`schtasks /Run /TN QS_DaemonForever`**（**唯一路径**；**禁 `Start-Process`**） | `data/daemon_status.json` 的 `started_at` 更新且 cmdline 为 `-m …daemon` | 窗口记录 §W8 | 回滚 |
| **W9** | +55 min | 收尾读数 | t1（窗口时长）、WAL 体积、daemon 首轮耗时 | **t1 ≤ 60 分钟** | 窗口记录 §W9 | — |
| **W10** | +60 min | **拆占用声明** ＋ 双方复核 | dev 线复核采集链；本方复核门禁 + 库尺寸/WAL | 双方读数各留一份 | 窗口记录 §W10 | — |

## 二、dev 线附加三条件（**硬约束**，已嵌入上表）

| # | 条件 | 嵌入位置 |
|---|---|---|
| **C1** | **W2 走精确 PID**（当前 32032），先核归属并**排除自身 `$PID`**；**不依赖 `stop.request` 优雅停**（已知缺陷：stop 协议检查点仅在任务边界，**空闲期无检查点** ⇒ 实测 stop.request 排队 **3.5 小时未被消费**，2026-10-29 10:19–14:47 ⇒ 空闲期必走超时强杀；已按铁律 08 立案） | W2 行 |
| **C2** | **拉起唯一路径 = `schtasks /Run /TN QS_DaemonForever`**（禁 `Start-Process` —— 不受 IgnoreNew 单实例保护）；任务定义已注册（S4U + LeastPrivilege + **Boot 触发**，Daily 已移除） | W8 行 |
| **C3** | **WAL 守既有纪律**（禁手工删 `.wal`；>256MB 走 `check_wal.ps1 -Force`）；⚠️ **`check_wal.ps1` 的 GBK 解析缺陷已由本会话修复**（2026-10-30：原文件**无 UTF-8 BOM** ⇒ 本机 PS 5.1 按 GBK 读 ⇒ 整文件 `ParserError`；已加 BOM，实测 PS 5.1 默认读 **`PARSE_OK`**，WAL 回归门 **PASS=13 FAIL=0**）⇒ 窗内可用；**主库 WAL 预期无文件**（DuckDB 每次提交自动 checkpoint），**以探测值为准** | W3 行 + 回滚路径 |

## 三、回滚触发与动作（T024 · 失败即执行，不等裁决）

| 触发 | 动作 | 复核 |
|---|---|---|
| W5 门禁非 `PASS 1.5.6` | `pip install "duckdb==1.4.5"` → 门禁复核 `PASS 1.4.5` → 记录 → **停止窗口** | 门禁 |
| W6 冒烟任一 FAIL | 同上（先恢复可运行态，再分析） | 门禁 + 库可开 |
| 库异常（WAL 残留 / 无法收敛） | `check_wal.ps1 -Force`（**禁手工删 `.wal`**；禁运行期 CHECKPOINT） | 开库 + 抽样读 |
| W8 拉起失败 | 降版 1.4.5 → `schtasks /Run` 拉起 1.4.5 版 → 记录 | 门禁 + 采集 |
| **窗口超时（>60 分钟）** | **无条件回滚并恢复 daemon** | 门禁 + 采集 |

**回滚后必做**：读数入 evidence ＋ 窗口报告标注「已回滚 + 原因」＋ 向总调度与 dev 线**双线回报**。

## 四、窗内禁行清单

- 禁一切会 `create_writer` / `init_schema` 的入口（`--mode once`、GUI 跑任务、离线回填、采集回写）—— **dev 线已承诺冻结**；
- 禁 `pip install -U duckdb`（会装到未放行版本）；
- 禁 `Start-Process` 拉起 daemon（唯一路径 = `schtasks`）；
- 禁手工删除 `.wal`；禁运行期 CHECKPOINT；
- 禁在窗口内改表结构 / 加列（铁律 24：加列＝契约变更，需六件套同 commit）；
- 禁用宽关键词做进程筛选（须精确 PID，且排除自身 `$PID`）。

## 五、双方读数归档（T048 输入）

| 方 | 读数 |
|---|---|
| **dev 线** | W8 后首轮 daemon 采集读数 + 巡检 `LastTaskResult` + 新 stderr 分文件续写 |
| **本方** | W3/W5/W6/W7/W9 全部读数（库尺寸、WAL、门禁双版本、S1–S6、E7 前后、t0/t1、daemon 首轮耗时） |

双方读数**各留一份**，同入 `docs/evidence/duckdb-1.5-upgrade-window-evidence.md`。

## 六、关联

| 用途 | 路径 |
|---|---|
| dev 回执（四项确认 + 三条件） | `docs/handoff/duckdb-window-dev-receipt-20261030.md` |
| 我方对接件（申请 + 回执格式） | `docs/handoff/duckdb-window-dev-coordination-20261030.md` |
| 窗口步骤与回滚（quickstart） | `specs/002-duckdb-1.5-upgrade/quickstart.md` |
| 窗前证据（当前进度） | `docs/evidence/duckdb-1.5-upgrade-window-evidence.md` |
| 窗口设计（六步①） | `docs/duckdb-1.5-upgrade-design.md` |
| 任务状态 | `specs/002-duckdb-1.5-upgrade/tasks.md`（T042–T048、T057、T058） |
