def initialize(context):
    if not is_trade():
        set_backtest()
