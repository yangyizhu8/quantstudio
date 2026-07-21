"""
纯 DuckDB 驱动的 A 股回测引擎
验证 QuantStudio 数据管线质量

用法：
    engine = BacktestEngine(db_path, strategy, start, end, capital)
    result = engine.run()
    result.report()

特点：
- 数据 100% 来自 DuckDB（不依赖 xtquant/tushare/baostock）
- 模拟 T+1、涨跌停、佣金印花税、滑点
- 逐日 bar 驱动（日线级别）
"""
from __future__ import annotations

import logging
import datetime
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)
from quantstudio._paths import db_path


@dataclass
class EngineConfig:
    """引擎运行配置（A1，D1）— 由 CLI/GUI 启动时显式注入，不做路径推导。

    设计意图：源码可在任意机器/容器运行，迁移时只需改入口（CLI/GUI）传入的路径，
    不需要改 ptrade_api.py / backtest_engine.py 源码。
    唯一允许推导项目根的地方是 default() 兜底工厂（CLI 未显式传入时用）。
    """
    db_path: Path
    output_dir: Path
    research_dir: Path
    # 权威数据源口径（D10 决策：tushare 为本地引擎/API 封装层的权威源）。
    # 与 PtradeBaseline.source_id 做一致性校验，防止沉默漂移。
    # 已知口径：tushare（本地 DuckDB）/ xtquant（交易所直连源）/ juyuan（Ptrade 平台聚源）。
    data_source: str = "tushare"

    @classmethod
    def default(cls) -> "EngineConfig":
        """兜底默认值（仅 CLI 未显式传入时用）。

        这是整个 backtest 包内唯一允许用 Path(__file__) 推导项目根的地方。
        生产路径应由入口（run_ptrade_strategy.py / GUI / 测试）显式构造并传入。
        """
        root = Path(__file__).resolve().parents[2]  # quantstudio/backtest/ → 项目根
        return cls(
            db_path=db_path(),
            output_dir=root / "output",
            research_dir=root / "output" / "research",
        )


@dataclass
class Position:
    """持仓"""
    code: str          # QMT 格式 600000.SH
    volume: int = 0    # 持仓股数
    avg_cost: float = 0.0  # 持仓均价
    can_sell: int = 0  # 可卖股数（T+1：今日买入的不可卖）


@dataclass
class Order:
    """订单对象（A3，D3）— order_target_value/order/order_value/order_target 的返回值。

    设计意图：
    - 老策略不检查返回值也能跑（__bool__ + __str__ 兼容）
    - 新策略可检查 status 感知失败（涨跌停阻断/资金不足）
    - 预留 partial/pending 状态，为 Phase E 实盘部分成交铺路
    """
    order_id: str
    security: str
    direction: str           # "buy"/"sell"
    # 金额口径（order_target_value/order_value 用）
    target: float = 0.0      # 目标金额
    filled: float = 0.0      # 实际成交金额
    # 数量口径（order/order_target 用；Phase E 实盘部分成交时 filled < target）
    target_amount: int = 0   # 目标股数
    filled_amount: int = 0   # 实际成交股数
    price: float = 0.0       # 成交价（含滑点）
    status: str = "rejected" # "filled"/"rejected"/"partial"/"pending"(Phase E 实盘预留)
    reason: str = ""         # "limit_up_blocked"/"limit_down_blocked"/"insufficient_cash"/"no_price"
    created_dt: str = ""     # 下单日期

    def __bool__(self):
        """老策略 if order: 判定为是否成交（兼容旧代码不检查返回值的写法）"""
        return self.filled > 0 or self.filled_amount > 0

    def __str__(self):
        if self.status == "filled":
            return f"Order(filled {self.filled_amount}@{self.price:.2f} {self.security})"
        return f"Order({self.status}: {self.reason} {self.security})"


