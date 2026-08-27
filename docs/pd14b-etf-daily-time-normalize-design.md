# P-D14b 设计：K-001 时刻戳管线根治（stock_daily + etf_daily 双表，2026-08-27）

- **流水线状态**：Step 1 v1（D2 公式被审计驳回→v2 修正通过）→ Step 2 审计通过 → **Step 3-4 实施/验收（进行中）**
- **范围扩展批准（2026-08-27 审计）**：P1 探针实测异常行 = **etf_daily 8244 + stock_daily 16619**（非原假设 etf 1974 单日）——MCP 补拉多日污染（07-01~08-14 值域），**stock_daily 也受影响**（此前未察觉）。审计批准：①存量归一扩至双表；②写入点归零扩至 stock_daily（同一 MCP 路径）；③断言重设（全库两表归一后 mod 单值 57600000）；④三防线按新规模（8244/16619 精确命中）；⑤**技术债门禁登记（见 §9）**。
- 关联：D3（P-D14）引擎端窗口匹配已容错；K-001 管线根治（本 WP）

---

## 1. 问题定义

### 1.1 现象（D3 已文档化）
etf_daily 2026-07-01 存在 **双 time 值**：73 行 `00:00`（1782835200000）+ **1974 行 `08:00`（1782864000000）**（含 515050/511260）。引擎单日精确匹配 `time=X` 漏掉 08:00 组 → 首日 ETF no_price（D3 已引擎端容错）。

### 1.2 根因取证（代码级 + 数值级）

**数值实证**：
- `1782835200000` = 2026-07-01 **00:00** Asia/Shanghai（正常）；
- `1782864000000` = 2026-07-01 **08:00** Asia/Shanghai（异常）；
- 正常路径（`to_ms_timestamp("20260713")` YYYYMMDD 分支 / `_mcp_time_to_utc_ms` daily trade_date 分支）→ 00:00；
- **08:00 = `pd.Timestamp("2026-07-01 08:00:00").tz_localize("Asia/Shanghai")` 的精确产物**（aligner L200-204 字符串路径）。

**代码级定位（写入时刻戳选择逻辑）**：
- `aligner.to_ms_timestamp`（L150-206）三种入参形态 → 三种结果：
  - int YYYYMMDD（L177-183）→ **00:00** CST（正常统一口径）；
  - str "YYYY-MM-DD[ HH:MM:SS]"（L200-204）→ `pd.Timestamp(s)` → **保留时刻**（若 s 带 08:00 → 08:00）；
  - pd.Timestamp（L189-193）→ 保留 tz → 时刻。
- `_mcp_time_to_utc_ms`（L2054-2095）daily 分支对 int/str 均取 00:00（正确）；**pd.Timestamp 分支（L2067-2076）daily 取 `strftime("%Y%m%d")` 归 00:00（也正确）**。

**收敛判定**：08:00 值 = **`to_ms_timestamp` 字符串路径**（aligner L200-204）接收了**带 08:00:00 时刻的字符串 trade_date**——即数据源（MCP/tushare 补拉）返回的 ETF 日线 trade_date 列中至少 1974 行是 "YYYY-MM-DD 08:00:00" 形态（而非纯 YYYYMMDD）。**时刻戳选择 = 字符串格式决定**：同一批数据混用两种格式 → 双 time 值。

> ⚠️ 注：需在**实施期探针**确认 08:00 是数据源原始返回形态还是某 adapter 转换引入（计划 §4 探针 P1）——当前为代码路径+数值双实证的收敛推断（采集时刻不是写入时刻的选择逻辑问题，而是**传入格式混用**）。

### 1.3 影响
- 引擎已容错（D3 窗口匹配）→ 当前无新业务影响；
- 数据层长期污染：历史 1974 行 08:00 双值残留 → 任何精确匹配消费者仍有隐患（已登记"仅此一处"消费者 query_daily_snapshot 已修；query_daily_for_status 未启用）。

## 2. 修复方案（三部分）

### 2.1 写入点修正（防复发）——管线层

**目标**：etf_daily 全量写入路径统一 time 为**当日 00:00 CST**（与 stock_daily 一致），无论入参是 YYYYMMDD/带时刻字符串/pd.Timestamp——**时刻归零**。

改动点（候选，实施时据探针定）：
- `aligner.to_ms_timestamp` 字符串路径（L200-204）：加**日期归零**——字符串含时刻时只取日期（`pd.Timestamp(s).strftime('%Y%m%d')` 再走 YYYYMMDD 路径）？**No**——这是工具函数，改影响面大（分钟数据依赖时刻）。
- **更精准**：在 ETF 日线写入前（daemon align 前）对 `raw_df['trade_date']` 做**格式归一**——若字符串含时刻 ` 08:00:00` 或日期-时间形态，统一转 YYYYMMDD 纯日期。定位点 = adapter.fetch_table 返回后、aligner.align 前（daemon L659→L755 之间）加行业特化归一。

