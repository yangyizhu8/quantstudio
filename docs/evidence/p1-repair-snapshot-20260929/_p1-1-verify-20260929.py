"""P1.1 验收取证（2026-09-29 夜）：真分片集上的
  ① 下推 vs 旧路径 逐行等价 ② 时延/RSS 对照 ③ row-group 可裁剪性事实 ④ 覆盖面计数。

只读，不写任何业务数据；输出 JSON 到 QuantStudio/docs/evidence/p1-repair-snapshot-20260929/。
用法：py -3.11 _tmp_p1_1_verify.py
"""
import glob, json, os, time
from collections import OrderedDict
from pathlib import Path

import pandas as pd
import psutil
import pyarrow.parquet as pq

import sys
sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")
from quantstudio.pipeline.sources import mcp_adapter as ma

PROC = psutil.Process(os.getpid())
OUT_DIR = Path(r"D:\miniQMT策略实盘\QuantStudio\docs\evidence\p1-repair-snapshot-20260929")
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = Path(r"D:\miniQMT策略实盘\QuantStudio\data\logs\daemon.log")

SHARD_SETS = {
    "etf_minutes": r"D:\miniQMT策略实盘\QuantStudio\data\mcp_landing\exp_etf_minutes_d07a4b4f",
    "stock_daily": r"D:\miniQMT策略实盘\QuantStudio\data\mcp_landing\exp_stock_daily_06e80619",
    "stock_minutes": None,  # 现取：mcp_landing 下最大的 exp_stock_minutes_* 目录
}


def rss_gb():
    return PROC.memory_info().rss / 1024 ** 3


def _adapter():
    a = ma.MCPAdapter.__new__(ma.MCPAdapter)
    a._shard_table_cache = OrderedDict()
    a._SHARD_CACHE_MAX = 30
    a._ckey_meta_cache = OrderedDict()
    a._CKEY_META_CACHE_MAX = 256
    return a


def legacy(adapter, ckey, paths, want_codes):
    """旧行为：全市场 concat（且入 LRU）→ astype(str).isin。"""
    full = adapter._read_ckey_cached(ckey, paths)
    if len(full) and want_codes:
        col = next((c for c in ("ts_code", "code", "stock_code") if c in full.columns), None)
        if col:
            return full[full[col].astype(str).isin(want_codes)]
    return full


def timed(fn):
    r0 = rss_gb()
    t0 = time.perf_counter()
    df = fn()
    dt = time.perf_counter() - t0
    r1 = rss_gb()
    return df, dt, r1 - r0


def main():
    # stock_minutes：现取最大目录
    cands = sorted(glob.glob(r"D:\miniQMT策略实盘\QuantStudio\data\mcp_landing\exp_stock_minutes_*"),
                   key=lambda p: sum(os.path.getsize(f) for f in glob.glob(os.path.join(p, "*.parquet")) or [0]),
                   reverse=True)
    SHARD_SETS["stock_minutes"] = cands[0] if cands else None

    report = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pushdown_flag": ma._QFQ_FILTER_PUSHDOWN,
              "shard_sets": {}, "log_counts": {}}

    for name, root in SHARD_SETS.items():
        if not root:
            report["shard_sets"][name] = {"error": "no dir"}
            continue
        files = sorted(Path(f) for f in glob.glob(os.path.join(root, "*.parquet")))
        sub = {"dir": os.path.basename(root), "n_shards": len(files),
               "bytes": sum(os.path.getsize(f) for f in files), "checks": {}}
        # --- ③ row-group 可裁剪性事实 ---
        pf = pq.ParquetFile(files[0])
        md = pf.metadata
        rgs = []
        for i in range(md.num_row_groups):
            rgm = md.row_group(i)
            st = next((rgm.column(j).statistics for j in range(rgm.num_columns)
                       if rgm.column(j).path_in_schema == "ts_code"), None)
            rgs.append({"rg": i, "rows": rgm.num_rows,
                        "ts_code_min": (st.min if st else None),
                        "ts_code_max": (st.max if st else None)})
        sub["row_groups_first_shard"] = rgs
        sub["prunable_by_code_filter"] = bool(md.num_row_groups > 1)
        d0 = pd.read_parquet(files[0], columns=["ts_code"])
        all_codes = sorted({str(x) for x in d0["ts_code"].unique()})
        one = [all_codes[len(all_codes) // 2]]
        many = all_codes[:50]
        ckey = f"{name}|probe"

        for label, codes in (("codes_1", one), ("codes_50", many), ("ALL", None)):
            want = {str(c) for c in codes} if codes else None
            a1 = _adapter()
            old_df, t_old, d_old = timed(lambda: legacy(a1, ckey, files, want))
            a2 = _adapter()
            new_df, t_new, d_new = timed(lambda: a2._read_ckey_filtered(ckey, files, want)) \
                if want is not None else (old_df, None, None)
            rec = {"rows_old": int(len(old_df)), "t_old_s": round(t_old, 3),
                   "rss_old_gb": round(d_old, 3)}
            if want is not None:
                # 逐行等价（列全集 + 行集；index 由下游 concat 重建，不参与语义）
                a = old_df.reset_index(drop=True).sort_values(["ts_code"]).reset_index(drop=True)
                b = new_df.reset_index(drop=True).sort_values(["ts_code"]).reset_index(drop=True)
                a = a.sort_values(list(a.columns)).reset_index(drop=True)
                b = b.sort_values(list(b.columns)).reset_index(drop=True)
                pd.testing.assert_frame_equal(a, b, check_like=False)
                rec.update({"rows_new": int(len(new_df)), "t_new_s": round(t_new, 3),
                            "rss_new_gb": round(d_new, 3),
                            "speedup": round(t_old / t_new, 2) if t_new else None,
                            "rows_equal": bool(len(old_df) == len(new_df)),
                            "frame_equal": True,
                            "lru_entries_new": len(a2._shard_table_cache)})
            else:
                rec["frame_equal"] = "ALL 路径同函数（未改）"
            sub["checks"][label] = rec
        report["shard_sets"][name] = sub

    # --- ④ 覆盖面计数（daemon.log：export_cache 命中行） ---
    if LOG.exists():
        n_hit = 0
        per_table = {}
        with LOG.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "export_cache 命中 " in line:
                    n_hit += 1
                    try:
                        ck = line.split("export_cache 命中 ", 1)[1].split(" ", 1)[0]
                        per_table[ck.split("|")[0]] = per_table.get(ck.split("|")[0], 0) + 1
                    except Exception:
                        pass
        report["log_counts"] = {"log": str(LOG), "export_cache_hit_lines": n_hit,
                                "by_table": per_table}

    out = OUT_DIR / "p1-1-verification.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("WROTE", out)


if __name__ == "__main__":
    main()
