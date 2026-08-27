"""stock_float_share MCP 全量回填（fetch_page cursor 分页 + 月度采样）。

数据源：qdb.stock_daily_basic（MCP 唯一权威源）
策略：fetch_page 全量分页拉取（cursor），本地按月采样（每月最新一条），
      2020-01 起保留（历史更早的丢弃），写入 DuckDB stock_float_share。
"""
import json
import sys

import pandas as pd

sys.path.insert(0, '.')

DB_PATH = "data/quantstudio.db"
CUTOFF = "2020-01-01"


def to_ms(date_str):
    return int(pd.Timestamp(str(date_str)[:10], tz="Asia/Shanghai").timestamp() * 1000)


def main():
    import duckdb
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter

    cfg = json.load(open("config/profiles/mcp_only/sources_config.json",
                         encoding="utf-8"))["sources"]["mcp"]
    adapter = MCPAdapter(dict(cfg, main_db=DB_PATH))
    con = duckdb.connect(DB_PATH)

    con.execute("""CREATE TABLE IF NOT EXISTS stock_float_share (
        code VARCHAR, end_date BIGINT, ann_date BIGINT,
        free_share DOUBLE, total_share DOUBLE,
        circ_mv DOUBLE, total_mv DOUBLE,
        update_time VARCHAR,
        PRIMARY KEY(code, end_date, ann_date)
    )""")

    # fetch_page cursor 分页全量拉取
    all_rows = []
    cursor = ""
    page_num = 0
    cols = ["ts_code", "trade_date", "float_share", "total_share", "circ_mv", "total_mv"]
    cutoff_ms = to_ms(CUTOFF)

    while True:
        result = adapter.client.fetch_page(
            "qdb.stock_daily_basic", cursor=cursor, page_size=50_000, columns=cols)
        rows = result.get("rows", [])
        next_cursor = result.get("next_cursor") or result.get("cursor")
        page_num += 1

        if not rows:
            break

        for r in rows:
            ts = str(r.get("ts_code", "")).strip()
            if "." not in ts:
                continue
            bare = ts.split(".")[0]
            td = r.get("trade_date")
            if td is None:
                continue
            ms = to_ms(td)
            if ms < cutoff_ms:
                continue  # 跳过 2020 之前
            fs = r.get("float_share")
            if fs is None:
                continue
            all_rows.append({
                "code": bare, "end_date": ms,
                "free_share": float(fs),
                "total_share": float(r["total_share"]) if r.get("total_share") else None,
                "circ_mv": float(r["circ_mv"]) if r.get("circ_mv") else None,
                "total_mv": float(r["total_mv"]) if r.get("total_mv") else None,
            })

        print(f"  page #{page_num}: {len(rows)} 行（有效累计 {len(all_rows):,}）", flush=True)

        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        # 安全阀：最多 200 页（1000 万行）
        if page_num >= 200:
            print("达到 200 页安全阀，停止")
            break

    if not all_rows:
        print("无数据！")
        return

    df = pd.DataFrame(all_rows)

    # 月度采样：每 (code, 年-月) 保留 end_date 最大的一条
    df["month"] = pd.to_datetime(df["end_date"], unit="ms").dt.strftime("%Y-%m")
    df_sorted = df.sort_values("end_date")
    df_monthly = df_sorted.drop_duplicates(subset=["code", "month"], keep="last")

    print(f"\n全量: {len(df):,} 行 → 月度采样后: {len(df_monthly):,} 行")

    # 批量写入
    now = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
    records = [(r["code"], int(r["end_date"]), None, r["free_share"],
                r["total_share"], r["circ_mv"], r["total_mv"], now)
               for _, r in df_monthly.iterrows()]
    con.executemany(
        """INSERT OR REPLACE INTO stock_float_share
        (code, end_date, ann_date, free_share, total_share,
         circ_mv, total_mv, update_time)
        VALUES (?,?,?,?,?,?,?,?)""", records)
    con.commit()

    # 验证
    r = con.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM stock_float_share").fetchone()
    print(f"\n=== 回填完成 ===")
    print(f"stock_float_share: {r[0]:,} 行 / {r[1]} 只")
    for code in ["002494", "003003", "002719"]:
        r2 = con.execute("""SELECT code, end_date, free_share, circ_mv FROM stock_float_share
            WHERE code=? ORDER BY end_date DESC LIMIT 2""", [code]).fetchall()
        for row in r2:
            d = pd.Timestamp(row[1], unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
            print(f"  {row[0]} {d.date()} free_share={row[2]} circ_mv={row[3]}")
    con.close()


if __name__ == "__main__":
    main()
