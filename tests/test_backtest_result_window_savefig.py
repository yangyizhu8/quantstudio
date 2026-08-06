from __future__ import annotations

"""BacktestResultWindow 导出回测可视化图片的回归测试。

验证: 回测完成 → 结果窗口 show → export_report_images() 后，会在回测结果
目录下生成 6 张 PNG（收益曲线 / 基本信息 / 交易记录 / 日收益 / 绩效分析两张），
且均非空、尺寸正常（与 daily_stats.csv 等结果文件同目录）。
"""
import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("matplotlib")

QApplication = qt_widgets.QApplication

from quantstudio.gui.backtest_result_window import BacktestResultWindow


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _png_size(path):
    """读取 PNG 文件头的宽高（无需 PIL）。返回 (width, height) 或 None。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def _write_backtest_dir(backtest_dir):
    """写入一份最小但完整的回测结果目录（与 result_exporter 的产出结构一致）。"""
    pd.DataFrame([{
        'strategy_file': 'demo.py',
        'strategy': 'demo',
        'start_time': '2024-01-01',
        'end_time': '2024-03-01',
        'init_capital': 1000000,
        'commission_rate': 0.00025,
        'min_commission': 5.0,
        'stamp_tax_rate': 0.0005,
        'transfer_fee_rate': 0.00001,
        'slippage_rate': 0.001,
        'fixed_slippage': 0.0,
        'match_price_mode': 'close',
        'engine_semantics_version': '0.1.0-legacy',
        'min_rebalance_pct': 0.0,
    }]).to_csv(backtest_dir / "config.csv", index=False, encoding='utf-8-sig')

    dates = pd.date_range('2024-01-02', periods=30, freq='B')
    nav = pd.DataFrame({
        'date': dates,
        'total_asset': [1000000 * (1 + 0.005 * i) for i in range(30)],
        'cash': 1000000,
        'market_value': 0,
    })
    nav['daily_return'] = nav['total_asset'].pct_change().fillna(0)
    nav.to_csv(backtest_dir / "daily_stats.csv", index=False, encoding='utf-8-sig')

    bench = pd.DataFrame({
        'date': dates,
        'close': [1 + 0.001 * i for i in range(30)],
    })
    bench.to_csv(backtest_dir / "benchmark.csv", index=False, encoding='utf-8-sig')

    pd.DataFrame([
        {'datetime': '2024-01-05', 'code': '510300', 'action': 'buy',
         'price': 3.5, 'volume': 10000, 'amount': 35000, 'commission': 5.0},
        {'datetime': '2024-01-19', 'code': '510300', 'action': 'sell',
         'price': 3.8, 'volume': 10000, 'amount': 38000, 'commission': 5.0},
    ]).to_csv(backtest_dir / "trades.csv", index=False, encoding='utf-8-sig')


def test_result_window_exports_report_images(app, tmp_path):
    _write_backtest_dir(tmp_path)
    win = BacktestResultWindow(str(tmp_path))
    try:
        win.show()
        QApplication.processEvents()
        win.export_report_images()

        expected = [
            "回测收益曲线.png",
            "基本信息.png",
            "交易记录.png",
            "日收益.png",
            "绩效分析-收益分布.png",
            "绩效分析-月度收益热力图.png",
        ]
        for name in expected:
            path = tmp_path / name
            assert path.exists(), f"未生成图片: {path}"
            size = _png_size(path)
            assert size is not None, f"不是合法 PNG: {path}"
            w, h = size
            assert w >= 50 and h >= 50, f"图片尺寸异常 ({w}x{h}): {path}"
            assert path.stat().st_size > 0, f"图片为空: {path}"
    finally:
        win.close()
