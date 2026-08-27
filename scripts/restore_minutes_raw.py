# -*- coding: utf-8 -*-
"""分钟污染全市场扫描 + 还原（dry-run 验证 + apply 两段式）。

原理：污染行 = 被 qfq/复权因子放大的分钟价格；raw = polluted × adj_row / adj_latest。
（600519 实证：1301.18 × 8.4464/8.6463 = 1271.1 = 日线；510500: 8.931 × 0.334/0.3401 = 8.77）
因子来源：qfq_aux_mcp_gen1.db（adj_factor 股票 / fund_adj ETF）。
判定：仅还原「收盘 bar 与日线不一致(>0.1%)、且还原后与日线一致(<=0.1%)」的污染日（整日所有 bar）；
不污染的行/日一律不动。异常日（还原后仍不一致）仅报告，不修改。
--apply 才写库；默认 dry-run。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb
import sqlite3
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"
AUX = ROOT / "data" / "qfq_aux_mcp_gen1.db"

DEV_TH = 1e-3  # 0.1% 一致性阈值


def load_factors(codes: list, is_etf: bool) -> pd.DataFrame:
    tbl = "fund_adj" if is_etf else "adj_factor"
    con = sqlite3.connect(AUX)
    ph = ",".join("?" * len(codes))
    df = pd.read_sql(f"SELECT code, time, adj_factor FROM {tbl} WHERE code IN ({ph})",
                     con, params=codes)
    con.close()
    return df


def process(table: str, is_etf: bool, apply: bool, codes: list | None = None) -> None:
    con = duckdb.connect(str(DB))
    daily = "etf_daily" if is_etf else "stock_daily"
    where = f"WHERE code IN ({','.join('?' * len(codes))})" if codes else ""
    params = codes or []
    mrows = con.execute(
        f"SELECT code, time, open, high, low, close FROM {table} {where}", params).fetchdf()
    if len(mrows) == 0:
        print(f"[{table}] 无行"); con.close(); return
    drows = con.execute(
        f"SELECT code, time, close FROM {daily}", []).fetchdf()
    drows = drows.rename(columns={"close": "daily_close"})
    drows["day"] = (drows["time"] + 28800000) // 86400000
    fac = load_factors(sorted(set(mrows["code"])), is_etf)
    fac = fac.rename(columns={"adj_factor": "adj_row"})
    fac["day"] = (fac["time"] + 28800000) // 86400000
    fac = fac.drop_duplicates(["code", "day"], keep="first")  # 日内恒定
    # 最新因子 = 时间最新行（非单调！不能 max）
    fac_latest = fac.sort_values("time").groupby("code", as_index=False).tail(1)[["code", "adj_row"]]
    fac_latest = fac_latest.rename(columns={"adj_row": "adj_latest"})
    mrows["day"] = (mrows["time"] + 28800000) // 86400000
    mrows = mrows.merge(fac[["code", "day", "adj_row"]], on=["code", "day"], how="left")
    mrows = mrows.merge(fac_latest, on="code", how="left")
    has_factor = mrows["adj_row"].notna() & (mrows["adj_latest"] > 0)
    mrows["ratio"] = 1.0
    mrows.loc[has_factor, "ratio"] = mrows.loc[has_factor, "adj_row"] / mrows.loc[has_factor, "adj_latest"]
    for c in ("open", "high", "low", "close"):
        mrows[c + "_raw"] = mrows[c] * mrows["ratio"]
    # 收盘 bar（每组 code+日 最后一根）
    mrows["bar_rank"] = mrows.groupby(["code", "day"])["time"].rank(method="first", ascending=False)
    bar15 = mrows[mrows["bar_rank"] == 1].copy()
    bar15 = bar15.merge(drows[["code", "day", "daily_close"]], on=["code", "day"], how="left")
    has_daily = bar15["daily_close"].notna()
    bar15["dev_before"] = (bar15["close"] / bar15["daily_close"] - 1).abs()
    bar15["dev_after"] = (bar15["close_raw"] / bar15["daily_close"] - 1).abs()
    polluted = has_daily & (bar15["dev_before"] > DEV_TH) & (bar15["dev_after"] <= DEV_TH)
    weird = has_daily & (bar15["dev_before"] > DEV_TH) & (bar15["dev_after"] > DEV_TH)
    n_before = int(has_daily[bar15["dev_before"] > DEV_TH].sum())
    print(f"[{table}] 收盘bar总={len(bar15)} 不一致日={n_before} "
          f"污染日={int(polluted.sum())} 异常日(还原后仍不一致)={int(weird.sum())}")
    # 无因子覆盖的股票（无法判定，跳过）
    nofac = sorted(set(mrows.loc[~has_factor, "code"]))
    if nofac:
        print(f"[{table}] 无因子覆盖股票 {len(nofac)} 只（跳过）: {nofac[:10]}")
    if weird.sum():
        w = bar15[weird].copy()
        w["date"] = (w["day"] * 86400000 - 28800000)
        w = w.sort_values("dev_before", ascending=False).head(10)
        for r in w.itertuples():
            print(f"  异常 {r.code} day={r.day} close={r.close} raw={r.close_raw:.4f} "
                  f"daily={r.daily_close} ratio={r.ratio:.4f}")
    # 逐股票污染日统计
    if polluted.any():
        per = bar15[polluted].groupby("code").agg(n=("time", "size")).reset_index()
        print(f"[{table}] 污染股票数={len(per)}，污染日合计={int(polluted.sum())}")
        print(per.to_string(index=False))
        keys = set(zip(bar15.loc[polluted, "code"], bar15.loc[polluted, "day"]))
        mrows["is_polluted"] = [ (r.code, r.day) in keys for r in mrows.itertuples() ]
        n_rows = int(mrows["is_polluted"].sum())
        print(f"[{table}] 将还原 {n_rows} 行（污染日全部 bar）")
        if apply:
            con.register("upd_df", mrows.loc[mrows["is_polluted"], ["code", "time", "ratio"]])
            # 回滚备份：保存 ratio 表
            bak = ROOT / "data" / f"minute_fix_ratio_{table}.csv"
            pd.DataFrame(mrows.loc[mrows["is_polluted"], ["code", "time", "ratio"]]).to_csv(bak, index=False)
            print(f"[{table}] ratio 备份 -> {bak.name}")
            upd = con.execute(
                f"""UPDATE {table} AS t SET
                    open = t.open * r.ratio, high = t.high * r.ratio,
                    low = t.low * r.ratio, close = t.close * r.ratio
                    FROM upd_df AS r
                    WHERE t.code = r.code AND t.time = r.time""")
            con.commit()
            print(f"[{table}] UPDATED {upd.rowcount} rows")
    else:
        print(f"[{table}] 无污染日，无需还原")
    con.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    print("MODE:", "APPLY" if apply else "DRY-RUN", "| scope:", only or "ALL")
    if only in (None, "stock"):
        process("stock_minutes", False, apply)
    if only in (None, "etf"):
        process("etf_minutes", True, apply)
