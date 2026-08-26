# -*- coding: utf-8 -*-
"""C1 双端宇宙 diff（P-D11 系前置取证 / master-plan WP-C1，2026-08-26，只读）。

对比：
  - 平台快照：data/ptrade_fidelity/ashares_<date>.parquet（探针甲回贴构建，
    平台 get_Ashares() 原样，如 2026-07-01 total=5205）；
  - 本地宇宙：stock_daily 在 as-of 日（<= before_ms 的最新交易日）有行情的
    DISTINCT code（duckdb_data_access.query_all_stocks 同口径，
    本地日志 FUN A=5511）。

输出：两端集合差 + 按板块前缀分类构成 + 样例清单，供 C1 处置裁定
（P-A0 快照口径收尾 vs 本地范围开关）。

用法：python scripts/c1_universe_diff.py [--asof 2026-07-01] [--db data/quantstudio.db]
只读：不写任何库表；结果打印 + 可选 --json <path> 落盘。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def _bare(code: str) -> str:
    s = str(code).strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    return s[:max(i, 6)] if s[:6].isdigit() else s[:6]


_BOARD_RULES = (
    ("68", "SH科创板"),
    ("60", "SH主板"),
    ("00", "SZ主板(000/001/002/003)"),
    ("30", "SZ创业板"),
    ("92", "北交所(920新码)"),
    ("43", "北交所(430原新三板)"),
    ("83", "北交所(83原新三板)"),
    ("87", "北交所(87原新三板)"),
    ("88", "北交所(88原新三板)"),
)


def _board(bare: str) -> str:
    for prefix, name in _BOARD_RULES:
        if bare.startswith(prefix):
            return name
    return "其他/未分类"


def _asof_ms(asof: str) -> int:
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=8))
    dt = datetime.strptime(asof, "%Y-%m-%d").replace(hour=23, minute=59,
                                                     second=59, tzinfo=tz)
    return int(dt.timestamp() * 1000)


def load_snapshot(snapshot_dir: Path, asof: str) -> set:
    import pyarrow.parquet as pq
    cands = sorted(snapshot_dir.glob("ashares_*.parquet"))
    if not cands:
        raise SystemExit(f"快照不存在: {snapshot_dir}")
    # 优先 asof 同日快照，否则最新一份（打印提示）
    exact = snapshot_dir / f"ashares_{asof}.parquet"
    path = exact if exact.exists() else cands[-1]
    codes = {_bare(c) for c in pq.read_table(path).column(0).to_pylist()}
    return codes, path.name


def load_local(db_path: Path, asof: str) -> set:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        ms = _asof_ms(asof)
        rows = conn.execute(
            """
            SELECT DISTINCT code FROM stock_daily WHERE time = (
                SELECT MAX(time) FROM stock_daily WHERE time <= ?
            )
            """, [ms]).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-07-01")
    ap.add_argument("--db", default=str(ROOT / "data" / "quantstudio.db"))
    ap.add_argument("--json", default=None, help="可选：结果落盘 JSON 路径")
    args = ap.parse_args()

    snap, snap_name = load_snapshot(ROOT / "data" / "ptrade_fidelity", args.asof)
    local = load_local(Path(args.db), args.asof)

    only_local = sorted(local - snap)
    only_snap = sorted(snap - local)
    common = local & snap

    def composition(codes):
        stat = {}
        for c in codes:
            stat[_board(c)] = stat.get(_board(c), 0) + 1
        return dict(sorted(stat.items(), key=lambda kv: -kv[1]))

    print(f"[C1] asof={args.asof} snapshot={snap_name}")
    print(f"[C1] 平台快照 n={len(snap)}  本地宇宙 n={len(local)}  交集 n={len(common)}")
    print(f"[C1] 仅本地（本地多覆盖）n={len(only_local)}")
    print(f"[C1]   构成: {composition(only_local)}")
    print(f"[C1]   样例: {only_local[:30]}")
    print(f"[C1] 仅平台（快照有本地无）n={len(only_snap)}")
    print(f"[C1]   构成: {composition(only_snap)}")
    print(f"[C1]   样例: {only_snap[:30]}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "asof": args.asof, "snapshot_file": snap_name,
            "snapshot_n": len(snap), "local_n": len(local),
            "common_n": len(common),
            "only_local": only_local, "only_local_board": composition(only_local),
            "only_snapshot": only_snap, "only_snapshot_board": composition(only_snap),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[C1] JSON 已落盘: {args.json}")


if __name__ == "__main__":
    main()
