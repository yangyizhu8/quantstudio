# -*- coding: utf-8 -*-
"""test_qmt_status_flags —— 停牌判据（QD-2026-0021 修复）验收单测。

判型：数据修正型（停牌判据归位）。

背景（2026-11-05 真机定谳）：QMT ``get_instrument_detail`` 在**回测语境**下对全部
标的返回 ``IsTrading = False``（bool）。旧实现把该字段当停牌判据
（``if d.get('IsTrading') is False: halted = True``），致 5224 只全部被判停牌
⇒ ``filter_stock_by_status`` 返回空 ⇒ 选股层零候选 ⇒ 零订单，且因 ``ok=True``
不走「不可判定保守保留」分支而**零告警**。

修复口径（②审裁定 A 案）：``InstrumentStatus`` 为唯一权威停牌判据
（平台文档 ``07-行情函数.md:2502``：``<=0 正常交易（-1 复牌）；>=1 停牌天数``）；
``IsTrading``（:2503「是否可交易」）**彻底退出**停牌判定；配套条件 =
``InstrumentStatus`` 缺失/不可解析时走既有「不可判定保守保留」出口 ``({}, False)``。

run: pytest tests/test_qmt_status_flags.py -q
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"

# _qs_status_flags 定义于 _QS_QMT_STOCK_INFO_EXT；按 _assemble 序先 exec MODULE_HEADER
# （提供 _qs_instr_detail / 快照层等依赖），再 exec STOCK_INFO。
_EXEC_ORDER = ("_QS_QMT_MODULE_HEADER", "_QS_QMT_STOCK_INFO_EXT")


def _load_src():
    import importlib.machinery
    import importlib.util
    import sys as _sys

    name = "qmt_status_flags_src"
    loader = importlib.machinery.SourceFileLoader(name, str(SRC))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ns():
    mod = _load_src()
    out = {"_qs_pick_ctx": lambda C=None: C}
    for t in _EXEC_ORDER:
        exec(getattr(mod, t), out)
    assert "_qs_status_flags" in out, "模板渲染命名空间缺 _qs_status_flags"
    return out


class _Ctx(object):
    """最小 ContextInfo 替身：仅提供 get_instrument_detail。"""

    def __init__(self, table):
        self._t = table

    def get_instrument_detail(self, code):
        return self._t.get(code)


def _flags(ns, code, detail):
    return ns["_qs_status_flags"](code, _Ctx({code: detail}))


# ---------------------------------------------------------------------------
# 核心回归：真机形态（InstrumentStatus=0 + IsTrading=False）必须判「非停牌」
# ——本条即 QD-2026-0021 的防回潮守卫：若 IsTrading 重新参与判定，此例立刻变红。
# ---------------------------------------------------------------------------
def test_live_form_status0_trading_false_is_not_halted(ns):
    flags, ok = _flags(ns, "000001.SZ", {
        "InstrumentName": "平安银行", "InstrumentStatus": 0,
        "IsTrading": False, "ExpireDate": 99999999,
    })
    assert ok is True
    assert flags["HALT"] is False, (
        "IsTrading=False 被误当停牌判据 —— QD-2026-0021 回潮！"
        "QMT 回测语境该字段对全部标的恒 False"
    )
    assert flags["ST"] is False
    assert flags["DELISTING"] is False


# ---------------------------------------------------------------------------
# 权威判据正向：InstrumentStatus >= 1 => 停牌
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [1, 2, 10])
def test_authoritative_status_ge1_is_halted(ns, status):
    flags, ok = _flags(ns, "600000.SH", {
        "InstrumentName": "浦发银行", "InstrumentStatus": status,
        "IsTrading": True, "ExpireDate": 99999999,
    })
    assert ok is True and flags["HALT"] is True


@pytest.mark.parametrize("status", [0, -1])
def test_authoritative_status_le0_is_normal(ns, status):
    """:2502 语义：<=0 正常交易（-1 复牌）。"""
    flags, ok = _flags(ns, "600000.SH", {
        "InstrumentName": "浦发银行", "InstrumentStatus": status,
        "IsTrading": False, "ExpireDate": 99999999,
    })
    assert ok is True and flags["HALT"] is False


# ---------------------------------------------------------------------------
# ②审配套条件：InstrumentStatus 缺失 / 不可解析 => 不可判定出口 ({}, False)
# （调用方 _qs_filter_stock_by_status 据此走「保守保留 + QS_QMT_UNDETERMINED」，
#  不得凭缺失推断「正常」，也不得回落到 IsTrading）
# ---------------------------------------------------------------------------
def test_missing_status_goes_undetermined(ns):
    assert _flags(ns, "000002.SZ", {
        "InstrumentName": "正常", "IsTrading": False,
    }) == ({}, False)


@pytest.mark.parametrize("bad", ["x", None, [], {}])
def test_unparsable_status_goes_undetermined(ns, bad):
    assert _flags(ns, "000002.SZ", {
        "InstrumentName": "正常", "InstrumentStatus": bad, "IsTrading": False,
    }) == ({}, False)


# ---------------------------------------------------------------------------
# ST / DELISTING 判据未被本次修复波及
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["ST某某", "*ST某某", "st某某"])
def test_st_still_detected(ns, name):
    flags, ok = _flags(ns, "000003.SZ", {
        "InstrumentName": name, "InstrumentStatus": 0, "IsTrading": False,
        "ExpireDate": 99999999,
    })
    assert ok is True and flags["ST"] is True and flags["HALT"] is False


def test_perpetual_expire_not_delisted(ns):
    """D-13 口径：ExpireDate=99999999 为永续标记（99 月 99 日非法 => 不过期）。"""
    flags, ok = _flags(ns, "000004.SZ", {
        "InstrumentName": "正常", "InstrumentStatus": 0,
        "IsTrading": False, "ExpireDate": 99999999,
    })
    assert ok is True and flags["DELISTING"] is False


def test_expired_date_is_delisted(ns):
    flags, ok = _flags(ns, "000005.SZ", {
        "InstrumentName": "正常", "InstrumentStatus": 0,
        "IsTrading": False, "ExpireDate": 20200101,
    })
    assert ok is True and flags["DELISTING"] is True


# ---------------------------------------------------------------------------
# 源码级守卫：IsTrading 不得参与 halted 赋值（结构断言，不依赖运行期）
# ---------------------------------------------------------------------------
def test_source_has_no_is_trading_halt_branch():
    txt = io.open(str(SRC), encoding="utf-8").read() if False else SRC.read_text(encoding="utf-8")
    assert "if d.get('IsTrading') is False:" not in txt,         "源码仍残留 IsTrading 停牌分支（QD-2026-0021 回潮）"
    # IsTrading 仅允许出现在：字段映射、注释、docstring 披露处（本次基线 = 3 处）
    assert txt.count("IsTrading") == 3,         "IsTrading 出现次数偏离基线（期望 3：映射 1 / 注释 1 / docstring 1），实际 %d" % txt.count("IsTrading")
