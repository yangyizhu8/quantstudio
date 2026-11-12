#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R-20261112-03 甲 + 序1/序2（只读·免额度）—— 判据合规版 v2。

## v2 修订（2026-11-12 总调度三处纠正后重写）

1. **不以间隔天数直接判节假日或真实缺口**
   v1 写「9~20 天 ⇒ 节假日型 / >20 天 ⇒ 异常」= 凭天数猜，违规。
   v2：间隔天数只**标记候选断点**，结论必须来自日历语义判据。
2. **日历有开市日 ⇒ 只记「候选缺口」**，仍须核 **停牌 / 上市状态 / 源 bar 契约**。
3. **日历查询 0 行 ⇒ 先验证日历覆盖完整、日期类型/时区、开市标志口径**，不通过则记**未知**。
4. **守候器每次探测都写进度行**（v1 前 15 次静默 = 无进度证据，违规）。
5. **三态输出**：`候选缺口` / `已排除（附口径验证）` / `未知（附原因）`——**不下「真实缺口」定论**。

序1：ETF「日线 raw OHLC 不一致」1,111 条码分布 + TOP20 集中度
序2：000615 128 条 created_at 批次分布
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

CST = 8 * 3600
CODE = "000538"
GAP_DAYS = 10  # 仅用于标记候选断点，绝不作结论


def d(v) -> str:
    if v is None:
        return "?"
    return dt.datetime.utcfromtimestamp(int(v) / 1000 + CST).strftime("%Y-%m-%d")


def bj_zero_ms(yyyy_mm_dd: str) -> int:
    """'YYYY-MM-DD' → 该日北京零点的 epoch-ms（库内 time 口径）。"""
    base = dt.datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return int((base - dt.datetime(1970, 1, 1)).total_seconds() * 1000) + CST * 1000


def log(msg: str) -> None:
    print(msg, flush=True)


def try_open(db: str):
    try:
        import duckdb
        return duckdb.connect(db, read_only=True), None
    except Exception as e:
        m = str(e)
        if "另一个程序正在使用此文件" in m or "already open" in m.lower():
            return None, "locked"
        return None, f"other:{type(e).__name__}"


