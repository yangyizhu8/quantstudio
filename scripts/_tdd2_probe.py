"""TD-D2 步骤1 同值性抽核（临时探测脚本，不入库）。

抽样：股票/ETF 各 12 只（优先最近 60 天因子有变化的 code，分辨力最高）。
对比：MCP 权威系（fetch_table raw_df 的 adj_factor，生产 API）vs legacy 混合系
（qfq_aux.db 同 code 因子）——latest 值 + 逐日因子，相对差 > 1e-6 记差异。
"""
import sys, sqlite3, json
sys.path.insert(0, '.')
import pandas as pd
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter, normalize_mcp_adj_factor_df
from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path

LEGACY = aux_db_path('data/quantstudio.db')
aux = sqlite3.connect(f"file:{LEGACY}?mode=ro", uri=True, timeout=30)


def pick_codes(table, n):
    chg = aux.execute(f"""
        WITH ranked AS (SELECT code, time, adj_factor,
               LAG(adj_factor) OVER (PARTITION BY code ORDER BY time) AS prev
             FROM {table})
        SELECT code, MAX(time) AS mt FROM ranked
        WHERE prev IS NOT NULL AND ABS(adj_factor - prev) > 1e-9
          AND time > 1782000000000 GROUP BY code ORDER BY mt DESC LIMIT {n}""").fetchall()
    codes = [c for c, _ in chg]
    if len(codes) < n:
        ph0 = ",".join("?" * len(codes)) if codes else "''"
        more = aux.execute(
            f"SELECT code, MAX(time) FROM {table} WHERE code NOT IN ({ph0}) "
            f"GROUP BY code ORDER BY 2 DESC LIMIT {n - len(codes)}", codes).fetchall()
        codes += [c for c, _ in more]
    return codes[:n]


def bar_day(ms):
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert(
        "Asia/Shanghai").strftime("%Y-%m-%d")


cfg = json.load(open('config/profiles/mcp_only/sources_config.json'))["sources"]["mcp"]
adapter = MCPAdapter(dict(cfg, main_db="data/quantstudio.db"))

report = {"stock": {}, "etf": {}}
for label, table, at, aux_tbl in [("stock", "stock_daily", "STOCK", "adj_factor"),
                                  ("etf", "etf_daily", "ETF", "fund_adj")]:
    codes = pick_codes(aux_tbl, 12)
    print(f"[{label}] 抽样 code: {codes}")
    try:
        raw_df, _meta = adapter.fetch_table(table, "2026-04-01", "2026-08-16",
                                            "daily", codes=codes)
    except Exception as e:
        print(f"[{label}] MCP fetch 失败: {type(e).__name__}: {e}")
        report[label] = {"error": f"{type(e).__name__}: {e}"}
        continue
    if raw_df is None or len(raw_df) == 0 or "adj_factor" not in raw_df.columns:
        n = 0 if raw_df is None else len(raw_df)
        print(f"[{label}] raw_df 无 adj_factor（行数={n} 列={list(raw_df.columns)[:8] if raw_df is not None else None}）")
        report[label] = {"error": "no adj_factor in raw_df"}
        continue
    norm = normalize_mcp_adj_factor_df(raw_df, "daily", at)
    mcp = {(str(r["code"]), bar_day(r["time"])): float(r["adj_factor"])
           for _, r in norm.iterrows()}
    ph = ",".join("?" * len(codes))
    leg_rows = aux.execute(
        f"SELECT code, time, adj_factor FROM {aux_tbl} WHERE code IN ({ph})",
        codes).fetchall()
    leg = {(c, bar_day(t)): float(f) for c, t, f in leg_rows}
    common = set(mcp) & set(leg)
    diff = [(k[0], k[1], leg[k], mcp[k]) for k in common
            if abs(mcp[k] - leg[k]) / max(abs(leg[k]), 1e-12) > 1e-6]
    only_mcp = len(set(mcp) - set(leg)); only_leg = len(set(leg) - set(mcp))
    mcp_latest, leg_latest = {}, {}
    for (c, d), f in mcp.items():
        if c not in mcp_latest or d > mcp_latest[c][0]:
            mcp_latest[c] = (d, f)
    for (c, d), f in leg.items():
        if c not in leg_latest or d > leg_latest[c][0]:
            leg_latest[c] = (d, f)
    lat_diff = [c for c in mcp_latest if c in leg_latest and
                abs(mcp_latest[c][1] - leg_latest[c][1]) /
                max(abs(leg_latest[c][1]), 1e-12) > 1e-6]
    report[label] = {"codes": codes, "mcp_rows": len(mcp),
                     "common_days": len(common), "daily_diff_rows": len(diff),
                     "latest_diff_codes": lat_diff,
                     "only_mcp_days": only_mcp, "only_legacy_days": only_leg}
    print(f"[{label}] MCP行={len(mcp)} 交集={len(common)} 逐日差={len(diff)} "
          f"latest差code={lat_diff}")
    for d in diff[:3]:
        print(f"   diff例: {d[0]}@{d[1]} legacy={d[2]} mcp={d[3]}")

aux.close()
print("\n=== 抽核结果 JSON ===")
print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
