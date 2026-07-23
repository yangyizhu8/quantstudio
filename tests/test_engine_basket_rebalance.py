"""G1-I 契约测试：next_open + callback_basket 引擎再平衡（设计 v2 §14 测试矩阵 + 不变式）。

设计文档：docs/strategy-compiler/engine-basket-rebalance-design.md
语义版本：0.4.0-next_open_basket
激活条件（三条件同时满足）：
    engine_profile == "daily-bar-v1"
    match_price_mode == "next_open"
    rebalance_mode == "callback_basket"

测试矩阵（设计 §14，25 项 + §15 不变式 + hermetic 增强 = 32 项 hermetic）：
  版本门禁 & 隔离（#1-4, #21-22）
  basket 数据结构 & 唯一性（#11-12）
  T+1 drain 状态机（#5-8, #13, #14-16, #18）
  涨跌停/方向翻转（#19-20, #13）
  cancel/expire 归还（#9-10, #25）
  status 真值表（#17）
  不变式（#23-24）

hermetic = 不连真实 DuckDB；引擎用临时 db_path 字符串 + mock _next_trade_day_str，
所有数据用合成 DataFrame 注入。与 test_next_open_pending_orders.py 同款测试风格。
"""
import pytest
import pandas as pd


# ========== 辅助构造 ==========

def _make_basket_engine(cash=100_000, rebalance_mode="callback_basket"):
    """构造 basket 模式引擎（不连真实库）。

    默认三条件激活：daily-bar-v1 + next_open + callback_basket。
    """
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-31",
        match_price_mode="next_open",
        engine_profile="daily-bar-v1",
        rebalance_mode=rebalance_mode,
    )
    engine.account.cash = cash
    engine._current_date_str = "2026-01-05"
    return engine


def _make_next_open_legacy_engine(cash=100_000):
    """构造 next_open + legacy 引擎（验证非回归）。"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    engine = BacktestEngine(
        db_path="/tmp/test.db", strategy={},
        start="2026-01-01", end="2026-01-31",
        match_price_mode="next_open",
        engine_profile="daily-bar-v1",
        rebalance_mode="legacy",
    )
    engine.account.cash = cash
    engine._current_date_str = "2026-01-05"
    return engine


def _t1_data(rows):
    """构造 T+1 全市场快照 DataFrame。

    rows: [(bare, open, close, preclose, volume, suspend), ...]
    """
    codes, opens, closes, pcs, vols, sus = [], [], [], [], [], []
    for bare, o, c, pc, v, s in rows:
        codes.append(bare); opens.append(o); closes.append(c)
        pcs.append(pc); vols.append(v); sus.append(s)
    return pd.DataFrame({
        'code': codes,
        'open': opens, 'close': closes, 'preClose': pcs,
        'volume': vols, 'suspendFlag': sus,
    })


# ========== §14 #1-4, #21: 版本门禁 & 隔离 ==========

def test_activation_default_off_is_legacy_semantics():
    """#1 显式激活默认关闭：rebalance_mode=legacy → 0.2.0 语义"""
    engine = _make_next_open_legacy_engine()
    assert engine.rebalance_mode == "legacy"
    assert engine.engine_semantics_version == "0.2.0-next_open_pending"


def test_basket_activation_sets_version_040():
    """#3 daily basket version=0.4.0：三条件满足 → engine_semantics_version"""
    engine = _make_basket_engine()
    assert engine.rebalance_mode == "callback_basket"
    assert engine.engine_semantics_version == "0.4.0-next_open_basket"


def test_minute_basket_is_blocked():
    """#4 minute-bar-v1 + callback_basket → raise（显式 BLOCK，非静默退化）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    with pytest.raises(ValueError, match="basket"):
        BacktestEngine(
            db_path="/tmp/test.db", strategy={},
            start="2026-01-05", end="2026-01-05",
            engine_profile="minute-bar-v1",
            match_price_mode="close",
            rebalance_mode="callback_basket",
        )


def test_invalid_rebalance_mode_rejected():
    """非法 rebalance_mode 抛 ValueError"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    with pytest.raises(ValueError, match="rebalance_mode"):
        BacktestEngine(
            db_path="/tmp/test.db", strategy={},
            start="2026-01-05", end="2026-01-05",
            engine_profile="daily-bar-v1",
            match_price_mode="next_open",
            rebalance_mode="bogus",
        )


