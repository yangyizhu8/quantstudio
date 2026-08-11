# -*- coding: utf-8 -*-
"""orchestrate_source ETF FREEZE 集成测试（任务安排 2.3）。

覆盖：
- test_etf_strategy_with_start_date → status PASS/PARTIAL, 产物含 ETF_POOL_STATIC, known_limitations 三段文案
- test_etf_strategy_without_start_date → status BLOCKED, errors 说明
- test_etf_strategy_data_blocked → etf_basic 缺失 → DATA_BLOCKED
"""
import json
import pathlib
import sys

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler.orchestrator import orchestrate_source  # noqa: E402


def _make_etf_db(tmp_path: pathlib.Path) -> pathlib.Path:
    db = tmp_path / "etf_test.db"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE etf_basic (code VARCHAR, list_date BIGINT, delist_date BIGINT, "
                 "etf_type VARCHAR, is_cross_border BOOLEAN)")
    ms = lambda d: int(pd.Timestamp(d).value // 10**6)  # noqa: E731
    conn.executemany(
        "INSERT INTO etf_basic VALUES (?, ?, ?, ?, FALSE)",
        [("510300", ms("2020-01-01"), None, "equity"),
         ("159001", ms("2020-01-01"), None, "equity"),
         ("588000", ms("2020-01-01"), ms("2023-06-01"), "equity"),
         ("159915", ms("2023-01-01"), None, "equity")])
    conn.execute("CREATE TABLE etf_daily (code VARCHAR, time BIGINT)")
    for c in ("510300", "159001", "588000", "159915"):
        conn.execute("INSERT INTO etf_daily VALUES (?, ?)", [c, ms("2021-06-30")])
    conn.close()
    return db


def _etf_strategy(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "etf_rotation.py"
    p.write_text(
        "def initialize(context):\n"
        "    g.pool = get_etf_list_local(context.current_dt)\n"
        "\n"
        "def handle_data(context, data):\n"
        "    pass\n", encoding="utf-8")
    return p


def test_etf_strategy_with_start_date(tmp_path, monkeypatch):
    db = _make_etf_db(tmp_path)
    strategy = _etf_strategy(tmp_path)
    out = tmp_path / "out"
    run_card = orchestrate_source(
        strategy, start="2024-01-01", end="2024-06-30",
        out_dir=out, run_smoke=False,
        etf_pool_start_date="2022-01-04", db_path=db,
    )
    assert run_card["status"] in ("PASS", "PARTIAL"), run_card["status"]
    pt = out / "etf_rotation" / "etf_rotation_ptrade.py"  # orchestrate_source: base_out/<strategy_id>/
    assert pt.exists()
    code = pt.read_text(encoding="utf-8")
    assert "ETF_POOL_STATIC" in code
    assert "g.pool = ETF_POOL_STATIC" in code
    # known_limitations 三段文案（07 §2.4）——warnings 在 source_import_report.json
    kls = "\n".join(run_card["known_limitations"])
    report = json.loads((out / "etf_rotation" / "source_import_report.json").read_text(encoding="utf-8"))
    kls += "\n" + "\n".join(report["warnings"])
    assert "ETF 池快照生成" in kls
    assert "不含起始日后新上市" in kls
    assert "仍含起始日后退市" in kls
    assert "回测起始日期不得早于 2022-01-04" in kls


def test_etf_strategy_without_start_date(tmp_path):
    db = _make_etf_db(tmp_path)
    strategy = _etf_strategy(tmp_path)
    out = tmp_path / "out2"
    run_card = orchestrate_source(
        strategy, start="2024-01-01", end="2024-06-30",
        out_dir=out, run_smoke=False, db_path=db,  # 不传 etf_pool_start_date
    )
    assert run_card["status"] == "BLOCKED"
    assert any("etf-pool-start-date" in k for k in run_card["known_limitations"])


def test_etf_strategy_data_blocked(tmp_path):
    empty_db = tmp_path / "empty.db"
    duckdb.connect(str(empty_db)).close()
    strategy = _etf_strategy(tmp_path)
    out = tmp_path / "out3"
    run_card = orchestrate_source(
        strategy, start="2024-01-01", end="2024-06-30",
        out_dir=out, run_smoke=False,
        etf_pool_start_date="2022-01-04", db_path=empty_db,
    )
    assert run_card["status"] == "BLOCKED"
    assert any("DATA_BLOCKED" in k for k in run_card["known_limitations"])
    # run_card 契约：不产出半成品
    assert not (out / "etf_rotation_ptrade.py").exists()
