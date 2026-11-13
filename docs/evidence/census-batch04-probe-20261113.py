# -*- coding: utf-8 -*-
"""census batch04 · 先补资源门与进度身份门，再扩大查询（R29）

R29 要求逐项落实：
 1) 资源门：**缺测拒跑**（GlobalMemoryStatusEx 返回值必须核）、上限、超时。
    阈值**本地论证**，不把云 T4 的 8GB 未经论证搬成本机 census 门。
 2) 进度身份绑定：progress 不能只按 table/day/side —— 必须绑定
    端点 / schema / 代码版本 / 输入版本；活表变化时旧键标 stale 并重取。
 3) 门通过后才扩大段键覆盖（本次 cap=40 段，仍不铺开千段）。

只读。生产零 DDL。端点 L-A 127.0.0.1:9000。
退出码：0=正常跑完；2=资源缺测/越界拒跑；3=超时中止。
"""
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9000/exec"
EV = r"D:\miniQMT策略实盘\QuantStudio\docs\evidence"
PROGRESS = os.path.join(EV, "census-batch04-progress.json")
MANIFEST = os.path.join(EV, "census-manifest-lia-batch04-20261113.json")
REFUSED_OUT = os.path.join(EV, "census-batch04-gate-refused-20261113.json")
SELF = os.path.abspath(__file__)

TABLES = ["stock_minutes", "etf_minutes"]
KEY_COLS = ["ts_code", "trade_time", "freq"]
KEY_ORDER = ", ".join(KEY_COLS)

MAX_SEGMENTS_PER_RUN = 40        # 内存偏紧：单批上限
QUERY_TIMEOUT_SEC = 300          # 单查询超时
TOTAL_BUDGET_SEC = 600           # 整批预算超时
# —— 资源门阈值（本地论证，见 manifest.resource_gate.rationale）——
RAM_FREE_FLOOR_GB = 1.0
RAM_FREE_FLOOR_PCT = 0.03        # 取 max(1.0GB, 3% total)
RAM_LOAD_CEIL_PCT = 95
DISK_FREE_FLOOR_GB = 5.0

START = time.time()


def budget_left():
    return TOTAL_BUDGET_SEC - (time.time() - START)


def q(sql, timeout=None):
    if budget_left() <= 0:
        raise TimeoutError("total budget exceeded")
    t = min(timeout or QUERY_TIMEOUT_SEC, max(1, int(budget_left())))
    with urllib.request.urlopen(BASE + "?query=" + urllib.parse.quote(sql), timeout=t) as r:
        return json.loads(r.read().decode("utf-8"))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest()[:16]


# ---------------- 资源门 ----------------
def measure_resources():
    """返回 (ok, rec, reasons)。缺测即 ok=False（缺测拒跑）。"""
    rec, reasons = {}, []
    try:
        import ctypes
        from ctypes import wintypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
        fn.argtypes = [ctypes.POINTER(MS)]
        fn.restype = ctypes.c_int
        rc = fn(ctypes.byref(m))                       # ← 必须核返回值
        rec["api_rc"] = rc
        rec["struct_size"] = ctypes.sizeof(MS)
        rec["GetLastError"] = ctypes.get_last_error()
        if rc != 1:
            reasons.append("GlobalMemoryStatusEx returned %d (0=failure) -> 缺测" % rc)
        if ctypes.sizeof(MS) != 64:
            reasons.append("struct size %d != 64 -> 结构体定义有误" % ctypes.sizeof(MS))
        if rc == 1:
            if not (m.ullTotalPhys > 0 and m.ullAvailPhys <= m.ullTotalPhys):
                reasons.append("sanity failed: total=%d avail=%d" % (m.ullTotalPhys, m.ullAvailPhys))
            rec["ram_total_gb"] = round(m.ullTotalPhys / 1e9, 2)
            rec["ram_free_gb"] = round(m.ullAvailPhys / 1e9, 2)
            rec["ram_load_pct"] = m.dwMemoryLoad
    except Exception as e:  # noqa: BLE001
        reasons.append("memory probe exception: %s" % str(e)[:150])
    try:
        import shutil
        du = shutil.disk_usage("D:\\")
        rec["disk_free_gb"] = round(du.free / 1e9, 1)
        rec["disk_total_gb"] = round(du.total / 1e9, 1)
    except Exception as e:  # noqa: BLE001
        reasons.append("disk probe exception: %s" % str(e)[:150])
    rec["sampled_at"] = now_iso()
    rec["source"] = "live GlobalMemoryStatusEx / shutil.disk_usage at this run"
    return (len(reasons) == 0), rec, reasons


