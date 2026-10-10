# 框架修复方案（审核申请）——估值字段契约 / 分钟档 BarData / ST 真值覆盖 / 1m 历史边界

- 提出：策略研发会话（首板高开接力1进2 · R1 能力探查）
- 日期：2026-10-09　｜　状态：**待审计**（六步流水线第 1 步；审计通过后方可实施）
- 触发来源：客户裁定（D-1 框架修复；D-2/D-3 立项按六步流水线；D-4 排查原因并补至最新交易日且防复发；D-5 登记复现）
- 证据根目录：`agent_workspace/first_board_high_open_1to2/`（`R1_CAPABILITY_REPORT.md` 与 `probe_r1_*.py`、`d4_probe*.py` 全部可复跑）

---

## 0. 一句话

本地回测框架的**估值字段契约不完整/单位不一致**、**分钟档位 BarData 缺失 preClose→high_limit 退化**、
**stock_daily 的 ST 真值在部分入库批次被静默置 False** 三处框架/管线缺陷，
使「1:1 复刻策略」在**选股精度**上不可信；本方案给出改动范围、验收标准与回退条件。

---

## 1. 问题定义（全部为实测，非推断）

### D-2 估值字段取不到（选股精度·阻断级）

调用：`get_fundamentals('600000.SS', table='valuation', fields=['market_cap','circulating_market_cap','total_value','float_value','turnover_ratio'])`
实测返回列：**只有 `['total_value','float_value','turnover_ratio']`** —— 请求的 `market_cap` / `circulating_market_cap` **被丢弃**。

代码路径（`PtradeAPI.get_fundamentals` → `DuckDBProvider.get_valuation`）：

| 路径 | 触发条件 | SELECT 行为 | 后果 |
| --- | --- | --- | --- |
| 预加载快照 | 默认（`force_as_of=False`） | `query_valuation_for_preload` **SELECT 无 market_cap / circulating_market_cap 列** | 请求即被 `get_fundamentals_from_preload` 的字段过滤丢弃 |
| 真 as-of | `date < T-1` | `query_valuation_daily_pit`：`total_mv AS market_cap`、`circ_mv AS circulating_market_cap`（**未换算**） | 值可用但单位=源表原值 |
| ORM | `get_fundamentals(query(valuation.market_cap))` | `query_valuation_orm`：`total_mv / 1e8` | 单位假设与源表不符（见 D-3） |

### D-3 单位不一致（正确性·阻断级）

**实测锚点**（两个独立样本互证）：

| 表 | 字段 | 实测值（2026-09-16 / 最新） | 折算 | 实际市值 | 判定单位 |
| --- | --- | --- | --- | --- | --- |
| `stock_daily_valuation` | `total_mv` (600000 浦发银行) | 30,308,312.853 | ÷1e4 = 3,030.8 亿 | ≈3,030 亿 | **万元** |
| `stock_daily_valuation` | `total_mv` (000001 平安银行) | 22,704,924.294 | ÷1e4 = 2,270.5 亿 | ≈2,270 亿 | **万元** |
| `limit_list_d` | `total_mv` (002442) | 2,859,162,502.4 | ÷1e8 = 28.6 亿 | ≈28.6 亿 | **元** |

结论：**两张表单位不同**（`stock_daily_valuation`＝万元；`limit_list_d`＝元），不可混用；
而 `query_valuation_orm` 对 `stock_daily_valuation.total_mv` 除以 `1e8`（按「元」假设）⇒ **偏差 1e4 倍**；
`ptrade_api.py:920` 文档称「market_cap/circ_mv/total_mv 单位：亿元（stock_float_share 存元 → /1e8）」与现实现不符。

### D-1 分钟档位 BarData 退化（正确性）

实测（`minute-bar-v1`，09:31 回调）：`data['600000.SS']` → `open=9.39 close=9.38 preclose=nan high_limit=0`。

- 机理：分钟事件循环用**分钟 bar 行**构建 `BarData`；`stock_minutes` 表**无 `preClose` 列**（实测为 NULL）⇒ `BarData.__init__` 取不到 `preClose` ⇒ `preclose=nan` ⇒ `high_limit = round(nan×档位, 2) → 0`。
- 后果：任何以 `data[code].high_limit` 为判据的策略在分钟档位下**判据恒真**（如「现价 ≥ high_limit×0.99」恒成立 ⇒ 该分支永不退出）。
- 范围：仅 `minute-bar-v1`（日线档位由日线行构造，字段齐备）。