# ================= 日历探测与计数 =================
def probe_calendar(con):
    tabs = {x[0] for x in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    if "trade_calendar" not in tabs:
        return {"ok": False, "why": "trade_calendar 表不存在"}
    desc = con.execute("DESCRIBE trade_calendar").fetchall()
    cols = [c[0] for c in desc]
    date_col = next((c for c in cols if c.lower() in
                     ("trade_date", "cal_date", "date", "dt")), None) or \
               next((c for c in cols if "date" in c.lower()), None)
    flag_col = next((c for c in cols if c.lower() in
                     ("is_trading", "is_open", "open_flag", "is_trade", "trading")), None) or \
                next((c for c in cols if "open" in c.lower() or "trad" in c.lower()), None)
    out = {"ok": False, "cols": cols, "date_col": date_col, "flag_col": flag_col}
    if not date_col:
        out["why"] = "未找到日期列"
        return out
    n = con.execute("SELECT COUNT(*) FROM trade_calendar").fetchone()[0]
    mn, mx = con.execute(
        f"SELECT MIN({date_col}), MAX({date_col}) FROM trade_calendar").fetchone()
    sv = (con.execute(f"SELECT {date_col} FROM trade_calendar LIMIT 1").fetchone() or [None])[0]
    if isinstance(sv, int):
        form = "int_yyyymmdd" if sv > 19000000 else "int_other"
    elif isinstance(sv, str):
        form = "str_dash" if "-" in sv else ("str_compact" if sv.isdigit() else "str_other")
    elif isinstance(sv, (dt.date, dt.datetime)):
        form = "date_type"
    else:
        form = "unknown"
    fv = None
    if flag_col:
        try:
            fv = [r[0] for r in con.execute(
                f"SELECT DISTINCT {flag_col} FROM trade_calendar ORDER BY 1 LIMIT 8").fetchall()]
        except Exception:
            fv = None
    out.update({"rows": n, "min": str(mn), "max": str(mx), "form": form,
                "flag_vocab": fv, "ok": n > 0})
    return out


def _norm8(s: str) -> str:
    return str(s).replace("-", "").replace(" ", "")[:8]


def cal_count_between(con, cal, lo: str, hi: str):
    """→ (cnt | None, note)。None = 不可判（未知）。"""
    if not cal.get("ok"):
        return None, f"日历不可用（{cal.get('why') or '空表'}）"
    dc, fc, form = cal["date_col"], cal["flag_col"], cal["form"]
    cmin, cmax = _norm8(cal["min"]), _norm8(cal["max"])
    lo8, hi8 = _norm8(lo), _norm8(hi)
    # S1 覆盖检查（不通过 ⇒ 未知，绝不据空查询判「无交易日」）
    if not (cmin <= lo8 and hi8 <= cmax):
        return None, f"日历覆盖不足（{cal['min']}~{cal['max']} 不含 {lo}~{hi}）"
    # S2 形态
    if form == "int_yyyymmdd":
        lo_v, hi_v = int(lo8), int(hi8)
    elif form == "str_dash":
        lo_v, hi_v = lo, hi
    elif form == "str_compact":
        lo_v, hi_v = lo8, hi8
    elif form == "date_type":
        lo_v, hi_v = lo, hi
    else:
        return None, f"日期形态未确认（{form}）"
    where = f"{dc} > ? AND {dc} < ?"
    try:
        total = con.execute(
            f"SELECT COUNT(*) FROM trade_calendar WHERE {where}", [lo_v, hi_v]).fetchone()[0]
    except Exception as e:
        return None, f"查询失败 {type(e).__name__}"
    if total == 0:
        return 0, "区间内日历无任何记录（覆盖检查已通过）"
    if fc and cal.get("flag_vocab"):
        vocab = [str(v) for v in cal["flag_vocab"]]
        if set(vocab) <= {"0", "1", "True", "False", "true", "false"}:
            opens = [v for v in cal["flag_vocab"] if str(v).lower() in ("1", "true")]
            if opens:
                ph = ",".join("?" for _ in opens)
                try:
                    n = con.execute(
                        f"SELECT COUNT(*) FROM trade_calendar WHERE {where} AND {fc} IN ({ph})",
                        [lo_v, hi_v] + opens).fetchone()[0]
                    return n, f"按 {fc} 过滤（取值域 {vocab}）"
                except Exception as e:
                    return None, f"标志过滤失败 {type(e).__name__}"
        return None, f"开市标志取值域不可解释（{vocab}）"
    return total, "无可用开市标志 ⇒ 仅计记录数（口径未确认，按候选处理）"


# ================= 甲 =================
def run_jia(con):
    log(f"\n=== 【甲】{CODE} 断点判定（判据合规版 v2：不下「真实缺口」定论）===")
    cols = [c[0] for c in con.execute("DESCRIBE stock_daily").fetchall()]
    log(f"  stock_daily 列 = {','.join(cols)}")
    suspend_col = next((c for c in cols if "suspend" in c.lower()), None)
    ts = [int(r[0]) for r in con.execute(
        "SELECT time FROM stock_daily WHERE code=? ORDER BY time", [CODE]).fetchall()]
    if not ts:
        log(f"  {CODE} 无数据")
        return
    log(f"  总行数 = {len(ts)}  首={d(ts[0])}  末={d(ts[-1])}  停牌列={suspend_col}")

    cal = probe_calendar(con)
    log(f"  日历探测：ok={cal.get('ok')} 日期列={cal.get('date_col')} 标志列={cal.get('flag_col')}")
    log(f"            形态={cal.get('form')} 覆盖={cal.get('min')}~{cal.get('max')} "
        f"行={cal.get('rows')} 标志域={cal.get('flag_vocab')}"
        + (f"  问题={cal['why']}" if cal.get("why") else ""))

    cand = [(d(a), d(b), round((b - a) / 86400000, 1))
            for a, b in zip(ts, ts[1:]) if (b - a) / 86400000 > GAP_DAYS]
    log(f"\n  候选断点（间隔 >{GAP_DAYS} 天，**仅标记不下结论**）= {len(cand)} 处")

    verdicts = []
    for i, (lo, hi, gap) in enumerate(cand, 1):
        log(f"\n  ── 断点 {i}: {lo} → {hi}（间隔 {gap} 天，仅标记）")
        cnt, note = cal_count_between(con, cal, lo, hi)
        if cnt is None:
            v = "未知"
            log(f"     S1/S2 ⇒ **未知**（{note}）")
        elif cnt == 0:
            v = "已排除"
            log(f"     S3 中间开市日=0（{note}）⇒ **已排除**（日历口径已验证，非缺口）")
        else:
            v = "候选缺口"
            log(f"     S3 中间开市日={cnt}（{note}）⇒ **候选缺口** ⇒ 继续 S4/S5/S6")
            if suspend_col:
                log(f"     S4 停牌：区间内无 bar，{suspend_col} 无从取值（该码区间内零行）")
            else:
                log("     S4 停牌：无 suspend 列 ⇒ **未核**")
            try:
                lo_ms, hi_ms = bj_zero_ms(lo), bj_zero_ms(hi)
                p = con.execute(
                    "SELECT COUNT(DISTINCT code) FROM stock_daily "
                    "WHERE code LIKE '000%' AND time > ? AND time < ?", [lo_ms, hi_ms]).fetchone()[0]
                log(f"     S5 上市状态：该码首日 {d(ts[0])}；同市场(000*)在区间内有数据的码数 = {p}")
            except Exception as e:
                log(f"     S5 上市状态：查询失败 {type(e).__name__}")
            log("     S6 源 bar 契约：需重拉 MCP 上游该区间 bar ⇒ **未验证（未授权重拉）**")
            log("     ⇒ 三项未核全，**不下「真实缺口」定论**")
        verdicts.append((i, lo, hi, gap, v, note))

    log("\n  ── 断点结论汇总（三态）──")
    for i, lo, hi, gap, v, note in verdicts:
        log(f"    {i}. {lo}→{hi} ({gap}d)  **{v}**  {note}")


# ================= 序1 / 序2 =================
def run_xu(con):
    log("\n=== 【序1】ETF 日线 raw OHLC 不一致的码分布 ===")
    tot = con.execute("""
    SELECT COUNT(DISTINCT code) nc, COUNT(*) n FROM qfq_trigger_queue
    WHERE status='dead_letter' AND asset_type='ETF' AND last_error LIKE '%日线 raw OHLC%'""").fetchone()
    log(f"  涉及码数 = {tot[0]}   总条数 = {tot[1]:,}")
    rows = con.execute("""
    SELECT code, COUNT(*) n FROM qfq_trigger_queue
    WHERE status='dead_letter' AND asset_type='ETF' AND last_error LIKE '%日线 raw OHLC%'
    GROUP BY 1 ORDER BY n DESC LIMIT 20""").fetchall()
    for i, r in enumerate(rows, 1):
        log(f"    {i:>2d}. {r[0]:<8s} {r[1]:>4,}")
    if tot[0] and rows:
        s = sum(r[1] for r in rows)
        pct = s / max(tot[1], 1) * 100
        log(f"  TOP20 合计 {s:,} / {tot[1]:,} = {pct:.1f}%")
        log("  集中度判读：" + ("高度集中（少数码主导，重拉范围可大幅缩小）"
                              if pct > 60 else "较均匀（优先修 raw 比对逻辑而非直接重拉）"))

    log("\n=== 【序2】000615 128 条的 created_at 批次分布 ===")
    nb = con.execute("""
    SELECT COUNT(DISTINCT created_at) FROM qfq_trigger_queue
    WHERE status='dead_letter' AND code='000615' AND asset_type='STOCK'""").fetchone()[0]
    log(f"  distinct created_at = {nb}")
    for r in con.execute("""
    SELECT created_at, COUNT(*) n FROM qfq_trigger_queue
    WHERE status='dead_letter' AND code='000615' AND asset_type='STOCK'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall():
        log(f"    {r[0]}  {r[1]:>3,} 条")


def run(db: str) -> int:
    con, why = try_open(db)
    if con is None:
        log(f"FATAL: 库不可读（{why}）")
        return 3
    try:
        log("NOW = " + str(con.execute("SELECT NOW()").fetchone()[0]))
        run_jia(con)
        run_xu(con)
        return 0
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"D:\miniQMT策略实盘\QuantStudio\data\quantstudio.db")
    ap.add_argument("--wait-lock", action="store_true")
    ap.add_argument("--max-wait-hours", type=float, default=14.0)
    ap.add_argument("--poll", type=int, default=20)
    a = ap.parse_args(argv)

    if not a.wait_lock:
        return run(a.db)

    deadline = time.time() + a.max_wait_hours * 3600
    n, t0 = 0, time.time()
    log(f"[{time.strftime('%H:%M:%S')}] 守候开始 poll={a.poll}s max={a.max_wait_hours}h "
        f"db={a.db}（**每次探测均留进度行**）")
    why = "init"
    while time.time() < deadline:
        n += 1
        con, why = try_open(a.db)
        el = int(time.time() - t0)
        if con is not None:
            con.close()
            log(f"[{time.strftime('%H:%M:%S')}] #{n} 锁已释放（等待 {el}s）⇒ 开始取证")
            time.sleep(3)
            return run(a.db)
        log(f"[{time.strftime('%H:%M:%S')}] #{n} 仍锁（{why}，已等 {el}s）")
        time.sleep(a.poll)
    log(f"[{time.strftime('%H:%M:%S')}] 超时 {a.max_wait_hours}h（探测 {n} 次，末状态={why}）")
    return 4


if __name__ == "__main__":
    sys.exit(main())
