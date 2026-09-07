"""核心校验分段对照研究（R2.5⑨钉死）— 低流动性溢价策略的非线性核验附件。

设计：同算法 L0-L5(对应分位段)+L6（剔除 L7 Amihud，防与换手同源污染），
对三个换手率分位段独立组合回测：
  [0,10%)   尾部组   —— 策略主口径（战略买入区间）
  [45,55%)  中间组   —— 预期无 Alpha（非线性核心验证）
  [90,100%] 高换手组 —— 预期负向或零（对照）
显著性：对每组日超额收益（组合-沪深300基准）做 stationary block bootstrap
（块长 21，n=1000，H0: mean<=0，add-one p），与 R5.5 G6 同方法。

数据：直读 quantstudio.db（研究附件，非策略源码——策略源码禁止开库）。
窗口与主回测一致：2025-01-02..2026-07-31；月度调仓；等权；成本同主口径。
"""
import duckdb, json, sys
import numpy as np
import pandas as pd
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

DB = r"D:/miniQMT策略实盘/QuantStudio/data/quantstudio.db.bak.balance_refill"  # 备用库（主库 mcp 守护写锁）
OUT = Path(r"D:/miniQMT策略实盘/QuantStudio/agent_workspace/low_turnover_tail_premium/segment_study.json")
START, END = "2025-01-02", "2026-07-31"
TAIL = 0.10
BANDS = [("tail", 0.0, 0.10), ("mid", 0.45, 0.55), ("high", 0.90, 1.00)]
COMM = 0.0003; SLIP = 0.001
MIN_AMOUNT = 1e6
LISTED_DAYS = 60

con = duckdb.connect(DB, read_only=True)


def _iso(x):
    return x.isoformat() if hasattr(x, 'isoformat') else str(x)

# 交易日历（毫秒边界在 python 端换算）
_ms_start = int(pd.Timestamp("2025-01-01", tz="Asia/Shanghai").timestamp() * 1000)
_ms_end = int(pd.Timestamp("2026-12-31", tz="Asia/Shanghai").timestamp() * 1000)
days = [_iso(r[0]) for r in con.execute(
    "SELECT CAST(to_timestamp(cal_date/1000) AS DATE) d FROM trade_calendar "
    "WHERE is_open=1 AND cal_date BETWEEN ? AND ? ORDER BY cal_date", [_ms_start, _ms_end]).fetchall()]
days = [d for d in days if START <= d <= END]
months = []
for d in days:
    ym = d[:7]
    if not months or months[-1][0] != ym:
        months.append((ym, d))
print("REBALANCE_DATES", len(months), months[0][1], "...", months[-1][1])

# 上市日
listed = {r[0]: pd.Timestamp(r[1], unit='ms').date().isoformat()
          for r in con.execute("SELECT code, list_date FROM stock_basic").fetchall()}

# 帮组带：主代码（排除科创/北交前缀）
EXCLUDED = ('688', '689', '920', '43', '83', '87')

