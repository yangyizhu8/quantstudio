from pathlib import Path

from quantstudio.backtest.backtest_engine import _prefer_project_data_db


def test_project_data_duckdb_has_priority_when_present(tmp_path):
    project_db = tmp_path / "data" / "quantstudio.db"
    project_db.parent.mkdir()
    project_db.touch()
    external = tmp_path / "external" / "quantstudio.db"
    assert _prefer_project_data_db(tmp_path, external) == project_db.resolve()


def test_configured_duckdb_is_fallback_when_project_db_absent(tmp_path):
    external = tmp_path / "external" / "quantstudio.db"
    assert _prefer_project_data_db(tmp_path, external) == Path(external)
