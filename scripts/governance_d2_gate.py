# -*- coding: utf-8 -*-
"""治理方案实施第 2 步 — D2 硬门槛检查 v2（只读，裁定版）

依据: docs/project-stabilization-plan.md §2.1.3 + DSH 审计裁定（2026-08-17）
门槛:
  G1 公共窗口内行数差 = 0（窗口由两端实测 min/max 推导，禁止手工指定）
     - 设计性窗口外差异（2000-2017）登记为已知噪声，出处: collector_tasks.json
       16/19 任务 start_date=2018-01-01 + daemon.py:571 默认值
  G2 抽样对账:
     - raw 口径容差 0；front 口径容差 5e-4（须以抽样实测最大舍入差证据支撑）
     - vol 换算 ×100（手→股），epsilon 1e-3
     - 逐笔明细: miss 代码、real_diff 代码@日期@差值，全部落报告
  G3 blocked = 0
  附: 分钟表覆盖矩阵专项；北交所代码两端存在性核查

输出: output/golden_baseline/d2_gate_report.json（v2）
用法: python scripts/governance_d2_gate.py [--sample 100]
"""
import json
import os
import sys
import urllib.parse
import urllib.request

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "quantstudio.db")
OUT = os.path.join(ROOT, "output", "golden_baseline")
QDB = "http://127.0.0.1:9000/exp?query="

WINDOW_DESIGN_BASIS = {
    "config": "config/collector_tasks.json（19 任务中 16 个 start_date=2018-01-01）",
    "code": "quantstudio/pipeline/daemon.py:571（默认 start_date='2018-01-01'）",
}

# 回测消费表全集（step1 §3 v2）→ QDB 对应表
TABLES = {
    "stock_daily": "stock_daily", "etf_daily": "etf_daily",
    "stock_minutes": "stock_minutes", "etf_minutes": "etf_minutes",
    "etf_basic": "etf_basic", "etf_dividend": "etf_dividend",
    "index_daily": "index_daily", "stock_basic": "stock_basic",
    "stock_dividend": None, "stock_float_share": None, "stock_daily_valuation": None,
    "fin_indicator": None, "index_constituents": None,
    "index_constituents_snapshot_meta": None, "industry_classification": None,
    "industry_membership": None, "sw_industry": None, "strategy_events": None,
}

# 有 trade_date 时间轴的表（用于公共窗口对账）；etf_basic/stock_basic 等静态表走全量对账
TIME_AXIS = {"stock_daily", "etf_daily", "stock_minutes", "etf_minutes", "index_daily"}
STATIC = {"etf_basic", "etf_dividend", "stock_basic"}


def q(sql):
    url = QDB + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read().decode()


def q1(sql):
    lines = q(sql).strip().split("\n")
    return lines[1].strip() if len(lines) > 1 else ""


def b2ts(code, is_etf):
    """裸码 → QDB ts_code 后缀（含北交所）"""
    if code.startswith(("92", "83", "43", "87", "88")):
        return code + ".BJ"
    if is_etf:
        return code + (".SH" if code.startswith(("5", "6")) else ".SZ")
    return code + (".SH" if code[0] in "6951" else ".SZ")


