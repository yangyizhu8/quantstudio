#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""首周「采集轮质量审计」读数复算器（只读·不触主库·不改判据）

用途
----
总调度 2026-11-14 审核要求：交**完整输入清单、采样口径和可复核输出**。
本脚本**不改** `scripts/check_duckdb_firstweek_obs.py` 的任何判据，只做两件事：

1. `--manifest`：导出**全量日志清单**（不截断 60 个），逐文件给出 mtime / 大小 / 命中行数 / 命中行号；
2. `--script-scope`：**逐位复现**观测脚本的采样口径（mtime 最新 60 → 再按 OBS_WINDOW_START 过滤 → 计数命中**行数**），
   用来自证「我读懂了脚本口径」；
3. `--byday`：按**日志 mtime 的自然日**归桶统计（固定分母，全量 266 个文件），用来回答
   「这些命中到底发生在哪一天」，与 60 窗口读数**区分开**。

口径声明（与观测脚本一致，逐字对应源码）
----------------------------------------
- 命中判据：行内含 `quality audit failed`（脚本 `check_duckdb_firstweek_obs.py:135`）
- **计数单位：命中行数**（脚本 `qa_hits.append("%s:%d")` → `len(qa_hits)`，L136/L144）
  ⇒ `qa_failed_count` 是**行数**，不必然等于命中文件数。
- 窗口起点：`OBS_WINDOW_START = "2026-11-07 00:00:00"`（脚本 L32）
- 采样：先取 mtime 最新 **60** 个（L124-125），再过滤 mtime ≥ 窗口起点（L126）

