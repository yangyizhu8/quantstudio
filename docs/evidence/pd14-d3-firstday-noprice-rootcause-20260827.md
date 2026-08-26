# P-D14 根因证据：D3 首日 ETF no_price 拒单（2026-08-27）

- 关联：master-plan WP-D（D3 首日修复）；tech_etf 07-01 `rejected_detail=[515050.SH:no_price,511260.SH:no_price]`（本地端空仓过第一周）
- 结论强度：**DB 级确证**（etf_daily time 戳双值不一致）

## 1. 现象

tech_etf 本地回测首日（2026-07-01 15:00）两笔买单全部被拒：
```
QS_FILL_AUDIT date=2026-07-01 ... buy_rejected=1 rejected_detail=[515050.SH:no_price]
```
而 ptrade 平台当日成交 33,900 股@1.334——两端首日建仓分岔（-10% 行情本地缺席）。

## 2. 根因（DB 实证）

引擎 `duckdb_provider._start_ms('2026-07-01')` = `pd.Timestamp('2026-07-01', tz='Asia/Shanghai')` = **1782835200000**（当日 00:00:00+08）。

`query_daily_snapshot` 用 `WHERE time = {_start_ms}` **精确匹配**。实测 etf_daily 07-01 的 time 分布：

| time 值 | ms | 行数 | 含义 |
|---|---|---|---|
| 2026-07-01 00:00:00+08 | 1782835200000 | 73 | 部分 ETF（采集管线正常） |
| **2026-07-01 08:00:00+08** | **1782864000000** | **1974** | **大部分 ETF（含 515050/511260）——时刻戳错误** |

515050 07-01 行：`time_ms=1782864000000 (07-01 08:00) close=1.334`。

- `time = 1782835200000` 精确匹配 → **08:00 组的 1974 只 ETF 全部查不到** → `_build_match_prices` 无价 → `_immediate_execute` no_price 拒单；
- stock_daily 07-01 统一 `time=1782835200000`（5511 行）→ 股票无此问题；
- 对照 etf_daily 06-30/07-02：`1782748800000 (06-30 00:00)` / `1782921600000 (07-02 00:00)`——**07-01 特有 08:00 异常值**（数据采集管线在 07-01 8 点时刻写入当天数据并错误落 time=8:00）。

## 3. 影响链

```
etf_daily 07-01 部分行 time=08:00（采集管线时区/时刻异常）
  → query_daily_snapshot(time=00:00 精确匹配) 漏掉 08:00 组
  → _build_match_prices 缺 515050 等 → curr_data 无价
  → _immediate_execute price<=0 → no_price 拒单
  → 本地首日 ETF 策略空仓
```

## 4. 修复方向（D3 设计要点）

1. **引擎层（容错）**：`query_daily_snapshot` 的精确匹配改为**当日窗口**（`time BETWEEN 当日00:00 AND 次日00:00` 或 `time >= 当日00:00 AND time < 次日00:00`），吸收 00:00/08:00 双值；
2. **数据层（根治）**：etf_daily 采集管线统一 time 为当日 00:00（数据管线修复——登记移交，非引擎侧）；
3. **兼容性**：`preload_daily_snapshots` 的预取查询（`time BETWEEN start_ms AND end_ms`）已用窗口匹配——**预取路径无此问题**，仅 per-day 精确匹配路径（`query_daily_snapshot` 单日查询）受影响。修复须保证预取与单日路径字节级一致（现有注释声明此契约）。

## 5. 遗留登记

- 单日查询 `time = X` 与预取 `time BETWEEN` 的契约差异（修窗口后须保持两者一致）；
- 采集管线 07-01 08:00 时刻异常写入的根本修复（数据管线，非本 WP）；
- D3 默认修复类（正确性问题）——非保真开关。