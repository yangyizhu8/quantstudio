"""K线图查看器 — 从 DuckDB 读取行情，叠加回测买卖标记。
纯 matplotlib 实现，不依赖任何外部 K线库。"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import numpy as np

import matplotlib
matplotlib.use('QtAgg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
# 皮肤调色板对齐（GitHub Dark 系，与 GUI 主界面一致）
plt.rcParams['figure.facecolor'] = '#161b22'
plt.rcParams['axes.facecolor'] = '#161b22'
plt.rcParams['savefig.facecolor'] = '#161b22'
plt.rcParams['text.color'] = '#e6edf3'
plt.rcParams['axes.labelcolor'] = '#8b949e'
plt.rcParams['xtick.color'] = '#8b949e'
plt.rcParams['ytick.color'] = '#8b949e'
plt.rcParams['grid.color'] = '#30363d'
plt.rcParams['axes.edgecolor'] = '#30363d'


class KlineViewer(QMainWindow):
    """K线图查看器 — 从 DuckDB 读取行情数据，叠加回测买卖点

    Args:
        db_path: DuckDB 路径
        code: 股票代码（裸码，如 600000）
        trades_df: 该标的的交易记录（含 date/datetime, action, price, volume）
        start_date / end_date: 显示区间
    """

    def __init__(self, db_path: str, code: str, trades_df: pd.DataFrame,
                 start_date: str = "", end_date: str = ""):
        super().__init__()
        self.db_path = db_path
        self.code = code
        self.trades_df = trades_df
        self.start_date = start_date
        self.end_date = end_date

        self.setWindowTitle(f"K线图 — {code}")
        self.resize(1200, 700)

        self._setup_ui()
        self._load_and_draw()

    def _setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        self.fig = Figure(figsize=(12, 6))
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas)
        self.setCentralWidget(central)

    def _load_and_draw(self):
        """从 DuckDB 读取数据并绘制"""
        # 1. 读取 K 线数据
        df = self._load_kline()
        if df is None or len(df) == 0:
            self.fig.text(0.5, 0.5, f"无数据: {self.code}", ha='center', va='center', fontsize=16)
            self.canvas.draw()
            return

        # 2. 绘制
        self.fig.clear()
        gs = self.fig.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.15)
        ax_price = self.fig.add_subplot(gs[0])
        ax_vol = self.fig.add_subplot(gs[1], sharex=ax_price)
        ax_signal = self.fig.add_subplot(gs[2], sharex=ax_price)

        self._draw_candlestick(ax_price, df)
        self._draw_ma(ax_price, df)
        self._draw_volume(ax_vol, df)
        self._draw_signals(ax_signal, df)

        ax_price.set_title(f"{self.code} K线 + 买卖标记", fontsize=13)
        ax_price.legend(loc='upper left', fontsize=9)
        ax_vol.set_ylabel('成交量', fontsize=9)
        ax_signal.set_ylabel('信号', fontsize=9)
        plt.setp(ax_price.get_xticklabels(), visible=False)
        plt.setp(ax_vol.get_xticklabels(), visible=False)

        self.fig.tight_layout()
        self.canvas.draw()

    def _load_kline(self) -> pd.DataFrame:
        """从 DuckDB 读取 K 线"""
        try:
            import duckdb
            conn = duckdb.connect(self.db_path, read_only=True)
            start_ms = int(pd.Timestamp(self.start_date, tz='Asia/Shanghai').timestamp() * 1000) if self.start_date else 0
            end_ms = int(pd.Timestamp(self.end_date, tz='Asia/Shanghai').timestamp() * 1000) + 86400000 if self.end_date else 9999999999999

            df = conn.execute(f"""
                SELECT code, time, open, high, low, close, volume, amount,
                       preClose, pctChg
                FROM stock_daily
                WHERE code = '{self.code}'
                AND time >= {start_ms} AND time <= {end_ms}
                ORDER BY time
            """).fetchdf()
            conn.close()

            if len(df) > 0:
                df['date'] = pd.to_datetime(df['time'], unit='ms', utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d')
                df['date'] = pd.to_datetime(df['date'])
                df['idx'] = range(len(df))
            return df
        except Exception as e:
            logger.error(f"K线数据读取失败: {e}")
            return None

    def _draw_candlestick(self, ax, df):
        """绘制蜡烛图"""
        width = 0.6
        for _, row in df.iterrows():
            color = '#ff4444' if row['close'] >= row['open'] else '#44aa44'
            # 影线
            ax.vlines(row['idx'], row['low'], row['high'], color=color, linewidth=0.8)
            # 实体
            bottom = min(row['open'], row['close'])
            height = max(abs(row['close'] - row['open']), 0.01)
            rect = Rectangle((row['idx'] - width/2, bottom), width, height,
                             color=color, alpha=0.85, edgecolor=color)
            ax.add_patch(rect)
        ax.set_xlim(-1, len(df))

    def _draw_ma(self, ax, df):
        """绘制均线"""
        for period, color in [(5, '#ff8800'), (10, '#0088ff'), (20, '#8800ff')]:
            if len(df) >= period:
                ma = df['close'].rolling(period).mean()
                ax.plot(df['idx'], ma, color=color, linewidth=0.8,
                        label=f'MA{period}', alpha=0.7)

    def _draw_volume(self, ax, df):
        """绘制成交量"""
        colors = ['#ff6666' if c >= o else '#66cc66' for c, o in zip(df['close'], df['open'])]
        ax.bar(df['idx'], df['volume'], color=colors, width=0.6, alpha=0.6)

    def _draw_signals(self, ax_signal, df):
        """叠加买卖标记"""
        if self.trades_df is None or len(self.trades_df) == 0:
            return

        # 标准化交易记录的日期列
        trades = self.trades_df.copy()
        date_col = 'datetime' if 'datetime' in trades.columns else 'date'
        if date_col in trades.columns:
            trades['trade_date'] = pd.to_datetime(trades[date_col]).dt.strftime('%Y-%m-%d')

            # 建立日期→idx 映射
            date_to_idx = dict(zip(df['date'].dt.strftime('%Y-%m-%d'), df['idx']))

            for _, trade in trades.iterrows():
                td = str(trade['trade_date'])[:10]
                if td in date_to_idx:
                    idx = date_to_idx[td]
                    action = trade.get('action', '')
                    price = trade.get('price', 0)
                    if action == 'buy':
                        ax_signal.scatter(idx, 1, marker='^', color='#ff0000', s=80, zorder=5)
                        ax_signal.annotate(f'买\n{price:.2f}', (idx, 1),
                                          textcoords="offset points", xytext=(0, 10),
                                          ha='center', fontsize=7, color='red')
                    elif action == 'sell':
                        ax_signal.scatter(idx, -1, marker='v', color='#00cc00', s=80, zorder=5)
                        ax_signal.annotate(f'卖\n{price:.2f}', (idx, -1),
                                          textcoords="offset points", xytext=(0, -15),
                                          ha='center', fontsize=7, color='green')

        ax_signal.set_ylim(-1.5, 1.5)
        ax_signal.set_yticks([-1, 0, 1])
        ax_signal.set_yticklabels(['卖出', '', '买入'])
        ax_signal.axhline(y=0, color='gray', linewidth=0.5, linestyle='--')
