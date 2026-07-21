import pandas as pd
import pytest

from quantstudio.backtest.calibration import CalibrationReport
from quantstudio.backtest.providers.ptrade_export_provider import PtradeExportProvider


def _write_exports(directory):
    pd.DataFrame([
        {'date': '2025-01-02', 'code': '600000.SH', 'action': 'buy',
         'volume': 100, 'price': 10.0, 'amount': 1000.0, 'commission': 5.0, 'tax': 0.0},
        {'date': '2025-01-02', 'code': '600000.SH', 'action': 'buy',
         'volume': 100, 'price': 12.0, 'amount': 1200.0, 'commission': 5.0, 'tax': 0.0},
    ]).to_csv(directory / 'trades.csv', index=False)
    pd.DataFrame([
        {'date': '2025-01-02', 'code': '600000.SH', 'volume': 200,
         'market_value': 2200.0, 'cost_basis': 11.0},
    ]).to_csv(directory / 'positions.csv', index=False)


def test_ptrade_export_provider_loads_and_filters(tmp_path):
    _write_exports(tmp_path)
    provider = PtradeExportProvider(tmp_path)

    assert len(provider.get_trades_on_date('2025-01-02')) == 2
    assert provider.get_positions_on_date('2025-01-02').iloc[0]['code'] == '600000'
    assert provider.get_stock_prices_from_trades()['2025-01-02']['600000'] == pytest.approx(11.0)


def test_ptrade_export_provider_requires_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        PtradeExportProvider(tmp_path)


def test_calibration_report_layers_sum_to_total():
    report = CalibrationReport(total_return_diff_bps=12.5, l1_engine_bps=2.0,
                               l2_data_bps=3.5, l3_source_bps=7.0)

    assert report.l1_engine_bps + report.l2_data_bps + report.l3_source_bps == pytest.approx(
        report.total_return_diff_bps)
    assert '12.50 bps' in report.summary()
