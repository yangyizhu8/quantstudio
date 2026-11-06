#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qmt_factor_diag —— QMT×本地 因子层诊断（D 类主因定位，2026-11-06，六步①c6699fc ②审通过）。

两个子命令（DSH 侧执行；本脚本不在审计禁区内运行转换/pytest）：

  --local    本地侧诊断产出：
             走本地回测同路径——quantstudio/backtest/ptrade_api.py 的 get_history
             （fq='pre' / include=False / count=260 / unit='1d' /
             fields=['close','high','volume']，只调用不修改该文件）；
             对 TARGET_CODES × DIAG_TIMES 逐时点取数（执行日 T 取 D-1 及之前
             260 根，与策略 E1 语义一致），经 exec_diag_block()（exec 同一份
             DIAG_BLOCK）调用 _qs_fact_dbg_emit 产出 QS_QMT_FACTDBG 行
             -> output/qmt-fidelity-20261106/local_factdiag.txt

  --compare  消融矩阵对照器：
             输入 --qmt-log（真机回传日志，含 QS_QMT_FACTDBG 行）×
             local_factdiag.txt；按 code 分组、按 d1（末日）归入三个时点；
             逐 (时点, code) 判定首个分叉层：
               LEN->C3（260 根门槛/候选池起点）/ ALIGN->C1（序列行对齐/停牌填充）/
               PRICE->C2（前复权基期）/ VOL->C5（成交量口径）/ SAME->SYNTH（合成/
               权重层）/ NO_LOCAL（本地无记录）/ NO_MATCH（QMT 日志无该时点记录）
             -> stdout 矩阵 + 汇总 + output/qmt-fidelity-20261106/factor_diag_matrix.json

用法：
  python scripts/qmt_factor_diag.py --local [--db PATH]
  python scripts/qmt_factor_diag.py --compare --qmt-log <path> [--local-file PATH]
"""
import argparse
import contextlib
import io
import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.strategy_compiler.factor_diag import (  # noqa: E402
    DIAG_TIMES, TARGET_CODES, exec_diag_block)

OUT_DIR = ROOT / "output" / "qmt-fidelity-20261106"
LOCAL_FILE = OUT_DIR / "local_factdiag.txt"
MATRIX_FILE = OUT_DIR / "factor_diag_matrix.json"
COUNT = 260
FIELDS = ["close", "high", "volume"]


# --------------------------------------------------------------------- local

def run_local(db_path: str | None) -> int:
    from quantstudio.backtest.backtest_engine import EngineConfig
    from quantstudio.backtest.ptrade_api import PtradeAPI

    if db_path:
        cfg = EngineConfig(db_path=pathlib.Path(db_path),
                           output_dir=ROOT / "output",
                           research_dir=ROOT / "output" / "research")
    else:
        cfg = EngineConfig.default()
    print("[local] db_path=%s" % cfg.db_path)

    # 桩引擎：attach_day 仅消费 engine.config（A1 注入）与可选 _providers；
    # _market 惰性建库走 DataProviderRegistry.from_duckdb(cfg.db_path)——与引擎
    # 首日 attach（backtest_engine.py:514，curr/prev 同日、curr_data=None）同款姿势。
    stub = SimpleNamespace(config=cfg)
    api = PtradeAPI()
    ns = exec_diag_block()  # 强制同源：本地侧执行的就是 DIAG_BLOCK 本体

    out_lines: list[str] = []
    fact_lines: list[str] = []
    for t8 in DIAG_TIMES:
        t_fmt = "%s-%s-%s" % (t8[:4], t8[4:6], t8[6:8])
        # 第一跳：暂以 T 为 prev（引擎首日形态），仅为让 calendar 就绪
        api.attach_day(stub, None, None, t_fmt, t_fmt, {})
        tds = [str(d) for d in api.get_trade_days("2015-01-01", t_fmt) if str(d)]
        if not tds:
            print("[local] FAIL: trade calendar empty for %s（db 异常？）" % t8)
            return 2
        eff = max((d for d in tds if d <= t_fmt), default=t_fmt)
        prev = max((d for d in tds if d < eff), default=eff)
        # 第二跳：修正为真实执行日/前一交易日（preload 亦按 prev 重跑）
        api.attach_day(stub, None, None, eff, prev, {})
        hist = api.get_history(list(TARGET_CODES), COUNT, unit="1d",
                               fields=FIELDS, fq="pre", include=False,
                               is_dict=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ns["_qs_fact_dbg_emit"](list(TARGET_CODES), hist)
        lines = [ln for ln in buf.getvalue().splitlines()
                 if "QS_QMT_FACTDBG" in ln]
        got = set()
        for ln in lines:
            for tok in ln.split():
                if tok.startswith("code="):
                    got.add(tok[5:])
                    break
        missing = [c for c in TARGET_CODES if c not in got]
        out_lines.append("# T=%s eff_exec=%s prev_trade=%s emitted=%d/%d missing=%s"
                         % (t8, eff, prev, len(lines), len(TARGET_CODES),
                            ",".join(missing) if missing else "-"))
        out_lines.extend(lines)
        fact_lines.extend(lines)
        print("[local] T=%s eff=%s prev=%s emitted=%d missing=%s"
              % (t8, eff, prev, len(lines),
                 ",".join(missing) if missing else "-"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print("[local] wrote %s (%d factdbg lines)" % (LOCAL_FILE, len(fact_lines)))
    print("[local] 前 20 行：")
    for ln in fact_lines[:20]:
        print("  " + ln)
    if not fact_lines:
        print("[local] FAIL: 零 factdbg 行（TARGET 全缺或取数异常）")
        return 1
    return 0


# ------------------------------------------------------------------- compare

def parse_factdbg_line(ln: str) -> dict | None:
    if "QS_QMT_FACTDBG" not in ln:
        return None
    rec = {}
    for tok in ln.strip().split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            rec[k] = v
    return rec if rec.get("code") else None


def read_text_loose(path: pathlib.Path) -> str:
    """真机日志编码自适应：utf-8 -> gbk -> latin-1(replace)。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def load_records(path: pathlib.Path) -> dict:
    """-> {code: {d1: rec}}；同 (code,d1) 多行（策略多次调用/不同 count）时
    保留 n 最大者（因子层主调用=260 窗口），n 同值取后出现行。"""
    per: dict = {}
    n_lines = 0
    for ln in read_text_loose(path).splitlines():
        rec = parse_factdbg_line(ln)
        if rec is None:
            continue
        code = str(rec["code"]).split(".")[0]
        if code not in TARGET_CODES:
            continue
        n_lines += 1

        def n_int(r):
            try:
                return int(r.get("n", "-1"))
            except ValueError:
                return -1
        d1 = rec.get("d1", "none")
        cur = per.setdefault(code, {}).get(d1)
        if cur is None or n_int(rec) >= n_int(cur):
            per[code][d1] = rec
    return {"per": per, "lines": n_lines}


