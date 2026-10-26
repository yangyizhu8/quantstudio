# ops 族候选条目（2 条草案）—— 仅候选，不落 knowledge/、不建族

> **性质**：KMAP R4 沉淀判断的 C 路径产物（总调度 2026-10-26 裁定 C）。
> **边界**：本件**不落 `knowledge/`**、**不建 ops 族**、**不改 `kb-info.yaml`**、不污染 qd；素材证据来自
> `docs/handoff/dispatch-graph.md`（图态·总调度维护）与 `docs/design-daemon-heartbeat-watchdog-20261026.md`，
> 细节标〔待运维1/dev 线确认〕者未经我方独立取证，落正式条前须对应线签字确认。
> **立族触发条件**：**第 3 条同域（运维探针族）教训出现时**立 `ops` 族（`knowledge/ops/OPS-*.md`），
> 枚举建议 `daemon_lifecycle / observability / alerting`（届时入 `kb-info.yaml` families 段并配 README）。
> 依据既有裁定：「单条目不足立族，≥3 条再评估」（2026-10-26 QD-0011 案，dispatch-graph L120）。

---

## 候选 1（拟 OPS-2026-0001）：daemon 静默死亡五例族（W-2）—— observability

| 字段 | 草案内容 |
|---|---|
| **symptom** | daemon 进程无任何输出消失；`status.json` 停留 `running`（陈旧误导）；日志无 traceback、无 stop.request；WAL/锁残留（第四死：WAL 1.004GB＝红线 4 倍、write_lock pid 残留、run_state 冻结）。首例 19.3h 无告警才发现 |
| **root_cause** | **死亡瞬间不可观测**——无栈、无 stderr、无 exit code（进程死亡时三无 ⇒ 事后无法归因）。已排除项（归因九假设）：宿主重启（10-25 案）、WinUpdate 窗、四看门狗、日志轮转、Python 崩溃记录；OOM/资源压力为**权重最高候选但未定因果**（5 死中 3 例在采集/写库期；free 735MB 为事实不作成因） |
| **fix_target** | 三件套（W-2②，已落地）：①stderr/stdout 落盘重定向（`daemon_stderr.log`，零代码提前实现）②`PYTHONFAULTHANDLER=1` env ③启动 wrapper 记 ExitCode（`exitcode.log`，区分异常退出 vs 外部终止）；配套：外部心跳巡检（W-2①，10min 轮询，不自动拉起）＋ status.json 陈旧自愈（新 lifecycle 清理陈旧状态文件） |
| **evidence** | dispatch-graph L115–L121：五死时间线（10-22 01:09 / 10-25 宿主重启 / 10-26 01:01 / 09:55 轮中亡 / 11:38）；前三案锚 ~01:00 带；第四死 stderr 落盘 4.7KB（强化拉起后）；巡检 3 分钟发现（09:55:23 死→09:58:33 FAIL，优于 10min 设计值）〔待运维1 线确认 stderr 4.7KB 内容归属〕 |
| **fixture** | `kill -9 <daemon_pid>` 模拟静默死 → 断言：①三件套全部落盘（stderr 有内容/exitcode 非 0）②巡检 ≤10min 判 FAIL ③status.json 被陈旧自愈清理〔脚本化归运维1 线，候选阶段仅设计〕 |
| **trigger**（两段式） | daemon 进程自己没了，日志里什么都没有，status 还显示 running，怎么查？；问法：daemon 悄悄死了十几小时没人发现，怎么让死亡瞬间留下证据 |

**处置链五步**（可回哺，dispatch-graph L115）：清锁（write_lock 残留＋陈旧 daemon.lock 备份留证后删）→ `check_wal` 收敛 WAL → **强化形态拉起**（三件套齐）→ 悬挂轮回收核验（水位/intent 未动逐字吻合）→ 占用声明入群践约。

---

## 候选 2（拟 OPS-2026-0002）：告警通道缺口（R-3）—— alerting

| 字段 | 草案内容 |
|---|---|
| **symptom** | 巡检已判 FAIL（LastTaskResult=3 真实失败），但**告警仅写入日志文件，无人即时知**——监控生效＝判对，通知失效＝没人来 |
| **root_cause** | 告警路径只有"落日志"一环，缺**推送通道**（无人盯日志 ⇒ FAIL 后依旧静默 19h 同族）；且无**去重**设计（持续 FAIL 会刷屏导致后来被无视的风险） |
| **fix_target** | 推送通道（飞书等 IM）＋ **30min 去重窗口**（并入 W-2② R-3 型推送）；配套判据：巡检 FAIL ⇒ 推送必达（双节点终证：06:00 巡检 OK＋07:00 看门狗首触 OK，飞书未推＋两 state 清空＝R-7 watchdog 体系闭环完成的对照面） |
| **evidence** | dispatch-graph L115「通道缺口确认（告警仅落日志无人即时知→并入 W-2② R-3 型推送+30min 去重）」；L116「告警仅落日志无推送的通道缺口并入 W-2②」〔推送通道落地时间与形态待 dev 线确认〕 |
| **fixture** | 注入巡检 FAIL 判据 → 断言：①推送在 N 分钟内到达 ②同一持续 FAIL 在 30min 窗口内不重复推送 ③恢复后首条 OK 有通知（或按设计静默，待定） |
| **trigger**（两段式） | 巡检明明报 FAIL 了为什么没有人第一时间知道？；问法：告警只写在日志里没人看，等于没有告警，怎么补推送 |

---

## 立族触发与迁移方案（写死）

1. **触发**：第 3 条同域教训（daemon 生命周期/可观测性/告警任一面）经 KB 侧成条判断通过 ⇒ 立 `ops` 族；
2. **立族动作**（一次性小批）：`knowledge/ops/` 目录＋README（schema 语义）＋`kb-info.yaml` families 段增 `ops`（`id_pattern: OPS-\d{4}-\d{4}`；`fix_layer_enum: [daemon_lifecycle, observability, alerting]`；`required_fields` 与 qd/pd 同六字段）＋ 本件 2 条草案转正式条目（经运维1/dev 线确认标注项）＋ `kb_hook` 体检过；
3. **不回溯**：候选期间不把本 2 条计入任何 R/B/H 观察窗分母（11-23 读数如实标「ops 候选 2 条未入族」）。

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-10-26 | 建件（裁定 C）：2 条草案（observability/alerting）＋处置链五步＋立族触发与迁移方案；未落 knowledge/、未建族、未动 kb-info.yaml |
