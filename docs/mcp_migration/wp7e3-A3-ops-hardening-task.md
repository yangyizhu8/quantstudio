# A3 任务书：云同步运维防漏加固（Trae 执行）

> 版本：v1（2026-08-08，reasonix 布置，用户需求：避免再次出现分钟线数据
> 拉取/同步云端漏掉的情况，确保本地与云端数据一致且完整）
> 关联：A2 已修复水位越位+静默丢行（commit fb6c29eb，未 push）；本任务把
> "事后人工排查"升级为"自动巡检 + 告警 + 可修复命令"的全链路闭环。

## 0. 背景与目标

### 现有运维体系（已核实）
| 环节 | 载体 | 现状 |
|---|---|---|
| 本地 ETL（手动，收盘后） | `scripts/run_etl_and_check.ps1` → `data/check_etl_integrity.py` + `_start_all_incremental.py` | 检查 max_date/row_delta/幻影股/PID，exit 0/1/2 |
| 增量同步 | `TradingCloudSync`（工作日+周日 05:00）→ `run_cloud_sync.ps1` | A2 后：水位修复 + Step 3 post-sync 扫描（近 7 天，绿/黄/红） |
| 全量同步 | `TradingCloudSyncFull`（周六 05:00） | 有 |
| 分钟线修复 | `Trading_Repair_Minutes_00_04_30`（每日 00:00-04:30） | 有 |
| 巡检 | `Trading_Tasks_Daily_Check`（每日 09:00）→ `check_trading_tasks_0900.ps1` | **只查任务 LastRunTime/退出码 + 飞书，不查数据** |

### 已知漏洞（本次要堵的）
1. **采集侧无自动防线**：`get_tushare_data.py` 的 15:00 bar 校验（A2 技术债 2）未入库——本地采集"完整"靠历史结论，无校验兜底；
2. **本地完整性检查无 bar 维度**：`check_etl_integrity.py` 只有总量维度（max_date/row_delta）——某天截断（缺尾盘 2-3 根或严重截断）时总量可能正常，**漏拉不被发现**；
3. **同步读取侧**：`read_increment` 部分返回时水位仍可能越位（A2 技术债 1）——靠近 7 天扫描兜底（最长 7 天发现窗口）；
4. **巡检不感知数据**：09:00 巡检只查"任务跑没跑"，不查"本地数据新鲜度"（**用户手动 ETL 若忘记/出差，无人发现**）、不查"云端缺口状态"（05:00 同步的扫描结果未纳入巡检）；
5. **本地 vs 云端对账不成例行**：diag_minutes_gap.py 存在但未集成例行流程；日线/水位无对账。

### 目标
ETL 采集 → 本地 QuestDB → 云端同步 全链路闭环：**任何环节漏掉 → 自动发现（当日/次日巡检告警）→ 给出可执行修复命令**。依赖人工记忆的部分（手动 ETL）也要有自动安全网。

---

## 任务 M1：采集侧 15:00 bar 防御落地（技术债 2）

- `data/get_tushare_data.py` 的 `_process_single_stock_minutes` 写入前校验：每 (code, day) 末 bar 时刻 == 15:00 且 bar 数 ≥ 240（缺失重试复用 failed_tracker）；
- **提交卫生**：该文件混有 ~1000 行无关 ETL 改动——实现时校验逻辑独立成函数，**不打包提交**，只留工作区改动（或独立小文件如 `data/minute_bar_guard.py` 由该函数 import）；
- 验证：构造缺 15:00 数据 → 标记失败重试（单测或 dry-run）。

## 任务 M2：本地完整性检查升级（bar 维度）

`data/check_etl_integrity.py` 增加检查项 5：
- 对 stock_minutes / etf_minutes：**每 (code, day) 的 bar 数与末 bar 时刻**校验（240 / 15:00:00；新股上市首日除外——bar 数 = 上市以来交易日 × 240，用 trade_calendar）；
- 检查范围：最近 N 个交易日（建议 7，可参数化）；
- 发现缺口 → 输出缺口清单（code, day, 缺 bar 数）+ 修复命令（复用 `data/backfill_stock_minutes.py` 或 minutes_integrity 同款 DEDUP 回填）；
- 保持 exit 0/1/2 语义（缺口 → 2）；
- 验证：构造截断日（本地删除某日尾盘 5 根）→ 检查 FAIL 且清单正确。

