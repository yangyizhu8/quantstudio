"""E5 阈值标定：按 BJ 日统计 |Δρ| 的中位数/标准差/价格/折合 tick 数。

目的：为「整段系统性偏移检测」定阈值（k / floor / 是否含 tick 相对项），
并判定 000563 尾部 +0.113% 究竟属「锚差异（H3，>1 tick 绝对金额）」还是「舍入偏置（≤1 tick）」。

数据源：daemon 导出分片（fresh 侧）× data/backup/quantstudio.db（库侧，2.1 态快照，只读）。
"""

import glob
import sys
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd

REPO = r"D:\miniQMT策略实盘\QuantStudio"
BK = REPO + r"\data\backup\quantstudio.db"
CODES = ["000726", "000729", "000563"]
TICK = {"STOCK": 0.01, "ETF": 0.001}
BJ = timezone(timedelta(hours=8))
SHARD_GLOB = (REPO + r"\data\mcp_landing\exp_stock_minutes_*\*.parquet").replace("\\", "/")


def main():
    con = duckdb.connect(BK, read_only=True)
    where = " OR ".join([f"ts_code LIKE '{c}%'" for c in CODES])
    f = con.execute(
        f"SELECT ts_code, trade_time, open, high, low, close, adj_factor, is_qfq "
        f"FROM read_parquet('{SHARD_GLOB}', union_by_name=true) WHERE {where}"
    ).fetchdf()
    f["code"] = f["ts_code"].astype(str).str.split(".").str[0]
    _tt = pd.to_datetime(f["trade_time"])
    if getattr(_tt.dt, "tz", None) is None:
        _tt = _tt.dt.tz_localize("Asia/Shanghai")
    f["time"] = _tt.dt.tz_convert("UTC").astype("int64") // 10**6
    f["day"] = [datetime.fromtimestamp(t / 1000.0, BJ).strftime("%Y-%m-%d")
                for t in f["time"].tolist()]

    for code in CODES:
        sub = f[f["code"] == code]
        db = con.execute(
            "SELECT time, open, high, low, close FROM stock_minutes "
            "WHERE code=? AND freq='1min'", [code]).fetchdf()
        if sub.empty or db.empty:
            print(f"{code}: 无数据")
            continue
        m = sub.merge(db, on="time", suffixes=("_f", "_b"))
        if len(m) < 50:
            print(f"{code}: join 太少 {len(m)}")
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            r_open = m["open_f"].to_numpy(float) / m["open_b"].to_numpy(float)
            r_high = m["high_f"].to_numpy(float) / m["high_b"].to_numpy(float)
            r_close = m["close_f"].to_numpy(float) / m["close_b"].to_numpy(float)
        delta = (r_high / r_open) - 1.0
        price = np.abs(m["high_b"].to_numpy(float))
        ok = np.isfinite(delta) & np.isfinite(r_open) & (r_open != 0)
        df = pd.DataFrame({"day": m["day"].to_numpy()[ok], "delta": delta[ok],
                           "price": price[ok]})
        g = df.groupby("day").agg(n=("delta", "size"), med=("delta", "median"),
                                  std=("delta", "std"), price=("price", "median"))
        g = g[g["n"] >= 30]
        g["abs_med"] = g["med"].abs()
        g["conc"] = g["std"] / g["abs_med"].replace(0, np.nan)
        g["amt_tick"] = g["abs_med"] * g["price"] / TICK["STOCK"]
        print(f"\n===== {code}  join={len(m)}  有样本天数={len(g)} =====")
        print(f"  |med| 分位: p50={g.abs_med.quantile(.5):.3e} p95={g.abs_med.quantile(.95):.3e} "
              f"p99={g.abs_med.quantile(.99):.3e} max={g.abs_med.max():.3e}")
        print(f"  折合 tick 数: p50={g.amt_tick.quantile(.5):.3f} p95={g.amt_tick.quantile(.95):.3f} "
              f"p99={g.amt_tick.quantile(.99):.3f} max={g.amt_tick.max():.3f}")
        print(f"  集中度 std/|med|: p50={g.conc.quantile(.5):.3f} p95={g.conc.quantile(.95):.3f}")
        top = g.sort_values("abs_med", ascending=False).head(5)
        print("  |med| 最大的 5 天:")
        for day, row in top.iterrows():
            print(f"    {day} n={int(row.n):4d} med={row.med:+.3e} std={row['std']:.3e} "
                  f"conc={row.conc:.4f} price={row.price:.3f} |med|·price/tick={row.amt_tick:.3f}")
        # 候选：|med| 显著 & 集中 & ≤1e-2 的天数
        cand = g[(g.abs_med > 3e-4) & (g.conc <= 0.25) & (g.abs_med <= 1e-2)]
        print(f"  E5 候选天数(|med|>3e-4, conc≤0.25, ≤1e-2): {len(cand)}")
        if len(cand):
            print("   ", cand.sort_values("abs_med", ascending=False).head(3)[
                ["n", "med", "std", "conc", "amt_tick"]].to_string())
    con.close()


if __name__ == "__main__":
    main()
