import sys, time, logging, inspect
sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from quantstudio.pipeline.mcp import client as C
print("[3] 真连云端（TLS 实弹，新策略生效）…")
key = C.load_mcp_api_key()
print(f"    key 已载入: {bool(key)}（不回显）")
sig = inspect.signature(C.MCPClient.__init__)
print(f"    __init__ 参数: {list(sig.parameters)[:8]}")
kw = {}
if "api_key" in sig.parameters: kw["api_key"] = key
if "tls_verify" in sig.parameters: kw["tls_verify"] = True
c = C.MCPClient(**kw)
print(f"    生效 verify={getattr(c,'_tls_verify_effective','?')!r} 来源={getattr(c,'_tls_source','?')}")
t0 = time.time()
info = c.handshake()
print(f"    handshake OK {time.time()-t0:.2f}s  server={getattr(info,'name','?')} v={getattr(info,'version','?')}")
print("[4] 小表 snapshot（不写主库）…")
t0 = time.time()
try:
    d = c.query_snapshot("qdb.trade_calendar", limit=3)
    n = len(d) if hasattr(d, "__len__") else "?"
    print(f"    OK 行数={n} {time.time()-t0:.2f}s")
except Exception as e:
    print(f"    异常: {type(e).__name__}: {str(e)[:130]}")
try:
    c.close()
except Exception:
    pass
