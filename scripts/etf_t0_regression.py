# -*- coding: utf-8 -*-
"""G2 双档回归脚本（docs/etf-t0-per-code-design.md §9.2，ZCode 修订1/6 + G2 注记1/2）

(a) 零差异档：三个现有策略 × {daily-bar-v1, minute-bar-v1} × --etf-t0 false
    → config.csv / daily_stats.csv / trades.csv SHA-256 逐位一致（hash 相等，非归因）；
      运行日志零 diff（时间戳规范化，防假失败）。
(b) per-code 语义档：ptrade/t0_t1_probe_ptrade.py × minute-bar-v1 × --etf-t0 true
    → 24 只标的当日卖出结果差异逐条归因（预期：equity 5 只 FILLED→REJECTED，其余不变）。

用法（两侧必须在固定哈希种子下运行，保证 dict/set 迭代顺序确定——存量策略存在
依赖迭代顺序的决策逻辑，默认 PYTHONHASHSEED 随机化会使 (a) 档零差异判定失效）：
    set PYTHONHASHSEED=0   # Windows；两侧同值
    python scripts/etf_t0_regression.py --mode capture --out <dir> \\
        [--engine-root <repo_root>] [--db <data/quantstudio.db 绝对路径>]
    python scripts/etf_t0_regression.py --mode compare --pre <dir> --post <dir> [--report <path>]
"""
import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
from pathlib import Path

# ---- 引擎根：必须在 import quantstudio 之前插入 sys.path（worktree 侧跑 pre）----
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_log(text: str) -> str:
    """规范化时间戳（G2 注记2）：YYYY-MM-DD HH:MM:SS[,mmm] 与 HH:MM:SS[,mmm] → <TS>；
    以及导出目录名内嵌的运行时刻 backtest_results\\YYYYMMDD_HHMMSS_ → <TS>"""
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(,\d{3})?", "<TS>", text)
    text = re.sub(r"(?<!\d)\d{2}:\d{2}:\d{2}(,\d{3})?", "<TS>", text)
    text = re.sub(r"backtest_results[\\/]\d{8}_\d{6}_", "backtest_results\\\\<TS>_", text)
    return text


class _LogFile(logging.Handler):
    """流式写日志文件（进程中途死亡也不丢已产生行）"""

    def __init__(self, path: Path):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        self._fh = path.open("w", encoding="utf-8")

    def emit(self, record):
        try:
            self._fh.write(self.format(record) + "\n")
        except Exception:
            pass

    def close(self):
        try:
            self._fh.close()
        finally:
            super().close()


# 回归矩阵
STRATEGIES_A = [
    "ETF动量.py",
    "tech_etf_mvo_rotation_quantstudio.py",
    "etf_theme_rotation_quantstudio.py",
]
PROFILES_A = ["daily-bar-v1", "minute-bar-v1"]
WINDOW_A = {"daily-bar-v1": ("2026-01-01", "2026-08-10"),
            "minute-bar-v1": ("2026-07-01", "2026-07-07")}   # 分钟档限窗控时长
PROBE_B = "ptrade/t0_t1_probe_ptrade.py"
WINDOW_B = ("2026-08-03", "2026-08-07")
CAPITAL_A = 100_000
CAPITAL_B = 300_000


def _run_one(key, strategy_path, start, end, engine_root, db, profile, etf_t0, capital, out_dir):
    sys.path.insert(0, str(engine_root))
    from quantstudio.backtest.run_ptrade_strategy import run_backtest
    log_path = out_dir / f"{key}.log"
    file_handler = _LogFile(log_path)
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)
    entry = {"key": key, "strategy": Path(strategy_path).name, "profile": profile,
             "etf_t0": etf_t0, "start": start, "end": end, "ok": False, "error": None}
    try:
        result, output_dir, engine = run_backtest(
            strategy_path, start, end, db_path=db, capital=capital,
            match_price_mode="close", engine_profile=profile, etf_t0=etf_t0)
        entry["ok"] = True
        entry["output_dir"] = str(output_dir)
        run_dir = out_dir / key
        run_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for csv in sorted(Path(output_dir).glob("*.csv")):
            dst = run_dir / csv.name
            shutil.copy2(csv, dst)
            files[csv.name] = _sha256(dst)
        entry["files"] = files
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
    finally:
        root_logger.removeHandler(file_handler)
        file_handler.close()
    if log_path.exists():
        entry["log_sha256"] = _sha256(log_path)
        entry["log_lines"] = sum(1 for _ in log_path.open(encoding="utf-8"))
    return entry


