# V4 — 合规干净策略（预期 PASS：不调 LOCAL_ONLY API、不读 LOCAL_ONLY 列、is_dict=False）
def initialize(context):
    _ensure_runtime_state()
    g.universe = ["000300.SS", "000905.SZ"]


def handle_data(context):
    _ensure_runtime_state()
    hist = get_history(security_list=g.universe, count=60, frequency="1d", fq="pre", is_dict=False)
    df = hist["000300.SS"]
    close = df["close"]
    money = df["money"]
    order_target_value("000300.SS", 0)


def _ensure_runtime_state():
    if not hasattr(g, "state"):
        g.state = {}
