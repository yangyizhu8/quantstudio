# B2' 修复设计：分钟 count 查询跨日窗口语义（v1，2026-09-05）

> 六步流水线主线 B 第 2' 步（B1 补录已复核采纳：第二层缺口 = 批量/单只版分钟 count 查询以
> (anchor, anchor) 单日 range 实现，跨日 count 语义缺失——Phase 4A/PR3 设计性限制）。
> 前置：快照 v2 重拷完成（逐码核对 match：000017@06-30 238 根、尾三根 14:55/56/57 @6.12——
> 与平台 QSPROBE closes=[6.12×3] 逐位对应，验收锚点数据就位）。

## 1. 问题定义
`get_history(count=N, frequency='1m', ..., include=False)` 的正确语义（平台 QSPROBE 定谳）：
**截止当前 bar（cutoff 之前）的最近 N 根分钟 bar，可跨交易日**。现实现（Phase 4A 批量版
L934 / PR3 单只版 L860）以 `iter_trading_days_in_range(anchor, anchor)` 单日窗口实现——
当日时段内 ≤cutoff 的 bar 不足 N 根时返回合法空（应取前交易日尾盘 bar 补足）。

## 2. 两案对比（审核要求；推荐方案 A）
### 方案 A（推荐，主修复）：窗口函数案——每码最近 N 根
- **SQL 形态**（与日线 `query_bars_by_count_batch` 同款已验证模式）：
  `SELECT ... FROM {table} WHERE freq = ? AND time <= ? AND code IN (...)
   QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY time DESC) <= ?`
  （time <= cutoff_ms 天然 PIT；分片沿用 A2 的 200 码/片+超时执行+心跳）
- **优点**：单 SQL、天然 PIT、无交易日枚举依赖（calendar 只在必要时用）、性能可控
  （索引/分区裁剪 time<=cutoff）、与日线批量模式同构（维护性）；
- **改动落点**：duckdb_data_access.py 新增 `query_minute_bars_by_count_batch`（或改造
  批量版语义）；duckdb_provider L94-104 批量分支调用改指新方法；单只版 L860 同语义改造
  （或委托批量版逐码）。
- **语义细节**：cutoff 由既有 include/锚点逻辑传入（ptrade_api L1266-1281 不变）；
  freq 缺失/表缺失的 FrequencyCapabilityError 三分类语义**原样保留**（预检查逻辑复用）。
### 方案 B（对比项，不推荐）：交易日枚举案——向 history 方向枚举交易日直至凑满 N 根
- iter_trading_days_in_range(anchor 回溯 N 个交易日) → 逐日 range 查询合并 tail(N)；
- **缺点**：多次 SQL/多日窗口合并逻辑复杂、calendar 依赖加重、与单只版行为对齐困难、
  性能劣（N 日 × 每日全时段窗口）；仅作对比记录。

## 3. 改动范围与影响面
- duckdb_data_access.py（新 count 批量方法 + 单只版同语义）、duckdb_provider.py（调用改指）、
  ptrade_api.py **零改动**（cutoff 语义已就位）；
- 策略源码零改动；纯增益：count 查询从「合法空」变为「正确跨日返回」——**这正是语义修复
  本体**（非性能优化，属正确性修复，按正确性修复验收）；
- 无关路径隔离：日线/秒级/其他 freq 不触达新方法；include=True 当日路径行为不变（当日
  bar 可见性由 cutoff 控制，语义保持）。

## 4. 验收锚点（钉死，审核口径）
- **锚点主断言**：快照 v2 库上 `get_history(3, frequency='1m', field=['close','preClose'],
  security_list=['000017.SZ'], fq='pre', include=False, is_dict=True)`，
  引擎语境 anchor=2026-07-01 09:31（include=False cutoff=09:30）→
  **期望返回 06-30 的 14:55/14:56/14:57 三根、close=[6.12, 6.12, 6.12]**（与平台
  QSPROBE closes=[6.12×3] 逐位对应）；
- 反断言：include=False 时当日 09:31+ 的 bar **不得泄漏**（PIT 不变式）；
- 五分支矩阵：TABLE_MISSING（指数）/TABLE_EMPTY/FREQ_NOT_IN_TABLE 语义不变（Timestamp
  入参双形态——B2 契约延续）；
- 回归：契约门失败集 ⊆ 钉死清单；149 套件 + 契约套件全绿；黄金门④打板短窗重跑
  （快照 v2 库）——v10.4.1 parity 期望（07-01 筛选链 sentiment=ok candidates 数与
  既有证据一致）。

## 5. 实施与回退
- stash create + **store 持久化**回退点；精确清单 add（duckdb_data_access.py /
  duckdb_provider.py / 新契约测试——duckdb_data_access 叠 A2+B2 在途改动，提交信息
  三方叠加分层写明）；
- 回退条件：①锚点断言不过（跨日取数错位/泄漏当日 bar）；②既有 include=True 当日路径
  行为变化；③FrequencyCapabilityError 语义漂移；④性能回退超阈值（批量单语句 >budget
  触发看门狗误杀——分片+预算联动复核）。

## 6. 与 B3 收口的衔接
B2' 实施+验收通过后：B3 E2E 在快照 v2 上复跑（产物本体驱动 + 门③锚点断言 + 手动
_try_play_window 正向注入触板断言——v15 已就绪）→ B 线结项判据（B3 + 6 策略重转
api_portability）同窗完成 → 与 A 线并窗走用户确认 → 推送。