def resource_gate(rec, reasons):
    """门判定。缺测 -> 拒跑；越界 -> 拒跑。"""
    fails = list(reasons)
    if "ram_free_gb" not in rec or "ram_total_gb" not in rec:
        fails.append("RAM 读数缺失 -> 缺测拒跑")
    else:
        floor = max(RAM_FREE_FLOOR_GB, rec["ram_total_gb"] * RAM_FREE_FLOOR_PCT)
        rec["ram_free_floor_gb"] = round(floor, 2)
        if rec["ram_free_gb"] < floor:
            fails.append("RAM free %.2f GB < floor %.2f GB" % (rec["ram_free_gb"], floor))
        if rec.get("ram_load_pct", 0) > RAM_LOAD_CEIL_PCT:
            fails.append("RAM load %d%% > ceil %d%%" % (rec["ram_load_pct"], RAM_LOAD_CEIL_PCT))
    if "disk_free_gb" not in rec:
        fails.append("磁盘读数缺失 -> 缺测拒跑")
    elif rec["disk_free_gb"] < DISK_FREE_FLOOR_GB:
        fails.append("disk free %.1f GB < floor %.1f GB" % (rec["disk_free_gb"], DISK_FREE_FLOOR_GB))
    return (len(fails) == 0), fails


# ---------------- 进度身份门 ----------------
def load_progress():
    if os.path.exists(PROGRESS):
        try:
            d = json.load(io.open(PROGRESS, encoding="utf-8"))
            if d.get("version") == "progress-v2":
                return d
        except Exception:  # noqa: BLE001
            pass
    return {"version": "progress-v2", "identities": {}, "done": {}, "stale": {},
            "created_at": now_iso(), "updates": 0}


def table_identity(tbl):
    r = q("select * from %s limit 0" % tbl, timeout=60)
    cols = [(c["name"], c["type"]) for c in r["columns"]]
    ts = [c["name"] for c in r["columns"] if c["type"] == "TIMESTAMP"]
    tc = next((p for p in ("trade_time", "ts") if p in ts), ts[0] if ts else None)
    agg = q("select count() c, min(%s) mn, max(%s) mx from %s" % (tc, tc, tbl))
    row = agg["dataset"][0]
    wal = q("select writerTxn, sequencerTxn from wal_tables() where name='%s'" % tbl)
    w = (wal["dataset"][0][0], wal["dataset"][0][1]) if wal.get("count") else (None, None)
    return {
        "endpoint": BASE,
        "designated_ts": tc,
        "schema_sha": hashlib.sha256(
            ("|".join("%s:%s" % c for c in cols)).encode("utf-8")).hexdigest()[:16],
        "column_count": len(cols),
        "script_sha": sha256_file(SELF),
        "input_fingerprint": {"rows": row[0], "ts_min": row[1], "ts_max": row[2],
                              "wal_writerTxn": w[0], "wal_sequencerTxn": w[1]},
        "captured_at": now_iso(),
    }


def reconcile_identity(prog, tbl, ident):
    """身份变化 -> 该表旧 done 项整体标 stale，随后重取。"""
    prev = prog["identities"].get(tbl)
    events = []
    if prev and prev != ident:
        diff = [k for k in ident if prev.get(k) != ident.get(k)]
        moved = 0
        for k in [k for k in prog["done"] if k.startswith(tbl + "|")]:
            prog["stale"][k] = {"value": prog["done"][k], "stale_at": now_iso(),
                                "reason": "identity changed: " + ",".join(diff)}
            del prog["done"][k]
            moved += 1
        events.append({"table": tbl, "changed_fields": diff, "stale_moved": moved})
    prog["identities"][tbl] = ident
    return events


def pick_pending(prog, tbl, segs, cap):
    pend = [s["day"] for s in segs
            if ("%s|%s|first" % (tbl, s["day"])) not in prog["done"]
            or ("%s|%s|last" % (tbl, s["day"])) not in prog["done"]]
    return pend[:cap], len(pend)


def key_of(tbl, tc, day, side):
    direction = "asc" if side == "first" else "desc"
    order_clause = ", ".join("%s %s" % (c, direction) for c in KEY_COLS)
    r = q("select %s from %s where %s >= '%s' and %s < '%s' order by %s limit 1"
          % (KEY_ORDER, tbl, tc, day, tc, next_day(day), order_clause))
    if not r["count"]:
        return None
    return dict(zip(KEY_COLS, r["dataset"][0]))


