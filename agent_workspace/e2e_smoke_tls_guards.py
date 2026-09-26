import os, sys, time, logging
sys.path.insert(0, r"D:\miniQMT策略实盘\QuantStudio")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from quantstudio.pipeline.mcp.client import MCPClient, _apply_tls_policy
from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
print("[1] TLS 策略（真实 env）…")
import requests
sess = requests.Session()
v, src = _apply_tls_policy(sess, True)
print(f"    verify={v!r}  来源={src}")
print(f"    env REQUESTS_CA_BUNDLE={os.environ.get('REQUESTS_CA_BUNDLE')!r} CURL_CA_BUNDLE={os.environ.get('CURL_CA_BUNDLE')!r}")
print("[2] 真实窗口/缓存键守卫（不误伤）…")
stub = object.__new__(MCPAdapter)
for a, b, is_min in (("2026-09-01","2026-09-25",True), ("2026-01-01","2026-09-25",False)):
    bs = stub._export_batches(a, b, is_min, est_rows=None, grid_aligned=True, table="etf_minutes")
    print(f"    {a}~{b} minute={is_min} → {len(bs)} 批；首/末={bs[0]}|{bs[-1]}")
    print(f"      _cache_key 合法: {MCPAdapter._cache_key('etf_minutes', bs[0][0], bs[0][1])}")
print("[3] 真连云端 handshake（TLS 实弹）…")
key = MCPClient.load_mcp_api_key()
c = MCPClient(api_key=key) if key else MCPClient()
t0 = time.time()
info = c.handshake()
print(f"    handshake OK {time.time()-t0:.2f}s server={getattr(info,'name','?')} ver={getattr(info,'version','?')}")
print("[4] 真连云端小表 export（不写主库，仅内存/临时）…")
t0 = time.time()
try:
    df = c.query_snapshot("qdb.trade_calendar", limit=5) if hasattr(c,"query_snapshot") else None
    print(f"    snapshot: {None if df is None else len(df)} 行 {time.time()-t0:.2f}s")
except Exception as e:
    print(f"    snapshot 异常（不影响判定）: {type(e).__name__}: {str(e)[:120]}")
