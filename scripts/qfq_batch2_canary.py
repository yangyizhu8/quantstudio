#!/usr/bin/env python
"""QFQ 批次2 canary —— 真实重锚验证（仅 staging，不写正式库）。

流程：
1. 在 staging 主库插入 qfq_bootstrap_run + qfq_bootstrap_item（status='pending'，canary ETF）。
2. 调用生产路径 bootstrap_run（真实 xtquant 取数 + 真实门控 _check_minute_cov_raw）。
3. 核验：
   - TICK_TOLERANCE ETF 全部 committed（eps=1e-3 生效）
   - canary 全部 committed
   - 守恒：etf_minutes front 不变（ratio=1.0，无段重写）
   - 正式库未污染（文件大小/路径不变）

用法：
    python scripts/qfq_batch2_canary.py [--skip-build]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio._paths import db_path  # noqa: E402
from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher  # noqa: E402
from quantstudio.pipeline.qfq_orchestrator_cli import _load_cfg  # noqa: E402
from quantstudio.pipeline.qfq_resident_orchestrator import (  # noqa: E402
    QFQResidentOrchestrator,
)

BJ_TZ = timezone(timedelta(hours=8))
RUN_ID = "batch2_canary_" + datetime.now(BJ_TZ).strftime("%Y%m%d_%H%M%S")

CANARY_TICK = ["159205", "159215", "159218", "588200", "159740"]
CANARY_NORMAL = ["510300", "159919", "510500", "512100"]
# 精度探针：已知有 1 行 |Δ|>0.001 的真实差异（非 tick 噪声），预期被门控正确拦截，
# 用于证明 eps=1e-3 是“精准放宽”——放行 tick 噪声、仍拦截真实 >1tick 差异。
CANARY_PROBE = ["159915"]
CANARY_ALL = CANARY_TICK + CANARY_NORMAL + CANARY_PROBE


def now_ms() -> int:
    return int(_time.time() * 1000)


def staging_dir() -> Path:
    return ROOT / "data" / "staging_batch2_20260730"


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def build() -> None:
    import subprocess
    print("[phase] 构建 staging ...")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "qfq_batch2_build_staging.py")],
                       check=True)
    _ = r


def checksum_etf_minutes(staging_main: Path) -> dict:
    import duckdb
    conn = duckdb.connect(str(staging_main), read_only=True)
    out = {}
    for code in CANARY_ALL:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(close_front),0), COALESCE(SUM(high_front),0) "
            "FROM etf_minutes WHERE code=?", [code]).fetchone()
        out[code] = {"rows": row[0], "close_sum": float(row[1]), "high_sum": float(row[2])}
    conn.close()
    return out


def _conserved(before: dict, after: dict, rel_tol: float = 1e-6) -> bool:
    """守恒判定：允许浮点 ULP 级误差（reanchor 写回 front*1.0 产生的 ~1e-12 噪声）。"""
    for code in before:
        b, a = before[code], after[code]
        if b["rows"] != a["rows"]:
            return False
        for k in ("close_sum", "high_sum"):
            denom = max(1.0, abs(b[k]))
            if abs(b[k] - a[k]) / denom > rel_tol:
                return False
    return True


def insert_bootstrap(conn, run_id: str) -> None:
    now = _now_ts()
    conn.execute(
        "INSERT INTO qfq_bootstrap_run "
        "(bootstrap_run_id, asset_type, total_count, completed_count, blocked_count, "
        " failed_count, status, schema_version, config_hash, baseline_version, "
        " started_at, updated_at) VALUES (?, 'ETF', ?, 0, 0, 0, 'planned', '0', '0', '0', ?, ?)",
        [run_id, len(CANARY_ALL), now, now])
    for code in CANARY_ALL:
        conn.execute(
            "INSERT INTO qfq_bootstrap_item "
            "(bootstrap_run_id, asset_type, code, status, attempt_count, updated_at) "
            "VALUES (?, 'ETF', ?, 'pending', 0, ?)",
            [run_id, code, now])


def run_canary() -> dict:
    d = staging_dir()
    staging_main = d / "quantstudio.db"
    staging_aux = d / "qfq_aux.db"
    formal_main = db_path("quantstudio.db")
    formal_aux = db_path("qfq_aux.db")
    formal_main_size_before = os.path.getsize(formal_main)
    formal_aux_size_before = os.path.getsize(formal_aux)

    # 守恒快照（reanchor 前）
    cs_before = checksum_etf_minutes(staging_main)

    import duckdb
    conn = duckdb.connect(str(staging_main))
    insert_bootstrap(conn, RUN_ID)
    conn.close()

    cfg = _load_cfg(__import__("argparse").Namespace(config_dir=None, override=[]))
    from quantstudio.pipeline.qfq_calendar import CalendarService
    orch = QFQResidentOrchestrator(
        cfg, main_db=str(staging_main), aux_db=str(staging_aux),
        calendar=CalendarService(main_db=str(staging_main)))
    # init_schema（保证 QFQ 表存在）
    c2 = duckdb.connect(str(staging_main))
    orch.init_schema(c2)
    c2.close()

    # 真实取数连接
    fetcher = XtquantFreshFetcher()
    xt = fetcher._ensure()
    fetcher._ensure_connected(xt)

    conn = duckdb.connect(str(staging_main))
    print(f"[phase] bootstrap_run run_id={RUN_ID} ...")
    result = orch.bootstrap_run(conn, run_id=RUN_ID, as_of_ms=now_ms(), fetcher=fetcher)
    conn.close()
    print(f"[phase] bootstrap_run 结果: {result}")

    # 结果核验
    conn = duckdb.connect(str(staging_main), read_only=True)
    items = conn.execute(
        "SELECT code, status, block_reason, last_error FROM qfq_bootstrap_item "
        "WHERE bootstrap_run_id=?", [RUN_ID]).fetchall()
    conn.close()
    status_map = {code: (st, br, le) for code, st, br, le in items}

    cs_after = checksum_etf_minutes(staging_main)

    formal_main_size_after = os.path.getsize(formal_main)
    formal_aux_size_after = os.path.getsize(formal_aux)

    report = {
        "run_id": RUN_ID,
        "bootstrap_result": result,
        "status_map": status_map,
        "conservation": {"before": cs_before, "after": cs_after,
                         "unchanged": _conserved(cs_before, cs_after)},
        "formal_untouched": {
            "main_before": formal_main_size_before, "main_after": formal_main_size_after,
            "aux_before": formal_aux_size_before, "aux_after": formal_aux_size_after,
            "ok": (formal_main_size_before == formal_main_size_after
                   and formal_aux_size_before == formal_aux_size_after),
        },
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()
    if not args.skip_build:
        build()
    else:
        print("[phase] 跳过构建（复用现有 staging）")

    report = run_canary()

    print("\n==================== 批次2 canary 报告 ====================")
    print(f"run_id: {report['run_id']}")
    print(f"bootstrap 结果: completed={report['bootstrap_result']['completed']} "
          f"blocked={report['bootstrap_result']['blocked']} "
          f"failed={report['bootstrap_result']['failed']}")
    print("\n--- 各 canary 证券状态 ---")
    for code in CANARY_TICK + CANARY_NORMAL:
        st, br, le = report["status_map"].get(code, ("MISSING", None, None))
        tag = "TICK_TOL" if code in CANARY_TICK else "normal "
        print(f"  [{tag}] {code}: {st}"
              + (f"  reason={br}" if br else "")
              + (f"  err={le}" if le else ""))
    for code in CANARY_PROBE:
        st, br, le = report["status_map"].get(code, ("MISSING", None, None))
        print(f"  [PROBE ] {code}: {st}  (预期 blocked={br})")
    print(f"\n--- TICK_TOLERANCE 全部 committed（eps=1e-3 生效）? --- "
          f"{all(report['status_map'][c][0]=='completed' for c in CANARY_TICK)}")
    print(f"--- 主 canary（TICK+normal）全部 committed? --- "
          f"{all(report['status_map'][c][0]=='completed' for c in CANARY_TICK + CANARY_NORMAL)}")
    print(f"--- 精度探针 159915 是否被门控拦截（证明 eps 精准不放宽）? --- "
          f"{report['status_map'].get(CANARY_PROBE[0], ('?',))[0]=='blocked'}")
    print(f"\n--- 守恒(etf_minutes front 不变)? --- {report['conservation']['unchanged']}")
    print(f"--- 正式库未污染? --- {report['formal_untouched']['ok']}")
    (staging_dir() / "batch2_canary_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n报告已写入: {staging_dir()/'batch2_canary_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
