import duckdb

from quantstudio.pipeline.quality_audit import DataQualityAuditor


def test_quality_auditor_detects_frequency_and_adjustment_errors(tmp_path):
    db = tmp_path / "q.db"
    with duckdb.connect(str(db)) as c:
        c.execute("CREATE TABLE stock_minutes(code VARCHAR,time BIGINT,freq VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,open_front DOUBLE,high_front DOUBLE,low_front DOUBLE,close_front DOUBLE,open_back DOUBLE,high_back DOUBLE,low_back DOUBLE,close_back DOUBLE,data_source VARCHAR, PRIMARY KEY(code,time,freq))")
        c.execute("INSERT INTO stock_minutes VALUES ('600000',90000,'5min',10,11,9,10,100,1000,5,8,4.5,5,20,NULL,NULL,NULL,'xtquant')")
    columns = {name: {"required": name in {"code", "time", "freq"}} for name in (
        "code","time","freq","open","high","low","close","volume","amount",
        "open_front","high_front","low_front","close_front","open_back","high_back","low_back","close_back")}
    report = DataQualityAuditor(db, {"stock_minutes": {"columns": columns,
        "primary_key": ["code","time","freq"]}}).run()
    checks = {issue.check for issue in report.issues}
    assert "FrequencyGrid" in checks
    assert "AdjustmentFactorConsistency" in checks
    assert "AdjustmentCompleteness" in checks


def test_future_corporate_action_is_warning(tmp_path):
    import time
    db = tmp_path / "events.db"
    with duckdb.connect(str(db)) as c:
        c.execute("CREATE TABLE stock_dividend(code VARCHAR,ex_date BIGINT, PRIMARY KEY(code,ex_date))")
        c.execute("INSERT INTO stock_dividend VALUES ('600000', ?)", [int(time.time()*1000)+20*86_400_000])
    report = DataQualityAuditor(db, {"stock_dividend": {"columns": {
        "code": {"required": True}, "ex_date": {"required": True}},
        "primary_key": ["code", "ex_date"]}}).run()
    issue = next(issue for issue in report.issues if issue.check == "FutureTimestamp")
    assert issue.severity == "warning"


def test_pipeline_ledger_and_quarantine_are_in_unified_report(tmp_path):
    import sqlite3
    db = tmp_path / "canonical.db"
    audit_db = tmp_path / "batch_audit.db"
    quarantine_db = tmp_path / "quarantine.db"
    with duckdb.connect(str(db)) as c:
        c.execute("CREATE TABLE sample(code VARCHAR PRIMARY KEY)")
        c.execute("INSERT INTO sample VALUES ('600000')")
    with sqlite3.connect(audit_db) as c:
        c.execute("""CREATE TABLE batch_audit(
            batch_id TEXT, rows_raw INTEGER, rows_aligned INTEGER,
            rows_passed INTEGER, rows_rejected INTEGER, rows_written INTEGER,
            status TEXT, finished_at TEXT)""")
        c.execute("INSERT INTO batch_audit VALUES ('b1',2,2,1,1,1,'success','2026-07-20T12:00:00')")
    with sqlite3.connect(quarantine_db) as c:
        c.execute("""CREATE TABLE quarantine(
            batch_id TEXT, table_name TEXT, source TEXT, original_payload TEXT,
            failed_rules TEXT, status TEXT)""")
        c.execute("INSERT INTO quarantine VALUES ('b1','sample','x','{}','[]','pending_repair')")
    report = DataQualityAuditor(
        db, {"sample": {"columns": {"code": {"required": True}},
                        "primary_key": ["code"]}},
        batch_audit_path=audit_db, quarantine_path=quarantine_db).run()
    checks = {issue.check for issue in report.issues}
    assert "QuarantineBacklog" in checks
    assert "StageCountConservation" not in checks
    assert report.passed