## 任务 M3：巡检升级（数据感知，核心）

`scripts/check_trading_tasks_0900.ps1` 在现有"任务状态检查"基础上增加**三查**：

1. **本地数据新鲜度（手动 ETL 安全网）**：
   - 用**交易日历**判断昨日是否交易日（trading-battle-back 本地 QuestDB 有 trade_calendar；节假日/周末不报）；
   - 昨日为交易日时：本地 stock_minutes/etf_minutes/stock_daily 的 max_date 是否 ≥ 昨日 → 缺失则 🚨"昨日 ETL 未跑或数据缺失，请收盘后跑 run_etl_and_check.ps1"；
2. **云端缺口状态**：读取 `run_cloud_sync.ps1` Step 3 最近一次扫描产物（logs/minutes_gaps*.json 或日志）→ 0 缺口 ✅ / 有缺口 🚨 + 缺口数；
3. **同步任务结果交叉验证**：`TradingCloudSync` 今日 LastTaskResult==0 时，顺带确认"昨日数据"已同步（本地 max_date 与云端 max_date 对比，可经 run_cloud_sync 的产物或 diag 脚本）；
- 飞书通知保留，新增维度用同样的 ✅/🚨 格式；
- 巡检本身失败（脚本异常）也要飞书告警（不能静默）；
- 验证：模拟"昨日 ETL 未跑"（改本地数据或参数注入）→ 告警正确；节假日不误报；真实数据跑一遍全绿。

## 任务 M4：本地 ↔ 云端对账例行化

- 升级 `data/sync_to_cloud/diag_minutes_gap.py` 或新建 `scripts/check_cloud_reconcile.ps1`：
  - **分钟线**：逐 (code, day) 本地 vs 云端（已有）；
  - **日线**：stock_daily / etf_daily 逐 (code, day) 对账（行数 + max_date）；
  - **水位**：本地水位 vs 云端水位（watermark 表）；
  - 输出缺口清单 + exit 0/1/2；
- 挂接：周六全量同步完成后自动跑（TradingCloudSyncFull 尾部）+ 手动可跑；
- 验证：真实数据对账 0 缺口；构造差异 → 正确报出。

## 任务 M5：运维手册

`docs/ops-runbook.md`（trading-battle-back 仓库内）：
- 运维体系总览（手动流程 + 4 定时任务表 + 各自职责/时间/退出码语义）；
- 告警语义（exit 0/1/2 + 飞书 ✅/🚨 含义与处置动作）；
- 修复命令速查（ETL 缺失、云端缺口、水位异常、分钟线修复、回填）；
- 故障排查指引（A2 根因模式：水位越位/静默丢行/read_increment 部分返回的症状与排查步骤）。

---

## 验收标准

1. M1：缺 15:00 数据被采集层拦截/重试（单测证据）；
2. M2：构造截断日 → check_etl_integrity exit 2 + 缺口清单正确；
3. M3：三个新查项全部生效——"昨日 ETL 未跑"告警、云端缺口告警、节假日不误报（模拟 + 真实数据各一轮）；
4. M4：分钟线 + 日线 + 水位对账真实数据 0 缺口；
5. M5：手册覆盖上述全部命令与语义；
6. **回归**：现有 4 定时任务、run_etl_and_check.ps1、A2 的 run_cloud_sync.ps1 Step 3 行为不变。

## 红线

- ❌ 不改云端 MCP server 读服务；
- ❌ 不改写已正确的行（回填必须幂等 DEDUP UPSERT）；
- ❌ 240 bar/15:00 标准不降；
- ❌ 巡检告警不得误报：所有"缺失"判断必须交易日历感知（节假日/周末/新股上市日）；
- ❌ get_tushare_data.py 不打包提交（提交卫生，与 A2 相同的处理方式）；
- ✅ 所有改动先本地验证，提交按 trading-battle-back 流程（Q040 等 pre-commit 钩子须通过；
  提交前确认边界，不打包无关改动）。

## 交付物

1. M1-M4 代码改动（本地验证通过，提交边界清晰）；
2. M5 运维手册（docs/ops-runbook.md）；
3. 验证证据（模拟场景 + 真实数据各一项）；
4. 完成总结（改动清单 + 验收逐项对照）。
