# P-D14 实施与验收证据：D3 首日 ETF no_price 修复（WP-D，2026-08-27）

- 流水线：Step 1 ✅（根因 DB 实证→设计）→ Step 2 ✅ 审计通过（两细化）→ Step 3 实施 → **Step 4 验收**
- 回退点：`b8718ccf3121683cee430623543d95398517de03`（baseline-wpd-20260827）

## 1. 实施清单

| 文件 | 改动 |
|---|---|
| `quantstudio/backtest/providers/duckdb_data_access.py` | ①`query_daily_snapshot` 单日精确匹配 → **当日窗口匹配**（`time BETWEEN date_ms AND date_ms+86_399_999`，与 preload BETWEEN 契约对齐）+ **同 code 去重护栏**（sort by time + groupby tail(1) 取最大 time 行，审计细化①）；②`preload_daily_snapshots` **缓存键按当日窗口聚合**（08:00 组并入当日 00:00 键——**T6 实证发现原实现破坏"单日/预取字节级一致"契约**：预取 5584 行 vs 单日 7558 行，修复后一致） |
| `tests/test_d3_firstday_noprice.py`（新） | T1~T6 矩阵 |

**实施中发现并修复的第二处 bug**：预取缓存原按 `time` 精确值分组（08:00 组独立键），单日查询（当日 00:00 键）命中不到 08:00 组 → **预取与单日路径字节级不一致（既有缺陷，T6 暴露）**。修复为按 `_day = time//86400000*86400000` 聚合键 + 去重护栏。

## 2. 验收结果

| 项 | 结果 |
|---|---|
| P-D14 矩阵 | **6/6 全绿**（T1 窗口条件 / T2 含 515050@1.334 / T3 边界不串日 / T4 零双 time 重复 / T5 去重取最大 time / T6 预取-单日一致） |
| 数据层相关回归 | duckdb_data_access_caching + etf_cash_dividends 等全绿 |
| 八套件合跑 | **180/180 全绿** |
| **tech_etf 本地重跑** | **07-01 买 33,900 股 @1.334 成交（与 ptrade 逐位一致！）**——no_price 拒单消除 |

### tech_etf 首日对齐（决定性）

```
D3 前: 2026-07-01 rejected_detail=[515050.SH:no_price]  → 空仓过第一周
D3 后: 2026-07-01 buy 33900@1.334                       → 与 ptrade 平台完全一致 ✅
```

## 3. 审计细化②：同型消费者扫描（"仅此一处"证明）

扫描 `quantstudio/backtest/providers/duckdb_data_access.py` 全部 `time = 精确匹配`：

| 位置 | 消费者 | 涉及表 | 风险 |
|---|---|---|---|
| L301 `query_daily_snapshot` | 引擎单日快照 | **etf_daily（UNION）+ stock_daily** | **高危 → 本 WP 修复** |
| L1874 `query_daily_for_status` | filter_stock_by_status/check_limit/get_stock_status（**预留 Phase 2，当前不被直接调用**） | 仅 stock_daily（无 etf UNION） | 低危（stock time 统一；且未启用）→ 登记不改 |
| L254/1454/1514/1760/1846/1857 | stock_daily / index 精确匹配 | 仅 stock_daily / index_constituents | 安全（无 etf 双值） |

**结论：`query_daily_snapshot` 是唯一消费 etf_daily 的单日精确匹配路径——“仅此一处”✔**。`query_daily_for_status` 仅 stock_daily + 未启用 → 登记为非高危，不改（若 Phase 2 启用时需复审）。

## 4. 数据域 known issue 登记（审计细化②）

- **K-001（etf_daily 时刻戳异常）**：2026-07-01 `etf_daily` 1974 行（含 515050/511260）`time=08:00:00+08`（应为 00:00）——数据采集管线时刻戳异常写入。引擎侧窗口匹配已容错；**数据管线侧根治登记 P-D14b**（统一 time 为当日 00:00）。
- **K-002（总结）**：etf_daily 历史其他日期是否还有非 00:00 time 值——本验证据 07-01/06-30/07-02 抽查未见，全量扫描放 P-D14b 数据管线验收。

## 5. 合并基线重验关联

D3 落地 = **合并基线重验窗口开启**（master-plan §5：P-A3 + B2 + D2 + D3 四元统一双跑 + 归因分解）——本地 tech_etf 收益 -17.95%（07 月）与 D3 前 -14.13% 的差异 = 首日建仓（-10% 行情)的直接影响，四元归因分解时单列。

## 6. 回退

- 回退点 `b8718cc`；
- 改动 = 1 tracked（duckdb_data_access.py）+ 1 新增测试——回退 = 定向 restore + 删测试；
- 窗口匹配 + 预取聚合是数据访问层内部实现（缓存键/匹配语义），无外部契约变更。