"""G1：A1 分钟 front 锚点漂移巡检 + A2 因子非单调告警测试。

方案：docs/mcp-minute-front-anchor-design.md §4 阶段1（R6 阈值：WARN>0.3%、FAIL>0.5%）
判别公式（实测 V2）：front = raw × adj_i/adj_latest；dev = |(close_front/close)/(adj_i/adj_latest) - 1|
"""
import datetime
import duckdb
import pytest

from quantstudio.pipeline.quality_audit import DataQualityAuditor
from quantstudio.pipeline.writers import DDL_DUCKDB

TZ = datetime.timezone(datetime.timedelta(hours=8))


def ms(y, m, d, hh=15, mm=0):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp() * 1000)


def minute_row(code, t, close, close_front):
    return {"code": code, "time": t, "freq": "1min", "open": close, "high": close,
            "low": close, "close": close, "open_front": close_front,
            "high_front": close_front, "low_front": close_front,
            "close_front": close_front, "data_source": "mcp"}


def build_aux(path, factor_rows):
    """factor_rows: list of (table, code, time, adj_factor)."""
    import sqlite3
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS fund_adj "
                    "(code TEXT, time INTEGER, adj_factor REAL)")
        con.execute("CREATE TABLE IF NOT EXISTS adj_factor "
                    "(code TEXT, time INTEGER, adj_factor REAL)")
        for tbl, code, t, v in factor_rows:
            con.execute(f"INSERT INTO {tbl} (code, time, adj_factor) VALUES (?,?,?)",
                        (code, int(t), float(v)))
        con.commit()
    finally:
        con.close()


def make_auditor(db_path, aux_path=None, aux_config=None, identity=None):
    return DataQualityAuditor(
        db_path, {"etf_minutes": {}}, qfq_aux_override=aux_path,
        qfq_aux_paths_config=aux_config, qfq_identity=identity)


