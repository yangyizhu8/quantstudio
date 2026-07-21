import pandas as pd

from quantstudio.pipeline.sources.akshare_adapter import AkshareAdapter
from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter


def test_akshare_adjustment_merges_by_date_not_position():
    raw = pd.DataFrame({"日期": ["2026-01-01", "2026-01-02"], "收盘": [10, 11]})
    qfq = pd.DataFrame({"日期": ["2026-01-02"], "开盘": [8], "最高": [9],
                        "最低": [7], "收盘": [8.5]})
    out = AkshareAdapter._merge_adjusted_ohlc(raw, qfq, "front")
    assert pd.isna(out.loc[0, "close_front"])
    assert out.loc[1, "close_front"] == 8.5


def test_xtquant_adjustment_merges_by_time_not_position():
    raw = pd.DataFrame({"time": [1, 2], "close": [10, 11]})
    qfq = pd.DataFrame({"time": [2], "open": [8], "high": [9], "low": [7], "close": [8.5]})
    out = XtquantAdapter._merge_adjusted_ohlc(raw, qfq, "front")
    assert pd.isna(out.loc[0, "close_front"])
    assert out.loc[1, "close_front"] == 8.5
