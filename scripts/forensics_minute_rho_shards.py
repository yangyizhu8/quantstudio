"""ρ 取证（离线分片路径 v2）：一次扫描取三码，再与库快照 join 算 ρ。

v1 问题：对每个 code 各读一遍 7832 个分片（3 遍全扫）→ 过慢。
v2：单次 read_parquet 带 IN 谓词取三码，再按码分组比较。

数据源：
  · fresh 侧 = daemon 自己导出的 data/mcp_landing/exp_stock_minutes_*/（含 adj_factor、is_qfq）
  · 库内侧 = data/backup/quantstudio.db（50.54GB 2.1 态快照，只读）

判据：|ρ_c/ρ_open − 1| 中位数 1e-3~1e-1 ⇒ H1/H3（口径差，真缺陷）；1e-9~1e-7 ⇒ H2（浮点，E1 容差过严）。
"""

import glob
import sys

import duckdb
import numpy as np
import pandas as pd

REPO = r"D:\miniQMT策略实盘\QuantStudio"
BK = REPO + r"\data\backup\quantstudio.db"
CODES = ["000726", "000729", "000563"]
RAW = ["open", "high", "low", "close"]
SHARD_GLOB = (REPO + r"\data\mcp_landing\exp_stock_minutes_*\*.parquet").replace("\\", "/")


def stat(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return dict(n=int(x.size), median=float(np.median(x)),
                p95=float(np.quantile(x, 0.95)), mx=float(np.max(x)))


def main():
    print(f"[rho] 分片文件数={len(glob.glob(SHARD_GLOB))}")
    con = duckdb.connect(BK, read_only=True)
    where = " OR ".join([f"ts_code LIKE '{c}%'" for c in CODES])
    print("[rho] 单次扫描三码…")
    f = con.execute(
        f"SELECT ts_code, trade_time, freq, open, high, low, close, adj_factor, is_qfq "
        f"FROM read_parquet('{SHARD_GLOB}', union_by_name=true) WHERE {where}"
    ).fetchdf()
    print(f"[rho] 分片命中行={len(f)}")
    if f.empty:
        return
    f["code"] = f["ts_code"].astype(str).str.split(".").str[0]
    # 时间口径（实测确认）：库内 time = 分片 trade_time 视作 **CST 墙钟** 后转 UTC 的毫秒。
    # 例：trade_time '2026-01-05 09:30:00' → 1767576600000（与库内 exacter 相等）。
    _tt = pd.to_datetime(f["trade_time"])
    if getattr(_tt.dt, "tz", None) is None:
        _tt = _tt.dt.tz_localize("Asia/Shanghai")
    f["time"] = _tt.dt.tz_convert("UTC").astype("int64") // 10**6

    for code in CODES:
        sub = f[f["code"] == code]
        db = con.execute(
            "SELECT time, open, high, low, close FROM stock_minutes "
            "WHERE code=? AND freq='1min'", [code]).fetchdf()
        print(f"\n===== {code} 分片={len(sub)} 库={len(db)} =====")
        if sub.empty or db.empty:
            continue
        if "is_qfq" in sub.columns:
            print("  is_qfq 取值:", pd.Series(sub["is_qfq"]).dropna().unique()[:4],
                  " adj_factor 唯一值数:", pd.Series(sub["adj_factor"]).nunique())
        m = sub.merge(db, on="time", suffixes=("_f", "_b"))
        print(f"  join={len(m)}")
        if len(m) < 20:
            continue
        rh = {}
        miss = [c for c in RAW if f"{c}_f" not in m.columns or f"{c}_b" not in m.columns]
        if miss:
            print("  缺列:", miss); continue
        for c in RAW:
            with np.errstate(divide="ignore", invalid="ignore"):
                rh[c] = m[f"{c}_f"].to_numpy(float) / m[f"{c}_b"].to_numpy(float)
            st = stat(rh[c])
            if st:
                print(f"  rho_{c:6s} n={st['n']:6d} median={st['median']:.12g} "
                      f"p95={st['p95']:.12g}")
        base = rh["open"]
        ok = np.isfinite(base) & (base != 0)
        for c in ["high", "low", "close"]:
            d = np.abs(rh[c][ok] / base[ok] - 1.0)
            st = stat(d)
            if st:
                med = st["median"]
                verdict = ("H1/H3 口径差" if med > 1e-6
                           else ("H2 浮点级" if med <= 1e-7 else "交界"))
                print(f"  |rho_{c}/rho_open-1| n={st['n']} median={med:.3e} "
                      f"p95={st['p95']:.3e} max={st['mx']:.3e} → {verdict}")
    con.close()


if __name__ == "__main__":
    main()
