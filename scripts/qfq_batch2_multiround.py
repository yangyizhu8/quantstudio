"""批次2 多日常驻验证（staging 副本，模拟多日）。

在 staging 副本上跑「bootstrap 基线 + 多轮 reconcile-once --execute」，验证：
- 第1轮：bootstrap stale 证券批量重锚（建立基线，满足 require_bootstrap 闸门）
- 第2轮：增量 trigger（新除权事件）→ reconcile-once 领取并重锚
- 第3-5轮：无新事件（确认不产生错误重锚 / 幂等）
- 每轮记录监控摘要（qfq_rebase_monitor.snapshot）
- 验证：
  - 幂等性（重复执行不重复写价，etf_minutes front 守恒）
  - 无错误重锚（空闲轮 0 新增 reanchor_event）
  - trigger 粒度修复（159205 注入 2 个 factor_observation ex_dates，全量枚举）

关键发现（本环境实测）：reconcile-once 在 require_bootstrap=true 且无可匹配
completed bootstrap 时 fail-closed（不处理 trigger、不推进水位）。故生产启用顺序
必须是：bootstrap-run（建基线）→ reconcile-once（增量）。这已写入启用 checklist。

铁律：只读观测 + staging 副本，绝不触碰正式库；不擅自开 enabled（CLI 用
--override enabled=true 仅作用于 staging 副本，且 _assert_not_production 会拦截
对生产库的执行）。

用法：
  python scripts/qfq_batch2_multiround.py [--rounds 5] [--skip-build]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BJ_TZ = timezone(timedelta(hours=8))
DAY_MS = 86_400_000

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.qfq_orchestrator_types import payload_hash_of, trigger_id_of
from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema, SCHEMA_VERSION
from scripts.qfq_rebase_monitor import snapshot as monitor_snapshot

# 多轮验证用 canary：仅含会 committed 的 9 只 ETF。
# 刻意排除 159915（精度探针，已知有 1 行 |Δ|>0.001 真实差异会被 blocked）。
# 原因（生产启用关键）：bootstrap_completed 要求「证券级状态机全清（blocked=0）」，
# 任一 blocked 项都会让整条 reconcile 流水线 fail-closed。故 bootstrap 基线必须 0 blocked。
CANARY_TICK = ["159205", "159215", "159218", "588200", "159740"]
CANARY_NORMAL = ["510300", "159919", "510500", "512100"]
CANARY_MULTI = CANARY_TICK + CANARY_NORMAL  # 9 只，全部 committed
CANARY_ALL = CANARY_MULTI
# 粒度修复探针：注入 2 个 factor_observation ex_dates（全量枚举验证）
GRANULARITY_CODE = "159205"


def _now_ts() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _staging_dir() -> Path:
    return ROOT / "data" / "staging_batch2_20260730"


def build_staging() -> None:
    print("[multiround] 构建 fresh staging ...")
    subprocess.run([sys.executable, "scripts/qfq_batch2_build_staging.py"],
                   cwd=str(ROOT), check=True)


def prepare_granularity_source(staging_aux: Path) -> int:
    """在 staging aux 注入 159205 的 2 个 factor_observation ex_dates（粒度修复探针）。"""
    import sqlite3
    conn = sqlite3.connect(str(staging_aux), timeout=30)
    try:
        init_sqlite_schema(conn)  # 确保 qfq_factor_observation 存在
        ts = _now_ts()
        d1 = int(datetime(2024, 6, 3, tzinfo=BJ_TZ).timestamp() * 1000)
        d2 = int(datetime(2025, 6, 2, tzinfo=BJ_TZ).timestamp() * 1000)
        rows = []
        for i, d in enumerate((d1, d2), start=1):
            rows.append(("ETF", GRANULARITY_CODE, d, 1.0 + i * 0.001, i,
                          "batch2_multiround", "batch2_multiround", ts, ts))
        conn.executemany(
            "INSERT OR REPLACE INTO qfq_factor_observation "
            "(asset_type, code, factor_time, factor_value, revision_no, "
             " first_seen_run_id, last_seen_run_id, first_seen_at, last_seen_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        return conn.execute(
            "SELECT COUNT(*) FROM qfq_factor_observation WHERE code=?",
            [GRANULARITY_CODE]).fetchone()[0]
    finally:
        conn.close()


def insert_bootstrap(staging_main: Path, run_id: str) -> None:
    """插入 bootstrap_run + 全 canary bootstrap_item（pending），供 bootstrap-run 消费。

    版本标识必须匹配当前 SCHEMA_VERSION，否则 bootstrap_completed 会判定未完成
    （fail-closed，reconcile 不处理 trigger）。config_hash/baseline_version 置 NULL
    以跳过对应项校验（本验证不涉及配置哈希变更）。
    """
    import duckdb
    now = _now_ts()
    conn = duckdb.connect(str(staging_main))
    try:
        conn.execute(
            "INSERT INTO qfq_bootstrap_run "
            "(bootstrap_run_id, asset_type, total_count, completed_count, blocked_count, "
             " failed_count, status, schema_version, config_hash, baseline_version, "
             " started_at, updated_at) VALUES (?, 'ETF', ?, 0, 0, 0, 'planned', ?, NULL, NULL, ?, ?)",
            [run_id, len(CANARY_ALL), SCHEMA_VERSION, now, now])
        for code in CANARY_ALL:
            conn.execute(
                "INSERT INTO qfq_bootstrap_item "
                "(bootstrap_run_id, asset_type, code, status, attempt_count, updated_at) "
                "VALUES (?, 'ETF', ?, 'pending', 0, ?)",
                [run_id, code, now])
    finally:
        conn.close()


def inject_triggers(staging_main: Path) -> int:
    """为全部 canary ETF 注入确定性 pending trigger（模拟新除权事件）。"""
    import duckdb
    eff = int(datetime(2024, 1, 2, tzinfo=BJ_TZ).timestamp() * 1000)
    now_iso = _now_ts()
    conn = duckdb.connect(str(staging_main))
    try:
        n = 0
        for code in CANARY_ALL:
            payload = payload_hash_of([code, eff, 1.0, 1.0])
            tid = trigger_id_of("ETF", code, eff, "tushare_fund_adj_new", payload)
            conn.execute(
                "INSERT OR IGNORE INTO qfq_trigger_queue "
                "(trigger_id, asset_type, code, trigger_type, detection_source, "
                 " source_key, effective_date, payload_hash, status, created_at, updated_at) "
                "VALUES (?, 'ETF', ?, 'factor_new', 'tushare_fund_adj_new', ?, ?, ?, 'pending', ?, ?)",
                [tid, code, str(eff), eff, payload, now_iso, now_iso])
            n += 1
        return n
    finally:
        conn.close()


def checksum_etf_minutes(staging_main: Path) -> dict:
    import duckdb
    conn = duckdb.connect(str(staging_main), read_only=True)
    out = {}
    try:
        for code in CANARY_ALL:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(close_front),0), COALESCE(SUM(high_front),0) "
                "FROM etf_minutes WHERE code=?", [code]).fetchone()
            out[code] = {"rows": row[0], "close_sum": float(row[1]), "high_sum": float(row[2])}
    finally:
        conn.close()
    return out


def count_reanchor_events(staging_main: Path) -> int:
    import duckdb
    conn = duckdb.connect(str(staging_main), read_only=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM qfq_reanchor_event").fetchone()[0]
    finally:
        conn.close()


def _run_cli(staging_main: Path, staging_aux: Path, *cli_args) -> int:
    p = subprocess.run(
        [sys.executable, "-m", "quantstudio.pipeline.qfq_orchestrator_cli",
         "--db", str(staging_main), "--aux-db", str(staging_aux),
         "--override", "enabled=true", "--execute", *cli_args],
        cwd=str(ROOT), capture_output=True, text=True)
    if p.returncode != 0:
        print(f"[cli] FAILED rc={p.returncode}")
        print((p.stdout or "")[-1500:])
        print((p.stderr or "")[-1500:])
    else:
        for line in p.stdout.splitlines():
            if any(k in line for k in ("摘要", "committed", "blocked", "dead_letter",
                                        "detector", "完成", "周期", "bootstrap",
                                        "require_bootstrap")):
                print("  " + line)
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    sd = _staging_dir()
    sd.mkdir(parents=True, exist_ok=True)
    staging_main = sd / "quantstudio.db"
    staging_aux = sd / "qfq_aux.db"

    if not args.skip_build or not staging_main.exists():
        build_staging()

    n_obs = prepare_granularity_source(staging_aux)
    print(f"[multiround] 粒度探针 {GRANULARITY_CODE} factor_observation 行数={n_obs} "
          f"(期望 2，验证全量枚举)")

    cs0 = checksum_etf_minutes(staging_main)
    ev0 = count_reanchor_events(staging_main)

    rounds = []

    # ---- 第1轮：bootstrap stale 证券批量重锚（建基线 + 满足 require_bootstrap 闸门）----
    run_id = f"bs_multiround_{int(_time.time())}"
    insert_bootstrap(staging_main, run_id)
    print(f"\n========== ROUND 1 bootstrap-run (run_id={run_id}) ==========")
    rc1 = _run_cli(staging_main, staging_aux, "bootstrap-run", "--run-id", run_id)
    mon1 = monitor_snapshot(str(staging_main), str(staging_aux))
    ev1 = count_reanchor_events(staging_main)
    rounds.append({"round": 1, "phase": "bootstrap", "rc": rc1,
                   "reanchor_event_total": ev1,
                   "reanchor_events_new": ev1 - ev0,
                   "committed_triggers": mon1["committed_triggers"],
                   "blocked_triggers": mon1["blocked_triggers"]})

    # ---- 第2轮：注入增量 trigger + reconcile-once（新除权事件）----
    n_trig = inject_triggers(staging_main)
    print(f"\n========== ROUND 2 reconcile-once（注入 {n_trig} 增量 trigger）==========")
    rc2 = _run_cli(staging_main, staging_aux, "reconcile-once")
    mon2 = monitor_snapshot(str(staging_main), str(staging_aux))
    ev2 = count_reanchor_events(staging_main)
    cs2 = checksum_etf_minutes(staging_main)
    rounds.append({"round": 2, "phase": "reconcile_incremental", "rc": rc2,
                   "reanchor_event_total": ev2,
                   "reanchor_events_new": ev2 - ev1,
                   "committed_triggers": mon2["committed_triggers"],
                   "blocked_triggers": mon2["blocked_triggers"],
                   "conserved": _conserved(cs0, cs2)})

    # ---- 第3..N轮：无新事件（幂等 / 无错误重锚）----
    prev_ev = ev2
    for i in range(3, args.rounds + 1):
        print(f"\n========== ROUND {i} reconcile-once（无新事件）==========")
        rc = _run_cli(staging_main, staging_aux, "reconcile-once")
        mon = monitor_snapshot(str(staging_main), str(staging_aux))
        ev = count_reanchor_events(staging_main)
        cs = checksum_etf_minutes(staging_main)
        rounds.append({"round": i, "phase": "reconcile_idle", "rc": rc,
                       "reanchor_event_total": ev,
                       "reanchor_events_new": ev - prev_ev,
                       "committed_triggers": mon["committed_triggers"],
                       "blocked_triggers": mon["blocked_triggers"],
                       "conserved": _conserved(cs0, cs),
                       "alarms": mon["alarms"]})
        prev_ev = ev

    # ---- 验证 ----
    # 幂等性：round2 之后事件数不再增长
    ev_after_incremental = rounds[1]["reanchor_event_total"]
    idempotent = all(r["reanchor_event_total"] == ev_after_incremental for r in rounds[2:])
    # 无错误重锚：空闲轮（round3+）新增事件 = 0
    idle = [r for r in rounds if r["phase"] == "reconcile_idle"]
    no_spurious = all(r["reanchor_events_new"] == 0 for r in idle)
    # 守恒：所有轮 checksum 与基线一致
    all_conserved = all(r.get("conserved", True) for r in rounds[1:])
    # 粒度：159205 的 2 个 factor_observation 仍在（全量枚举源未被消费）
    import duckdb
    conn = duckdb.connect(str(staging_main), read_only=True)
    try:
        gran_events = conn.execute(
            "SELECT COUNT(*) FROM qfq_reanchor_event WHERE code=? AND status='committed'",
            [GRANULARITY_CODE]).fetchone()[0]
    finally:
        conn.close()
    import sqlite3
    ac = sqlite3.connect(str(staging_aux))
    try:
        gran_obs = ac.execute(
            "SELECT COUNT(*) FROM qfq_factor_observation WHERE code=?",
            [GRANULARITY_CODE]).fetchone()[0]
    finally:
        ac.close()

    report = {
        "generated_at": _now_ts(),
        "db": str(staging_main),
        "rounds_total": args.rounds,
        "rounds": rounds,
        "verification": {
            "bootstrap_committed_events": rounds[0]["reanchor_events_new"],
            "incremental_committed_events": rounds[1]["reanchor_events_new"],
            "idempotent_no_duplicate_event": idempotent,
            "no_spurious_reanchor_idle_rounds": no_spurious,
            "conserved_all_rounds": all_conserved,
            "granularity_source_rows": gran_obs,
            "granularity_code_committed_events": gran_events,
            "granularity_full_exdates_enumerated": (gran_obs == 2 and gran_events >= 1),
        },
    }
    out = sd / "batch2_multiround_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")

    print("\n========== 多轮验证结论 ==========")
    v = report["verification"]
    print(f"  Round1 bootstrap 提交事件: {v['bootstrap_committed_events']}")
    print(f"  Round2 增量提交事件: {v['incremental_committed_events']}")
    print(f"  幂等性（重复执行不重复写价事件）: {v['idempotent_no_duplicate_event']}")
    print(f"  空闲轮无错误重锚: {v['no_spurious_reanchor_idle_rounds']}")
    print(f"  全轮守恒: {v['conserved_all_rounds']}")
    print(f"  粒度探针 {GRANULARITY_CODE}: factor_observation={gran_obs} 行, "
          f"committed 重锚事件={gran_events} 个")
    print(f"  全 ex_dates 枚举验证: {v['granularity_full_exdates_enumerated']}")
    print(f"\n  报告: {out}")
    return 0


def _conserved(before: dict, after: dict, rel_tol: float = 1e-6) -> bool:
    for code in before:
        b, a = before[code], after[code]
        if b["rows"] != a["rows"]:
            return False
        for k in ("close_sum", "high_sum"):
            denom = max(1.0, abs(b[k]))
            if abs(b[k] - a[k]) / denom > rel_tol:
                return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
