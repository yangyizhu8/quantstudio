import sys, json, hashlib, os
mode = sys.argv[1]
src  = sys.argv[2] if len(sys.argv) > 2 else ""
dst  = sys.argv[3] if len(sys.argv) > 3 else ""

# 独立合成载荷：**明确含中文内容**，仅用于编码控制；不冒充真实环境 identity
CN = {
    "duckdb": "1.5.6",
    "duckdb_file": "D:\\\u5408\u6210\\\u4e2d\u6587\\duckdb\\__init__.py",
    "exe": "D:\\\u5408\u6210\\\u4e2d\u6587\\python.exe",
    "prefix": "D:\\\u5408\u6210\\\u4e2d\u6587\u524d\u7f00",
    "python": "3.11.9",
    "source_id": "\u5408\u6210SID",
}
PLAIN = {"a": "b"}

def dump(obj, ensure_ascii):
    return json.dumps(obj, ensure_ascii=ensure_ascii, sort_keys=True)

if mode == "write-utf8":
    s = dump(CN, False)
    b = s.encode("utf-8")
    with open(dst, "wb") as f: f.write(b)
    print("WROTE-BYTES " + str(len(b)))
    print("HEX " + hashlib.sha256(b).hexdigest())
    print("PREFIX " + CN["prefix"])
elif mode == "write-ensureascii":
    s = dump(CN, True)
    b = s.encode("utf-8")
    with open(dst, "wb") as f: f.write(b)
    print("WROTE-BYTES " + str(len(b)))
    print("HEX " + hashlib.sha256(b).hexdigest())
elif mode == "write-ascii-cn":
    s = dump(CN, False)
    with open(dst, "w", encoding="ascii", newline="\n") as f: f.write(s)
    print("WROTE-ASCII-OK")
elif mode == "write-ascii-plain":
    s = dump(PLAIN, False)
    with open(dst, "w", encoding="ascii", newline="\n") as f: f.write(s)
    print("WROTE-ASCII-PLAIN-OK")
    print("PATH " + dst)
elif mode in ("mut-byte", "mut-pfx", "mut-fields"):
    raw = open(src, "rb").read()
    d = json.loads(raw.decode("utf-8"))
    if mode == "mut-byte":
        d["python"] = "3.11.8"
    elif mode == "mut-pfx":
        d["prefix"] = d["prefix"] + "X"
    else:
        d.pop("duckdb_file", None)
    s = json.dumps(d, ensure_ascii=False, sort_keys=True)
    b = s.encode("utf-8")
    with open(dst, "wb") as f: f.write(b)
    print("MUTATED " + mode)
    print("HEX " + hashlib.sha256(b).hexdigest())
else:
    print("BAD-MODE " + mode)
    sys.exit(9)
