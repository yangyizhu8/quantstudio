# -*- coding: utf-8 -*-
"""F4 守卫：passthrough 覆盖后的 PK 存在性巡检（零改动、纯只读）

背景（PT-01 段 A 定谳，docs/evidence/passthrough-drop-repro-20261028.md）：
  passthrough 全量覆盖链 `CTAS _pt_tmp → INSERT → DROP 原表 → RENAME` 会
  **静默清空目标表的主键**（PK 1→0，不报错、不告警）。已由 1.4.5/1.5.5/1.5.6 三版本复现。

本脚本作用：
  ① `--snapshot`：记录当前所有表的 PK 状态作为基线（JSON）
  ② `--check`   ：与基线比对，凡 **PK 从 ≥1 变 0** 即 ALERT（退出码 2）
  ③ 无参数      ：仅打印当前 PK 清单

用法：
  python scripts/check_passthrough_pk.py --snapshot
  python scripts/check_passthrough_pk.py --check
  python scripts/check_passthrough_pk.py

注意：
  · 严格只读（read_only=True）；daemon 运行时可能无法打开（DuckDB 单写者独占），
    此时输出「SKIP: 库被占用」并退出码 3，不是失败。
  · 本脚本不修改库、不修改任何既有代码；属新增检测型能力。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

DEFAULT_DB = os.path.join("data", "quantstudio.db")
BASELINE_DIR = os.path.join("docs", "evidence")
BASELINE_NAME = "pk_baseline.json"


def load_pk_state(db_path: str):
    """返回 {table: pk_count}；失败抛异常"""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute(
            "SELECT table_name, count(*) FROM duckdb_constraints() "
            "WHERE constraint_type='PRIMARY KEY' GROUP BY 1"
        ).fetchall()
        tabs = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchall()]
    finally:
        con.close()
    state = {t: 0 for t in tabs}
    for t, n in rows:
        state[t] = int(n)
    return state


def cmd_snapshot(db: str, baseline_path: str):
    state = load_pk_state(db)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": os.path.abspath(db),
        "pk_tables": sorted([t for t, n in state.items() if n > 0]),
        "total_tables": len(state),
        "pk_table_count": sum(1 for n in state.values() if n > 0),
        "state": state,
    }
    os.makedirs(os.path.dirname(baseline_path) or ".", exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"基线已写入: {baseline_path}")
    print(f"  表数={out['total_tables']}  有PK表数={out['pk_table_count']}")
    return 0


def cmd_check(db: str, baseline_path: str):
    path = baseline_path
    if not os.path.exists(path):
        print(f"FAIL: 基线不存在（{path}），请先运行 --snapshot")
        return 3
    with open(path, encoding="utf-8") as fh:
        base = json.load(fh)
    state = load_pk_state(db)
    alerts, warns = [], []
    for t, n_base in base["state"].items():
        n_now = state.get(t)
        if n_now is None:
            warns.append(f"表已消失: {t}")
            continue
        if n_base >= 1 and n_now == 0:
            alerts.append(f"★PK 丢失: {t}（{n_base} → 0）——疑似 passthrough 覆盖")
        elif n_base == 0 and n_now >= 1:
            warns.append(f"PK 新增: {t}（0 → {n_now}）")
    new_tables = [t for t in state if t not in base["state"]]
    if new_tables:
        warns.append(f"新表 {len(new_tables)} 张（无基线可比）: {', '.join(new_tables[:8])}"
                     + (" ..." if len(new_tables) > 8 else ""))

    print(f"基线: {base['generated_at']}（有PK表 {base['pk_table_count']}/{base['total_tables']}）")
    print(f"当前: 有PK表 {sum(1 for n in state.values() if n > 0)}/{len(state)}")
    if alerts:
        print("\n=== ALERT（PK 从 ≥1 变 0）===")
        for a in alerts:
            print("  " + a)
    if warns:
        print("\n=== WARN ===")
        for w in warns:
            print("  " + w)
    if not alerts and not warns:
        print("\nOK: PK 状态与基线一致")
    if alerts:
        print(f"\n结果: ALERT（{len(alerts)} 张表 PK 丢失）")
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser(description="passthrough 覆盖后 PK 存在性巡检（只读）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--baseline", default=os.path.join(BASELINE_DIR, BASELINE_NAME),
                    help="基线 JSON 路径（默认 docs/evidence/pk_baseline.json）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--snapshot", action="store_true", help="写入基线")
    g.add_argument("--check", action="store_true", help="与基线比对")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"FAIL: 库不存在 {args.db}")
        return 3
    try:
        if args.snapshot:
            return cmd_snapshot(args.db, args.baseline)
        if args.check:
            return cmd_check(args.db, args.baseline)
        state = load_pk_state(args.db)
        n_pk = sum(1 for n in state.values() if n > 0)
        print(f"表数={len(state)}  有PK表数={n_pk}")
        for t in sorted(t for t, n in state.items() if n > 0):
            print(f"  PK  {t}")
        return 0
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "Cannot open file" in msg or "already open" in msg or "另一个程序" in msg:
            print(f"SKIP: 库被占用（daemon 运行中），请在停服窗或副本上执行。{msg[:120]}")
            return 3
        print(f"FAIL: {msg[:240]}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