### D-4 ST 真值覆盖断档（选股精度·数据层）

**证据链**：

1. `is_st_reliable=true` 逐日计数：2026-08-13 = **122**，2026-08-14 起 = **0**（至 09-18 全 0）。
2. 依赖表 `stock_namechange` **正常**：14,931 行、最新记录 2026-09-18、`data_source='mcp'`；含 9 月 ST 更名（002743「富煌钢构→ST富煌」2026-09-08；300472 2026-09-03；301117 2026-08-25）。
3. 逐股对照（002743，09-08 起为 ST）：其 09-08~09-18 的 `stock_daily` 行 **全部 `is_st_reliable=False, is_st_reliable_source='none'`**（应为 True）。
4. **写入批次对照**：日期 ≤08-13 的行由 **2026-08-14 批次**写入（该批次推导正常，True=122）；
   日期 ≥08-14 的行由 **2026-09-06** 与 **2026-09-20** 批次写入（这两批**未产生任何 True**）。
5. 推导实现：`aligner.py:670-742 _derive_st_status`（PIT 查 namechange，`status_after ∈ {ST,*ST}` → True，`source='namechange'` 仅标记 True 行）。
6. daemon 多处为「依赖拉取失败 → **留 False 不阻断主流程**」（`daemon.py:1005 / 1705 / 2016`）⇒ **静默降级**无告警升级。

**根因（已定到批次级）**：2026-09-06 与 2026-09-20 两次 `stock_daily` 入库未应用（或未成功应用）`_derive_st_status`，
行落库时保持默认 `False/'none'`；因设计上不阻断，缺陷静默存在。
（尚待实施前确认的最后一跳：这两批次具体走哪条入库分支——见 §3.4 前置取证。）

### D-5 1m 历史 count 边界不一致（一致性）

同一时刻（09:31）、同一标的（600000.SS）：`cnt=3` **返回空**，`cnt=1/2/5` **正常**（v4 vs v6 探针）。
另 11 只样本在 `cnt=1/2/5` 全部命中，末根均为各自当日 09:30 竞价 bar（逐值对库一致）。

---

## 2. 修复前置纯增益审计（三型判定）

| 项 | 判定类型 | 依据 |
| --- | --- | --- |
| D-2/D-3 估值字段与单位 | **数据修正型** | 结果按正确口径变化；须显式声明影响面 + diff 只能归因于修正行集 + 准确度只升不降；出现任何其他 diff 即验收失败回退 |
| D-1 分钟 BarData | **纯恢复型** | 恢复到契约应有语义（`preclose/high_limit` 有值）；日线档位零改动 |
| D-4 ST 覆盖 | **数据修正型** + **新增检测型**（防复发门） | 回填按正确口径变化；新增覆盖率告警属「新增能力开始报告真相」，须单列不与回滚混淆 |
| D-5 1m 边界 | 待复现后定 | 复现前不得改（根因未证实不得修）|

三项均**不得改变回测引擎的撮合/费用/生命周期语义**（与「性能优化不得改变引擎行为」铁律一致）。

---

## 3. 改动范围

### 3.1 D-2/D-3（框架层 · 估值契约）

| 文件 | 位置 | 改法（建议，实施前需审计确认） |
| --- | --- | --- |
| `quantstudio/backtest/providers/duckdb_data_access.py` | `query_valuation_for_preload`（≈1635） | SELECT 增补 `v.total_mv`、`v.circ_mv`（或直接按目标单位别名）|
| 同上 | `query_valuation_daily_pit`（≈1680） | 将 `total_mv AS market_cap` / `circ_mv AS circulating_market_cap` 换算为**目标单位** |
| 同上 | `query_valuation_orm`（≈1781） | `/1e8` → 按实测单位换算（万元→亿元 = `/1e4`）；并修正 `ptrade_api.py:920` 文档口径 |
| `quantstudio/backtest/providers/duckdb_provider.py` | `get_valuation`（181） | 两路径统一补齐 `market_cap` / `circulating_market_cap`，保证字段恒在（fail-closed：缺列即报错而非静默丢字段）|

**契约目标（需审计裁定）**：

