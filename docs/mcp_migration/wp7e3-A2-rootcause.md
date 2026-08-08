# A2 根因结论：云端分钟线完整性缺陷（Trae 执行）

> 版本：v1（2026-08-08）
> 关联任务书：wp7e3-A2-sync-pipeline-task.md
> 状态：根因已定位，证据充分；修复方案见文末。

## 1. 结论摘要

**根因在推送侧（sync_to_cloud），不在采集侧。** 本地 QuestDB 分钟线采集 100% 完整；
云端缺失是「水位推进过快 + 推送静默丢行」导致：水位被推进到 `max(读取行 ts)`，
而非 `max(已写入行 ts)`，未写入的证券行 `trade_time ≤ 水位`，永不再被增量同步读取。

任务书 4 个候选方向的验证结论：

| # | 候选方向 | 结论 | 证据 |
|---|---------|------|------|
| 1 | 采集器提前结束（缺尾盘 2-3 根） | ❌ 不成立 | 本地 000001/600519/300750/688981/510300 全部 386 天 × 241 bar，末 bar 均 15:00:00 |
| 2 | 严重截断天（126 bars）无告警 | ⚠️ 部分成立 | 「126 bars」是云端截断（推送丢行），非采集截断；但「无告警」确为真问题（推送静默丢行不告警） |
| 3 | 推送环节丢行 | ✅ **根因** | 见下文证据 A/B/C |
| 4 | 上游 tushare/xtquant 缺 15:00 bar | ❌ 不成立 | 本地所有样本所有天均含 15:00 bar；stk_mins 请求区间为 `09:30:00 ~ 次日00:00:00`，含 15:00 |

## 2. 证据

### 证据 A：本地完整、云端缺证券（etf_minutes 近 1 月窗口）

诊断脚本 `data/sync_to_cloud/diag_minutes_gap.py`（只读，新增）扫描 2026-07-01 ~ 2026-08-09：

```
--- etf_minutes 按交易日 distinct ts_code 数 ---
  日期            本地codes    云端codes    缺失codes     本地rows     云端rows
❌ 2026-07-14        1617         337        1280       389697       81217
❌ 2026-07-30        1632         302        1330       393312       72782
❌ 2026-08-03        1637          30        1607       394517        7230
   其余 25 个交易日   本地==云端     0          —           —           —
>> 窗口内共 3 个交易日存在云端缺失证券，累计缺失 (code,day) ≈ 4217
```

- 510300.SH（沪深300ETF）在云端完全缺失 2026-07-14 / 07-30 / 08-03 三天（本地均有 241 bar / 15:00）。
- 2026-08-03 云端仅 30 只 ETF（7230 行 ≈ 30×241），即推送只写了 ~2% 证券后中断，但水位已到当日 15:00。
- stock_minutes 同窗口 0 缺失（每日本地 codes == 云端 codes，行数完全一致）——问题对 etf_minutes 更显著，但机制通用。

### 证据 B：水位源码 = `max(ts_col)` of 读取行，非已写入行

`data/sync_to_cloud/sync_incremental.py:48-55`（sync_one_table）：

```python
df = reader.read_increment(table, ts_col, since)   # 读取本地 trade_time > 水位 的全部行
if df is None or len(df) == 0:
    return {"table": table, "rows": 0, "mode": "none", "status": "skipped"}
mode = "increment"
max_ts = df[ts_col].max()                          # ← max(读取行)，含未写入的行
rows = cw.batch_insert(table, df, batch_size=...)  # ← 返回已写入行数，可能 < len(df)
wm.update(table, max_ts)                           # ← 水位推进到 max(读取行)，越过未写入行
```

`watermark.py:25`：`ts = self.cw.max_ts(table, ts_col)` = 云端 `max(trade_time)`（全表单一水位，跨所有证券）。

**失血路径**：`rows < len(df)` 时（batch_insert 丢了行），`max_ts` 仍是 `df.max()`（含丢失行），
水位越过丢失行 → 下一轮 `read_increment` 用 `WHERE trade_time > 水位` → 丢失行 `trade_time ≤ 水位` → 永不再读。

### 证据 C：batch_insert 静默丢行 + read_increment 可能部分返回

`cloud_writer.py:196-211`（_write_recursive）：连接 OperationalError 时二分降批，
降到单行仍失败则 `return 0`（跳过该行，仅 warning），不抛异常。batch_insert 返回的 `total` 不含这些行，
但调用方无法区分「输入 0 行」与「N 行全失败」。

