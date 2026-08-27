# -*- coding: utf-8 -*-
"""ETF 停牌/公司行为巡检（设计文档 backtest-align-diagnosability-design §2.3，改动点 3）。

只读巡检（不改任何数据/代码），用于：
1. 确认"2026-07-01 停牌/公司行为群"是模拟数据源特征还是同步缺口（根因 B/C 定性依据）；
2. 列出全月 ETF 停牌日与公司行为日（拆分/合并），供两端对齐差异归因。

用法: python audit_etf_corporate_actions.py --db <quantstudio.db> [--start 2026-07-01] [--end 2026-07-31]
输出: output/debug_align/etf_ca_audit_202607.md

判定口径：
- 停牌日：当日无 bar（equity ETF 在 PIT 池内但 etf_daily 无该日记录）
- 公司行为日：preClose 相对前一交易日收盘跳变 |ratio-1| > 1.5%（阈值可配）
  - ratio = preClose(t) / close(t-1)；ratio>1 → 份额合并（价格翻倍）；ratio<1 → 拆分/送股（价格减半）
  - 边界样本（1.2%~1.8%）单独列出，避免阈值选择倒推结论
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument("--jump-threshold", type=float, default=0.015, help="公司行为判定阈值（±）")
    args = ap.parse_args()

    conn = duckdb.connect(str(args.db), read_only=True)

    def to_ms(d: str) -> int:
        return int(pd.Timestamp(d).value // 10**6)

    start_ms, end_ms = to_ms(args.start), to_ms(args.end)
    # etf_daily.time 为 UTC 存储（+08 交易日 00:00 = 前一日 16:00 UTC）：
    # 查询范围外扩 8h（起始日）与 24h（末日），日期过滤在 pandas 层按 +08 交易日做。
    query_start = start_ms - 8 * 3600 * 1000
    query_end = end_ms + 24 * 3600 * 1000

    # equity 元数据（按日 PIT 判定上市/退市，避免"未上市代码被误判为停牌"）
    basic = conn.execute("""
        SELECT code, list_date, delist_date FROM etf_basic
        WHERE etf_type = 'equity' AND COALESCE(is_cross_border, FALSE) = FALSE
    """).fetchdf()
    basic["list_date"] = pd.to_datetime(basic["list_date"], unit="ms", utc=True) \
        .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d")
    basic["delist_date"] = pd.to_datetime(basic["delist_date"], unit="ms", utc=True) \
        .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d")

    def pit_pool(d: str) -> set[str]:
        ok = basic["list_date"].notna() & (basic["list_date"] <= d)
        ok &= basic["delist_date"].isna() | (basic["delist_date"] > d)
        return set(basic.loc[ok, "code"])

    pool_end = pit_pool(args.end)

    df = conn.execute("""
        SELECT code, time, close, preClose FROM etf_daily
        WHERE time BETWEEN ? AND ?
        ORDER BY code, time
    """, [query_start, query_end]).fetchdf()
    df["date"] = (pd.to_datetime(df["time"], unit="ms", utc=True)
                  .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    df = df[(df["date"] >= args.start) & (df["date"] <= args.end)]
    # 全历史（用于前一交易日收盘与停牌判定）
    hist = conn.execute("""
        SELECT code, time, close FROM etf_daily
        WHERE time < ? ORDER BY code, time
    """, [query_start]).fetchdf()
    hist["date"] = (pd.to_datetime(hist["time"], unit="ms", utc=True)
                    .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    hist = hist[hist["date"] < args.start]
    conn.close()

    cal_days = sorted(set(df["date"]))
    prev_close: dict[str, float] = {}
    # 初始化 prev_close：起始日前的最后一根收盘（按 code）
    for c, g in hist.groupby("code"):
        prev_close[c] = float(g["close"].iloc[-1])

    halted_days: dict[str, list[str]] = {}
    ca_rows: list[tuple[str, str, float, float, float, str]] = []   # code, date, close, preClose, ratio, kind
    edge_rows: list[tuple[str, str, float, float, float, str]] = []

    by_date = {d: g for d, g in df.groupby("date")}
    for d in cal_days:
        rows = by_date[d]
        seen = set(rows["code"])
        # 1) 停牌：当日 PIT 池内但当日无 bar
        for c in sorted(pit_pool(d) - seen):
            halted_days.setdefault(d, []).append(c)
        # 2) 公司行为：preClose vs 前一收盘
        for _, r in rows.iterrows():
            c, cl, pc = r["code"], float(r["close"]), float(r["preClose"])
            if c in prev_close and prev_close[c] > 0 and pc > 0:
                ratio = pc / prev_close[c]
                dev = abs(ratio - 1.0)
                if dev > args.jump_threshold:
                    kind = "合并(份额减半/价翻倍)" if ratio > 1.0 else "拆分/送股(价减半)"
                    ca_rows.append((c, d, cl, pc, ratio, kind))
                elif dev >= args.jump_threshold - 0.003:  # 边界样本（阈值±0.3pp）
                    kind = "疑似边界"
                    edge_rows.append((c, d, cl, pc, ratio, kind))
            prev_close[c] = cl

    # ---- 汇总 ----
    lines = [
        "# ETF 停牌/公司行为巡检报告",
        "",
        f"- 数据源: `{args.db}`",
        f"- 区间: {args.start} ~ {args.end}（{len(cal_days)} 个交易日）",
        f"- 巡检脚本: `scripts/audit_etf_corporate_actions.py`（只读）",
        f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M}",
        f"- equity PIT 池（{args.end} 视图）: {len(pool_end)} 只",
        "",
        f"## 1. 停牌日（池内但当日无 bar）",
        "",
    ]
    total_halt = sum(len(v) for v in halted_days.values())
    lines.append(f"共 {total_halt} 只·日。")
    for d in cal_days:
        v = halted_days.get(d, [])
        lines.append(f"- **{d}**: {len(v)} 只" + (f"（{', '.join(v[:20])}" + ("..." if len(v) > 20 else "") + "）" if v else ""))
    lines += [
        "",
        f"## 2. 公司行为日（preClose/前收 偏离 > ±{args.jump_threshold:.1%}）",
        "",
        f"共 {len(ca_rows)} 条：",
        "",
        "| 代码 | 日期 | close | preClose | ratio | 类型 |",
        "|---|---|---|---|---|---|",
    ]
    for c, d, cl, pc, ratio, kind in sorted(ca_rows, key=lambda x: (x[1], x[0])):
        lines.append(f"| {c} | {d} | {cl:.3f} | {pc:.3f} | {ratio:.4f} | {kind} |")
    lines += [
        "",
        f"## 3. 阈值边界样本（偏离 {args.jump_threshold-0.003:.1%} ~ {args.jump_threshold+0.003:.1%}）",
        "",
        f"共 {len(edge_rows)} 条（避免阈值选择倒推结论）：",
        "",
        "| 代码 | 日期 | close | preClose | ratio |",
        "|---|---|---|---|---|",
    ]
    for c, d, cl, pc, ratio, _ in sorted(edge_rows, key=lambda x: (x[1], x[0])):
        lines.append(f"| {c} | {d} | {cl:.3f} | {pc:.3f} | {ratio:.4f} |")

    out = ROOT / "output" / "debug_align" / "etf_ca_audit_202607.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[audit] 停牌 {total_halt} 只·日；公司行为 {len(ca_rows)} 条；边界 {len(edge_rows)} 条 → {out}")


if __name__ == "__main__":
    main()