@dataclass
class Account:
    """账户"""
    # 默认本金对齐 Ptrade 平台回测默认 10 万（与 BacktestEngine.capital 默认一致，
    # 消除"忘传 capital 误用 100 万"陷阱）
    cash: float = 100_000.0
    positions: Dict[str, Position] = field(default_factory=dict)  # code → Position

    @property
    def market_value(self) -> float:
        return sum(p.volume * p.avg_cost for p in self.positions.values())  # 用成本估算

    def market_value_at_price(self, prices: Dict[str, float]) -> float:
        """用最新价计算持仓市值"""
        return sum(p.volume * prices.get(p.code, p.avg_cost)
                   for p in self.positions.values())

    @property
    def total_asset(self) -> float:
        return self.cash + self.market_value

    def total_asset_at_price(self, prices: Dict[str, float]) -> float:
        return self.cash + self.market_value_at_price(prices)


@dataclass
class TradeCost:
    """交易成本（对齐 Ptrade 平台回测实际费率）"""
    commission_rate: float = 0.00035   # 佣金费率（万3.5，对齐 Ptrade）
    min_commission: float = 5.0        # 最低佣金
    stamp_tax_rate: float = 0.001      # 印花税（卖出单向，千1，对齐 Ptrade）
    transfer_fee_rate: float = 0.00001 # 过户费（万0.1）
    slippage_rate: float = 0.001       # 滑点
    fixed_slippage: float = 0.0        # absolute yuan/share slippage


# 统一交易成本常量（所有入口共用，保证成本口径一致）
# 费率对齐 Ptrade 平台回测实际值：佣金万3.49、印花税千1（卖出单向）、滑点0
DEFAULT_TRADE_COST = TradeCost(
    commission_rate=0.00035, min_commission=5.0,
    stamp_tax_rate=0.001, transfer_fee_rate=0.00001,
    slippage_rate=0.0,
)


@dataclass
class BacktestResult:
    """回测结果"""
    nav_history: List[Dict] = field(default_factory=list)    # 净值曲线
    trade_records: List[Dict] = field(default_factory=list)  # 交易记录
    metrics_summary: Dict = field(default_factory=dict)      # 统一指标来源（GUI / CLI 共用）
    round_trips: List[Dict] = field(default_factory=list)    # 配对后的 round-trip 交易明细

    def report(self):
        if not self.nav_history:
            print("无回测结果")
            return

        df = pd.DataFrame(self.nav_history)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')

        assets = df['nav'].astype(float)
        initial_asset = float(self.initial_capital or assets.iloc[0])
        final_asset = float(assets.iloc[-1])
        summary = self.metrics_summary or {}

        total_return = float(summary.get('strategy_return_pct', (final_asset / initial_asset - 1) * 100))
        annual_return = float(summary.get('annual_return_pct', ((final_asset / initial_asset) ** (250 / len(df)) - 1) * 100))
        bench_return = float(summary.get('benchmark_return_pct', (df['benchmark'].iloc[-1] / df['benchmark'].iloc[0] - 1) * 100 if 'benchmark' in df else 0.0))
        max_drawdown = float(summary.get('max_drawdown_pct', 0.0))
        sharpe = float(summary.get('sharpe_ratio', 0.0))
        win_rate = float(summary.get('win_rate_pct', 0.0))
        profit_count = int(summary.get('profit_count', 0))
        loss_count = int(summary.get('loss_count', 0))

        print("=" * 60)
        print("回测绩效报告")
        print("=" * 60)
        print(f"回测区间:     {df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}")
        print(f"交易日数:     {len(df)} 天")
        print(f"初始资金:     {initial_asset:,.2f}")
        print(f"最终净值:     {final_asset:,.2f}")
        print(f"总收益率:     {total_return:.2f}%")
        print(f"年化收益率:   {annual_return:.2f}%")
        print(f"基准(沪深300): {bench_return:.2f}%")
        print(f"超额收益:     {total_return - bench_return:.2f}%")
        print(f"最大回撤:     {max_drawdown:.2f}%")
        print(f"夏普比率:     {sharpe:.2f}")
        print(f"交易笔数:     {len(self.trade_records)}")

        if self.trade_records:
            print(f"胜率:         {win_rate:.1f}%")
            print(f"盈利笔数:     {profit_count}")
            print(f"亏损笔数:     {loss_count}")

        print()
        df['month'] = df.index.to_period('M')
        monthly = df.groupby('month')['nav'].agg(lambda x: (x.iloc[-1] / x.iloc[0] - 1) * 100)
        print("月度收益率:")
        for m, r in monthly.items():
            print(f"  {m}: {r:+.2f}%")
        print("=" * 60)


