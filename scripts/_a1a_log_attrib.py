
"""A1a：日志时间归因（口径修正 v2）——真 batch_id + START→完成配对 + 陈旧守卫。

v1 教训：仅靠形态过滤仍会跨运行（无完成行的批会一直敞开吞后续行），
故加 30 分钟陈旧守卫强制闭合，把每笔限定在一次连续运行内。
"""
import re, glob, os, sys

# Windows GBK 控制台无法输出日志里的 emoji/替换字符：输出侧降级为可编码字符
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
BID = re.compile(r"\[([A-Za-z0-9_.\-]+?_\d{8}_\d{6}_[0-9a-f]{6})\]")
START = re.compile(r"=== START task=")
DONE = re.compile(r"\[streaming\]|PER_DATE 完成:")
STALE = 1800

records = []
for lp in sorted(glob.glob("data/logs/*.log")):
    cur = None
    prev_t = None
    day = 0
    for ln in open(lp, encoding="utf-8", errors="replace"):
        m = TS.match(ln)
        if not m:
            continue
        t = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))
        if prev_t is not None and t < prev_t - 60:
            day += 86400
        prev_t = t
        t += day
        b = BID.search(ln)
        if not b:
            continue
        bid = b.group(1)
        if START.search(ln):
            cur = {"bid": bid, "f": os.path.basename(lp), "items": [(t, ln.strip()[:120])]}
            records.append(cur)
            continue
        if cur is not None and bid == cur["bid"]:
            if t - cur["items"][-1][0] > STALE:
                cur = None            # 陈旧守卫：跨运行残留强制闭合
                continue
            cur["items"].append((t, ln.strip()[:120]))
            if DONE.search(ln):
                cur = None

ok = [r for r in records if len(r["items"]) >= 3 and r["items"][-1][0] > r["items"][0][0]]
ok = [r for r in ok if (r["items"][-1][0] - r["items"][0][0]) <= STALE]
ok.sort(key=lambda r: -(r["items"][-1][0] - r["items"][0][0]))
print("CLOSED_BATCH_RECORDS =", len(ok), "(raw", len(records), ")")
print()
for r in ok[:5]:
    it = r["items"]
    total = it[-1][0] - it[0][0]
    print("=== %s [%s] total=%ds lines=%d ===" % (r["bid"], r["f"], total, len(it)))
    gaps = sorted(((b[0]-a[0], a[1], b[1]) for a, b in zip(it, it[1:])), reverse=True)
    acc = sum(g for g, _, _ in gaps[:4])
    for g, before, after in gaps[:4]:
        print("   %5ds %5.1f%%  after: %s" % (g, 100.0*g/total if total else 0, after[:92]))
    print("   top4_share=%.1f%%" % (100.0*acc/total if total else 0))
    print()
