"""stock_float_share 全量回填（2018-01-01 ~ 2026-08-16，月度分批 export_dataset）。

数据源：qdb.stock_daily_basic（MCP 唯一权威源）
方法：按月分批 export_dataset（每批拉一个月数据），月度采样后写入 DuckDB。
"""
import json
import sys
import time
import io

import pandas as pd

sys.path.insert(0, '.')

DB_PATH = "data/quantstudio.db"
START = "2018-01-01"
END = "2026-08-16"


def to_ms(s):
    return int(pd.Timestamp(str(s)[:10], tz="Asia/Shanghai").timestamp() * 1000)


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
        PRIMARY KEY(code, end_date, ann_date))""")

    months = pd.date_range(START, END, freq="MS")
    total_written = 0
    now = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")

    for i, month_start in enumerate(months):
        month_end = (months[i + 1] - pd.Timedelta(days=1)) if i + 1 < len(months) else pd.Timestamp(END)
        t0 = time.time()

        try:
            artifacts = adapter.client.export_dataset(
                "qdb.stock_daily_basic", page_size=50_000,
                time_start=month_start.strftime("%Y-%m-%dT00:00:00"),
                time_end=month_end.strftime("%Y-%m-%dT23:59:59"),
                row_limit=None)
        except Exception as e:
            print(f"  [{month_start.strftime('%Y-%m')}] export 失败: {str(e)[:60]}", flush=True)
            continue

        all_rows = []
        for art in artifacts:
            df = pd.read_parquet(io.BytesIO(art.parquet_bytes))
            all_rows.extend(df.to_dict("records"))

        if not all_rows:
            print(f"  [{month_start.strftime('%Y-%m')}] 0 行", flush=True)
            continue

        # 标准化
        norm = []
        for row in all_rows:
            ts = str(row.get("ts_code", "")).strip()
            if "." not in ts:
                continue
            bare = ts.split(".")[0]
            td = row.get("trade_date")
            fs = row.get("float_share")
            if td is None or fs is None:
                continue
            ms = to_ms(td)
            norm.append((bare, ms, ms, float(fs),
                        float(row["total_share"]) if row.get("total_share") else None,
                        float(row["circ_mv"]) if row.get("circ_mv") else None,
                        float(row["total_mv"]) if row.get("total_mv") else None, now))

        if norm:
            con.executemany(
                """INSERT OR REPLACE INTO stock_float_share
                (code, end_date, ann_date, free_share, total_share,
                 circ_mv, total_mv, update_time)
                VALUES (?,?,?,?,?,?,?,?)""", norm)
            total_written += len(norm)

        elapsed = time.time() - t0
        print(f"  [{month_start.strftime('%Y-%m')}] {len(norm):,} 行 "
              f"（累计 {total_written:,}，{elapsed:.0f}s）", flush=True)

    con.commit()

    # 验证
    r = con.execute("""SELECT COUNT(*), COUNT(DISTINCT code),
                       MIN(end_date), MAX(end_date) FROM stock_float_share""").fetchone()
    d1 = pd.Timestamp(r[2], unit="ms", tz="UTC").tz_convert("Asia/Shanghai").date()
    d2 = pd.Timestamp(r[3], unit="ms", tz="UTC").tz_convert("Asia/Shanghai").date()
    print(f"\n=== 回填完成 ===")
    print(f"stock_float_share: {r[0]:,} 行 / {r[1]} 只 / {d1} ~ {d2}")

    for code in ["002494", "600000", "510300"]:
        r2 = con.execute("""SELECT end_date, free_share FROM stock_float_share
            WHERE code=? ORDER BY end_date DESC LIMIT 1""", [code]).fetchone()
        if r2:
            d = pd.Timestamp(r2[0], unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
            print(f"  {code}: {d.date()} free_share={r2[1]}")
    con.close()


if __name__ == "__main__":
    main()