class BacktestEngine:
    """纯 DuckDB 驱动的回测引擎（仅支持 Ptrade 模式，custom 模式已废弃）

    策略接口（Ptrade 函数式，经 ptrade_import 注入全部 API）：
        initialize(context)           → 初始化（仅回测开始调用一次）
        before_trading_start(ctx,data)→ 盘前选股
        handle_data(ctx,data)         → 盘中交易（order_target_value 即时成交）
        after_trading_end(ctx,data)   → 收盘后处理

    成交：order_target_value/order/order_value/order_target 均即时成交，
    涨跌停用 shared_ashare_rules 精确检查，T+1 规则由 Account 控制。
    """

    def __init__(self, db_path: str, strategy, start: str, end: str,
                 capital: float = 100_000, cost: TradeCost = None,  # 默认 10万，对齐 Ptrade 平台回测默认资金
                 strategy_type: str = "ptrade",
                 min_rebalance_pct: float = 0.005,
                 progress_callback=None,
                 config: Optional[EngineConfig] = None,
                 match_price_mode: str = "close",
                 providers=None):
        """strategy_type: 'ptrade' = Ptrade 原版策略（唯一支持模式，即时成交 + 精确涨跌停）。

        注：custom 自定义信号模式已于 2026-07-19 废弃（不保证撮合精度、无涨跌停阻断、
        绕过统一数据层），不再接受。若传入非 'ptrade' 值，引擎强制按 ptrade 模式运行并告警。
        min_rebalance_pct: 存量调仓最小阈值（默认0.5%），低于此差额的微调自动跳过。
        progress_callback: 可选回调 fn(current, total, date_str)，GUI进度条用。
        config: 引擎运行配置（A1）。优先级：config > db_path > EngineConfig.default()。
            生产路径应由入口（CLI/GUI）显式构造传入；测试可传自定义 config 指向临时库。
        match_price_mode: 撮合价口径（A2，消除未来函数风险）：
            - "close"（默认）：所有 order 按当日收盘价+滑点即时成交。⚠️ 未来函数风险：
              策略在 before_trading_start 能读到 data[stock].close（当日收盘），若据此下单
              = 用当天收盘价成交。保持默认是为兼容历史回测结果。
            - "open"：撮合价用当日开盘价（策略仍能读 close 做信号，但成交用 open）。
            - "next_open"：撮合价用次日开盘价（最贴 Ptrade 实盘：T 日决策、T+1 开盘成交，
              彻底消除未来函数）。Ptrade 对齐对照时推荐此模式。
            详见 docs/interface-contract.md 第 1 节。"""
        # A1: 统一从 EngineConfig 取路径，ptrade_api.py 通过 self.config 消费
        if config is not None:
            self.config = config
        elif db_path is not None:
            # 向后兼容：仅传 db_path 时，输出/研究目录用 default 兜底
            base = EngineConfig.default()
            self.config = EngineConfig(
                db_path=Path(db_path),
                output_dir=base.output_dir,
                research_dir=base.research_dir,
            )
        else:
            self.config = EngineConfig.default()
        # db_path 保留为属性供旧代码引用（内部统一用 self.config.db_path）
        self.db_path = str(self.config.db_path)
        self.strategy = strategy
        self.start = start
        self.end = end
        # Copy the cost object per run. Strategy-level set_commission/set_slippage
        # must never mutate DEFAULT_TRADE_COST or leak into the next backtest.
        self.cost = replace(cost or TradeCost())
        self.min_rebalance_pct = min_rebalance_pct
        # custom 模式已废弃：任何非 ptrade 入参强制回退，避免误用保真黑洞
        if strategy_type != "ptrade":
            logger.warning(f"[BacktestEngine] strategy_type={strategy_type!r} 已废弃，"
                           f"强制使用 ptrade 模式（即时成交 + 精确涨跌停）")
        self.strategy_type = "ptrade"
        # A2: 撮合价模式
        if match_price_mode not in ("close", "open", "next_open"):
            raise ValueError(f"match_price_mode 必须是 close/open/next_open， got {match_price_mode!r}")
        self.match_price_mode = match_price_mode
        self.account = Account(cash=capital)
        self.result = BacktestResult()
        if providers is None:
            from .providers.base import DataProviderRegistry
            providers = DataProviderRegistry.from_duckdb(self.config.db_path)
        self._providers = providers
        self._ptrade_api = None
        self._progress_callback = progress_callback
        self._strategy_name = "strategy"
        self._initial_capital = capital
        self.result.initial_capital = float(capital)

    def run(self) -> BacktestResult:
        """Execute the daily-bar backtest."""
        from .ptrade_api import _api, Context, Portfolio

        _api.reset_session()
        trade_days = self._get_trade_days()
        if not trade_days:
            raise ValueError(f"No trading days in backtest range: {self.start} ~ {self.end}")
        logger.info(f"[Backtest] {len(trade_days)} trading days: {trade_days[0]} ~ {trade_days[-1]}")

        if self.strategy_type == "ptrade" and 'initialize' in self.strategy:
            first_day = trade_days[0].strftime('%Y-%m-%d')
            init_ctx = Context(first_day, first_day, Portfolio(self.account.cash, {}))
            self._ptrade_context = init_ctx
            _api.attach(self, None, None, first_day, first_day, {})
            try:
                self.strategy['initialize'](init_ctx)
            except Exception as e:
                logger.error(f"[Ptrade] initialize error: {e}")

        # set_benchmark() may run in initialize(); benchmark starts at previous close.
        benchmark_code = getattr(_api, '_benchmark', '000300') or '000300'
        first_trade_day = trade_days[0].strftime('%Y-%m-%d')
        benchmark_base_date = self._providers.calendar.get_trading_day(first_trade_day, offset=-1)
        benchmark_start = (benchmark_base_date.strftime('%Y-%m-%d')
                           if benchmark_base_date is not None else first_trade_day)
        benchmark_raw = self._providers.market.get_benchmark(
            benchmark_code, benchmark_start, trade_days[-1].strftime('%Y-%m-%d'))
        first_bench = benchmark_raw.get(benchmark_start) or benchmark_raw.get(first_trade_day, 1.0)

        total_days = len(trade_days)
        for i, day in enumerate(trade_days):
            day_str = day.strftime('%Y-%m-%d')
            if self._progress_callback:
                self._progress_callback(i + 1, total_days, day_str)

            if i > 0:
                prev_day = trade_days[i - 1]
            else:
                prev_date = self._providers.calendar.get_trading_day(day_str, offset=-1)
                prev_day = (pd.Timestamp(prev_date, tz='Asia/Shanghai').to_pydatetime()
                            if prev_date is not None else day)

            if i > 0 and getattr(self, '_last_curr_data', None) is not None:
                prev_data = self._last_curr_data
            else:
                prev_data = self._get_daily_data(prev_day)
            curr_data = self._get_daily_data(day)
            self._last_curr_data = curr_data
            if len(curr_data) == 0:
                logger.debug(f"[Backtest] {day_str} no data, skipped")
                continue

            match_prices = self._build_match_prices(curr_data, trade_days, i)
            prices = {self._to_qmt(c): v for c, v in zip(curr_data['code'], curr_data['close'])}

            positions_snapshot = {}
            for code, pos in self.account.positions.items():
                if pos.volume > 0:
                    positions_snapshot[code] = {
                        'volume': pos.volume,
                        'can_use_volume': pos.can_sell,
                        'open_price': pos.avg_cost,
                        'avg_price': pos.avg_cost,
                        'current_price': prices.get(code, pos.avg_cost),
                    }

            for pos in self.account.positions.values():
                pos.can_sell = pos.volume

            prev_day_str = prev_day.strftime('%Y-%m-%d')
            self._run_ptrade_strategy(day, prev_day, match_prices, positions_snapshot,
                                      day_str, prev_day_str, curr_data, prev_data)

            if self.strategy_type == "ptrade" and 'after_trading_end' in self.strategy:
                try:
                    self.strategy['after_trading_end'](
                        self._ptrade_context, self._build_data_dict(curr_data, day_str))
                except Exception as e:
                    logger.debug(f"[Ptrade] after_trading_end error: {e}")

            nav = self.account.total_asset_at_price(prices)
            bench_close = benchmark_raw.get(day_str, first_bench)
            bench_nav = bench_close / first_bench * 100 if first_bench else 100.0
            self.result.nav_history.append({
                'date': day_str,
                'nav': nav,
                'cash': self.account.cash,
                'market_value': self.account.market_value_at_price(prices),
                'benchmark': bench_nav,
                'positions': len([p for p in self.account.positions.values() if p.volume > 0]),
            })

        logger.info(f"[Backtest] completed: {len(self.result.nav_history)} days")
        from .ptrade_metrics import calculate_ptrade_like_metrics
        metrics = calculate_ptrade_like_metrics(self.result, self)
        self.result.metrics_summary = metrics.summary
        self.result.round_trips = metrics.round_trips

        from .result_exporter import export_result
        output_dir = export_result(self.result, self)
        logger.info(f"[Backtest] result exported: {output_dir}")
        return self.result, output_dir

    # ===================== Immediate execution =====================

    def _immediate_execute(self, security: str, target_value=None, shares=None,
                           prices=None, date="", curr_data=None) -> Order:
        """即时成交（Ptrade 模式核心）— 被 PtradeAPI.order_target_value/order 直接调用。
        调用后 Account 立即更新，策略下一行可见最新状态。
        A3: 返回 Order 对象（成功 filled/失败 rejected），策略可检查 status 感知失败。"""
        # 裸码 → QMT 格式
        bare = str(security).split(".")[0]
        code = self._to_qmt(bare)
        price = (prices or {}).get(code, 0)
        if price <= 0:
            return Order(order_id=f"ord_{code}_{date}", security=security, direction="unknown",
                         status="rejected", reason="no_price", created_dt=date)

        # 涨跌停检查（使用 shared_ashare_rules 精确规则）
        # is_price_limit_blocked 的 direction 参数：1=买入(查涨停阻断)，0/其他=卖出(查跌停阻断)
        from .libs.shared_ashare_rules import is_price_limit_blocked, round_to_lot
        pct_chg = self._get_pct_chg(code, curr_data, date)
        if target_value is not None:
            direction_int = 1 if target_value > 0 else 0
            direction = "buy" if target_value > 0 else "sell"
        elif shares is not None:
            direction_int = 1 if shares > 0 else 0
            direction = "buy" if shares > 0 else "sell"
        else:
            return Order(order_id=f"ord_{code}_{date}", security=security, direction="unknown",
                         status="rejected", reason="no_instruction", created_dt=date)
        if is_price_limit_blocked(code, direction_int, pct_chg):
            reason = "limit_up_blocked" if direction_int == 1 else "limit_down_blocked"
            action = "买入涨停" if direction_int == 1 else "卖出跌停"
            logger.debug(f"[即时执行] {code} {action}被阻断 (pct_chg={pct_chg:.4f})")
            return Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                         status="rejected", reason=reason, created_dt=date)

        # 执行交易
        filled_vol = 0
        fill_price = 0.0
        if target_value is not None:
            if target_value == 0:
                # 清仓 → 始终执行
                filled_vol, fill_price = self._execute_sell(code, price, sell_all=True, date=date, curr_data=curr_data)
            else:
                pos = self.account.positions.get(code)
                current_value = (pos.volume * price) if pos and pos.volume > 0 else 0
                delta = target_value - current_value
                if current_value > 0:
                    # 存量调仓 → 双重过滤（百分比阈值 + 整手）
                    if abs(delta) / current_value < self.min_rebalance_pct:
                        logger.debug(f"[即时执行] 跳过微调: {code} delta={delta:.0f} "
                                     f"({abs(delta)/current_value*100:.2f}% < {self.min_rebalance_pct*100}%)")
                        # 微调跳过视为未成交（非失败，策略可据此判断）
                        return Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                                     target=abs(delta), status="rejected", reason="below_rebalance_threshold",
                                     created_dt=date)
                if delta > 0:
                    filled_vol, fill_price = self._execute_buy(code, price, buy_value=delta, date=date, curr_data=curr_data)
                elif delta < 0 and pos and pos.can_sell > 0:
                    filled_vol, fill_price = self._execute_sell(code, price, sell_value=abs(delta), date=date, curr_data=curr_data)
        elif shares is not None:
            if shares > 0:
                filled_vol, fill_price = self._execute_buy(code, price, buy_shares=shares, date=date, curr_data=curr_data)
            elif shares < 0:
                filled_vol, fill_price = self._execute_sell(code, price, sell_shares=abs(shares), date=date, curr_data=curr_data)

        # 构造返回 Order
        status = "filled" if filled_vol > 0 else "rejected"
        reason = "" if filled_vol > 0 else "insufficient_cash_or_rounding"
        return Order(
            order_id=f"ord_{code}_{date}",
            security=security,
            direction=direction,
            target=abs(target_value) if target_value else 0.0,
            filled=filled_vol * fill_price,
            target_amount=abs(shares) if shares else 0,
            filled_amount=filled_vol,
            price=fill_price,
            status=status,
            reason=reason,
            created_dt=date,
        )

    def _apply_slippage(self, price: float, direction: str) -> float:
        """Apply strategy-configured slippage below the public API boundary."""
        fixed = max(0.0, float(getattr(self.cost, "fixed_slippage", 0.0)))
        ratio = max(0.0, float(getattr(self.cost, "slippage_rate", 0.0)))
        if direction == "buy":
            return max(0.0, price * (1 + ratio) + fixed)
        return max(0.0, price * (1 - ratio) - fixed)

    def _execute_buy(self, code, price, buy_value=None, buy_shares=None, date="", curr_data=None):
        """即时买入。返回成交股数（0 表示未成交），供 _immediate_execute 构造 Order。"""
        from .libs.shared_ashare_rules import round_to_lot
        fill_price = self._apply_slippage(price, "buy")

        if buy_shares is not None:
            target_vol = round_to_lot(buy_shares, 100)
        else:
            max_vol = int(buy_value / fill_price)
            target_vol = round_to_lot(max_vol, 100)
        if target_vol <= 0:
            return 0, fill_price

        cost_amount = target_vol * fill_price
        commission = max(cost_amount * self.cost.commission_rate, self.cost.min_commission)
        transfer_fee = cost_amount * self.cost.transfer_fee_rate
        total_cost = cost_amount + commission + transfer_fee

        # Ptrade 语义：资金不足时直接拒单，不做“缩单后部分成交”。
        # 这能避免轮动/ETF 动量类策略在同一 bar 内因为旧仓未卖出而用残余现金误买出一个很小的仓位，
        # 从而破坏平台行为一致性。显式股数单与目标市值单统一按拒单处理。
        if total_cost > self.account.cash:
            logger.warning(f"当前账户资金不足，{code}下单失败")
            return 0, fill_price

        self.account.cash -= total_cost
        pos = self.account.positions.get(code)
        if pos:
            new_total = pos.volume + target_vol
            pos.avg_cost = (pos.avg_cost * pos.volume + fill_price * target_vol) / new_total
            pos.volume = new_total
            # T+1: 今日买入的 can_sell 不增加
        else:
            self.account.positions[code] = Position(
                code=code, volume=target_vol, avg_cost=fill_price, can_sell=0)

        self.result.trade_records.append({
            'date': date, 'code': code, 'action': 'buy',
            'volume': target_vol, 'price': fill_price,
            'commission': commission, 'tax': 0, 'pnl': 0,
        })
        logger.debug(f"[即时买入] {code} {target_vol}股@{fill_price:.2f} 成本{total_cost:.2f}")
        return target_vol, fill_price

    def _execute_sell(self, code, price, sell_all=False, sell_value=None,
                      sell_shares=None, date="", curr_data=None):
        """即时卖出。返回 (成交股数, 成交价)，供 _immediate_execute 构造 Order。"""
        from .libs.shared_ashare_rules import round_to_lot
        pos = self.account.positions.get(code)
        if not pos or pos.can_sell <= 0:
            return 0, price

        if sell_all:
            target_vol = pos.can_sell
        elif sell_shares is not None:
            target_vol = min(round_to_lot(sell_shares, 100), pos.can_sell)
        elif sell_value is not None:
            max_vol = int(sell_value / price)
            target_vol = min(round_to_lot(max_vol, 100), pos.can_sell)
        else:
            return 0, price
        if target_vol <= 0:
            return 0, price

        fill_price = self._apply_slippage(price, "sell")
        proceeds = target_vol * fill_price
        commission = max(proceeds * self.cost.commission_rate, self.cost.min_commission)
        stamp_tax = proceeds * self.cost.stamp_tax_rate
        transfer_fee = proceeds * self.cost.transfer_fee_rate
        net_proceeds = proceeds - commission - stamp_tax - transfer_fee

        pnl = (fill_price - pos.avg_cost) * target_vol - commission - stamp_tax - transfer_fee

        self.account.cash += net_proceeds
        pos.volume -= target_vol
        pos.can_sell -= target_vol
        if pos.volume <= 0:
            pos.avg_cost = 0
            pos.can_sell = 0

        self.result.trade_records.append({
            'date': date, 'code': code, 'action': 'sell',
            'volume': target_vol, 'price': fill_price,
            'commission': commission, 'tax': stamp_tax, 'pnl': pnl,
        })
        logger.debug(f"[即时卖出] {code} {target_vol}股@{fill_price:.2f} 净收入{net_proceeds:.2f}")
        return target_vol, fill_price

    def _get_pct_chg(self, code, curr_data, date):
        """获取当日涨跌幅"""
        if curr_data is None:
            return 0.0
        bare = code.split(".")[0] if "." in code else code
        row = curr_data[curr_data['code'] == bare] if 'code' in curr_data.columns else pd.DataFrame()
        if len(row) > 0:
            close = row.iloc[0].get('close', 0)
            preclose = row.iloc[0].get('preClose', 0)
            if preclose and preclose > 0:
                return (close - preclose) / preclose
        return 0.0

    def _build_match_prices(self, curr_data, trade_days, i):
        """A2: 构建 order 撮合价字典（match_prices）。

        语义：
        - close（默认）：当日收盘价。⚠️ 未来函数风险（策略可读当日 close 再按 close 成交）。
        - open：当日开盘价。策略仍能读 close 做信号，但成交用 open。
        - next_open：次日开盘价。最贴 Ptrade 实盘（T 日决策、T+1 开盘成交），
          彻底消除未来函数。末日（无次日数据）回退到当日 close。

        注意：记账价（净值/持仓估值）不在此方法，始终用当日收盘（见 run() 的 prices）。
        """
        if self.match_price_mode == "open":
            col = "open"
        elif self.match_price_mode == "next_open":
            # 预取下一个交易日的开盘价
            if i + 1 < len(trade_days):
                next_data = self._get_daily_data(trade_days[i + 1])
                if len(next_data) > 0:
                    return {self._to_qmt(c): v
                            for c, v in zip(next_data['code'], next_data['open'])}
            # 末日或无数据：回退到当日收盘（记录日志便于排查）
            logger.debug(f"[A2] next_open 无次日数据（i={i}），回退当日 close")
            col = "close"
        else:  # close
            col = "close"
        return {self._to_qmt(c): v for c, v in zip(curr_data['code'], curr_data[col])}

    def refresh_portfolio(self, prices):
        """原地更新 context.portfolio（补充项②：不重建 context，只更新属性）。
        PtradeAPI 每次成交后调用，策略持有的 context 引用不变。"""
        from .ptrade_api import Portfolio
        ptrade_positions = self._get_ptrade_positions(prices)
        if hasattr(self, '_ptrade_context') and self._ptrade_context is not None:
            self._ptrade_context.portfolio = Portfolio(self.account.cash, ptrade_positions)

    def _build_data_dict(self, curr_data, day_str):
        """构建 Ptrade data 字典（供 after_trading_end 使用）"""
        from .ptrade_api import DataDict
        data = DataDict()
        data.set_curr_data(curr_data, day_str)
        return data

    # ===================== Ptrade 策略执行 =====================

    def _run_ptrade_strategy(self, day, prev_day, prices, positions_snapshot,
                             day_str, prev_day_str, curr_data, prev_data):
        """以 Ptrade 即时执行模式运行策略。
        策略调用 order_target_value() 时立即成交，Account 即时更新。
        context 原地更新（策略持有的引用不变）。"""
        from .ptrade_api import _api, Context, Portfolio, DataDict, BarData

        # 构建 Ptrade context + data
        ptrade_positions = self._get_ptrade_positions(prices)
        portfolio = Portfolio(self.account.cash, ptrade_positions)
        ctx = Context(day_str, prev_day_str, portfolio)

        # 保存 context 引用（供 refresh_portfolio 原地更新）
        self._ptrade_context = ctx

        # 构建 data 字典（惰性：不预先构建 5000 个 BarData，只在 data[code] 被访问时构建）
        data = DataDict()
        data.set_curr_data(curr_data, day_str)

        # 注入 API（含 prices + curr_data 供即时执行使用）
        _api.attach(self, curr_data, prev_data, day_str, prev_day_str, prices)

        # 调用 Ptrade 策略函数（即时执行：order 直接成交）
        try:
            if 'before_trading_start' in self.strategy:
                self.strategy['before_trading_start'](ctx, data)
        except Exception as e:
            logger.error(f"[Ptrade] before_trading_start 错误: {e}")
        try:
            if 'handle_data' in self.strategy:
                self.strategy['handle_data'](ctx, data)
            # run_daily 注册任务与 handle_data 可并存；日线引擎每天均执行一次。
            daily_tasks = getattr(_api, '_daily_tasks', [])
            for func, _time in daily_tasks:
                func(ctx)
        except Exception as e:
            logger.error(f"[Ptrade] handle_data 错误: {e}")

        # 即时执行模式：不需要返回信号，交易已在策略执行过程中即时完成

    def _get_ptrade_positions(self, prices: dict | None = None) -> dict:
        """Expose positions exactly as PTrade strategy/CSV containers do.

        Keys use .SS/.SZ and the returned mapping deliberately keeps normal dict
        membership semantics.  Four-letter aliases remain supported by get_position()
        and market-data containers, but making this mapping alias-aware changes real
        strategy control flow (notably the aligned ETF momentum strategy).
        """
        from .ptrade_api import Position, _api
        positions = {}
        prices = prices or {}
        for code, pos in self.account.positions.items():
            if pos.volume > 0:
                bare = code.split(".")[0]
                ptrade_code = _api._to_ptrade_code(bare)
                current_price = prices.get(code, pos.avg_cost)
                positions[ptrade_code] = Position(
                    sid=ptrade_code, volume=pos.volume, avg_cost=pos.avg_cost,
                    current_price=current_price)
        return positions

    # ===================== DuckDB 数据查询 =====================

    def _get_trade_days(self) -> list:
        """获取回测区间的交易日列表"""
        return self._providers.calendar.get_trade_days(self.start, self.end)

    def _get_daily_data(self, day: datetime.datetime) -> pd.DataFrame:
        """获取某日的全市场日线数据（含 is_st_reliable / is_delisting_risk 等 ST 字段）"""
        return self._providers.market.get_daily_snapshot(day.strftime('%Y-%m-%d'))

    def _get_benchmark(self, trade_days: list, benchmark_code: str = '000300') -> dict:
        """获取基准指数数据（基准代码由 set_benchmark 设定，默认沪深300）"""
        try:
            return self._providers.market.get_benchmark(
                benchmark_code, trade_days[0].strftime('%Y-%m-%d'),
                trade_days[-1].strftime('%Y-%m-%d'))
        except Exception as e:
            logger.warning(f"[Backtest] 基准数据加载失败: {e}")
            return {}

    @staticmethod
    def _to_qmt(bare_code: str) -> str:
        """Normalize any supported alias to the engine's QMT-style suffix."""
        from .libs.security_code_rules import normalize_to_qmt
        return normalize_to_qmt(bare_code)
