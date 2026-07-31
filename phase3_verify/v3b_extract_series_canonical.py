# V3b — _extract_series(df, 'amount', 'money') 合规放行（预期不触发 PTRADE-LOCAL-COLUMN）
def _extract_series(item, *names):
    for n in names:
        try:
            v = item[n]
        except Exception:
            v = getattr(item, n, None)
        if v is not None:
            return v
    return None


def initialize(context):
    _ensure_runtime_state()
    g.universe = ["000300.SS", "000905.SZ"]


def handle_data(context):
    _ensure_runtime_state()
    hist = get_history(security_list=g.universe, count=60, frequency="1d", fq="pre", is_dict=False)
    df = hist["000300.SS"]
    amt = _extract_series(df, "amount", "money")
    order_target_value("000300.SS", 0)


def _ensure_runtime_state():
    if not hasattr(g, "state"):
        g.state = {}
