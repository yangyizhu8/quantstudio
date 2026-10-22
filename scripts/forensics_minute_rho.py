"""ρ 取证（H1/H2 判定）：000726/000729/000563 分钟 raw 的 fresh/db 四列比值谱。

库内侧 = data/backup/quantstudio.db（50.54GB，2.1 态快照，**只读**，不触运行库）
fresh 侧 = MCP 现拉 1min（export_cache 关，不写主库）

输出：各列 ρ 的中位数/分位数，以及 ρ_high 相对 ρ_open 的偏离量级：
  · 1e-3~1e-1 ⇒ H1（high 列口径缺陷）
  · 1e-9~1e-7 ⇒ H2（浮点噪声，E1 容差过严）
"""

import io
import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd

REPO = r"D:\miniQMT策略实盘\QuantStudio"
sys.path.insert(0, REPO)
BK = REPO + r"\data\backup\quantstudio.db"
CODES = ["000726", "000729", "000563"]
RAW = ["open", "high", "low", "close"]


def rel_stats(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {}
    return {"n": int(x.size), "median": float(np.median(x)),
            "p05": float(np.quantile(x, 0.05)), "p95": float(np.quantile(x, 0.95)),
            "max_abs": float(np.max(np.abs(x)))}


def main():
    con = duckdb.connect(BK, read_only=True)
    cfg = json.load(io.open(REPO + r"\config\profiles\mcp_only\sources_config.json",
                            encoding="utf-8"))
    blk = None
    for s in (cfg.get("sources") or []):
        if isinstance(s, dict) and (s.get("name") == "mcp" or s.get("type") == "mcp"):
            blk = s
            break
    mc = dict(blk or {})
    mc["export_cache"] = False
    from quantstudio.pipeline.qfq_fresh_capture import McpFreshFetcher
    f = McpFreshFetcher(mcp_cfg=mc, main_db=None)

    for code in CODES:
        row = con.execute(
            "select min(time), max(time), count(*) from stock_minutes "
            "where code=? and freq='1min'", [code]).fetchone()
        if not row or not row[2]:
            print(f"{code}: 库内无 1min 数据"); continue
        tmax = int(row[1])
        # 取最近 3 个自然日窗口的分钟行（足够 700+ 行）
        t_lo = tmax - 3 * 86400 * 1000
        db = con.execute(
            "select time, open, high, low, close from stock_minutes "
            "where code=? and freq='1min' and time>=? order by time", [code, t_lo]).fetchdf()
        if len(db) < 50:
            print(f"{code}: 窗口行数过少 {len(db)}"); continue
        s = pd.Timestamp(int(db.time.min()), unit="ms", tz="Asia/Shanghai")
        e = pd.Timestamp(int(db.time.max()), unit="ms", tz="Asia/Shanghai")
        try:
            none_df, _ = f.fetch_none_front(
                "STOCK", code, "1m", s.strftime("%Y%m%d"), e.strftime("%Y%m%d"))
        except Exception as ex:
            print(f"{code}: fresh 拉取失败 {type(ex).__name__}: {str(ex)[:120]}"); continue
        if none_df is None or len(none_df) == 0:
            print(f"{code}: fresh 空"); continue
        fr = none_df.reset_index()
        fr.columns = ["ts"] + list(fr.columns[1:])
        fr["time"] = fr["ts"].map(lambda x: int(pd.Timestamp(x).value // 10**6))
        m = fr.merge(db, on="time", suffixes=("_f", "_b"))
        if len(m) < 50:
            print(f"{code}: join 行数过少 {len(m)}（fresh={len(fr)}, db={len(db)}）"); continue

        rhos = {}
        for c in RAW:
            with np.errstate(divide="ignore", invalid="ignore"):
                r = (m[f"{c}_f"].to_numpy(float) / m[f"{c}_b"].to_numpy(float))
            rhos[c] = r
        print(f"\n===== {code} join={len(m)} 窗 {s:%Y-%m-%d %H:%M}~{e:%Y-%m-%d %H:%M} =====")
        for c in RAW:
            st = rel_stats(rhos[c])
            if st:
                print(f"  rho_{c:6s} median={st['median']:.12g} p05={st['p05']:.12g} "
                      f"p95={st['p95']:.12g} max|ρ-1|={st['max_abs']:.3e}")
        # 关键：ρ_high vs ρ_open 的偏离量级
        base = rhos["open"]
        ok = np.isfinite(base) & (base != 0)
        for c in ["high", "low", "close"]:
            d = np.abs(rhos[c][ok] / base[ok] - 1.0)
            st = rel_stats(d)
            if st:
                print(f"  |rho_{c}/rho_open - 1|: median={st['median']:.3e} "
                      f"p95={st['p95']:.3e} max={st['max_abs']:.3e}  → "
                      f"{'H1(口径差)' if st['median'] > 1e-6 else 'H2(浮点级)'}")
    con.close()


if __name__ == "__main__":
    main()