def next_day(d):
    import datetime
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def main():
    manifest = {
        "manifest_version": "census-v4-gated",
        "batch": "L-A/batch04",
        "endpoint": BASE,
        "started_at": now_iso(),
        "gates": {},
        "tables": {},
    }

    # ---- 门 1：资源 ----
    ok, rec, reasons = measure_resources()
    gate_ok, fails = resource_gate(rec, reasons)
    manifest["gates"]["resource"] = {
        "passed": gate_ok,
        "readings": rec,
        "failures": fails,
        "thresholds": {"ram_free_floor_gb": RAM_FREE_FLOOR_GB,
                       "ram_free_floor_pct": RAM_FREE_FLOOR_PCT,
                       "ram_load_ceil_pct": RAM_LOAD_CEIL_PCT,
                       "disk_free_floor_gb": DISK_FREE_FLOOR_GB},
        "rationale": (
            "阈值本地论证，非照搬：本负载为服务端聚合（SAMPLE BY 1d 仅回 614/450 行），"
            "客户端只接收小 JSON，工作集远小于 100MB；batch03 实测在 free 0.86GB / load 97% 下"
            "仍成功完成（14:55:17→14:55:18），说明阈值不是「工作负载需求」而是「给 OS 与其他会话留余量」"
            "的保守门。故取 max(1.0GB, 3% total) 与 load<=95%。"
            "云 T4 的 8GB 阈值**未在本地论证**，不采纳为本机 census 门。"
        ),
    }
    print("[gate:resource] passed=%s readings=%s fails=%s" % (gate_ok, rec, fails), flush=True)
    if not gate_ok:
        manifest["finished_at"] = now_iso()
        manifest["decision"] = "REFUSED_BY_RESOURCE_GATE"
        # 拒跑回执单独落盘：不覆盖既有成功运行的 manifest（否则拒跑会把好回执冲掉）
        atomic_write_json(REFUSED_OUT, manifest)
        print("REFUSED (resource gate). refused-record ->", REFUSED_OUT)
        return 2

    prog = load_progress()

    try:
        for tbl in TABLES:
            if budget_left() <= 0:
                raise TimeoutError("total budget exceeded before %s" % tbl)
            ident = table_identity(tbl)
            events = reconcile_identity(prog, tbl, ident)
            atomic_write_json(PROGRESS, prog)          # 身份变更即落盘
            segs = [{"day": r[0][:10], "rows": r[1]} for r in
                    q("select %s, count() c from %s sample by 1d" % (ident["designated_ts"], tbl))["dataset"]]
            picks, n_pend = pick_pending(prog, tbl, segs, MAX_SEGMENTS_PER_RUN)
            entry = {"identity": ident, "identity_events": events,
                     "segment_count": len(segs), "rows_sum": sum(s["rows"] for s in segs),
                     "pending_before": n_pend, "picked": len(picks), "keys": {}}
            print("%-15s segs=%d pending_before=%d picked=%d identity_events=%d"
                  % (tbl, len(segs), n_pend, len(picks), len(events)), flush=True)
            for day in picks:
                for side in ("first", "last"):
                    k = "%s|%s|%s" % (tbl, day, side)
                    if k in prog["done"]:
                        entry["keys"].setdefault(day, {})[side] = prog["done"][k]
                        continue
                    v = key_of(tbl, ident["designated_ts"], day, side)
                    entry["keys"].setdefault(day, {})[side] = v
                    prog["done"][k] = v
                    prog["updates"] = prog.get("updates", 0) + 1
                    atomic_write_json(PROGRESS, prog)   # 每项即落盘
            entry["pending_after"] = len([s for s in segs
                                          if ("%s|%s|first" % (tbl, s["day"])) not in prog["done"]])
            manifest["tables"][tbl] = entry
    except TimeoutError as e:
        manifest["finished_at"] = now_iso()
        manifest["decision"] = "TIMEOUT"
        manifest["timeout_detail"] = str(e)
        atomic_write_json(PROGRESS, prog)
        atomic_write_json(MANIFEST, manifest)
        print("TIMEOUT:", e)
        return 3

    manifest["finished_at"] = now_iso()
    manifest["decision"] = "RAN"
    manifest["progress_state"] = {"done_items": len(prog["done"]),
                                 "stale_items": len(prog["stale"]),
                                 "updates": prog.get("updates")}
    atomic_write_json(PROGRESS, prog)
    atomic_write_json(MANIFEST, manifest)
    print("\nmanifest ->", MANIFEST)
    print("progress ->", PROGRESS, manifest["progress_state"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
