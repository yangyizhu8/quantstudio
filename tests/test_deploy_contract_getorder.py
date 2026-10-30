# -*- coding: utf-8 -*-
"""PD-0013：契约表补 `get_order` / `get_open_orders` 机检。

背景：`get_order(order_id)` 是 PTrade 真实 API（Context7 `/kay-ou/ptradeapi` 取证），
本地引擎亦已实现（`ptrade_api.py:2731`），但契约表原只有 `get_orders` ⇒ P5 报
「契约表外，需人工确认」（note）。**本项不阻断转换**，仅消除噪音。
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from quantstudio.ptrade_deploy.preflight import load_contract  # noqa: E402

REQUIRED = ("signature", "backtest", "trading", "action", "replacement")


@pytest.mark.parametrize("name", ["get_order", "get_open_orders"])
def test_contract_rows_exist_with_five_fields(name):
    api = load_contract()["api"]
    assert name in api, f"契约表缺 {name}"
    assert all(k in api[name] for k in REQUIRED), api[name].keys()


@pytest.mark.parametrize("name", ["get_order", "get_open_orders"])
def test_contract_rows_semantics_keep_both_sides(name):
    """双端可用 + `keep`（与同族 get_orders 一致，不门控/不替换/不剔除）。"""
    e = load_contract()["api"][name]
    assert e["backtest"] is True and e["trading"] is True
    assert e["action"] == "keep"
    assert e["replacement"] is None


def test_get_order_signature_matches_local_impl():
    """签名与本地实现一致（ptrade_api.py:2731 `def get_order(self, order_id)`）。"""
    assert load_contract()["api"]["get_order"]["signature"] == "get_order(order_id)"
    src = (REPO / "quantstudio/backtest/ptrade_api.py").read_text(encoding="utf-8")
    assert "def get_order(self, order_id)" in src


def test_product_using_get_order_has_no_unknown_note(tmp_path):
    """产物含 `get_order(...)` ⇒ P5 不再报「契约表外」（note 归零）。"""
    from quantstudio.ptrade_deploy import run_deploy
    src = (
        "def initialize(context):\n"
        "    g.done = False\n"
        "\n"
        "\n"
        "def handle_data(context, data):\n"
        "    if not g.done:\n"
        "        o = get_order('abc')\n"
        "        g.done = True\n"
    )
    prod = tmp_path / "prod_get_order_ptrade.py"
    prod.write_text(src, encoding="utf-8")
    cfg = tmp_path / "d.yaml"
    cfg.write_text(
        "strategy_id: t_get_order\n"
        f"source: {prod.as_posix()}\n"
        "execution:\n"
        "  profile: daily_eod_1450\n"
        "persistence:\n"
        "  enabled: true\n"
        f"  state_file: {(tmp_path / 'state.pkl').name}\n"
        f"output:\n  root: {(tmp_path / 'out').as_posix()}\n",
        encoding="utf-8")
    d = run_deploy(str(cfg)).to_dict()
    notes = " ".join(d.get("notes") or [])
    assert "get_order" not in notes or "契约表外" not in notes