def add_etf_dividend(db_path, rows):
    """rows: list of (code, ex_date_ms)."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute(DDL_DUCKDB["etf_dividend"])
        for code, ex in rows:
            con.execute("INSERT INTO etf_dividend (code, ex_date) VALUES (?, ?)", (code, int(ex)))
    finally:
        con.close()


# ---------------------------------------------------------------------------
# A1：漂移检出
# ---------------------------------------------------------------------------
def test_a1_detects_drift_fail(build_db, tmp_path):
    """因子变化点后（旧锚 bar 未重锚）→ dev 显著 >0.5% → AdjustmentAnchorDrift error。"""
    # 因子：06-01=1.0 → 06-10=1.02 → 07-20=1.03（最新锚 1.03）
    # bar：06-05 15:00（06-10 变化点前）front 仍 = raw（旧锚写入，未随 07-20 重锚）
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "999999", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "999999", ms(2026, 6, 10, 0), 1.02),
                    ("fund_adj", "999999", ms(2026, 7, 20, 0), 1.03)])
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 10.0)],
                  etf_daily=[])
    add_etf_dividend(db, [("999999", ms(2026, 7, 20, 0))])
    report = make_auditor(db, aux_path=aux).run()
    drift = [i for i in report.issues if i.check == "AdjustmentAnchorDrift"]
    assert any(i.severity == "error" and i.count >= 1 for i in drift), [
        (i.check, i.severity, i.count, i.detail) for i in report.issues]


def test_a1_detects_drift_warn(build_db, tmp_path):
    """dev ∈ (0.3%, 0.5%] → warning 级。"""
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "999999", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "999999", ms(2026, 6, 10, 0), 1.02),
                    ("fund_adj", "999999", ms(2026, 7, 20, 0), 1.03)])
    # expect = 1.0/1.03 = 0.970874；actual 需 dev≈0.4% → close_front ≈ 10×0.970874×1.004 ≈ 9.749
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 9.749)],
                  etf_daily=[])
    add_etf_dividend(db, [("999999", ms(2026, 7, 20, 0))])
    report = make_auditor(db, aux_path=aux).run()
    drift = [i for i in report.issues if i.check == "AdjustmentAnchorDrift"]
    assert any(i.severity == "warning" and i.count >= 1 for i in drift), [
        (i.check, i.severity, i.count) for i in report.issues]


def test_a1_clean_no_issue(build_db, tmp_path):
    """front 与因子一致（front = raw × adj_i/adj_latest）→ 无漂移 issue。"""
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "999999", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "999999", ms(2026, 6, 10, 0), 1.02)])
    # 正确 front = 10 × 1.0/1.02 = 9.8039
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 9.8039)],
                  etf_daily=[])
    add_etf_dividend(db, [("999999", ms(2026, 6, 10, 0))])
    report = make_auditor(db, aux_path=aux).run()
    assert not [i for i in report.issues if i.check == "AdjustmentAnchorDrift"], report.issues


def test_a1_no_candidates_skips(build_db, tmp_path):
    """除权表为空 → A1 静默跳过（不崩、无 issue）。"""
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "999999", ms(2026, 6, 1, 0), 1.0)])
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 10.0)],
                  etf_daily=[])
    add_etf_dividend(db, [])
    report = make_auditor(db, aux_path=aux).run()
    assert not [i for i in report.issues if i.check == "AdjustmentAnchorDrift"], report.issues


def test_a1_aux_unavailable_warns(build_db, tmp_path):
    """因子库不可用（override 指向不存在文件）→ AnchorDriftAuxUnavailable warning。"""
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 10.0)],
                  etf_daily=[])
    add_etf_dividend(db, [("999999", ms(2026, 7, 20, 0))])
    report = make_auditor(db, aux_path=tmp_path / "missing_aux.db").run()
    assert any(i.check == "AnchorDriftAuxUnavailable" and i.severity == "warning"
               for i in report.issues)


def test_a1_follows_runtime_route(build_db, tmp_path):
    """无 override 时跟随路由：qfq_aux_paths.json released=false → fail-secure legacy
    （main_db 同目录 qfq_aux.db），因子来自 legacy 库（ZCode 执行注记 2）。"""
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "999999", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "999999", ms(2026, 6, 10, 0), 1.02),
                    ("fund_adj", "999999", ms(2026, 7, 20, 0), 1.03)])
    cfg = tmp_path / "qfq_aux_paths.json"
    cfg.write_text('{"released": false, "default": "qfq_aux.db"}', encoding="utf-8")
    db = build_db(etf_minutes=[minute_row("999999", ms(2026, 6, 5), 10.0, 10.0)],
                  etf_daily=[])
    add_etf_dividend(db, [("999999", ms(2026, 7, 20, 0))])
    # 无 override → 路由解析（released=false → legacy = main_db 同目录 qfq_aux.db）
    report = make_auditor(db, aux_path=None, aux_config=cfg).run()
    drift = [i for i in report.issues if i.check == "AdjustmentAnchorDrift"]
    assert any(i.severity == "error" for i in drift), [
        (i.check, i.severity, i.detail) for i in report.issues]


# ---------------------------------------------------------------------------
# A2：因子非单调告警
# ---------------------------------------------------------------------------
def test_a2_detects_non_monotonic(build_db, tmp_path):
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "AAA", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "AAA", ms(2026, 6, 10, 0), 1.02),
                    ("fund_adj", "AAA", ms(2026, 6, 20, 0), 1.01),   # 回落
                    ("fund_adj", "BBB", ms(2026, 6, 1, 0), 2.0),
                    ("fund_adj", "BBB", ms(2026, 6, 10, 0), 2.05)])  # 单调
    db = build_db(etf_minutes=[], etf_daily=[])
    report = make_auditor(db, aux_path=aux).run()
    mono = [i for i in report.issues if i.check == "FactorMonotonicity"]
    assert len(mono) == 1 and mono[0].severity == "warning" and mono[0].count == 1, mono


def test_a2_monotonic_no_issue(build_db, tmp_path):
    aux = tmp_path / "qfq_aux.db"
    build_aux(aux, [("fund_adj", "AAA", ms(2026, 6, 1, 0), 1.0),
                    ("fund_adj", "AAA", ms(2026, 6, 10, 0), 1.02),
                    ("fund_adj", "AAA", ms(2026, 6, 20, 0), 1.03)])
    db = build_db(etf_minutes=[], etf_daily=[])
    report = make_auditor(db, aux_path=aux).run()
    assert not [i for i in report.issues if i.check == "FactorMonotonicity"], report.issues


def test_a2_missing_tables_ok(build_db, tmp_path):
    """aux 无因子表（只建了一个）→ 静默跳过。"""
    import sqlite3
    aux = tmp_path / "qfq_aux.db"
    con = sqlite3.connect(str(aux))
    con.execute("CREATE TABLE fund_adj (code TEXT, time INTEGER, adj_factor REAL)")
    con.commit()
    con.close()
    db = build_db(etf_minutes=[], etf_daily=[])
    report = make_auditor(db, aux_path=aux).run()
    assert not [i for i in report.issues if i.check == "FactorMonotonicity"], report.issues
