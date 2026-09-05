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

# PR4 决策4（per-code 扩展，docs/etf-t0-per-code-design.md §4）：
# etf_basic.fund_type ∈ T0_FUND_TYPES → ETF T+0（买入当日可卖）；equity → T+1；
# 未知代码（不在 etf_basic，如 LOF）→ fail-closed T+1。
T0_FUND_TYPES = frozenset({"qdii", "gold", "commodity", "bond", "money"})


def _prefer_project_data_db(project_root: Path, configured_db: Path) -> Path:
    """Prefer the current project's DuckDB for local backtests.

    Strategy code remains storage-isolated and receives data through providers.
    An explicitly packaged project database at ``<project>/data/quantstudio.db``
    wins; configured/environment data roots remain the fallback for deployments
    where the project-local database is absent.
    """
    project_db = Path(project_root) / "data" / "quantstudio.db"
    return project_db.resolve() if project_db.exists() else Path(configured_db)


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
    # G1-I: basket 再平衡开关（设计 v2 §3.2）。默认 "legacy"（next_open 单订单队列，不变）。
    # "callback_basket" 仅在 daily-bar-v1 + next_open 下激活 → 0.4.0-next_open_basket 语义。
    # 通过显式开关隔离而非自动检测，保证 close/open 与 legacy next_open 零回归。
    rebalance_mode: str = "legacy"

    @classmethod
    def default(cls) -> "EngineConfig":
        """兜底默认值（仅 CLI 未显式传入时用）。

        这是整个 backtest 包内唯一允许用 Path(__file__) 推导项目根的地方。
        生产路径应由入口（run_ptrade_strategy.py / GUI / 测试）显式构造并传入。
        """
        root = Path(__file__).resolve().parents[2]  # quantstudio/backtest/ → 项目根
        return cls(
            db_path=_prefer_project_data_db(root, db_path()),
            output_dir=root / "output",
            research_dir=root / "output" / "research",
        )


@dataclass
class Position:
    """持仓"""
    code: str          # QMT 格式 600000.SH
    volume: int = 0    # 持股股数
    avg_cost: float = 0.0  # 持仓均价
    can_sell: int = 0  # 可卖股数（T+1：今日买入的不可卖）
    # PR2: next_open pending 卖单预扣股数。close/open 模式恒为 0（隔离契约）。
    # get_positions() 返回的 enable_amount = can_sell - pending_sell_shares，
    # 让 T 日策略看到真实可卖量，避免超卖穿越。T+1 drain 成交则 pending_sell_shares
    # 归零并正式扣减 volume；拒单/expire/cancel 则原路归还。
    pending_sell_shares: int = 0


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
    status: str = "rejected" # "filled"/"rejected"/"partial"/"pending"(PR2 next_open + Phase E 实盘预留)
    reason: str = ""         # "limit_up_blocked"/"limit_down_blocked"/"insufficient_cash"/"no_price"
    created_dt: str = ""     # 下单日期（next_open: T 日信号日）
    # PR2: next_open 成交日（T+1）。close/open 路径留空，向后兼容。
    # 主计划 7.13 "正确记录 created_dt 和 filled_dt"。
    filled_dt: str = ""
    # G1-I audit-fix 阻断3: 所属 basket id（运行时 Order 可见）。None = 独立订单（legacy）。
    basket_id: Optional[str] = None

    def __bool__(self):
        """老策略 if order: 判定为是否成交（兼容旧代码不检查返回值的写法）"""
        return self.filled > 0 or self.filled_amount > 0

    def __str__(self):
        if self.status == "filled":
            return f"Order(filled {self.filled_amount}@{self.price:.2f} {self.security})"
        return f"Order({self.status}: {self.reason} {self.security})"


@dataclass
class PendingOrder:
    """PR2: next_open 延迟订单队列项。生命周期 created → pending → filled/rejected/expired/cancelled。

    T 日策略下单 → _create_pending_order 创建 PendingOrder(status=pending) 并对称预扣资源；
    T+1 开盘事件 _drain_pending_orders 用 T+1 open 价 + T+1 状态执行，成交则正式过户，
    拒单/expire/cancel 则原路归还预扣。主计划 7.12 设计 + 7.13 任务清单。

    instruction 7 枚举（与 PtradeAPI 四个交易方法换算前分流映射对齐）：
      target_value  ← order_target_value(value)
      target_shares ← order_target(target_amount)
      buy_shares    ← order(+amount)
      sell_shares   ← order(-amount)
      buy_value     ← order_value(+value)
      sell_value    ← order_value(-value)
      sell_all      ← order_target_value(0) / 清仓
    """
    order_id: str
    created_dt: str          # T 日（信号日）
    scheduled_dt: str        # T+1 日（计划执行日）
    security: str            # 策略原样传入的 security
    code: str                # QMT 格式 600000.SH（引擎内部键）
    instruction: str         # 7 枚举之一
    direction: str           # "buy" / "sell"（创建时由 _estimate_pending 确定）
    target_value: float | None = None
    shares: int | None = None
    # T 日预扣快照（cancel/reject/expired 精确归还用，防多日重复挂单的累积漂移）
    est_cost: float = 0.0         # 买单预扣金额（含估算佣金/过户费）
    est_shares: int = 0           # 卖单预扣股数
    status: str = "pending"       # pending/filled/rejected/expired/cancelled
    filled: float = 0.0           # 实际成交金额（drain 后填）
    filled_amount: int = 0        # 实际成交股数
    price: float = 0.0            # 实际成交价（T+1 open ± 滑点）
    filled_dt: str = ""           # 成交日（T+1）；主计划 7.13 "区分 created_dt 与 filled_dt"
    reason: str = ""              # 拒单原因
    # G1-I: 所属 basket id（设计 v2 §4.2）。None = 独立订单（legacy pending queue）。
    basket_id: Optional[str] = None


@dataclass
class RebalanceBasket:
    """G1-I: basket 再平衡容器（设计 v2 §4.1）。

    每次 handle_data 调用形成一个 basket（daily-bar-v1 only，§3.6）。
    卖单优先 drain（Phase 1），所得现金支持买单（Phase 3A/3B 原子预检）。

    status 真值表见设计 §10：pending → completed/partial/rejected/cancelled/expired。
    realized_sell_proceeds 仅作审计元数据（卖出所得已由 _execute_sell 计入 cash，不重复加，§6.2）。
    """
    basket_id: str                        # "basket_{created_dt}_{seq}"
    created_dt: str                       # T 日日期
    scheduled_dt: str                     # T+1 日期
    sell_orders: List[PendingOrder] = field(default_factory=list)
    buy_orders: List[PendingOrder] = field(default_factory=list)
    status: str = "pending"               # 见 §10 状态真值表
    realized_sell_proceeds: float = 0.0   # 审计元数据（不重复加到 cash）
    # G1-I audit-fix 阻断4: 取消任意 mandatory sell 后置 True → drain 时 buy leg 全拒
    # （reason=mandatory_sell_cancelled），避免旧仓未退又买入新仓的超目标持仓风险。
    mandatory_sell_cancelled: bool = False


@dataclass
class Account:
    """账户"""
    # 默认本金对齐 Ptrade 平台回测默认 10 万（与 BacktestEngine.capital 默认一致，
    # 消除"忘传 capital 误用 100 万"陷阱）
    cash: float = 100_000.0
    # PR2: next_open pending 买单预扣资金。close/open 模式恒为 0（隔离契约）。
    # 贴合 A 股实盘"委托即冻结资金"语义；T 日策略可用现金 = cash - locked_cash 之外的自由部分，
    # 避免重复下单穿越。locked_cash 仍计入总资产（净值不因挂单失真）。
    # T+1 drain 成交则 locked_cash 归零（资金转为持仓）；拒单/expire/cancel 原路退回 cash。
    locked_cash: float = 0.0
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
        # PR2: locked_cash 计入总资产（净值不因挂单低估）。close/open 模式 locked_cash=0，数值不变。
        return self.cash + self.locked_cash + self.market_value

    def total_asset_at_price(self, prices: Dict[str, float]) -> float:
        return self.cash + self.locked_cash + self.market_value_at_price(prices)


