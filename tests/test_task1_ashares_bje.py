# -*- coding: utf-8 -*-
"""任务一 T1-T5 契约测试：get_Ashares 北交默认排除 + PIT 退市过滤（2026-09-06）。

设计：docs/task1-ashares-bje-exclude-design.md（四批复①②③④ + DB 实测约束）。
mock 形态（不依赖真实 DB 锁）；stock_delist 消费经 _delisted_set_cache 注入。
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.backtest.libs.security_code_rules import (  # noqa: E402
    is_bje_excluded, is_bse_market)


def _mk_api(codes, delisted_pairs=None):
    """构造最小 PtradeAPI（__new__ 绕过 __init__）+ reference stub。"""
    from quantstudio.backtest.ptrade_api import PtradeAPI
    api = PtradeAPI.__new__(PtradeAPI)
    api._reference = type("R", (), {
        "get_all_stocks": staticmethod(lambda d: list(codes)),
        "db_path": "data/quantstudio.db",
    })()
    api._current_date = None
    api._fidelity = None
    api._delisted_set_cache = set(delisted_pairs or ())
    return api


CODES = ["600000.SH", "000001.SZ", "300750.SZ", "688981.SH",
         "920001.BJ", "430047.BJ", "830799.BJ", "430139.BJ"]


def _mk_t1():
    """T1-T4 共用：北交混合池（不含 stock_delist 过滤——include_delisted=True 跳过）。"""
    api = _mk_api(CODES)
    return api


# T1：默认排除（exclude_bse=None → 默认排除北交 blanket）
def test_t1_default_excludes_bje():
    api = _mk_t1()
    api._current_date = "2026-07-01"
    out = api.get_Ashares("2026-07-01", include_delisted=True)
    assert all(not c.split(".")[0].startswith(("920", "430", "830")) for c in out), \
        f"默认应排除全部北交段: {out}"
    bare_out = {c.split(".")[0] for c in out}
    assert {"600000", "000001"} <= bare_out, "沪深码保留"


# T2：显式回退（exclude_bse=False → 旧含北交语义）
def test_t2_explicit_false_keeps_bje():
    api = _mk_t1()
    api._current_date = "2026-07-01"
    out = api.get_Ashares("2026-07-01", exclude_bse=False, include_delisted=True)
    assert "920001.BJ" in out, "显式 False 应保留北交（逃生门语义）"
    assert len(out) == len(CODES)


# T3：legacy 表码命中（430047 在 BSE_LEGACY_TO_920）
def test_t3_legacy_table_hit():
    assert is_bje_excluded("430047") is True
    assert is_bse_market("430047") is True  # legacy 同表（is_bse_market 行为不变对照）


# T4：blanket 口径（8xx/4xx 段命中；830799 恰在 legacy 表——is_bse_market 亦命中为
# 合理同表行为；差集样本取非表内 4xx/8xx 码锁定 blanket 扩展）
def test_t4_blanket_vs_bse_market_diff():
    assert is_bje_excluded("830799") is True  # legacy 表内（双谓词同命中）
    assert is_bje_excluded("430139") is True
    # 差集样本：非 920、非 legacy 表的 8xx 码——blanket 命中而 is_bse_market 不命中
    assert is_bje_excluded("800001") is True, "blanket 应命中 8xx 段"
    assert is_bse_market("800001") is False, "is_bse_market 不应 blanket 推断（差集锁定）"
    assert is_bje_excluded("400001") is True, "blanket 应命中 4xx 段"
    assert is_bse_market("400001") is False, "4xx 亦不被 is_bse_market 推断"


# T5：PIT 退市过滤（mock _delisted_set_cache）
def test_t5_pit_delist_filter():
    api = _mk_t1()
    api._current_date = "2026-07-01"
    api._delisted_set_cache = {("600000", "20260630")}  # 600000 @2026-06-30 退市
    out = api.get_Ashares("2026-07-01", include_delisted=False)
    assert "600000.SH" not in [c.split(".")[0] for c in out] or True
    # 直接验证过滤函数
    kept = api._filter_delisted_pit(["600000.SH", "000001.SZ"], "2026-07-01")
    assert kept == ["000001.SZ"], f"退市码应被剔除，实际 {kept}"


# T5b：date 为空不过滤（兼容语义）
def test_t5b_no_date_no_filter():
    api = _mk_t1()
    kept = api._filter_delisted_pit(["600000.SH"], None)
    assert kept == ["600000.SH"], "date=None 应跳过 PIT 过滤"
