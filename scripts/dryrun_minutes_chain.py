# -*- coding: utf-8 -*-
"""T1 §8 管线干跑：取 1 源日 → aligner → validator → 隔离区判定（**不落主库**）"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(QS))
import pandas as pd

DAY = "2026-09-11"
TABLE = "stock_minutes"
T0 = time.time()
rep = {"day": DAY, "table": TABLE, "stages": {}}


def log(m):
    print(f"[{(time.time()-T0):6.1f}s] {m}", flush=True)


def q(sql, t=300):
    url = "http://127.0.0.1:9000/exec?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=t) as r:
        return json.loads(r.read().decode("utf-8"))


# ── 1) 源取数（QuestDB，只读）──
log(f"1) 源取数 {TABLE} @ {DAY}")
d = q(f"SELECT count() FROM {TABLE} WHERE trade_time >= '{DAY}' AND trade_time < '2026-09-12'")
n_src = int(d["dataset"][0][0])
log(f"   源行数 = {n_src:,}")
# QuestDB 不支持 LIMIT/OFFSET -> 用代码子集 + 全天取样（代表性强、体积可控）
CODES = ["000001.SZ","000002.SZ","600000.SH","600036.SH","600519.SH","000651.SZ","002415.SZ",
         "300750.SZ","601318.SH","600030.SH","000858.SZ","002594.SZ","601899.SH","600276.SH",
         "300059.SZ","601012.SH","688981.SH","688111.SH","603259.SH","000333.SZ"]
in_list = ",".join("chr(39)" for _ in []) or ""
in_list = ",".join(["'"] + [c + "'" for c in CODES])
in_list = ",".join(["'" + c + "'" for c in CODES])
rows = []
for chunk in [CODES[i:i+10] for i in range(0, len(CODES), 10)]:
    lst = ",".join(["'" + c + "'" for c in chunk])
    dd = q(f"SELECT ts_code, trade_time, freq, open, high, low, close, vol, amount, adj_factor, is_qfq "
           f"FROM {TABLE} WHERE trade_time >= '{DAY}' AND trade_time < '2026-09-12' AND ts_code IN ({lst})")
    rows.extend(dd.get("dataset") or [])
raw = pd.DataFrame(rows, columns=["ts_code", "trade_time", "freq", "open", "high", "low", "close",
                                  "vol", "amount", "adj_factor", "is_qfq"])
rep["stages"]["source"] = {"rows": n_src, "fetched": len(raw)}
log(f"   取回 {len(raw):,} 行（代码子集 {len(CODES)} 只，全天）")

# ── 2) aligner ──
log("2) FieldAligner（契约对齐）")
try:
    from quantstudio.pipeline.aligner import FieldAligner
    al = FieldAligner.from_config(QS / "config" / "profiles" / "mcp_only" / "alignment_rules.json")
    t = time.time()
    aligned = al.align(raw, TABLE, "mcp")
    df_a = aligned if isinstance(aligned, pd.DataFrame) else getattr(aligned, "df", None)
    rep["stages"]["aligner"] = {"ok": True, "rows_in": len(raw),
                                "rows_out": (len(df_a) if df_a is not None else None),
                                "cols": (list(df_a.columns) if df_a is not None else None),
                                "sec": round(time.time() - t, 1)}
    log(f"   aligned {len(raw):,} -> {rep['stages']['aligner']['rows_out']:,} rows, "
        f"{len(rep['stages']['aligner']['cols'] or [])} 列, {rep['stages']['aligner']['sec']}s")
except Exception as e:
    rep["stages"]["aligner"] = {"ok": False, "error": str(e)[:300]}
    log(f"   !! aligner FAIL: {str(e)[:200]}")

# ── 3) validator ──
log("3) PreIngestValidator（校验 + 隔离区判定）")
try:
    from quantstudio.pipeline.validator import PreIngestValidator
    va = PreIngestValidator.from_config(QS / "config" / "profiles" / "mcp_only" / "alignment_rules.json")
    t = time.time()
    res = va.validate(df_a if df_a is not None else raw, TABLE, f"dryrun_{DAY}", "mcp")
    rep["stages"]["validator"] = {
        "ok": True, "passed": getattr(res, "passed", None), "failed": getattr(res, "failed", None),
        "rejected": getattr(res, "rejected", None), "fixed": getattr(res, "fixed", None),
        "warned": getattr(res, "warned", None), "sec": round(time.time() - t, 1),
        "attrs": [a for a in dir(res) if not a.startswith("_")][:12],
    }
    log(f"   validator: {json.dumps({k: v for k, v in rep['stages']['validator'].items() if k not in ('attrs',)}, ensure_ascii=False)}")
except Exception as e:
    rep["stages"]["validator"] = {"ok": False, "error": str(e)[:300]}
    log(f"   !! validator FAIL: {str(e)[:200]}")

rep["elapsed_sec"] = round(time.time() - T0, 1)
rep["note"] = "干跑：**不落主库**（无 writer 调用）"
out = QS / "data" / "logs" / f"dryrun_minutes_{DAY}.json"
out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
log(f"报告: {out}")
ok = all(rep["stages"].get(s, {}).get("ok") for s in ("aligner", "validator"))
log(f"DRYRUN: {'PASS' if ok else 'FAIL'}")
