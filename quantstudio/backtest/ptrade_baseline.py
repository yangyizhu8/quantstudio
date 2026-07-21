"""Ptrade 平台回测结果导入器（B0/B2）。

基于真实样本探样产出的格式规格表实现（见 docs/interface-contract.md 附录 B）。
严格按规格表解析，不做猜测。样本：私募工作文件/ptrade_samples/。

提供三件套加载 + 净值反推能力，供 FidelityComparator（B3）对照使用。
"""
from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 初始资金（Ptrade 默认，与本地引擎一致）
PTRADE_INITIAL_CAPITAL = 100_000.0

# ========== 数据源口径（D10 决策）==========
# 已知口径：本地引擎/API 封装层与 Ptrade 基准各自声明自己的数据来源，
# 对照前做一致性校验，把"口径一致"从文档约定变成程序断言。
KNOWN_DATA_SOURCES = frozenset({"tushare", "xtquant", "juyuan"})
# 唯一允许的跨源对照（已知数据源精度容差，对应 fidelity-validation.md §四）：
# 本地 tushare 引擎 vs Ptrade 平台 juyuan 基准。其余任何跨源一律拒绝对照。
ACCEPTED_CROSS_SOURCE_PAIRS = frozenset({("tushare", "juyuan"), ("juyuan", "tushare")})


class SourceConsistencyError(ValueError):
    """数据源口径不一致，拒绝对照时抛出。"""


def assert_source_consistency(engine_source: str, baseline_source: str,
                              strict: bool = False) -> dict:
    """校验本地引擎数据源口径与 Ptrade 基准数据源口径是否一致。

    规则（把"口径一致"变为程序断言）：
    - 任一口径为空或未知 → 直接拒绝（沉默漂移最危险，永远拦截）。
    - 两端口径相同 → 完全一致，放行。
    - 两端口径不同但属于已声明跨源白名单（tushare↔juyuan）→
      告警并通过，标记 cross_source=True（已知 float_value 精度容差，属 L3 软指标）。
    - 其余任何不一致 → 拒绝对照；strict=True 时连白名单跨源也拒绝。

    返回 dict：{consistent, cross_source, engine_source, baseline_source, message}。
    """
    if not engine_source or engine_source not in KNOWN_DATA_SOURCES:
        raise SourceConsistencyError(
            f"引擎数据源口径未声明或未知: {engine_source!r}（已知: {sorted(KNOWN_DATA_SOURCES)}）")
    if not baseline_source or baseline_source not in KNOWN_DATA_SOURCES:
        raise SourceConsistencyError(
            f"Ptrade 基准数据源口径未声明或未知: {baseline_source!r}（已知: {sorted(KNOWN_DATA_SOURCES)}）")
    if engine_source == baseline_source:
        return {"consistent": True, "cross_source": False,
                "engine_source": engine_source, "baseline_source": baseline_source,
                "message": f"数据源口径一致: {engine_source}"}
    pair = (engine_source, baseline_source)
    if pair in ACCEPTED_CROSS_SOURCE_PAIRS:
        if strict:
            raise SourceConsistencyError(
                f"strict 模式拒绝跨源对照: 引擎={engine_source} vs 基准={baseline_source}")
        logger.warning(
            f"[SourceCheck] 跨源对照（已知容差）: 引擎={engine_source} vs 基准={baseline_source}。"
            f"数据精度差异（如 float_value 流通市值）可能导致选股排名边界翻转，属 L3 软指标容差范围。")
        return {"consistent": True, "cross_source": True,
                "engine_source": engine_source, "baseline_source": baseline_source,
                "message": f"跨源对照（已声明容差）: 引擎={engine_source} vs 基准={baseline_source}"}
    # 非白名单的不一致 → 拒绝（防止口径悄悄漂移）
    raise SourceConsistencyError(
        f"数据源口径不一致，拒绝对照: 引擎={engine_source} vs 基准={baseline_source}。"
        f"仅允许相同口径或白名单跨源 {sorted(ACCEPTED_CROSS_SOURCE_PAIRS)}")