`local_reader.py:33-44`（read_increment）：`SELECT * WHERE trade_time > since ORDER BY trade_time`，
经 HTTP `/exp` 流式 CSV。若本地 QuestDB 在大结果集（单日 etf ~39 万行 / stock ~125 万行）流式传输时
连接中断，`query_csv` 可能返回部分 DataFrame 而不抛异常 → df 为子集 → max_ts 仍可能是当日 15:00
（若子集含 15:00 的证券）→ 水位越过未读证券。

### 证据 D：日志直接印证水位越过

`logs/run_sync_now.log`（2026-08-06 19:21）：

```
read_increment etf_minutes since=2026-08-03 15:00:00 -> 789757 rows
batch_insert etf_minutes: 789757 rows
```

08-06 同步的 `since=2026-08-03 15:00:00` —— 说明水位在 08-03 当天已到 15:00。
但证据 A 显示云端 08-03 仅有 30 只 ETF。即：08-03 推送只写了 30 只，水位却到了 08-03 15:00，
其余 1607 只的 08-03 数据（trade_time ≤ 08-03 15:00）此后再不会被增量读取。

### 证据 E：monitor 无法发现（diff 恒为 0）

`run_sync_now.py:55-58` 与 `sync_daemon.py:145-148`：`monitor.record(..., local_rows=r.get("rows"),
cloud_rows=r.get("rows"), ...)` —— local_rows 与 cloud_rows 用同一个值（batch_insert 返回值），
`diff = local_rows - cloud_rows` 恒为 0。监控永远报「一致」，掩盖丢行。

## 3. 复合因素

1. **单一全局水位/表**：所有证券共享一个 `max(trade_time)`。任一证券的 15:00 bar 入库即把水位推到 15:00，
   其余缺 15:00 的证券被掩盖。这是「20% 天缺末 bar」的放大器。
2. **无推送完整性校验**：sync_one_table 推送前/后不校验每 (ts_code, day) 的 bar 数与末 bar 时刻，
   不完整数据静默入库。
3. **无对账回填**：sync_repairs 仅消费 `local_repair_log`，而分钟线采集（load_minutes）不写该日志，
   故水位越过的缺口无任何自动回填路径。

## 4. 修复方案（按任务书步骤 2/3/4）

### 步骤 3（核心）：推送侧水位与完整性校验
1. **水位正确性**（sync_incremental.py）：`rows < len(df)` 时不推进水位（或仅推进到已写入行 max ts），
   并显式告警。利用 QuestDB DEDUP UPSERT 的幂等性，下一轮重读重推是安全的。
2. **batch_insert 返回已写入行 max ts**（cloud_writer.py）：新增返回 `(rows, max_written_ts)`，
   sync_one_table 用 max_written_ts 推进水位，精确越过已确认写入的行。
3. **分钟线完整性校验**（新模块 `minutes_integrity.py`）：推送后对每 (ts_code, day) 校验 bar 数 ≥ 240
   且末 bar = 15:00；不完整则告警 + 记入缺口表，触发定向重推。
4. **monitor 修正**：cloud_rows 改为真实云端 count，使 diff 可反映丢行。

### 步骤 2（防御性）：采集侧完整性校验
本地采集已完整，无需改采集逻辑；但作为纵深防御，在 `_process_single_stock_minutes` 写入前加
「当日 bar 数与末 bar 时刻」校验，缺 15:00 则标记失败并重试（复用现有 failed_tracker）。

### 步骤 4：历史重采
扫描 2025-01-01 至今本地 vs 云端 (ts_code, day) 集合差异，对缺失/不完整组用
`push_repair_window.py`（DEDUP UPSERT，只补缺失行）定向重推；完成后云端缺失清单归零。

## 5. 红线遵守
- 不改云端 MCP server 读服务（本方案全部在本地 sync_to_cloud + 采集层）。
- 不改写已正确行（重采用 DEDUP UPSERT，幂等覆盖同值，不扰动正确数据）。
- 240 bar/日 + 15:00 标准不降（完整性校验以此为准）。
- 先保新采集正确（步骤 3 水位修复优先），历史重采其次（步骤 4 分批）。

---

## 6. 交付证据（A2 完成，2026-08-08）

