"""qfq_orchestrator_cli 运维 CLI 测试（in-process 调 main()，不依赖 xtquant / 不写正式库）。

覆盖安全闸门与子命令语义：
- 变更命令缺 --db / 缺 --execute（dry-run 不落库）；
- 正式库保护：目标 == _paths.db_path() 时须 --allow-production；
- enabled=false 时 reconcile-once / bootstrap-run 拒绝执行（紧急回退开关语义）；
- status / show-pending / show-dead-letter 只读可用；
- retry-due 三类恢复；reopen dead_letter→pending；
- bootstrap-plan → bootstrap-audit 闭环；
- reconcile-once --execute e2e（fake 引擎 + fake fetcher 注入）。
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from quantstudio.pipeline import qfq_orchestrator_cli as cli
from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
from quantstudio.pipeline.qfq_fresh_capture import FakeFreshFetcher

BJ_TZ = timezone(timedelta(hours=8))


def _ms(s: str) -> int:
    fmt = "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d"
    return int(datetime.strptime(s, fmt).replace(tzinfo=BJ_TZ).timestamp() * 1000)


EX_PAST_MS = _ms("2026-07-10")


def test_parse_codes_filter_accepts_csv_and_json(tmp_path):
    assert cli._parse_codes_filter("600001,159215,600001") == ["159215", "600001"]

    path = tmp_path / "canary.json"
    path.write_text(
        json.dumps({"codes": ["600002", "159218", "600002"]}),
        encoding="utf-8",
    )
    assert cli._parse_codes_filter(str(path)) == ["159218", "600002"]


def test_parse_codes_filter_rejects_empty_or_invalid(tmp_path):
    with pytest.raises(SystemExit, match="不能为空"):
        cli._parse_codes_filter("")
    with pytest.raises(SystemExit, match="6 位裸码"):
        cli._parse_codes_filter("600001.SH")

    path = tmp_path / "canary.json"
    path.write_text(json.dumps({"securities": ["600001"]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="codes 数组"):
        cli._parse_codes_filter(str(path))


def test_parse_admissible_codes_accepts_default_and_validates_count(tmp_path):
    path = tmp_path / "qfq_rebase_admissible_securities.json"
    path.write_text(json.dumps({
        "total_admissible": 2,
        "by_asset": {"STOCK": ["600001"], "ETF": ["159215"]},
    }), encoding="utf-8")
    assert cli._parse_admissible_codes("", config_dir=str(tmp_path)) == [
        ("ETF", "159215"), ("STOCK", "600001")]

    path.write_text(json.dumps({
        "total_admissible": 3,
        "by_asset": {"STOCK": ["600001"], "ETF": ["159215"]},
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="唯一证券数=2"):
        cli._parse_admissible_codes("", config_dir=str(tmp_path))


def test_bootstrap_plan_parser_accepts_admissible_default():
    args = cli.build_parser().parse_args([
        "--db", "staging.db", "bootstrap-plan", "--admissible"])
    assert args.cmd == "bootstrap-plan"
    assert args.admissible == ""


def test_repository_loose_admissible_manifest_matches_evidence():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((
        root / "config/qfq_rebase_admissible_securities.json"
    ).read_text(encoding="utf-8"))
    evidence_dir = (
        root / "docs/evidence/qfq_raw_admission_fullmarket_20260730")
    with (evidence_dir / "admission_summary.csv").open(
            encoding="utf-8-sig", newline="") as handle:
        summary_rows = list(csv.DictReader(handle))
    with (evidence_dir / "block_tick_tolerance_downgraded.csv").open(
            encoding="utf-8-sig", newline="") as handle:
        downgraded_rows = list(csv.DictReader(handle))

    by_asset = config["by_asset"]
    manifest_codes = set(by_asset["STOCK"]) | set(by_asset["ETF"])
    downgraded = {row["code"] for row in downgraded_rows}
    summary_by_code = {}
    for row in summary_rows:
        summary_by_code.setdefault(row["code"], set()).add(row["status"])
    strict = manifest_codes - downgraded

    assert config["total_admissible"] == 5487
    assert config["codes"] == sorted(manifest_codes)
    assert len(manifest_codes) == 5487
    assert len(strict) == 5395
    assert len(downgraded) == 92
    assert all(
        row["verdict"] == "ADMISSIBLE_TICK_TOLERANCE"
        for row in downgraded_rows)
    assert all("ADMISSIBLE" in summary_by_code.get(code, set()) for code in strict)
    assert all(code in summary_by_code for code in downgraded)
    assert manifest_codes == strict | downgraded
    assert config["status_summary"] == {
        "ADMISSIBLE": 5395,
        "ADMISSIBLE_TICK_TOLERANCE": 92,
    }


def test_reconcile_once_parser_accepts_codes():
    args = cli.build_parser().parse_args([
        "--db", "staging.db", "reconcile-once", "--codes", "600001,159215"
    ])
    assert args.cmd == "reconcile-once"
    assert args.codes == "600001,159215"


def test_reconcile_once_scoped_held_returns_success(env, monkeypatch):
    from types import SimpleNamespace

    summary = SimpleNamespace(
        status="finalized_held", error=None,
        gate_report={"scoped_mode": True})
    monkeypatch.setattr(cli, "_make_orchestrator", lambda *args, **kwargs: SimpleNamespace(
        init_schema=lambda conn: None,
        begin_cycle=lambda conn: "cyc_scoped",
        run_post_ingest=lambda *args, **kwargs: summary,
    ))

    rc = cli.main(_base_args(
        env, "--override", "enabled=true", "--execute",
        "reconcile-once", "--codes", "600000"))
    assert rc == 0


def test_reconcile_once_full_held_returns_failure(env, monkeypatch):
    from types import SimpleNamespace

    summary = SimpleNamespace(
        status="finalized_held", error=None,
        gate_report={"scoped_mode": False})
    monkeypatch.setattr(cli, "_make_orchestrator", lambda *args, **kwargs: SimpleNamespace(
        init_schema=lambda conn: None,
        begin_cycle=lambda conn: "cyc_full",
        run_post_ingest=lambda *args, **kwargs: summary,
    ))

    rc = cli.main(_base_args(
        env, "--override", "enabled=true", "--execute", "reconcile-once"))
    assert rc == 1


def _make_ohlc(index_dates, prices):
    idx = pd.to_datetime(index_dates)
    rows = [{"open": o, "high": h, "low": l, "close": c} for (o, h, l, c) in prices]
    return pd.DataFrame(rows, index=idx)


NONE_DAILY = _make_ohlc(["2026-07-08", "2026-07-09", "2026-07-10"],
                        [(10, 11, 9, 10), (10.5, 11.5, 10, 11), (11, 12, 10.5, 11.5)])
FRONT_DAILY = NONE_DAILY * 0.9
NONE_MIN = _make_ohlc(["2026-07-10 09:30:00", "2026-07-10 09:31:00"],
                      [(10, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2)])
FRONT_MIN = NONE_MIN * 0.9


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _seed_db(path: str) -> None:
    """建文件版最小库（与 orchestrator 测试 _new_conn 同构）。"""
    conn = duckdb.connect(path)
    init_duckdb_schema(conn)
    conn.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    conn.execute("CREATE TABLE etf_minutes (code VARCHAR, time BIGINT)")
    # v2.4 B-3a：source_watermark 现由 init_duckdb_schema 建立（8 列含
    # source_generation/cutover_id NOT NULL），此处不再重复 CREATE（已致 Catalog 冲突）。
    conn.execute("""
        CREATE TABLE stock_dividend (
            code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT,
            end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE,
            cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE,
            div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR,
            PRIMARY KEY(code, ex_date))""")
    conn.execute("INSERT INTO stock_daily VALUES ('600000', ?), ('600000', ?)",
                 [_ms("2026-07-08"), _ms("2026-07-10")])
    conn.execute("INSERT INTO stock_minutes VALUES ('600000', ?), ('600000', ?)",
                 [_ms("2026-07-10 09:30:00"), _ms("2026-07-10 09:31:00")])
    conn.close()


def _init_aux(aux_path: str) -> None:
    aconn = sqlite3.connect(aux_path)
    try:
        aconn.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                      "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.execute("CREATE TABLE IF NOT EXISTS fund_adj "
                      "(code TEXT, time INTEGER, adj_factor REAL)")
        aconn.commit()
    finally:
        aconn.close()


@pytest.fixture()
def env(tmp_path):
    db = str(tmp_path / "quantstudio.db")
    aux = str(tmp_path / "qfq_aux.db")
    _seed_db(db)
    _init_aux(aux)
    return {"db": db, "aux": aux}


def _base_args(env, *extra):
    return ["--db", env["db"], "--aux-db", env["aux"], *extra]


def _insert_trigger(db, trigger_id, status, **cols):
    conn = duckdb.connect(db)
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    base = {
        "trigger_id": trigger_id, "asset_type": "STOCK", "code": "600000",
        "trigger_type": "stock_dividend", "detection_source": "tushare_dividend",
        "status": status, "attempt_count": 0,
        # v2.4 B-3a：qfq_trigger_queue 新增 NOT NULL 列（pre-cutover 静态值）。
        "trigger_id_version": 1,
        "price_source": "xtquant",
        "source_generation": "xtquant-legacy",
        "cutover_id": "legacy-xtquant-pre-cutover",
        "created_at": now, "updated_at": now,
    }
    base.update(cols)
    keys = list(base.keys())
    conn.execute(
        f"INSERT INTO qfq_trigger_queue ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' for _ in keys)})", [base[k] for k in keys])
    conn.close()


def _count(db, sql, params=()) -> int:
    conn = duckdb.connect(db, read_only=True)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 安全闸门
# ---------------------------------------------------------------------------

def test_mutating_requires_db(env):
    with pytest.raises(SystemExit):
        cli.main(["reconcile-once", "--execute"])


def test_dry_run_default_no_write(env):
    # 不带 --execute：dry-run 返回 0，且不产生任何 cycle 记录
    rc = cli.main(_base_args(env, "--override", "enabled=true", "reconcile-once"))
    assert rc == 0
    assert _count(env["db"], "SELECT COUNT(*) FROM qfq_cycle_run") == 0


def test_production_guard(env, monkeypatch):
    import quantstudio._paths as paths
    monkeypatch.setattr(paths, "db_path", lambda name="quantstudio.db": env["db"])
    with pytest.raises(SystemExit) as ei:
        cli.main(_base_args(env, "--override", "enabled=true",
                            "--execute", "reconcile-once"))
    assert "正式库" in str(ei.value)


def test_disabled_refuses_reconcile(env):
    # 默认 enabled=false（紧急回退开关）→ 即使 --execute 也拒绝
    with pytest.raises(SystemExit) as ei:
        cli.main(_base_args(env, "--execute", "reconcile-once"))
    assert "enabled=false" in str(ei.value)


def test_disabled_refuses_bootstrap_run(env):
    with pytest.raises(SystemExit):
        cli.main(_base_args(env, "--execute", "bootstrap-run", "--run-id", "bs_x"))


# ---------------------------------------------------------------------------
# 只读命令
# ---------------------------------------------------------------------------

def test_status_readonly(env, capsys):
    rc = cli.main(_base_args(env, "status"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "QFQ orchestrator status" in out
    assert "enabled=False" in out


def test_show_pending_and_dead_letter(env, capsys):
    _insert_trigger(env["db"], "t_dead", "dead_letter",
                    attempt_count=6, last_error="boom",
                    dead_letter_at="2026-07-28 09:00:00")
    rc = cli.main(_base_args(env, "show-dead-letter"))
    assert rc == 0
    assert "t_dead" in capsys.readouterr().out
    rc = cli.main(_base_args(env, "show-pending"))
    assert rc == 0


# ---------------------------------------------------------------------------
# 变更命令
# ---------------------------------------------------------------------------

def test_retry_due_execute(env, capsys):
    _insert_trigger(env["db"], "t_retry", "retryable_failed",
                    next_retry_at="2026-07-01 00:00:00")
    _insert_trigger(env["db"], "t_sched", "scheduled", effective_date=EX_PAST_MS)
    rc = cli.main(_base_args(env, "--execute", "retry-due"))
    assert rc == 0
    assert _count(env["db"], "SELECT COUNT(*) FROM qfq_trigger_queue "
                             "WHERE status='pending'") == 2


def test_reopen_dead_letter(env):
    _insert_trigger(env["db"], "t_dead", "dead_letter", attempt_count=6,
                    dead_letter_at="2026-07-28 09:00:00")
    # dry-run 不改状态
    rc = cli.main(_base_args(env, "reopen", "--trigger-id", "t_dead"))
    assert rc == 0
    assert _count(env["db"], "SELECT COUNT(*) FROM qfq_trigger_queue "
                             "WHERE status='dead_letter'") == 1
    # --execute 重开
    rc = cli.main(_base_args(env, "--execute", "reopen", "--trigger-id", "t_dead"))
    assert rc == 0
    conn = duckdb.connect(env["db"], read_only=True)
    st, att, dla = conn.execute(
        "SELECT status, attempt_count, dead_letter_at FROM qfq_trigger_queue "
        "WHERE trigger_id='t_dead'").fetchone()
    conn.close()
    assert (st, att, dla) == ("pending", 0, None)


def test_reopen_rejects_non_dead_letter(env):
    _insert_trigger(env["db"], "t_pend", "pending")
    with pytest.raises(SystemExit):
        cli.main(_base_args(env, "--execute", "reopen", "--trigger-id", "t_pend"))


def test_bootstrap_plan_and_audit(env, capsys):
    conn = duckdb.connect(env["db"])
    conn.execute("INSERT INTO stock_dividend (code, ex_date, cash_div, stk_div, "
                 "div_rat, div_proc) VALUES ('600000', ?, 0.5, 0, 0, '实施')",
                 [EX_PAST_MS])
    conn.close()
    rc = cli.main(_base_args(env, "--execute", "bootstrap-plan"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "total=1" in out
    # audit：pending 未跑完 → clean=False → rc 1
    rc = cli.main(_base_args(env, "bootstrap-audit"))
    assert rc == 1
    assert "'remaining': 1" in capsys.readouterr().out


def test_bootstrap_plan_admissible_default_excludes_out_of_scope(env, capsys):
    manifest = {
        "total_admissible": 1,
        "by_asset": {"STOCK": ["600000"], "ETF": []},
    }
    config_dir = Path(env["db"]).parent
    (config_dir / "qfq_rebase_admissible_securities.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    conn = duckdb.connect(env["db"])
    conn.execute(
        "INSERT INTO stock_dividend (code, ex_date, cash_div, stk_div, "
        "div_rat, div_proc) VALUES "
        "('600000', ?, 0.5, 0, 0, '实施'), "
        "('600001', ?, 0.5, 0, 0, '实施')",
        [EX_PAST_MS, EX_PAST_MS])
    conn.close()

    rc = cli.main(_base_args(
        env, "--config-dir", str(config_dir), "--execute",
        "bootstrap-plan", "--admissible"))

    assert rc == 0
    assert "pending=1 excluded=1" in capsys.readouterr().out
    conn = duckdb.connect(env["db"], read_only=True)
    rows = conn.execute(
        "SELECT code, status, block_reason FROM qfq_bootstrap_item ORDER BY code"
    ).fetchall()
    conn.close()
    assert rows == [
        ("600000", "pending", None),
        ("600001", "excluded", "NOT_ADMISSIBLE"),
    ]


def test_reconcile_once_e2e(env, monkeypatch, capsys):
    """CLI e2e：fake fetcher + fake 引擎 → committed + 水位路径不炸。"""
    import quantstudio.pipeline.qfq_reanchor_engine as eng
    import quantstudio.pipeline.qfq_fresh_capture as fc
    from types import SimpleNamespace
    from quantstudio.pipeline.qfq_orchestrator_types import event_id_of

    def fake_engine(conn, *, asset_type, code, event_id=None, **kw):
        now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR IGNORE INTO qfq_reanchor_event "
            "(event_id, event_type, asset_type, code, source_generation, "
            "cutover_id, status, trigger_surface, "
            " created_at, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [event_id, "reanchor", asset_type, code,
             "xtquant-legacy", "legacy-xtquant-pre-cutover",
             "committed",
             kw.get("trigger_surface", "resident_v2"), now, now, now])
        return SimpleNamespace(status="committed", event_id=event_id, error=None)

    monkeypatch.setattr(eng, "apply_reanchor_for_security", fake_engine)
    monkeypatch.setattr(
        fc, "XtquantFreshFetcher",
        lambda *a, **kw: FakeFreshFetcher({
            ("600000.SH", "1d"): (NONE_DAILY, FRONT_DAILY),
            ("600000.SH", "1m"): (NONE_MIN, FRONT_MIN)}))

    conn = duckdb.connect(env["db"])
    conn.execute("INSERT INTO stock_dividend (code, ex_date, cash_div, stk_div, "
                 "div_rat, div_proc) VALUES ('600000', ?, 0.5, 0, 0, '实施')",
                 [EX_PAST_MS])
    conn.close()

    rc = cli.main(_base_args(
        env, "--override", "enabled=true", "--override", "require_bootstrap=false",
        "--execute", "reconcile-once"))
    assert rc == 0
    assert _count(env["db"], "SELECT COUNT(*) FROM qfq_trigger_queue "
                             "WHERE status='committed'") == 1
    assert _count(env["db"], "SELECT COUNT(*) FROM qfq_cycle_run "
                             "WHERE status='finalized'") == 1
