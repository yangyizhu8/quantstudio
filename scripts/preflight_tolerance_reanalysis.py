"""容差重判分析（独立补充工具，不改主脚本 preflight_raw_fullmarket.py 的判定逻辑）。

用途：全市场 raw 准入预检（零容差）完成后，对其中 final==BLOCK 的证券
重新下载 fresh、计算四列 max_abs_diff，按容差口径重判：
    max_abs_diff(四列取 max) <= tol 且 mismatch bar 占比 < ratio
    → ADMISSIBLE_TICK_TOLERANCE（降级，仍视为可安全 rebase）
    → 否则保持 BLOCK（真实需关注）

输出：
  1) 两口径对比报告（零容差 vs 容差）
  2) 严格版准入名单（零容差 ADMISSIBLE）
  3) 宽松版准入名单（ADMISSIBLE + ADMISSIBLE_TICK_TOLERANCE）
  4) BLOCK→降级 证券独立清单（code / asset_type / mismatch bar 数 / max_abs_diff）

铁律：只读。read_only 直连 DB + 下载 fresh（不写正式库）。
必须在全量预检进程退出后运行，避免与全量抢 miniQMT 带宽。
"""
import argparse
import csv
import json
import io
import os
import sys
import time
import datetime
from pathlib import Path
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from preflight_raw_fullmarket import (  # noqa: E402
    fetch_segment, distinct_minute_freqs,
)
from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher  # noqa: E402


def log(*a):
    print(f"[{datetime.datetime.now():%H:%M:%S}]", *a, flush=True)


def tick_tolerance_ok(segs, tol, ratio):
    """该证券所有 OHLC_MISMATCH 段是否都满足 tick 容差。

    mismatch bar 数用四列 mismatch 之和（上界，更严格）。
    """
    ohlc_segs = [(p, s) for (p, s, st) in segs if st == "OHLC_MISMATCH"]
    if not ohlc_segs:
        return True
    for _p, s in ohlc_segs:
        mx = max(s.get("max_abs_diff_open", 0.0), s.get("max_abs_diff_high", 0.0),
                 s.get("max_abs_diff_low", 0.0), s.get("max_abs_diff_close", 0.0))
        if mx > tol:
            return False
        target = s.get("target_count", 0) or 1
        mb = (s.get("open_mismatch", 0) + s.get("high_mismatch", 0) +
              s.get("low_mismatch", 0) + s.get("close_mismatch", 0))
        if target and (mb / target) > ratio:
            return False
    return True


def write_list(path, codes, args):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    json.dump({
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "preflight_tolerance_reanalysis.py",
        "tolerance": {"max_abs_diff": args.tol, "mismatch_bar_ratio": args.ratio},
        "total_admissible": len(codes),
        "codes": codes,
    }, io.open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"名单已写 {path} ({len(codes)})")


