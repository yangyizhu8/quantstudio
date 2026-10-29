# F4 巡检守卫接入说明：总运维每日哨兵（`schedule-b59df0be`）第 6 步

- **日期**：2026-10-29｜**归属**：duckdb 数据库升级执行会话（图节点⑲）｜spec 002-duckdb-1.5-upgrade · T035
- **裁定（总调度 2026-10-29）**：挂载点 = **总运维每日哨兵 `schedule-b59df0be` 第 6 步**；本件为**接入说明**（供④运维会话照 F4 onboarding 同款执行），**本会话不代为改哨兵配置**
- **守卫本体**：`scripts/check_passthrough_pk.py`（**只读、零源码改动**；新增检测型能力）

## 一、第 6 步要做的事（一句话）

在既有每日哨兵步骤序列末尾**追加一步**：跑 `python scripts/check_passthrough_pk.py --check`，
把「PK 从 ≥1 变 0」作为**新增告警项**并入当日哨兵结论。

## 二、执行细节

| 项 | 内容 |
|---|---|
| 工作目录 | **项目根**（`D:\miniQMT策略实盘\QuantStudio`）——脚本按相对路径定位主库 `data/quantstudio.db` |
| 命令 | `python scripts/check_passthrough_pk.py --check` |
| 解释器 | 用**该哨兵既有的项目解释器**（勿用系统默认 python，避免 duckdb 版本/依赖差异） |
| 退出码语义 | **0 = 通过**（无 PK 消失）；**2 = ALERT**（有表 PK 由 ≥1 变 0，**须当日上报**）；**3 = SKIP**（库被 daemon 占用，**不是失败**，按哨兵既有「可跳过项」处理，不计入失败数） |
| 基线来源 | `docs/evidence/pk_baseline.json`（段 A 产出；含各表 PK 计数基线） |
| 只读保证 | 脚本内部 `duckdb.connect(path, read_only=True)`，**不改库、不改 schema、不写文件**（`--snapshot` 会写基线文件，日常哨兵**不得**用 `--snapshot`） |

## 三、告警文案与判定

- **命中（退出码 2）**：输出形如 `ALERT: 表 '<t>' 的 PK 由 <n> 变为 0` —— 处置照
  `docs/handoff/duckdb-pt01-passthrough-runbook-20261029.md` §3（窗口内七步：停写者 → 取证 → 判定影响面 → 恢复约束或重建表 → 复验 → 复启 → 重取基线）。
- **未命中（退出码 0）**：记「PK 巡检 PASS」。
- **SKIP（退出码 3）**：记「PK 巡检 SKIP（daemon 占用，库单写者独占）」，**不得记为失败**，也不得因此中断哨兵后续步骤。

## 三之二、补丁①：哨兵第 4 步文本（供④直接替换）

> **更正**：F4 已由④于 **2026-10-29 02:11** 接入总运维每日哨兵**第 4 步**并首轮全绿（**非第 6 步**；我方此前误判「尚未接入」，特此更正）。本件使命收窄为**两处补丁**，其中补丁①的替换文本如下：

```
第 4 步 · PK 覆盖链巡检（F4 守卫）
  执行：python scripts/check_passthrough_pk.py --check
  判据：退出码 0 = PASS；2 = ALERT（当日上报，按 runbook §3 处置：docs/handoff/duckdb-pt01-passthrough-runbook-20261029.md）；3 = SKIP（库被独占，非失败，不计失败数）
  红线：日常巡检**禁用 --snapshot**（会覆盖基线 docs/evidence/pk_baseline.json）；基线更新只在停服窗内按 runbook §3.7 执行
```

## 四、为什么是第 4 步

段 A 判定（`docs/evidence/passthrough-drop-repro-20261028.md` §七）建议「紧随三判据 grep 之后」接入每日巡检；
**实际落点：总运维每日哨兵 `schedule-b59df0be` 的第 4 步**（④于 2026-10-29 02:11 完成接入 + 首轮全绿）。

## 五、边界

- 本守卫只检测「PK 由 ≥1 变 0」这一类**覆盖链事故**；它**不能**替代 `check_wal` 回归门、`config_lint` 前瞻守卫（三者互补）。
- **④实测（2026-10-30，修正本节原表述）**：`read_only` 连接可与 daemon 写连接**共存** ⇒ daemon 运行期 `--check` 可正常返回 **exit 0**（④三次实测一致）。仅在库被真正独占等极端情形才出现 SKIP/3（按非失败处理）。本说明初稿「运行期多为 SKIP」的表述**作废**。
- 哨兵配置本身**由④运维会话修改**（F4 onboarding 同款流程），本会话只提供本说明与守卫脚本。

## 六、附：可直接复制的执行片段

```bash
cd /d/miniQMT策略实盘/QuantStudio   # Git Bash；PowerShell 用 cd 'D:\miniQMT策略实盘\QuantStudio'
python scripts/check_passthrough_pk.py --check
rc=$?
case "$rc" in
  0) echo "PK 巡检 PASS" ;;
  2) echo "PK 巡检 ALERT —— 照 runbook §3 处置并当日上报" ;;
  3) echo "PK 巡检 SKIP（daemon 占用，非失败）" ;;
  *) echo "PK 巡检异常 rc=$rc" ;;
esac
```