def bucket(recs_by_d1: dict, t8: str):
    """按「d1 <= 时点且为其中最大 d1」取该时点记录（两侧同规则）。"""
    cands = {d1: rec for d1, rec in recs_by_d1.items()
             if len(d1) == 8 and d1.isdigit() and d1 <= t8}
    if not cands:
        return None, None
    best = max(cands)
    return dict(cands[best]), best


def _n_int(rec):
    try:
        return int(rec.get("n", "-1"))
    except (TypeError, ValueError):
        return -1


def run_compare(qmt_log: pathlib.Path, local_file: pathlib.Path) -> int:
    if not qmt_log.exists():
        print("[compare] FAIL: --qmt-log 不存在: %s" % qmt_log)
        return 2
    if not local_file.exists():
        print("[compare] FAIL: 本地文件不存在: %s（先跑 --local）" % local_file)
        return 2
    qmt = load_records(qmt_log)
    loc = load_records(local_file)
    print("[compare] qmt_log=%s（factdbg 行 %d，命中 TARGET 的 code 数 %d）"
          % (qmt_log, qmt["lines"], len(qmt["per"])))
    print("[compare] local=%s（factdbg 行 %d，code 数 %d）"
          % (local_file, loc["lines"], len(loc["per"])))

    header = ("code", "时点", "n_QMT", "n_LOC", "n同", "d1_QMT", "d1_LOC", "日期同",
              "c1同", "c60同", "c251同", "h260同", "v20同", "v60同", "首个分叉层", "结论")
    rows = []
    concl_counts: dict = {}
    per_time: dict = {t: {} for t in DIAG_TIMES}
    for t8 in DIAG_TIMES:
        for code in TARGET_CODES:
            qrec, qd1 = bucket(qmt["per"].get(code, {}), t8)
            lrec, ld1 = bucket(loc["per"].get(code, {}), t8)
            row = {"code": code, "time": t8}
            if qrec is None and lrec is None:
                row.update({"n_QMT": "-", "n_LOC": "-", "n_eq": "-",
                            "d1_QMT": "-", "d1_LOC": "-", "date_eq": "-",
                            "c1_eq": "-", "c60_eq": "-", "c251_eq": "-",
                            "h260_eq": "-", "v20_eq": "-", "v60_eq": "-",
                            "layer": "ABSENT", "conclusion": "ABSENT",
                            "qmt": None, "loc": None})
                rows.append(row)
                concl_counts["ABSENT"] = concl_counts.get("ABSENT", 0) + 1
                per_time[t8]["ABSENT"] = per_time[t8].get("ABSENT", 0) + 1
                continue
            if qrec is None:
                row.update({"n_QMT": "-", "n_LOC": _n_int(lrec),
                            "d1_QMT": "-", "d1_LOC": ld1,
                            "layer": "NO_MATCH", "conclusion": "NO_MATCH",
                            "qmt": None, "loc": lrec})
                concl = "NO_MATCH"
            elif lrec is None:
                row.update({"n_QMT": _n_int(qrec), "n_LOC": "-",
                            "d1_QMT": qd1, "d1_LOC": "-",
                            "layer": "NO_LOCAL", "conclusion": "NO_LOCAL",
                            "qmt": qrec, "loc": None})
                concl = "NO_LOCAL"
            else:
                nq, nl = _n_int(qrec), _n_int(lrec)
                date_eq = (qrec.get("d1") == lrec.get("d1")
                           and qrec.get("d0") == lrec.get("d0"))
                c1 = qrec.get("c1") == lrec.get("c1")
                c60 = qrec.get("c60") == lrec.get("c60")
                c251 = qrec.get("c251") == lrec.get("c251")
                h260 = qrec.get("h260") == lrec.get("h260")
                v20 = qrec.get("v20") == lrec.get("v20")
                v60 = qrec.get("v60") == lrec.get("v60")
                if nq != nl:
                    layer, concl = "LEN", "C3"
                elif not date_eq:
                    layer, concl = "ALIGN", "C1"
                elif not (c1 and c60 and c251 and h260):
                    layer, concl = "PRICE", "C2"
                elif not (v20 and v60):
                    layer, concl = "VOL", "C5"
                else:
                    layer, concl = "SAME", "SYNTH"
                row.update({"n_QMT": nq, "n_LOC": nl,
                            "d1_QMT": qrec.get("d1"), "d1_LOC": lrec.get("d1"),
                            "layer": layer, "conclusion": concl,
                            "qmt": qrec, "loc": lrec})
                row["_eq"] = (nq == nl, date_eq, c1, c60, c251, h260, v20, v60)
            if "_eq" in row:
                (n_eq, date_eq, c1, c60, c251, h260, v20, v60) = row.pop("_eq")
                row.update({"n_eq": int(n_eq), "date_eq": int(date_eq),
                            "c1_eq": int(c1), "c60_eq": int(c60),
                            "c251_eq": int(c251), "h260_eq": int(h260),
                            "v20_eq": int(v20), "v60_eq": int(v60)})
            else:
                for k in ("n_eq", "date_eq", "c1_eq", "c60_eq", "c251_eq",
                          "h260_eq", "v20_eq", "v60_eq"):
                    row.setdefault(k, "-")
            rows.append(row)
            concl = row["conclusion"]
            concl_counts[concl] = concl_counts.get(concl, 0) + 1
            per_time[t8][concl] = per_time[t8].get(concl, 0) + 1

    # ---- stdout 矩阵 ----
    print()
    print("[matrix] " + " | ".join(header))
    print("[matrix] " + "-" * 118)
    for r in rows:
        cells = []
        for k in header:
            v = r.get(k if k != "时点" else "time", "-")
            cells.append(str(v))
        print("[matrix] " + " | ".join(cells))

    # ---- 汇总 ----
    eff = [r for r in rows if r["conclusion"] != "ABSENT"]
    print()
    print("[summary] 结论分布（全部 %d 行，其中 ABSENT %d 行不计入总裁定）:"
          % (len(rows), len(rows) - len(eff)))
    for k in sorted(concl_counts, key=lambda x: -concl_counts[x]):
        print("[summary]   %-10s %d" % (k, concl_counts[k]))
    for t in DIAG_TIMES:
        print("[summary] 时点 %s: %s" % (t, per_time[t]))
    verdict, share = "INCONCLUSIVE", 0.0
    if eff:
        cnt: dict = {}
        for r in eff:
            cnt[r["conclusion"]] = cnt.get(r["conclusion"], 0) + 1
        top = max(cnt, key=lambda x: cnt[x])
        share = cnt[top] / len(eff)
        if share >= 0.5:
            verdict = top
    print("[verdict] %s（有效行占比 %.1f%%，判据：最高且 >=50%%，否则 INCONCLUSIVE）"
          % (verdict, share * 100))

    MATRIX_FILE.parent.mkdir(parents=True, exist_ok=True)
    MATRIX_FILE.write_text(
        json.dumps({"qmt_log": str(qmt_log), "local_file": str(local_file),
                    "diag_times": list(DIAG_TIMES),
                    "target_codes": list(TARGET_CODES),
                    "rows": rows,
                    "summary": {"conclusion_counts": concl_counts,
                                "per_time": per_time,
                                "verdict": verdict,
                                "verdict_share": round(share, 4)}},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    print("[compare] wrote %s" % MATRIX_FILE)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="QMT×本地 因子层诊断（--local 本地产出 / --compare 消融矩阵）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--local", action="store_true", help="本地侧诊断产出")
    g.add_argument("--compare", action="store_true", help="消融矩阵对照器")
    ap.add_argument("--db", default=None, help="（--local）DuckDB 路径，缺省 EngineConfig.default()")
    ap.add_argument("--qmt-log", default=None, help="（--compare）真机回传日志路径")
    ap.add_argument("--local-file", default=str(LOCAL_FILE),
                    help="（--compare）本地 factdiag 文件，缺省 output/qmt-fidelity-20261106/local_factdiag.txt")
    args = ap.parse_args()
    if args.local:
        return run_local(args.db)
    if not args.qmt_log:
        print("[compare] FAIL: 需要 --qmt-log <path>")
        return 2
    return run_compare(pathlib.Path(args.qmt_log), pathlib.Path(args.local_file))


if __name__ == "__main__":
    sys.exit(main())