@dataclass
class TradeCost:
    """交易成本（对齐 Ptrade 平台回测实际费率）"""
    commission_rate: float = 0.00035   # 佣金费率（万3.5，对齐 Ptrade）
    min_commission: float = 5.0        # 最低佣金
    stamp_tax_rate: float = 0.001      # 印花税（卖出单向，千1，对齐 Ptrade）
    transfer_fee_rate: float = 0.00001 # 过户费（万0.1）
    slippage_rate: float = 0.0         # 滑点（对齐 PTrade 实证：0 滑点）
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
    corporate_actions: List[Dict] = field(default_factory=list)

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
                 providers=None,
                 engine_profile: str = "daily-bar-v1",
                 etf_t0: bool = False,
                 rebalance_mode: Optional[str] = None):
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
        # PR4: 引擎 Profile（daily-bar-v1 逐日循环 / minute-bar-v1 分钟事件循环）
        if engine_profile not in ("daily-bar-v1", "minute-bar-v1", "daily-open-close-proxy-v1"):
            raise ValueError(f"engine_profile 必须是 daily-bar-v1/minute-bar-v1/daily-open-close-proxy-v1， got {engine_profile!r}")
        # 分钟 Profile 强制即时撮合，禁用 next_open pending queue（分钟是即时 bar 流模型）
        if engine_profile in ("minute-bar-v1", "daily-open-close-proxy-v1") and match_price_mode == "next_open":
            raise ValueError("minute-bar-v1 不支持 next_open（分钟即时撮合模型，无跨日 pending 语义）")
        self.engine_profile = engine_profile
        # G1-I: basket 再平衡激活门禁（设计 v2 §3.2-§3.3，audit-fix 阻断1）。
        # 解析优先级：显式构造参数（非 None）> config.rebalance_mode > "legacy"。
        # 这样 EngineConfig.rebalance_mode 能真正生效；显式参数仅作兼容覆盖。
        resolved_rebalance_mode = (rebalance_mode if rebalance_mode is not None
                                   else getattr(self.config, 'rebalance_mode', 'legacy'))
        if resolved_rebalance_mode not in ("legacy", "callback_basket"):
            raise ValueError(f"rebalance_mode 必须是 legacy/callback_basket， got {resolved_rebalance_mode!r}")
        # §3.3: minute-bar-v1 + callback_basket → 显式 BLOCK（非静默退化）
        if resolved_rebalance_mode == "callback_basket" and engine_profile == "minute-bar-v1":
            raise ValueError("minute-bar-v1 不支持 callback_basket（分钟即时撮合模型，无跨日 basket 语义）")
        self.rebalance_mode = resolved_rebalance_mode
        # PR4 决策 4（per-code）：ETF T+0 只挂分钟 Profile。日线 Profile 强制 False（守护黄金基线 87,752.56）。
        # etf_t0=True 时按 etf_basic.fund_type 做 per-code 分类（_is_t0 懒装载，fail-closed T+1）；
        # etf_t0=False（默认）恒 T+1，不触达数据装载（零查询零 warning）。
        self.etf_t0 = bool(etf_t0) if engine_profile == "minute-bar-v1" else False
        # per-code T+0 分类缓存：None=未装载；dict={code: is_t0}（仅 etf_t0=True 时懒装载）
        self._t0_cache: Optional[dict] = None
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
        # PR2: next_open pending order queue。close/open 模式下始终为空（隔离契约）。
        # _pending_orders 跨日持久；_today_orders 每个 drain 日清空（避免 get_order 跨日累积）。
        # _t_day_close_prices 由主循环每日注入，供 _create_pending_order 估算预扣（T 日 close，无穿越）。
        self._pending_orders: list = []
        self._today_orders: list = []
        self._t_day_close_prices: dict = {}
        self._current_date_str: str = ""
        self._proxy_intraday_bars: list = []
        # G1-I: basket 再平衡状态（设计 v2 §4/§3.6）。
        # _baskets: 已提交的 basket 列表（跨日持久，drain 后状态更新）。
        # _current_basket: handle_data 调用期间的活跃 basket context（None=不在 basket 内）。
        # _basket_seq: basket id 序号（同日递增）。
        # 仅 basket_active 时使用；close/open/legacy next_open 路径恒不触达（§12）。
        self._baskets: list = []
        self._current_basket = None
        self._basket_seq: int = 0
        # 实例私有按日索引缓存（纯性能优化）：FIFO，固定上限 4；缓存项存 (df, index)，
        # 命中时验证 entry_df is df 防止 id 复用读到脏索引。不缓存构建失败的 None。
        self._df_index_cache: list = []
        # 拒单采集（QS_FILL_AUDIT 口径，设计文档 backtest-align-diagnosability-design §2.1）：
        # 元素 (code, direction, reason)；每交易日循环起点重置，日末由 _emit_fill_audit 消费。
        # 覆盖 no_price / limit_up(down)_blocked / halted / insufficient_cash_or_rounding；
        # below_rebalance_threshold 属正常微调跳过，不采集。
        self._day_rejections: list = []

    @property
    def engine_semantics_version(self) -> str:
        """PR2/PR4/G1-I: 引擎执行语义版本（Run Card 记录用）。

        - close/open：`0.1.0-legacy`（next_open pending queue 未激活，行为与 PR2 前一致）
        - next_open + legacy：`0.2.0-next_open_pending`（PR2 真实 pending order queue）
        - next_open + callback_basket：`0.4.0-next_open_basket`（G1-I basket 再平衡）
        - minute-bar-v1：`0.3.0-minute-bar`（PR4 分钟事件循环 + 精确调度 + ETF T+0）
        """
        if self.engine_profile == "minute-bar-v1":
            return "0.3.0-minute-bar"
        if self.engine_profile == "daily-open-close-proxy-v1":
            return "0.5.0-daily-open-close-proxy"
        if self.match_price_mode == "next_open":
            if self.basket_active:
                return "0.4.0-next_open_basket"
            return "0.2.0-next_open_pending"
        return "0.1.0-legacy"

    @property
    def basket_active(self) -> bool:
        """G1-I: basket 再平衡是否激活（设计 v2 §3.2 三条件同时满足）。

        通过显式开关隔离，保证 close/open 与 legacy next_open 零回归（§12）。
        """
        return (self.engine_profile == "daily-bar-v1"
                and self.match_price_mode == "next_open"
                and self.rebalance_mode == "callback_basket")

    def run(self) -> BacktestResult:
        """Execute the daily-bar backtest."""
        from .ptrade_api import _api, Context, Portfolio

        _api.reset_session()
        trade_days = self._get_trade_days()
        if not trade_days:
            raise ValueError(self._build_empty_trade_days_error())
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
        benchmark_raw = {}
        try:
            benchmark_raw = self._providers.market.get_benchmark(
                benchmark_code, benchmark_start, trade_days[-1].strftime('%Y-%m-%d'))
        except Exception as e:
            logger.debug(f"[Backtest] 基准数据加载失败（分钟测试合成库可能缺 index_daily）: {e}")
        first_bench = benchmark_raw.get(benchmark_start) or benchmark_raw.get(first_trade_day, 1.0)

        total_days = len(trade_days)

        # 纯性能优化：一次性预取回测全期日线快照，避免每日对行情表做全表扫描。
        # 内存缓存后 query_daily_snapshot 命中，结果与单日查询字节级一致。
        # 预取区间下探到首个交易日的前一个交易日，覆盖日循环 day0 的 prev_date。
        try:
            pre_start = self._providers.calendar.get_trading_day(
                trade_days[0].strftime('%Y-%m-%d'), offset=-1)
            pre_start_str = (pre_start.strftime('%Y-%m-%d')
                             if pre_start is not None else trade_days[0].strftime('%Y-%m-%d'))
            self._providers.market.preload(pre_start_str, trade_days[-1].strftime('%Y-%m-%d'))
        except Exception as e:
            logger.debug(f"[Backtest] 日线快照预取跳过（性能优化不可用）: {e}")

        for i, day in enumerate(trade_days):
            day_str = day.strftime('%Y-%m-%d')
            if self._progress_callback:
                self._progress_callback(i + 1, total_days, day_str)
            # 拒单采集每日起点重置（QS_FILL_AUDIT 口径，设计文档 §2.1）
            self._day_rejections = []
            self._apply_corporate_actions(day_str)
            # 阶段 2（v2-final §3.6）：ETF 现金分红入账（公募免税全额；与送股反推不互斥）
            self._apply_etf_cash_dividends(day_str)

            # PR4: 分钟 Profile 走分钟事件循环（独立方法，日线循环逐行不变）
            if self.engine_profile == "minute-bar-v1":
                self._run_minute_day(i, day, trade_days, benchmark_raw, first_bench)
                continue
            if self.engine_profile == "daily-open-close-proxy-v1":
                self._run_daily_open_close_proxy_day(i, day, trade_days, benchmark_raw, first_bench)
                continue

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
            # P-D13b C4b：退市强平保真开关（默认关 opt-in；开时对齐平台 is expired 强平）。
            # 审计条件②：强平审计行带 fidelity_delist 标记（与普通卖出可区分——防对账混入策略行为）。
            self._apply_delist_force_close(day_str, curr_data, prev_data)
            if len(curr_data) == 0:
                logger.debug(f"[Backtest] {day_str} no data, skipped")
                continue

            # ETF 除权补正兜底：preClose 反推（ETF-only，stock_dividend 无 ETF 记录时）
            self._apply_factor_derived_split(curr_data, prev_data, day_str)

            match_prices = self._build_match_prices(curr_data, trade_days, i)
            prices = {self._to_qmt(c): v for c, v in zip(curr_data['code'], curr_data['close'])}

            # PR2: 注入 T 日状态供 _create_pending_order 估算预扣（T 日 close，无穿越）
            self._current_date_str = day_str
            self._t_day_close_prices = match_prices if self.match_price_mode == "next_open" else {}

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

            # PR2: T+1 drain（解锁之后、策略之前 — 修正点 1）。
            # 仅 next_open 模式激活；close/open 模式零触达。drain 成交的新买单 can_sell=0
            # 不被覆盖（解锁已在 drain 之前跑完）；pending 卖单走 pending_sell_shares 预扣。
            # 成交价用 T+1 open（单独构建，不用 match_prices，后者在 next_open 模式下是 T 日 close）。
            if self.match_price_mode == "next_open":
                t1_open_prices = {self._to_qmt(c): v
                                  for c, v in zip(curr_data['code'], curr_data['open'])}
                self._today_orders = []
                self._drain_pending_orders(curr_data, day_str, t1_open_prices)
                # G1-I: basket drain（独立订单之后，§11 优先级）。basket_active 时激活。
                if self.basket_active:
                    self._drain_baskets(curr_data, day_str, t1_open_prices)
                self.refresh_portfolio(prices)

            prev_day_str = prev_day.strftime('%Y-%m-%d')
            self._run_ptrade_strategy(day, prev_day, match_prices, positions_snapshot,
                                      day_str, prev_day_str, curr_data, prev_data)

            if self.strategy_type == "ptrade" and 'after_trading_end' in self.strategy:
                try:
                    self.strategy['after_trading_end'](
                        self._ptrade_context, self._build_data_dict(curr_data, day_str))
                except Exception as e:
                    logger.debug(f"[Ptrade] after_trading_end error: {e}")

            # 日末实际成交审计行（QS_FILL_AUDIT，引擎层，设计文档 §2.2；仅日线 Profile 输出）
            self._emit_fill_audit(day_str)

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

        # PR2: 主循环结束 → 末日 pending 订单标记 expired，预扣原路归还。
        # 主计划 7.13 "末日订单标记 expired 或保留 pending"。仅 next_open 模式有 pending。
        if self.match_price_mode == "next_open":
            self._expire_remaining_pending()
            # G1-I: basket 也 expire（§9.2，pending_sell_shares 归零不变式）
            if self.basket_active:
                self._expire_remaining_baskets()

        logger.info(f"[Backtest] completed: {len(self.result.nav_history)} days")
        from .ptrade_metrics import calculate_ptrade_like_metrics
        metrics = calculate_ptrade_like_metrics(self.result, self)
        self.result.metrics_summary = metrics.summary
        self.result.round_trips = metrics.round_trips

        # F-DUCKDB-LOCK（A2-P2/P3，2026-09-05）：数据层诊断事件回测收尾无条件汇总输出
        # （审核附加条：不依赖错误状态，事后可检——防「空数据静默出回测」事故模式）。
        # getattr 守卫同 L2490 既有模式：mock provider / 不支持诊断 → 零行为变更。
        try:
            _dda = getattr(getattr(self._providers, "market", None),
                           "_data", None) if self._providers is not None else None
            _diag_fn = getattr(_dda, "qs_diagnostics", None)
            if callable(_diag_fn):
                _diag_events = _diag_fn() or []
                for _ev in _diag_events:
                    logger.warning("QS_DIAG %s" % _ev)
                if _diag_events:
                    logger.warning("QS_DIAG summary: %d data-layer diagnostic event(s) this run"
                                   % len(_diag_events))
        except Exception:
            pass  # 诊断汇总自身异常不得引入新失败路径（同 L2498 既有守卫原则）

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

        # 方向判定上移（设计文档 §2.1 修订①）：target_value/shares 的符号在价格检查前
        # 即可得，no_price 等拒单据此归类 buy/sell（仅无任何指令时为 unknown）。
        if target_value is not None:
            direction_int = 1 if target_value > 0 else 0
            direction = "buy" if target_value > 0 else "sell"
        elif shares is not None:
            direction_int = 1 if shares > 0 else 0
            direction = "buy" if shares > 0 else "sell"
        else:
            direction_int = 0
            direction = "unknown"

        price = (prices or {}).get(code, 0)
        if price <= 0:
            return self._finalize_immediate(
                Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                      status="rejected", reason="no_price", created_dt=date), code)

        # P-D13b C4a：停牌撤单保真开关（默认关 opt-in；开时对齐平台 bar.volume==0 撤单）。
        # reason 语义区分（审计条件①）：halted 唯一对应停牌撤单——可区分于 no_price/limit 类。
        try:
            from .ptrade_api import _api as _ptrade_api
            _fid = getattr(_ptrade_api, "_fidelity", None)
            if _fid is not None and getattr(_fid, "fidelity_halt_reject", False):
                _row = curr_data is not None and self._lookup_curr_row(curr_data, code)
                if _row is not None and (
                        float(_row.get("volume", 0) or 0) == 0
                        or int(_row.get("suspendFlag", 0) or 0) == 1):
                    return self._finalize_immediate(
                        Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                              status="rejected", reason="halted", created_dt=date), code)
        except Exception:
            pass  # 防护：读配置/查行异常不阻断（默认关行为保持）

        # 无指令保护（原分支语义保留：价格检查之后、涨跌停检查之前）。
        if target_value is None and shares is None:
            return self._finalize_immediate(
                Order(order_id=f"ord_{code}_{date}", security=security, direction="unknown",
                      status="rejected", reason="no_instruction", created_dt=date), code)

        # 涨跌停检查（使用 shared_ashare_rules 精确规则）
        # is_price_limit_blocked 的 direction 参数：1=买入(查涨停阻断)，0/其他=卖出(查跌停阻断)
        from .libs.shared_ashare_rules import is_price_limit_blocked, round_to_lot
        pct_chg = self._get_pct_chg(code, curr_data, date)
        if is_price_limit_blocked(code, direction_int, pct_chg):
            reason = "limit_up_blocked" if direction_int == 1 else "limit_down_blocked"
            action = "买入涨停" if direction_int == 1 else "卖出跌停"
            logger.debug(f"[即时执行] {code} {action}被阻断 (pct_chg={pct_chg:.4f})")
            return self._finalize_immediate(
                Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                      status="rejected", reason=reason, created_dt=date), code)

        # PR4: 停牌检查（仅分钟 Profile；日线保持现状避免影响黄金基线）。
        # 分钟即时撮合应拒停牌单（suspendFlag==1 OR volume==0）。
        if self.engine_profile == "minute-bar-v1" and self._is_halted_at(code, curr_data):
            return self._finalize_immediate(
                Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                      status="rejected", reason="halted", created_dt=date), code)

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
                        return self._finalize_immediate(
                            Order(order_id=f"ord_{code}_{date}", security=security, direction=direction,
                                  target=abs(delta), status="rejected", reason="below_rebalance_threshold",
                                  created_dt=date), code)
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
        return self._finalize_immediate(
            Order(
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
            ), code)

    def _finalize_immediate(self, order: Order, code: str) -> Order:
        """即时执行单出口（设计文档 §2.1 修订②）：拒单集中采集（QS_FILL_AUDIT 口径）。

        一处覆盖全部现在和未来的拒单路径：no_price / limit_up_blocked /
        limit_down_blocked / halted（分钟 Profile）/ insufficient_cash_or_rounding
        （含整手取整不足 100 股与资金不足兜底）。
        below_rebalance_threshold 属正常微调跳过（非拒单），不采集。
        纯日志/内存采集，不改变订单、资金、持仓语义（行为等价）。
        """
        if order.status == "rejected" and order.reason != "below_rebalance_threshold":
            self._day_rejections.append((code, order.direction, order.reason))
            if order.reason in ("no_price", "halted"):
                # no_price 原零日志（修订②：至少补 DEBUG，与其他拒单分支对齐）；
                # halted（分钟 Profile）同样零日志，一并补 DEBUG。
                logger.debug(f"[即时执行] {code} 拒单: reason={order.reason} "
                             f"direction={order.direction}")
        return order

    def _emit_fill_audit(self, day_str: str) -> None:
        """日末实际成交审计行（QS_FILL_AUDIT，引擎层，设计文档 §2.2）。

        与策略层 QS_REBALANCE_AUDIT（计划）配对：submitted vs filled/rejected 即"计划 vs 实际"，
        供两端（本地/PTrade）日志机械对齐。
        - sell_filled/buy_filled：当日 trade_records 计数（close/open 同日归账；
          next_open 按 T+1 成交日归账，drain 成交 date=T+1）
        - sell_rejected/buy_rejected：当日拒单按方向计数（_day_rejections）
        - positions_total：当日收盘实际持仓数（volume>0 计数）；与 QS_REBALANCE_AUDIT 的
          positions（目标仓数）口径不同，不可直接相减（清仓单、未变动持仓均造成口径差）
        - rejected_detail：code:reason 明细，最多 10 条，超出输出 ...(+N more)
        有拒单 → WARNING，无拒单 → INFO。
        """
        buy_filled = sell_filled = 0
        for r in self.result.trade_records:
            if r.get('date') != day_str:
                continue
            if r.get('action') == 'buy':
                buy_filled += 1
            elif r.get('action') == 'sell':
                sell_filled += 1
        sell_rejected = sum(1 for _, d, _ in self._day_rejections if d == 'sell')
        buy_rejected = sum(1 for _, d, _ in self._day_rejections if d == 'buy')
        total_rejected = len(self._day_rejections)
        detail = ",".join(f"{c}:{r}" for c, _, r in self._day_rejections[:10])
        if total_rejected > 10:
            detail += f"...(+{total_rejected - 10} more)"
        positions_total = len([p for p in self.account.positions.values() if p.volume > 0])
        msg = (f"QS_FILL_AUDIT date={day_str} sell_filled={sell_filled} buy_filled={buy_filled} "
               f"sell_rejected={sell_rejected} buy_rejected={buy_rejected} "
               f"positions_total={positions_total} rejected_detail=[{detail}]")
        if total_rejected:
            logger.warning(msg)
        else:
            logger.info(msg)

    def _stamp_tax_rate(self, date: str) -> float:
        """Historical A-share sell stamp duty (halved from 2023-08-28)."""
        if str(date)[:10] >= "2023-08-28":
            return min(float(self.cost.stamp_tax_rate), 0.0005)
        return float(self.cost.stamp_tax_rate)

    def _apply_corporate_actions(self, day_str: str) -> None:
        """Apply ex-date dividends to positions before the opening callback.

        Cash distributions: reads cash_div_before_tax (pre-tax per-share cash dividend),
        credits cash net of 20% conservative short-hold dividend tax.
        Falls back to legacy cash_div column for backward compatibility.
        Stock distributions increase shares and reduce per-share cost without creating PnL.
        """
        if not self.account.positions:
            return
        reference = getattr(self._providers, "reference", None)
        if reference is None or not hasattr(reference, "get_corporate_actions"):
            return
        actions = reference.get_corporate_actions(day_str)
        if actions is None or len(actions) == 0:
            return
        for _, row in actions.iterrows():
            code = self._to_qmt(str(row.get("code", "")))
            pos = self.account.positions.get(code)
            if pos is None or pos.volume <= 0:
                continue
            old_volume = int(pos.volume)
            # Prefer cash_div_before_tax (pre-tax); fall back to legacy cash_div
            cash_div_before_tax = max(0.0, float(row.get("cash_div_before_tax",
                                             row.get("cash_div", 0.0) or 0.0)))
            stock_div = max(0.0, float(row.get("stk_div", 0.0) or 0.0))
            # 20% short-hold dividend tax on pre-tax amount
            cash_credit = old_volume * cash_div_before_tax * 0.80
            if cash_credit:
                self.account.cash += cash_credit
            if cash_div_before_tax:
                pos.avg_cost = max(0.0, pos.avg_cost - cash_div_before_tax)
            added = int(round(old_volume * stock_div))
            if added > 0:
                new_volume = old_volume + added
                pos.avg_cost = pos.avg_cost * old_volume / new_volume
                pos.volume = new_volume
                pos.can_sell += added
            self.result.corporate_actions.append({
                "date": day_str, "code": code,
                "cash_div_per_share": cash_div_before_tax,
                "cash_div_before_tax": cash_div_before_tax,
                "cash_div_after_tax": float(row.get("cash_div_after_tax", 0.0) or 0.0),
                "cash_credit_net": cash_credit,
                "stock_div_ratio": stock_div,
                "added_shares": added,
                "tax_policy": "pre_tax_x_0.80",
            })

    def _apply_factor_derived_split(self, curr_data, prev_data, day_str):
        """ETF 除权补正兜底：preClose 反推除权比例（仅 ETF，stock_dividend 无 ETF 记录时）。

        带区规则（v2-final §3.2）：
          ratio < 0.99      份额合并 → 对称处理（吸附 0.5 倍数，未吸附 WARN+跳过）【P1-4】
          0.99~1.01         非除权 → 跳过
          1.01 < ratio < 1.10 现金分红带 → 跳过 + WARN（阶段2 etf_dividend 精确入账）【P0-2】
          ratio >= 1.10     送股/折算 → 吸附 0.5 倍数（容差 0.5%），未吸附按原值 + WARN【P1-3】
        仅 is_etf 生效（股票走 stock_dividend 精确路径，行为零变化）【P1-5】。
        already_handled 用裸码比对（corporate_actions code 为 QMT 格式）【P0-1】。
        """
        if not self.account.positions:
            return
        if curr_data is None or prev_data is None:
            return
        from .libs.shared_ashare_rules import round_to_lot
        from .libs.security_code_rules import is_etf
        # 【P0-1 修订】裸码统一：corporate_actions 的 code 是 QMT 格式（'600000.SH'）
        # 【阶段 2 协调】排除 etf_cash_dividend：同日 ETF 现金分红不阻止送股反推
        # （分红+送股同日可同时发生，already_handled 只防同一路径重复触发）。
        already_handled = {str(a.get('code', '')).split('.')[0]
                           for a in self.result.corporate_actions
                           if a.get('date') == day_str
                           and a.get('type') != 'etf_cash_dividend'}
        prev_close_map = {}
        if 'code' in prev_data.columns:
            for _, row in prev_data.iterrows():
                prev_close_map[str(row['code'])] = row.get('close', 0)
        for code, pos in self.account.positions.items():
            if pos.volume <= 0:
                continue
            bare = code.split('.')[0] if '.' in code else code
            # 【P1-5 修订】仅 ETF 走反推；股票由 stock_dividend 精确路径处理
            if not is_etf(bare):
                continue
            if bare in already_handled:
                continue  # 已有精确记录（stock_dividend/现金入账）→ 不重复处理
            prev_close = prev_close_map.get(bare, 0)
            if prev_close <= 0:
                continue
            row = curr_data[curr_data['code'] == bare]
            if len(row) == 0:
                continue
            preclose = row.iloc[0].get('preClose', 0)
            if preclose <= 0:
                continue
            ratio = prev_close / preclose

            # ---- 带区规则（v2-final §3.2）----
            if ratio < 0.99:
                # 【P1-4 修订】份额合并：对称处理（吸附 0.5 倍数）
                snapped = round(ratio * 2) / 2
                if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
                    ratio = snapped
                else:
                    logger.warning(f"[Split] {code} {day_str} 疑似份额合并 ratio={ratio:.4f} "
                                   f"未吸附，跳过（数据异常/非 0.5 倍数）")
                    continue
                old_volume = int(pos.volume)
                new_total = int(round(old_volume * ratio))
                # 【P1-4 执行修正】round_to_lot 对负值截 0（max(raw/100*100, 0)，A股订单语义），
                # 合并为负向变化会恒得 0 → 合并永不生效（v2-final §3.5 代码缺陷，与 §3.2 表格/
                # §六 测试 7 矛盾）。此处按 §3.2 "volume ×= ratio（整手向下取整）"直接对乘积取整手。
                new_volume = int(new_total / 100) * 100
                added = new_volume - old_volume
                if added >= 0 or new_volume <= 0:
                    continue  # 合并且无净减少/合并到 0 股（数值异常）→ 跳过
                new_volume = old_volume + added
                pos.avg_cost = pos.avg_cost * old_volume / new_volume
                pos.volume = new_volume
                pos.can_sell += added
                self.result.corporate_actions.append({
                    'date': day_str, 'code': bare,
                    'type': 'factor_derived_merge',
                    'ratio': ratio, 'old_volume': old_volume,
                    'new_volume': new_volume, 'added': added,
                    'note': 'preClose反推合并（stock_dividend无记录）',
                })
                logger.info(f"[Split] {code} {day_str} 因子反推合并: "
                            f"{old_volume}→{new_volume} (ratio={ratio:.4f})")
                continue

            if ratio <= 1.01:
                continue  # 非除权日

            if ratio < 1.10:
                # 【P0-2 修订】现金分红带：不送股、不改成本（阶段2 由 etf_dividend 精确入账）
                logger.warning(f"[Split] {code} {day_str} 现金分红带 ratio={ratio:.4f} "
                               f"（收益率 {1-1/ratio:.2%}），跳过送股（TD-ETF-DIV/阶段2）")
                continue

            # ratio >= 1.10：送股/份额折算
            snapped = round(ratio * 2) / 2
            if snapped > 0 and abs(ratio - snapped) / snapped < 0.005:
                ratio = snapped  # 吸附命中（1.9993→2.0）
            else:
                logger.warning(f"[Split] {code} {day_str} 非 0.5 倍数折算 ratio={ratio:.4f}，"
                               f"按原值送股")  # 【P1-3】512890≈2.0462 等真实折算

            old_volume = int(pos.volume)
            new_total = int(round(old_volume * ratio))
            added = round_to_lot(new_total - old_volume, 100)
            if added <= 0:
                continue
            new_volume = old_volume + added
            pos.avg_cost = pos.avg_cost * old_volume / new_volume
            pos.volume = new_volume
            pos.can_sell += added
            self.result.corporate_actions.append({
                'date': day_str, 'code': bare,
                'type': 'factor_derived_split',
                'ratio': ratio, 'old_volume': old_volume,
                'new_volume': new_volume, 'added': added,
                'note': 'preClose反推（stock_dividend无记录）',
            })
            logger.info(f"[Split] {code} {day_str} 因子反推送股: "
                        f"{old_volume}→{new_volume} (ratio={ratio:.4f}, preClose反推)")

    def _apply_etf_cash_dividends(self, day_str: str) -> None:
        """ETF 现金分红入账（阶段 2，v2-final §3.6）：etf_dividend.div_cash × volume × 0.80。

        **0.8 口径（2026-08-16 PTrade 实测修正）**：PTrade 平台对 ETF 现金分红与股票统一
        按税前 × 0.80（扣 20%）入账——实测 600000 11500×0.42×0.8=3864.00、
        510500 10800×0.149×0.8=1287.36，与平台现金增量逐分吻合；公募基金税法免税与平台
        实现不一致，回测目标是复刻平台行为，**以平台实测为准**（与股票 `pre_tax × 0.80`
        同口径，tax_policy='etf_pre_tax_x_0.80'）。
        与阶段 1（`_apply_factor_derived_split`）**不互斥**：同日分红+送股同时发生；
        already_handled 只防同一路径重复触发，不阻止两路径并行。
        etf_dividend 表不存在/无记录 → no-op（不阻塞回测，缺口由阶段 1 现金分红带
        WARN 兜底检测）。
        """
        if not self.account.positions:
            return
        reference = getattr(self._providers, "reference", None)
        if reference is None or not hasattr(reference, "get_etf_dividends"):
            return
        try:
            df = reference.get_etf_dividends(day_str)
        except Exception as e:
            logger.debug(f"[ETF Dividend] 查询失败（etf_dividend 未落地）: {e}")
            return
        if df is None or len(df) == 0:
            return
        for _, row in df.iterrows():
            code = self._to_qmt(str(row.get("code", "")))
            pos = self.account.positions.get(code)
            if pos is None or pos.volume <= 0:
                continue
            div_cash = float(row.get("div_cash", 0.0) or 0.0)
            if div_cash <= 0:
                continue
            # 0.8 入账：PTrade 实测（2026-08-16）ETF 分红同样扣 20%（与股票 pre_tax×0.80 同口径）
            credit = pos.volume * div_cash * 0.80
            self.account.cash += credit
            self.result.corporate_actions.append({
                "date": day_str, "code": code,
                "type": "etf_cash_dividend",
                "div_cash": div_cash,
                "cash_credit_net": credit,
                "tax_policy": "etf_pre_tax_x_0.80",
            })
            logger.info(f"[ETF Dividend] {code} {day_str} 现金分红入账: "
                        f"{pos.volume}×{div_cash}×0.80={credit:.2f}（PTrade 实测口径）")

    def _apply_slippage(self, price: float, direction: str) -> float:
        """Apply strategy-configured slippage below the public API boundary."""
        fixed = max(0.0, float(getattr(self.cost, "fixed_slippage", 0.0)))
        ratio = max(0.0, float(getattr(self.cost, "slippage_rate", 0.0)))
        if direction == "buy":
            return max(0.0, price * (1 + ratio) + fixed)
        return max(0.0, price * (1 - ratio) - fixed)

    def _load_etf_t0_cache(self) -> None:
        """装载 etf_basic.fund_type → {code: is_t0} 缓存（per-code T+0，PR4 决策4 扩展）。

        仅当 minute-bar-v1 and etf_t0 时被 _is_t0 懒调用；数据经 provider/数据访问层
        （与 get_etf_list_local 同一数据源），引擎内不裸 SQL。任何失败 → 空缓存 + warning
        （fail-closed 全 T+1），绝不静默放行。etf_t0=False / daily profile 路径不触达本方法。
        """
        self._t0_cache = {}
        try:
            market = getattr(self._providers, "market", None)
            data = getattr(market, "_data", None)
            if data is None or not hasattr(data, "query_etf_fund_types"):
                logger.warning("ETF T+0 分类数据源不可用，全部按 T+1（fail-closed）")
                return
            fund_types = data.query_etf_fund_types()
            for code, ftype in fund_types.items():
                self._t0_cache[code] = ftype in T0_FUND_TYPES
            if not self._t0_cache:
                logger.warning("ETF T+0 分类装载结果为空，全部按 T+1（fail-closed）")
        except Exception as e:
            logger.warning("ETF T+0 分类装载失败，全部按 T+1（fail-closed）: %s", e)

    def _is_t0(self, code: str) -> bool:
        """per-code ETF T+0 判定：fund_type ∈ T0_FUND_TYPES → True；未知代码 → False（T+1）。

        入参为 QMT 格式（如 159870.SZ，见 _immediate_execute 的 _to_qmt），
        归一化为裸码后查 etf_basic.fund_type 缓存（缓存键为裸码）。
        """
        if self._t0_cache is None:
            self._load_etf_t0_cache()
        bare = str(code).split(".")[0]
        return self._t0_cache.get(bare, False)

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

        # 取整边界溢出（差额 < 一手总成本含费用）→ 减一手重试
        # 真实资金不足（差额 ≥ 一手总成本）→ 整单拒单（保留 PTrade 语义，防碎仓）
        if total_cost > self.account.cash:
            one_lot_cost = 100 * fill_price
            one_lot_total = (one_lot_cost
                             + max(one_lot_cost * self.cost.commission_rate,
                                   self.cost.min_commission)
                             + one_lot_cost * self.cost.transfer_fee_rate)
            if target_vol >= 200 and total_cost - self.account.cash < one_lot_total:
                target_vol -= 100
                cost_amount = target_vol * fill_price
                commission = max(cost_amount * self.cost.commission_rate,
                                 self.cost.min_commission)
                transfer_fee = cost_amount * self.cost.transfer_fee_rate
                total_cost = cost_amount + commission + transfer_fee
            if target_vol < 100 or total_cost > self.account.cash:
                logger.warning("当前账户资金不足，%s下单失败" % code)
                return 0, fill_price

        self.account.cash -= total_cost
        pos = self.account.positions.get(code)
        # PR4 决策 4（per-code）：ETF T+0 按 etf_basic.fund_type 分类（_is_t0）。
        # etf_t0=True → 按分类解锁 can_sell；False（默认）→ 全 T+1，且短路不触达数据装载。
        # 清理项：盘前解锁全证券一致；T+0 差异只在这里——买入后立即解锁（含昨日存量+今日新买）。
        is_etf_t0 = self.etf_t0 and self._is_t0(code)
        if pos:
            new_total = pos.volume + target_vol
            pos.avg_cost = (pos.avg_cost * pos.volume + fill_price * target_vol) / new_total
            pos.volume = new_total
            if is_etf_t0:
                pos.can_sell = new_total   # ETF T+0：买入后立即全部可卖
            # else: 股票 T+1，今日新买 can_sell 不增加
        else:
            self.account.positions[code] = Position(
                code=code, volume=target_vol, avg_cost=fill_price,
                can_sell=(target_vol if is_etf_t0 else 0))

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
        stamp_tax = proceeds * self._stamp_tax_rate(date)
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

    def _df_index(self, df):
        """实例私有按日索引查询：命中（验证 entry_df is df）返回索引，否则构建并入 FIFO 缓存。

        上限固定 4，超出淘汰最旧；不缓存构建失败的 None。返回 None 时调用点回退原布尔过滤。
        """
        from .ptrade_api import _build_code_index
        for entry_df, idx in self._df_index_cache:
            if entry_df is df:
                return idx
        built = _build_code_index(df)
        if built is not None:
            self._df_index_cache.append((df, built))
            if len(self._df_index_cache) > 4:
                self._df_index_cache.pop(0)
        return built

    def _get_pct_chg(self, code, curr_data, date):
        """获取当日涨跌幅。

        优先使用管线预计算的 pctChg 列（除权日正确，已是复权校正后真实涨跌幅），
        回退才用 (close - preClose) / preClose（preClose 为除权参考价语义，同样正确）。
        """
        if curr_data is None:
            return 0.0
        bare = code.split(".")[0] if "." in code else code
        if 'code' not in curr_data.columns:
            return 0.0
        idx = self._df_index(curr_data)
        if idx is not None:
            i = idx.get(bare)
            if i is not None:
                row = curr_data.iloc[i]
                pctchg = row.get('pctChg')
                if pctchg is not None and pctchg == pctchg:  # not NaN
                    return pctchg / 100.0
                close = row.get('close', 0)
                preclose = row.get('preClose', 0)
                if preclose and preclose > 0:
                    return (close - preclose) / preclose
            return 0.0
        # 索引不可用 → 回退原布尔过滤
        row = curr_data[curr_data['code'] == bare]
        if len(row) > 0:
            r0 = row.iloc[0]
            pctchg = r0.get('pctChg')
            if pctchg is not None and pctchg == pctchg:
                return pctchg / 100.0
            close = r0.get('close', 0)
            preclose = r0.get('preClose', 0)
            if preclose and preclose > 0:
                return (close - preclose) / preclose
        return 0.0

    def _get_open_pct_chg(self, code, curr_data, open_price):
        """Return T+1 opening gap versus T-1 close for pending-open checks.

        优先使用管线预计算的 pctChg 列判断涨跌停基准，回退才用 preClose 手算。
        """
        if curr_data is None or not open_price or open_price <= 0:
            return 0.0
        bare = code.split(".")[0] if "." in code else code
        if "code" not in curr_data.columns:
            return 0.0
        idx = self._df_index(curr_data)
        preclose = None
        if idx is not None:
            i = idx.get(bare)
            if i is None:
                return 0.0
            preclose = curr_data.iloc[i].get("preClose", 0)
        else:
            # 索引不可用 → 回退原布尔过滤
            row = curr_data[curr_data["code"] == bare]
            if len(row) == 0:
                return 0.0
            preclose = row.iloc[0].get("preClose", 0)
        if preclose and preclose > 0:
            return (float(open_price) - float(preclose)) / float(preclose)
        return 0.0

    def _build_match_prices(self, curr_data, trade_days, i):
        """A2: 构建 order 撮合价字典（match_prices）。

        语义：
        - close（默认）：当日收盘价。⚠️ 未来函数风险（策略可读当日 close 再按 close 成交）。
        - open：当日开盘价。策略仍能读 close 做信号，但成交用 open。
        - next_open：PR2 修正——不再预取 T+1 开盘价（那是跨日穿越）。
          改为返回当日 close 作为"策略可见现价"（供 _get_current_price / order_value 等
          前置逻辑读现价）。真实撮合职责转移给 _drain_pending_orders 的 T+1 open
          （主循环 next_open 分支单独构建 T+1 open 价字典传入 drain）。
          这样消除了"T 日提前读 T+1 价并在 T 日循环内即时成交"的穿越，同时保证
          策略在 T 日 handle_data 里查现价仍能拿到合理值（T 日 close，无穿越）。

        注意：记账价（净值/持仓估值）不在此方法，始终用当日收盘（见 run() 的 prices）。
        """
        if self.match_price_mode == "open":
            col = "open"
        elif self.match_price_mode == "next_open":
            # PR2: 撮合价口径交给 drain 的 T+1 open；这里只返回当日 close 作"策略可见现价"。
            col = "close"
        else:  # close
            col = "close"
        return {self._to_qmt(c): v for c, v in zip(curr_data['code'], curr_data[col])}

    # ===================== PR2: next_open pending order queue =====================

    def _next_trade_day_str(self, day_str: str):
        """返回 day_str 的下一交易日字符串；无下一日（末日/超出日历）返回 None。"""
        try:
            nxt = self._providers.calendar.get_trading_day(day_str, offset=1)
        except Exception:
            return None
        if nxt is None:
            return None
        return nxt.strftime('%Y-%m-%d')

    def _estimate_pending(self, code, instruction, target_value, shares, est_price):
        """根据 instruction + T 日估算价，确定方向 + 预扣股数/金额。

        返回 (direction, est_shares, est_cost)：
        - direction: "buy" / "sell" / None（noop）
        - 买单：est_shares 用 T 日 close 估算股数（向下取整到整手），est_cost 含估算佣金/过户费
        - 卖单：est_shares 为待卖股数，est_cost=0（卖单不锁现金，锁 can_sell）
        """
        from .libs.shared_ashare_rules import round_to_lot
        buy_instructions = ("target_value", "buy_shares", "buy_value", "target_shares")
        sell_instructions = ("sell_shares", "sell_value", "sell_all")

        if instruction in ("buy_value",):
            direction = "buy"
            est_shares = round_to_lot(int(target_value / est_price), 100)
        elif instruction == "sell_value":
            direction = "sell"
            est_shares = round_to_lot(int(target_value / est_price), 100)
        elif instruction == "target_value":
            # target_value 的方向取决于目标 vs 当前持仓（用 T 日 close 估算）
            pos = self.account.positions.get(code)
            current_value = (pos.volume * est_price) if pos and pos.volume > 0 else 0
            if target_value == 0:
                direction = "sell"
                est_shares = pos.volume if pos else 0
            elif target_value >= current_value:
                direction = "buy"
                delta = target_value - current_value
                est_shares = round_to_lot(int(delta / est_price), 100)
            else:
                direction = "sell"
                delta = current_value - target_value
                est_shares = round_to_lot(int(delta / est_price), 100)
        elif instruction == "target_shares":
            pos = self.account.positions.get(code)
            current = pos.volume if pos else 0
            delta = (shares or 0) - current
            if delta > 0:
                direction = "buy"
                est_shares = round_to_lot(delta, 100)
            elif delta < 0:
                direction = "sell"
                est_shares = round_to_lot(abs(delta), 100)
            else:
                return None, 0, 0.0
        elif instruction == "buy_shares":
            direction = "buy"
            est_shares = round_to_lot(shares or 0, 100)
        elif instruction == "sell_shares":
            direction = "sell"
            est_shares = round_to_lot(shares or 0, 100)
        elif instruction == "sell_all":
            direction = "sell"
            pos = self.account.positions.get(code)
            est_shares = pos.volume if pos else 0
        else:
            return None, 0, 0.0

        if est_shares <= 0:
            return None, 0, 0.0

        if direction == "buy":
            cost_amount = est_shares * est_price
            commission = max(cost_amount * self.cost.commission_rate, self.cost.min_commission)
            transfer_fee = cost_amount * self.cost.transfer_fee_rate
            est_cost = cost_amount + commission + transfer_fee
        else:
            est_cost = 0.0
        return direction, est_shares, est_cost

    def _create_pending_order(self, security, instruction,
                               target_value=None, shares=None) -> Order:
        """PR2: T 日策略下单 → 创建 PendingOrder + 对称预扣资源。

        T 日不产生 trade_record、不改 volume。买单预扣 locked_cash，卖单预扣 pending_sell_shares。
        预扣用 T 日 close 估算（无穿越）。返回 status=pending 的 Order（__bool__ False，兼容老策略）。

        主计划 7.13: T 日信号只创建 pending order；T 日现金/持仓不变。
        """
        bare = str(security).split(".")[0]
        code = self._to_qmt(bare)
        created_dt = self._current_date_str
        scheduled_dt = self._next_trade_day_str(created_dt)

        # 末日下单：无下一交易日 → 创建即拒，无预扣发生（无需归还）
        if scheduled_dt is None:
            return Order(order_id=f"pend_{code}_{created_dt}_rej",
                         security=security, direction="unknown",
                         status="rejected", reason="no_next_trade_day",
                         created_dt=created_dt)

        est_price = self._t_day_close_prices.get(code, 0)
        if est_price <= 0:
            return Order(order_id=f"pend_{code}_{created_dt}_rej",
                         security=security, direction="unknown",
                         status="rejected", reason="no_price", created_dt=created_dt)

        direction, est_shares, est_cost = self._estimate_pending(
            code, instruction, target_value, shares, est_price)
        if direction is None:
            # noop（如 target_shares delta=0）→ 返回 noop Order（与 order_target 现有行为一致）
            return Order(order_id=f"pend_{code}_{created_dt}_noop",
                         security=security, direction="unknown",
                         status="rejected", reason="noop", created_dt=created_dt)

        # 对称预扣
        if direction == "buy":
            if est_cost > self.account.cash:
                return Order(order_id=f"pend_{code}_{created_dt}_rej",
                             security=security, direction=direction,
                             status="rejected", reason="insufficient_cash",
                             created_dt=created_dt)
            self.account.cash -= est_cost
            self.account.locked_cash += est_cost
        else:  # sell
            pos = self.account.positions.get(code)
            avail = (pos.can_sell - pos.pending_sell_shares) if pos else 0
            if avail < est_shares:
                return Order(order_id=f"pend_{code}_{created_dt}_rej",
                             security=security, direction=direction,
                             status="rejected", reason="insufficient_sellable",
                             created_dt=created_dt)
            pos.pending_sell_shares += est_shares

        order_id = f"pend_{code}_{created_dt}_{len(self._pending_orders)}"
        po = PendingOrder(
            order_id=order_id, created_dt=created_dt, scheduled_dt=scheduled_dt,
            security=security, code=code, instruction=instruction, direction=direction,
            target_value=target_value, shares=shares,
            est_cost=est_cost, est_shares=est_shares, status="pending")
        self._pending_orders.append(po)
        logger.debug(f"[PR2] 创建 pending {direction} {code} instr={instruction} "
                     f"est_shares={est_shares} est_cost={est_cost:.0f} T={created_dt} T+1={scheduled_dt}")
        return Order(order_id=order_id, security=security, direction=direction,
                     status="pending", created_dt=created_dt)

    # ===================== G1-I: basket rebalance =====================

    def push_basket_context(self, created_dt: str):
        """G1-I: handle_data 调用前 push basket context（设计 v2 §3.6）。

        每次 handle_data 调用形成一个 basket。before_trading_start / run_daily 不并入 basket
        （那些路径的订单仍走 _create_pending_order legacy 队列）。
        """
        self._basket_seq += 1
        seq_str = f"{self._basket_seq:03d}"
        dt_compact = created_dt.replace("-", "")
        basket_id = f"basket_{dt_compact}_{seq_str}"
        scheduled_dt = self._next_trade_day_str(created_dt)
        self._current_basket = RebalanceBasket(
            basket_id=basket_id, created_dt=created_dt,
            scheduled_dt=scheduled_dt if scheduled_dt else created_dt)

    def submit_basket(self):
        """G1-I: handle_data 调用后 pop + 提交 basket（设计 v2 §3.6）。

        空 basket（无任何有效订单）→ 标记 cancelled，不入 _baskets。
        否则入 _baskets，等待 scheduled_dt 的 T+1 drain。
        """
        basket = self._current_basket
        self._current_basket = None
        if basket is None:
            return None
        if not basket.sell_orders and not basket.buy_orders:
            basket.status = "cancelled"
        self._baskets.append(basket)
        return basket

    def abort_basket_context(self, reason: str = "callback_exception"):
        """G1-I audit-fix 阻断6: handle_data 异常时放弃半成品 basket（不提交到待 drain 队列）。

        - 归还所有 pending sell orders 的 pending_sell_shares；
        - buy orders 无 cash 归还（T 日未预扣）；
        - 所有 order status=cancelled/rejected，reason=<reason>；
        - basket status=cancelled，**不进入 _baskets**（T+1 不产生成交）；
        - _current_basket=None。
        """
        basket = self._current_basket
        self._current_basket = None
        if basket is None:
            return None
        for po in basket.sell_orders + basket.buy_orders:
            if po.status != "pending":
                continue
            if po.direction == "sell":
                pos = self.account.positions.get(po.code)
                if pos:
                    pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)
            po.status = "cancelled"
            po.reason = reason
        basket.status = "cancelled"
        # 不 append 到 _baskets：放弃的半成品不进入 T+1 drain（审计上仍返回引用供调用方记录）
        return basket

    def _intent_direction(self, instruction, target_value, shares, pos):
        """G1-I: 从指令意图判定方向（不依赖估算结果），供冲突检查用。

        与 _estimate_pending 的区别：即使 _estimate_pending 因边界返回 None（如 sell_all-0），
        本函数仍能给出意图方向，保证 §4.3 冲突检测不漏判。
        """
        sell_instructions = ("sell_shares", "sell_value", "sell_all")
        if instruction in sell_instructions:
            return "sell"
        if instruction in ("buy_shares", "buy_value"):
            return "buy"
        if instruction == "target_value":
            return "sell" if target_value == 0 else "buy"
        if instruction == "target_shares":
            current = pos.volume if pos else 0
            delta = (shares or 0) - current
            return "buy" if delta >= 0 else "sell"
        return None

    def order_in_basket(self, security, instruction,
                        target_value=None, shares=None) -> Order:
        """G1-I: basket 内下单（设计 v2 §4.3/§5）。

        与 _create_pending_order 的区别：
        - 订单挂在当前 basket 上（basket_id 非空），不进 _pending_orders。
        - 卖单仍预扣 pending_sell_shares（§5.1）；买单不预扣 cash（§5.2，T+1 原子预检）。
        - 同一 bare code 唯一性检查（§4.3）：重复/冲突/不支持 target → 拒绝后者。
        """
        basket = self._current_basket
        if basket is None:
            raise RuntimeError("order_in_basket 需在 push_basket_context / submit_basket 之间调用")
        bare = str(security).split(".")[0]
        code = self._to_qmt(bare)
        created_dt = basket.created_dt
        scheduled_dt = basket.scheduled_dt

        # 末日下单：无下一交易日 → 创建即拒
        if self._next_trade_day_str(created_dt) is None:
            return Order(order_id=f"basket_{code}_{created_dt}_rej",
                         security=security, direction="unknown",
                         status="rejected", reason="no_next_trade_day", created_dt=created_dt)

        est_price = self._t_day_close_prices.get(code, 0)
        if est_price <= 0:
            return Order(order_id=f"basket_{code}_{created_dt}_rej",
                         security=security, direction="unknown",
                         status="rejected", reason="no_price", created_dt=created_dt)

        # §4.3: 同一 bare code 唯一性（已入篮的 code 集合）
        existing_codes = {po.code for po in basket.sell_orders + basket.buy_orders}

        # MVP 限制（§4.3）：对已持仓标的只接受 target_value=0 的 sell-all；
        # 对未持仓标的只接受 target_value>0 的 buy；其余增减仓 BLOCK。
        pos = self.account.positions.get(code)

        def _reject(reason, direction="unknown"):
            return Order(order_id=f"basket_{code}_{created_dt}_rej",
                         security=security, direction=direction,
                         status="rejected", reason=reason, created_dt=created_dt)

        # 先判定方向 + MVP target 合法性（复用 _estimate_pending 的方向逻辑）
        direction, est_shares, est_cost = self._estimate_pending(
            code, instruction, target_value, shares, est_price)

        # §4.3 唯一性 / 冲突检查（先于 noop / MVP target 检查：同 code 冲突优先归 conflict/duplicate）。
        # 用"指令意图方向"判定，避免 _estimate_pending 在边界（如 sell_all-0）返回 None 导致漏判。
        intent_direction = self._intent_direction(instruction, target_value, shares, pos)
        if code in existing_codes:
            prior_dirs = {po.direction for po in basket.sell_orders + basket.buy_orders
                          if po.code == code}
            check_dir = direction or intent_direction
            if check_dir is None:
                return _reject("noop")
            if check_dir in prior_dirs:
                return _reject("basket_duplicate_order", check_dir)
            else:
                return _reject("basket_conflicting_order", check_dir)

        if direction is None:
            return _reject("noop")

        # MVP 限制：只允许 sell_all（已持仓清仓）和纯买入（未持仓建仓）
        if pos and pos.volume > 0:
            # 已持仓：只接受 sell_all（target_value=0）
            if instruction != "sell_all" and not (instruction == "target_value" and target_value == 0):
                return _reject("basket_unsupported_target", direction)
        else:
            # 未持仓：只接受买入类（direction=buy）
            if direction != "buy":
                return _reject("basket_unsupported_target", direction)

        # 卖单：预扣 pending_sell_shares（§5.1，与 legacy 一致）；买单：不预扣 cash（§5.2）
        if direction == "sell":
            avail = (pos.can_sell - pos.pending_sell_shares) if pos else 0
            if avail < est_shares:
                return _reject("insufficient_sellable", direction)
            pos.pending_sell_shares += est_shares
            est_cost = 0.0  # 卖单不锁现金
        # 买单：不预扣 cash / locked_cash（§5.2 变更）

        order_id = f"basket_{code}_{created_dt}_{self._basket_seq}_{len(basket.buy_orders) + len(basket.sell_orders)}"
        po = PendingOrder(
            order_id=order_id, created_dt=created_dt, scheduled_dt=scheduled_dt,
            security=security, code=code, instruction=instruction, direction=direction,
            target_value=target_value, shares=shares,
            est_cost=est_cost, est_shares=est_shares, status="pending",
            basket_id=basket.basket_id)
        if direction == "buy":
            basket.buy_orders.append(po)
        else:
            basket.sell_orders.append(po)
        logger.debug(f"[G1-I] basket {basket.basket_id} 入篮 {direction} {code} "
                     f"instr={instruction} est_shares={est_shares} T={created_dt} T+1={scheduled_dt}")
        return Order(order_id=order_id, security=security, direction=direction,
                     status="pending", created_dt=created_dt, basket_id=basket.basket_id)

    def _drain_baskets(self, t1_data, t1_day_str: str, t1_open_prices: dict):
        """G1-I: T+1 开盘 basket drain 状态机（设计 v2 §6）。

        处理顺序（§11）：独立订单（basket_id=None）已由 _drain_pending_orders 先 drain；
        本方法只处理 basket（按 basket_id 排序）。

        每个 scheduled_dt == T+1 的 basket 按五阶段处理：
          Phase 1: 卖单优先（bare code 字典序）→ _execute_sell 已 cash += net_proceeds
          Phase 2: mandatory sell 失败检查 → buy leg 全拒（reason=mandatory_sell_failed）
          Phase 3A: buy-leg 原子预检（T+1 实际价计算 actual_required_cash）
          Phase 3B: buy-leg 执行判定（total > cash 或 preflight 失败 → 全拒）
          Phase 5: basket status 更新（§10 真值表）

        t1_open_prices: T+1 open 价字典。
        """
        from .libs.shared_ashare_rules import is_price_limit_blocked
        # 按 basket_id 排序（§6.3）
        baskets_to_drain = sorted(
            [b for b in self._baskets if b.status == "pending" and b.scheduled_dt == t1_day_str],
            key=lambda b: b.basket_id)
        for basket in baskets_to_drain:
            self._drain_single_basket(basket, t1_data, t1_day_str, t1_open_prices,
                                      is_price_limit_blocked)

    def _drain_single_basket(self, basket, t1_data, t1_day_str, t1_open_prices,
                              is_price_limit_blocked):
        """G1-I: 单个 basket 的五阶段 drain（设计 v2 §6.1）。"""
        # Phase 1: 卖单优先（bare code 字典序，§6.3）
        basket.sell_orders.sort(key=lambda po: po.code)
        basket.buy_orders.sort(key=lambda po: po.code)
        sells = basket.sell_orders
        cash_before_sells = self.account.cash
        sell_has_failure = False
        sell_any_filled = False
        for po in sells:
            ok = self._drain_basket_sell(po, t1_data, t1_day_str, t1_open_prices,
                                          is_price_limit_blocked)
            if ok:
                sell_any_filled = True
            else:
                sell_has_failure = True
        cash_after_sells = self.account.cash
        # §6.2: realized_sell_proceeds 仅审计元数据（_execute_sell 已计入 cash，不重复加）
        basket.realized_sell_proceeds = cash_after_sells - cash_before_sells

        # Phase 2: mandatory sell 失败检查 → buy leg 全拒（§7）。
        # audit-fix 阻断4: mandatory sell 被 cancel 同样中止 buy leg（mandatory_sell_cancelled）。
        if sell_has_failure or basket.mandatory_sell_cancelled:
            reject_reason = "mandatory_sell_failed" if sell_has_failure else "mandatory_sell_cancelled"
            for buy_po in basket.buy_orders:
                buy_po.status = "rejected"
                buy_po.reason = reject_reason
            basket.status = "partial" if sell_any_filled else "rejected"
            self._record_basket_orders_today(basket)
            return

        # 无卖单或卖单全过 → 进入 buy leg
        buys = basket.buy_orders

        # Phase 3A: buy-leg 原子预检（T+1 实际价计算 actual_required_cash，§7.2）
        preflight = []  # [(buy_po, actual_shares, actual_value, ok, reject_reason)]
        total_required_cash = 0.0
        preflight_any_failed = False
        for buy_po in buys:
            ok, actual_shares, actual_value, reject_reason, req_cash = self._buy_preflight_one(
                buy_po, t1_data, t1_day_str, t1_open_prices, is_price_limit_blocked)
            preflight.append((buy_po, actual_shares, actual_value, ok, reject_reason))
            if ok:
                total_required_cash += req_cash
            else:
                preflight_any_failed = True

        # Phase 3B: buy-leg 执行判定（全过才执行，否则全拒，§7.2）
        if preflight_any_failed or total_required_cash > self.account.cash + 1e-9:
            for buy_po, _ash, _av, ok, reject_reason in preflight:
                buy_po.status = "rejected"
                # 已被预检标记具体原因（halted/limit_up/direction_changed/rounding）的保留；
                # 否则统一归为资金不足
                buy_po.reason = reject_reason if (not ok and reject_reason) else "insufficient_cash_after_sells"
            basket.status = "partial" if (sell_any_filled or any(
                po.status == "filled" for po in basket.sell_orders)) else "rejected"
            self._record_basket_orders_today(basket)
            return

        # 全部预检通过 + 资金充足 → 依次执行（§6.1 Phase 3B）。
        # audit-fix 阻断5: 执行阶段某笔 buy 意外失败（_execute_buy 返回 0）→
        # 当前订单 + 所有剩余 rejected(reason=execution_failed_after_preflight)，不再继续。
        exec_failed = False
        for buy_po, actual_shares, actual_value, _ok, _rr in preflight:
            if exec_failed:
                buy_po.status = "rejected"
                buy_po.reason = "execution_failed_after_preflight"
                continue
            vol, fp = self._execute_buy(
                buy_po.code, t1_open_prices.get(buy_po.code, 0),
                buy_value=actual_value if actual_value else None,
                buy_shares=actual_shares if actual_shares else None,
                date=t1_day_str, curr_data=t1_data)
            if vol > 0:
                buy_po.status = "filled"
                buy_po.filled_amount = vol
                buy_po.price = fp
                buy_po.filled = vol * fp
                buy_po.filled_dt = t1_day_str
            else:
                buy_po.status = "rejected"
                buy_po.reason = "execution_failed_after_preflight"
                exec_failed = True

        # Phase 5: basket status 更新（§10 真值表）
        sell_all_filled = all(po.status == "filled" for po in basket.sell_orders) if basket.sell_orders else True
        buy_all_filled = all(po.status == "filled" for po in basket.buy_orders) if basket.buy_orders else True
        if sell_all_filled and buy_all_filled:
            basket.status = "completed"
        else:
            basket.status = "partial"
        self._record_basket_orders_today(basket)

    def _record_basket_orders_today(self, basket):
        """G1-I audit-fix 阻断3: basket drain 后把 filled/rejected Order 放入 _today_orders，
        使 get_order / get_orders 能查询（含 basket_id）。"""
        for po in basket.sell_orders + basket.buy_orders:
            self._today_orders.append(self._po_to_order(po, filled=(po.status == "filled")))

    def _drain_basket_sell(self, po, t1_data, t1_day_str, t1_open_prices,
                            is_price_limit_blocked) -> bool:
        """G1-I: Phase 1 单笔卖单 drain。返回是否成交（True=filled）。"""
        t1_open = t1_open_prices.get(po.code, 0)
        if t1_open <= 0:
            po.status = "rejected"; po.reason = "no_price"
            self._release_basket_sell_reservation(po)
            return False
        if self._is_halted_at(po.code, t1_data):
            po.status = "rejected"; po.reason = "halted"
            self._release_basket_sell_reservation(po)
            return False
        pct_chg = self._get_open_pct_chg(po.code, t1_data, t1_open)
        # direction=0 查跌停阻断（§8：跌停卖 blocked）
        if is_price_limit_blocked(po.code, 0, pct_chg):
            po.status = "rejected"; po.reason = "limit_down_blocked"
            self._release_basket_sell_reservation(po)
            return False
        # 释放预扣（防 double-count，§6.2）
        self._release_basket_sell_reservation(po)
        # 用 T+1 open 执行（_execute_sell 内部已 cash += net_proceeds）
        # target_value=0 与 sell_all 等价（清仓，§4.3 MVP）
        is_sell_all = po.instruction == "sell_all" or (
            po.instruction == "target_value" and po.target_value == 0)
        vol, fp = self._execute_sell(
            po.code, t1_open,
            sell_all=is_sell_all,
            sell_value=po.target_value if po.instruction == "sell_value" else None,
            sell_shares=po.shares if po.instruction == "sell_shares" else None,
            date=t1_day_str, curr_data=t1_data)
        if vol > 0:
            po.status = "filled"; po.filled_amount = vol; po.price = fp
            po.filled = vol * fp; po.filled_dt = t1_day_str
            return True
        po.status = "rejected"; po.reason = "insufficient_cash_or_rounding"
        return False

    def _release_basket_sell_reservation(self, po):
        """G1-I: 释放卖单预扣的 pending_sell_shares（drain 执行前调用，防 double-count）。"""
        pos = self.account.positions.get(po.code)
        if pos:
            pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)

    def cancel_basket_order(self, basket, po):
        """G1-I: 取消 basket 内订单 + 归还预扣（设计 v2 §9.1）。

        - sell order → 精确减少 pending_sell_shares（by est_shares）
        - buy order → 无需归还（未预扣 cash）
        - 从 basket 移除该 order；basket 变空 → status = "cancelled"
        - refund 幂等：已 filled/rejected/cancelled 的 order 不得重复归还（状态不变）
        """
        # 幂等：仅 pending 状态可 cancel
        if po.status != "pending":
            return False
        if po.direction == "sell":
            pos = self.account.positions.get(po.code)
            if pos:
                pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)
            # audit-fix 阻断4: 取消 mandatory sell → 标记 basket，drain 时 buy leg 全拒
            basket.mandatory_sell_cancelled = True
        # buy 无预扣，无需归还
        po.status = "cancelled"
        po.reason = "user_cancelled"
        # 从 basket 移除
        if po in basket.sell_orders:
            basket.sell_orders.remove(po)
        if po in basket.buy_orders:
            basket.buy_orders.remove(po)
        if not basket.sell_orders and not basket.buy_orders:
            basket.status = "cancelled"
        return True

    def _expire_remaining_baskets(self):
        """G1-I: 主循环结束，仍 pending 的 basket 标记 expired，归还卖单预扣（设计 v2 §9.2）。

        不变式：end-of-backtest 后所有 pending_sell_shares == 0。
        """
        for basket in self._baskets:
            if basket.status != "pending":
                continue
            for po in basket.sell_orders + basket.buy_orders:
                if po.status != "pending":
                    continue
                if po.direction == "sell":
                    pos = self.account.positions.get(po.code)
                    if pos:
                        pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)
                po.status = "expired"
                po.reason = "end_of_backtest"
            basket.status = "expired"

    def _buy_preflight_one(self, po, t1_data, t1_day_str, t1_open_prices,
                            is_price_limit_blocked):
        """G1-I: Phase 3A 单笔买单原子预检（§7.2）。

        返回 (ok, actual_shares, actual_value, reject_reason, required_cash)。
        actual_shares/actual_value 二选一非 None（None 表示用另一口径传给 _execute_buy）。
        """
        from .libs.shared_ashare_rules import round_to_lot
        t1_open = t1_open_prices.get(po.code, 0)
        if t1_open <= 0:
            return False, None, None, "no_price", 0.0
        if self._is_halted_at(po.code, t1_data):
            return False, None, None, "halted", 0.0
        pct_chg = self._get_open_pct_chg(po.code, t1_data, t1_open)
        # direction=1 查涨停阻断（§8：涨停买 blocked）
        if is_price_limit_blocked(po.code, 1, pct_chg):
            return False, None, None, "limit_up_blocked", 0.0

        # 用 T+1 实际价重算股数/金额（延迟解析，§6/§7.2）
        pos = self.account.positions.get(po.code)
        if po.instruction == "buy_value":
            actual_shares = None
            actual_value = po.target_value
        elif po.instruction == "buy_shares":
            actual_shares = po.shares
            actual_value = None
        elif po.instruction == "target_value":
            current_value = (pos.volume * t1_open) if pos and pos.volume > 0 else 0
            delta = po.target_value - current_value
            if po.target_value == 0:
                return False, None, None, "noop", 0.0
            if delta <= 0:
                # §5.3: T+1 跳空导致方向翻转（T 日判 buy，T+1 重算后应卖）→ reject
                return False, None, None, "direction_changed_at_drain", 0.0
            if current_value > 0 and abs(delta) / current_value < self.min_rebalance_pct:
                return False, None, None, "below_rebalance_threshold", 0.0
            actual_shares = None
            actual_value = delta
        elif po.instruction == "target_shares":
            current = pos.volume if pos else 0
            delta = (po.shares or 0) - current
            if delta <= 0:
                return False, None, None, "direction_changed_at_drain", 0.0
            actual_shares = round_to_lot(delta, 100)
            actual_value = None
        else:
            return False, None, None, "unknown_instruction", 0.0

        # 用 T+1 价计算实际所需现金（含佣金/过户费；买入侧本就无印花税）
        if actual_shares is not None:
            shares_int = round_to_lot(actual_shares, 100)
        else:
            shares_int = round_to_lot(int(actual_value / t1_open), 100)
        if shares_int <= 0:
            return False, None, None, "insufficient_cash_or_rounding", 0.0
        fill_price = self._apply_slippage(t1_open, "buy")
        cost_amount = shares_int * fill_price
        commission = max(cost_amount * self.cost.commission_rate, self.cost.min_commission)
        transfer_fee = cost_amount * self.cost.transfer_fee_rate
        required_cash = cost_amount + commission + transfer_fee
        return True, actual_shares, actual_value, "", required_cash

    def _reject_pending(self, po: PendingOrder, status: str, reason: str):
        """拒单/expire/cancel 公共归还逻辑。status ∈ {rejected, expired, cancelled} 三态独立。
        用 po.est_cost/est_shares 精确归还预扣，防累积漂移。"""
        if po.direction == "buy":
            self.account.locked_cash -= po.est_cost
            self.account.cash += po.est_cost
        else:  # sell
            pos = self.account.positions.get(po.code)
            if pos:
                pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)
        po.status = status
        po.reason = reason

    def _reject_drain(self, po: PendingOrder, reason: str):
        """PR2 drain 拒单公共路径（设计文档 §2.1）：归还预扣 + 拒单采集（QS_FILL_AUDIT 口径）。

        与 _finalize_immediate 同一排除规则：below_rebalance_threshold 属正常微调跳过，不采集。
        """
        self._reject_pending(po, "rejected", reason)
        if reason != "below_rebalance_threshold":
            self._day_rejections.append((po.code, po.direction, reason))
        self._today_orders.append(self._po_to_order(po, filled=False))

    def _cancel_pending_order(self, order_id: str):
        """PR2: cancel_order 按 order_id 精确移除目标单 + 归还预扣。
        多日重复挂单允许累积，本方法只处理指定单。"""
        for idx, po in enumerate(self._pending_orders):
            if po.order_id == order_id and po.status == "pending":
                self._reject_pending(po, "cancelled", "user_cancelled")
                self._pending_orders.pop(idx)
                return True
        return False

    def _expire_remaining_pending(self):
        """PR2: 主循环结束，仍 pending 的订单标记 expired，原路归还预扣。
        主计划 7.13 "末日订单标记 expired 或保留 pending"。"""
        for po in self._pending_orders:
            if po.status == "pending":
                self._reject_pending(po, "expired", "end_of_backtest")

    def _is_halted_at(self, code, curr_data) -> bool:
        """停牌判断（与 ptrade_api.py:631,1497 一致）：suspendFlag==1 OR volume==0。"""
        if curr_data is None:
            return False
        bare = code.split(".")[0] if "." in code else code
        if 'code' not in curr_data.columns:
            return False
        idx = self._df_index(curr_data)
        if idx is not None:
            i = idx.get(bare)
            if i is None:
                return False
            r = curr_data.iloc[i]
        else:
            # 索引不可用 → 回退原布尔过滤（保留原 try/except 异常行为）
            try:
                row = curr_data[curr_data['code'] == bare]
            except Exception:
                return False
            if len(row) == 0:
                return False
            r = row.iloc[0]
        suspend = r.get('suspendFlag', 0)
        volume = r.get('volume', 0)
        try:
            suspend = float(suspend)
        except (TypeError, ValueError):
            suspend = 0
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            volume = 0
        return suspend == 1 or volume == 0

    def _resolve_at_t1(self, po: PendingOrder, t1_open: float):
        """T+1 drain 时用 T+1 open + T+1 持仓重算 delta/shares（延迟解析，修正点 3）。

        返回 (actual_shares, actual_value, skip_reason)：
        - skip_reason 非空 → 跳过（below_rebalance_threshold 等），预扣原路归还
        - actual_shares / actual_value 二选一传给 _execute_buy/_execute_sell（None 表示用另一口径）
        """
        code = po.code
        pos = self.account.positions.get(code)

        if po.instruction == "target_value":
            current_value = (pos.volume * t1_open) if pos and pos.volume > 0 else 0
            if po.target_value == 0:
                return None, None, ""  # sell_all 路径
            delta = po.target_value - current_value
            if current_value > 0 and abs(delta) / current_value < self.min_rebalance_pct:
                return None, None, "below_rebalance_threshold"
            if delta > 0:
                return None, delta, ""       # 买入增量
            elif delta < 0:
                return None, abs(delta), ""  # 卖出增量
            else:
                return None, None, "noop"
        elif po.instruction == "buy_value":
            return None, po.target_value, ""
        elif po.instruction == "sell_value":
            return None, po.target_value, ""
        elif po.instruction == "buy_shares":
            return po.shares, None, ""
        elif po.instruction == "sell_shares":
            return po.shares, None, ""
        elif po.instruction == "target_shares":
            current = pos.volume if pos else 0
            delta = (po.shares or 0) - current
            if delta > 0:
                return delta, None, ""
            elif delta < 0:
                return abs(delta), None, ""
            else:
                return None, None, "noop"
        elif po.instruction == "sell_all":
            return None, None, ""
        return None, None, "unknown_instruction"

    def _drain_pending_orders(self, t1_data, t1_day_str: str, t1_open_prices: dict):
        """PR2: T+1 开盘事件。用 T+1 open 价 + T+1 状态执行 scheduled_dt == T+1 的 pending orders。

        主计划 7.13: T+1 开盘前执行 pending queue；使用 T+1 开盘价和 T+1 状态；
        停牌/涨停/跌停/资金不足返回明确原因。成交则正式过户，trade_record 日期=T+1；
        跳空重算股数超预扣 → 整单拒单 insufficient_cash_or_rounding，原路归还，不缩单。

        t1_open_prices: T+1 open 价字典（主循环单独构建，不是 match_prices）。
        """
        from .libs.shared_ashare_rules import is_price_limit_blocked
        still_pending = []
        for po in self._pending_orders:
            if po.status != "pending" or po.scheduled_dt != t1_day_str:
                still_pending.append(po)
                continue

            t1_open = t1_open_prices.get(po.code, 0)
            if t1_open <= 0:
                self._reject_drain(po, "no_price")
                continue

            # 停牌检查（suspendFlag==1 OR volume==0）
            if self._is_halted_at(po.code, t1_data):
                self._reject_drain(po, "halted")
                continue

            # 涨跌停检查（用 T+1 当日 pct_chg）
            pct_chg = self._get_open_pct_chg(po.code, t1_data, t1_open)
            direction_int = 1 if po.direction == "buy" else 0
            if is_price_limit_blocked(po.code, direction_int, pct_chg):
                reason = "limit_up_blocked" if po.direction == "buy" else "limit_down_blocked"
                self._reject_drain(po, reason)
                continue

            # 延迟解析：用 T+1 价 + T+1 持仓重算
            actual_shares, actual_value, skip_reason = self._resolve_at_t1(po, t1_open)
            if skip_reason:
                self._reject_drain(po, skip_reason)
                continue

            # 释放预扣（防 double-count）
            if po.direction == "buy":
                self.account.locked_cash -= po.est_cost
                self.account.cash += po.est_cost
            else:
                pos = self.account.positions.get(po.code)
                if pos:
                    pos.pending_sell_shares = max(0, pos.pending_sell_shares - po.est_shares)

            # 用 T+1 open 正式执行（复用现有账本，trade_record 日期=T+1）
            if po.direction == "buy":
                vol, fp = self._execute_buy(
                    po.code, t1_open,
                    buy_value=actual_value if actual_value else None,
                    buy_shares=actual_shares if actual_shares else None,
                    date=t1_day_str, curr_data=t1_data)
            else:
                vol, fp = self._execute_sell(
                    po.code, t1_open,
                    sell_all=(po.instruction == "sell_all"),
                    sell_value=actual_value if actual_value else None,
                    sell_shares=actual_shares if actual_shares else None,
                    date=t1_day_str, curr_data=t1_data)

            if vol == 0:
                # 资金不足/整手为 0 → 整单拒单（不缩单，与 _execute_buy 现有 PTrade 语义一致）
                po.status = "rejected"
                po.reason = "insufficient_cash_or_rounding"
                self._day_rejections.append((po.code, po.direction, po.reason))
                self._today_orders.append(self._po_to_order(po, filled=False))
            else:
                po.status = "filled"
                po.filled_amount = vol
                po.price = fp
                po.filled = vol * fp
                po.filled_dt = t1_day_str
                self._today_orders.append(self._po_to_order(po, filled=True))
                logger.debug(f"[PR2] drain 成交 {po.direction} {po.code} "
                             f"{vol}@{fp:.2f} T+1={t1_day_str}")
        self._pending_orders = still_pending

    def _po_to_order(self, po: PendingOrder, filled: bool) -> Order:
        """把 PendingOrder 转为 Order（供 _today_orders / get_order 返回）。"""
        return Order(
            order_id=po.order_id, security=po.security, direction=po.direction,
            target=abs(po.target_value) if po.target_value else 0.0,
            filled=po.filled,
            target_amount=abs(po.shares) if po.shares else 0,
            filled_amount=po.filled_amount,
            price=po.price, status=po.status, reason=po.reason,
            created_dt=po.created_dt, filled_dt=po.filled_dt,
            basket_id=po.basket_id)

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
                # §3.6: before_trading_start 不并入 basket（_current_basket 仍 None → legacy pending）
                self.strategy['before_trading_start'](ctx, data)
        except Exception as e:
            logger.error(f"[Ptrade] before_trading_start 错误: {e}")
        try:
            # G1-I: handle_data 内的订单形成一个 basket（§3.6）。
            # §3.6 顺序：before_trading_start / run_daily 走 legacy（_current_basket=None）；
            # 仅 handle_data 在 basket context 内。因此 push → handle_data → submit，
            # submit 之后才执行 run_daily，确保 run_daily 的订单进 _pending_orders（legacy）。
            if self.basket_active:
                self.push_basket_context(day_str)
            if 'handle_data' in self.strategy:
                self.strategy['handle_data'](ctx, data)
            # handle_data 结束立即提交 basket（audit-fix 阻断2：run_daily 不并入 basket）
            if self.basket_active and self._current_basket is not None:
                self.submit_basket()
            # run_daily 注册任务与 handle_data 可并存；日线引擎每天均执行一次。
            # 此时 _current_basket 已 None → run_daily 订单走 legacy pending。
            daily_tasks = getattr(_api, '_daily_tasks', [])
            for func, _time in daily_tasks:
                func(ctx)
        except Exception as e:
            logger.error(f"[Ptrade] handle_data 错误: {e}")
            # 异常时 abort basket context（audit-fix 阻断6：不提交半成品 basket）
            if self.basket_active and self._current_basket is not None:
                self.abort_basket_context("callback_exception")

        # 即时执行模式：不需要返回信号，交易已在策略执行过程中即时完成

    # ===================== PR4: 分钟事件驱动回测 =====================

    def _build_daily_pctchg_map(self, daily_data) -> dict:
        """PR4: 从当日日线 snapshot 构建日级 pctChg 字典（分钟涨跌停判断用）。

        分钟 bar 的 preClose 不复权（除权日风险），故涨跌停用日级 pctChg（已复权校正）。
        """
        pctchg = {}
        if daily_data is None or len(daily_data) == 0:
            return pctchg
        for _, row in daily_data.iterrows():
            bare = str(row.get('code', ''))
            close = row.get('close', 0)
            preclose = row.get('preClose', 0)
            if preclose and preclose > 0:
                pctchg[self._to_qmt(bare)] = (close - preclose) / preclose
        return pctchg

    def _run_daily_open_close_proxy_day(self, i, day, trade_days, benchmark_raw, first_bench):
        """Run two causal synthetic intraday snapshots from a completed daily bar.

        The 09:31 snapshot exposes only the known opening print (OHLC all equal
        to daily open).  The 15:00 snapshot exposes the completed daily OHLC.
        This profile is a generic daily-data proxy for strategies whose
        confirmed semantics require open entry and completed-close decisions
        when historical minute coverage is unavailable.
        """
        from .ptrade_api import _api, Context, Portfolio, DataDict
        from .minute_scheduler import _MinuteScheduler

        day_str = day.strftime('%Y-%m-%d')
        if i > 0:
            prev_day = trade_days[i - 1]
        else:
            prev_date = self._providers.calendar.get_trading_day(day_str, offset=-1)
            prev_day = (pd.Timestamp(prev_date, tz='Asia/Shanghai').to_pydatetime()
                        if prev_date is not None else day)
        prev_day_str = prev_day.strftime('%Y-%m-%d')
        prev_data = (self._last_curr_data if i > 0 and getattr(self, '_last_curr_data', None) is not None
                     else self._get_daily_data(prev_day))
        daily_data = self._get_daily_data(day)
        if daily_data is None or len(daily_data) == 0:
            logger.debug("[DailyProxy] %s no daily data, skipped", day_str)
            return
        self._last_curr_data = daily_data
        self._current_date_str = day_str
        self._proxy_intraday_bars = []

        # 【v2-final 分钟挂钩（2026-08-16）】ETF 除权补正与日线主循环一致：
        # daily_data/prev_data 已加载（行 2094-2096），策略执行前完成送股/折算/合并补正。
        self._apply_factor_derived_split(daily_data, prev_data, day_str)

        for pos in self.account.positions.values():
            pos.can_sell = pos.volume

        prev_close_prices = ({self._to_qmt(c): v for c, v in zip(prev_data['code'], prev_data['close'])}
                             if prev_data is not None and len(prev_data) > 0 else {})
        ptrade_positions = self._get_ptrade_positions(prev_close_prices)
        portfolio = Portfolio(self.account.cash, ptrade_positions)
        ctx = Context(day_str, prev_day_str, portfolio)
        self._ptrade_context = ctx
        _api.attach_day(self, daily_data, prev_data, day_str, prev_day_str, prev_close_prices)

        preopen_data = DataDict()
        preopen_data.set_curr_data(prev_data if prev_data is not None else daily_data, prev_day_str)
        try:
            if 'before_trading_start' in self.strategy:
                self.strategy['before_trading_start'](ctx, preopen_data)
        except Exception as exc:
            logger.error("[DailyProxy] before_trading_start error: %s", exc)

        open_bar = daily_data.copy()
        for field in ('high', 'low', 'close'):
            open_bar[field] = open_bar['open']
        if 'amount' in open_bar.columns:
            open_bar['amount'] = 0.0
        if 'volume' in open_bar.columns:
            halted = ((open_bar.get('suspendFlag', 0) == 1) | (daily_data['volume'] <= 0))
            open_bar['volume'] = (~halted).astype(float)

        close_bar = daily_data.copy()
        snapshots = [
            (pd.Timestamp(f"{day_str} 09:31:00", tz='Asia/Shanghai'), open_bar),
            (pd.Timestamp(f"{day_str} 15:00:00", tz='Asia/Shanghai'), close_bar),
        ]
        scheduler = _MinuteScheduler(getattr(_api, '_daily_tasks', []))
        scheduler.reset_day()
        for bar_ts, raw_bar in snapshots:
            bar_df = raw_bar.copy()
            bar_df['time'] = int(bar_ts.value // 10**6)
            self._proxy_intraday_bars.append(bar_df.copy())
            ctx.current_dt = bar_ts
            if hasattr(ctx, 'blotter') and ctx.blotter is not None:
                ctx.blotter.current_dt = bar_ts
            bar_prices = {self._to_qmt(c): v for c, v in zip(bar_df['code'], bar_df['close'])}
            pct_map = self._build_daily_pctchg_map(bar_df)
            _api.attach_bar(self, bar_df, day_str, prev_day_str, bar_prices,
                            pct_map, current_bar_ts=bar_ts)
            scheduler.dispatch_if_match(ctx, bar_ts)
            if 'handle_data' in self.strategy:
                bar_data = DataDict()
                bar_data.set_curr_data(bar_df, bar_ts.strftime('%Y-%m-%d %H:%M:%S'))
                try:
                    self.strategy['handle_data'](ctx, bar_data)
                except Exception as exc:
                    logger.error("[DailyProxy] handle_data error @ %s: %s", bar_ts, exc)

        try:
            if 'after_trading_end' in self.strategy:
                self.strategy['after_trading_end'](ctx, self._build_data_dict(daily_data, day_str))
        except Exception as exc:
            logger.debug("[DailyProxy] after_trading_end error: %s", exc)

        daily_close_prices = {self._to_qmt(c): v for c, v in zip(daily_data['code'], daily_data['close'])}
        nav = self.account.total_asset_at_price(daily_close_prices)
        bench_close = benchmark_raw.get(day_str, first_bench)
        bench_nav = bench_close / first_bench * 100 if first_bench else 100.0
        self.result.nav_history.append({
            'date': day_str,
            'nav': nav,
            'cash': self.account.cash,
            'market_value': self.account.market_value_at_price(daily_close_prices),
            'benchmark': bench_nav,
            'positions': len([p for p in self.account.positions.values() if p.volume > 0]),
        })

    def _load_minute_snapshots(self, day_str, daily_data):
        """PR4: 加载当日全 universe 的分钟 bar，按 bar timestamp 分组。

        返回 ([(bar_ts, bar_snapshot_df), ...], all_bars)：
        - snapshots 按 time 升序；
        - all_bars 为当日全 universe 的原始分钟 DataFrame（fq=None 未替换 OHLC，
          time 列为 epoch 毫秒 int）——Phase 4B 供 ptrade_api 内存切片（零 DB 往返）。
        universe = daily_data 中的 code（当日有交易的证券）。

        真实数据修复（2026-07-22）：原逐 code 循环（5525 只 × 4 次 DB 调用）在真实全
        universe 上导致 duckdb C 扩展 GIL 累积崩溃（Fatal PyEval_SaveThread）。改为
        批量查询：query_minute_bars_by_range_batch 一次 SQL per 表（stock_minutes/etf_minutes），
        单日 DB 往返 ≤ 2 次（与 universe 大小无关）。个别 code 无分钟数据自然不在结果集
        （与原"跳过"语义一致），整表无 freq 数据则 raise FrequencyCapabilityError（契约不变）。
        """
        from .providers.frequency_labels import FrequencyCapabilityError
        import pandas as pd
        if daily_data is None or len(daily_data) == 0:
            return [], None
        codes = [str(c) for c in daily_data['code'].unique()]
        try:
            all_bars = self._providers.market._data.query_minute_bars_by_range_batch(
                codes, day_str, day_str, '1min', None,
                getattr(self._providers.market, '_calendar', None))
        except FrequencyCapabilityError:
            # 整 universe 无分钟数据（表空或缺 freq），与原"全无则 raise"契约一致
            raise FrequencyCapabilityError(
                "TABLE_EMPTY", api_freq='1m', table="stock_minutes/etf_minutes",
                detail=f"{day_str} 全 universe 无分钟数据")
        if len(all_bars) == 0:
            raise FrequencyCapabilityError(
                "TABLE_EMPTY", api_freq='1m', table="stock_minutes/etf_minutes",
                detail=f"{day_str} 全 universe 无分钟数据")
        # 按 time 分组为 (bar_ts, snapshot_df)
        snapshots = []
        for ts_ms, group in all_bars.groupby('time'):
            bar_ts = pd.Timestamp(ts_ms, unit='ms', tz='Asia/Shanghai')
            snapshots.append((bar_ts, group))
        # Phase 4B：all_bars 原样返回（零拷贝），供 ptrade_api 当日分钟历史内存切片
        return snapshots, all_bars

    def _run_minute_day(self, i, day, trade_days, benchmark_raw, first_bench):
        """PR4: 分钟事件驱动——一个交易日的完整生命周期（主计划 7.24）。

        生命周期：T+1 解锁 → attach_day → before_trading_start(1次) →
        每 bar[更新 current_dt → attach_bar → run_daily 精确调度 → handle_data] →
        after_trading_end(1次) → 日终净值。

        修正缺口 1：attach_bar 注入 current_bar_ts，get_history/get_price 锚定到此（无未来泄漏）。
        修正缺口 2：attach_day 传昨日收盘价（before_trading_start 在 09:31 前，不见当日收盘）。
        """
        from .ptrade_api import _api, Context, Portfolio, DataDict
        from .minute_scheduler import _MinuteScheduler
        import pandas as pd

        day_str = day.strftime('%Y-%m-%d')
        if i > 0:
            prev_day = trade_days[i - 1]
            prev_day_str = prev_day.strftime('%Y-%m-%d')
        else:
            # 第一日：用 calendar 取前一交易日（返回 date），包装成 prev_day_str
            prev_date = self._providers.calendar.get_trading_day(day_str, offset=-1)
            prev_day_str = prev_date.strftime('%Y-%m-%d') if prev_date else day_str
            prev_day = prev_date   # date 对象，供 _get_daily_data 用（该函数接受 date/datetime）

        # ① 取当日 + 前日日线 snapshot
        daily_data = self._get_daily_data(day)
        if len(daily_data) == 0:
            logger.debug(f"[PR4] {day_str} 无日线数据，跳过")
            return
        prev_data = self._last_curr_data if getattr(self, '_last_curr_data', None) is not None \
            else self._get_daily_data(prev_day)
        self._last_curr_data = daily_data

        # 【v2-final 分钟挂钩（2026-08-16）】ETF 除权补正与日线主循环一致：
        # daily_data/prev_data 已加载（行 2244-2249），策略执行前完成送股/折算/合并补正
        # （_apply_corporate_actions/_apply_etf_cash_dividends 已在主循环行 514-516 生效）。
        self._apply_factor_derived_split(daily_data, prev_data, day_str)

        # 【修正缺口 2】_prices 用昨日收盘价（before_trading_start 在 09:31 前，不见当日收盘）
        prev_close_prices = {self._to_qmt(c): v
                             for c, v in zip(prev_data['code'], prev_data['close'])} if len(prev_data) > 0 else {}
        daily_pctchg = self._build_daily_pctchg_map(daily_data)

        # ② T+1 解锁（盘前全证券一致；ETF T+0 差异只在 _execute_buy 买入后）
        for pos in self.account.positions.values():
            pos.can_sell = pos.volume

        # ③ attach_day（每日一次，含 preload）—— 传 prev_close_prices（修正缺口 2）
        self._current_date_str = day_str
        ptrade_positions = self._get_ptrade_positions(prev_close_prices)
        portfolio = Portfolio(self.account.cash, ptrade_positions)
        ctx = Context(day_str, prev_day_str, portfolio)
        self._ptrade_context = ctx
        _api.attach_day(self, daily_data, prev_data, day_str, prev_day_str, prev_close_prices)

        # ④ before_trading_start（每日一次；data 用当日日线，策略可读当日选股——与日线一致）
        data_bts = DataDict()
        data_bts.set_curr_data(daily_data, day_str)
        try:
            if 'before_trading_start' in self.strategy:
                self.strategy['before_trading_start'](ctx, data_bts)
        except Exception as e:
            logger.error(f"[PR4] before_trading_start 错误: {e}")

        # ⑤ 遍历当日所有分钟 bar（09:31-11:30, 13:01-15:00，含 15:00 收盘 bar）
        bar_snapshots, all_bars = self._load_minute_snapshots(day_str, daily_data)
        if all_bars is not None and len(all_bars) > 0:
            # Phase 4B：把当日全 universe 分钟原始数据（fq=None 未替换 OHLC）注入
            # ptrade_api，get_history(frequency='1m') 优先内存切片（零 DB 往返）；
            # PIT 截断仍由调用侧 bar_cutoff_ms（当前 bar 时间戳）保证，语义与 SQL 路径一致。
            _api.attach_day_minute_history(all_bars, day_str)
        scheduler = _MinuteScheduler(getattr(_api, '_daily_tasks', []))
        scheduler.reset_day()
        for bar_ts, bar_df in bar_snapshots:
            bar_ts_str = bar_ts.strftime('%Y-%m-%d %H:%M:%S')
            # 双更新 current_dt（ctx.current_dt + ctx.blotter.current_dt）
            ctx.current_dt = bar_ts
            if hasattr(ctx, 'blotter') and ctx.blotter is not None:
                ctx.blotter.current_dt = bar_ts
            # 该 bar 全 universe 收盘价（end-labeled：bar.close 是该分钟真实收盘）
            bar_prices = {}
            for _, row in bar_df.iterrows():
                bare = str(row.get('code', ''))
                bar_prices[self._to_qmt(bare)] = row.get('close', 0)
            # 【修正缺口 1】attach_bar 注入 current_bar_ts
            _api.attach_bar(self, bar_df, day_str, prev_day_str, bar_prices,
                            daily_pctchg, current_bar_ts=bar_ts)
            # 精确触发匹配该 bar 时刻的 run_daily（scheduler 在 handle_data 之前，主计划 7.24）
            scheduler.dispatch_if_match(ctx, bar_ts)
            # handle_data（每 bar 一次）
            bar_data = DataDict()
            bar_data.set_curr_data(bar_df, bar_ts_str)
            try:
                if 'handle_data' in self.strategy:
                    self.strategy['handle_data'](ctx, bar_data)
            except Exception as e:
                logger.error(f"[PR4] handle_data 错误 @ {bar_ts_str}: {e}")

        # ⑥ after_trading_end（每日一次，用当日日线 snapshot）
        try:
            if 'after_trading_end' in self.strategy:
                self.strategy['after_trading_end'](ctx, self._build_data_dict(daily_data, day_str))
        except Exception as e:
            logger.debug(f"[PR4] after_trading_end 错误: {e}")

        # ⑦ 日终净值（用当日日线 close 估值，收盘后记账，无穿越）
        daily_close_prices = {self._to_qmt(c): v
                              for c, v in zip(daily_data['code'], daily_data['close'])}
        nav = self.account.total_asset_at_price(daily_close_prices)
        bench_close = benchmark_raw.get(day_str, first_bench)
        bench_nav = bench_close / first_bench * 100 if first_bench else 100.0
        self.result.nav_history.append({
            'date': day_str,
            'nav': nav,
            'cash': self.account.cash,
            'market_value': self.account.market_value_at_price(daily_close_prices),
            'benchmark': bench_nav,
            'positions': len([p for p in self.account.positions.values() if p.volume > 0]),
        })

        # 【补齐主计划漏项】分钟进度回调（每日一次，保持签名兼容，不每 bar 回调）
        if self._progress_callback:
            self._progress_callback(i + 1, len(trade_days), day_str)

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

    def _build_empty_trade_days_error(self) -> str:
        """构建"无交易日"错误的细化诊断信息。

        原行为：raise ValueError("No trading days in backtest range: {start} ~ {end}")
        现行为：异常类型仍为 ValueError，message 追加诊断上下文，帮助区分
          - 数据库连接失败/文件缺失
          - 库有数据但不在回测区间（显示实际 MIN/MAX）
        成功路径不受影响（仅在空交易日分支调用）。诊断全程被 getattr/try-except
        守卫，任何诊断失败一律回退到原始 message，绝不引入新的失败路径。
        """
        base = f"No trading days in backtest range: {self.start} ~ {self.end}"

        # 安全获取诊断信息：provider 可能是测试 mock（无 diagnose 方法）
        diagnose_fn = getattr(self._providers.calendar, "diagnose", None)
        if not callable(diagnose_fn):
            return base  # mock/不支持诊断 → 保持原样，零行为变更

        try:
            info = diagnose_fn() or {}
        except Exception:
            return base  # 诊断自身异常不得掩盖原始错误

        if not info:
            return base  # diagnose() 返回 None/{}（基类默认）→ 保持原样

        if not info.get("connection_ok"):
            return (
                f"{base}\n"
                f"  原因：无法连接数据库。请检查：\n"
                f"    - 数据库路径：{info.get('db_path')}\n"
                f"    - 文件是否存在：{info.get('file_exists')}\n"
                f"    - 连接异常：{info.get('error') or '（无详细信息，可能文件被其它进程独占占用）'}\n"
                f"  建议：关闭其它占用该数据库的程序（daemon、其它 GUI 实例）后重试；"
                f"确认 config/data_config.json 的 path 指向正确的 quantstudio.db。"
            )

        try:
            min_t = info.get("min_time")
            max_t = info.get("max_time")
            n = info.get("distinct_days")
            min_str = (pd.Timestamp(min_t, unit="ms", tz="Asia/Shanghai").strftime("%Y-%m-%d")
                       if min_t is not None else "无数据")
            max_str = (pd.Timestamp(max_t, unit="ms", tz="Asia/Shanghai").strftime("%Y-%m-%d")
                       if max_t is not None else "无数据")
            return (
                f"{base}\n"
                f"  原因：数据库中没有该区间的行情数据。stock_daily 实际覆盖范围：\n"
                f"    - 日期范围：{min_str} ~ {max_str}\n"
                f"    - 交易日总数：{n if n is not None else 0}\n"
                f"    - 数据库路径：{info.get('db_path')}\n"
                f"  建议：请先在「采集任务」Tab 采集 {self.start} ~ {self.end} 期间的 stock_daily 数据"
                f"（确认 TUSHARE_TOKEN 已配置），采集完成后重新回测。"
            )
        except Exception:
            return base

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

    @staticmethod
    def _lookup_curr_row(curr_data, code: str):
        """P-D13b C4a：curr_data（当日快照 DataFrame）按 code 匹配行（归一对照）。

        返回 dict（row）或 None；code 支持裸码/QMT 后缀/平台后缀任一形态。
        """
        if curr_data is None or not hasattr(curr_data, "columns"):
            return None
        try:
            target = BacktestEngine._to_qmt(str(code).split(".")[0])
            for _, row in curr_data.iterrows():
                if BacktestEngine._to_qmt(str(row.get("code", ""))[:10]) == target:
                    return row.to_dict()
        except Exception:
            pass
        return None

    def _apply_delist_force_close(self, day_str, curr_data, prev_data):
        """P-D13b C4b：退市强平保真开关（默认关；开时对齐平台 is expired 强平）。

        检测：持仓 code 不在当日快照 code 集（当日无行情）→ 按最后已知价强平。
        审计：强平行 log 带 `fidelity_delist` 标记（验收②：与普通卖出可区分）。
        强平复用 _execute_sell（费用/滑点/记账同构），不新造卖出路径。
        """
        try:
            from .ptrade_api import _api as _ptrade_api
            _fid = getattr(_ptrade_api, "_fidelity", None)
            if _fid is None or not getattr(_fid, "fidelity_delist_force_close", False):
                return
            day_codes = set()
            if curr_data is not None and hasattr(curr_data, "columns"):
                for c in curr_data.get("code", []):
                    try:
                        day_codes.add(self._to_qmt(str(c)[:10]))
                    except Exception:
                        pass
            last_prices = {}
            if prev_data is not None and hasattr(prev_data, "columns"):
                for c, p in zip(prev_data.get("code", []), prev_data.get("close", [])):
                    try:
                        last_prices[self._to_qmt(str(c)[:10])] = float(p)
                    except Exception:
                        pass
            for code, pos in list(self.account.positions.items()):
                if pos.volume <= 0:
                    continue
                norm = self._to_qmt(str(code).split(".")[0])
                if norm not in day_codes and norm in last_prices:
                    price = last_prices[norm]
                    logger.warning(
                        f"[fidelity_delist] date={day_str} code={code} "
                        f"force_close volume={pos.volume} @ {price}（当日无行情=平台 is expired 强平）")
                    self._execute_sell(code, price, sell_all=True,
                                       date=day_str, curr_data=prev_data)
        except Exception:
            pass  # 防护：强平异常不阻断回测（默认关行为保持）


def _is_etf_code(code: str) -> bool:
    """PR4 决策 4：ETF 判定，走 PR1 的 security_code_rules（不新写分类逻辑）。"""
    from .libs.security_code_rules import is_etf
    return is_etf(code)
