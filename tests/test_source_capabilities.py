from quantstudio.pipeline.sources.akshare_adapter import AkshareAdapter
from quantstudio.pipeline.sources.baostock_adapter import BaostockAdapter
from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter


def _bare(cls):
    obj = object.__new__(cls)
    return obj


def test_support_matrices_reject_unimplemented_tables():
    assert not _bare(TushareAdapter).supports_task("stock_delist", "daily")[0]
    assert not _bare(BaostockAdapter).supports_task("fin_indicator", "daily")[0]
    assert not _bare(AkshareAdapter).supports_task("stock_minutes", "1min")[0]
    assert not _bare(XtquantAdapter).supports_task("stock_dividend", "daily")[0]


def test_support_matrices_keep_real_implementations():
    assert _bare(TushareAdapter).supports_task("stock_minutes", "15min")[0]
    assert _bare(BaostockAdapter).supports_task("stock_minutes", "5min")[0]
    assert _bare(AkshareAdapter).supports_task("etf_minutes", "5min")[0]
    assert _bare(XtquantAdapter).supports_task("stock_minutes", "30min")[0]
