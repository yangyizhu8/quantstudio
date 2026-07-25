"""
smallcap_overnight_scalp_7.py ? portable rebuild

Agent-authored canonical QuantStudio/PTrade strategy.

Confirmed semantics:
- Build a T-1 PIT hard-filtered A-share universe, then take the smallest 500
  securities by circulating market capitalization.
- Require four-day amplitude <= 10%, T-1 close > EMA5, and the 09:31 first
  minute-bar open < T-1 low.
- Buy up to seven names in a 49% daily batch (7% target increment per name).
- First attempt to sell the prior batch at T+1 10:30; retry failed exits every
  later minute and carry genuine unfilled positions across days.

This canonical source targets the documented PTrade backtest public subset and is
published unchanged to QuantStudio and PTrade. Local data is injected through
project providers; strategy code never opens DuckDB, providers, or local files.
"""

from datetime import date, datetime

import numpy as np


STRATEGY_ID = "smallcap_overnight_scalp_7"


def _ensure_runtime_state():
    """Idempotently create all runtime parameters and persistent fields."""
    if not hasattr(g, "universe_size"):
        g.universe_size = 500
    if not hasattr(g, "buy_count"):
        g.buy_count = 7
    if not hasattr(g, "daily_batch_weight"):
        g.daily_batch_weight = 0.49
    if not hasattr(g, "per_stock_weight"):
        g.per_stock_weight = 0.07
    if not hasattr(g, "cash_reserve_weight"):
        g.cash_reserve_weight = 0.02
    if not hasattr(g, "amplitude_lookback"):
        g.amplitude_lookback = 4
    if not hasattr(g, "amplitude_threshold"):
        g.amplitude_threshold = 0.10
    if not hasattr(g, "ema_period"):
        g.ema_period = 5
    if not hasattr(g, "ema_warmup_bars"):
        g.ema_warmup_bars = 60
    if not hasattr(g, "recent_suspension_days"):
        g.recent_suspension_days = 5
    if not hasattr(g, "minimum_listing_days"):
        g.minimum_listing_days = 365
    if not hasattr(g, "preopen_candidates"):
        g.preopen_candidates = []
    if not hasattr(g, "batches"):
        g.batches = {}
    if not hasattr(g, "pending_exits"):
        g.pending_exits = set()
    if not hasattr(g, "buy_executed_dates"):
        g.buy_executed_dates = set()
    if not hasattr(g, "last_exit_attempt_minute"):
        g.last_exit_attempt_minute = {}
    if not hasattr(g, "daily_diagnostics"):
        g.daily_diagnostics = {}


def initialize(context):
    """Configure costs and callbacks without assuming later callbacks wait on success."""
    _ensure_runtime_state()
    set_benchmark("000300.SS")
    set_commission(commission_ratio=0.00035, min_commission=5.0, type="STOCK")
    set_slippage(slippage=0.0)
    run_daily(context, buy_today_batch, time="09:31")
    run_daily(context, sell_due_batches, time="10:30")


