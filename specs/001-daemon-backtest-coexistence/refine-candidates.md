# specs/001 refine 候选项

> 本文件只登记**待裁定的语义疑问与 refine 候选**，不改动本目录既有的 spec / plan / tasks / contracts 基线。
> 登记：2026-10-30 21:55（策略研发专属会话）；呈批：2026-11-04 框架项同轮。

## RC-001 · 副本根回测为何仍触发主 daemon 停止

| 项 | 内容 |
| --- | --- |
| 现象 | 回测以 `QUANTSTUDIO_DATA_ROOT` 指向**副本**库（`agent_workspace/r1_live_copy_20261011/quantstudio.db`）运行，但主 daemon 仍被停 |
| 实测证据 1 | 回测日志首行 `[coexist] 已等待 0.0s · 等待采集收尾（任务边界）` —— 回测启动了 coexist 收尾流程 |
| 实测证据 2 | `data/logs/daemon.log` 20:08:10 `[DaemonLifecycle] 已清理本进程 status 文件` ⇒ 主 daemon 于该时刻停止 |
| 实测证据 3 | 同一日志 21:51:34 `[CLI] 常驻模式（v3 DaemonLifecycle）` + status 已发布 pid=29152 ⇒ daemon 已自愈重启（非本会话操作） |
| 语义疑问 | coexist 的设立目的是**主库写锁共存**（回测与采集争同一个 `data/quantstudio.db`）。副本根回测**不争主库写锁**（只读副本）⇒ 按设计不应需要停主 daemon |
| 可能原因（待证） | ① coexist 判定只看「是否在跑回测」，未区分数据根是否为副本；② 任务边界收尾走统一停服路径，未按 `QUANTSTUDIO_DATA_ROOT` 分支；③ 其他（待定位） |
| 影响 | 副本根回测（本线全部历史跑法的默认形态）会**误停主采集 daemon**，造成采集窗口中断且调用方不知情 |
| 候选处置 | refine：coexist 进入前先判定有效数据根——若为副本根则跳过停服（或仅做只读探测确认无锁争用）；需补一条 `QUANTSTUDIO_DATA_ROOT=副本 ⇒ 不停 daemon` 的单测 |
| 状态 | **候选（未立项）**，待 2026-11-04 框架项同轮呈批裁定 |

## 附：本次暴露的申报纪律缺失（已入记忆，非 spec 项）

- 2026-10-30 20:08 黄金逐位一致 A/B 两次回测各停一次主 daemon，**当轮回执未申报**。
- 处置：新增会话纪律「任何回测（含副本根）后回执必须附 daemon 状态一行（RUNNING PID / NOT RUNNING + 依据来源 + 是否触发停服 + 恢复时点）」，已写入 global 常驻记忆。
