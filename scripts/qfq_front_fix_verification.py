"""QFQ front 修正语义验证（补充 staging 演练未覆盖项）。

staging 演练首轮因 fresh xtquant 基准与库内不一致导致重锚被 BLOCK，front 修正场景不可达。
本脚本用【库内证券自身的真实数据】构造自洽的 fresh_daily/fresh_minutes 输入，直接调用
apply_reanchor_for_security(model="fresh_staged")，验证：
- 重锚能 committed（fresh 数据自洽 → precheck 通过）；
- committed 后 raw OHLC / *_back 不变（引擎只 UPDATE front 列）；
- 被注入污染的 front 行被修正回真值（验证"修正"语义）。

安全：在 staging 库（data/staging_qfq_rehearsal_20260729/，已存在）上执行，不动正式库。
若 staging 库不存在，提示先跑 qfq_staging_rehearsal.py。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb
import pandas as pd

logger = logging.getLogger("qfq_front_fix_verify")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

BJ_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
STAMP = "20260729"
STAGING_DB = ROOT / "data" / f"staging_qfq_rehearsal_{STAMP}" / "quantstudio.db"
STAGING_AUX = ROOT / "data" / f"staging_qfq_rehearsal_{STAMP}" / "qfq_aux.db"
OUTPUT_DIR = ROOT / "output" / f"qfq_staging_rehearsal_{STAMP}"

# 验证目标证券（600000，与 staging 演练污染样本一致，便于对照）
TARGET = "600000"
RAW_COLS = ["open", "high", "low", "close"]
FRONT_COLS = ["open_front", "high_front", "low_front", "close_front"]
BACK_COLS = ["open_back", "high_back", "low_back", "close_back"]


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _ensure_trade_calendar(conn) -> None:
    """用 tushare trade_cal 获取完整 SSE 交易日历（2018-2026）写入 staging trade_calendar。

    fresh_staged 的 postcheck（front-chain 收益一致性）需要连续完整的真实交易日历链
    （每天的 prev trading day）。staging 小样本库天然不连续，故从 tushare 取权威完整日历。
    """
    import os
    import tushare as ts
    from quantstudio.pipeline.qfq_reanchor_schema import DDL_DUCKDB
    conn.execute(DDL_DUCKDB["trade_calendar"])
    tok = os.environ.get("TUSHARE_TOKEN")
    if not tok:
        sys.exit("❌ 需 TUSHARE_TOKEN 环境变量获取完整交易日历")
    ts.set_token(tok)
    pro = ts.pro_api(tok)
    # 取 2017-01-01 ~ 2026-12-31 完整 SSE 日历（2017 确保 daily 首日 2018-01-02 有 prev）
    cal = pro.trade_cal(exchange="SSE", start_date="20170101", end_date="20261231")
    # cal_date 是 YYYYMMDD 字符串；is_open 0/1。转 Asia/Shanghai 自然日 epoch-ms
    records = []
    for _, r in cal.iterrows():
        day = pd.Timestamp(str(int(r["cal_date"])), tz="Asia/Shanghai").normalize()
        day_ms = int(day.value // 10**6)
        is_open = 1 if int(r["is_open"]) == 1 else 0
        records.append((day_ms, is_open))
    conn.execute("DELETE FROM trade_calendar")
    conn.executemany(
        "INSERT INTO trade_calendar (cal_date, is_open) VALUES (?, ?)", records)
    conn.commit()
    open_days = sum(1 for _, o in records if o)
    logger.info(f"trade_calendar 已填充 {len(records)} 个自然日（{open_days} 开市日，"
                f"来自 tushare SSE 2018-2026）")


def _build_fresh_inputs(conn):
    """构造【完全自洽的合成 fresh】数据验证 fresh_staged 修正语义。

    fresh_staged 模型 postcheck 的 front_chain 同时校验乘法 dev 和加法 dev：
    - 乘法 dev：|close_front(t)/close_front(prev) - close(t)/preClose(t)| ≤ tol_return
    - 加法 dev：|(close_t − cf_t) − (preClose_t − cf_prev)| ≤ 1 tick（减法复权豁免）

    恒定乘法比例 K 无法满足加法 dev（(1-K)(close-prev) ≠ 0）。
    故用【减法复权】模型：front = raw - 固定 D。这样：
    - 加法链：(close_t − (close_t−D)) − (preClose_t − (preClose_t−D)) = D − D = 0 ≤ tick ✓
    - 乘法 dev：超 tol 但被加法豁免 ✓（fresh_staged 二者满足其一即可）

    同时把库内对应证券的 front 列也设为 raw - D（建立一致的"真值"基线）。
    """
    D = 1.0  # 固定减法复权偏移（模拟前复权：front = raw - 1.0）
    # daily
    fd = conn.execute(
        f"SELECT code, time, {','.join(RAW_COLS)} FROM stock_daily "
        f"WHERE code=? ORDER BY time", [TARGET]
    ).fetchdf()
    fresh_daily = fd.copy()
    for rc, fc in zip(RAW_COLS, FRONT_COLS):
        fresh_daily[fc] = fresh_daily[rc] - D
    # minutes（需含 freq 列）
    fm = conn.execute(
        f"SELECT code, time, freq, {','.join(RAW_COLS)} FROM stock_minutes "
        f"WHERE code=? ORDER BY time", [TARGET]
    ).fetchdf()
    fresh_minutes = fm.copy()
    for rc, fc in zip(RAW_COLS, FRONT_COLS):
        fresh_minutes[fc] = fresh_minutes[rc] - D
    logger.info(f"构造合成 fresh 输入（减法复权 D={D}）：daily {len(fresh_daily)} 行，"
                f"minutes {len(fresh_minutes)} 行")
    # 把库内 front 列也设为 raw - D（建立一致的"真值"基线，供后续污染/修正对照）
    for rc, fc in zip(RAW_COLS, FRONT_COLS):
        conn.execute(f"UPDATE stock_daily SET {fc}={rc}-? WHERE code=?", [D, TARGET])
        conn.execute(f"UPDATE stock_minutes SET {fc}={rc}-? WHERE code=?", [D, TARGET])
    conn.commit()
    return fresh_daily, fresh_minutes, D


def _snapshot(conn, label: str) -> dict:
    """快照 600000 的 daily + minutes 的 raw/back/front SHA（用于前后比对）。"""
    snap = {}
    for tbl, time_col in [("stock_daily", "time"), ("stock_minutes", "time")]:
        df = conn.execute(
            f"SELECT {time_col}, {','.join(RAW_COLS)}, {','.join(BACK_COLS)}, "
            f"{','.join(FRONT_COLS)} FROM {tbl} WHERE code=? ORDER BY {time_col}",
            [TARGET]
        ).fetchdf()
        snap[tbl] = {
            "rows": len(df),
            "raw_sha": hashlib.sha256(
                df[RAW_COLS].to_csv(index=False).encode()).hexdigest(),
            "back_sha": hashlib.sha256(
                df[BACK_COLS].to_csv(index=False).encode()).hexdigest(),
            "front_sha": hashlib.sha256(
                df[FRONT_COLS].to_csv(index=False).encode()).hexdigest(),
        }
    snap["label"] = label
    return snap


def main() -> int:
    logger.info("=" * 60)
    logger.info("QFQ front 修正语义验证（补充 staging 演练）")
    logger.info("=" * 60)
    if not STAGING_DB.exists():
        sys.exit(f"❌ staging 库不存在: {STAGING_DB}\n请先跑 scripts/qfq_staging_rehearsal.py")

    result = {"started_at": _now_iso(), "target": TARGET}

    conn = duckdb.connect(str(STAGING_DB))
    try:
        # 1) 补填 trade_calendar（避免"未知日"BLOCK）
        _ensure_trade_calendar(conn)

        # 2) 构造合成自洽 fresh 输入 + 把库内 front 设为同一 D 基线（建立"真值"）
        fresh_daily, fresh_minutes, D = _build_fresh_inputs(conn)
        result["front_offset_D"] = D

        # 3) 记录原始（D 基线，未污染）快照 —— 这是重锚后 front 应回到的目标
        snap_original = _snapshot(conn, "original_D_baseline")
        result["snapshot_original"] = snap_original
        logger.info(f"原始（D={D}）快照：daily rows={snap_original['stock_daily']['rows']}")

        # 4) 注入 front 污染（daily 最近 5 行 close_front = close+1，偏离 close-D）
        pollute_rows = conn.execute(
            "SELECT time, close FROM stock_daily "
            "WHERE code=? ORDER BY time DESC LIMIT 5", [TARGET]
        ).fetchall()
        pollution = []
        for t, close in pollute_rows:
            polluted = float(close) + 1.0
            true_val = float(close) - D  # D 基线真值（front = raw - D）
            conn.execute("UPDATE stock_daily SET close_front=? WHERE code=? AND time=?",
                         [polluted, TARGET, t])
            pollution.append({"time": int(t), "true_close_front": true_val,
                              "polluted": polluted})
        conn.commit()
        logger.info(f"注入污染 {len(pollution)} 行（close_front=close+1，偏离 close-D）")
        result["pollution"] = pollution

        # 5) 调用 apply_reanchor_for_security(model="fresh_staged")
        #    fresh 用 D 减法（与库内基线一致），重锚应把污染 front 修正回 close-D
        from quantstudio.pipeline.qfq_reanchor_engine import apply_reanchor_for_security
        from quantstudio.pipeline.qfq_calendar import CalendarService
        cal = CalendarService(main_db=str(STAGING_DB))
        import uuid
        event_id = f"frontfix_{uuid.uuid4().hex[:8]}"
        capture_id = f"cap_frontfix_{uuid.uuid4().hex[:8]}"
        # 审计三元组：metadata_sha256 = fresh 数据内容的 64 位 hex
        daily_csv = fresh_daily[
            ["time"] + RAW_COLS + FRONT_COLS].to_csv(index=False)
        minute_csv = fresh_minutes[
            ["time"] + RAW_COLS + FRONT_COLS].to_csv(index=False)
        metadata_sha = hashlib.sha256(
            f"{hashlib.sha256(daily_csv.encode()).hexdigest()}|"
            f"{hashlib.sha256(minute_csv.encode()).hexdigest()}|"
            f"internal_self_consistent|STOCK|{TARGET}".encode()).hexdigest()
        logger.info(f"调用 apply_reanchor_for_security (fresh_staged), event={event_id}")
        # list_date_ms = daily 数据首日（staging 库数据起点；豁免首日 prev 要求）
        list_date_ms = int(fresh_daily["time"].min())
        try:
            res = apply_reanchor_for_security(
                conn, asset_type="STOCK", code=TARGET,
                fresh_daily=fresh_daily, calendar=cal,
                freqs=("1min",),
                ex_dates_ms=tuple(p["time"] for p in pollution),
                list_date_ms=list_date_ms,
                model="fresh_staged",
                model_reason="front_fix_verification: 合成自洽fresh(K比例)验证修正语义",
                fresh_minutes=fresh_minutes,
                fresh_source="internal_self_consistent",
                fresh_capture_id=capture_id,
                fresh_metadata_sha256=metadata_sha,
                event_id=event_id,
                trigger_surface="front_fix_verify",
            )
            result["reanchor_status"] = res.status
            result["reanchor_error"] = getattr(res, "error", None)
            logger.info(f"重锚结果：status={res.status}")
        except Exception as e:
            logger.exception(f"重锚异常: {e}")
            result["reanchor_status"] = "exception"
            result["reanchor_error"] = f"{type(e).__name__}: {e}"

        # 6) 重锚后快照
        snap_after = _snapshot(conn, "after_reanchor")
        result["snapshot_after"] = snap_after

        # 7) 守恒 + 修正判定
        verdict = {"raw_conserved": True, "back_conserved": True,
                   "front_fixed": False, "rows_conserved": True}
        for tbl in ("stock_daily", "stock_minutes"):
            o, a = snap_original[tbl], snap_after[tbl]
            verdict["raw_conserved"] = verdict["raw_conserved"] and (o["raw_sha"] == a["raw_sha"])
            verdict["back_conserved"] = verdict["back_conserved"] and (o["back_sha"] == a["back_sha"])
            verdict["rows_conserved"] = verdict["rows_conserved"] and (o["rows"] == a["rows"])
        # front 修正：daily front 应回到 D 基线（与 snap_original 一致）
        verdict["front_fixed"] = (snap_original["stock_daily"]["front_sha"]
                                  == snap_after["stock_daily"]["front_sha"])
        result["verdict"] = verdict
        logger.info(f"判定：raw={verdict['raw_conserved']} back={verdict['back_conserved']} "
                    f"rows={verdict['rows_conserved']} front_fixed={verdict['front_fixed']}")
    finally:
        conn.close()

    # 写报告
    result["finished_at"] = _now_iso()
    (OUTPUT_DIR / "front_fix_verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = ["# QFQ front 修正语义验证报告\n",
             f"时间：{result['started_at']}\n目标：{TARGET}\n",
             f"## 重锚结果\n- status={result['reanchor_status']}",
             f"\n\n## 守恒判定\n",
             f"- raw OHLC 守恒：{'✓' if verdict['raw_conserved'] else '❌'}",
             f"- *_back 守恒：{'✓' if verdict['back_conserved'] else '❌'}",
             f"- 行数守恒：{'✓' if verdict['rows_conserved'] else '❌'}",
             f"- front 修正回真值：{'✓' if verdict['front_fixed'] else '❌'}\n",
             f"\n## 结论\n"]
    all_ok = (result["reanchor_status"] == "committed" and verdict["raw_conserved"]
              and verdict["back_conserved"] and verdict["front_fixed"])
    if all_ok:
        lines.append("✅ **front 修正语义验证通过**：fresh_staged 重锚 committed，"
                     "raw/back 守恒，污染 front 被修正回真值。")
    else:
        lines.append(f"⚠️ 未完全通过（status={result['reanchor_status']}），见判定项。"
                     f" 错误：{result.get('reanchor_error')}")
    (OUTPUT_DIR / "front_fix_report.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"报告：{OUTPUT_DIR / 'front_fix_report.md'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
