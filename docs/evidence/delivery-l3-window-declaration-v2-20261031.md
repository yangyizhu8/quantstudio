# 占用声明 v2 · L3 生产库只读窗口（重排：2026-10-31 16:30–20:30）

**声明时刻**：2026-10-31 15:38（Asia/Shanghai）｜**批准**：审核方 2026-10-31 裁定（重排 16:30–20:30，240 分钟硬上限）
**历史**：v1 窗口（15:09–15:20）因他线陈锁作废，见 `delivery-l3-window-void-20261031.md`
**铁律 19**：daemon 生命周期跨会话占用

## 0 与 v1 的差异（全部来自裁定）

| # | 变更 | 来源 |
|---|---|---|
| 1 | **前置步 0：陈锁体检**（`scripts/delivery_window_precheck.py`）——持有者已死则走既有自愈（一次 `acquire_write_lock`+`release_write_lock` 触发 `try_reclaim_stale`，CAS + 审计留痕）；自愈被拒 ⇒ **不开窗** | 裁定② |
| 2 | 体检通过后**重跑一次 `read_holder()`** 确认锁目录干净（防体检与开窗之间又有他线留锁） | 裁定细节① |
| 3 | 6 项复验单独设 **30 分钟上限**，超时即作废 | 裁定③ |
| 4 | 再次作废则**再出边界报告并停手，不第三次冒进** | 审核方指示 |

## 1 占用窗

| 项 | 值 |
|---|---|
| 时段 | **2026-10-31 16:30 – 20:30**（240 分钟硬上限） |
| 操作 | 停 daemon（精确 PID）→ 写 flag → **陈锁体检** → 复检 → 三件套 → 删 flag → 恢复 daemon |
| 影响的采集轮次 | 无（`daily_time=06:00` 已过，窗口不跨 06:00） |
| 恢复路径 | `schtasks /Run /TN QS_DaemonForever`（**禁 `Start-Process`**） |

## 2 开窗第一步（顺序不可交换）

```
a) 重核三事实：daemon_status.pid / create_time / data 六文件指纹（不沿用旧值）
b) 精确 PID 停 daemon（双形态匹配：`daemon.py` 或 `-m quantstudio.pipeline.daemon`；排除 $PID；create_time delta ≤ 2s）
c) 写 data/backtest_stop.flag
d) 三读数：目标进程不存在 / 全部 daemon 进程 0 个 / flag 存在
e) **陈锁体检**：python scripts/delivery_window_precheck.py
     - exit 0 且打印 CLEAN 或 RECLAIMED ⇒ 继续
     - exit 2（持有者存活/不可判定/自愈失败）⇒ **不开窗**，删 flag + 恢复 daemon + 上报
f) **复检**：read_holder() 必须为 None（否则不开窗）
g) 记录 e/f 的读数与 reclaimed 证据（进回执）
```

## 3 三件套（顺序固化；缺一 W2 不算收口）

| 序 | 内容 | 命令 | 判据/上限 |
|---|---|---|---|
| ① | 6 项隔离失败的无隔离复验 | `pytest tests/test_qfq_schema_migration.py::TestProductionRefuse tests/test_qfq_schema_migration.py::TestReportPathSafety tests/test_writer_channel_contract.py::test_b1_chunked_variant_is_same_channel_not_third -q` | 全绿；**30 分钟上限**，超时即作废 |
| ② | **CLI 两次串行导出 + 逐位比对** | `python -m quantstudio.delivery.cli export --export-dir D:\delivery_l3_tmp\run1 --json` → manifest + 逐文件 sha256 清单先落 `docs/evidence/delivery-l3-export1-20261031.json` → 删 `run1` → 同命令导出 `run2` → 比对 | 文件清单与逐文件 SHA256 一致率 **100%**（`generated_at`/`export.*` 时间字段除外）；输出目录**不得在 `data/` 内** |
| ③ | 全量测试基线（最后） | `python -m pytest tests -q`（真实 `DATA_ROOT`） | passed/failed 计数 + 既有失败逐条；**整份才算落成**；预算耗尽 ⇒ 中止并出「已完成 N / 剩余 M」 |

## 4 中断与异常

- 窗口内**禁手动启 daemon**（`run_forever()` 起始会清 flag）；机器重启后必须重新确认 flag。
- **任一异常**（daemon 意外起来 / 磁盘异常 / 非预期锁冲突 / 单项超时）⇒ 判窗口作废：删 flag → 恢复 daemon → 边界报告停手；**读数作废不硬凑，不第三次冒进**。

## 5 回执必含（v2 新增两项）

占用声明 v2 全文 → 开窗三读数 → **陈锁体检读数（BEFORE/diagnose/AFTER + reclaimed 证据）** → **复检 read_holder=None** → 三件套逐项读数 → SC-013 窗口前后各一次 → flag 删除确认 → daemon 恢复状态一行（PID + started_at）→ `D:\delivery_l3_tmp\` 清理确认。