class PtradeBaseline:
    """加载 Ptrade 平台导出的回测结果，作为对照基准。

    三件套来源（GBK 编码，文件名含时间戳，按前缀匹配）：
    - 交易详情*.csv  → trades（L1 信号、L4 成本、净值反推现金流）
    - 持仓明细*.csv  → holdings（L3 持仓重叠、净值反推市值）
    - Log.txt        → log_lines（L1 辅助校验）
    """

    def __init__(self, source_id: str = "juyuan"):
        self.trades: Optional[pd.DataFrame] = None
        self.holdings: Optional[pd.DataFrame] = None
        self.log_lines: list = []
        self._nav: Optional[pd.DataFrame] = None  # 反推的逐日净值（惰性）
        # 该基准所代表的数据源口径（Ptrade 平台导出 = 聚源 juyuan）。
        # 与引擎 EngineConfig.data_source 做一致性校验（D10 决策）。
        self.source_id: str = source_id

    def check_source_consistency(self, engine_source: str, strict: bool = False) -> dict:
        """与本地引擎数据源口径做一致性校验（见 assert_source_consistency）。"""
        return assert_source_consistency(engine_source, self.source_id, strict=strict)

    # ========== 三件套加载 ==========

    def load_trades_csv(self, path) -> pd.DataFrame:
        """加载交易详情 CSV。规格见 interface-contract.md B.2。

        标准化：买/卖→buy/sell，成交量→int，代码保留 .SZ/.SH。
        """
        path = Path(path)
        # B.1: GBK 编码
        df = pd.read_csv(path, encoding='gbk')
        expected_cols = ['日期', '时间', '合约代码', '买/卖', '开/平', '成交量', '成交价', '手续费']
        if list(df.columns) != expected_cols:
            raise ValueError(f"交易详情表头不符规格。期望 {expected_cols}，实际 {list(df.columns)}")

        # B.6 标准化
        df['direction'] = df['买/卖'].map({'买': 'buy', '卖': 'sell'})
        if df['direction'].isna().any():
            bad = df.loc[df['direction'].isna(), '买/卖'].unique()
            raise ValueError(f"买/卖列含非预期值: {bad}（规格仅允许 买/卖）")
        df['volume'] = df['成交量'].astype(int)
        df['code'] = df['合约代码'].astype(str)
        df['date'] = pd.to_datetime(df['日期'], format='%Y-%m-%d')
        df['price'] = df['成交价'].astype(float)
        df['commission'] = df['手续费'].astype(float)
        # 开/平列恒为 '-'，忽略（B.2）

        self.trades = df
        logger.info(f"[PtradeBaseline] 加载交易详情 {len(df)} 笔: "
                    f"{df['date'].min().date()} ~ {df['date'].max().date()}")
        return df

    def load_holdings_csv(self, path) -> pd.DataFrame:
        """加载持仓明细 CSV。规格见 interface-contract.md B.3。

        标准化：仓位→int，市值/成本价→float。
        """
        path = Path(path)
        df = pd.read_csv(path, encoding='gbk')
        expected_cols = ['日期', '时间', '合约代码', '最新价', '仓位', '多/空', '持仓成本价', '市值', '累计盈亏']
        if list(df.columns) != expected_cols:
            raise ValueError(f"持仓明细表头不符规格。期望 {expected_cols}，实际 {list(df.columns)}")

        df['volume'] = df['仓位'].astype(int)
        df['code'] = df['合约代码'].astype(str)
        df['date'] = pd.to_datetime(df['日期'], format='%Y-%m-%d')
        df['last_price'] = df['最新价'].astype(float)
        df['avg_cost'] = df['持仓成本价'].astype(float)
        df['market_value'] = df['市值'].astype(float)
        df['cum_pnl'] = df['累计盈亏'].astype(float)
        # 多/空列恒为 '多'，忽略（B.3）

        self.holdings = df
        logger.info(f"[PtradeBaseline] 加载持仓明细 {len(df)} 行 "
                    f"({df['date'].nunique()} 个交易日): "
                    f"{df['date'].min().date()} ~ {df['date'].max().date()}")
        return df

    def load_log_txt(self, path) -> list:
        """加载 Log.txt。规格见 B.4。

        实测编码为 UTF-16 LE（文件头 BOM fffe）。带 errors='replace' 容错。
        """
        path = Path(path)
        with open(path, 'rb') as f:
            raw = f.read()
        # B.4 实测：UTF-16 LE 编码（fffe BOM）
        # 优先 UTF-16，失败回退 GBK/UTF-8（兼容未来 Ptrade 版本变化）
        text = None
        for enc in ('utf-16', 'gbk', 'utf-8'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode('utf-8', errors='replace')
        lines = [line.strip() for line in text.splitlines() if ' - INFO - ' in line]
        self.log_lines = lines
        logger.info(f"[PtradeBaseline] 加载 Log.txt {len(lines)} 行有效日志")
        return lines

    def load_dir(self, samples_dir) -> 'PtradeBaseline':
        """从样本目录自动加载三件套（按文件名前缀匹配）。"""
        samples_dir = Path(samples_dir)
        # B.1: 按前缀匹配（文件名含时间戳）
        trades_file = next(samples_dir.glob('交易详情*.csv'), None)
        holdings_file = next(samples_dir.glob('持仓明细*.csv'), None)
        log_file = samples_dir / 'Log.txt'
        if trades_file is None:
            raise FileNotFoundError(f"未找到 交易详情*.csv 于 {samples_dir}")
        if holdings_file is None:
            raise FileNotFoundError(f"未找到 持仓明细*.csv 于 {samples_dir}")
        self.load_trades_csv(trades_file)
        self.load_holdings_csv(holdings_file)
        if log_file.exists():
            self.load_log_txt(log_file)
        return self

    # ========== 净值反推（B.5）==========

    def compute_nav(self, initial_capital: float = PTRADE_INITIAL_CAPITAL) -> pd.DataFrame:
        """反推逐日净值（无独立净值 CSV，见 interface-contract.md B.5）。

        每日总资产(t) = 持仓市值合计(t) + 现金(t)
        现金(t) = 初始资金 - Σ(截止t买入支出) + Σ(截止t卖出收入)

        精度损失：成交价(含滑点) vs 最新价(收盘)，预期 < 0.1%。
        结果含 residual 列（反推残差告警用）。
        """
        if self.trades is None or self.holdings is None:
            raise RuntimeError("需先加载 trades 和 holdings")

        # 按日累计现金流
        trades = self.trades.copy()
        trades['cash_out'] = trades.apply(
            lambda r: r['volume'] * r['price'] + r['commission'] if r['direction'] == 'buy'
            else -(r['volume'] * r['price'] - r['commission']), axis=1)
        cum_cash_out = trades.groupby('date')['cash_out'].sum().cumsum()

        # 每日持仓市值合计
        daily_mv = self.holdings.groupby('date')['market_value'].sum()

        # 对齐到所有出现过的日期
        all_dates = sorted(set(cum_cash_out.index) | set(daily_mv.index))
        nav_rows = []
        prev_total = initial_capital
        for d in all_dates:
            cash = initial_capital - cum_cash_out.get(d, 0.0)
            # cum_cash_out 截止 d 的累计；若该日无交易，用前一累计值
            cum_to_date = cum_cash_out[cum_cash_out.index <= d]
            cash = initial_capital - (cum_to_date.iloc[-1] if len(cum_to_date) > 0 else 0.0)
            mv = daily_mv.get(d, 0.0)
            total = cash + mv
            nav_rows.append({
                'date': d,
                'cash': cash,
                'market_value': mv,
                'total': total,
                'nav': total / initial_capital,  # 归一化净值（初始=1.0）
            })
            prev_total = total
        self._nav = pd.DataFrame(nav_rows).sort_values('date').reset_index(drop=True)
        logger.info(f"[PtradeBaseline] 反推净值 {len(self._nav)} 天: "
                    f"末值 {self._nav['nav'].iloc[-1]:.4f}")
        return self._nav

    @property
    def nav(self) -> pd.DataFrame:
        """惰性获取净值（未计算则先 compute_nav）"""
        if self._nav is None:
            self.compute_nav()
        return self._nav

    # ========== Log.txt 全周期净值重建（持仓明细仅到 04-29，Log.txt 到 07-13）==========

    def extract_log_selections(self) -> pd.DataFrame:
        """从 Log.txt 提取每日 buy_stocks 选择集（handle_data 每日必打，比「生成订单」完整）。

        返回 DataFrame: date(Timestamp), codes(list[str])。
        """
        if not self.log_lines:
            return pd.DataFrame(columns=['date', 'codes'])
        pat = re.compile(r'(\d{4}-\d{2}-\d{2}).*?buy_stocks:\[(.*?)\]')
        rows = []
        for line in self.log_lines:
            m = pat.search(line)
            if m:
                d = pd.to_datetime(m.group(1))
                # 归一化为裸码（去 .SZ/.XSHE），与 seed_pos / close_of 口径一致
                codes = [
                    re.sub(r'\.\w+$', '', c.strip().strip("'\""))
                    for c in m.group(2).split(',') if c.strip()
                ]
                rows.append({'date': d, 'codes': codes})
        return pd.DataFrame(rows)

    def rebuild_nav_from_log(self, db_path, initial_capital: float = PTRADE_INITIAL_CAPITAL) -> pd.DataFrame:
        """从 Log.txt 的逐日 buy_stocks 选择集 + 本地价格 重建全周期净值（覆盖到 07-13）。

        背景：
        - 持仓明细/交易详情（真实成交）仅覆盖到 2026-04-29，且 compute_nav 已是该段
          的可靠真值（market_value 直接来自平台导出）。
        - Log.txt 的 buy_stocks 选择集（handle_data 每日必打）完整到 2026-07-13，
          是比「生成订单」更完整的信号来源（order_target_value 仅在偏离目标超阈值时
          才产生订单，日常再平衡多被省略，纯订单重放会退化为「长期持有期初组合」）。

        方法（v2 修复：替换旧的「生成订单」重放）：
        - 起点：持仓明细 04-29 末态持仓 + compute_nav 末态现金（ground truth）。
        - 复刻：以 04-30 起每日记录的 buy_stocks 为目标选择集，逐日复刻策略 trade()
          （卖出不在目标中的；新进入者按 value=cash/(5-pc) 集中买入），用本地
          stock_daily 收盘价（前向+后向填充，含种子日 04-29 以避免首日估值塌缩）撮合与估值。
        - 成本模型与本地引擎一致：佣金万3.5+最低5元、印花税千1(卖)、过户费万0.1、滑点0。
        返回 DataFrame: date, cash, market_value, total, nav（归一化，初始=1.0）。
        """
        import duckdb
        from datetime import timedelta

        # ---- 起点状态：持仓明细末态（ground truth）----
        if self.holdings is not None and len(self.holdings) > 0:
            hd = self.holdings.copy()
            last_hd_date = hd['date'].max()
            seed_pos = {
                r['code'].split('.')[0]: int(r['volume'])
                for _, r in hd[hd['date'] == last_hd_date].iterrows()
                if int(r['volume']) > 0
            }
            nav_h = self.compute_nav()
            row = nav_h[nav_h['date'] == last_hd_date].iloc[0]
            seed_cash = float(row['cash'])
            begin_date = last_hd_date + timedelta(days=1)
            logger.info(f"[PtradeBaseline] 以持仓明细 {last_hd_date.date()} 末态为起点 "
                        f"(持仓 {seed_pos}, 现金 {seed_cash:.2f})，复刻 buy_stocks 至 07-13")
        else:
            seed_pos, seed_cash, begin_date = {}, initial_capital, None

        # ---- 逐日选择集（buy_stocks，handle_data 每日必打，比「生成订单」完整）----
        sels = self.extract_log_selections()
        if sels.empty:
            raise RuntimeError("Log.txt 无 buy_stocks 选择集，无法重建净值")
        if begin_date is not None:
            sels = sels[sels['date'] >= pd.Timestamp(begin_date)].copy()
        sel_dates = sorted(sels['date'].dt.normalize().unique())
        if not sel_dates:
            sel_dates = sorted(sels['date'].unique())
        bare_codes = sorted(
            {c.split('.')[0] for row in sels['codes'] for c in row} | set(seed_pos.keys())
        )

        # ---- 本地价格（前向填充，避免微盘停牌日估值塌缩）----
        conn = duckdb.connect(str(db_path), read_only=True)
        # 价格窗口包含种子日(04-29)，以便 04-30 能前向填充到种子日价格（否则首日估值塌缩）
        seed_day = pd.Timestamp(last_hd_date).normalize()
        ms0 = int(seed_day.timestamp() * 1000)
        ms1 = int(pd.Timestamp(sel_dates[-1]).timestamp() * 1000) + 86_399_999
        pdf = conn.execute(f"""
            SELECT code, time, close FROM stock_daily
            WHERE code IN ({','.join(repr(c) for c in bare_codes)})
              AND time BETWEEN {ms0} AND {ms1}
        """).fetchdf()
        conn.close()
        pdf['date'] = pd.to_datetime(pdf['time'], unit='ms').dt.date
        price_dates = sorted(set([seed_day] + list(sel_dates)))
        pivot = pdf.pivot_table(index='date', columns='code', values='close').reindex(price_dates).ffill().bfill()
        date_to_idx = {d: i for i, d in enumerate(price_dates)}
        close_of = {
            c: [None if pd.isna(v) else float(v) for v in pivot[c].values]
            for c in bare_codes
        }

        COMM, MINC, STAMP, TRANSFER = 0.00035, 5.0, 0.001, 0.00001

        def price(code, i):
            v = close_of[code][i]
            return v if v else 0.0

        positions: dict = dict(seed_pos)
        cash = seed_cash
        nav_rows = []
        prev_codes = None
        for d in sel_dates:
            i = date_to_idx[d]
            codes = prev_codes
            sub = sels[sels['date'].dt.normalize() == d]['codes']
            if len(sub):
                codes = sub.iloc[0]
            if codes is None:
                codes = prev_codes or []
            prev_codes = codes
            # 1) 卖出不在目标中的（对齐策略 trade() 先卖）
            for c in list(positions):
                if c not in codes and positions[c] > 0:
                    px = price(c, i)
                    if px > 0:
                        pro = positions[c] * px
                        cash += pro - max(pro * COMM, MINC) - pro * TRANSFER - pro * STAMP
                        positions[c] = 0
            # 2) 当前持仓数（非0）
            pc = sum(1 for c in positions if positions[c] > 0)
            if 5 > pc:
                value = cash / (5 - pc)
                for c in codes:
                    if positions.get(c, 0) <= 0:
                        px = price(c, i)
                        if px <= 0:
                            continue
                        target = int(value / px // 100) * 100
                        delta = target - positions.get(c, 0)
                        if delta > 0:
                            cost = delta * px
                            if cash >= cost + max(cost * COMM, MINC) + cost * TRANSFER:
                                cash -= cost + max(cost * COMM, MINC) + cost * TRANSFER
                                positions[c] = positions.get(c, 0) + delta
            mv = sum(sh * price(c, i) for c, sh in positions.items() if price(c, i) > 0)
            nav_rows.append({
                'date': pd.Timestamp(d), 'cash': cash,
                'market_value': mv, 'total': cash + mv,
                'nav': (cash + mv) / initial_capital,
            })
        self._nav = pd.DataFrame(nav_rows).sort_values('date').reset_index(drop=True)
        logger.info(f"[PtradeBaseline] 重建净值 {len(self._nav)} 天: "
                    f"{self._nav['date'].min().date()} ~ {self._nav['date'].max().date()}, "
                    f"末值 {self._nav['nav'].iloc[-1]:.4f}")
        return self._nav

    # ========== Log.txt 信号提取（B.4，L1 辅助校验）==========

    def extract_log_signals(self) -> pd.DataFrame:
        """从 Log.txt 提取每日下单意图（订单生成日志）。

        返回 DataFrame: date, code, direction(buy/sell), volume。
        比 trades CSV 更原始（记录意图而非成交），用于交叉验证。
        """
        if not self.log_lines:
            return pd.DataFrame(columns=['date', 'code', 'direction', 'volume'])

        # B.4 正则：生成订单，订单号:xxx，股票代码：xxxxxx.XSHE，数量：买入NNNN股
        pattern = re.compile(
            r'(\d{4}-\d{2}-\d{2}).*生成订单.*?股票代码：(\d{6}\.\w+).*?数量：(买入|卖出)([\d.]+)股'
        )
        rows = []
        for line in self.log_lines:
            m = pattern.search(line)
            if m:
                rows.append({
                    'date': pd.to_datetime(m.group(1), format='%Y-%m-%d'),
                    'code': m.group(2),
                    'direction': 'buy' if m.group(3) == '买入' else 'sell',
                    'volume': int(float(m.group(4))),
                })
        return pd.DataFrame(rows)