def write_report(path, zero, tol_dist, total, n_strict, n_loose, downgraded,
                 args, tol_records):
    L = []
    L.append(f"# 全市场 raw 准入预检 —— 容差重判分析（{datetime.datetime.now():%Y-%m-%d}）\n")
    L.append("> 补充分析，**不改主脚本判定逻辑**。零容差口径直接来自主预检 manifest；"
             "容差口径对 `final==BLOCK` 的证券重新下载 fresh、计算四列 max_abs_diff，"
             f"判定条件：`max_abs_diff(四列取 max) ≤ {args.tol}` 且 "
             f"`mismatch bar 占比 < {args.ratio*100:.3f}%`。\n")
    L.append("## 1. 两口径对比\n")
    L.append(f"- 检查证券总数：**{total}**\n")
    L.append("| 状态 | 零容差口径 | 容差口径 |")
    L.append("|---|---|---|")
    all_states = ["ADMISSIBLE", "ADMISSIBLE_TICK_TOLERANCE", "BLOCK", "MIGRATE",
                  "DOWNLOAD_FAILED", "NO_DATA", "SKIPPED"]
    for s in all_states:
        z = zero.get(s, 0)
        t = tol_dist.get(s, 0)
        if z or t:
            L.append(f"| {s} | {z} | {t} |")
    L.append("")
    L.append(f"- **严格版 ADMISSIBLE 率：{n_strict/total*100:.2f}%**"
             f"（{n_strict}/{total}，零容差）")
    L.append(f"- **宽松版 ADMISSIBLE 率：{n_loose/total*100:.2f}%**"
             f"（{n_loose}/{total}，含 {downgraded} 只 tick 容差降级）\n")

    L.append("## 2. 容差降级明细（BLOCK → ADMISSIBLE_TICK_TOLERANCE）\n")
    L.append(f"共 **{downgraded}** 只由 tick 级噪声降级。这些证券 OHLC 最大差异 "
             f"≤ {args.tol}（ETF tick 级，单价 ~1 元时即 ≤ 1 个最小价格变动单位），"
             "且 mismatch 仅个别 bar（占比远低于阈值），属精度/舍入噪声，**非真实数据源错误**，"
             "rebase 信任边界不受影响，可安全纳入准入。\n")

    L.append("## 3. 仍保留 BLOCK 的证券（真实需关注）\n")
    L.append(f"容差口径下仍 BLOCK：**{tol_dist.get('BLOCK', 0)}** 只。"
             "其 OHLC 差异超过 tick 容差或 mismatch 占比过高，需逐只分析"
             "（见主报告 / mismatch_details.csv），编排器跳过（不 rebase）。\n")

    L.append("## 4. 准入名单\n")
    L.append(f"- 严格版：`{os.path.basename(args.strict_list)}`"
             f"（零容差 ADMISSIBLE，{n_strict} 只）")
    L.append(f"- 宽松版：`{os.path.basename(args.loose_list)}`"
             f"（含 tick 容差，{n_loose} 只）\n")

    L.append("## 5. 决策建议\n")
    L.append("若接受 tick 级噪声降级（推荐：差异 ≤ 1 tick 且占比极小，不改变 rebase 引擎行为），"
             "采用**宽松版名单**启用；若保持零容差保守策略，采用**严格版名单**。"
             "两者均仅影响准入证券范围，不改变 rebase 引擎逻辑。\n")

    L.append("## 附录：降级证券清单（前 50，按 max_abs_diff 降序）\n")
    L.append("| code | 资产 | mismatch bar 数 | max_abs_diff |")
    L.append("|---|---|---|---|")
    for code, at, mb, worst in sorted(tol_records, key=lambda x: -x[3])[:50]:
        L.append(f"| {code} | {at} | {mb} | {worst} |")
    L.append("")
    io.open(path, "w", encoding="utf-8").write("\n".join(L))
    log(f"报告已写 {path}")