def test_close_open_mode_zero_touch_no_basket():
    """#21 close/open 模式零触达：close/open 模式无 basket（version 仍 legacy）"""
    from quantstudio.backtest.backtest_engine import BacktestEngine
    for mode in ("close", "open"):
        engine = BacktestEngine(
            db_path="/tmp/test.db", strategy={},
            start="2026-01-05", end="2026-01-05",
            engine_profile="daily-bar-v1",
            match_price_mode=mode,
            rebalance_mode="callback_basket",  # 即使显式开 basket
        )
        # close/open 模式 basket 不激活：version 不是 0.4.0
        assert engine.engine_semantics_version == "0.1.0-legacy"
        assert engine._baskets == []


# ========== §4 数据结构 + §3.6 basket context 边界 ==========

def test_basket_context_push_pop_around_handle_data():
    """§3.6: 每次 handle_data 调用一个 basket；引擎 push → 提交 → pop。

    before_trading_start / run_daily 不并入 basket（走 legacy pending）。
    """
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    # handle_data 之前：无活跃 basket
    assert engine._current_basket is None

    # push basket context（模拟 _run_ptrade_strategy 在 handle_data 前 push）
    engine.push_basket_context("2026-01-05")
    assert engine._current_basket is not None
    basket = engine._current_basket
    assert basket.created_dt == "2026-01-05"
    assert basket.scheduled_dt == "2026-01-06"
    assert basket.status == "pending"
    assert basket.sell_orders == [] and basket.buy_orders == []

    # handle_data 内下单 → 订单入 basket（而非 _pending_orders）
    engine.order_in_basket("600000.SH", instruction="target_value", target_value=10_000)
    assert len(basket.buy_orders) == 1
    assert len(engine._pending_orders) == 0  # basket 订单不进 legacy 队列

    # pop + 提交 basket
    engine.submit_basket()
    assert engine._current_basket is None
    assert len(engine._baskets) == 1
    assert engine._baskets[0].status == "pending"


def test_basket_order_carries_basket_id():
    """§4.2: PendingOrder 扩展 basket_id（None = 独立订单 legacy）"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="target_value", target_value=10_000)
    engine.submit_basket()

    basket = engine._baskets[0]
    assert basket.basket_id.startswith("basket_")
    for po in basket.buy_orders + basket.sell_orders:
        assert po.basket_id == basket.basket_id


def test_basket_id_format_contains_created_dt_and_seq():
    """§4.1: basket_id = "basket_{created_dt}_{seq}"，同日递增"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    engine.submit_basket()
    b1 = engine._baskets[0]
    engine.push_basket_context("2026-01-05")
    engine.submit_basket()
    b2 = engine._baskets[1]
    assert b1.basket_id == "basket_20260105_001"
    assert b2.basket_id == "basket_20260105_002"


# ========== §4.3 同一 bare code 唯一性（#11, #12） ==========

def test_duplicate_same_direction_same_code_rejected():
    """#11 同一 bare code + 同方向重复订单：拒绝后者（basket_duplicate_order）"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    o1 = engine.order_in_basket("600000.SH", instruction="target_value", target_value=10_000)
    assert o1.status == "pending"
    o2 = engine.order_in_basket("600000.SH", instruction="target_value", target_value=5_000)
    assert o2.status == "rejected"
    assert o2.reason == "basket_duplicate_order"
    assert len(engine._current_basket.buy_orders) == 1


def test_conflicting_buy_sell_same_code_rejected():
    """#11 同一 bare code 买卖方向冲突：拒绝后者（basket_conflicting_order）

    用未持仓 code：先 buy 建仓（合法），再 sell_all（与已入篮 buy 冲突）。
    """
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    # 先买（未持仓 code，合法 buy_value）
    o1 = engine.order_in_basket("600000.SH", instruction="buy_value", target_value=10_000)
    assert o1.status == "pending"
    # 再卖同一 code（sell_all）→ 方向冲突
    o2 = engine.order_in_basket("600000.SH", instruction="sell_all")
    assert o2.status == "rejected"
    assert o2.reason == "basket_conflicting_order"


def test_sh_ss_suffix_treated_as_same_bare_code():
    """#12 .SH/.SS 不同后缀同一 bare code 视为同一证券"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=100_000)
    # 600000 normalize 后为 600000.SH；策略用 .SS 后缀也归一
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    # 用 .SS 后缀卖（sell_all）
    o1 = engine.order_in_basket("600000.SS", instruction="sell_all")
    assert o1.status == "pending"
    # 用 .SH 后缀再买同一 code（buy_value 增量买入，无 noop 边界）→ 冲突
    o2 = engine.order_in_basket("600000.SH", instruction="buy_value", target_value=20_000)
    assert o2.status == "rejected"
    assert o2.reason == "basket_conflicting_order"


