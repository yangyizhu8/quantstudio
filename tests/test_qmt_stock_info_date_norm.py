# -*- coding: utf-8 -*-
"""test_qmt_stock_info_date_norm —— D-14（2026-10-12）：QMT get_stock_info 日期契约键归一。

根因（2026-10-12 QMT_WEEKLY_SMALLCAP 复测实证，L0_all=5227 → L1_listed=0）：
  QMT wrapper `_qs_get_stock_info` 返回字典仅含 QMT 原生字段名
  （start_date / end_date / ...），**无本地契约键 `listed_date`**；
  而 `get_stock_info(stocks, field=['listed_date'])` 按 field 白名单取键
  （source_import_qmt.py:1531-1533）→ `d.get('listed_date')` 恒 None
  → 策略 L1 `if not listed: continue` 全部滤除 → 零交易。

契约对齐目标（ptrade_api.py:2481-2523 本地 get_stock_info）：
  listed_date = pd.Timestamp(start_date).strftime('%Y-%m-%d') → 'YYYY-MM-DD' 字符串。

判型：数据修正型（影响面 = 经 QMT wrapper 消费 listed_date 的全部策略 L1 层；
diff 只能源于归一化行集 —— source_import_qmt.py 纯新增、零删除）。

run: pytest tests/test_qmt_stock_info_date_norm.py -q
"""
import datetime
import pathlib
import re
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.strategy_compiler.source_import_qmt import (  # noqa: E402
    _QS_QMT_STOCK_INFO_EXT,
    _QS_QMT_WRAPPER_ZONE,
)


class _FakeCtx(object):
    """假 ContextInfo：仅需 get_instrument_detail（QMT 单位置参数签名）。"""

    def __init__(self, table):
        self._table = table

    def get_instrument_detail(self, code):
        return self._table.get(code)


def _ns():
    """exec QMT 模板并注入 _qs_pick_ctx（模板外模块级函数）的直通替身。

    需两个模板同命名空间：
      _QS_QMT_STOCK_INFO_EXT（:883-1090）→ _qs_norm_ymd / _qs_get_stock_info；
      _QS_QMT_WRAPPER_ZONE（:1422-1593）→ get_stock_info 同名遮蔽（:1567）。
    """
    ns = {"_qs_pick_ctx": lambda C=None: C}
    exec(_QS_QMT_STOCK_INFO_EXT, ns)
    exec(_QS_QMT_WRAPPER_ZONE, ns)
    return ns


# ---------------------------------------------------------------- 归一单元

@pytest.mark.parametrize("raw,expect", [
    (19910403, "1991-04-03"),      # QMT 实测形态（OpenDate int YYYYMMDD）
    (19910403.0, "1991-04-03"),    # float 形态
    ("19910403", "1991-04-03"),    # 数字串
    ("1991-04-03", "1991-04-03"),  # 已归一串
    ("1991/04/03", "1991-04-03"),  # 斜杠串
    (99999999, None),              # 永续标记（99 月 99 日非法 → 自然 ValueError）
    (0, None),                     # 未上市/缺失标记
    (-1, None),                    # 负数
    (None, None),                  # 空
    (True, None),                  # bool 须与 int 区分（True == 1）
    (False, None),
    (20260230, None),              # 非法日（2 月 30）
    (20261301, None),              # 非法月（13 月）
    (1991040, None),               # 7 位
    (199104031, None),             # 9 位
    ("", None),                    # 空串
    ("abc", None),                 # 非数字
    ("1991040", None),             # 7 位串
])
def test_qs_norm_ymd_matrix(raw, expect):
    assert _ns()["_qs_norm_ymd"](raw) == expect


def test_qs_norm_ymd_matches_local_contract_shape():
    """与本地契约逐位同形态：strftime('%Y-%m-%d') 且可被 pd.Timestamp 解析。"""
    val = _ns()["_qs_norm_ymd"](19910403)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", val)
    assert pd.Timestamp(val).date() == datetime.date(1991, 4, 3)


# ---------------------------------------------------------------- 结构契约

def test_stock_info_exposes_local_contract_keys():
    """listed_date / de_listed_date 必须存在（本轮修复本体）。"""
    ns = _ns()
    fake = _FakeCtx({"000001.SZ": {
        "InstrumentName": "平安银行",
        "OpenDate": 19910403,        # QMT 实测形态
        "ExpireDate": 99999999,      # 永续标记
        "InstrumentStatus": 0,
        "IsTrading": None,
        "UpStopPrice": 11.0,
        "DownStopPrice": 9.0,
        "FloatVolume": 1.0e9,
    }})
    out = ns["_qs_get_stock_info"]("000001.SZ", C=fake)
    assert out["listed_date"] == "1991-04-03"
    assert out["de_listed_date"] is None            # 99999999 → 永续 → None


