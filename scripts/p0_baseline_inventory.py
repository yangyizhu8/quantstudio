"""
P0 双库基线冻结采集脚本（READ-ONLY）。

- QuantStudio 侧：只读连接 data/quantstudio.db (DuckDB)，采集表清单/行数/水位/来源。
- 云端侧：只读探测本地 http://127.0.0.1:9000 (QuestDB 与云端同构免鉴权)，
  采集 109 表 schema 与关键表行数/深度。

产物：
  output/mcp_migration/P0_baseline/quantstudio_duckdb_baseline.json
  output/mcp_migration/P0_baseline/source_watermark_snapshot.json
  output/mcp_migration/P0_baseline/questdb_cloud_baseline.json

注意：本脚本不执行任何写操作；不得修改生产代码。
"""
import os
import json
import urllib.request
import urllib.parse
import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "quantstudio.db")
OUT = os.path.join(ROOT, "output", "mcp_migration", "P0_baseline")
QDB = "http://127.0.0.1:9000/exp?query="
os.makedirs(OUT, exist_ok=True)


def qdb_query(sql):
    url = QDB + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode()


# ---------- A1: QuantStudio DuckDB ----------
def collect_duckdb():
    con = duckdb.connect(DB, read_only=True)
    tabs = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY table_name"
    ).fetchall()

    table_list = []
    for schema, name in tabs:
        row = {"schema": schema, "name": name}
        try:
            row["rows"] = con.execute(f'SELECT count(*) FROM "{schema}"."{name}"').fetchone()[0]
        except Exception as e:
            row["rows"] = f"ERR:{e}"
        # 主键时间字段探测（canonical 表）
        for col, kind in [("time", "ms_epoch"), ("trade_date", "ts"), ("trade_time", "ts"),
                          ("end_date", "ms_epoch"), ("cal_date", "ms_epoch"), ("list_date", "ts")]:
            try:
                mn, mx = con.execute(
                    f'SELECT min("{col}"), max("{col}") FROM "{schema}"."{name}"'
                ).fetchone()
                if mn is not None:
                    row["time_col"] = col
                    row["time_kind"] = kind
                    row["min_time"] = mn
                    row["max_time"] = mx
                    break
            except Exception:
                continue
        table_list.append(row)

    # 水位表：来源分布（权威）
    watermark = []
    try:
        for r in con.execute("SELECT source, table_name, freq, last_date FROM source_watermark ORDER BY source, table_name").fetchall():
            watermark.append({"source": r[0], "table_name": r[1], "freq": r[2], "last_date": str(r[3])})
    except Exception as e:
        watermark = [{"error": str(e)}]

    # qfq 水位意图
    qfq_intent = []
    try:
        for r in con.execute("SELECT * FROM qfq_watermark_intent").fetchall():
            qfq_intent.append([str(x) for x in r])
    except Exception as e:
        qfq_intent = [{"error": str(e)}]

    con.close()

    baseline = {
        "engine": "duckdb",
        "db_path": "data/quantstudio.db",
        "table_count": len(table_list),
        "tables": table_list,
        "watermark": watermark,
        "qfq_watermark_intent": qfq_intent,
        "code_format": "bare_numeric (e.g. 600063, 159982) — no exchange suffix",
        "time_repr": "millisecond epoch (e.g. 1514822400000 = 2018-01-01) for time/end_date/cal_date",
    }
    with open(os.path.join(OUT, "quantstudio_duckdb_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "source_watermark_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(watermark, f, ensure_ascii=False, indent=2)
    return baseline


# ---------- A2: QuestDB (local 9000 mirror of cloud) ----------
def collect_qdb():
    raw = qdb_query("SELECT table_name FROM tables() WHERE table_name NOT LIKE 'telemetry%' ORDER BY table_name")
    tables = [l.strip() for l in raw.strip().split("\n")[1:] if l.strip()]

    # schema + rows for canonical tables
    canonical = {}
    key_tables = ["stock_daily", "stock_minutes", "etf_daily", "etf_minutes",
                  "stock_basic", "index_daily", "sw_classify", "ths_member",
                  "hm_list", "limit_list_d", "ai_research_snapshot",
                  "block_trade", "broker_recommend", "cninfo_first_rating"]
    for t in key_tables:
        entry = {"exists": False}
        try:
            sch = qdb_query(f"SHOW COLUMNS FROM {t}").strip().split("\n")[1:]
            cols = []
            for line in sch:
                parts = [p.strip() for p in line.split(",")]
                if parts and parts[0]:
                    cols.append({"name": parts[0].strip('"'), "type": parts[1] if len(parts) > 1 else ""})
            entry["columns"] = cols
            entry["exists"] = True
        except Exception as e:
            entry["schema_error"] = str(e)
        try:
            c = qdb_query(f"SELECT count(*) FROM {t}").strip().split("\n")
            entry["rows"] = int(c[1]) if len(c) > 1 and c[1].strip().isdigit() else c[1] if len(c) > 1 else None
        except Exception as e:
            entry["rows_error"] = str(e)
        canonical[t] = entry

    # 109 全量表名清单
    with open(os.path.join(OUT, "questdb_109_tables.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(tables))

    baseline = {
        "engine": "questdb",
        "probe_endpoint": "http://127.0.0.1:9000 (local mirror, schema-identical to cloud per §0 0-diff)",
        "table_count": len(tables),
        "code_format": "symbol_with_suffix (e.g. 600780.SH, 159982.SZ)",
        "time_repr": "TIMESTAMP (UTC, e.g. 2000-01-21T00:00:00.000000Z)",
        "designated_timestamp": "trade_date (daily) / trade_time (minutes) per QuestDB partition",
        "canonical_tables": canonical,
        "note": "分钟数据深度: stock_minutes ≈ 4.78亿行; 全量109表行数/深度以云端C0校验报告为准(local 9000 为同构只读探测)。",
    }
    with open(os.path.join(OUT, "questdb_cloud_baseline.json"), "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    return baseline


if __name__ == "__main__":
    b1 = collect_duckdb()
    b2 = collect_qdb()
    print("A1 DuckDB tables:", b1["table_count"])
    print("A2 QuestDB tables:", b2["table_count"])
    print("Artifacts written to:", OUT)
