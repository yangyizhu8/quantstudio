# ==== PY_COLLECT ====
import sys, json, hashlib, duckdb
c = duckdb.connect()
r = c.execute("SELECT * FROM pragma_version()").fetchone()
d = {"python": sys.version.split()[0], "exe": sys.executable, "prefix": sys.prefix,
     "duckdb": duckdb.__version__, "source_id": r[1], "duckdb_file": duckdb.__file__}
s = json.dumps(d, ensure_ascii=False, sort_keys=True)
open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(s)
print("META-HEX " + hashlib.sha256(s.encode("utf-8")).hexdigest())
print("PFX-HEX  " + hashlib.sha256(sys.prefix.encode("utf-8")).hexdigest())

# ==== PY_RT ====
import sys, json, hashlib
b = open(sys.argv[1], "rb").read()
print("FILE-HEX " + hashlib.sha256(b).hexdigest())
d = json.loads(b.decode("utf-8"))
print("PFX-HEX  " + hashlib.sha256(d["prefix"].encode("utf-8")).hexdigest())
print("FIELDS   " + ",".join(sorted(d.keys())))
