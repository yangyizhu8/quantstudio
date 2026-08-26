# P-D14 设计：D3 首日 ETF no_price 修复（WP-D，2026-08-27）

- **流水线状态**：Step 1 方案（本文件）→ 根因证据 `docs/evidence/pd14-d3-firstday-noprice-rootcause-20260827.md`（DB 确证：etf_daily 07-01 双 time 值）→ 待审计 → 实施 → 验收 → 用户确认 → 双仓库推送
- **D3 定性**：默认修复类（正确性问题，非保真开关——首日有行情却撮合无价 = 数据匹配缺陷）
- **⚠️ 前置风险（审计提醒②）**：改动落在 `duckdb_data_access.py::query_daily_snapshot`（数据访问层），**非 backtest_engine.py**——与他线 11 hunk（backtest_engine）无交集 ✓；但需确认 `duckdb_data_access.py` 自身是否有他线未提交改动

---

## 1. 问题定义

### 1.1 现象

tech_etf 本地首日（07-01）两笔 ETF 买单全部 `no_price` 拒单（515050/511260），而 ptrade 平台当日成交——本地空仓过第一周（-10% 行情缺席），tech_etf 收益差 -1.37pp 的最大残余归因。

### 1.2 根因（DB 确证）

`etf_daily` 07-01 部分行（1974 只，含 515050/511260）`time = 2026-07-01 08:00:00`（ms 1782864000000），而引擎单日查询 `query_daily_snapshot` 用 `WHERE time = {_start_ms('2026-07-01')}`（= 07-01 00:00 = 1782835200000）**精确匹配** → 08:00 组 ETF 全部查不到 → `_build_match_prices` 无价 → no_price 拒单。`stock_daily` time 统一 00:00 → 股票无此问题。`preload_daily_snapshots`（`time BETWEEN` 窗口）无此问题——**仅 per-day 精确匹配路径受影响**。

## 2. 改动范围

### 2.1 核心修复：`duckdb_data_access.py::query_daily_snapshot`

单日查询的精确匹配改为**当日窗口匹配**（与预取路径 `time BETWEEN` 对齐）：

```python
def query_daily_snapshot(self, date_ms: int) -> pd.DataFrame:
    # D3（P-D14）：单日查询用当日窗口 [date_ms, date_ms + 86_399_999] 吸收
    # etf_daily 双 time 值（07-01 有 00:00/08:00 两组）——与 preload 的
    # BETWEEN 窗口契约一致，保证两路径字节级一致。
    cached = self._daily_snapshot_cache.get(date_ms)
    if cached is not None:
        return cached.copy()
    ...
    df = conn.execute(
        self._snapshot_sql(f"time >= {date_ms} AND time <= {date_ms + 86_399_999}")
    ).fetchdf()
```

**关键考虑**：
- 缓存键保持 `date_ms`（当日 00:00）——窗口匹配结果缓存在 date_ms 键下，语义不变；
- `_snapshot_sql` 的 `where_clause` 参数本身支持任意条件（预取已用 BETWEEN）——复用即可；
- **兼容性**：08:00 组的 close 是**当日收盘价**（515050 07-01 close=1.334 = ptrade 成交价）→ 窗口匹配返回的 ETF 行与平台撮合价一致 ✓；
- **边界**：`date_ms + 86_399_999` 含 23:59:59.999——08:00 在其内 ✓，次日 00:00（1782921600000）恰好超出 1ms → 不会串日 ✓。

### 2.2 数据层根治（登记移交，非本 WP）

etf_daily 采集管线统一 time 为当日 00:00（07-01 08:00 异常写入的系统性修复——数据管线侧，登记 P-D14b）。

### 2.3 涉及文件

| 文件 | 改动 | 他线 hunk 交集 |
|---|---|---|
| `quantstudio/backtest/providers/duckdb_data_access.py` | query_daily_snapshot 窗口匹配（1 处条件） | **待查**（审计提醒②） |
| `tests/test_d3_firstday_noprice.py`（新） | 测试矩阵（§5） | — |
| 证据/设计文档 | 根因报告 + 本文件 | — |

**不改**：backtest_engine.py（根因不在引擎）；etf_daily 数据本身（数据管线 P-D14b）。

## 3. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 窗口 = `[date_ms, date_ms + 86_399_999]` | 与预取 BETWEEN 对齐；08:00 在内、次日 00:00 恰好排除（差 1ms） |
| D2 | 缓存键不变（date_ms） | 窗口结果缓存在当日键下，语义无变化 |
| D3 | 不动 `_snapshot_sql` 结构 | where_clause 已是任意条件注入点，复用即可 |
| D4 | 数据管线根治拆 P-D14b | 引擎容错先上（正确性），数据源修复后续（管线纪律） |
| D5 | 默认修复（非保真开关） | 首日有行情却撮合无价 = 数据匹配缺陷（非语义选择） |

## 4. 影响面

- **受益**：所有首日交易的 ETF 策略（tech_etf 直接受益——首日建仓恢复）；含 ETF 的在首日买入的股票+ETF 混合策略；
- **行为变化**：首日 ETF 订单从拒单变为成交——**纳入合并基线重验**（P-A3+B2+D2+D3 统一，D3 为四元之一）；
- **风险**：窗口匹配若返回多 time 值组的重复行（00:00 组 + 08:00 组同 code）→ 快照中同一 ETF 双行 → 下游取第一行？需在测试中验证去重。实测 515050 仅在 08:00 组（不在 00:00 组）→ 无双行风险；但需证明**所有 ETF 不跨组重复**（测试 T4）。

## 5. 测试矩阵（tests/test_d3_firstday_noprice.py）

| 用例 | 场景 | 断言 |
|---|---|---|
| T1 | mock `_snapshot_sql` 接收窗口条件 | 单日查询条件为 `time BETWEEN date_ms AND date_ms+86399999` |
| T2 | DB 实证：query_daily_snapshot('2026-07-01') 含 515050 | close=1.334（08:00 组行被窗口吸收） |
| T3 | 边界：query_daily_snapshot 次日不含 07-01 | 无 08:00 组漏入次日 |
| T4 | 去重：任一 ETF 在 00:00/08:00 双组 | 全库扫描无重复（数据管线上限）+ 若有则快照去重策略 |
| T5 | 首日 ETF 策略 smoke | 07-01 买入成交（非 no_price 拒单） |
| T6 | 预取/单日两路径字节级一致 | preload 后 query vs 直接 query 结果相等 |

## 6. 验收标准

1. T1~T6 全绿；
2. tech_etf 本地重跑：07-01 买入 33,900 股@1.334 成交（与 ptrade 一致），首日不再空仓；
3. 全量套件除已知存量红外零新增；
4. 合并基线重验纳入（四元 D3）。

## 7. 回退

- stash create -u 回退点；
- 单点改动（条件字符串）——回退 = 还原一行；
- 若窗口匹配暴露既有数据重复行问题 → 回退并转数据管线治理。

## 8. 明确不做

- 不改 etf_daily 数据（P-D14b 数据管线）；
- 不改 backtest_engine.py；
- 不改 preload 路径（已正确）；
- 不处理 06-30 之前的 etf_daily time 历史异常（数据管线全覆盖）。