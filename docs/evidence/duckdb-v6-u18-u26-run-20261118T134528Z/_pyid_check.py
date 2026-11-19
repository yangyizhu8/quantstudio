import sys, json, hashlib
b = open(sys.argv[1], "rb").read()
print("FILE-HEX " + hashlib.sha256(b).hexdigest())
d = json.loads(b.decode("utf-8"))
print("PFX-HEX  " + hashlib.sha256(d["prefix"].encode("utf-8")).hexdigest())
print("FIELDS   " + ",".join(sorted(d.keys())))
