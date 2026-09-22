# -*- coding: utf-8 -*-
"""code_contract 形式契约单测（错误二 T1）。

测试向量纪律：**全部取自实测污染样本与生产合法码对照**，非虚构——
拒绝向量 = 8 处实测脏值形态（TEST999.SH/FIXTEST/TEST.SH/GISISI_TEST*）+ 边界劣形；
通过向量 = 沪深主板/创业/科创/北交所/ETF/指数 合法 6 位码全谱 + 带后缀合法形态。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.pipeline.code_contract import (  # noqa: E402
    validate_sec_code, is_valid_sec_code, filter_sec_codes)


# ========== 拒绝向量：8 处实测污染（错误二 §1.1） ==========

def test_reject_measured_dirty_values():
    """实测 8 处脏值全部纯形式拒绝（无关键词参与）。"""
    dirty = [
        "TEST999.SH",   # 落盘分片 etf_daily（触发误冷启动）
        "test999.sh",   # 大小写变体
        "FIXTEST",      # qfq_aux.adj_factor（卡死 qfq_orch 周期的实锤）
        "TEST.SH",      # block_trade/limit_cpt_list/limit_step/stk_factor_pro/top_list
        "GISISI_TEST",  # sentiment_factor_daily
        "GISISI_TEST3",
    ]
    for raw in dirty:
        ok, norm, reason = validate_sec_code(raw)
        assert not ok, "脏值竟通过契约: %r" % raw
        assert norm == ""
        assert reason == "non_canonical", "%r reason=%r" % (raw, reason)


def test_reject_boundary_forms():
    """边界劣形一律拒（位数域不猜语义，宽进必漏）。"""
    bad = ["60000", "6000000", "sh600000", "60000.SH", "600000.SHXX",
           "600000.XX", "6000 0", "60000A", "9999999", "００００"]  # 全角
    for raw in bad:
        assert not is_valid_sec_code(raw), "劣形竟通过: %r" % raw


def test_reject_null_forms():
    """None/空串/NONE 显式 reason（与 _normalize_code 旧语义对齐，便于迁移）。"""
    assert validate_sec_code(None) == (False, "", "none")
    assert validate_sec_code("   ") == (False, "", "empty")
    assert validate_sec_code("none") == (False, "", "NONE")
    assert validate_sec_code("NONE") == (False, "", "NONE")


# ========== 通过向量：合法码全谱（零误杀面证明） ==========

def test_accept_bare_canonical():
    """裸 6 位全谱：主板/创业/科创/北交所/ETF/指数（位数相同形态相同即合法，不猜语义）。"""
    legit = ["600000", "000001", "300750", "688981", "830799",   # 股/北交所
             "510300", "159915",                                  # ETF
             "000300", "880300",                                  # 指数/申万类 6 位形态
             "999999", "000000"]  # 纯形态契约：数字域码值语义不拦（哨兵语义归错误一案管辖）
    for raw in legit:
        ok, norm, reason = validate_sec_code(raw)
        assert ok, "合法裸码被拒: %r" % raw
        assert norm == raw and reason == "ok"


def test_accept_with_suffix_and_normalize():
    """带交易所后缀的合法形态 → 归一为裸码。"""
    assert validate_sec_code("600000.SH") == (True, "600000", "ok")
    assert validate_sec_code("000001.SZ") == (True, "000001", "ok")
    assert validate_sec_code("830799.BJ") == (True, "830799", "ok")
    assert validate_sec_code("510300.sh") == (True, "510300", "ok")  # 小写后缀容忍


def test_strip_whitespace():
    ok, norm, _ = validate_sec_code("  600000.SH  ")
    assert ok and norm == "600000"


# ========== 批量接口 ==========

def test_filter_alignment():
    """kept 与输入逐位对齐语义（不去重、不乱序），rejected 带原始值。"""
    vals = ["600000.SH", "FIXTEST", "510300", "TEST.SH", "000001"]
    kept, rejected = filter_sec_codes(vals)
    assert kept == ["600000", "510300", "000001"]
    assert [r[0] for r in rejected] == ["FIXTEST", "TEST.SH"]
    assert all(r[1] == "non_canonical" for r in rejected)


def test_filter_empty():
    kept, rejected = filter_sec_codes([])
    assert kept == [] and rejected == []
