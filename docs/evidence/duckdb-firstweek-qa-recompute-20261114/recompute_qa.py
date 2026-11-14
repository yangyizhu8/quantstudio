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


def cmd_top60(outdir):
    """导出 top60 分母清单（**分母不是结论**）。"""
    logs = iter_logs()
    wstart = dt.datetime.strptime(WINDOW_START, "%Y-%m-%d %H:%M:%S").timestamp()
    newest = sorted(logs, key=lambda x: x[2], reverse=True)[:TOP_N]
    lines = ["=== top60 分母清单（--top60） ===",
             "  全量日志 = %d ；取 mtime 最新 %d 个作为分母；再按 mtime>=%s 过滤" % (len(logs), TOP_N, WINDOW_START),
             "",
             "  %-3s %-58s %-20s %10s %8s %s" % ("#", "file", "mtime", "size_B", "hit行", "命中行号")]
    kept = 0
    for i, (n, p, mt, sz) in enumerate(newest, 1):
        n_hit, rows = scan_file(p)
        ok = mt >= wstart
        if ok:
            kept += 1
        lines.append("  %-3d %-58s %-20s %10d %8d %s%s" % (
            i, n[:58], dt.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S"),
            sz, n_hit, ",".join(str(r) for r in rows) or "-",
            "" if ok else "   [被窗口过滤掉]"))
    lines += ["",
              "  过滤后实际扫描 = %d 个" % kept,
              "",
              "  ★ 这是**分母清单**，不是结论。分母是滑动窗口：新日志增加会把老日志挤出 60 名之外。",
              "  ★ 任何「命中数下降」必须先排除『老命中文件滑出窗口』，才可讨论是否修复。"]
    txt = "\n".join(lines) + "\n"
    io.open(os.path.join(outdir, "output-top60.txt"), "w", encoding="utf-8", newline="\n").write(txt)
    print(txt)
    return 0


def cmd_verdict(outdir):
    """独立复算 8 项 checks + verdict，并与脚本 --json 对拍（只读，不改脚本）。"""
    import json
    import subprocess
    script = os.path.join(REPO, "scripts", "check_duckdb_firstweek_obs.py")
    py = sys.executable
    r = subprocess.run([py, script, "--json"], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    body = r.stdout.strip()
    try:
        res = json.loads(body[body.index("{"):]) if "{" in body else {}
    except Exception:  # noqa: BLE001
        res = {}
    checks = res.get("checks", {}) or {}
    # 独立复算两项纯文件面判据（不依赖脚本内部）
    logs = iter_logs()
    wstart = dt.datetime.strptime(WINDOW_START, "%Y-%m-%d %H:%M:%S").timestamp()
    scoped = [x for x in sorted(logs, key=lambda y: y[2], reverse=True)[:TOP_N] if x[2] >= wstart]
    my_hits = sum(scan_file(p)[0] for _n, p, _mt, _sz in scoped)
    lines = ["=== 脚本 verdict 与 8 项 checks（--verdict） ===",
             "  脚本 = scripts/check_duckdb_firstweek_obs.py --json（只读，未改动）",
             "  脚本 rc = %s" % r.returncode,
             "",
             "  %-28s %-6s %s" % ("check 键", "值", "含义"),
             ]
    meaning = {
        "fatal_zero": "FATAL 关键词命中==0",
        "wal_under_256mb": "WAL < 256MB",
        "gate_pass_1_5_6": "版本门输出 PASS 1.5.6",
        "daemon_alive": "forever 实例在位 或 有豁免",
        "daemon_absence_excused": "占用声明窗内缺席被豁免（**观测项：无声明时恒 False**）",
        "no_declaration_mismatch": "声明称在位∧实际缺席==不成立",
        "once_running": "当前有 once 轮在跑（**观测项：无 once 轮时恒 False**）",
        "no_qa_failure": "质量审计命中行数==0",
    }
    for k in ("fatal_zero", "wal_under_256mb", "gate_pass_1_5_6", "daemon_alive",
              "daemon_absence_excused", "no_declaration_mismatch", "once_running", "no_qa_failure"):
        v = checks.get(k, "(缺)")
        lines.append("  %-28s %-6s %s" % (k, v, meaning.get(k, "")))
    bad = sorted(k for k, v in checks.items() if v is False)
    lines += ["",
              "  verdict       = %s" % res.get("verdict"),
              "  exit_code     = %s" % res.get("exit_code"),
              "  红项(False)   = %s" % (bad if bad else "无"),
              "",
              "  --- 独立复算对拍（文件面两项）---",
              "  我算 qa 命中行数 = %d ；脚本 qa_failed_count = %s → %s" % (
                  my_hits, res.get("qa_failed_count"),
                  "一致" if my_hits == res.get("qa_failed_count") else "**不一致，须查口径**"),
              "  我算扫描日志数 = %d ；脚本 logs_scanned = %s → %s" % (
                  len(scoped), res.get("logs_scanned"),
                  "一致" if len(scoped) == res.get("logs_scanned") else "**不一致，须查口径**"),
              "",
              "  ★ 判据共 8 项，verdict = all(checks.values())：任一项 False 即 FAIL。",
              "  ★ `daemon_absence_excused` 与 `once_running` 是**观测项**（记录事实），却也参与 all()，",
              "    故无实际缺陷的时段同样会 FAIL —— 该设计问题**未改**，另见六步方案件。",
              "  ★ 本文件**不**把 FAIL 写成 PASS，也**不**因某项 0 命中而改判整体。",
              ]
    txt = "\n".join(lines) + "\n"
    io.open(os.path.join(outdir, "output-verdict.txt"), "w", encoding="utf-8", newline="\n").write(txt)
    print(txt)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--script-scope", action="store_true")
    ap.add_argument("--byday", action="store_true")
    ap.add_argument("--top60", action="store_true")
    ap.add_argument("--verdict", action="store_true")
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
    if a.top60:
        return cmd_top60(a.outdir)
    if a.verdict:
        return cmd_verdict(a.outdir)
    sys.argv = [sys.argv[0], "--help"]
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
