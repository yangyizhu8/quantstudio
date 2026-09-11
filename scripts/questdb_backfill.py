# -*- coding: utf-8 -*-
"""QuestDB 合规源回填编排器（方案 v1.4 · 2026-09-12）

职责：
  1. S1 四件套单写者探测（编排器启动前 + 每表切换前）
  2. S5 避让窗（16:00 ETL / 21:00 / 21:45 / 22:30 / 03:00 / 05:00 / 08:55 —— 写入期暂停、表级断点续跑）
  3. 表级状态 + 分片 ledger 断点（分片 ledger 由 writers.write_passthrough_chunked 维护）
  4. 单表失败隔离（失败记录并继续下一表）
  5. 双模式读数：baseline（QDB 读 -> passthrough 直写） / chain（分片 + ledger 全径）

红线（v1.4 §十二）：所有写入仅经 writers.py 两合法通道；本编排器不自建 connect+INSERT。
   baseline 模式 -> writer.write(df, table, batch_id, passthrough=True)
   chain    模式 -> writer.write_passthrough_chunked(table, batch_id, chunk_iter)

用法：
  python scripts/questdb_backfill.py --mode chain --tables daily_info,sw_daily --dry-run
  python scripts/questdb_backfill.py --mode baseline --tables daily_info
  python scripts/questdb_backfill.py --mode chain --all --order-file data/logs/s4_backfill_order.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.sources.questdb_adapter import QuestDBAdapter  # noqa: E402
from quantstudio.pipeline.writers import DuckDBWriter  # noqa: E402

LOG = logging.getLogger("questdb_backfill")
DATA = ROOT / "data"
STATE_PATH = DATA / "logs" / "questdb_backfill_state.json"
EVID_DIR = DATA / "logs"

# ── S5 避让窗（本地时间；保守前后留 5 分钟缓冲）────────────────────
AVOID_WINDOWS = [
    (dtime(15, 45), dtime(21, 0),  "Trading_Daily_ETL_1600（含巡检，~16:00-20:50）"),
    (dtime(20, 55), dtime(21, 40), "Trading_MinutesDailyBackfill_2100"),
    (dtime(21, 40), dtime(22, 0),  "Trading_EtfAdjEveningFill 21:45"),
    (dtime(22, 25), dtime(23, 15), "Trading_CyqChips_Evening_Fill 22:30"),
    (dtime(2, 55),  dtime(3, 30),  "TradingCloudSync 03:00"),
    (dtime(4, 55),  dtime(5, 30),  "TradingCloudParity 05:00"),
    (dtime(8, 50),  dtime(9, 20),  "Trading_MinutesCoverage_0855 / PreMarket_Check 09:05"),
]


def avoid_window_now(now=None):
    """当前是否落在避让窗；返回窗口说明或 None。"""
    t = (now or datetime.now()).time()
    for s, e, why in AVOID_WINDOWS:
        if s <= t < e:
            return f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')} {why}"
    return None


def wait_until_clear(max_wait_s=3600):
    """阻塞直到离开避让窗（返回等待秒数）；超过上限则返回 None（交由调用方决策）。"""
    waited = 0
    while True:
        why = avoid_window_now()
        if not why:
            return waited
        if waited >= max_wait_s:
            LOG.warning("避让窗 %s 持续超过 %ds，仍未离开", why, max_wait_s)
            return None
        LOG.info("S5 避让：%s -> 暂停写入，60s 后重探", why)
        time.sleep(60)
        waited += 60


def default_package() -> Path:
    """package-first：今日包文件（QuantStudio 根目录）。"""
    return ROOT / f"quantstudio_data_package_{datetime.now().strftime('%Y%m%d')}.db"


def s1_probe(target_db: Path = None, require_main_free: bool = False, log=LOG) -> dict:
    """S1 四件套单写者探测。

    package-first（2026-09-12 裁定）：写入目标是**独立包文件**，故权威判据是
      「目标库可读写打开」；主库仅在 require_main_free=True（Part 1 / 主库模式）时
      才要求无写者（主库被研究/测试进程持有不再阻断 Part 2 回填）。
    陈旧锁清理需 --clean-locks。
    """
    target_db = target_db or default_package()
    import os
    import subprocess
    r = dict(daemon_lock=None, collector_lock=None, write_lock=None,
             processes=[], duckdb_writable=None, ok=False)
    for name, fn in (("daemon_lock", DATA / ".daemon.lock"),
                     ("collector_lock", DATA / ".collector_run.lock")):
        if fn.exists():
            r[name] = dict(exists=True, size=fn.stat().st_size)
        else:
            r[name] = dict(exists=False, size=0)
    wl = DATA / "snapshots" / ".write_lock"
    r["write_lock"] = dict(exists=wl.exists(),
                           age_sec=int(time.time() - wl.stat().st_mtime) if wl.exists() else None)
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             " Where-Object { $_.CommandLine -match 'quantstudio' } | "
             " Select-Object -ExpandProperty ProcessId) -join ','"],
            capture_output=True, text=True, timeout=60)
        pids = [p for p in (out.stdout or "").strip().split(",") if p]
        r["processes"] = pids
    except Exception as e:
        log.warning("进程扫描失败（保守视为有写者）: %s", e)
        r["processes"] = ["unknown"]
    # 权威判据①：目标库（包文件）可读写打开。
    # duckdb 单写者=排他：任何持有者（含只读打开）都会让读写打开失败并报持有 PID。
    try:
        import duckdb
        con = duckdb.connect(str(target_db))
        con.execute("SELECT 1").fetchone()
        con.close()
        r["target_writable"] = True
    except Exception as e:
        r["target_writable"] = False
        r["target_error"] = str(e)[:200]
    r["target_db"] = str(target_db)
    r["target_exists"] = Path(target_db).exists()

    # 判据②：主库仅在需要写主库时要求无写者（Part 2 包模式不需要）
    r["main_check_required"] = bool(require_main_free)
    r["main_free"] = None
    try:
        import duckdb
        con = duckdb.connect(str(DATA / "quantstudio.db"))
        con.execute("SELECT 1").fetchone()
        con.close()
        r["main_free"] = True
    except Exception as e:
        r["main_free"] = False
        r["main_error"] = str(e)[:200]
    r["duckdb_writable"] = r["target_writable"]   # 兼容旧字段名

    # 进程扫描作为**辅助**信号：仅 pipeline 写者模式硬阻断；
    # 研究/回测（agent_workspace）/云同步/测试等只读消费进程仅记录告警。
    WRITER_PATTERNS = ("quantstudio.pipeline.daemon", "--mode once",
                       "quantstudio.pipeline.collector", "run_etl_and_check",
                       "questdb_backfill.py")
    writers, readers = [], []
    for pid in r["processes"]:
        if pid == "unknown":
            writers.append(pid)
            continue
        try:
            import subprocess as _sp
            cl = _sp.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine"],
                         capture_output=True, text=True, timeout=30).stdout or ""
        except Exception:
            cl = ""
        (writers if any(p in cl for p in WRITER_PATTERNS) else readers).append(pid)
    r["pipeline_writers"] = writers
    r["read_only_processes"] = readers
    if readers:
        log.info("S1 提示：%d 个只读消费进程在跑（研究/同步/测试），不影响单写者判据: %s",
                 len(readers), readers)

    r["blocking_writers"] = writers
    ok = (r["target_writable"] and not writers
          and not (r["collector_lock"] or {}).get("exists")
          and not (r["write_lock"] or {}).get("exists"))
    if require_main_free:
        ok = ok and bool(r["main_free"])
    r["ok"] = ok
    return r


def clean_stale_locks(log=LOG) -> list:
    """清理 0 字节陈旧锁（仅在 S1 进程扫描为空时调用）。"""
    removed = []
    for p in (DATA / ".daemon.lock", DATA / ".collector_run.lock"):
        if p.exists() and p.stat().st_size == 0:
            p.unlink()
            removed.append(p.name)
            log.info("清理陈旧锁: %s", p.name)
    return removed


def s5_gated(chunk_iter, max_wait_s=3600, log=LOG):
    """S5 门控迭代器：每片写入前检查避让窗，命中则等待（片级断点续跑）。

    与 writers.write_passthrough_chunked 的分片 ledger 配合 —— 暂停发生在片边界，
    恢复后从下一片续插，不整表重跑（B+ 增补语义）。
    """
    for key, df in chunk_iter:
        why = avoid_window_now()
        if why:
            log.warning("S5 表内避让：%s -> 片 %s 前暂停", why, key)
            if wait_until_clear(max_wait_s) is None:
                log.error("避让未结束，主动中断分片迭代（本表可续跑）")
                raise RuntimeError(f"S5 避让窗未结束（{why}）")
            log.info("S5 避让结束，从片 %s 续跑", key)
        yield key, df


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return dict(tables={}, runs=[])


def save_state(st: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def resolve_tables(args) -> list:
    if args.tables:
        return [t.strip() for t in args.tables.split(",") if t.strip()]
    if args.all:
        order_file = Path(args.order_file)
        if not order_file.is_absolute():
            order_file = ROOT / args.order_file
        doc = json.loads(order_file.read_text(encoding="utf-8"))
        return [r["table"] for r in doc["order"]]
    return []


def run_one(adapter, writer, table, mode, start, end, log=LOG) -> dict:
    """单表回填（单表失败隔离由调用方 try/except 负责）。"""
    batch_id = f"questdb_bf_{table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    t0 = time.perf_counter()
    if mode == "baseline":
        df, meta = adapter.fetch_table(table, start, end, freq="daily")
        rows_in = len(df)
        t_read = time.perf_counter() - t0
        t1 = time.perf_counter()
        written = writer.write(df, table, batch_id, passthrough=True)
        t_write = time.perf_counter() - t1
        chunks = 1
        rebuilt = None
        resumed_from = 0
    else:
        meta, chunk_iter = adapter.fetch_table_chunked(table, start, end, freq="daily")
        t_read = None
        t1 = time.perf_counter()
        res = writer.write_passthrough_chunked(
            table, batch_id, s5_gated(chunk_iter, log=log), resume=True)
        t_write = time.perf_counter() - t1
        rows_in = res["written"]
        written = res["written"]
        chunks = res["chunks"]
        rebuilt = res["rebuilt"]
        resumed_from = res["resumed_from"]
    elapsed = time.perf_counter() - t0
    out = dict(table=table, mode=mode, rows=written, chunks=chunks,
               wrote_rows=written, read_rows=rows_in, elapsed_sec=round(elapsed, 2),
               rows_per_sec=round(written / elapsed, 1) if elapsed > 0 else 0,
               batch_id=batch_id, date_basis=meta.get("date_basis"),
               watermark_basis=meta.get("watermark_basis"),
               qdb_rows=meta.get("qdb_rows"), rebuilt=rebuilt, resumed_from=resumed_from)
    if t_read is not None:
        out["t_read_sec"] = round(t_read, 2)
        out["t_write_sec"] = round(t_write, 2)
    return out


def main():
    ap = argparse.ArgumentParser(description="QuestDB 合规源回填编排器（v1.4）")
    ap.add_argument("--mode", choices=["baseline", "chain"], default="chain")
    ap.add_argument("--tables", type=str, default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--order-file", type=str, default="data/logs/s4_backfill_order.json")
    ap.add_argument("--start", type=str, default="2000-01-01")
    ap.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true", help="只做 S1/S5 探测与计划，不写库")
    ap.add_argument("--clean-locks", action="store_true", help="S1 通过后清理 0 字节陈旧锁")
    ap.add_argument("--max-avoid-wait", type=int, default=3600)
    ap.add_argument("--evidence", type=str, default="")
    ap.add_argument("--target", type=str, default="",
                    help="写入目标 db（默认=今日包文件 quantstudio_data_package_<date>.db）")
    ap.add_argument("--write-main", action="store_true",
                    help="写主库模式（默认 package-first：写包文件，主库零写入）")
    ap.add_argument("--days-per-slice", type=int, default=1,
                    help="chain 模式片跨度（天）：片数↓则吞吐↑，断点粒度变粗（默认 1）")
    args = ap.parse_args()
    target = Path(args.target) if args.target else (
        DATA / "quantstudio.db" if args.write_main else default_package())

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    tables = resolve_tables(args)
    if not tables:
        LOG.error("未指定表：用 --tables a,b 或 --all")
        return 2

    LOG.info("=== QuestDB 回填编排器 | mode=%s tables=%d start=%s end=%s ===",
             args.mode, len(tables), args.start, args.end)

    LOG.info("写入目标: %s (package-first=%s)", target, not args.write_main)

    # ── S1 四件套 ──
    probe = s1_probe(target, require_main_free=args.write_main)
    LOG.info("S1 探测: processes=%s collector_lock=%s write_lock=%s duckdb=%s",
             probe["processes"], probe["collector_lock"], probe["write_lock"],
             probe["duckdb_writable"])
    if args.clean_locks and not probe["processes"]:
        probe["removed_locks"] = clean_stale_locks()
        probe = s1_probe(target, require_main_free=args.write_main)
    if not probe["ok"]:
        LOG.error("S1 未通过，拒绝启动写入：%s", probe)
        return 3

    # ── S5 避让 ──
    why = avoid_window_now()
    if why:
        LOG.warning("当前处于 S5 避让窗：%s", why)
        if args.dry_run:
            return 0
        if wait_until_clear(args.max_avoid_wait) is None:
            LOG.error("避让窗未在限定时间结束，退出（可稍后重跑，断点续跑）")
            return 4

    adapter = QuestDBAdapter(dict(name="questdb",
                                  days_per_slice=args.days_per_slice))
    writer = DuckDBWriter(dict(path=str(target)))
    st = load_state()
    run_rec = dict(ts=datetime.now().isoformat(timespec="seconds"), mode=args.mode,
                   start=args.start, end=args.end, results=[])

    LOG.info("计划表（%d）: %s", len(tables), ",".join(tables[:8]) + ("..." if len(tables) > 8 else ""))
    if args.dry_run:
        for t in tables:
            try:
                spec = adapter.spec(t)
                LOG.info("  [dry] %-28s rows=%-12s date_basis=%-14s watermark=%s",
                         t, spec.get("qdb_rows"), spec.get("date_basis"),
                         spec.get("watermark_basis"))
            except Exception as e:
                LOG.warning("  [dry] %s 不在映射表: %s", t, e)
        LOG.info("dry-run 完成（未写库）")
        return 0

    for i, table in enumerate(tables, 1):
        LOG.info("---- [%d/%d] %s ----", i, len(tables), table)
        why = avoid_window_now()
        if why:
            LOG.warning("进入 S5 避让窗：%s", why)
            if wait_until_clear(args.max_avoid_wait) is None:
                LOG.error("避让未结束，停在 %s（已完成表零重写；本表可续跑）", table)
                break
        pr = s1_probe(target, require_main_free=args.write_main)
        if not pr["ok"]:
            LOG.warning("S1 复检未通过（%s），等 120s 重试",
                        pr.get("target_error") or pr["processes"])
            time.sleep(120)
            if not s1_probe(target, require_main_free=args.write_main)["ok"]:
                LOG.error("S1 仍不通过，中止（停在 %s）", table)
                break
        try:
            rec = run_one(adapter, writer, table, args.mode, args.start, args.end)
            rec["status"] = "success"
            LOG.info("[%s] 完成 rows=%s chunks=%s %.1fs (%.0f 行/s)",
                     table, f"{rec['rows']:,}", rec["chunks"], rec["elapsed_sec"],
                     rec["rows_per_sec"])
        except Exception as e:
            rec = dict(table=table, mode=args.mode, status="failed", error=str(e)[:300])
            LOG.error("[%s] 失败（单表隔离，继续下一表）: %s", table, e)
        run_rec["results"].append(rec)
        st["tables"][table] = dict(status=rec["status"], mode=args.mode,
                                   rows=rec.get("rows"),
                                   elapsed_sec=rec.get("elapsed_sec"),
                                   ts=run_rec["ts"],
                                   error=rec.get("error"))
        save_state(st)

    ok = [r for r in run_rec["results"] if r["status"] == "success"]
    bad = [r for r in run_rec["results"] if r["status"] != "success"]
    total_rows = sum(r.get("rows") or 0 for r in ok)
    total_sec = sum(r.get("elapsed_sec") or 0 for r in ok)
    run_rec["summary"] = dict(success=len(ok), failed=len(bad), rows=total_rows,
                              elapsed_sec=round(total_sec, 1),
                              rows_per_sec=round(total_rows / total_sec, 1) if total_sec else 0)
    LOG.info("==== 汇总: 成功 %d / 失败 %d | rows=%s | %.1fs | %.0f 行/s ====",
             len(ok), len(bad), f"{total_rows:,}", total_sec,
             run_rec["summary"]["rows_per_sec"])
    if bad:
        for r in bad:
            LOG.warning("  失败: %s -> %s", r["table"], r.get("error"))

    ev = Path(args.evidence) if args.evidence else (
        EVID_DIR / f"questdb_backfill_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    ev.write_text(json.dumps(dict(probe=probe, run=run_rec), ensure_ascii=False, indent=1),
                  encoding="utf-8")
    LOG.info("证据: %s", ev)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
