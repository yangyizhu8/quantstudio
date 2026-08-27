# -*- coding: utf-8 -*-
"""v6.7.53 等价性验收：_load_factor_lookup 向量化修复前后 (code,day)->factor 字典逐键一致。

旧实现 = 逐行 _bar_day_from_ms(pd.Series([单行]))（内联复刻，不改动已修复模块）；
新实现 = 修复后的 quantstudio.pipeline.qfq_invariant._load_factor_lookup。
数据源：真实 data/qfq_aux.db adj_factor 前 100,000 行（含边界：NULL 行若存在必须一致跳过）。
"""
import sqlite3
import sys
import time
from typing import Dict, Tuple

import pandas as pd

sys.path.insert(0, ".")
from quantstudio.pipeline.qfq_invariant import _load_factor_lookup, _bar_day_from_ms, open_ro_sqlite


def old_load_factor_lookup(aux_conn, adj_table, codes) -> Dict[Tuple[str, str], float]:
    """修复前实现（qfq_invariant.py:261-270 原样复刻）。"""
    lookup: Dict[Tuple[str, str], float] = {}
    if not codes:
        return lookup
    placeholders = ", ".join(["?"] * len(codes))
    rows = aux_conn.execute(
        f"SELECT code, time, adj_factor FROM {adj_table} "
        f"WHERE code IN ({placeholders})", list(codes)).fetchall()
    best: Dict[Tuple[str, str], Tuple[int, float]] = {}
    for code, t_ms, factor in rows:
        if factor is None or t_ms is None:
            continue
        day = _bar_day_from_ms(pd.Series([int(t_ms)])).iloc[0]
        key = (str(code), day)
        prev = best.get(key)
        if prev is None or int(t_ms) > prev[0]:
            best[key] = (int(t_ms), float(factor))
    lookup = {k: v[1] for k, v in best.items()}
    return lookup


def main():
    aux = open_ro_sqlite("data/qfq_aux.db")
    # 按 code 字典序取前 N 只，使其全历史因子 ≈ 10 万行（_load_factor_lookup
    # 语义 = 加载给定 code 的全部因子行；行数可控且含真实 NULL/同日多行边界）
    all_codes = [r[0] for r in aux.execute(
        "SELECT DISTINCT code FROM adj_factor ORDER BY code").fetchall()]
    codes, acc = [], 0
    for c in all_codes:
        n = aux.execute("SELECT COUNT(*) FROM adj_factor WHERE code = ?", (c,)).fetchone()[0]
        codes.append(c)
        acc += n
        if acc >= 100_000:
            break
    ph = ",".join("?" * len(codes))
    n_rows = aux.execute(
        f"SELECT COUNT(*) FROM adj_factor WHERE code IN ({ph})", codes).fetchone()[0]
    n_null = aux.execute(
        f"SELECT COUNT(*) FROM adj_factor WHERE code IN ({ph}) "
        f"AND (adj_factor IS NULL OR time IS NULL)", codes).fetchone()[0]
    print(f"codes={len(codes)} rows={n_rows} null_rows={n_null}", flush=True)

    t0 = time.perf_counter()
    old = old_load_factor_lookup(aux, "adj_factor", codes)
    t_old = time.perf_counter() - t0

    t0 = time.perf_counter()
    new = _load_factor_lookup(aux, "adj_factor", codes)
    t_new = time.perf_counter() - t0

    print(f"old: {len(old)} keys, {t_old:.2f}s")
    print(f"new: {len(new)} keys, {t_new:.2f}s")
    print(f"speedup: {t_old / max(t_new, 1e-9):.0f}x")

    assert len(old) == len(new), f"key count mismatch: {len(old)} vs {len(new)}"
    diff = [k for k in old if k not in new or old[k] != new[k]]
    if diff:
        print(f"MISMATCH {len(diff)} keys, e.g. {diff[:5]}")
        sys.exit(1)
    print("PASS: 新旧实现 (code,day)->factor 字典逐键一致")
    aux.close()


if __name__ == "__main__":
    main()