def before_trading_start(context, data):
    """Build the T-1 PIT pool and precompute completed-daily-bar conditions."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    previous_date = _date_text(context.previous_date)
    previous_api_date = _api_date(context.previous_date)
    _reconcile_batch_state(context, previous_date)

    diagnostics = {
        "all_ashares": 0,
        "main_board": 0,
        "status_ok": 0,
        "listing_ok": 0,
        "recent_halt_ok": 0,
        "valuation_ok": 0,
        "smallest_500": 0,
        "daily_conditions_ok": 0,
    }

    try:
        stocks = list(get_Ashares(previous_api_date) or [])
    except Exception as exc:
        log.info("get_Ashares failed for %s: %s" % (previous_date, exc))
        stocks = []
    diagnostics["all_ashares"] = len(stocks)

    stocks = [_portable_code(code) for code in stocks if _is_main_board(code)]
    stocks = _unique_codes(stocks)
    diagnostics["main_board"] = len(stocks)

    try:
        stocks = filter_stock_by_status(
            stocks,
            filter_type=["ST", "HALT", "DELISTING", "DELISTING_SORTING"],
            query_date=previous_api_date,
        )
    except Exception as exc:
        log.info("T-1 status filter failed; fail-closed for the day: %s" % exc)
        stocks = []
    diagnostics["status_ok"] = len(stocks)

    stocks = _filter_by_listing_age(
        stocks, context.current_dt, g.minimum_listing_days)
    diagnostics["listing_ok"] = len(stocks)

    stocks = _exclude_recent_suspensions(stocks, context.previous_date)
    diagnostics["recent_halt_ok"] = len(stocks)

    cap_rows = _load_pit_float_values(stocks, previous_api_date)
    diagnostics["valuation_ok"] = len(cap_rows)
    cap_rows.sort(key=lambda item: (item["float_value"], _bare_code(item["code"])))
    cap_rows = cap_rows[:g.universe_size]
    diagnostics["smallest_500"] = len(cap_rows)

    candidates = []
    for item in cap_rows:
        feature = _completed_daily_feature(item["code"])
        if feature is None:
            continue
        feature["float_value"] = item["float_value"]
        candidates.append(feature)

    candidates.sort(key=lambda item: (item["float_value"], _bare_code(item["code"])))
    diagnostics["daily_conditions_ok"] = len(candidates)
    g.preopen_candidates = candidates
    g.daily_diagnostics[today] = diagnostics

    log.info(
        "Pre-open %s: all=%s main=%s status=%s listing=%s halt5=%s valuation=%s "
        "small500=%s daily_conditions=%s"
        % (
            today,
            diagnostics["all_ashares"], diagnostics["main_board"],
            diagnostics["status_ok"], diagnostics["listing_ok"],
            diagnostics["recent_halt_ok"], diagnostics["valuation_ok"],
            diagnostics["smallest_500"], diagnostics["daily_conditions_ok"],
        )
    )


def buy_today_batch(context):
    """At 09:31 evaluate the completed first-minute bar and buy today's batch."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    if today in g.buy_executed_dates:
        return
    g.buy_executed_dates.add(today)

    overdue = set(_due_symbols(today)) | set(g.pending_exits)
    selected = []
    for item in g.preopen_candidates:
        code = item["code"]
        bare = _bare_code(code)
        if bare in overdue:
            continue
        if not _currently_tradeable_for_buy(code, today):
            continue
        minute = _current_minute_bar(code)
        open_price = _finite_float(minute.get("open"))
        current_price = _finite_float(minute.get("close"))
        volume = _finite_float(minute.get("volume"))
        if open_price is None or current_price is None or volume is None:
            continue
        if open_price <= 0 or current_price <= 0 or volume <= 0:
            continue
        if not (open_price < item["previous_low"]):
            continue
        candidate = dict(item)
        candidate["entry_open"] = open_price
        candidate["entry_close"] = current_price
        selected.append(candidate)
        if len(selected) >= g.buy_count:
            break

    if not selected:
        g.batches.setdefault(today, [])
        log.info("09:31 %s: no qualifying low-open stocks" % today)
        return

    total_value = _portfolio_total_value(context)
    cash = max(0.0, _finite_float(getattr(context.portfolio, "cash", 0.0)) or 0.0)
    reserve = total_value * g.cash_reserve_weight
    cash_budget = max(0.0, cash - reserve)
    batch_budget = min(total_value * g.daily_batch_weight, cash_budget)
    per_name_budget = min(total_value * g.per_stock_weight, batch_budget / float(len(selected)))
    if per_name_budget <= 0:
        log.info("09:31 %s: no available cash after reserve; batch skipped" % today)
        return

    submitted = []
    remaining_cash = cash_budget
    for item in selected:
        code = item["code"]
        allocation = min(per_name_budget, remaining_cash)
        if allocation <= 0:
            break
        before_position = get_position(code)
        before_amount = _position_amount(before_position)
        current_value = _position_market_value(before_position)
        target_value = current_value + allocation
        try:
            order_id = order_target_value(code, target_value)
        except Exception as exc:
            log.info("Buy submit failed %s: %s" % (code, exc))
            continue
        if _order_submission_failed(order_id):
            log.info("Buy rejected %s at 09:31" % code)
            continue
        after_amount = _position_amount(get_position(code))
        open_pending = _has_open_order(code)
        if after_amount <= before_amount and not open_pending:
            log.info("Buy produced no fill/open order %s at 09:31" % code)
            continue
        submitted.append(_bare_code(code))
        remaining_cash -= allocation
        log.info(
            "Buy batch=%s code=%s increment=%.2f target=%.2f open=%.4f prev_low=%.4f"
            % (today, code, allocation, target_value,
               item["entry_open"], item["previous_low"])
        )

    g.batches[today] = sorted(set(submitted))
    log.info(
        "09:31 %s: selected=%s submitted=%s batch_budget=%.2f per_name=%.2f cash=%.2f"
        % (today, len(selected), len(submitted), batch_budget, per_name_budget, cash)
    )


