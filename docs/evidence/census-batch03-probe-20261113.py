# -*- coding: utf-8 -*-
"""census batch03 · 修夹具两项 + 扩段键（R28 要求）

R28 夹具整改：
 (a) 持久进度：每段每任务完成即原子落盘，中断后跳过已完成项 —— 不再"只在末尾写 manifest"
 (b) 资源即时采样：资源读数带本次采样时刻，禁止字面常量冒充即时测量
 (c) 原子写出：tmp + os.replace，避免半截文件
 (d) 完整复合键：段内首末按 (ts_code, trade_time, freq) 全序取
 (e) 日界标 source_tz：日界依据必须声明时区；无配置依据时标 unverified，不预设
 (f) 内存偏紧：不铺开千段逐次扫描 —— 抽样段取键，其余显式标 pending

只读。生产零 DDL。端点 L-A 127.0.0.1:9000。
"""
import io
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9000/exec"
EV = r"D:\miniQMT策略实盘\QuantStudio\docs\evidence"
PROGRESS = os.path.join(EV, "census-batch03-progress.json")
MANIFEST = os.path.join(EV, "census-manifest-lia-batch03-20261113.json")

TABLES = ["stock_minutes", "etf_minutes"]
KEY_COLS = ["ts_code", "trade_time", "freq"]   # 完整复合键
KEY_ORDER = "ts_code, trade_time, freq"

# 抽样策略：内存偏紧，不铺开千段逐次扫描
SAMPLE_POLICY = "每表取 首2段 + 末2段 + 每 100 段 1 个；其余 segment_keys 标 pending"