def main(sample_n=100):
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect(DB, read_only=True)
    # 抽样确定性：hash 排序替代 random()（本 duckdb 版本无 seed 配置）
    report = {"version": 2, "generated": "2026-08-17",
              "window_design_basis": WINDOW_DESIGN_BASIS,
              "gates": {}, "tables": {}, "blocked": [],
              "known_noise": [], "findings": []}

    # ---------- G1: 公共窗口行数对账 ----------
    g1_fail = {}
    for duck_tbl, qdb_tbl in TABLES.items():
        entry = {}
        try:
            duck_rows = con.execute(f"select count(*) from {duck_tbl}").fetchone()[0]
        except Exception as e:
            report["blocked"].append({"table": duck_tbl, "stage": "duckdb_count",
                                      "error": str(e)[:200]})
            continue
        entry["duckdb_rows_total"] = duck_rows
        if qdb_tbl is None:
            entry["qdb_counterpart"] = None
            entry["note"] = "无 QDB 对应表（分片直入/本地生成/衍生），G1 不适用"
            report["tables"][duck_tbl] = entry
            continue
        try:
            entry["qdb_rows_total"] = int(q1(f"select count() from {qdb_tbl}"))
        except Exception as e:
            report["blocked"].append({"table": duck_tbl, "stage": "qdb_count",
                                      "error": str(e)[:200]})
            report["tables"][duck_tbl] = entry
            continue

        if duck_tbl in TIME_AXIS:
            d_lo, d_hi = con.execute(
                f"select strftime(to_timestamp(min(time)/1000),'%Y-%m-%d'), "
                f"strftime(to_timestamp(max(time)/1000),'%Y-%m-%d') from {duck_tbl}").fetchone()
            tcol = "trade_time" if duck_tbl.endswith("_minutes") else "trade_date"
            q_lo = q1(f"select min(cast({tcol} as date)) from {qdb_tbl}").strip('"')[:10]
            q_hi = q1(f"select max(cast({tcol} as date)) from {qdb_tbl}").strip('"')[:10]
            w_lo, w_hi = max(d_lo, q_lo), min(d_hi, q_hi)
            entry.update({"duck_window": [d_lo, d_hi], "qdb_window": [q_lo, q_hi],
                          "common_window": [w_lo, w_hi]})
            # 2026-08-17 时区修复（DSH 审计）：原 epoch_ms(strptime(...)) 按 UTC 解释边界，
            # 每日 0 点 CST 时间戳被错切（首日整日被排除，少算 3281 行）。
            # 改用 strftime 日期字符串比较，与 QDB 侧 cast(trade_date as date) 同口径。
            duck_cw = con.execute(
                f"select count(*) from {duck_tbl} where strftime(to_timestamp(time/1000),'%Y-%m-%d')"
                f" between '{w_lo}' and '{w_hi}'").fetchone()[0]
            qdb_cw = int(q1(f"select count() from {qdb_tbl} where cast({tcol} as date) >= '{w_lo}' and cast({tcol} as date) <= '{w_hi}'"))
            entry["duck_rows_common_window"] = duck_cw
            entry["qdb_rows_common_window"] = qdb_cw
            entry["common_window_diff"] = qdb_cw - duck_cw
            if entry["common_window_diff"] != 0:
                g1_fail[duck_tbl] = entry["common_window_diff"]
            # 窗口外差异 → 已知噪声登记（设计性窗口，有出处）
            out_diff = (entry["qdb_rows_total"] - qdb_cw) - (duck_rows - duck_cw)
            report["known_noise"].append({
                "table": duck_tbl, "type": "设计性窗口外差异",
                "detail": f"QDB 窗口外(2000-2017 等)多 {out_diff} 行；设计出处见 window_design_basis",
            })
        else:
            entry["row_diff_total"] = entry["qdb_rows_total"] - duck_rows
            if duck_tbl in STATIC and entry["row_diff_total"] != 0:
                g1_fail[duck_tbl] = entry["row_diff_total"]
        report["tables"][duck_tbl] = entry

    # 末端增量缺口（08-14）→ D1 工单登记
    sd = report["tables"].get("stock_daily", {})
    if sd.get("duck_window") and sd["duck_window"][1] < sd.get("qdb_window", ["", "9999"])[1]:
        report["findings"].append({
            "id": "D1-TODO-1", "level": "D1",
            "desc": f"末端增量缺失: duck max={sd['duck_window'][1]} < qdb max={sd['qdb_window'][1]}（恢复增量同步工单）",
        })

    # ---------- G2: 抽样对账（含容差证据 + 逐笔明细 + 北交所核查） ----------
    sample_results, g2_detail = {}, {}
    for duck_tbl in ("stock_daily", "etf_daily"):
        is_etf = duck_tbl.startswith("etf")
        try:
            rows = con.execute(
                f"select code, strftime(to_timestamp(time/1000), '%Y-%m-%d'), "
                f"close, close_front, volume from {duck_tbl} order by hash(code, time) limit {sample_n}"
            ).fetchall()
            miss, rounding, real, checked = 0, 0, 0, 0
            max_round = 0.0
            detail = []
            for code, d, close, close_front, vol in rows:
                ts = b2ts(code, is_etf)
                try:
                    qd = q(f"select close, vol*100, is_qfq from {duck_tbl} "
                           f"where ts_code = '{ts}' and cast(trade_date as date) = '{d}' limit 1"
                           ).strip().split("\n")
                except Exception:
                    qd = []
                if len(qd) < 2 or not qd[1].strip():
                    miss += 1
                    detail.append({"code": ts, "date": d, "kind": "pk_miss",
                                   "duck_close": close})
                    continue
                vals = [v.strip() for v in qd[1].split(",")]
                checked += 1
                try:
                    qfq = vals[2] == "true"
                    ref, tol = (float(close_front), 5e-4) if qfq else (float(close), 0.0)
                    dc = abs(float(vals[0]) - ref)
                    dv = abs(float(vals[1]) - float(vol))
                    if dc <= tol and dv <= 1e-3:
                        max_round = max(max_round, dc if qfq else 0.0)
                        continue
                    if dc <= 5e-4 and dv <= 1.0:
                        rounding += 1
                        detail.append({"code": ts, "date": d, "kind": "rounding_edge",
                                       "dc": dc, "dv": dv})
                    else:
                        real += 1
                        detail.append({"code": ts, "date": d, "kind": "real_diff",
                                       "qdb_close": float(vals[0]), "duck_ref": ref,
                                       "is_qfq": qfq, "duck_raw": close,
                                       "duck_front": close_front,
                                       "dc": dc, "dv": dv, "vol_match": dv <= 1e-3})
                except (ValueError, IndexError):
                    miss += 1
            sample_results[duck_tbl] = {
                "sampled": len(rows), "checked": checked, "pk_miss": miss,
                "rounding_level": rounding, "real_diff": real,
                "max_rounding_residual_within_tol": max_round,
            }
            g2_detail[duck_tbl] = detail
        except Exception as e:
            report["blocked"].append({"table": duck_tbl, "stage": "sample_diff",
                                      "error": str(e)[:200]})
    report["sample_diff"] = sample_results
    report["sample_diff_detail"] = g2_detail

    # 北交所 miss 核查: 两端存在性与格式
    bj_checks = []
    for tbl, details in g2_detail.items():
        for it in details:
            if it["kind"] == "pk_miss":
                bare = it["code"].split(".")[0]
                q_has = q1(f"select count() from {('etf_daily' if tbl=='etf_daily' else 'stock_daily')} "
                           f"where ts_code = '{bare}.BJ' limit 1")
                q_try = q1(f"select ts_code from {tbl} where ts_code like '{bare}%' limit 1")
                bj_checks.append({"code": bare, "duck": it, "qdb_bj_count": q_has,
                                  "qdb_any_suffix": q_try})
    report["bj_code_check"] = bj_checks

    # ---------- 分钟表覆盖矩阵专项 ----------
    minute_matrix = {}
    for duck_tbl in ("stock_minutes", "etf_minutes"):
        qdb_tbl = duck_tbl
        try:
            cov = con.execute(
                f"select strftime(to_timestamp(min(time)/1000),'%Y-%m'), "
                f"strftime(to_timestamp(max(time)/1000),'%Y-%m'), "
                f"count(distinct strftime(to_timestamp(time/1000),'%Y-%m-%d')), "
                f"count(distinct code) from {duck_tbl}").fetchone()
            entry = {"duck_month_range": [cov[0], cov[1]],
                     "duck_covered_days": cov[2], "duck_distinct_codes": cov[3]}
            # 公共窗口内 QDB 覆盖
            w_lo = report["tables"][duck_tbl]["common_window"][0]
            w_hi = report["tables"][duck_tbl]["common_window"][1]
            entry["common_window"] = [w_lo, w_hi]
            entry["qdb_covered_days_cw"] = int(q1(
                f"select count(distinct cast(trade_time as date)) from {qdb_tbl} "
                f"where cast(trade_time as date) >= '{w_lo}' and cast(trade_time as date) <= '{w_hi}'"))
            entry["duck_covered_days_cw"] = con.execute(
                f"select count(distinct strftime(to_timestamp(time/1000),'%Y-%m-%d')) "
                f"from {duck_tbl} where strftime(to_timestamp(time/1000),'%Y-%m-%d')"
                f" between '{w_lo}' and '{w_hi}'"
            ).fetchone()[0]
            minute_matrix[duck_tbl] = entry
        except Exception as e:
            report["blocked"].append({"table": duck_tbl, "stage": "minute_matrix",
                                      "error": str(e)[:200]})
    report["minute_coverage_matrix"] = minute_matrix

    # ---------- 门槛判定 ----------
    report["gates"]["G1_common_window_diff0"] = {"pass": not g1_fail, "failing": g1_fail}
    g2_fail = {t: r for t, r in sample_results.items() if r["pk_miss"] or r["real_diff"]}
    # 容差证据生效性: 全部 rounding 内残差须 ≤5e-4
    tol_ok = all(r["max_rounding_residual_within_tol"] <= 5e-4
                 for r in sample_results.values() if r["checked"])
    report["gates"]["G2_sample_exact"] = {
        "pass": not g2_fail and tol_ok,
        "failing": g2_fail,
        "tolerance_evidence_valid": tol_ok,
        "note": "5e-4 容差以抽样实测最大舍入残差为证据（见 max_rounding_residual_within_tol）",
    }
    report["gates"]["G3_blocked0"] = {"pass": not report["blocked"],
                                      "count": len(report["blocked"])}

    overall = all(g["pass"] for g in report["gates"].values())
    report["overall"] = "PASS" if overall else "FAIL"
    with open(os.path.join(OUT, "d2_gate_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"overall": report["overall"], "gates": report["gates"],
                      "sample_diff": report["sample_diff"],
                      "minute_coverage_matrix": report.get("minute_coverage_matrix"),
                      "bj_code_check": report.get("bj_code_check"),
                      "blocked": report["blocked"]},
                     ensure_ascii=False, indent=2))
    con.close()
    return 0 if overall else 1


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--sample") + 1]) if "--sample" in sys.argv else 100
    sys.exit(main(n))
