# -*- coding: utf-8 -*-
"""F4 守卫：passthrough 覆盖后的 PK + 自定义索引 存在性巡检（零改动、纯只读）

背景（两级定谳）：
  · PT-01 段 A/B（docs/evidence/passthrough-drop-repro-20261028.md）：
    覆盖链 `CTAS _pt_tmp → INSERT → DROP 原表 → RENAME` 会**静默清空主键**（PK 1→0），
    1.4.5/1.5.5/1.5.6 三版本、16 组一致复现。
  · PT-01 段 C（docs/evidence/pt01-segC-conditions-20261109.md）：C3 实证同一覆盖链
    会**连带销毁表的全部自定义索引**（`duckdb_indexes()`：['idx_code'] → []），
    同属静默、无错无告警；C4 对照证明 `TRUNCATE + INSERT` 保留 PK 与索引。

本脚本作用：
  ① `--snapshot`：记录当前所有表的 PK 计数与自定义索引清单作为基线（JSON）
  ② `--check`   ：与基线比对，凡 **PK 从 ≥1 变 0** 或 **索引从有到无/减少** 即 ALERT（退出码 2）
  ③ 无参数      ：仅打印当前 PK 与索引清单

判型声明（铁律 18 · 新增检测型）：
  2026-11-09 裁定 R-20261109-01 = 甲（即刻扩展）。本条属**新增检测型**：
  新增索引比对后，verdict 面扩大——原「PK 未变即 PASS」的场景，现在若索引丢失会由
  PASS 变为 ALERT。这是「新增能力开始报告真相」，不是行为漂移；既有 PK 判据一字未改。

兼容性（裁定条件②）：
  旧基线（无 index_state 字段）**仍可正常 check**：PK 比对照常执行，
  索引比对打印一行 WARN「基线无索引字段，请于停服窗按 runbook §3.7 重取基线」后跳过，
  **不因此判 FAIL、不改变退出码语义**。

用法：
  python scripts/check_passthrough_pk.py --snapshot   # 仅在停服窗/副本重取基线
  python scripts/check_passthrough_pk.py --check      # 日常巡检（每日哨兵第 4 步）
  python scripts/check_passthrough_pk.py              # 打印清单

注意：
  · 严格只读（read_only=True）；daemon/once 任务运行时可能无法打开（DuckDB 单写者独占），
    此时输出「SKIP: 库被占用」并退出码 3，**不是失败**。
  · 本脚本不修改库、不修改任何既有代码。
  · `duckdb_indexes()` 不含 PK 索引（PK 走 duckdb_constraints）⇒ 两项判据互补不重复。
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


def load_state(db_path: str):
    """返回 ({table: pk_count}, {table: [index_name, ...]})；失败抛异常

    索引来源：duckdb_indexes()（仅显式 CREATE INDEX，不含 PK 索引）。
    """
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    try:
        pk_rows = con.execute(
            "SELECT table_name, count(*) FROM duckdb_constraints() "
            "WHERE constraint_type='PRIMARY KEY' GROUP BY 1"
        ).fetchall()
        idx_rows = con.execute(
            "SELECT table_name, index_name FROM duckdb_indexes() ORDER BY 1, 2"
        ).fetchall()
        tabs = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND table_type='BASE TABLE'"
        ).fetchall()]
    finally:
        con.close()
    pk_state = {t: 0 for t in tabs}
    for t, n in pk_rows:
        pk_state[t] = int(n)
    idx_state = {t: [] for t in tabs}
    for t, name in idx_rows:
        idx_state.setdefault(t, []).append(name)
        idx_state[t].sort()
    return pk_state, idx_state


def cmd_snapshot(db: str, baseline_path: str):
    pk_state, idx_state = load_state(db)
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": os.path.abspath(db),
        "pk_tables": sorted([t for t, n in pk_state.items() if n > 0]),
        "total_tables": len(pk_state),
        "pk_table_count": sum(1 for n in pk_state.values() if n > 0),
        "state": pk_state,
        # 2026-11-09 新增（裁定 R-20261109-01 甲）：自定义索引清单
        "index_state": idx_state,
        "index_table_count": sum(1 for v in idx_state.values() if v),
    }
    os.makedirs(os.path.dirname(baseline_path) or ".", exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"基线已写入: {baseline_path}")
    print(f"  表数={out['total_tables']}  有PK表数={out['pk_table_count']}  "
          f"有索引表数={out['index_table_count']}")
    return 0


def cmd_check(db: str, baseline_path: str):
    if not os.path.exists(baseline_path):
        print(f"FAIL: 基线不存在（{baseline_path}），请先运行 --snapshot")
        return 3
    with open(baseline_path, encoding="utf-8") as fh:
        base = json.load(fh)
    pk_state, idx_state = load_state(db)

    alerts, warns = [], []

    # ---------- PK 比对（既有判据，逻辑一字未改） ----------
    for t, n_base in base["state"].items():
        n_now = pk_state.get(t)
        if n_now is None:
            warns.append(f"表已消失: {t}")
            continue
        if n_base >= 1 and n_now == 0:
            alerts.append(f"★PK 丢失: {t}（{n_base} → 0）——疑似 passthrough 覆盖")
        elif n_base == 0 and n_now >= 1:
            warns.append(f"PK 新增: {t}（0 → {n_now}）")
    new_tables = [t for t in pk_state if t not in base["state"]]
    if new_tables:
        warns.append(f"新表 {len(new_tables)} 张（无基线可比）: {', '.join(new_tables[:8])}"
                     + (" ..." if len(new_tables) > 8 else ""))

    # ---------- 索引比对（2026-11-09 新增；旧基线兼容跳过） ----------
    idx_active = "index_state" in base
    if idx_active:
        for t, base_idx in base["index_state"].items():
            now_idx = idx_state.get(t)
            if now_idx is None:
                continue                      # 表消失已在 PK 段 WARN
            base_set, now_set = set(base_idx or []), set(now_idx or [])
            lost = base_set - now_set
            if lost:
                alerts.append(
                    f"★索引丢失: {t}（{'/'.join(sorted(lost))}）——疑似 passthrough 覆盖"
                    f"（基线 {len(base_set)} 个 → 现 {len(now_set)} 个）")
            added = now_set - base_set
            if added:
                warns.append(f"索引新增: {t}（{'/'.join(sorted(added))}）")
    else:
        warns.append("基线为旧结构（无 index_state 字段）⇒ 本次仅比对 PK，索引比对跳过；"
                     "请于停服窗按 runbook §3.7 重取基线后自动启用索引判据")

    # ---------- 输出 ----------
    n_pk_now = sum(1 for n in pk_state.values() if n > 0)
    n_idx_now = sum(1 for v in idx_state.values() if v)
    print(f"基线: {base['generated_at']}（有PK表 {base['pk_table_count']}/{base['total_tables']}"
          + (f"，有索引表 {base.get('index_table_count', '?')}" if idx_active else "，索引未入基线")
          + "）")
    print(f"当前: 有PK表 {n_pk_now}/{len(pk_state)}，有索引表 {n_idx_now}/{len(idx_state)}")
    print(f"判据: PK {'启用' if True else ''} / 索引 {'启用' if idx_active else '跳过（旧基线）'}")
    if alerts:
        print("\n=== ALERT ===")
        for a in alerts:
            print("  " + a)
    if warns:
        print("\n=== WARN ===")
        for w in warns:
            print("  " + w)
    if not alerts and not warns:
        print("\nOK: PK 与索引状态均与基线一致")
    if alerts:
        print(f"\n结果: ALERT（{len(alerts)} 项异常）")
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser(description="passthrough 覆盖后 PK + 索引存在性巡检（只读）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--baseline", default=os.path.join(BASELINE_DIR, BASELINE_NAME),
                    help="基线 JSON 路径（默认 docs/evidence/pk_baseline.json）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--snapshot", action="store_true", help="写入基线（仅停服窗/副本）")
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
        pk_state, idx_state = load_state(args.db)
        n_pk = sum(1 for n in pk_state.values() if n > 0)
        n_idx = sum(1 for v in idx_state.values() if v)
        print(f"表数={len(pk_state)}  有PK表数={n_pk}  有索引表数={n_idx}")
        for t in sorted(t for t, n in pk_state.items() if n > 0):
            print(f"  PK  {t}")
        for t, names in sorted(idx_state.items()):
            if names:
                print(f"  IDX {t}: {', '.join(names)}")
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
