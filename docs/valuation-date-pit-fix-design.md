# 方案：get_fundamentals(valuation) 的 date PIT 语义修复

- 文档类型：框架层改动方案（六步流水线第 1 步）
- 状态：**待审计**（未经审计不得实施）
- 提出日期：2026-09-04
- 触发来源：股息防守小市值五日轮动 R1 能力探查（B2 阻断项）· 客户裁定采用"乙-修正版"
- 关联设计：`docs/strategy-compiler/parameter-optimization-design.md`（无关）、在途 design_metadata / get_index_day_bar 重写（**共享核心文件，需总调度协调避让**）

## 一、问题定义

`get_fundamentals(security, table='valuation', fields=[...], date=D)` 在回测运行期，
当 `D` 早于预加载快照锚点时，**静默返回快照数据而非 D 日数据**，且无任何告警。

### 实测证据（真实注入 API 运行时调用，非 SQL 推断）

1. 短路位置：`quantstudio/backtest/providers/duckdb_provider.py:183-189`
   ```python
   def get_valuation(self, codes, date, fields=None):
       query_ms = _end_ms(date)
       df = self._data.get_fundamentals_from_preload(codes, fields)   # <-- 命中即返回，date 被丢弃
       if df is not None and not df.empty:
           ...
           return df
       df = self._data.query_valuation_daily_pit(codes, query_ms)     # <-- 仅未命中预加载才走 as-of
   ```
   预加载覆盖全市场（`query_valuation_for_preload`），故回测期内 as-of 分支实际不可达。

2. 现象复现：对 2026-07-23 / 24 / 27 / 28 / 29 五个不同 `date` 分别调用
   `get_fundamentals(['600519.SS','000060.SZ'], 'valuation', fields=['turnover_ratio'], date=D)`，
   五次返回**完全相同**值（600519 = 0.4986，000060 = 2.2147）。

3. 快照真实锚点**实测定谳**（客户要求）：
   attach(date='2026-07-30', prev_date='2026-07-29') 下，快照 turnover_ratio 与
   `stock_daily_valuation` 的 **07-29** 值逐值吻合（600519 0.4986 / 000060 2.2147），
   流通市值同源吻合（165,135,779.36 万元 = 07-29 circ_mv）。
   → **快照锚点 = prev_date（T-1），不是决策日 T。** 此事实写入契约。

## 二、语义目标（对齐 PTrade 原生 date 语义）

`date` 应表达"以该日为查询锚点的 as-of 取值"。分界以**快照锚点 T-1** 划定：

| date 取值 | 目标行为 | 说明 |
|---|---|---|
| 未传 | 快照路径（T-1 as-of） | 逐位不变 |
| date == T | 快照路径（T-1 as-of） | 逐位不变；T 日估值在 T 日回调时点不可得 |
| date == T-1 | 快照路径（T-1 as-of） | 逐位不变；与锚点重合 |
| **date < T-1** | **真 as-of 查询** | 修复点：返回该日真实估值 |

## 三、改动范围

### 改动点 A（路由，主修复）
`quantstudio/backtest/ptrade_api.py` :: `PtradeAPI.get_fundamentals`
- 在进入 valuation 分支前判定：请求 `date` 解析后与 `self._prev_date` 比较；
  `date` 为空 或 `date >= self._prev_date` → 保持既有调用链，**逐位不变**；
  `date < self._prev_date` → 走强制 as-of 入口（改动点 B），跳过预加载短路。
- 不改动 `_query_cache` 键结构（已含 date，行为不变）。`_query_cache` 在 `attach` / `attach_bar`
  每交易日/每 bar 清空，故同一日内 force 判定稳定，不存在跨日陈旧命中。
- **边界定稿（审计钉死 3）**：`date` 归一化与既有 `qd` 同 canon（`'YYYYMMDD'`）后再比较，防格式差穿界。
  `date` 解析失败**维持现行行为** —— 实测定谳：现行是**吞异常 + warning 日志 + 返回空 DataFrame**
  （fail-soft 早已存在，空串则视为未传），本修复**不新增也不移除** fail-soft。
  （更正：本节初稿曾写"维持现状抛异常"，与实测不符，已按 `tests/test_valuation_date_pit.py` 实测行为更正。）

### 改动点 B（provider 原语）
`quantstudio/backtest/providers/duckdb_provider.py`
- `get_valuation(codes, date, fields, force_as_of=False)`：新增**默认关闭**的强制 as-of 开关；
  默认值下现有调用点行为逐位不变（含 `get_valuation_query` 路径）。
- 复用既有 `DuckDBDataAccess.query_valuation_daily_pit(codes, query_ms)`（已实现逐日 PIT：`time <= query_ms` + `QUALIFY ROW_NUMBER() PARTITION BY code ORDER BY time DESC = 1`）。
- **不新增注入 API（审计钉死 1 · 范围缩减）**：原设计的 `get_valuation_series` 序列原语**取消**，
  不作为注入 API。改动点 A 落地后，策略直接**按日循环**调用
  `get_fundamentals(pool_list, 'valuation', fields, date=D_i)`（按日一次、list 批量；
  既有 `_query_cache` 已含 date 键，行为不变）——防守月每次调仓 20 次调用、全程约千次、毫秒级，完全够用。
  序列原语如需保留只能作为内部优化且**永不注入**；当前实现直接删除。
  **新增 API 面为零 → 本地专用面登记（local_only_symbols）与引用级拦截问题整体消除。**