def sell_due_batches(context):
    """At 10:30 make the first mandatory exit attempt for every due batch."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    due = _due_symbols(today)
    for bare in due:
        g.pending_exits.add(bare)
    for bare in sorted(due):
        _attempt_exit(context, bare, today, "10:30-first-attempt")


def handle_data(context, data):
    """After 10:30 retry genuine failed exits once per completed minute bar."""
    _ensure_runtime_state()
    if _time_text(context.current_dt) < "10:30":
        return
    today = _date_text(context.current_dt)
    for bare in sorted(list(g.pending_exits)):
        _attempt_exit(context, bare, today, "minute-retry")


def after_trading_end(context, data):
    """Reconcile actual positions and carry unresolved exits honestly."""
    _ensure_runtime_state()
    today = _date_text(context.current_dt)
    _reconcile_batch_state(context, today)
    held = sorted(_held_bare_codes(context))
    pending = sorted(set(g.pending_exits) & set(held))
    g.pending_exits = set(pending)
    log.info(
        "Post-close %s: held=%s batches=%s pending_exits=%s"
        % (today, held, g.batches, pending)
    )


def _exclude_recent_suspensions(stocks, previous_date):
    if not stocks:
        return []
    try:
        days = list(get_trade_days(
            end_date=_api_date(previous_date), count=g.recent_suspension_days))
    except Exception as exc:
        log.info("get_trade_days for suspension filter failed: %s" % exc)
        return []
    days = [_date_text(day) for day in days]
    if len(days) < g.recent_suspension_days:
        log.info("Suspension filter lacks five completed trade days: %s" % days)
        return []

    excluded = set()
    for day in days:
        try:
            status = get_stock_status(
                stocks, query_type="HALT", query_date=_api_date(day))
        except Exception as exc:
            log.info("Historical HALT query failed for %s: %s" % (day, exc))
            return []
        for code in stocks:
            if bool(_mapping_value(status, code, False)):
                excluded.add(_bare_code(code))
    return [code for code in stocks if _bare_code(code) not in excluded]


def _load_pit_float_values(stocks, previous_date):
    if not stocks:
        return []
    try:
        frame = get_fundamentals(
            stocks,
            "valuation",
            fields=["float_value", "circulating_market_cap"],
            date=previous_date,
        )
    except Exception as exc:
        log.info("PIT valuation query failed: %s" % exc)
        return []
    if frame is None or len(frame) == 0:
        return []

    rows = []
    try:
        iterator = frame.iterrows()
    except Exception:
        return []
    for index, row in iterator:
        code = row.get("code", index) if hasattr(row, "get") else index
        portable = _portable_code(code)
        if not portable:
            continue
        value = None
        if hasattr(row, "get"):
            value = _finite_float(row.get("float_value"))
            if value is None:
                value = _finite_float(row.get("circulating_market_cap"))
        if value is None or value <= 0:
            continue
        rows.append({"code": portable, "float_value": value})
    return rows


def _completed_daily_feature(code):
    try:
        history = get_history(
            g.ema_warmup_bars,
            frequency="1d",
            field=["close", "high", "low", "volume"],
            security_list=code,
            fq="pre",
            include=False,
            fill="nan",
            is_dict=True,
        )
    except Exception as exc:
        log.info("Daily history failed %s: %s" % (code, exc))
        return None

    closes = _history_field(history, code, "close")
    highs = _history_field(history, code, "high")
    lows = _history_field(history, code, "low")
    volumes = _history_field(history, code, "volume")
    required = max(g.ema_warmup_bars, g.amplitude_lookback)
    if min(len(closes), len(highs), len(lows), len(volumes)) < required:
        return None
    if not np.all(np.isfinite(closes[-g.ema_warmup_bars:])):
        return None
    if not np.all(np.isfinite(highs[-g.amplitude_lookback:])):
        return None
    if not np.all(np.isfinite(lows[-g.amplitude_lookback:])):
        return None
    if not np.all(np.isfinite(volumes[-g.recent_suspension_days:])):
        return None
    if np.any(volumes[-g.recent_suspension_days:] <= 0):
        return None

    recent_high = float(np.max(highs[-g.amplitude_lookback:]))
    recent_low = float(np.min(lows[-g.amplitude_lookback:]))
    if recent_low <= 0:
        return None
    amplitude = (recent_high - recent_low) / recent_low
    if not np.isfinite(amplitude) or amplitude > g.amplitude_threshold:
        return None

    ema_value = _ema_last(closes[-g.ema_warmup_bars:], g.ema_period)
    if ema_value is None:
        return None
    previous_close = float(closes[-1])
    if not (previous_close > ema_value):
        return None

    previous_low = float(lows[-1])
    if not np.isfinite(previous_low) or previous_low <= 0:
        return None
    return {
        "code": _portable_code(code),
        "previous_low": previous_low,
        "previous_close": previous_close,
        "ema5": ema_value,
        "amplitude4": float(amplitude),
    }


def _currently_tradeable_for_buy(code, today):
    for query_type in ("ST", "HALT", "DELISTING"):
        try:
            status = get_stock_status(
                [code], query_type=query_type, query_date=_api_date(today))
        except Exception:
            return False
        if bool(_mapping_value(status, code, False)):
            return False
    return True


def _attempt_exit(context, bare, today, reason):
    minute_key = "%s %s" % (today, _time_text(context.current_dt))
    if g.last_exit_attempt_minute.get(bare) == minute_key:
        return
    g.last_exit_attempt_minute[bare] = minute_key

    code = _portable_code(bare)
    position = get_position(code)
    amount = _position_amount(position)
    enabled = int(_finite_float(getattr(position, "enable_amount", 0)) or 0)
    if amount <= 0:
        _resolve_due_code(bare, today)
        return
    if enabled <= 0:
        g.pending_exits.add(bare)
        return
    if _has_open_order(code):
        g.pending_exits.add(bare)
        return

    for query_type in ("HALT", "DELISTING"):
        try:
            status = get_stock_status(
                [code], query_type=query_type, query_date=_api_date(today))
        except Exception:
            g.pending_exits.add(bare)
            return
        if bool(_mapping_value(status, code, False)):
            g.pending_exits.add(bare)
            return

    before_amount = amount
    try:
        order_id = order(code, -enabled)
    except Exception as exc:
        log.info("Exit submit failed %s reason=%s error=%s" % (code, reason, exc))
        g.pending_exits.add(bare)
        return
    if _order_submission_failed(order_id):
        log.info("Exit rejected %s qty=%s reason=%s" % (code, enabled, reason))
        g.pending_exits.add(bare)
        return

    refreshed = get_position(code)
    remaining_amount = _position_amount(refreshed)
    remaining_enabled = int(_finite_float(getattr(refreshed, "enable_amount", 0)) or 0)
    open_pending = _has_open_order(code)
    if remaining_amount <= max(0, before_amount - enabled) and remaining_enabled <= 0             and not open_pending:
        _resolve_due_code(bare, today)
    else:
        g.pending_exits.add(bare)
    log.info("Exit submit code=%s qty=%s reason=%s" % (code, enabled, reason))


def _due_symbols(today):
    due = set(g.pending_exits)
    for batch_date, symbols in list(g.batches.items()):
        if str(batch_date) < today:
            due.update(_bare_code(code) for code in symbols)
    return sorted(due)


def _resolve_due_code(bare, today):
    bare = _bare_code(bare)
    for batch_date in list(g.batches.keys()):
        if str(batch_date) >= today:
            continue
        remaining = [code for code in g.batches.get(batch_date, []) if _bare_code(code) != bare]
        if remaining:
            g.batches[batch_date] = remaining
        else:
            del g.batches[batch_date]
    g.pending_exits.discard(bare)


def _reconcile_batch_state(context, fallback_entry_date):
    held = _held_bare_codes(context)
    for batch_date in list(g.batches.keys()):
        remaining = [code for code in g.batches.get(batch_date, []) if _bare_code(code) in held]
        if remaining:
            g.batches[batch_date] = sorted(set(_bare_code(code) for code in remaining))
        else:
            del g.batches[batch_date]
    tracked = set()
    for symbols in g.batches.values():
        tracked.update(_bare_code(code) for code in symbols)
    untracked = sorted(held - tracked)
    if untracked:
        existing = set(g.batches.get(fallback_entry_date, []))
        existing.update(untracked)
        g.batches[fallback_entry_date] = sorted(existing)
        g.pending_exits.update(untracked)
    g.pending_exits.intersection_update(held)


def _held_bare_codes(context):
    held = set()
    positions = getattr(context.portfolio, "positions", {}) or {}
    try:
        items = positions.items()
    except AttributeError:
        return held
    for code, position in items:
        amount = int(_finite_float(getattr(position, "amount", 0)) or 0)
        if amount > 0:
            held.add(_bare_code(code))
    return held


def _has_open_order(code):
    try:
        orders = get_open_orders(security=code)
    except Exception:
        return False
    if orders is None:
        return False
    if isinstance(orders, dict):
        values = list(orders.values())
    else:
        try:
            values = list(orders)
        except TypeError:
            return bool(orders)
    target = _bare_code(code)
    for item in values:
        security = getattr(item, "security", getattr(item, "sid", ""))
        if security and _bare_code(security) != target:
            continue
        status = str(getattr(item, "status", "pending")).lower()
        if status in ("pending", "open", "new", "submitted", "part_filled", "partial"):
            return True
    return False


def _filter_by_listing_age(stocks, current_dt, minimum_days):
    if not stocks:
        return []
    try:
        info_map = get_stock_info(stocks, field=["listed_date"])
    except Exception as exc:
        log.info("Batch listing-date query failed: %s" % exc)
        return []
    current_day = _date_value(current_dt)
    if current_day is None:
        return []
    result = []
    for code in stocks:
        record = _mapping_value(info_map, code, {})
        listed_date = record.get("listed_date") if hasattr(record, "get") else None
        listing_day = _date_value(listed_date)
        if listing_day is None:
            continue
        if (current_day - listing_day).days >= int(minimum_days):
            result.append(code)
    return result


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value).strip()
    for pattern, width in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:width], pattern).date()
        except Exception:
            pass
    return None


def _history_field(history, code, field):
    item = _history_item(history, code)
    if item is None:
        return np.asarray([], dtype=float)
    values = None
    try:
        values = item[field]
    except Exception:
        if field == "close" and not hasattr(item, "columns"):
            values = item
    if values is None:
        return np.asarray([], dtype=float)
    if hasattr(values, "values"):
        values = values.values
    try:
        flat = np.asarray(values, dtype=object).reshape(-1)
    except Exception:
        return np.asarray([], dtype=float)
    result = []
    for value in flat:
        number = _finite_float(value)
        result.append(number if number is not None else np.nan)
    return np.asarray(result, dtype=float)


def _history_item(history, code):
    if history is None:
        return None
    if not isinstance(history, dict):
        return history
    try:
        return history[code]
    except Exception:
        pass
    target = _bare_code(code)
    for key, value in history.items():
        if _bare_code(key) == target:
            return value
    return None


def _current_minute_bar(code):
    try:
        history = get_history(
            1,
            frequency="1m",
            field=["open", "close", "volume"],
            security_list=code,
            fq="pre",
            include=True,
            fill="nan",
            is_dict=True,
        )
    except Exception as exc:
        log.info("Current minute history failed %s: %s" % (code, exc))
        return {}
    result = {}
    for field in ("open", "close", "volume"):
        values = _history_field(history, code, field)
        result[field] = values[-1] if len(values) else None
    return result


def _mapping_value(mapping, code, default=None):
    if mapping is None:
        return default
    try:
        return mapping[code]
    except Exception:
        pass
    target = _bare_code(code)
    try:
        items = mapping.items()
    except AttributeError:
        return default
    for key, value in items:
        if _bare_code(key) == target:
            return value
    return default


def _order_submission_failed(order_id):
    if order_id is None:
        return True
    if isinstance(order_id, (str, int)):
        try:
            detail = get_order(order_id)
        except Exception:
            detail = None
        if detail is not None:
            status = str(getattr(detail, "status", "")).lower()
            if status in ("rejected", "cancelled", "canceled", "failed", "expired"):
                return True
    return False


def _ema_last(values, period):
    try:
        array = np.asarray(values, dtype=float).reshape(-1)
    except Exception:
        return None
    if len(array) == 0 or not np.all(np.isfinite(array)) or int(period) <= 0:
        return None
    alpha = 2.0 / (float(period) + 1.0)
    ema = float(array[0])
    for value in array[1:]:
        ema = alpha * float(value) + (1.0 - alpha) * ema
    return ema if np.isfinite(ema) else None


def _position_amount(position):
    return int(_finite_float(getattr(position, "amount", 0)) or 0)


def _api_date(value):
    day = _date_value(value)
    return day.strftime("%Y%m%d") if day is not None else str(value)


def _position_market_value(position):
    direct = _finite_float(getattr(position, "market_value", None))
    if direct is not None:
        return max(0.0, direct)
    amount = _finite_float(getattr(position, "amount", 0)) or 0.0
    price = _finite_float(getattr(position, "last_sale_price", 0)) or 0.0
    return max(0.0, amount * price)


def _portfolio_total_value(context):
    value = _finite_float(getattr(context.portfolio, "total_value", None))
    if value is not None and value > 0:
        return value
    cash = _finite_float(getattr(context.portfolio, "cash", 0)) or 0.0
    positions = getattr(context.portfolio, "positions", {}) or {}
    market_value = 0.0
    try:
        values = positions.values()
    except AttributeError:
        values = []
    for position in values:
        market_value += _position_market_value(position)
    return max(0.0, cash + market_value)


def _is_main_board(code):
    bare = _bare_code(code)
    return bare.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def _portable_code(code):
    bare = _bare_code(code)
    if len(bare) != 6 or not bare.isdigit():
        return ""
    if bare.startswith(("5", "6", "9")):
        return bare + ".SS"
    if bare.startswith(("0", "1", "2", "3")):
        return bare + ".SZ"
    if bare.startswith(("4", "8")):
        return bare + ".BJ"
    return ""


def _bare_code(code):
    return str(code).strip().upper().split(".")[0]


def _unique_codes(codes):
    result = []
    seen = set()
    for code in codes:
        bare = _bare_code(code)
        if bare and bare not in seen:
            seen.add(bare)
            result.append(_portable_code(bare))
    return result


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _date_text(value):
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    return str(value)[:10]


def _time_text(value):
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%H:%M")
        except Exception:
            pass
    text = str(value)
    return text[11:16] if len(text) >= 16 else "00:00"
