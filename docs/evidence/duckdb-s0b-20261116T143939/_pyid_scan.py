import os, sys, stat
root = sys.argv[1]
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 根有效断言：不能"扫了个不存在/读不了的地方"还报 0 链接
if not os.path.isdir(root):
    print("SCAN-ROOT-INVALID " + root)
    sys.exit(8)
try:
    root_entries = os.listdir(root)
except OSError as ex:
    print("SCAN-ROOT-INVALID " + root + " :: " + str(ex))
    sys.exit(8)
print("SCAN-ROOT-ENTRIES " + str(len(root_entries)))

# onerror 必须显式给：默认 onerror=None 时 scandir 被拒会被**静默忽略**，
# 内层循环根本看不到那个目录的内容，错误数于是仍是 0 —— 这正是上一版的漏。
walk_errors, lstat_errors, hits = [], [], []
dirs_visited = 0

def onerror(ex):
    walk_errors.append("%s :: %s" % (getattr(ex, "filename", "?"), ex))

for dp, dns, fns in os.walk(root, onerror=onerror, followlinks=False):
    dirs_visited += 1
    # Windows 上 junction 的 is_dir() 为 True、is_symlink() 为 False，
    # 只靠 followlinks=False **挡不住**它 —— 必须自行检测并剪枝，否则会钻进链接目标。
    for name in list(dns):
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            dns.remove(name)
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)
            dns.remove(name)
    for name in fns:
        q = os.path.join(dp, name)
        try:
            st = os.lstat(q)
        except OSError as ex:
            lstat_errors.append("%s :: %s" % (q, ex))
            continue
        if getattr(st, "st_file_attributes", 0) & REPARSE:
            hits.append(q)

print("SCAN-LINKS " + str(len(hits)))
print("SCAN-DIRS " + str(dirs_visited))
print("SCAN-WALK-ERRORS " + str(len(walk_errors)))
print("SCAN-LSTAT-ERRORS " + str(len(lstat_errors)))
for h in hits[:10]:
    print("SCAN-HIT " + h)
for err in (walk_errors + lstat_errors)[:10]:
    print("SCAN-ERR " + err)
sys.exit(0 if (not walk_errors and not lstat_errors) else 7)