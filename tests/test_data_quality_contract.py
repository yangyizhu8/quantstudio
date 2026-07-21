import pandas as pd

from quantstudio.pipeline.validator import PreIngestValidator


def _schema(table="stock_minutes"):
    columns = {
        "code": {"type": "str", "required": True, "regex": r"^\d{6}$"},
        "time": {"type": "int", "required": True},
        "freq": {"type": "str", "required": table.endswith("minutes")},
        "open": {"type": "float", "required": True, "gt": 0},
        "high": {"type": "float", "required": True, "gt": 0},
        "low": {"type": "float", "required": True, "gt": 0},
        "close": {"type": "float", "required": True, "gt": 0},
        "volume": {"type": "float", "required": True, "ge": 0},
        "amount": {"type": "float", "required": True, "ge": 0},
    }
    for side in ("front", "back"):
        for price in ("open", "high", "low", "close"):
            columns[f"{price}_{side}"] = {"type": "float", "required": False}
    return {table: {"columns": columns, "primary_key": ["code", "time", "freq"],
                    "time_key": "time"}}


def _row():
    return {"code": "600000", "time": 1_767_312_300_000, "freq": "5min",
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 1000.0, "amount": 10500.0,
            "open_front": 5.0, "high_front": 5.5, "low_front": 4.5, "close_front": 5.25,
            "open_back": 20.0, "high_back": 22.0, "low_back": 18.0, "close_back": 21.0}


def test_required_value_null_is_rejected():
    row = _row(); row["close"] = None
    result = PreIngestValidator(_schema()).validate(pd.DataFrame([row]), "stock_minutes", "b", "xtquant", "5min")
    assert "RequiredValueNull" in result.rejected_rules[0]


def test_frequency_mismatch_and_grid_are_rejected():
    row = _row(); row["freq"] = "1min"; row["time"] += 30_000
    result = PreIngestValidator(_schema()).validate(pd.DataFrame([row]), "stock_minutes", "b", "xtquant", "5min")
    rules = result.rejected_rules[0]
    assert "FrequencyMismatch" in rules and "FrequencyGrid" in rules


def test_partial_adjustment_set_is_rejected():
    row = _row(); row["high_front"] = None
    result = PreIngestValidator(_schema()).validate(pd.DataFrame([row]), "stock_minutes", "b", "xtquant", "5min")
    assert "AdjustmentCompleteness" in result.rejected_rules[0]


def test_inconsistent_adjustment_factor_is_rejected():
    row = _row(); row["high_front"] = 8.0
    result = PreIngestValidator(_schema()).validate(pd.DataFrame([row]), "stock_minutes", "b", "xtquant", "5min")
    assert "AdjustmentFactorConsistency" in result.rejected_rules[0]
