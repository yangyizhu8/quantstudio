# V2 — LOCAL_ONLY API 阻断用例（预期 PTRADE-API-UNSUPPORTED + PTRADE-LOCAL-SYMBOL）
def initialize(context):
    _ensure_runtime_state()
    g.universe = ["000300.SS", "000905.SZ"]


def handle_data(context):
    _ensure_runtime_state()
    ind = get_industry("000300.SS")
    hist = get_history(security_list=g.universe, count=60, frequency="1d", fq="pre", is_dict=False)
    df = hist["000300.SS"]
    close = df["close"]
    order_target_value("000300.SS", 0)


def _ensure_runtime_state():
    if not hasattr(g, "state"):
        g.state = {}
