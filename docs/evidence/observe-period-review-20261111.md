# ④ 守护链 observe 期满材料（2026-11-11 20:35）

> 触发：节点⑳ 定时提醒（`schedule-e41dfaae`），总调度 2026-11-10 授权「④ 已实施 observe 期，期满出切 active 申请」
> 范围：**只读汇总，不改配置、不切 active**（切换动作需总调度/④ 线执行）
> T0 = 2026-11-10 10:48:33，观察期 24h

---

## 零、结论

**✅ 观察期「零误判」成立 ⇒ 具备切 active 的判据条件。**

**⚠️ 但切 active 存在两个硬前提（详见第五节）：**
1. **F8 任务未注册（归节点1）** —— 实施文档 L71 明示：「在节点1 完成前，即使 `active` 也无法拉起」。
2. **occupancy 窗口语义必须先收紧** —— 观察期已实测到占用闸**误挡 2 次真实复活**。

---

## 一、观察期统计

| 项 | 值 |
|---|---|
| 总检查次数 | **200** |
| verdict=OK | **160** |
| verdict=FAIL | **37** |
| 其他 verdict | 3 |
| 实际跨度 | 11-10 10:58 → 11-11 20:28（≈23.5h）|
| `last_revive_at` | **null**（observe 模式从未真拉起）|
| `last_action` | NONE |
| `consecutive`（期末）| 0 |

## 二、FAIL 段按 gate 分布

| gate | 次数 | 首 | 末 | 含义 |
|---|---|---|---|---|
| **all-pass** | **29** | 11-11 01:08 | 11-11 05:48 | **全闸通过，本应拉起**（observe 只记不拉）|
| skip-threshold(1/3) | 3 | 11-10 20:58 | 11-11 00:48 | 未达 N=3 阈值 |
| skip-threshold(2/3) | 3 | 11-10 21:08 | 11-11 00:58 | 未达阈值 |
| **skip-occupancy** | **2** | 11-10 22:18 | 11-10 22:28 | **占用闸挡住**（见第四节）|

## 三、FAIL 连续段 = 真死窗口（3 段）

| 段 | 时间 | 连续次数 | gate | 对应真实事件 |
|---|---|---|---|---|
| **段1** | 11-10 20:58:34 → 21:08:38 | 2 | skip-threshold(1/3) | **第三死**：PID 49060 于 20:49 死 |
| **段2** | 11-10 21:58:34 → 22:28:37 | 4 | skip-threshold(1/3) | **第四死**：PID 25852 于 21:58 死（21:17 我手动起后再次死）|
| **段3** | 11-11 00:48:34 → 05:48:33 | **31** | skip-threshold(1/3) | **第五死**：00:41 死，持续 5 小时 7 分无人救 |

**⇒ 观察期内 daemon 共死 3 次**（第三、四、五死）；段3 连续 31 次全闸通过却未拉起 ⇒ **observe 模式的「只记不拉」直接导致 5 小时空窗**。

## 四、占用闸命中记录（2 次，均为我方造成）

```
2026-11-10 22:18:34 verdict=FAIL consecutive=3 gate=skip-occupancy action=NONE target_pid=- |
  占用窗内（owner=会话节点⑳（水位线和全部执行增量拉取修复执行会话）；
  2026-11-10T21:17:45+08:00 ~ 2026-11-11T07:17:45+08:00）

2026-11-10 22:28:37 verdict=FAIL consecutive=4 gate=skip-occupancy action=NONE target_pid=- | （同上）
```

**⇒ 根因**：我在 21:17 手动恢复 daemon 时，按「预期它要跑到明早」写了 **10 小时** occupancy 窗口，
**把复活闸焊死**，致第五死的头两次检测被跳过。

**⇒ 教训已入 project 记忆 `332778ca`**：
「occupancy 是**瞬时锁（类 mutex）**，不是「daemon 在位」的状态标记；
只写 ≤5 分钟，起完即 release；写前必须查目标机制有无 `skip-<我的声明>` 类门禁。」

## 五、切 active 的两个硬前提

### 5.1 🔴 F8 任务未注册（归节点1）—— **决定性**

`docs/evidence/daemon-guard-revival-impl-20261110.md` L71 明示：

> **F8 任务定义：未注册（归节点1）**；在节点1 完成前，**即使 `active` 也无法拉起**
> （`schtasks /Run /TN QS_DaemonForever` 现存的 Boot-only 任务仍可被调用一次）。

**⇒ 观察期 29 次 `gate=all-pass` 未拉起，正是因为 observe 模式不执行；
而切 active 后要真拉起，依赖一个**尚未注册的 F8 任务**。**

**⇒ **切 active 必须等节点1 完成 F8 注册**，否则会出现「已切 active 但仍不拉起」的假象。

### 5.2 🔴 occupancy 窗口语义必须先收紧

**⇒ 现状**：任何会话若再写长窗口 occupancy，复活闸会再次被焊死（已发生 2 次）。
**⇒ 建议**：`daemon_occupancy.json` 的 `window` 强制 ≤5 分钟，或复活闸的占用判定改为
「**仅在声明的 operation 含「停/起」时生效**」，而不是「任何 occupied 都挡复活」。

## 六、若建议切 active——需要的动作清单

| # | 动作 | 责任线 | 前置 |
|---|---|---|---|
| 1 | **注册 F8 任务**（复活用计划任务；现 `QS_DaemonForever` 是 Boot-only）| **节点1** | — |
| 2 | **收紧 occupancy 语义**（窗口 ≤5min 或按 operation 判定）| ④ 线（+ 我方配合改用法）| — |
| 3 | `check_daemon_alive.ps1` 切 `-ReviveMode active`（或改 `QS_DaemonHeartbeat` 任务参数）| ④ 线 | 1、2 完成 |
| 4 | **切后观察 1 个 24h 周期**：`last_revive_at` 应出现非空值；无误拉起 | 观察 | 3 |
| 5 | 把「切 active」写进 `docs/decisions-pending-node20.md` 台账留痕 | 我方 | — |

**⇒ 建议：先做 1、2，再切 3；第 4 步的观察结果作为 active 生效的验收证据。**

## 七、当前现场

| 项 | 读数 |
|---|---|
| daemon 进程 | **✅ PID 36372**，起 05:56:18（已存活 15.7h）|
| daemon.log | 最后写 20:35:37（0 分钟前）|
| status | pid=36372，写于 05:56:23 |
| revival state | consecutive=0 / last_action=NONE / last_revive_at=null |

## 八、如实划界

| # | 未完成 | 原因 |
|---|---|---|
| 1 | 「其他 verdict」3 条未逐条解读 | 汇总脚本只按 OK/FAIL 二分统计 |
| 2 | 第五死（00:41）的**根因**未定位 | 当时 daemon 无 stderr 落盘（本次是第一次用官方 wrapper 起，带取证四件套）|
| 3 | 复活机制的**正向验收**（真死 ⇒ 60s 内 REVIVED）未做 | 需 `active` 模式 + 真实死亡场景，**属切 active 后的验收项** |
