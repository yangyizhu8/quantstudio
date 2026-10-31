# 占用声明 v3 · L3 生产库只读窗口（第 3 次开窗）

**声明**：2026-10-31 18:42（Asia/Shanghai）｜**批准**：审核方 2026-10-31 裁定（前置完成即开窗，240 分钟硬上限）
**发布渠道**：会话群 + **额外同步客户运维会话 1**（其有只读验证待重跑；RO 不互斥但占用板互见，双向纪律）
**历史**：v1 15:09–15:20 作废（他线陈锁）；v2 16:31–17:30 部分成立 + 事故（见 `delivery-l3-window2-report-20261031.md`）

## 0 v3 相对 v2 的增补（全部来自裁定）

| # | 增补 |
|---|---|
| 1 | **事故三核**：终止任何进程前必须核 ①脚本文件归属 ②启动时刻与本窗口关系 ③`%TEMP%` 下脚本一律先归属；**匹配通过 ≠ 有权终止**（v2 误杀 A2b 进程的直接教训） |
| 2 | **主库独占探测为开窗硬门**：`CreateFileW(GENERIC_READ, share=0)` 成功（无人独占）才可开窗；失败 ⇒ 不开窗并上报（v2 的 `TARGET_HANDLE_BUSY` 由此可在开窗前发现） |
| 3 | **发布渠道**：会话群 + 客户运维会话 1，双向可见 |
| 4 | 前置件已完成：SC-013 基线 **v2**（36 保护路径；含他线窗口内新增的 `qfq_observation.py` / `qfq_profile.py` 当前态）；`stats.py` 表集口径按 **FR-006 v5 / FR-018 v5** 修正并通过 fixture 验收 |

## 1 占用窗

| 项 | 值 |
|---|---|
| 时段 | 2026-10-31 **18:45 – 22:45**（240 分钟硬上限） |
| 操作 | 停 daemon（精确 PID）→ 写 flag → 三读数 → 陈锁体检 → **独占探测** → 复检 → 任务 → 删 flag → 恢复 daemon |
| 影响采集轮次 | 无（`daily_time=06:00` 已过，窗口不跨 06:00） |
| 恢复 | `schtasks /Run /TN QS_DaemonForever`（禁 `Start-Process`） |

## 2 开窗序列（顺序不可交换）

```
a) 三事实重核：daemon_status.pid / create_time / data 六文件指纹（不沿用旧值）
b) 精确 PID 停 daemon（双形态核验：daemon.py | -m quantstudio.pipeline.daemon；排除 $PID；create_time delta ≤ 2s）
c) 写 data/backtest_stop.flag
d) 三读数：目标进程不存在 / daemon 进程 0 个 / flag 存在
e) 陈锁体检：scripts/delivery_window_precheck.py（exit 0 = CLEAN 或 RECLAIMED；exit 2 ⇒ 不开窗）
f) 主库独占探测：CreateFileW(share=0) 必须成功（handle 有效）；失败 ⇒ 不开窗上报
g) 复检 read_holder() == None
```

## 3 任务（按序）

| 序 | 内容 | 判据 |
|---|---|---|
| ① | **SC-009**：CLI 两次串行导出（`D:\delivery_l3_tmp\`，禁 `data/` 内）；第一份 manifest+sha256 清单先落 evidence 再删；逐文件比对（时间字段除外） | 一致率 **100%**；顺带记录 SC-003 生产侧（两份各自 ATTEST 的三件 `db_open`） |
| ② | 全量测试基线（最后跑） | passed/failed + 既有失败逐条；整份才算落成；超时即中止出边界报告 |

## 4 异常即作废

非预期锁冲突 / **独占探测命中** / daemon 意外起来 ⇒ 立即删 flag、恢复 daemon、出边界报告停手。

## 5 回执必含

v3 全文 → 重核三读数 → 陈锁体检与**独占探测**读数 → SC-009 比对结论 + SC-003 生产侧读数 → 全量基线计数与既有失败清单 → **SC-013 窗口前后各一次（对照基线 v2）** → flag 删除确认 → daemon 恢复一行（PID + started_at）→ 清理确认。