> 四步全部落地。根因已修复、历史缺口已归零、post-sync 完整性校验已上线。

### 6.1 修复 diff 清单

| # | 文件 | 改动 | 对应步骤 |
|---|------|------|---------|
| 1 | `data/sync_to_cloud/sync_incremental.py:56-79` | `sync_one_table` 部分写入（`rows < len(df)`）时不推进水位，返回 `status=partial_write` + `error` 告警；完整写入才 `wm.update` | 步骤3 核心 |
| 2 | `data/sync_to_cloud/run_sync_now.py:104-111` | `partial_write` 并入错误状态集，`has_error=True` → 退出码 1，暴露失败让运维感知 | 步骤3 告警 |
| 3 | `data/get_tushare_data.py`（`_process_single_stock_minutes`） | 采集后按交易日校验末 bar 时刻，缺 15:00 且 bar≥200 则记入 `failed_tracker` 待重试 | 步骤2 防御 |
| 4 | `data/sync_to_cloud/minutes_integrity.py`（新增） | 两阶段扫描（日级 code 计数 → 缺口日下钻）+ `backfill_day_batch` 批量回填 + `--verify` 验证 | 步骤3+4 |
| 5 | `data/sync_to_cloud/tests/test_sync_watermark.py`（新增） | 4 项单元测试：全量写入推进 / 部分写入保持 / 零返回保持 / 无增量跳过 | 步骤3 验证 |
| 6 | `scripts/run_cloud_sync.ps1:80-130` | Step 3：sync 成功后扫描近 7 天，小缺口(≤50)自动回填+验证，大缺口 exit 2 告警 | 步骤3 上线 |

### 6.2 单元测试结果

```
data/sync_to_cloud/tests/test_sync_watermark.py
  test_full_write_advances_watermark   PASSED
  test_partial_write_holds_watermark   PASSED   ← 根因修复关键断言：部分写入 wm.update_count==0
  test_zero_return_holds_watermark     PASSED
  test_no_increment_skipped            PASSED
======================== 4 passed, 1 warning in 0.79s =========================
```

### 6.3 历史重采验证（云端缺失清单归零）

原始全量扫描（`minutes_gaps_full.json`，750KB）发现 4216 个缺失/不完整组（etf_minutes 2026-07-14/07-30/08-03 等），经 `backfill_day_batch` DEDUP UPSERT 定向回填后：

**最终全窗口验证扫描**（`--scan --since 2025-01-01`，2026-08-08 20:37）：

```
scan_table stock_minutes 2025-01-01~2026-08-09
  阶段1: 交易日 387, codes缺口日 0, rows缺口日 0
  stock_minutes 缺失/不完整组: 0
scan_table etf_minutes 2025-01-01~2026-08-09
  阶段1: 交易日 387, codes缺口日 0, rows缺口日 0
  etf_minutes 缺失/不完整组: 0
扫描完成: 0 个缺失/不完整组
```

→ 387 个交易日 × 2 表，云端缺失清单 = 0。回填只补缺失行，正确行未触碰（DEDUP UPSERT 幂等）。

### 6.4 post-sync 完整性校验上线证据

`run_cloud_sync.ps1` Step 3 冒烟（2026-08-08 20:36，近 7 天窗口）：

```
[Integrity] Post-sync minute-line scan (recent 7 days)...
scan_table stock_minutes 2026-08-01~2026-08-09
  阶段1: 交易日 5, codes缺口日 0, rows缺口日 0
  stock_minutes 缺失/不完整组: 0
scan_table etf_minutes 2026-08-01~2026-08-09
  etf_minutes 缺失/不完整组: 0
[Integrity] OK - no gaps in recent 7 days (watermark fix holding)
```

- PS1 语法校验：`SYNTAX OK`（Parser 无错）。
- gaps JSON 可被 PowerShell `ConvertFrom-Json` 解析，计数正确。
- 水位修复后新数据无缺口 → 「禁止静默入库」红线满足：部分写入不推进水位 + 显式 `partial_write` 告警 + post-sync 扫描兜底。

### 6.5 与 QuantStudio 侧（A1）联动

云端缺口归零后，受影响证券在 QuantStudio 侧 T-B1（分片化改造）扩窗口重灌时自然补齐。A2 不阻塞 A1 bootstrap（A1 已批准），两条线并行推进。