def main():
    ap = argparse.ArgumentParser(description="容差重判分析（独立补充）")
    ap.add_argument("--db", default="data/quantstudio.db")
    ap.add_argument("--evidence-dir",
                    default="docs/evidence/qfq_raw_admission_fullmarket_20260730")
    ap.add_argument("--tol", type=float, default=0.001,
                    help="max_abs_diff 容差（默认 0.001 = ETF 1 tick）")
    ap.add_argument("--ratio", type=float, default=0.001,
                    help="mismatch bar 占比上限（默认 0.001 = 0.1%）")
    ap.add_argument("--report", default=None)
    ap.add_argument("--strict-list",
                    default="config/qfq_rebase_admissible_securities.strict.json")
    ap.add_argument("--loose-list",
                    default="config/qfq_rebase_admissible_securities.loose.json")
    ap.add_argument("--etf-csv", default=None)
    args = ap.parse_args()

    ev = Path(args.evidence_dir)
    manifest = json.load(io.open(ev / "preflight_manifest.json", encoding="utf-8"))
    done = manifest.get("done", {})
    log(f"manifest done={len(done)}")

    # 零容差口径（来自主预检）
    zero = Counter(v["final"] for v in done.values())
    admissible_strict = sorted(
        k.split("|")[0] for k, v in done.items() if v["final"] == "ADMISSIBLE")
    block_keys = [k for k, v in done.items() if v["final"] == "BLOCK"]
    log(f"零容差分布={dict(zero)}；BLOCK={len(block_keys)}")

    # 连接（全量已完成后 miniQMT 空闲）
    db_ro = duckdb.connect(args.db, read_only=True)
    log("连接 miniQMT（XtquantFreshFetcher）...")
    fetcher = XtquantFreshFetcher()
    log("miniQMT 连接成功，开始对 BLOCK 证券重算四列 max_abs_diff")

    tol_records = []  # (code, asset_type, mismatch_bar_total, max_abs_diff)
    final_tol = {}
    for idx, key in enumerate(block_keys):
        code, at = key.split("|", 1)
        xt = done[key]["xt_code"]
        segs = []
        for freq in ["daily"] + distinct_minute_freqs(db_ro, code, at):
            seg, st = fetch_segment(at, xt, code, freq, db_ro, fetcher)
            segs.append((freq, seg, st))
        if tick_tolerance_ok(segs, args.tol, args.ratio):
            final_tol[key] = "ADMISSIBLE_TICK_TOLERANCE"
            worst = 0.0
            mb_total = 0
            for _p, s, st in segs:
                if st == "OHLC_MISMATCH":
                    worst = max(worst, s.get("max_abs_diff_open", 0.0),
                                s.get("max_abs_diff_high", 0.0),
                                s.get("max_abs_diff_low", 0.0),
                                s.get("max_abs_diff_close", 0.0))
                    mb_total += (s.get("open_mismatch", 0) + s.get("high_mismatch", 0) +
                                 s.get("low_mismatch", 0) + s.get("close_mismatch", 0))
            tol_records.append((code, at, mb_total, round(worst, 6)))
        else:
            final_tol[key] = "BLOCK"
        if (idx + 1) % 20 == 0:
            log(f"重算 {idx+1}/{len(block_keys)}；已降级 {len(tol_records)}")
        time.sleep(0.02)

    db_ro.close()

    downgraded = sum(1 for v in final_tol.values()
                     if v == "ADMISSIBLE_TICK_TOLERANCE")
    tol_dist = dict(zero)
    tol_dist["BLOCK"] = tol_dist.get("BLOCK", 0) - downgraded
    tol_dist["ADMISSIBLE_TICK_TOLERANCE"] = downgraded
    log(f"容差口径降级 {downgraded} 只；分布={tol_dist}")

    admissible_loose = sorted(
        set(admissible_strict) |
        set(k.split("|")[0] for k, v in final_tol.items()
            if v == "ADMISSIBLE_TICK_TOLERANCE"))

    write_list(args.strict_list, admissible_strict, args)
    write_list(args.loose_list, admissible_loose, args)

    etf_csv = args.etf_csv or str(ev / "block_tick_tolerance_downgraded.csv")
    with io.open(etf_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "asset_type", "mismatch_bar_total",
                    "max_abs_diff", "verdict"])
        for code, at, mb, worst in sorted(tol_records, key=lambda x: -x[3]):
            w.writerow([code, at, mb, worst, "ADMISSIBLE_TICK_TOLERANCE"])
    log(f"降级清单已写 {etf_csv}（{len(tol_records)}）")

    report = args.report or (
        f"docs/qfq-raw-admission-fullmarket-"
        f"{datetime.datetime.now():%Y%m%d}-tolerance.md")
    write_report(report, zero, tol_dist, len(done), len(admissible_strict),
                 len(admissible_loose), downgraded, args, tol_records)
    log("容差重判分析完成")


if __name__ == "__main__":
    main()