def test_basket_unsupported_target_rejected():
    """§4.3 MVP 限制：已持仓标的的增减仓 target（非 sell-all / 非 zero）→ BLOCK"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=100_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    # 已持仓 10000 元，调仓到 5000 元（减仓，非 sell-all）→ MVP BLOCK
    o = engine.order_in_basket("600000.SH", instruction="target_value", target_value=5_000)
    assert o.status == "rejected"
    assert o.reason == "basket_unsupported_target"


# ========== §4.1 T 日 cash 不变性（§5.4） ==========

def test_basket_t_day_cash_unchanged_by_buy_orders():
    """§5.4: basket 内买单不预扣 cash（T 日 cash 只受独立订单影响）"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    cash_before = engine.account.cash
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="target_value", target_value=10_000)
    assert engine.account.cash == cash_before  # 买单 T 日不动 cash
    assert engine.account.locked_cash == 0     # 不预扣 locked_cash
    engine.submit_basket()
    assert engine.account.cash == cash_before  # 提交后仍不变


def test_basket_sell_t_day_locks_pending_sell_shares():
    """§5.1: basket 内卖单仍预扣 pending_sell_shares（与 legacy 一致），不预释放 cash"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    cash_before = engine.account.cash
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.submit_basket()

    pos = engine.account.positions["600000.SH"]
    assert pos.pending_sell_shares == 1000  # 预扣
    assert engine.account.cash == cash_before  # 不预释放 cash
    assert engine.account.locked_cash == 0


# ========== §10 basket 结构：buy-only / sell-only（#15, #16） ==========

def test_buy_only_basket_has_no_sell_orders():
    """#15 buy-only basket：无 sells，buys 正常入篮"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="target_value", target_value=10_000)
    engine.order_in_basket("600001.SH", instruction="target_value", target_value=10_000)
    engine.submit_basket()

    basket = engine._baskets[0]
    assert len(basket.sell_orders) == 0
    assert len(basket.buy_orders) == 2