**待探针确认**（实施期 P1）：08:00 原始形态在哪个 adapter——MCP ETF 日线（`_mcp_time_to_utc_ms` pd.Timestamp 分支理应归 00:00？）或 tushare 补拉（fund_daily trade_date 可能是 Timestamp 含时刻）——**探针在真实采集日志/DB 取原始返回样例**。

### 2.2 存量 1974 行归一（可逆 UPDATE + 备份）——**D2 修正版（审计驳回后重写）**

**⛔ 错误版本回放（v1 被审计驳回，勿再用）**：原公式 `SET time = time // 86400000 * 86400000 WHERE time % 86400000 != 0` 存在致命缺陷——
- **存储口径**：DB `time` 为 **UTC epoch ms**（非 CST 墙钟），故「当日 00:00 CST」= 前一日 16:00 UTC，`% 86400000 = 57600000（非零）`；「08:00 CST」= 当日 00:00 UTC，`% 86400000 = 0`；
- 原 `WHERE % != 0` **反向命中全部正常行**；原地板 `//*86400000` 归到 **UTC 日界 = CST 08:00 且跨日前移**（正常行 07-01 00:00 CST 被改成 06-30 08:00 CST）——**波及全表的灾难性 UPDATE**；
- 审计方数值复核正确（数值验证见 `output/pd14b_d2_verify.py`：正常行 %=57600000、异常行 %=0、修正后=正常行、旧地板错位确认）。

**✅ 修正方案（三防线）**：

```sql
-- 防线 0：实施前数值断言（设计文档即含，实施脚本必测）
--   断言 1：异常行命中数 ≈ 1974
--   SELECT COUNT(*) FROM etf_daily WHERE time % 86400000 = 0;         -- 应 ≈1974（变化率兜底 <1%）
--   断言 2：正常行约定全库一致（stock_daily 同位抽查）
--   SELECT COUNT(*) FROM stock_daily WHERE time % 86400000 != 57600000; -- 应 = 0（全库正常行余数=57600000）

-- 防线 1：备份（可逆）
CREATE TABLE etf_daily_backup_k001 AS
  SELECT * FROM etf_daily WHERE time % 86400000 = 0;

-- 防线 2：抽样 10 行人工核对（正常→异常对齐，执行前目检）
--   SELECT code, time, datetime(time/1000,'unixepoch','+8 hours') AS cst
--   FROM etf_daily WHERE time % 86400000 = 0 LIMIT 10;

-- 修正（异常行 = UTC 日界 = CST 08:00，平移 8h 到 CST 零点；正常行余数=57600000 不受影响）
UPDATE etf_daily SET time = time - 28800000
WHERE time % 86400000 = 0;
```

- 备份表 `etf_daily_backup_k001`（约 1974 行，可回滚）；
- **修正语义**：异常行 time（UTC 日界）**平移 -8h** → UTC 16:00 前日 = **CST 当日 00:00**（与正常行同锚）；正常行（`% = 57600000`）**零触碰**；
- 归一后全库 etf_daily 单 time 锚（全为 `% 86400000 = 57600000` = CST 00:00）；
- **注意**：本次归一改变 D3 引擎已观测的数据（08:00 组 close 值保留、time 归 CST 00:00 UTC 16:00）——引擎窗口匹配返回的行 close/volume 不变、撮合结果不变，仅 time 列数值微调 → 归入下一轮基线维护节奏（用户裁定）。

### 2.3 验收

| 项 | 判据 |
|---|---|
| 实施前断言 1 | `SELECT COUNT(*) FROM etf_daily WHERE time % 86400000 = 0` ≈ 1974（变化率 <1% 兜底） |
| 实施前断言 2 | `SELECT COUNT(*) FROM stock_daily WHERE time % 86400000 != 57600000` = 0（正常行约定全库一致） |
| 抽样人工核对 | 防线 2：10 行目检异常→正常对齐 |
| 全库单 time 锚 | `SELECT COUNT(DISTINCT time % 86400000) FROM etf_daily` = 1（全为 57600000） |
| 引擎快照行为不变 | D3 测试矩阵重跑 T1~T6 全绿（窗口匹配在平移后行为不变——close 不变，仅 time 数值微调） |
| 防复发 | 探针 P1 确认写入路径归零后，重采集样例日 time=00:00 |
| 回滚 | 备份表恢复（DROP 归一表 → CREATE AS backup） |

