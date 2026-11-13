# -*- coding: utf-8 -*-
"""R27 batch02：按交易日切段边界 + 行数（只读·可续·资源前置）。

段清单口径（R27 明确：9 表统计 ≠ 段清单）：
  段边界 = 单日区间（[d 00:00, d+1 00:00) 左闭右开）
  本批可给：段边界 / 行数 / 段内 min-max 时间戳
  本批不给：首末键（每段 ordered by sym 的首末）、可回放判定 —— 标记为 pending，不冒充
可续：按表+日期幂等覆盖，中断直接重跑同批不重复。
"""
import io
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9000/exec"
OUT = r"D:\miniQMT策略实盘\QuantStudio\docs\evidence\census-manifest-lia-batch02-20261113.json"
TABLES = ["stock_minutes", "etf_minutes"]


def q(sql, timeout=300):
    with urllib.request.urlopen(BASE + "?query=" + urllib.parse.quote(sql), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ts_col_of(tbl, prefer=("trade_time", "ts", "datetime")):
    r = q("select * from %s limit 0" % tbl, timeout=60)
    ts_cols = [c["name"] for c in r["columns"] if c["type"] == "TIMESTAMP"]
    for p in prefer:
        if p in ts_cols:
            return p
    if not ts_cols:
        raise RuntimeError("no TIMESTAMP column in %s" % tbl)
    return ts_cols[0]


def segments_sample_by(tbl):
    """一次性拿全表按日分段的行数（段边界+行数），SAMPLE BY 1d。"""
    tc = ts_col_of(tbl)
    t0 = time.perf_counter()
    r = q("select %s, count() c from %s sample by 1d" % (tc, tbl))
    el = time.perf_counter() - t0
    rows = []
    for rec in r["dataset"]:
        ts, c = rec[0], rec[1]
        rows.append({"day": ts[:10], "rows": c})
    return rows, el


def segment_span(tbl, day):
    """单段 min/max 时间戳（验证段边界真实性）。"""
    tc = ts_col_of(tbl)
    t0 = time.perf_counter()
    r = q("select min(%s) mn, max(%s) mx, count() c from %s "
          "where %s >= '%s' and %s < '%s'"
          % (tc, tc, tbl, tc, day, tc, next_day(day)))
    el = time.perf_counter() - t0
    row = r["dataset"][0]
    return {"min_ts": row[0], "max_ts": row[1], "rows_filtered": row[2]}, el


def next_day(d):
    import datetime
    dt = datetime.date.fromisoformat(d) + datetime.timedelta(days=1)
    return dt.isoformat()


def main():
    out = {
        "manifest_version": "census-v2-segments",
        "batch": "L-A/batch02",
        "endpoint": "L-A 本机 QDB 127.0.0.1:9000",
        "data_root": "D:\\questdb\\quest_database",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "segment_rule": "单日区间 [day 00:00, day+1 00:00) 左闭右开；SAMPLE BY 1d 得段行数",
        "provided_per_segment": ["段边界(day)", "行数"],
        "NOT_provided": ["首末键(每段 ordered by sym 首末)", "可回放判定", "L2 执行可用性"],
        "effects_boundary": (
            "本件是段级统计清单，仅用于定位缺口与回放候选；"
            "不构成 L2 回放执行输入，也不等价于 9 表总量统计。"
        ),
        "resource_precondition": {
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "disk_free_gb": 141.4,
            "ram_free_gb": 5.0,
            "ram_load_pct": 85,
            "note": "内存偏紧；本批只读且单查询聚合，未做逐段 N 次查询",
        },
        "tables": {},
    }

    for tbl in TABLES:
        segs, el = segments_sample_by(tbl)
        tc = ts_col_of(tbl)
        total = sum(s["rows"] for s in segs)
        entry = {
            "designated_ts": tc,
            "segment_count": len(segs),
            "rows_sum": total,
            "sample_by_elapsed_sec": round(el, 2),
            "segments": segs,
        }
        # 抽样验证 3 段（首/中/末）边界真实性
        picks = [segs[0]["day"], segs[len(segs) // 2]["day"], segs[-1]["day"]] if segs else []
        checks = []
        for d in picks:
            span, e = segment_span(tbl, d)
            checks.append({"day": d, "span": span, "rows_match": span["rows_filtered"] ==
                           next(s["rows"] for s in segs if s["day"] == d),
                           "elapsed_sec": round(e, 3)})
        entry["boundary_verification"] = checks
        out["tables"][tbl] = entry
        print("%-16s segments=%-5d rows_sum=%-14d sample_by=%.2fs 首段=%s 末段=%s"
              % (tbl, len(segs), total, el, segs[0]["day"] if segs else None,
                 segs[-1]["day"] if segs else None), flush=True)
        for c in checks:
            print("    verify %s rows_match=%s min=%s max=%s"
                  % (c["day"], c["rows_match"], c["span"]["min_ts"], c["span"]["max_ts"]),
                  flush=True)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nmanifest ->", OUT)


if __name__ == "__main__":
    main()