### 改动点 C（不改代码，仅同步文档）
`README.md`、`docs/strategy_toolbox.md`、`docs/prompt_engineering.md`、
`docs/strategy-compiler/ptrade-profile-contract.md` —— 登记：date 分界语义、快照锚点 = T-1、
新序列原语签名与契约。

### 明确不改动（禁止面）
- 预加载快照构建逻辑（`query_valuation_for_preload` / `_preload_float`）与其锚点
- `date` 未传 / `date >= T-1` 的全部既有路径
- 其他任何 API（get_history / get_fundamentals 其他 table / get_stock_status 等）
- **任何具体策略源码零改动**（修复经引擎层生效，不重写策略）

## 四、影响面

| 面 | 结论 |
|---|---|
| 存量策略 | 12 个使用 `get_fundamentals`；传 `date=` 的 **2 个**（恐慌抄底事件驱动逆向策略 :142 `date=prev`；smallcap_overnight_scalp_7 :315 `date=previous_date`），两者均 = T-1 → 路径不变 |
| 消费 `date < T-1` 的存量调用 | **0 处**；修复前该分支静默返回错误数据，无人可能依赖 → 纯增益成立 |
| 引擎生命周期 / 撮合 / 复权 / 费用 / 净值 | 零接触 |
| 策略产物（本地策略 .py） | 零改动；6 策略重转须逐位一致 |
| 数据语义 | `date < T-1` 的返回**由"快照值"变为"D 日真值"** —— 这是**正确性变更**，不是性能优化 |

## 五、验收标准

1. **分界测试（硬门）**：`date` 未传 / `= T` / `= T-1` 三种输入，修复前后返回值**逐位一致**（含 dtype、索引、列序）。
2. **as-of 正确性**：`date < T-1` 时与 `stock_daily_valuation` 真值逐值一致；以 600519 / 000060 在 2026-07-27 至 07-30 的四日窗口为固定对拍样本。
3. **序列原语一致性**：`get_valuation_series` 与逐日逐只循环结果逐值一致；缺失日语义契约测试。
4. **多策略横验证（审计加严）**：6 策略（CANSLIM / fall_reversal / tech_etf_mvo_rotation / vol_regime_mom_rev / weekly_smallcap_growth / 周频小市值成长动量（三层止损））重转，
   **转换产物 `.py` SHA-256 逐位一致**；api_portability 冒烟全 PASS 作为前哨门。
5. **既有功能零衰减**：相关测试套件 + 既有功能回归**零新增失败**。**事实表述（审计钉死 4）**：
   本修复不触 `source_import.py` wrapper 模板，**无需** matrix reverify；
   运行 `scripts/check_fund_matrix.py --check` 作保险即可。
6. **纯增益证明（审计加硬）**：修复前后黄金结果（信号 / 订单 / 成交 / 持仓 / 净值 / 指标）逐项一致；
   并增补 **恐慌抄底策略（唯一 `date=T-1` 消费者）重跑，config.csv / daily_stats.csv / trades.csv
   三件套 SHA-256 与修复前逐位一致**（G3.5 口径）。

## 六、回退条件

任一条件命中即回退到修复前版本并重新审计：
1. 分界测试任一输入返回值与修复前不等价；
2. 6 策略重转产物哈希出现非预期差异；
3. 回归出现失败；
4. 快照路径行为漂移（含锚点、列序、dtype、空值行为）。

回退手段：`git stash create -u` 建零副作用回退点并 `git stash store` 持久化；精确文件清单提交，禁 `git add -A`。

## 七、风险与待定项

| 项 | 说明 |
|---|---|
| R1 | PTrade 平台原生 `date` 语义是否与本方案"date < T-1 真 as-of"一致，**未经平台实证** → 标注 `PTRADE_RUNTIME_UNVERIFIED`；如需实证走 D4 探针序列 |
| R2 | ~~新序列原语归属待定~~ —— **已由审计钉死 1 消除**：不新增注入 API，本地专用面登记问题不存在 |
| R3 | 与在途 `design_metadata`、`get_index_day_bar` 重写共享 `ptrade_api.py` / `duckdb_provider.py` → **须经总调度协调避让**；每次 edit 后 `git diff` 自检，防并行提交覆盖 |
| R4 | `get_valuation_series` 若触发"每分钟 200 次"类外部频率限制，须在契约中标注并加间隔策略（本地 DuckDB 路径不受限） |

## 八、六步流水线排程

1. 方案（本文档）→ **当前**
2. 独立审计（ZCode 等审核方）→ 未通过则修订重审，禁止带病实施
3. 实施（精确文件清单 + 写前快照 + edit 后 diff 自检）
4. 验收（第五节 6 项，证据写入 `docs/evidence/`）
5. 用户确认
6. 双仓库推送（`git push origin`，核对两个远程 HEAD 一致）

R1 关闭条件：本修复验收通过并落地后，R1 补记"换手率历史序列 PASS"，方可进入 R2。