## 3. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 写入点归零在 **daemon align 前**（adapter 返回后）而非改 to_ms_timestamp | to_ms_timestamp 是全局工具（分钟数据依赖时刻）；日线 ETF 行业归零最小影响 |
| D2 | **存量归一 = 异常行 `time - 28800000 WHERE time % 86400000 = 0`（平移 8h 到 CST 零点）；正常行（% = 57600000）零触碰** | **存储为 UTC ms**：CST 00:00 = 16:00 UTC（% = 57600000 非零），CST 08:00 = 00:00 UTC（% = 0）——原地板公式谓词反向+锚点错位（审计驳回，已修正）；三防线（断言 1/2 + 抽样核对）防复发 |
| D2b | **设计数值自查纪律（审计建议纳入设计自查清单）** | 任何涉及时间戳/取模/位移的 SQL 公式，设计阶段即用两行手算或脚本断言验证（本 D2 错误即"未数值验证的臆测公式"，价值教训：设计自查清单加「数值类公式必断言」） |
| D3 | 归一时机 = 合并基线重验归档后 | 用户裁定（基线二次过期风险）；数据影响并入下一轮基线维护 |
| D4 | 先探针 P1 定 08:00 形态来源再改管线 | 新铁律"根因未证实不得修"——当前为收敛推断，实施期以探针实证 |
| D5 | 归一表备份（`etf_daily_backup_k001`）| 可逆 UPDATE 纪律 |

## 4. 实施期探针（P1，设计批准后执行）

1. **采集日志取证**：查 daemon 采集日志中 07-01 etf_daily 写入的 batch 记录（source=？trade_date 形态）；
2. **DB 原始形态**：查 08:00 行对应 code 的可用原始返回样例（若有采集缓存/日志 dump）；
3. **adapter 行为**：对 MCP/tushare 的 `fetch_table('etf_daily', ...)` 打点，捕获 trade_date 列的 dtype 与首值形态；
4. **裁决**：确认 1974 行的 08:00 是"数据源原始带时刻字符串" vs "前缀转换引入"。

## 5. 涉及文件

| 文件 | 改动 |
|---|---|
| `quantstudio/pipeline/daemon.py` | etf_daily 写入前 trade_date 归零归一（行业特化，align 前） |
| （候选）`quantstudio/pipeline/sources/mcp_adapter.py` / `tushare_adapter.py` | 若探针指向 adapter 层——在 fetch 返回处归零 |
| `scripts/etf_daily_time_normalize.py`（新） | 存量归一 + 备份 + 校验（--check/--apply/--revert CLI，仿 P-A3 backfill 模式） |
| `tests/test_pd14b_time_normalize.py`（新） | 管道归零 + 存量归一 + 引擎行为不变 |
| 设计/证据文档 | 本文件 + 实施验收 |

## 6. 验收标准

1. P1 探针裁决 08:00 形态来源；
2. 写入点归一生效（探针样例日重采集 time=00:00）；
3. 全库 `time % 86400000 != 0` = 0（含新增采集）；
4. D3 测试矩阵重跑全绿（引擎快照行为不变）；
5. 备份/回滚可逆验证。

## 7. 回退

- 存量归一：备份表恢复；
- 管线改动：stash create -u 回退点 + 定向 restore；
- 归一后 D3 测试矩阵回归作为验证。

## 8. 明确不做

- 不改引擎窗口匹配（D3 已容错，保留兼容）；
- 不改 to_ms_timestamp 全局工具（分钟数据依赖时刻）；
- 不动 index_daily 等非日线行情表（K-001 仅 stock_daily/etf_daily 两表实证）；
- 不删除备份表（保留可回滚性，后续确认无回归后再归档）。

## 9. 技术债门禁登记（总调度令，2026-08-27）

**「补拉/修复通道数据质量门禁」**——后续一切补拉/修复任务写入前必须过此门禁：

| 子项 | 门禁内容 | 来源 |
|---|---|---|
| **S1 时间戳归一** | 写入前 trade_date 归零为纯 YYYYMMDD（去时刻），time 恒为当日 00:00 CST | **本 WP（P-D14b，已实施）** |
| **S2 窗口边界** | `sync_repairs <= end` 截断缺陷（已在案）——补拉窗口不得越界 | 已登记缺陷 |
| **S3 去重** | 写入前按 (code, time) 唯一性断言（防 concurrent 重复写入） | 本门禁新增 |

门禁落点：daemon 写入前（align 前）——P-D14b 的写入点归零即 S1 子项实现；后续补拉任务一律过此三查。

## 10. 验收证据（实施后）

- 存量清洗：etf_daily 8244 + stock_daily 16619 已归一（`time - 28800000 WHERE %86400000=0`），备份表 `etf_daily_backup_pd14b`/`stock_daily_backup_pd14b` 保留原值；
- 正向断言：两表 `distinct_mods = 1`（全单锚 57600000）、`mod=0` 残留 = 0；
- 回滚验证：备份可逆向定位（主表 1786665600000-8h == 备份原值）；
- 测试：P-D14b 8/8 + D3 6/6 + 相关套件 124/124 全绿；
- 写入点归零（daemon 双表）已实施。

- 不改引擎窗口匹配（D3 已容错，保留兼容双值历史）；
- 不改 to_ms_timestamp 全局工具（分钟数据风险）；
- 不动 stock_daily（time 统一无此问题）；
- 不在合并基线重验归档前执行存量归一（用户裁定）。