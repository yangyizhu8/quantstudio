from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd


class PtradeExportProvider:
    """读取标准化 Ptrade 导出物，供轨迹校准使用。"""

    TRADE_COLUMNS = ['date', 'code', 'action', 'volume', 'price', 'amount',
                     'commission', 'tax']
    POSITION_COLUMNS = ['date', 'code', 'volume', 'market_value', 'cost_basis']

    def __init__(self, export_dir: Path):
        self._dir = Path(export_dir)
        self._trades_df = self._load_trades()
        self._positions_df = self._load_positions()
        self._log_text = self._load_log()

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        last_error = None
        for encoding in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError as error:
                last_error = error
        raise last_error

    def _load_trades(self) -> pd.DataFrame:
        path = self._dir / 'trades.csv'
        if not path.exists():
            raise FileNotFoundError(f'缺少 Ptrade 成交文件: {path}')
        df = self._read_csv(path)
        missing = [column for column in self.TRADE_COLUMNS if column not in df.columns]
        if missing:
            raise ValueError(f'trades.csv 缺少列: {missing}')
        df = df[self.TRADE_COLUMNS].copy()
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df['code'] = df['code'].astype(str).str.split('.').str[0].str.zfill(6)
        actions = {'buy': 'buy', 'sell': 'sell', '买': 'buy', '卖': 'sell'}
        df['action'] = df['action'].astype(str).str.lower().map(actions)
        if df['action'].isna().any():
            raise ValueError('trades.csv action 仅支持 buy/sell/买/卖')
        for column in ('volume', 'price', 'amount', 'commission', 'tax'):
            df[column] = pd.to_numeric(df[column], errors='raise')
        df['volume'] = df['volume'].astype(int)
        return df

    def _load_positions(self) -> pd.DataFrame:
        path = self._dir / 'positions.csv'
        if not path.exists():
            raise FileNotFoundError(f'缺少 Ptrade 持仓文件: {path}')
        df = self._read_csv(path)
        missing = [column for column in self.POSITION_COLUMNS if column not in df.columns]
        if missing:
            raise ValueError(f'positions.csv 缺少列: {missing}')
        df = df[self.POSITION_COLUMNS].copy()
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df['code'] = df['code'].astype(str).str.split('.').str[0].str.zfill(6)
        for column in ('volume', 'market_value', 'cost_basis'):
            df[column] = pd.to_numeric(df[column], errors='raise')
        df['volume'] = df['volume'].astype(int)
        return df

    def _load_log(self) -> Optional[str]:
        path = self._dir / 'log.txt'
        if not path.exists():
            path = self._dir / 'Log.txt'
        if not path.exists():
            return None
        raw = path.read_bytes()
        for encoding in ('utf-8-sig', 'utf-16', 'gbk', 'utf-8'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')

    @property
    def trades(self) -> pd.DataFrame:
        return self._trades_df.copy()

    @property
    def positions(self) -> pd.DataFrame:
        return self._positions_df.copy()

    @property
    def log_text(self) -> Optional[str]:
        return self._log_text

    def get_trades_on_date(self, date: str) -> pd.DataFrame:
        date = pd.Timestamp(date).strftime('%Y-%m-%d')
        return self._trades_df[self._trades_df['date'] == date].copy()

    def get_positions_on_date(self, date: str) -> pd.DataFrame:
        date = pd.Timestamp(date).strftime('%Y-%m-%d')
        return self._positions_df[self._positions_df['date'] == date].copy()

    def get_stock_prices_from_trades(self) -> Dict[str, Dict[str, float]]:
        prices: Dict[str, Dict[str, float]] = {}
        for (date, code), rows in self._trades_df.groupby(['date', 'code'], sort=True):
            volume = rows['volume'].abs()
            denominator = volume.sum()
            price = ((rows['price'] * volume).sum() / denominator
                     if denominator else rows['price'].iloc[-1])
            prices.setdefault(date, {})[code] = float(price)
        return prices