不改动、不写入 data/ 或库；仅写本目录下的输出文件。
"""
import argparse
import datetime as dt
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOGDIR = os.path.join(REPO, "data", "logs")
QA_NEEDLE = "quality audit failed"
WINDOW_START = "2026-11-07 00:00:00"
TOP_N = 60


def scan_file(path):
    """返回 (命中行数, 命中行号列表)。逐行读，遇异常返回 (-1, [])。"""
    rows = []
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if QA_NEEDLE in line:
                    rows.append(i)
    except Exception:  # noqa: BLE001
        return -1, []
    return len(rows), rows


def iter_logs():
    try:
        names = [f for f in os.listdir(LOGDIR) if f.endswith(".log")]
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("无法列目录 %s: %s\n" % (LOGDIR, exc))
        return []
    out = []
    for n in names:
        p = os.path.join(LOGDIR, n)
        try:
            st = os.stat(p)
        except Exception:  # noqa: BLE001
            continue
        out.append((n, p, st.st_mtime, st.st_size))
    return out


def cmd_manifest(outdir):
    logs = iter_logs()
    path = os.path.join(outdir, "input-manifest.tsv")
    tot_files = tot_hit_files = tot_hit_lines = 0
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("file\tmtime\tsize_bytes\thit_lines\thit_line_numbers\tin_top60\tge_window_start\n")
        top60 = set()
        newest = sorted(logs, key=lambda x: x[2], reverse=True)[:TOP_N]
        wstart = dt.datetime.strptime(WINDOW_START, "%Y-%m-%d %H:%M:%S").timestamp()
        for n, p, mt, _sz in newest:
            if mt >= wstart:
                top60.add(n)
        for n, p, mt, sz in sorted(logs, key=lambda x: (x[2], x[0])):
            n_hit, rows = scan_file(p)
            tot_files += 1
            if n_hit > 0:
                tot_hit_files += 1
                tot_hit_lines += n_hit
            fh.write("%s\t%s\t%d\t%d\t%s\t%s\t%s\n" % (
                n,
                dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S"),
                sz, n_hit, ",".join(str(r) for r in rows) or "-",
                "Y" if n in top60 else "N",
                "Y" if mt >= wstart else "N"))
    print("  输入清单已写: %s" % path)
    print("  全量日志文件 = %d" % tot_files)
    print("  命中文件 = %d   命中行合计 = %d" % (tot_hit_files, tot_hit_lines))
    return 0


def cmd_script_scope(outdir):
    """逐位复现观测脚本口径。"""
    logs = iter_logs()
    wstart = dt.datetime.strptime(WINDOW_START, "%Y-%m-%d %H:%M:%S").timestamp()
    newest = sorted(logs, key=lambda x: x[2], reverse=True)[:TOP_N]
    scoped = [x for x in newest if x[2] >= wstart]
    hits = []
    for n, p, _mt, _sz in scoped:
        for row in scan_file(p)[1]:
            hits.append("%s:%d" % (n, row))
    lines = [
        "=== 复现观测脚本口径（--script-scope） ===",
        "  LOGDIR            = %s" % LOGDIR,
        "  QA_NEEDLE         = %r" % QA_NEEDLE,
        "  WINDOW_START      = %s" % WINDOW_START,
        "  TOP_N             = %d" % TOP_N,
        "  全量日志          = %d" % len(logs),
        "  最新 %d 个中过滤后 = %d" % (TOP_N, len(scoped)),
        "  命中行数(应=脚本 qa_failed_count) = %d" % len(hits),
        "  命中明细:",
    ]
    for h in hits:
        lines.append("    " + h)
    txt = "\n".join(lines) + "\n"
    io.open(os.path.join(outdir, "output-script-scope.txt"), "w", encoding="utf-8", newline="\n").write(txt)
    print(txt)
    return 0


def cmd_byday(outdir):
    """按日志 mtime 自然日归桶（固定分母 = 全量）。"""
    logs = iter_logs()
    buckets = {}
    for n, p, mt, _sz in logs:
        d = dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d")
        b = buckets.setdefault(d, {"files": 0, "hit_files": 0, "hit_lines": 0})
        b["files"] += 1
        n_hit, _rows = scan_file(p)
        if n_hit > 0:
            b["hit_files"] += 1
            b["hit_lines"] += n_hit
    lines = ["=== 按日志 mtime 自然日归桶（固定分母=全量 %d 个） ===" % len(logs),
             "  %-12s %8s %10s %10s" % ("日期", "日志数", "命中文件", "命中行数")]
    for d in sorted(buckets):
        b = buckets[d]
        lines.append("  %-12s %8d %10d %10d" % (d, b["files"], b["hit_files"], b["hit_lines"]))
    win_lo, win_hi = "2026-11-08", "2026-11-14"
    w_files = sum(v["files"] for d, v in buckets.items() if win_lo <= d <= win_hi)
    w_hit_f = sum(v["hit_files"] for d, v in buckets.items() if win_lo <= d <= win_hi)
    w_hit_l = sum(v["hit_lines"] for d, v in buckets.items() if win_lo <= d <= win_hi)
    pre_hit_l = sum(v["hit_lines"] for d, v in buckets.items() if d < win_lo)
    lines += [
        "",
        "  首周窗口(%s~%s): 日志=%d  命中文件=%d  命中行=%d" % (win_lo, win_hi, w_files, w_hit_f, w_hit_l),
        "  首周窗口之前      : 命中行=%d" % pre_hit_l,
        "",
        "  重要限定：以上仅覆盖**已落盘的日志文件**。观测记录为抽样时点（每日 1~5 行），",
        "  **不代表 11-14 全天已过、也不代表全周各时段无故障**。",
    ]
    txt = "\n".join(lines) + "\n"
    io.open(os.path.join(outdir, "output-byday.txt"), "w", encoding="utf-8", newline="\n").write(txt)
    print(txt)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--script-scope", action="store_true")
    ap.add_argument("--byday", action="store_true")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args(argv)
    if not os.path.isdir(a.outdir):
        os.makedirs(a.outdir)
    if a.manifest:
        return cmd_manifest(a.outdir)
    if a.script_scope:
        return cmd_script_scope(a.outdir)
    if a.byday:
        return cmd_byday(a.outdir)
    sys.argv = [sys.argv[0], "--help"]
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