def _parse_probe_summary(log_text: str) -> dict:
    """从探针日志汇总行提取 code -> (same_day_r1, same_day_r2, verdict)"""
    out = {}
    for line in log_text.splitlines():
        m = re.search(r"\[T0PROBE\] code=(\S+) fund_type=(\S+) expect_t0=(\S+)"
                      r" same_day\(r1\)=(\S+) same_day\(r2\)=(\S+) cleanup=(\S+) verdict=(.+)$", line)
        if m:
            out[m.group(1)] = {"fund_type": m.group(2), "expect_t0": m.group(3),
                               "r1": m.group(4), "r2": m.group(5),
                               "cleanup": m.group(6), "verdict": m.group(7)}
    return out


def capture(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine_root = Path(args.engine_root or _SCRIPT_ROOT)
    db = args.db or str(engine_root / "data" / "quantstudio.db")
    if not Path(db).exists():
        print(f"[capture] DB 不存在: {db}")
        sys.exit(2)
    tiers = set((args.tiers or "all").split(","))
    records = {"engine_root": str(engine_root), "db": db, "runs": [], "tiers": sorted(tiers)}

    def _save():
        (out_dir / "manifest.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # (a) 档
    for strat in STRATEGIES_A:
        sp = engine_root / "quantstudio" / "backtest" / "strategies" / strat
        if not sp.exists():
            records["runs"].append({"key": f"a_{strat}", "ok": False,
                                    "error": f"strategy not found: {sp}"})
            _save()
            continue
        for prof in PROFILES_A:
            if "a-daily" in tiers and prof == "daily-bar-v1":
                pass
            elif "a-minute" in tiers and prof == "minute-bar-v1":
                pass
            elif "all" in tiers:
                pass
            else:
                continue
            start, end = WINDOW_A[prof]
            key = f"a_{Path(strat).stem}_{prof}"
            print(f"[capture] {key} ...")
            records["runs"].append(_run_one(key, str(sp), start, end, engine_root, db,
                                            prof, False, CAPITAL_A, out_dir))
            _save()   # 增量落盘：进程中途死亡不丢已完成运行
    # (b) 档
    if "b" in tiers or "all" in tiers:
        pp = engine_root / PROBE_B
        if pp.exists():
            key = "b_probe_minute"
            print(f"[capture] {key} ...")
            start, end = WINDOW_B
            records["runs"].append(_run_one(key, str(pp), start, end, engine_root, db,
                                            "minute-bar-v1", True, CAPITAL_B, out_dir))
        else:
            records["runs"].append({"key": "b_probe_minute", "ok": False,
                                    "error": f"probe not found: {pp}"})
    _save()
    print(f"[capture] done -> {out_dir} (runs: {len(records['runs'])})")


def compare(args):
    pre_dir, post_dir = Path(args.pre), Path(args.post)
    pre_m = json.loads((pre_dir / "manifest.json").read_text(encoding="utf-8"))
    post_m = json.loads((post_dir / "manifest.json").read_text(encoding="utf-8"))
    pre_runs = {r["key"]: r for r in pre_m["runs"]}
    post_runs = {r["key"]: r for r in post_m["runs"]}

    report = []
    report.append("# G2 双档回归报告（ETF T+0 per-code）\n")
    report.append(f"- pre 侧: {pre_dir}（引擎根 {pre_m['engine_root']}）")
    report.append(f"- post 侧: {post_dir}（引擎根 {post_m['engine_root']}）")
    report.append(f"- DB: {pre_m['db']}\n")

    a_fail = 0
    report.append("## (a) 零差异档：hash 逐位一致 + 日志零 diff（时间戳规范化）\n")
    for key in sorted(pre_runs):
        pre, post = pre_runs[key], post_runs.get(key)
        if pre.get("key", "").startswith("b_"):
            continue
        report.append(f"### {key}\n")
        if not pre.get("ok") or not post or not post.get("ok"):
            report.append(f"- ❌ 运行失败: pre={pre.get('error')} post={post.get('error') if post else 'MISSING'}\n")
            a_fail += 1
            continue
        pre_files, post_files = pre.get("files", {}), post.get("files", {})
        all_keys = sorted(set(pre_files) | set(post_files))
        same = True
        for f in all_keys:
            h1, h2 = pre_files.get(f), post_files.get(f)
            ok = h1 is not None and h1 == h2
            same = same and ok
            report.append(f"- {f}: pre={h1} post={h2} {'✅一致' if ok else '❌不一致'}")
        pre_log = (pre_dir / f"{key}.log").read_text(encoding="utf-8")
        post_log = (post_dir / f"{key}.log").read_text(encoding="utf-8")
        log_same = _normalize_log(pre_log) == _normalize_log(post_log)
        report.append(f"- 日志（时间戳规范化后）: {'✅零diff' if log_same else '❌有diff'}")
        if not same or not log_same:
            a_fail += 1
        report.append("")
    report.append(f"**(a) 档判定: {'✅ 全部通过' if a_fail == 0 else f'❌ {a_fail} 项失败'}**\n")

    # (b) 档
    report.append("## (b) per-code 语义档：探针差异逐条归因\n")
    pre_b = pre_runs.get("b_probe_minute", {})
    post_b = post_runs.get("b_probe_minute", {})
    if not pre_b.get("ok") or not post_b.get("ok"):
        report.append(f"- ❌ 探针运行失败: pre={pre_b.get('error')} post={post_b.get('error')}\n")
    else:
        pre_map = _parse_probe_summary((pre_dir / "b_probe_minute.log").read_text(encoding="utf-8"))
        post_map = _parse_probe_summary((post_dir / "b_probe_minute.log").read_text(encoding="utf-8"))
        all_codes = sorted(set(pre_map) | set(post_map))
        # 预期：equity 5 只 FILLED→REJECTED/PENDING（本地引擎拒单 Order 为 falsy 对象，
        # 探针 _oid_ok 将其记为受理→14:50 判 PENDING，语义等价于拒单，见设计 §7 对账模式）
        equity_expected = {"510300.SS", "510500.SS", "159915.SZ", "512480.SS", "159995.SZ"}
        b_fail = 0
        for code in all_codes:
            p = pre_map.get(code, {})
            q = post_map.get(code, {})
            r1p, r1q = p.get("r1", "?"), q.get("r1", "?")
            changed = r1p != r1q
            if changed:
                if code in equity_expected and r1p == "FILLED" and r1q in ("REJECTED", "PENDING"):
                    verdict = "✅ 预期差异（per-code：equity 当日卖 FILLED→拒单）"
                else:
                    verdict = "❌ 非预期差异"
                    b_fail += 1
            else:
                verdict = "✅ 无差异"
            report.append(f"- {code} ({p.get('fund_type','?')}, expect_t0={p.get('expect_t0','?')}): "
                          f"pre r1={r1p} → post r1={r1q} {verdict}")
        # 513100 归因措辞（ZCode 数据事实修正）
        report.append("")
        report.append("> 513100 归因说明（ZCode 数据事实修正）：本地存在零量 bar（68125 bar 中 1321 根零量、"
                      "09:35 近期连续零量）但引擎不建模成交量门槛故本地仍成交，与 PTrade 零量不成交形成已知撮合近似。")
        report.append(f"\n**(b) 档判定: {'✅ 差异全部归因到 per-code 语义' if b_fail == 0 else f'❌ {b_fail} 项非预期差异'}**")

    report_path = Path(args.report) if args.report else Path(args.post) / "g2_regression_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report), encoding="utf-8")
    print(f"[compare] report -> {report_path}")
    # 终端打印避免非 ASCII 编码崩溃（GBK 控制台）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(report[-40:]))


def main():
    ap = argparse.ArgumentParser(description="ETF T+0 per-code G2 双档回归")
    ap.add_argument("--mode", choices=["capture", "compare"], required=True)
    ap.add_argument("--out", help="capture 输出目录")
    ap.add_argument("--pre", help="compare: 修复前 capture 目录")
    ap.add_argument("--post", help="compare: 修复后 capture 目录")
    ap.add_argument("--engine-root", help="引擎根（pre 侧指向干净 worktree）")
    ap.add_argument("--db", help="quantstudio.db 绝对路径（两侧共用同一数据）")
    ap.add_argument("--tiers", default="all",
                    help="捕获档位：all / a-daily,a-minute,b（逗号分隔）")
    ap.add_argument("--report", help="compare 报告输出路径")
    args = ap.parse_args()
    if args.mode == "capture":
        capture(args)
    else:
        compare(args)


if __name__ == "__main__":
    main()
