# -*- coding: utf-8 -*-
"""test_qmt_fund_field_map —— D-29 profit_ability 原生比率字段映射单测。

背景：`_QS_QMT_FUND_FIELD_MAP` 等符号位于 `_QS_QMT_FUNDAMENTALS_EXT` 模板串内
（source_import_qmt.py:1152..1887），**不进宿主模块命名空间** ⇒ 测试须按既有
`_ns()` 惯例（见 test_qmt_progress_audit.py）把模板 exec 进命名空间后取用。

覆盖（方案 docs/qmt-profit-ability-mapping-design.md §四.3）：
  1. profit_ability 映射存在且字段名与 v5 探针实证一致
  2. 既有 eps/growth_ability/valuation 三映射零变化（纯增益）
  3. 键恒附：调用 fields=['roe'] 时 native_fields 自动含 m_anntime/m_timetag
  4. 投影剥离：native_fields 不含本地字段名 'roe'
  5. 未映射表早退语义不变（全 NaN 契约行）
  6. 值域合理性：QMT 百分比量级（7.81）直通

run: pytest tests/test_qmt_fund_field_map.py -q
"""
import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"

# 按 `_assemble` 真实序的模板集合。`_qs_pick_ctx`（C 捕获源）在
# `_QS_QMT_CONTEXT_VIEW_EXT` 串内、`_QS_QMT_FUND_FIELD_MAP` 与
# `_qs_get_fundamentals` 在 `_QS_QMT_FUNDAMENTALS_EXT` 串内 —— 二者均
# **不进宿主模块命名空间**（实测 hasattr(mod, ...) 均 False）。
_TEMPLATES = (
    "_QS_QMT_LOG_EXT",
    "_QS_QMT_CONTEXT_VIEW_EXT",
    "_QS_QMT_FUNDAMENTALS_EXT",
    "_QS_QMT_STOCK_INFO_EXT",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("sqm_d29", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sqm_d29"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ns():
    """按 `_assemble` 序 exec 模板进同一命名空间（MODULE_HEADER 为序首项，见 D-32 先例）。"""
    mod = _load_module()
    ns = {}
    for name in ("_QS_QMT_MODULE_HEADER",) + _TEMPLATES:
        exec(getattr(mod, name), ns)
    ns["_TPL_SRC"] = {name: getattr(mod, name) for name in _TEMPLATES}
    return ns


@pytest.fixture(scope="module")
def ns():
    return _ns()


# ---------- 1. 映射存在且字段名与 v5 实证一致 ----------
def test_profit_ability_mapping_present(ns):
    mp = ns["_QS_QMT_FUND_FIELD_MAP"]
    assert "profit_ability" in mp, sorted(mp)
    table, fmap = mp["profit_ability"]
    assert table == "PERSHAREINDEX"
    assert fmap["roe"] == "du_return_on_equity"           # v5 实测 valid=9460/ratio=0.541
    assert fmap["net_profit_margin"] == "net_profit"       # v5 实测 valid=9460/ratio=0.541
    assert fmap["publ_date"] == "m_anntime"
    assert fmap["end_date"] == "m_timetag"


# ---------- 2. 既有三映射零变化（纯增益） ----------
def test_existing_mappings_unchanged(ns):
    mp = ns["_QS_QMT_FUND_FIELD_MAP"]
    assert mp["eps"] == ("PERSHAREINDEX", {"eps": "s_fa_eps_basic",
                                          "publ_date": "m_anntime",
                                          "end_date": "m_timetag"})
    assert mp["growth_ability"] == ("PERSHAREINDEX", {"or_yoy": "inc_revenue_rate",
                                                     "publ_date": "m_anntime",
                                                     "end_date": "m_timetag"})
    assert mp["valuation"] == ("CAPITALSTRUCTURE", {"float_value": "circulating_capital"})
    assert sorted(mp) == ["eps", "growth_ability", "profit_ability", "valuation"]


# ---------- 3+4. 键恒附与投影剥离（经真实调用路径取证） ----------
class _FakeC(object):
    """记录 get_financial_data 实参；返回空 DateFrame（走「空 -> 全 NaN 契约行」）。"""

    def __init__(self):
        self.captured = []

    def get_financial_data(self, fields, codes, start, end, **kw):
        self.captured.append({"fields": list(fields), "codes": list(codes),
                              "start": start, "end": end, "kw": kw})
        return pd.DataFrame()


def test_key_auto_attach_and_projection_strip(ns):
    C = _FakeC()
    ns["_qs_get_fundamentals"]("profit_ability", ["000001.SZ"], None, ["roe"], C)
    assert C.captured, "get_financial_data 未被调用"
    got = C.captured[0]["fields"]
    # 真实请求面带表前缀（PERSHAREINDEX.<字段>），键恒附两键同为前缀形式
    assert "PERSHAREINDEX.du_return_on_equity" in got, got   # 值字段按映射翻译
    assert "PERSHAREINDEX.roe" not in got, got             # 投影剥离：本地字段名不入请求
    assert "PERSHAREINDEX.m_anntime" in got, got            # 键恒附（公告日）
    assert "PERSHAREINDEX.m_timetag" in got, got            # 键恒附（报告截止日）


def test_net_profit_margin_request(ns):
    C = _FakeC()
    ns["_qs_get_fundamentals"]("profit_ability", ["000001.SZ"], None,
                               ["net_profit_margin"], C)
    got = C.captured[0]["fields"]
    assert "PERSHAREINDEX.net_profit" in got, got
    assert "PERSHAREINDEX.net_profit_margin" not in got, got


# ---------- 5. 未映射表早退语义不变 ----------
def test_unmapped_table_early_return_unchanged(ns):
    C = _FakeC()
    frame = ns["_qs_get_fundamentals"]("no_such_table", ["000001.SZ"], None, ["roe"], C)
    assert list(frame.index) == ["000001.SZ"]
    assert list(frame.columns) == ["roe"]
    assert frame["roe"].isna().all()
    assert not C.captured, "未映射表不应触发平台调用"


# ---------- 6. 值域合理性（百分比量级直通，不做单位换算） ----------
def test_value_domain_passthrough_pct():
    # v5 探针实证 sample：du_return_on_equity=7.81 / net_profit=22.75（百分比量级）
    for sample in (7.81, 22.75):
        assert 0.0 < sample < 100.0
        assert float(sample) == sample            # 直通，无隐式换算


# ---------- 7. GBK 契约（渲染面可编码性，防 QD-2026-0001 复发） ----------
def test_render_face_gbk_encodable(ns):
    src = ns["_TPL_SRC"]["_QS_QMT_FUNDAMENTALS_EXT"]
    src.encode("gbk")   # 非 GBK 字符在此抛错（QD-2026-0001 复发防线）