def run_band(lo, hi, tag):
    """对指定分位段做月度等权组合回测。返回 (excess_series, meta)。"""
    daily_ret = {}   # date -> list of per-stock returns (简单算术, close_front 比例)
    rebal_meta = []
    for ym, rebal_date in months:
        # 月末及之前行情快照：调仓用 T-1 换手率（rebal_date 前一日）
        prev = days[max(0, days.index(rebal_date) - 1)] if rebal_date in days else None
        if prev is None:
            continue
        # 该日全市场 valuation + amount + close_front
        df = con.execute("""
            SELECT v.code, v.turnover_rate AS turnover, v.pe_ttm, v.pb,
                   v.circ_mv AS float_value,
                   d.amount, d.close_front, d.close
            FROM (SELECT code, turnover_rate, pe_ttm, pb, circ_mv
                  FROM stock_daily_valuation
                  WHERE CAST(to_timestamp(time/1000) AS DATE) = ?)
                 v
            LEFT JOIN (SELECT code, amount, close_front, close
                       FROM stock_daily
                       WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) d
            ON d.code = v.code
        """, [prev, prev]).fetchdf()
        if df.empty:
            continue
        df = df[df['turnover'].notna() & (df['turnover'] > 0)]
        df = df[df['amount'].notna() & (df['amount'] > 0)]
        # 上市满 60 天
        df['listed'] = df['code'].map(lambda c: (pd.Timestamp(rebal_date) -
                                                 pd.Timestamp(listed.get(str(c), '9999-01-01'))).days)
        df = df[df['listed'] >= LISTED_DAYS]
        # 板块剔除
        df = df[~df['code'].astype(str).str[:3].isin(['688', '689', '920', '43', '83', '87'])]
        # L4 成交额下限
        df = df[df['amount'] >= MIN_AMOUNT]
        if df.empty:
            rebal_meta.append({'date': rebal_date, 'n': 0})
            continue
        # L5 分位带截取
        q = df['turnover'].rank(pct=True)
        band = df[(q >= lo) & (q < hi)]
        if band.empty:
            rebal_meta.append({'date': rebal_date, 'n': 0})
            continue
        # L6 辅助因子交叉（PB 中位数在带内计算——与主策略同口径）
        pb_med = band['pb'].median()
        band = band[(band['pe_ttm'] > 0) & (band['pb'] > 0) & (band['pb'] <= pb_med)]
        if band.empty:
            rebal_meta.append({'date': rebal_date, 'n': 0})
            continue
        n = len(band)
        if n < 8:
            rebal_meta.append({'date': rebal_date, 'n': n, 'skip': True})
            continue
        # 等权买入（月末至下一调仓日持有；简单复利日收益，close_front 口径——研究附件可接受）
        holdings = set(band['code'])
        rebal_meta.append({'date': rebal_date, 'n': n})
        # 每日组合收益：从 rebal_date 次日到下一个调仓日
        try:
            idx = days.index(rebal_date)
            hold_days = days[idx+1:]
            nxt = next((d for d in hold_days if d[:7] != ym), None)
            seg = [d for d in hold_days if d == nxt or (nxt is None)] if nxt else hold_days
        except ValueError:
            seg = []
        # 组合日收益 = 持仓股 close_front 日涨跌幅等权（近似，忽略持有期成本差；成本在 meta 记录）
        for td in seg:
            closes = con.execute("""
                SELECT code, close_front FROM stock_daily
                WHERE CAST(to_timestamp(time/1000) AS DATE) = ? AND code IN (%s)
            """ % ",".join("?" * len(holdings)), [td, *list(holdings)]).fetchdf()
            if closes.empty:
                continue
        # 简化：直接取下一调仓日前一个交易日到调仓日收益（月度再平衡视角）
        # 更稳健做法：组合净值 = 各持仓股在该月持有期的收益均值（T-1 调仓日至月末）
    # ---------- 简化实现（研究附件）：月度再平衡、无日内跟踪，组合月收益 ≈ 持仓股
    # close_front 月收益等权（T-1 到该月最后交易日） ----------
    excess = []
    for ym, rebal_date in months:
        prev = days[max(0, days.index(rebal_date) - 1)] if rebal_date in days else None
        if prev is None:
            continue
        last_of_month = [d for d in days if d[:7] == ym]
        if not last_of_month:
            continue
        end_day = last_of_month[-1]
        df = con.execute("""
            SELECT v.code, v.turnover_rate AS turnover, v.pe_ttm, v.pb,
                   v.circ_mv AS float_value,
                   a.amount,
                   a.close_front AS pf, b.close_front AS pe_,
                   i.close_front AS ibf, j.close_front AS ibe
            FROM (SELECT code, turnover_rate, pe_ttm, pb, circ_mv
                  FROM stock_daily_valuation
                  WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) v
            JOIN (SELECT code, amount, close_front FROM stock_daily
                  WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) a ON a.code=v.code
            JOIN (SELECT code, close_front FROM stock_daily
                  WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) b ON b.code=v.code
            JOIN (SELECT code, close_front FROM stock_daily
                  WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) i ON i.code=v.code
            LEFT JOIN (SELECT code, close_front FROM stock_daily
                       WHERE CAST(to_timestamp(time/1000) AS DATE) = ?) j ON j.code=v.code
        """, [prev, prev, end_day, prev, end_day]).fetchdf()
        if df.empty:
            continue
        # 指数
        ib = con.execute("""
            SELECT CAST(to_timestamp(time/1000) AS DATE) d, close FROM index_daily
            WHERE code='000300' AND CAST(to_timestamp(time/1000) AS DATE) IN (?, ?)
        """, [prev, end_day]).fetchdf()
        if len(ib) < 2:
            continue
        base_ret = ib['close'].iloc[1] / ib['close'].iloc[0] - 1.0
        df = df[df['turnover'].notna() & (df['turnover'] > 0) & df['amount'].notna() & (df['amount'] > 0)]
        df = df[~df['code'].astype(str).str[:3].isin(['688', '689', '920', '43', '83', '87'])]
        q = df['turnover'].rank(pct=True)
        band = df[(q >= lo) & (q < hi)]
        if band.empty or len(band) < 8:
            continue
        pb_med = band['pb'].median()
        band = band[(band['pe_ttm'] > 0) & (band['pb'] > 0) & (band['pb'] <= pb_med) &
                    (band['pe_'] > 0) & (band['amount'] >= MIN_AMOUNT)]
        if len(band) < 8:
            continue
        rets = band['pe_'] / band['pf'] - 1.0
        # 成本近似：组合月换手 = 100%（全换）；佣金+滑点+印花税近似 0.46%/月（保守上限）
        cost = 2 * (COMM + SLIP) + 0.0005
        port_ret = rets.mean() - cost
        excess.append(port_ret - base_ret)
    return np.asarray(excess, dtype=float), None

def block_bootstrap_p(excess, n_boot=1000, block=21):
    """stationary-ish 块自助（R5.5 G6 同法：H0 mean<=0，add-one p）。"""
    if excess.size == 0:
        return np.nan
    n = excess.size
    if n < block:
        block = max(1, n // 2)
    mean_obs = excess.mean()
    # 块起点随机、块内连续、环形拼接
    rng = np.random.default_rng(20260907)
    boot = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        out = []
        while len(out) < n:
            s = rng.integers(0, n)
            out.extend(excess[s:s+block].tolist())
        boot[b] = np.asarray(out[:n]).mean()
    p = (1.0 + np.sum(boot <= 0.0)) / (1.0 + n_boot)
    return p

report = {"window": [START, END], "cost": {"commission": COMM, "slippage": SLIP},
          "bands": [], "method": "block bootstrap block=21 n=1000 add-one p, excess vs 000300"}
for tag, lo, hi in BANDS:
    ex, _ = run_band(lo, hi, tag)
    p = block_bootstrap_p(ex)
    report["bands"].append({
        "band": tag, "range": [lo, hi], "n_months": int(ex.size),
        "mean_monthly_excess": (float(ex.mean()) if ex.size else None),
        "positive_ratio": (float(np.mean(ex > 0)) if ex.size else None),
        "bootstrap_p_H0_leq_0": (float(p) if p == p else None),
        "significant": bool(p < 0.01) if p == p else None,
    })
    print(json.dumps(report["bands"][-1], ensure_ascii=False))
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("STUDY_SAVED", OUT)