def test_existing_keys_zero_regression():
    """既有键一律不得改动（原样透传 QMT 原生字段值）。"""
    ns = _ns()
    fake = _FakeCtx({"600000.SH": {
        "InstrumentName": "浦发银行",
        "OpenDate": 19991110,
        "ExpireDate": 0,
        "InstrumentStatus": 0,
        "IsTrading": None,
        "UpStopPrice": 12.5,
        "DownStopPrice": 10.5,
        "FloatVolume": 2.0e9,
    }})
    out = ns["_qs_get_stock_info"]("600000.SH", C=fake)
    assert out["start_date"] == 19991110        # 未归一，保持原值
    assert out["end_date"] == 0                 # 未归一，保持原值
    assert out["display_name"] == "浦发银行"
    assert out["name"] == "浦发银行"
    assert out["high_limit"] == 12.5
    assert out["low_limit"] == 10.5
    assert out["float_shares"] == 2.0e9
    assert out["instrument_status"] == 0
    assert out["is_trading"] is None
    assert out["listed_date"] == "1999-11-10"   # 新增键
    assert out["de_listed_date"] is None        # ExpireDate=0 → None


def test_missing_detail_degrades_to_none():
    """get_instrument_detail 取不到（空 dict / 抛异常）→ 全部 None，不抛。"""
    ns = _ns()
    out = ns["_qs_get_stock_info"]("999999.SZ", C=_FakeCtx({}))
    assert out["listed_date"] is None
    assert out["de_listed_date"] is None

    class _Boom(object):
        def get_instrument_detail(self, code):
            raise RuntimeError("boom")

    out2 = ns["_qs_get_stock_info"]("999999.SZ", C=_Boom())
    assert out2["listed_date"] is None


# ------------------------------------------------- L1 阻断回归闸（端到端口径）

def test_field_whitelist_yields_listed_date():
    """复现并锁死 L1 阻断：field=['listed_date'] 白名单取值必须非 None。

    修复前：返回 {'listed_date': None} → 策略 L1 全滤（L1_listed=0）。
    """
    ns = _ns()
    fake = _FakeCtx({
        "000001.SZ": {"OpenDate": 19910403},
        "600000.SH": {"OpenDate": 19991110},
        "301999.SZ": {"OpenDate": 0},          # 无上市日 → None（保守跳过）
        "688001.SH": {},                       # 详情缺失 → None
    })
    got = ns["get_stock_info"](
        ["000001.SZ", "600000.SH", "301999.SZ", "688001.SH"],
        field=["listed_date"], C=fake,
    )
    assert got["000001.SZ"]["listed_date"] == "1991-04-03"
    assert got["600000.SH"]["listed_date"] == "1999-11-10"
    assert got["301999.SZ"]["listed_date"] is None
    assert got["688001.SH"]["listed_date"] is None
    # 白名单只留请求键（本地契约同判）
    assert set(got["000001.SZ"].keys()) == {"listed_date"}


def test_field_whitelist_l1_funnel_simulation():
    """模拟策略 L1 漏斗：修复前 4 只 → 0 只；修复后 → 2 只（满 25 自然日）。"""
    ns = _ns()
    fake = _FakeCtx({
        "000001.SZ": {"OpenDate": 19910403},
        "600000.SH": {"OpenDate": 19991110},
        "301999.SZ": {"OpenDate": 0},
        "688001.SH": {},
    })
    codes = ["000001.SZ", "600000.SH", "301999.SZ", "688001.SH"]
    info = ns["get_stock_info"](codes, field=["listed_date"], C=fake)
    today = datetime.date(2026, 10, 12)
    _MIN_LISTED_DAYS = 25
    stage1 = []
    for code in codes:
        listed = (info.get(code) or {}).get("listed_date")
        if not listed:
            continue
        listed_date = pd.Timestamp(listed).date()
        if (today - listed_date).days < _MIN_LISTED_DAYS:
            continue
        stage1.append(code)
    assert stage1 == ["000001.SZ", "600000.SH"]
    assert len(stage1) > 0, "L1 阻断回归：L1_listed 必须 > 0"
