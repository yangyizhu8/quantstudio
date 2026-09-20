# -*- coding: utf-8 -*-
"""引擎级双跑对比器（M0 新工件 · 方案件 §2 · 2026-09-20）。

对比「Python 原版引擎 vs Rust 内核引擎」同平台同数据逐位回归（G3.5 零容差）。
L1-L4 平台间对比器不可复用（方案件 §2.1）。

双通道比对（②审补充指示⑤）：
  主通道（全精度，裁决权威）：worker 进程内直接跑 BacktestEngine API，从 result 对象
    导出 nav_history / trade_records 的 %.17g 全精度镜像（repr/17g 字符串，杜绝 CSV 截断盲区）。
  第二通道（CSV 字符串级）：worker 的 output/backtest_results 导出目录内 trades.csv 逐行字符串比对。
  两通道不一致时以全精度通道裁决。

QS_FILL_AUDIT 审计通道：worker 挂内存 logging handler 捕获裸消息体（不含时间戳/级别，
时间戳类字段处理原则：比对消息体规避不可比时间戳）；分钟档无审计行产出时如实报告
（其派生量已由 trades 全精度通道位级覆盖）。

用法：
  python scripts/double_run_engine.py --case U1|U2|U3|U4|U5 [--all]
  python scripts/double_run_engine.py --case U4 --self-test-negative   # 四谱负样本
  python scripts/double_run_engine.py --case U1 --rust-kernel          # 占位 fail-fast
报告：output/double_run/<case>_<ts>/report.json（+ A/B 全精度镜像）
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BR_DB = r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly\quantstudio.db"
SNAP_DB = (r"D:\miniQMT策略实盘\QuantStudio\data\snapshots"
           r"\SNAP_20260825_003_81260e83\quantstudio.db")

CASES = {
    # 用例: (策略文件, 起, 止, profile, price_mode, db, 数据根)
    "U1": ("连板梯队龙头打板套利策略.py", "2026-03-02", "2026-03-03",
           "minute-bar-v1", "close", BR_DB,
           r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly"),
    "U2": ("smallcap_overnight_scalp_7_quantstudio.py", "2026-07-15", "2026-07-17",
           "minute-bar-v1", "close", SNAP_DB,
           r"D:\miniQMT策略实盘\QuantStudio\data\snapshots\SNAP_20260825_003_81260e83"),
    "U3": ("连板梯队龙头打板套利策略.py", "2026-02-25", "2026-03-06",
           "minute-bar-v1", "close", BR_DB,
           r"D:\miniQMT策略实盘\QuantStudio\agent_workspace\backtest_readonly"),
    "U4": ("小市值策略ptrade.py", "2026-07-01", "2026-07-31",
           "daily-bar-v1", "close", SNAP_DB,
           r"D:\miniQMT策略实盘\QuantStudio\data\snapshots\SNAP_20260825_003_81260e83"),
    "U5": ("smallcap_overnight_scalp_7_quantstudio.py", "2026-07-15", "2026-07-15",
           "minute-bar-v1", "close", SNAP_DB,
           r"D:\miniQMT策略实盘\QuantStudio\data\snapshots\SNAP_20260825_003_81260e83"),
}


# ---------------------------------------------------------------- worker ----
class _AuditCollector(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records = []

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if msg.startswith("QS_FILL_AUDIT"):
            self.records.append(msg)


def g17(x):
    """全精度浮点字符串（17 位有效，round-trip 保证；不同 f64 必不同串）。"""
    return "%.17g" % float(x)


def run_worker(case, out_dir):
    """worker 模式：进程内跑 BacktestEngine API，导出全精度三件套镜像。"""
    strat, s, e, profile, price_mode, db, data_root = CASES[case]
    os.environ["QUANTSTUDIO_DATA_ROOT"] = data_root
    os.environ["PYTHONIOENCODING"] = "utf-8"
    from quantstudio.backtest.backtest_engine import BacktestEngine
    from quantstudio.backtest.strategy_runner import load_strategy

    collector = _AuditCollector()
    hlog = logging.getLogger("quantstudio.backtest.backtest_engine")
    hlog.addHandler(collector)
    sp = ROOT / "quantstudio" / "backtest" / "strategies" / strat
    functions, _m = load_strategy(sp)
    t0 = time.perf_counter()
    eng = BacktestEngine(db_path=db, strategy=functions, start=s, end=e,
                         capital=100000.0, match_price_mode=price_mode,
                         engine_profile=profile)
    res, _o = eng.run()
    wall = time.perf_counter() - t0

    nav = [{"date": r["date"], "nav": g17(r["nav"]), "cash": g17(r["cash"]),
            "market_value": g17(r["market_value"]),
            "benchmark": g17(r["benchmark"]), "positions": int(r["positions"])}
           for r in (res.nav_history or [])]
    trades = [{"date": r["date"], "code": r["code"], "action": r["action"],
               "volume": int(r["volume"]), "price": g17(r["price"]),
               "commission": g17(r["commission"]), "tax": g17(r["tax"]),
               "pnl": g17(r["pnl"])}
              for r in (res.trade_records or [])]
    out = {"case": case, "wall_s": round(wall, 3), "nav_count": len(nav),
           "trade_count": len(trades), "nav": nav, "trades": trades,
           "audit_lines": collector.records}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "mirror.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[worker] %s nav=%d trades=%d audit=%d wall=%.1fs -> %s"
          % (case, len(nav), len(trades), len(collector.records), wall, out_dir))
    return out


# --------------------------------------------------------------- compare ----
def first_diff(a_list, b_list, key_fields, label, diffs):
    """逐行逐字段字符串级比对（全精度镜像已是字符串，== 即位级）。"""
    if len(a_list) != len(b_list):
        diffs.append({"item": label, "field": "__rowcount__",
                      "A": len(a_list), "B": len(b_list)})
        return
    for i, (ra, rb) in enumerate(zip(a_list, b_list)):
        for f in key_fields:
            if str(ra.get(f)) != str(rb.get(f)):
                diffs.append({"item": label, "row": i, "field": f,
                              "A": str(ra.get(f)), "B": str(rb.get(f))})


def compare_mirrors(a, b):
    diffs = []
    first_diff(a["nav"], b["nav"],
               ["date", "nav", "cash", "market_value", "benchmark", "positions"],
               "nav_full", diffs)
    first_diff(a["trades"], b["trades"],
               ["date", "code", "action", "volume", "price", "commission",
                "tax", "pnl"], "trades_full", diffs)
    if a["audit_lines"] or b["audit_lines"]:
        if len(a["audit_lines"]) != len(b["audit_lines"]):
            diffs.append({"item": "audit", "field": "__rowcount__",
                          "A": len(a["audit_lines"]), "B": len(b["audit_lines"])})
        else:
            for i, (x, y) in enumerate(zip(a["audit_lines"], b["audit_lines"])):
                if x != y:
                    diffs.append({"item": "audit", "row": i, "field": "msg",
                                  "A": x[:160], "B": y[:160]})
    return diffs


def find_result_dir_since(ts):
    """定位 mtime >= ts 的引擎导出目录（API 模式后缀恒为 _strategy，按时间定位）。"""
    base = ROOT / "output" / "backtest_results"
    if not base.exists():
        return None
    dirs = [d for d in base.iterdir()
            if d.is_dir() and d.stat().st_mtime >= ts]
    return sorted(dirs, key=lambda d: d.stat().st_mtime)[-1] if dirs else None


def compare_csv(dir_a, dir_b, diffs):
    """第二通道：trades.csv 逐行字符串级（截断格式；仅作旁证，全精度通道裁决）。"""
    if not dir_a or not dir_b:
        diffs.append({"item": "csv_channel", "field": "__locate__",
                      "A": str(dir_a), "B": str(dir_b)})
        return
    ta = list((dir_a / "trades.csv").read_text(encoding="utf-8-sig").splitlines()) \
        if (dir_a / "trades.csv").exists() else None
    tb = list((dir_b / "trades.csv").read_text(encoding="utf-8-sig").splitlines()) \
        if (dir_b / "trades.csv").exists() else None
    if ta is None and tb is None:
        return  # 双侧均无 trades.csv（零成交）——第二通道无料，主通道已覆盖
    if ta is None or tb is None:
        diffs.append({"item": "csv_channel", "field": "__exists__",
                      "A": ta is not None, "B": tb is not None})
        return
    if len(ta) != len(tb):
        diffs.append({"item": "csv_channel", "field": "__rowcount__",
                      "A": len(ta), "B": len(tb)})
        return
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x != y:
            diffs.append({"item": "csv_channel", "row": i, "field": "line",
                          "A": x[:160], "B": y[:160]})


# ------------------------------------------------------- negative 4 谱 ----
def perturb(mirror, kind):
    import copy
    m = copy.deepcopy(mirror)
    if kind == "nav_1ulp" and m["nav"]:
        i = len(m["nav"]) // 2
        v = float(m["nav"][i]["nav"])
        m["nav"][i]["nav"] = g17(math.nextafter(v, math.inf))
    elif kind == "trade_1ulp" and m["trades"]:
        i = len(m["trades"]) // 2
        v = float(m["trades"][i]["price"])
        m["trades"][i]["price"] = g17(math.nextafter(v, math.inf))
    elif kind == "code_char" and m["trades"]:
        m["trades"][0]["code"] = ("X" + str(m["trades"][0]["code"])[1:])
    elif kind == "row_drop":
        if m["trades"]:
            m["trades"].pop(len(m["trades"]) // 2)
        elif m["nav"]:
            m["nav"].pop(len(m["nav"]) // 2)
        else:
            return None
    else:
        return None
    return m


# ------------------------------------------------------------------ main ----
def run_case(case, negative=False):
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_dir = ROOT / "output" / "double_run" / ("%s_%s" % (case, ts))
    a_dir, b_dir = report_dir / "A", report_dir / "B"

    def spawn(tag_dir):
        t0 = time.time()
        r = subprocess.run([sys.executable, __file__, "--worker", case,
                            "--out", str(tag_dir)], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            return None, ("worker: " + (r.stderr or r.stdout or "")[-400:])
        return find_result_dir_since(t0), None

    dir_a, err = spawn(a_dir)
    if err:
        return {"case": case, "pass": False, "error": "A " + err}
    dir_b, err = spawn(b_dir)
    if err:
        return {"case": case, "pass": False, "error": "B " + err}

    a = json.loads((a_dir / "mirror.json").read_text(encoding="utf-8"))
    b = json.loads((b_dir / "mirror.json").read_text(encoding="utf-8"))

    report = {"case": case, "strategy": CASES[case][0],
              "a_wall_s": a["wall_s"], "b_wall_s": b["wall_s"],
              "nav_count": a["nav_count"], "trade_count": a["trade_count"],
              "audit_lines": a.get("audit_lines") and len(a["audit_lines"]) or 0}
    diffs = compare_mirrors(a, b)
    compare_csv(dir_a, dir_b, diffs)
    report["diffs_full_channel"] = diffs if diffs else []
    report["pass"] = not diffs

    if negative:
        specs = [("nav_1ulp", "谱a nav 1ulp"), ("trade_1ulp", "谱b trades 1ulp"),
                 ("code_char", "谱c code 单字符"), ("row_drop", "谱d 增删行")]
        report["negative"] = []
        for kind, label in specs:
            pert = perturb(a, kind)
            if pert is None:
                report["negative"].append({"kind": kind, "label": label,
                                           "fail_expected": None,
                                           "note": "无可用扰动对象"})
                continue
            d = compare_mirrors(pert, b)
            report["negative"].append({
                "kind": kind, "label": label, "fail_expected": bool(d),
                "first_diff": d[0] if d else None})
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[%s] pass=%s nav=%d trades=%d audit=%d%s%s"
          % (case, report["pass"], report["nav_count"], report["trade_count"],
             report["audit_lines"],
             ("  negative=" + json.dumps(
                 [{"k": n["kind"], "f": n["fail_expected"]}
                  for n in report.get("negative", [])])) if negative else "",
             "" if report["pass"] else "  DIFFS:" + json.dumps(diffs[:3])))
    print("  report: %s" % (report_dir / "report.json"))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="U4")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--worker", default=None, metavar="CASE",
                    help="worker 模式：进程内跑 CASE 并导出全精度镜像")
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test-negative", action="store_true")
    ap.add_argument("--rust-kernel", action="store_true")
    args = ap.parse_args()

    if args.rust_kernel:
        print("❌ --rust-kernel 尚未实现（M1+ 段注入；M0 占位 fail-fast，禁止静默跑成 "
              "Python 版）")
        sys.exit(3)
    if args.worker:
        if args.out is None:
            print("❌ --worker 需要 --out 目录")
            sys.exit(2)
        run_worker(args.worker, pathlib.Path(args.out))
        return
    cases = sorted(CASES) if args.all else [args.case]
    results = [run_case(c, negative=args.self_test_negative) for c in cases]
    allpass = all(r.get("pass") for r in results)
    print("DOUBLE_RUN", "PASS" if allpass else "FAIL")
    sys.exit(0 if allpass else 1)


if __name__ == "__main__":
    main()
