
"""A1a→A1b 对齐验证：真实任务 wall clock x rows_written（只读日志）。"""
import re, glob, os
from datetime import datetime
from collections import defaultdict

PAT_TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
PAT_START = re.compile(r"\[(\S+?)\] === START task=(\S+) source=(\S+) table=(\S+)/(\S+) ===")
PAT_DONE_S = re.compile(r"\[(\S+?)\] .*?\[streaming\] raw=(\d+) aligned=(\d+) passed=(\d+) rejected=(\d+) written=(\d+)")
PAT_DONE_P = re.compile(r"\[(\S+?)\] .*?PER_DATE 完成: (\d+)天, 入库(\d+)行")

logs = sorted(glob.glob("data/logs/*.log"))
print("LOGS =", len(logs))
starts = {}
rows = []
for lp in logs:
    try:
        with open(lp, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = PAT_TS.match(line)
                if not m:
                    continue
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                ms = PAT_START.search(line)
                if ms:
                    bid, task, src, tbl, freq = ms.groups()
                    starts[(os.path.basename(lp), bid)] = (t, tbl, freq)
                    continue
                md = PAT_DONE_S.search(line)
                if md:
                    bid = md.group(1)
                    key = (os.path.basename(lp), bid)
                    if key in starts:
                        t0, tbl, freq = starts.pop(key)
                        dt = t - t0
                        if dt > 0 and dt < 7200:
                            rows.append((tbl, freq, int(md.group(6)), dt))
                    continue
                mp = PAT_DONE_P.search(line)
                if mp:
                    bid = mp.group(1)
                    key = (os.path.basename(lp), bid)
                    if key in starts:
                        t0, tbl, freq = starts.pop(key)
                        dt = t - t0
                        if dt > 0 and dt < 7200:
                            rows.append((tbl, freq, int(mp.group(3)), dt))
    except Exception as _e:      # 单文件解析失败不阻断整体（日志格式差异容忍）
        print("  [SKIP-LOG]", os.path.basename(lp), type(_e).__name__, str(_e)[:60])
        continue

print("PAIRED_BATCHES =", len(rows))
print()
print("%-22s %-8s %-12s %-9s %s" % ("TABLE", "FREQ", "WRITTEN", "SECONDS", "ROWS/S"))
print("-" * 66)
for tbl, freq, w, dt in sorted(rows, key=lambda r: -(r[2] / r[3] if r[3] else 0))[:20]:
    print("%-22s %-8s %-12d %-9d %d" % (tbl, freq, w, dt, w // dt if dt else 0))
if rows:
    tot_r = sum(r[2] for r in rows); tot_t = sum(r[3] for r in rows)
    print("-" * 66)
    print("AGGREGATE rows/s =", tot_r // tot_t if tot_t else 0, " (rows=%d, seconds=%d)" % (tot_r, tot_t))
