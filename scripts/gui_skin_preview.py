#!/usr/bin/env python
"""GUI 皮肤预览截图工具（皮肤改造验收用，Phase 0 before / Phase 4 after）。

用法：
    python scripts/gui_skin_preview.py                       # 默认输出 output/skin_preview/before
    python scripts/gui_skin_preview.py --out output/skin_preview/after

行为：
- 强制 QT_QPA_PLATFORM=offscreen（无窗口环境安全）。
- 与 main_gui.py 完全相同的装配路径构造 MainWindow（真实 DbHelper + 当前 profile 配置）。
- 若 quantstudio.gui.skin 存在则应用皮肤（改造前该模块不存在 → 产出即 before 基线）。
- 逐 Tab grab() 截图 + 整窗截图 + 合成回测结果目录的 BacktestResultWindow 截图。

只读操作：不写数据库、不启动 worker、不启动 daemon（与 GUI 启动时构造行为一致）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write_fixture_result_dir(base: Path) -> Path:
    """合成一份最小完整回测结果目录（结构对齐 tests/test_backtest_result_window_savefig.py）。"""
    import pandas as pd

    d = base / "_fixture_result"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        'strategy_file': 'demo.py', 'strategy': 'demo',
        'start_time': '2024-01-01', 'end_time': '2024-03-01',
        'init_capital': 1000000, 'commission_rate': 0.00025,
        'min_commission': 5.0, 'stamp_tax_rate': 0.0005,
        'transfer_fee_rate': 0.00001, 'slippage_rate': 0.001,
        'fixed_slippage': 0.0, 'match_price_mode': 'close',
        'engine_semantics_version': '0.1.0-legacy', 'min_rebalance_pct': 0.0,
    }]).to_csv(d / "config.csv", index=False, encoding='utf-8-sig')

    dates = pd.date_range('2024-01-02', periods=60, freq='B')
    nav = pd.DataFrame({
        'date': dates,
        'total_asset': [1000000 * (1 + 0.004 * i) for i in range(60)],
        'cash': 600000, 'market_value': 400000,
    })
    nav['daily_return'] = nav['total_asset'].pct_change().fillna(0)
    nav.to_csv(d / "daily_stats.csv", index=False, encoding='utf-8-sig')

    pd.DataFrame({'date': dates, 'close': [1 + 0.001 * i for i in range(60)]}) \
        .to_csv(d / "benchmark.csv", index=False, encoding='utf-8-sig')

    rows = []
    for k in range(0, 50, 10):
        rows.append({'datetime': str(dates[k].date()), 'code': '510300', 'action': 'buy',
                     'price': 3.5 + 0.01 * k, 'volume': 10000,
                     'amount': (3.5 + 0.01 * k) * 10000, 'commission': 5.0})
        rows.append({'datetime': str(dates[k + 5].date()), 'code': '510300', 'action': 'sell',
                     'price': 3.6 + 0.01 * k, 'volume': 10000,
                     'amount': (3.6 + 0.01 * k) * 10000, 'commission': 5.0})
    pd.DataFrame(rows).to_csv(d / "trades.csv", index=False, encoding='utf-8-sig')
    return d


def main() -> int:
    parser = argparse.ArgumentParser(description="GUI 皮肤预览截图")
    parser.add_argument("--out", default=str(ROOT / "output" / "skin_preview" / "before"),
                        help="截图输出目录")
    parser.add_argument("--skip-result", action="store_true", help="跳过回测结果窗口截图")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from PyQt6.QtWidgets import QApplication
    from quantstudio._paths import db_path, quarantine_db_path, DATA_ROOT
    from quantstudio.gui.main_window import MainWindow
    from quantstudio.gui.db_helper import DbHelper

    app = QApplication(sys.argv)

    # 皮肤应用（与 main_gui.py 演进保持一致：皮肤模块存在则应用，否则仅暗色主题）
    skin_applied = False
    try:
        from quantstudio.gui import skin
        skin.apply_app_skin(app)
        skin_applied = True
    except ImportError:
        from qfluentwidgets import setTheme, Theme
        setTheme(Theme.DARK)
    print(f"[skin-preview] skin module applied: {skin_applied}")

    db_helper = DbHelper(
        duckdb_path=str(db_path()),
        quarantine_path=str(quarantine_db_path()),
        batch_audit_path=str(DATA_ROOT / "batch_audit.db"),
    )
    window = MainWindow(
        db_helper=db_helper,
        config_dir=ROOT / "config" / "profiles" / "mcp_only",
    )
    window.resize(1440, 900)
    app.processEvents()

    if skin_applied:
        from quantstudio.gui import skin
        skin.apply_window_skin(window)
        app.processEvents()

    saved = []

    # 整窗截图（侧边栏 + 内容 + 日志面板）
    win_png = out_dir / "window-full.png"
    window.grab().save(str(win_png))
    saved.append(win_png)

    # 逐 Tab 截图
    stack = window.stackedWidget
    for i in range(stack.count()):
        w = stack.widget(i)
        if w is None:
            continue
        w.resize(1280, 780)
        app.processEvents()
        png = out_dir / f"tab{i:02d}-{type(w).__name__}.png"
        w.grab().save(str(png))
        saved.append(png)

    # 日志面板写入几条示例日志再截一次整窗（验证日志着色）
    try:
        import logging
        for lvl, msg in [
            (logging.INFO, "[skin-preview] INFO 示例：常驻采集进程已启动"),
            (logging.WARNING, "[skin-preview] WARNING 示例：kHistory 源缺失 股票-300392.SZ"),
            (logging.ERROR, "[skin-preview] ERROR 示例：任务 stock_daily 拉取失败"),
        ]:
            logging.getLogger("skin.preview").log(lvl, msg)
        app.processEvents()
        win_png2 = out_dir / "window-full-with-logs.png"
        window.grab().save(str(win_png2))
        saved.append(win_png2)
    except Exception as e:  # noqa: BLE001
        print(f"[skin-preview] 日志示例写入失败（不影响截图）: {e}")

    # 回测结果窗口（合成数据）
    if not args.skip_result:
        try:
            from quantstudio.gui.backtest_result_window import BacktestResultWindow
            fixture = _write_fixture_result_dir(out_dir)
            rw = BacktestResultWindow(str(fixture), None)
            rw.resize(1500, 950)
            app.processEvents()
            png = out_dir / "result-window.png"
            rw.grab().save(str(png))
            saved.append(png)
            rw.close()
        except Exception as e:  # noqa: BLE001
            print(f"[skin-preview] 结果窗口截图失败: {e}")

    window.close()
    app.processEvents()

    for p in saved:
        print(f"[skin-preview] saved: {p}")
    print(f"[skin-preview] done -> {out_dir} ({len(saved)} images)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