1. `market_cap` / `circulating_market_cap` **统一为「亿元」**（聚宽 `get_valuation` 与 PTrade `valuation.market_cap` 口径一致，也是本策略阈值 70/520 的量纲）。
2. `float_value` / `total_value` **保持现值与单位不变**（存量策略 `smallcap_overnight_scalp_7_quantstudio.py` 等在用），避免连带破坏；其单位在文档中显式标注（实测为源表原值＝万元）。
3. 字段**恒存在**：请求未支持的字段应显式报错/告警，不得静默丢弃（本次 D-2 即由静默丢弃掩盖）。

### 3.2 D-1（框架层 · 分钟档位 BarData）

| 文件 | 位置 | 改法 |
| --- | --- | --- |
| `quantstudio/backtest/ptrade_api.py` | `BarData.__init__`（262） / `DataDict.set_curr_data`（303） | 分钟档位构造 `BarData` 时以**当日日线快照**的 `preClose` 兜底（分钟行缺该列）|
| `quantstudio/backtest/backtest_engine.py` | `_run_minute_day`（2413） / `_load_minute_snapshots`（2372） | 装载分钟快照时把 `preClose`（及未来需要的日线字段）并入 bar 行；或在 `attach_day` 阶段向 `DataDict` 注入 `prev_close_map` |

### 3.3 D-4（数据管线 · ST 覆盖）

| 文件 | 位置 | 改法 |
| --- | --- | --- |
| `quantstudio/pipeline/daemon.py` | 三处 stock_daily 入库分支（≈989 / 1691 / 2002） | 确认并统一：**每次** stock_daily 写入都必须准备 `namechange_df` 并调用 `_derive_st_status`；缺失时 **fail-loud**（告警升级为 error/阻断或写入覆盖率标记）|
| 同上 | `_prepare_namechange_df`（3369） | 保留 auto-trigger；增加「依赖为空」的显式上报 |
| 新增 | 数据质量门 | stock_daily 入库后断言：若 `stock_namechange` 在 [该批次日期区间] 内存在 ST 更名记录，则该区间 `is_st_reliable` True 计数必须 > 0；否则 fail-loud |
| 回填 | 一次性脚本 | 按 PIT 从 `stock_namechange` 重算 **2026-08-14 ~ 最新交易日** 的 `is_st_reliable` / `is_st_reliable_source` / `is_delisting_risk` |

### 3.4 D-5（登记 + 复现）

| 项 | 内容 |
| --- | --- |
| 复现脚本 | `agent_workspace/first_board_high_open_1to2/probe_r1_v4.py`（cnt=3 返空）与 `probe_r1_v6.py`（cnt=1/2/5 正常）|
| 前置取证 | 读 `ptrade_api.get_history` 1m 分支的内存切片与 SQL 回退两条路径的 `count` 语义，确认是否「缓存命中 → len 容忍 < count；缓存缺失 → SQL 严格 count 即返空」|
| 处置 | 复现并定性后再定改法（本次**不动**）|

### 3.5 待裁定：名称过滤（源码「名称不含退」）

- 现状：`get_stock_info(..., field='stock_name')` 返回**代码**（`ptrade_api.py:2500/2527` 硬编码 `display_name = code`）⇒ 源码该过滤无 API 可走。
- 建议纳入同批：把 `display_name` 接真实简称，**且按 PIT 取值**（用 `stock_namechange` 推导当日简称；直接读 `stock_basic.name` 当前快照会引入未来信息）。
- 若不修：以 `get_stock_status(query_type='DELISTING')` 替代并登记差异（口径弱于原名判据）。

---

## 4. 影响面

| 受影响面 | 说明 |
| --- | --- |
| 读 valuation 的全部策略 | `market_cap`/`circulating_market_cap` 单位由「万元（as-of）/缺失（preload）」改为「亿元」，**小市值类策略的阈值需同步复核**（属数据修正型的显式影响面）|
| ORM 面 `query(valuation.market_cap)` | 值将从 1e-4 倍的错误量级回到正确量级 |
| 分钟档位策略 | `high_limit` 由 0 变为真实值；依赖旧退化行为者（若有）将改变行为——须回归 |
| 日线档位 | **零改动**（D-1 仅分钟档位）|
| 数据层 | D-4 回填只写 `is_st_reliable*` / `is_delisting_risk*` 四列，不改行情/估值数值 |

