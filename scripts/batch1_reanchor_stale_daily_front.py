# -*- coding: utf-8 -*-
"""批1：历史日线 front 物化列重锚（单一工单，默认 dry-run）。

边界：
- 只处理 output/golden_baseline/batch1_reanchor_scope.json 确认的 40 个股票代码；
- 只 UPDATE stock_daily.open_front/high_front/low_front/close_front；
- 不碰 raw/back/volume/amount/估值/分钟表/因子库；
- 期望值 = raw OHLC * qfq_aux.adj_i / qfq_aux.adj_latest（按日 as-of 因子）；
- 整批单事务，任一门禁失败全部回滚；
- --apply 前强制验证正式修复前快照 PASS+protected，并获取 3A 写锁。

用法：
  python scripts/batch1_reanchor_stale_daily_front.py --db <临时库> --aux <aux> --dry-run
  python scripts/batch1_reanchor_stale_daily_front.py --db data/quantstudio.db --aux data/qfq_aux.db --apply
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCOPE = ROOT / "output" / "golden_baseline" / "batch1_reanchor_scope.json"
DEFAULT_DB = ROOT / "data" / "quantstudio.db"
DEFAULT_AUX = ROOT / "data" / "qfq_aux.db"
SNAP_ID = "SNAP_20260818_001_a98a78c7"
SNAP_MANIFEST = ROOT / "data" / "snapshots" / SNAP_ID / "manifest.json"
SNAP_INDEX = ROOT / "data" / "snapshots" / "index.json"
OUT = ROOT / "output" / "golden_baseline"
BJ_TZ = timezone(timedelta(hours=8))
FRONT = ["open_front", "high_front", "low_front", "close_front"]
RAW = ["open", "high", "low", "close"]
TOL = 1e-9


def _snapshot_gate():
    manifest = json.loads(io.open(SNAP_MANIFEST, encoding="utf-8").read())
    index = json.loads(io.open(SNAP_INDEX, encoding="utf-8").read())
    entry = next(x for x in index["snapshots"] if x["snapshot_id"] == SNAP_ID)
    if not (manifest.get("verify_status") == "PASS"
            and manifest.get("protected") is True
            and entry.get("protected") is True
            and manifest.get("verify_recomputed_sha256") == manifest.get("logical_total_sha256")):
        raise RuntimeError("修复前快照门禁未通过（verify/protected/hash）")


def _scope_codes(path: Path):
    data = json.loads(io.open(path, encoding="utf-8").read())
    return sorted(str(x["code"]) for x in data["affected"])


def _factors(aux_path: Path, codes):
    import sqlite3
    conn = sqlite3.connect(f"file:{aux_path}?mode=ro", uri=True)
    q = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"SELECT code,time,adj_factor FROM adj_factor WHERE code IN ({q}) ORDER BY code,time",
        codes).fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["code", "time", "adj_factor"])


def _build_stage(conn, aux_path: Path, codes):
    bars = conn.execute(
        "SELECT code,time,open,high,low,close,open_front,high_front,low_front,close_front "
        "FROM stock_daily WHERE code IN (SELECT * FROM UNNEST(?)) ORDER BY code,time",
        [codes]).fetchdf()
    factors = _factors(aux_path, codes)
    if bars.empty or factors.empty:
        raise RuntimeError("bars/factors 为空")
    parts = []
    for code, b in bars.groupby("code", sort=True):
        f = factors[factors["code"] == code].sort_values("time")
        if f.empty:
            raise RuntimeError(f"{code} 无因子链")
        m = pd.merge_asof(b.sort_values("time"), f[["time", "adj_factor"]],
                          on="time", direction="backward")
        anchor = float(f.iloc[-1]["adj_factor"])
        if not math.isfinite(anchor) or anchor <= 0:
            raise RuntimeError(f"{code} anchor 非法 {anchor}")
        if m["adj_factor"].isna().any():
            raise RuntimeError(f"{code} 首段无 as-of 因子")
        m["anchor"] = anchor
        for raw, front in zip(RAW, FRONT):
            m[f"expected_{front}"] = m[raw].astype(float) * m["adj_factor"].astype(float) / anchor
        mismatch = pd.Series(False, index=m.index)
        for front in FRONT:
            cur = pd.to_numeric(m[front], errors="coerce")
            exp = m[f"expected_{front}"]
            mismatch |= cur.isna() | ((cur - exp).abs() > TOL * exp.abs().clip(lower=1.0))
        parts.append(m[mismatch].copy())
    stage = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    keep = ["code", "time"] + [f"expected_{x}" for x in FRONT]
    return stage[keep], len(bars)


def _non_front_hash(conn, codes):
    columns = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='stock_daily' "
        "ORDER BY ordinal_position").fetchall() if r[0] not in FRONT]
    expr = ",".join(f'"{c}"' for c in columns)
    rows = conn.execute(
        f"SELECT {expr} FROM stock_daily WHERE code IN (SELECT * FROM UNNEST(?)) ORDER BY code,time",
        [codes]).fetchall()
    h = hashlib.sha256()
    for row in rows:
        h.update(repr(row).encode("utf-8")); h.update(b"\n")
    return h.hexdigest(), len(rows)


def run(db_path: Path, aux_path: Path, scope_path: Path, apply: bool):
    codes = _scope_codes(scope_path)
    if len(codes) != 40:
        raise RuntimeError(f"范围门禁：期望40码，实际{len(codes)}")
    lock = None
    if apply:
        _snapshot_gate()
        from quantstudio.pipeline.snapshot_lock import acquire_write_lock
        # 3A 门禁：在打开任何 read-write 连接之前先获取写锁。
        lock = acquire_write_lock("batch1:daily_front_reanchor", timeout_s=30)
    try:
        conn = duckdb.connect(str(db_path), read_only=not apply)
    except BaseException:
        if lock is not None:
            lock.release()
        raise
    try:
        stage, scanned = _build_stage(conn, aux_path, codes)
        before_hash, before_rows = _non_front_hash(conn, codes)
        report = {
            "generated": datetime.now(BJ_TZ).isoformat(), "db": str(db_path),
            "apply": apply, "codes": codes, "codes_count": len(codes),
            "scanned_rows": scanned, "mismatch_rows": len(stage),
            "non_front_hash_before": before_hash,
        }
        if not apply:
            return report
        try:
            conn.execute("BEGIN TRANSACTION")
            conn.register("batch1_stage", stage)
            matched = conn.execute(
                "SELECT COUNT(*) FROM stock_daily t JOIN batch1_stage s "
                "ON t.code=s.code AND t.time=s.time").fetchone()[0]
            if int(matched) != len(stage):
                raise RuntimeError(f"stage匹配不全 {matched}/{len(stage)}")
            conn.execute(
                "UPDATE stock_daily t SET "
                "open_front=s.expected_open_front, high_front=s.expected_high_front, "
                "low_front=s.expected_low_front, close_front=s.expected_close_front "
                "FROM batch1_stage s WHERE t.code=s.code AND t.time=s.time")
            after_hash, after_rows = _non_front_hash(conn, codes)
            if after_rows != before_rows or after_hash != before_hash:
                raise RuntimeError("非-front列或行数发生变化，回滚")
            # postcheck：重建 stage 后必须 0 mismatch
            post, _ = _build_stage(conn, aux_path, codes)
            if len(post) != 0:
                raise RuntimeError(f"postcheck仍有 {len(post)} 行不自洽，回滚")
            conn.execute("COMMIT")
            report.update({"updated_rows": int(matched), "post_mismatch": 0,
                           "non_front_hash_after": after_hash, "status": "committed"})
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return report
    finally:
        conn.close()
        if lock is not None:
            lock.release()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--aux", type=Path, default=DEFAULT_AUX)
    ap.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    result = run(args.db, args.aux, args.scope, args.apply)
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "apply" if args.apply else "dry_run"
    out = OUT / f"batch1_reanchor_{suffix}.json"
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: result.get(k) for k in
                      ("apply", "codes_count", "scanned_rows", "mismatch_rows",
                       "updated_rows", "post_mismatch", "status")}, ensure_ascii=False))
    print(f"evidence={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
