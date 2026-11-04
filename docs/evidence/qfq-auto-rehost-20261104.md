# D5-e 自动换代自愈（客户零操作）· 2026-11-04

## 为什么要做

客户机（macOS）上 `qfq_source_cutover` 的 active 记录 `aux_db_path` 是
`D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db` —— **我方开发机（Windows）的绝对路径**。
该记录由我方 2026-08-07 做 B-5 正式 cutover（`cutover_id=b6_formal_20260807_v2`）时写入，
并随库/配置流到客户机 ⇒ **属我方缺陷**。
在客户机上该路径既非本机绝对路径也不存在 ⇒ 路由解析把它当相对路径拼成 `data/D:\...`
⇒ `stock_daily` / `etf_daily` / `stock_minutes` **取数前崩溃**，数据一行没拉。

⇒ 处置原则：**能我方修的就不让客户动手**。程序升级后**自动换代**，客户只需更新并重启。

## 实现（D5-e）

| 位置 | 内容 |
|---|---|
| `quantstudio/pipeline/qfq_cutover_tools.py` | 新增 `ensure_local_cutover(conn, *, config_dir, audit_path, operator, dry_run)`：**换代**（插新记录 → planned→prepared→baseline_building→baseline_validated → activate，旧记录自动 superseded） |
| `quantstudio/pipeline/daemon.py` | `ResidentCollector.from_configs` 启动即调用（once 与 forever 两条链都经过），失败**不阻断启动** |

**只在两个条件同时成立时动手**（宁可不改，也不猜）：
① 记录里的路径确属**异平台绝对路径**（或本机不存在）；
② 本机 `qfq_aux_paths.json` 为该世代提供了**存在**的候选。
否则只报告 `needs_manual`（例如辅助库真缺失）交人工。

**开关**：`QS_QFQ_AUTO_REHOST=0` 关闭（默认开启）。**幂等**：已是本机路径则跳过。

## 验证读数

| 项 | 读数 |
|---|---|
| 单测 | `tests/test_qfq_auto_rehost.py` **5 passed**（换代成功 / 幂等 / 无候选不动 / env 开关 / dry-run 不写） |
| 端到端 | 模拟客户机坏记录 → `repaired=['b6_formal_20260807_v2']`；旧记录 `superseded`、新记录 `active`、`aux_db_path`=本机路径；**路由解析随即取到存在的本机 aux 库**；审计行落盘（`action=auto-rehost`，含旧新值与授权依据字段） |
| 夹具教训 | 首轮用例失败：写死 Windows 路径在 **Windows 开发机上本机就存在** ⇒ 被判"本机可用"而跳过 ⇒ 夹具须按宿主取"真正异平台"路径（D5-a 测试同一手法） |

## 客户侧动作（更新后）

1. `git pull`（拿更新版）；
2. 重启 GUI / daemon（启动即自愈，日志出现 `[qfq] 检测到迁移遗留的异平台路由记录，已自动换代到本机路径（客户无需操作）`）；
3. 三张表恢复拉取；**无需**手工改配置、无需禁用任务、无需跑诊断命令。

回滚：`QS_QFQ_AUTO_REHOST=0` 关闭自愈；已换代记录保留（旧记录为 superseded，可回看）。