---

## 5. 验收标准

**V-1（D-2/D-3 字段与单位）**
1. `get_fundamentals('600000.SS', table='valuation', fields=['market_cap','circulating_market_cap'])` 返回**两列均在**；
2. 值为**亿元**且与独立锚点一致：600000 ≈ 3,030 亿、000001 ≈ 2,270 亿（容差 ±1%）；
3. ORM 面 `query(valuation.market_cap)` 与上同值；
4. `float_value`/`total_value` 数值**逐位不变**（回归对照）；
5. preload 与 as-of 两路径同值同单位（同一交易日对照）。

**V-2（D-1 分钟 BarData）**
1. 分钟档位下 `data[code].preclose` = 当日日线 `preClose`；`high_limit = round(preClose×档位, 2)`；
2. 断言样本：002442（涨停股，high_limit 应等于当日涨停价）、600000（普通股）、1 只 ST 股（档位 5%）；
3. 日线档位回归：`preclose/high_limit` 逐位不变；
4. 分钟档位既有测试全绿 + 与修复前对照：**除 high_limit/preclose 外无其它字段差异**。

**V-3（D-4 ST 覆盖）**
1. 回填后逐日 True 计数与独立复算（从 `stock_namechange` 独立脚本 PIT 推导）**逐日一致**；
2. 定点断言：002743 在 2026-09-08~2026-09-18 = True；600000 全期 = False；2026-08-13 及以前**逐日不变**（122 等原值保持）；
3. 覆盖率门生效：人为构造「有 ST 更名但 True 计数为 0」的场景 → 门必须 fail-loud；
4. 数据更新至最新交易日后，最新交易日的 True 计数 > 0 且与名称快照口径可解释。

**V-4（整体回归）**
1. 相关测试套件全绿（含 `tests/` 中 valuation / minute profile / pipeline 相关）；
2. **黄金结果对照**：选取既有策略在**修复前**基线回测（同日同参）→ 修复后重跑，逐日净值/成交序列差异**只能归因于上述修正行集**，逐笔归因；出现其他 diff 即验收失败；
3. `QUANTSTUDIO_DATA_ROOT` 指向副本库跑通端到端（客户已批准副本库口径）。

---

## 6. 回退条件

1. 任一验收项不通过 → 停在该项、**原样回退**（`git checkout -- <本方案改动的精确文件清单>`），不得带病推送；
2. D-4 回填脚本必须先 dry-run 出「将更新的行数/区间/样例」并留档，确认后再写；回填前后对受影响四列之外的**任何列**做逐位比对，出现差异即回退；
3. 黄金对照出现非归因 diff → 立即回退并重新定位；
4. 推送前必须建立**写前快照**（`git stash create -u` + `git stash store`）并记录 hash。

---

## 7. 前置取证（实施前必须补的最后证据）

| # | 待取证 | 目的 |
| --- | --- | --- |
| P-1 | 2026-09-06 / 2026-09-20 两次 stock_daily 批次走的具体入库分支（backfill / mcp-direct / 补拉） | 精确定位 D-4 断点，避免改错分支 |
| P-2 | `float_value` / `total_value` 的**存量消费方清单**及各自期望单位 | 确保 §3.1 第 2 条「保持不变」不破存量策略 |
| P-3 | `query_valuation_orm` 的实际消费方（是否有策略在用 ORM 面） | 评估 D-3 单位修正的影响面 |
| P-4 | 主库（`data/quantstudio.db`）当前被 PID 41372 独占；R5/回测需数据库可用 | 排期前提（客户已批准用副本库跑本策略线）|

---

## 8. 与策略线的关系（不阻塞项）

- 本方案通过并落地后，策略线执行 **R1 复验**（估值字段、分钟 BarData、ST 覆盖三项在新库上重测）→ 闭合 R1 → 进 R2（design JSON + 组件计划）。
- 窗口按客户裁定**更新至最新交易日**，前提是 D-4 回填完成 + 分钟数据端点 ≥ 窗口末日（副本库分钟止于 2026-09-16，主库口径待解锁核实）。
- 名称过滤项（§3.5）若本批不修，策略线将以 `get_stock_status(DELISTING)` 替代并登记差异——**会低于「完全复刻」要求**，故建议纳入本批。