def test_sell_only_basket_has_no_buy_orders():
    """#16 sell-only basket：无 buys，sells 正常入篮"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine.account.positions["600001.SH"] = Position(
        code="600001.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="sell_all")
    engine.submit_basket()

    basket = engine._baskets[0]
    assert len(basket.sell_orders) == 2
    assert len(basket.buy_orders) == 0


# ========== §6 T+1 drain 状态机 ==========

def _setup_rotation_basket(engine, sell_code="600000.SH", buy_code="600001.SH",
                            sell_volume=1000, t_day_price=10.0, buy_value=9000):
    """构造一个标准轮动 basket：卖 A → 买 B。返回 basket。"""
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions[sell_code] = Position(
        code=sell_code, volume=sell_volume, avg_cost=t_day_price, can_sell=sell_volume)
    engine._t_day_close_prices = {sell_code: t_day_price, buy_code: t_day_price}
    engine._next_trade_day_str = lambda d: "2026-01-06"

    engine.push_basket_context("2026-01-05")
    engine.order_in_basket(sell_code, instruction="sell_all")
    engine.order_in_basket(buy_code, instruction="buy_value", target_value=buy_value)
    engine.submit_basket()
    return engine._baskets[0]


def test_drain_sell_then_buy_cash_not_double_counted():
    """#5 卖出所得不双计：cash delta == net_proceeds（不重复加）"""
    engine = _make_basket_engine(cash=5_000)
    basket = _setup_rotation_basket(engine, sell_code="600000.SH", buy_code="600001.SH",
                                     sell_volume=1000, t_day_price=10.0, buy_value=9000)
    cash_before_drain = engine.account.cash

    # T+1: 卖价 10.5（open），买价 10.5（open）— preClose 10.0 → +5%，不触发涨停
    t1_data = _t1_data([
        ("600000", 10.5, 10.5, 10.0, 100000, 0),
        ("600001", 10.5, 10.5, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.5, "600001.SH": 10.5}
    # 解锁（主循环 drain 前已解锁）
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    # 卖单净所得（审计元数据）= cash 增量中归因于卖出的部分
    sell_proceeds_net = basket.realized_sell_proceeds
    assert sell_proceeds_net > 0
    buy_po = basket.buy_orders[0]
    assert buy_po.status == "filled"
    # 买单实际消耗的现金（从 trade_records 直接读取，含佣金/过户费）
    buy_tr = [t for t in engine.result.trade_records if t['action'] == 'buy'][0]
    buy_consumed = buy_tr['volume'] * buy_tr['price'] + max(
        buy_tr['volume'] * buy_tr['price'] * engine.cost.commission_rate, engine.cost.min_commission) \
        + buy_tr['volume'] * buy_tr['price'] * engine.cost.transfer_fee_rate
    # 不双计不变式：cash delta == sell_net - buy_consumed（卖出所得只计一次）
    expected_cash_delta = sell_proceeds_net - buy_consumed
    actual_cash_delta = engine.account.cash - cash_before_drain
    assert actual_cash_delta == pytest.approx(expected_cash_delta, rel=1e-6)
    assert basket.status == "completed"


def test_drain_buy_preflight_uses_t1_actual_price():
    """#6 T+1 actual-cost preflight：用 T+1 实际价计算，非 T 日 est_cost"""
    engine = _make_basket_engine(cash=5_000)
    # T 日价 10.0，T+1 跳空到 10.8（+8%，未涨停）：若用 T 日 est_cost 判资金会误判
    basket = _setup_rotation_basket(engine, sell_code="600000.SH", buy_code="600001.SH",
                                     sell_volume=1000, t_day_price=10.0, buy_value=9_000)
    # 买入 est_cost（T 日）≈ 900*10 + 费
    buy_po = basket.buy_orders[0]
    est_cost_t_day = buy_po.est_cost
    assert est_cost_t_day > 0  # T 日 est_cost 记录在案（审计）

    # T+1 价 10.8：9000/10.8 = 833 → 800 股（与 T 日 900 股不同，证明用 T+1 价重算）
    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.8, 10.8, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.8}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    # 用 T+1 实际价 10.8 成交：800 股（非 T 日估算的 900 股）
    assert buy_po.status == "filled"
    assert buy_po.price == pytest.approx(10.8, abs=0.05)  # open ±滑点
    assert buy_po.filled_amount == 800  # 9000/10.8=833 → 整手 800，证明用 T+1 价


def test_drain_buy_leg_insufficient_cash_all_rejected():
    """#7 buy leg 资金不足→0 笔 buy filled（全拒非缩单）"""
    engine = _make_basket_engine(cash=0)  # 无额外现金，全靠卖出所得
    # 卖 1000 股@10 = 10000 所得；但买 2 只各 buy_value=9000 需 ~18000 → 资金不足
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.order_in_basket("600002.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
        ("600002", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    # 卖单成交，买单全拒（原子预检：总需求 > 可用现金）
    assert basket.sell_orders[0].status == "filled"
    for buy_po in basket.buy_orders:
        assert buy_po.status == "rejected"
        assert buy_po.reason == "insufficient_cash_after_sells"
    assert basket.status == "partial"


def test_drain_mandatory_sell_failure_rejects_all_buys():
    """#8 mandatory sell 失败→buy leg 0 笔（已成交 sell 不回滚）"""
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position
    # 卖 600000（T+1 跌停，被阻断）；买 600001
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    # T+1: 600000 跌停（preClose 10.0, close 9.0 = -10%）→ 卖出阻断
    t1_data = _t1_data([
        ("600000", 9.0, 9.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 9.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.sell_orders[0].status == "rejected"
    assert basket.sell_orders[0].reason == "limit_down_blocked"
    for buy_po in basket.buy_orders:
        assert buy_po.status == "rejected"
        assert buy_po.reason == "mandatory_sell_failed"
    assert basket.status == "rejected"  # 无任何成交
    # sell 失败：pending_sell_shares 归还
    pos = engine.account.positions["600000.SH"]
    assert pos.pending_sell_shares == 0


def test_drain_limit_up_blocks_buy_allows_sell():
    """#19 涨停买阻断、涨停卖允许：direction=buy+涨停→blocked；direction=sell+涨停→OK"""
    engine = _make_basket_engine(cash=5_000)
    from quantstudio.backtest.backtest_engine import Position
    # 600000 已持仓，T+1 涨停 → 卖允许；600001 未持仓，T+1 涨停 → 买阻断
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    # T+1: 两者均涨停（preClose 10.0, close 11.0 = +10%）
    t1_data = _t1_data([
        ("600000", 11.0, 11.0, 10.0, 100000, 0),
        ("600001", 11.0, 11.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 11.0, "600001.SH": 11.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.sell_orders[0].status == "filled"   # 涨停卖允许
    assert basket.buy_orders[0].status == "rejected"
    assert basket.buy_orders[0].reason == "limit_up_blocked"
    assert basket.status == "partial"


def test_drain_limit_down_blocks_sell_allows_buy():
    """#20 跌停卖阻断、跌停买允许：direction=sell+跌停→blocked；direction=buy+跌停→OK"""
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position
    # 600000 已持仓 T+1 跌停 → 卖阻断；600001 未持仓 T+1 跌停 → 买允许
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    # T+1: 两者均跌停（preClose 10.0, close 9.0 = -10%）
    t1_data = _t1_data([
        ("600000", 9.0, 9.0, 10.0, 100000, 0),
        ("600001", 9.0, 9.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 9.0, "600001.SH": 9.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.sell_orders[0].status == "rejected"
    assert basket.sell_orders[0].reason == "limit_down_blocked"
    # sell 失败 → buy leg 全拒（mandatory sell failed）
    for buy_po in basket.buy_orders:
        assert buy_po.status == "rejected"
        assert buy_po.reason == "mandatory_sell_failed"


def test_drain_halted_sell_rejected():
    """§8 停牌：卖单停牌→rejected(halted)"""
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    # T+1: 600000 停牌（suspendFlag=1）
    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 0, 1),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.sell_orders[0].status == "rejected"
    assert basket.sell_orders[0].reason == "halted"
    for buy_po in basket.buy_orders:
        assert buy_po.reason == "mandatory_sell_failed"


def test_drain_completed_when_all_filled():
    """§10 所有 sell + 所有 buy 均 filled → completed"""
    engine = _make_basket_engine(cash=5_000)
    basket = _setup_rotation_basket(engine, buy_value=9000)
    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.status == "completed"
    assert all(po.status == "filled" for po in basket.sell_orders + basket.buy_orders)


def test_drain_completed_sell_only_basket():
    """§10 sell-only basket，所有 sell filled → completed"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([("600000", 10.0, 10.0, 10.0, 100000, 0)])
    t1_open = {"600000.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.status == "completed"
    assert basket.sell_orders[0].status == "filled"


def test_drain_completed_buy_only_basket():
    """§10 buy-only basket，所有 buy filled → completed"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([("600001", 10.0, 10.0, 10.0, 100000, 0)])
    t1_open = {"600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    assert basket.status == "completed"
    assert basket.buy_orders[0].status == "filled"


# ========== §6.3 确定性排序（#18） ==========

def test_drain_deterministic_code_ordering():
    """#18 跨进程插入顺序不同→drain 顺序一致（bare code 字典序）"""
    engine = _make_basket_engine(cash=5_000)
    from quantstudio.backtest.backtest_engine import Position
    # 逆序插入卖单：600002, 600001, 600000
    for c in ("600002.SH", "600001.SH", "600000.SH"):
        engine.account.positions[c] = Position(code=c, volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {c: 10.0 for c in
                                  ("600000.SH", "600001.SH", "600002.SH")}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    for c in ("600002.SH", "600001.SH", "600000.SH"):
        engine.order_in_basket(c, instruction="sell_all")
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([(c.split(".")[0], 10.0, 10.0, 10.0, 100000, 0)
                        for c in ("600000.SH", "600001.SH", "600002.SH")])
    t1_open = {c: 10.0 for c in ("600000.SH", "600001.SH", "600002.SH")}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    # drain 后 sell_orders 按字典序：600000 < 600001 < 600002
    drained_codes = [po.code for po in basket.sell_orders]
    assert drained_codes == ["600000.SH", "600001.SH", "600002.SH"]


# ========== §11 独立订单与 basket 优先级（#14） ==========

def test_independent_orders_drain_before_basket():
    """#14 混合独立订单 + basket：独立先 drain，basket 用剩余 cash"""
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position
    # 独立卖单（legacy pending）：卖 600000
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    # 独立卖单（basket_id=None）走 _pending_orders
    engine._create_pending_order("600000.SH", instruction="sell_all")
    # basket：买 600001 + 600002，用卖出所得
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=8000)
    engine.order_in_basket("600002.SH", instruction="buy_value", target_value=8000)
    engine.submit_basket()

    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
        ("600002", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    # 先 drain 独立订单（legacy），再 drain basket
    engine._drain_pending_orders(t1_data, "2026-01-06", t1_open)
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)

    basket = engine._baskets[0]
    # 独立卖单成交（所得现金供 basket 用）
    assert engine._pending_orders == [] or all(po.status != "pending" for po in engine._pending_orders)
    # basket 用剩余现金买 600001 + 600002
    assert basket.status in ("completed", "partial")


# ========== §5.3 方向翻转（#13） ==========

def test_buy_preflight_direction_change_logic_directly():
    """#13 核心逻辑：target_value 重算后 delta<=0 → direction_changed_at_drain

    用未涨停价直接验证翻转检测（绕开涨停拦截）。
    """
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position, PendingOrder
    from quantstudio.backtest.libs.shared_ashare_rules import is_price_limit_blocked
    # 持仓 1000 股，T+1 价 10.5（+5%，未涨停）。target_value=5000 < 持仓市值 10500 → delta<0 → 翻转
    engine.account.positions["600001.SH"] = Position(
        code="600001.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    po = PendingOrder(
        order_id="test_dc2", created_dt="2026-01-05", scheduled_dt="2026-01-06",
        security="600001.SH", code="600001.SH", instruction="target_value",
        direction="buy", target_value=5000, est_cost=5000, est_shares=0,
        status="pending", basket_id="basket_test")
    t1_data = _t1_data([("600001", 10.5, 10.5, 10.0, 100000, 0)])
    t1_open = {"600001.SH": 10.5}
    ok, ash, av, rr, rc = engine._buy_preflight_one(
        po, t1_data, "2026-01-06", t1_open, is_price_limit_blocked)
    assert ok is False
    assert rr == "direction_changed_at_drain"


# ========== §9 cancel / expire 归还（#9, #10, #25） ==========

def test_cancel_basket_sell_restores_pending_sell_shares():
    """#9 cancel sell reservation restore：pending_sell_shares 精确归还"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]
    sell_po = basket.sell_orders[0]

    assert engine.account.positions["600000.SH"].pending_sell_shares == 1000
    # cancel basket order
    engine.cancel_basket_order(basket, sell_po)
    assert sell_po.status == "cancelled"
    assert engine.account.positions["600000.SH"].pending_sell_shares == 0  # 精确归还
    assert sell_po not in basket.sell_orders


def test_cancel_basket_buy_no_reservation_to_restore():
    """§9.1 cancel buy order → 无需归还（未预扣 cash）"""
    engine = _make_basket_engine(cash=100_000)
    engine._t_day_close_prices = {"600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]
    buy_po = basket.buy_orders[0]
    cash_before = engine.account.cash

    engine.cancel_basket_order(basket, buy_po)
    assert buy_po.status == "cancelled"
    assert engine.account.cash == cash_before  # 无现金变动
    assert engine.account.locked_cash == 0


def test_cancel_empty_basket_marked_cancelled():
    """§9.1 basket 变空 → status = cancelled"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.submit_basket()
    basket = engine._baskets[0]

    engine.cancel_basket_order(basket, basket.sell_orders[0])
    assert basket.status == "cancelled"


def test_cancel_is_idempotent_for_filled_order():
    """§9.1 refund 幂等：已 filled 的 order 不得重复归还"""
    engine = _make_basket_engine(cash=5_000)
    basket = _setup_rotation_basket(engine, buy_value=9000)
    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)
    sell_po = basket.sell_orders[0]
    assert sell_po.status == "filled"
    cash_before = engine.account.cash
    # 再次 cancel 一个已 filled 的 sell → 不应重复归还 pending_sell_shares / 不动 cash
    engine.cancel_basket_order(basket, sell_po)
    assert sell_po.status == "filled"  # 状态不变
    assert engine.account.cash == cash_before


def test_expire_remaining_baskets_restores_reservations():
    """#10 expire sell reservation restore：末日归还，pending_sell_shares=0"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine.account.positions["600001.SH"] = Position(
        code="600001.SH", volume=500, avg_cost=10.0, can_sell=500)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600002.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="sell_all")
    engine.order_in_basket("600002.SH", instruction="buy_value", target_value=5000)
    engine.submit_basket()

    engine._expire_remaining_baskets()
    basket = engine._baskets[0]
    assert basket.status == "expired"
    # §9.2 不变式：end-of-backtest 后所有 pending_sell_shares == 0
    for pos in engine.account.positions.values():
        assert pos.pending_sell_shares == 0


# ========== §10 status 真值表（#17） ==========

def test_status_truth_table_rejected_no_fills():
    """§10 无任何订单 filled → rejected"""
    engine = _make_basket_engine(cash=20_000)
    from quantstudio.backtest.backtest_engine import Position
    # 卖单全跌停拒 + 买单 → mandatory_sell_failed → 全拒 → rejected
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([
        ("600000", 9.0, 9.0, 10.0, 100000, 0),  # 跌停
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 9.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)
    assert basket.status == "rejected"


def test_status_truth_table_partial_sells_filled_buys_rejected():
    """§10 sells filled + buys 全拒 → partial

    卖 1 只（所得 ~9989），买 2 只各 9000（总需求 ~18006 > 所得）→ 买单全拒，sells 成交 → partial。
    """
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=0)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.order_in_basket("600002.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()
    basket = engine._baskets[0]

    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
        ("600002", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0, "600002.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)
    # 卖单成交，买单全拒（资金不足：~18006 > ~9989）→ partial
    assert basket.sell_orders[0].status == "filled"
    for buy_po in basket.buy_orders:
        assert buy_po.status == "rejected"
    assert basket.status == "partial"


# ========== 不变式（#23, #24） ==========

def test_invariant_cash_non_negative_after_drain():
    """#23 cash >= 0：所有路径不变式"""
    engine = _make_basket_engine(cash=0)
    basket = _setup_rotation_basket(engine, buy_value=999999)  # 远超所得
    t1_data = _t1_data([
        ("600000", 10.0, 10.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 10.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)
    assert engine.account.cash >= 0
    assert basket.status in ("partial", "rejected")


def test_invariant_pending_sell_le_can_sell_after_drain():
    """#24 pending_sell_shares <= can_sell：所有路径不变式"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0, "600001.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.order_in_basket("600001.SH", instruction="buy_value", target_value=9000)
    engine.submit_basket()

    # 卖单跌停拒（未成交）→ pending_sell_shares 必须归还 → <= can_sell
    t1_data = _t1_data([
        ("600000", 9.0, 9.0, 10.0, 100000, 0),
        ("600001", 10.0, 10.0, 10.0, 100000, 0),
    ])
    t1_open = {"600000.SH": 9.0, "600001.SH": 10.0}
    for pos in engine.account.positions.values():
        pos.can_sell = pos.volume
    engine._drain_baskets(t1_data, "2026-01-06", t1_open)
    for pos in engine.account.positions.values():
        assert pos.pending_sell_shares <= pos.can_sell
        assert pos.pending_sell_shares >= 0


def test_invariant_reservations_zero_after_expire():
    """#25 cancel/expire 后 reservation 归零：pending_sell_shares==0"""
    from quantstudio.backtest.backtest_engine import Position
    engine = _make_basket_engine(cash=5_000)
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine._t_day_close_prices = {"600000.SH": 10.0}
    engine._next_trade_day_str = lambda d: "2026-01-06"
    engine.push_basket_context("2026-01-05")
    engine.order_in_basket("600000.SH", instruction="sell_all")
    engine.submit_basket()
    engine._expire_remaining_baskets()
    for pos in engine.account.positions.values():
        assert pos.pending_sell_shares == 0


# ========== §3.6 / §12 e2e：handle_data basket 上下文 + 非回归 ==========

def _e2e_strategy():
    """构造一个 handle_data 内 sell-then-buy 的策略（用 context 注入的初始持仓）。"""
    state = {"called": False}

    def initialize(ctx):
        pass

    def handle_data(ctx, data):
        from quantstudio.backtest.ptrade_api import order_target_value, order_value
        # 卖 600000（清仓，建底仓由测试直接注入 account.positions）
        order_target_value("600000.SH", 0)
        # 用卖出所得买 600001
        order_value("600001.SH", 9000)
        state["called"] = True

    return {"initialize": initialize, "handle_data": handle_data}, state


def test_e2e_handle_data_orders_form_one_basket(build_db):
    """§3.6 e2e：handle_data 内的卖+买订单并入同一个 basket，T+1 drain 用卖出所得支持买入。

    用真实 run() 循环 + 合成库验证端到端：Day1 handle_data 下卖买单 → Day2 T+1 drain。
    """
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig, Position
    from tests.conftest import daily_row, make_providers
    from pathlib import Path

    # 合成库：2 天，600000 + 600001（股票 → stock_daily）
    rows = []
    for day in ("2026-01-05", "2026-01-06"):
        rows.append(daily_row("600000", day, close=10.0, open_p=10.5, preclose=10.0))
        rows.append(daily_row("600001", day, close=10.0, open_p=10.5, preclose=10.0))
    db = build_db(stock_daily=rows)
    cal_days = ("2026-01-05", "2026-01-06")
    providers = make_providers(db, _make_fixed_cal(cal_days))

    engine = BacktestEngine(
        db_path=str(db), strategy=_e2e_strategy()[0],
        start="2026-01-05", end="2026-01-06",
        match_price_mode="next_open",
        engine_profile="daily-bar-v1",
        rebalance_mode="callback_basket",
        providers=providers,
    )
    # 注入 600000 初始持仓（T-1 已买入，Day1 可卖）
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)

    engine.run()

    # Day1 handle_data 的卖+买应形成 1 个 basket（Day2 也会 push basket 但因持仓已变通常空 → cancelled）
    real_baskets = [b for b in engine._baskets
                    if b.sell_orders or b.buy_orders]
    assert len(real_baskets) == 1
    basket = real_baskets[0]
    assert basket.created_dt == "2026-01-05"
    assert basket.status == "completed"
    assert len(basket.sell_orders) == 1
    assert len(basket.buy_orders) == 1
    # T+1 drain：卖成交（600000 清仓），买成交（600001 建仓，用卖出所得）
    assert basket.sell_orders[0].status == "filled"
    assert basket.buy_orders[0].status == "filled"
    pos600001 = engine.account.positions.get("600001.SH")
    assert pos600001 is not None and pos600001.volume > 0
    pos600000 = engine.account.positions.get("600000.SH")
    assert pos600000 is None or pos600000.volume == 0


def test_e2e_legacy_next_open_non_regression(build_db):
    """#22 legacy next_open 非 basket 不回归：rebalance_mode=legacy 行为与 0.2.0 一致"""
    from quantstudio.backtest.backtest_engine import BacktestEngine, Position
    from tests.conftest import daily_row, make_providers

    rows = []
    for day in ("2026-01-05", "2026-01-06"):
        rows.append(daily_row("600000", day, close=10.0, open_p=10.5, preclose=10.0))
    db = build_db(stock_daily=rows)
    providers = make_providers(db, _make_fixed_cal(("2026-01-05", "2026-01-06")))

    # legacy 模式：无 basket，订单走 _pending_orders（用 order(-shares) 走 sell_shares 路径）
    def _legacy_handle(ctx, data):
        from quantstudio.backtest.ptrade_api import order
        order("600000.SH", -1000)  # 卖 1000 股
    engine = BacktestEngine(
        db_path=str(db), strategy={"initialize": lambda c: None,
                                    "handle_data": _legacy_handle},
        start="2026-01-05", end="2026-01-06",
        match_price_mode="next_open",
        engine_profile="daily-bar-v1",
        rebalance_mode="legacy",
        providers=providers,
    )
    engine.account.positions["600000.SH"] = Position(
        code="600000.SH", volume=1000, avg_cost=10.0, can_sell=1000)
    engine.run()

    assert engine.engine_semantics_version == "0.2.0-next_open_pending"
    assert engine._baskets == []  # legacy 无 basket
    # legacy pending 订单 T+1 drain 成交
    pos = engine.account.positions.get("600000.SH")
    assert pos is None or pos.volume == 0


def _make_fixed_cal(days):
    """构造固定交易日的 calendar provider。"""
    import pandas as pd
    class Cal:
        def __init__(self, d):
            self._days = d
        def get_trade_days(self, start, end):
            return [pd.Timestamp(d, tz="Asia/Shanghai").to_pydatetime() for d in self._days]
        def get_trading_day(self, date, offset=0):
            idx = self._days.index(date) if date in self._days else 0
            idx = max(0, min(len(self._days) - 1, idx + offset))
            return pd.Timestamp(self._days[idx], tz="Asia/Shanghai").date()
    return Cal(list(days))