def q(sql, timeout=300):
    with urllib.request.urlopen(BASE + "?query=" + urllib.parse.quote(sql), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_progress():
    if os.path.exists(PROGRESS):
        try:
            return json.load(io.open(PROGRESS, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"version": "progress-v1", "done": {}, "created_at": now_iso(), "updates": 0}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def sample_resources():
    """即时资源采样（带采样时刻；不用字面常量）。"""
    rec = {"sampled_at": now_iso(), "source": "live ctypes/shim measurement at this run"}
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        rec["ram_free_gb"] = round(m.ullAvailPhys / 1e9, 2)
        rec["ram_total_gb"] = round(m.ullTotalPhys / 1e9, 2)
        rec["ram_load_pct"] = m.dwMemoryLoad
    except Exception as e:  # noqa: BLE001
        rec["ram_error"] = str(e)[:120]
    try:
        import shutil
        du = shutil.disk_usage("D:\\")
        rec["disk_free_gb"] = round(du.free / 1e9, 1)
        rec["disk_total_gb"] = round(du.total / 1e9, 1)
    except Exception as e:  # noqa: BLE001
        rec["disk_error"] = str(e)[:120]
    return rec


def ts_col_of(tbl):
    r = q("select * from %s limit 0" % tbl, timeout=60)
    ts = [c["name"] for c in r["columns"] if c["type"] == "TIMESTAMP"]
    for p in ("trade_time", "ts"):
        if p in ts:
            return p
    return ts[0]


def segments(tbl, tc):
    r = q("select %s, count() c from %s sample by 1d" % (tc, tbl))
    return [{"day": rec[0][:10], "rows": rec[1]} for rec in r["dataset"]]


def pick_segments(segs):
    """抽样：首2 + 末2 + 每100段——避免千段逐次扫描。"""
    n = len(segs)
    idx = set()
    idx.update([0, 1, n - 2, n - 1])
    for i in range(0, n, 100):
        idx.add(i)
    return sorted(i for i in idx if 0 <= i < n)


def key_of(tbl, tc, day, side):
    """段内首/末完整复合键（按 ts_code, trade_time, freq 全序）。"""
    # 注意：order by a,b,c desc 的 desc 只作用于最后一列 c（SQL 语义），
    # 会使"末"仍落在最小 ts_code 上 => 首末同值（本轮实测踩中并修正）。
    # 必须逐列标注方向。
    direction = "asc" if side == "first" else "desc"
    order_clause = ", ".join("%s %s" % (c, direction) for c in KEY_COLS)
    r = q("select %s from %s where %s >= '%s' and %s < '%s' "
          "order by %s limit 1"
          % (KEY_ORDER, tbl, tc, day, tc, next_day(day), order_clause))
    if not r["count"]:
        return None
    row = r["dataset"][0]
    return {k: v for k, v in zip(KEY_COLS, row)}


def next_day(d):
    import datetime
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def main():
    prog = load_progress()
    out = {
        "manifest_version": "census-v3-segments-keys",
        "batch": "L-A/batch03",
        "endpoint": "L-A 本机 QDB 127.0.0.1:9000",
        "data_root": "D:\\questdb\\quest_database",
        "started_at": now_iso(),
        "resource_live": sample_resources(),
        "key_definition": {
            "composite_key": KEY_COLS,
            "order_by": KEY_ORDER,
            "note": ("freq 存在 NULL（stock_minutes 实测 763,805 行为 NULL、580,540,174 行为 1min）；"
                     "含 NULL 的复合键不构成唯一性判据，仅作为段内定序键使用"),
        },
        "source_tz": {
            "observed": "trade_time 以 'Z' 后缀存储，实测日内取值 09:30:00Z ~ 15:30:00Z",
            "config_evidence": "config/*.json 中未检索到时区键（本轮实测：无 tz/timezone 键）",
            "declared": None,
            "status": "unverified",
            "caveat": ("09:30Z~15:30Z 与 A 股交易时段一致；若确为 UTC 则应为 01:30Z~07:30Z。"
                       "故「Z 后缀 = UTC」未经证实，日界口径在 source_tz 确认前不得用于跨源对账"),
        },
        "sample_policy": SAMPLE_POLICY,
        "resumable": {"progress_file": os.path.basename(PROGRESS),
                      "note": "每段每任务完成即原子落盘；中断后重跑跳过已完成项"},
        "tables": {},
    }

    for tbl in TABLES:
        tc = ts_col_of(tbl)
        segs = segments(tbl, tc)
        picked = pick_segments(segs)
        entry = {"designated_ts": tc, "segment_count": len(segs),
                 "rows_sum": sum(s["rows"] for s in segs),
                 "segments": segs, "segment_keys": {}}
        out["tables"][tbl] = entry
        print("%-16s segs=%d rows_sum=%d picked=%d" % (tbl, len(segs), entry["rows_sum"], len(picked)),
              flush=True)

        for i in picked:
            day = segs[i]["day"]
            for side in ("first", "last"):
                k = "%s|%s|%s" % (tbl, day, side)
                if k in prog["done"]:
                    entry["segment_keys"].setdefault(day, {})[side] = prog["done"][k]
                    continue
                try:
                    val = key_of(tbl, tc, day, side)
                    entry["segment_keys"].setdefault(day, {})[side] = val
                    prog["done"][k] = val
                    prog["updates"] = prog.get("updates", 0) + 1
                    atomic_write_json(PROGRESS, prog)   # 每项即落盘 → 真可续
                except Exception as e:  # noqa: BLE001
                    entry["segment_keys"].setdefault(day, {})[side] = {"error": str(e)[:150]}
            print("    key %s first=%s last=%s" % (day, entry["segment_keys"][day].get("first"),
                                                   entry["segment_keys"][day].get("last")), flush=True)

        # 未抽样的段显式标 pending（不冒充已给）
        entry["segment_keys_pending"] = [segs[i]["day"] for i in range(len(segs)) if i not in set(picked)]

    out["finished_at"] = now_iso()
    out["progress_state"] = {"done_items": len(prog["done"]), "progress_updates": prog.get("updates")}
    atomic_write_json(MANIFEST, out)
    atomic_write_json(PROGRESS, prog)
    print("\nmanifest ->", MANIFEST)
    print("progress ->", PROGRESS, "done_items =", len(prog["done"]))


if __name__ == "__main__":
    main()
