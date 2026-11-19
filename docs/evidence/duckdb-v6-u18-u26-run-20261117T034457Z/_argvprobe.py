import sys, hashlib
# 临时 echo-argv probe：只回显槽位个数与每个槽位的长度/内容哈希
args = sys.argv[1:]
print("ARGC %d" % len(args))
for i, a in enumerate(args, 1):
    b = a.encode("utf-8")
    print("A%d LEN %d" % (i, len(a)))
    print("A%d HASH %s" % (i, hashlib.sha256(b).hexdigest()))
