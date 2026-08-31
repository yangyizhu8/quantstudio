# -*- coding: utf-8 -*-
"""fund-matrix 契约覆盖测试（批次1·C/B，2026-08-31）。

按 docs/evidence/fundamentals-contract-matrix.yaml 逐形态补证（串引探针证据编号）：
  - f01/f03/f08/f10     已有 settled 测试（p10 系列）✓
  - f02 回退路径         本文件 test_fm_b6c_range_exception_fallback_b6b / failopen2
  - f06 or_yoy 翻译      已有 test_p10_field_map_request_translated ✓（本文件不再重复）
  - f07 eps P-A2         本文件 test_fm_eps_basic_translation
  - f04/f14 RD-1 固化    本文件 test_fm_rd1_seeds_no_score_semantics（P2 证据）
  - f05 RD-2 固化        本文件 test_fm_rd2_roe_passthrough_no_ann_date（P3 证据）
  - f11 prefetch SKIP    本文件 test_fm_prefetch_skip_pool_gt32（v8.3 卡死，2026-08-31）
  - f11 小池缓存         本文件 test_fm_prefetch_small_pool_cache_hit
  - f13 RD-3             已有 test_b1_industry_wrapper_pool_and_failopen ✓
缺口（待 P5）：f09 list+date+range（矩阵 o）
"""
import numpy as np
import pandas as pd

import quantstudio.strategy_compiler.source_import as si



def _ed_dstr(x):
    """end_date ms epoch -> 'YYYY-MM-DD'（本地策略 np.datetime64(int(ed),'ms') 消费语义，
    v8.7 契约：wrapper 归一 end_date 为 epoch 毫秒）。"""
    import numpy as _np
    try:
        return str(_np.datetime64(int(float(x)), 'ms'))[:10]
    except Exception:
        return 'NA'  # NaN/None end_date 容错

def _shape_check_def():
    return '''def _qs_shape_check(api_name, expected, actual):
    if actual is None:
        raise AssertionError("%s: None 返回（期望 %s）" % (api_name, expected))
'''


def _mk(platform_fake, eps_basis="passthrough", **g_attrs):
    """exec _QS_FUNDAMENTALS_EXT（eps_basis 参数化）+ mock 平台；返回 ns（含 g/calls/warnings）。
    探针证据串引：模板行为 = 平台实证契约（P1-P4），断言以证据号码注释。"""
    calls = []
    warns = []
    infos = []

    def _fake(*a, **k):
        calls.append((a, k))
        return platform_fake(*a, **k)

    log = type("L", (), {
        "warning": staticmethod(lambda *a, **k: warns.append((a, k))),
        "info": staticmethod(lambda *a, **k: infos.append((a, k)))})()

    g = type("G", (), {})()
    for _k, _v in g_attrs.items():
        setattr(g, _k, _v)

    ns = {"get_fundamentals": _fake, "log": log, "g": g}
    exec(_shape_check_def(), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# fm", eps_basis=eps_basis), ns)
    ns["_g"] = g
    ns["_calls"] = calls
    ns["_warnings"] = warns
    ns["_infos"] = infos
    return ns


# ---------- f02：B6c 主路径异常 → 回退 B6b 双查询 ----------

def test_fm_b6c_range_exception_fallback_b6b():
    """f02 回退路径（P1：range 多期 / date 前最近披露期）：range 透传抛异常 → 回退
    B6b date-only 双查询（cur + curED 年-1同月日），结果可消费。"""
    def fake(*a, **k):
        if k.get("start_year") is not None or k.get("end_year") is not None:
            raise RuntimeError("range unsupported")
        y = str(k["date"])[:4]
        return pd.DataFrame({"end_date": ["%s-03-31" % y],
                             "np_parent_company_owners": [float(y)]},
                            index=["000001.SZ"])

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260701", start_year=2024, end_year=2026)
    eds = sorted(_ed_dstr(x) for x in out["end_date"] if _ed_dstr(x) != "NA")
    assert eds == ["2025-03-31", "2026-03-31"], eds  # 回退成功：cur + 反推同比期
    assert any("GF-RANGE-FAILOPEN" in str(w[0]) for w in ns["_warnings"])


def test_fm_b6c_range_failopen2_both_levels():
    """f02 回退也失败：range 与 date-only 全抛 → GF-RANGE-FAILOPEN2 → 落到平台路径
    （逐股退化），不崩溃、契约空 df 兜底（P-D10 v1.2 空行为）。"""
    def fake(*a, **k):
        raise RuntimeError("all unsupported")

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260701", start_year=2024, end_year=2026)
    warns = " ".join(str(w[0]) for w in ns["_warnings"])
    assert "GF-RANGE-FAILOPEN" in warns and "GF-RANGE-FAILOPEN2" in warns
    assert out is not None and "end_date" in out.columns  # 契约列保留（0 行或 NaN 行不炸）


# ---------- gap：部分缺列补 NaN（P-D10 v1.2 语义扩展） ----------

def test_fm_gap_partial_missing_nan_cols():
    """B8 部分缺列：available 值保留 + missing 补 NaN 列（v7 _qs_fund_fields_merge 同款，
    P2 缺列家族语义）——策略 KeyError 免疫。"""
    def fake(*a, **k):
        return pd.DataFrame({"zz_ok_field": [1.0]}, index=["000001.SZ"])

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "valuation",
                                 fields=["zz_ok_field", "zz_miss_field"], date="20260701")
    assert list(out.columns) == ["zz_ok_field", "zz_miss_field"]
    assert out["zz_ok_field"].iloc[0] == 1.0
    assert np.isnan(out["zz_miss_field"].iloc[0])


# ---------- f11：prefetch 大池 SKIP / 小池缓存（v8.3 卡死，2026-08-31） ----------

def test_fm_prefetch_skip_pool_gt32():
    """f11 prefetch pool>32 → QS_GF_PREFETCH_SKIP（v8.3 大池 list 卡死保守化，2026-08-31）；
    SKIP 按月幂等（第二次调用不再重复 SKIP，直接逐股直调）。"""
    universe = ["6000%02d.SS" % i for i in range(40)]

    def fake(*a, **k):
        return pd.DataFrame({"roe": [10.0], "end_date": ["2026-03-31"]}, index=[k["date"]])

    ns = _mk(fake, universe=universe)
    out1 = ns["get_fundamentals"]("600001.SS", "profit_ability", ["roe"], date="20260701")
    assert float(out1["roe"].iloc[0]) == 10.0
    skips = [w for w in ns["_infos"] if "QS_GF_PREFETCH_SKIP" in str(w[0])]
    assert len(skips) == 1, skips
    n1 = len(ns["_calls"])
    ns["get_fundamentals"]("600001.SS", "profit_ability", ["roe"], date="20260701")
    skips2 = [w for w in ns["_infos"] if "QS_GF_PREFETCH_SKIP" in str(w[0])]
    assert len(skips2) == 1, "SKIP 应按月幂等"
    assert len(ns["_calls"]) == n1 + 1, "第二次为单码逐股直调"


def test_fm_prefetch_small_pool_cache_hit():
    """f11 小池（≤32）批量预取 → 单码 cache_get 命中零平台直调（B9 设计保留）。"""
    codes = ["6000%02d.SS" % i for i in range(3)]

    def fake(*a, **k):
        return pd.DataFrame({"roe": [10.0, 11.0, 12.0],
                             "end_date": ["2026-03-31"] * 3}, index=codes)

    ns = _mk(fake, universe=codes)
    out1 = ns["get_fundamentals"](codes[0], "profit_ability", ["roe"], date="20260701")
    # 首调：gap 无 → 小池预取 2 期（list×2）→ cache_get 命中 → 无单码直调
    assert float(out1["roe"].iloc[0]) == 10.0
    n = len(ns["_calls"])
    assert n == 2, "小池预取 2 期 list 调用（date / date-1年）: %r" % (ns["_calls"],)
    ns["get_fundamentals"](codes[1], "profit_ability", ["roe"], date="20260701")
    assert len(ns["_calls"]) == n, "第二码应命中缓存，零新增平台调用"


# ---------- f07：eps P-A2 请求/返回双语翻译 ----------

def test_fm_eps_basic_translation():
    """f07 eps P-A2（审计 v2 收尾项 + 探针乙，2026-08-24）：eps_basis=basic →
    请求字段 eps→basic_eps，返回 basic_eps 列逆翻译回 eps（本地语义锚）。"""
    def fake(*a, **k):
        assert k["fields"] == ["basic_eps", "publ_date", "end_date"], k
        return pd.DataFrame({"basic_eps": [0.67], "publ_date": ["2026-04-25"],
                             "end_date": ["2026-03-31"]}, index=["000001.SZ"])

    ns = _mk(fake, eps_basis="basic")
    out = ns["get_fundamentals"]("000001.SZ", "eps",
                                 fields=["eps", "publ_date", "end_date"], date="20260701")
    assert list(out.columns) == ["eps", "publ_date", "end_date"]
    assert float(out["eps"].iloc[0]) == 0.67  # 逆翻译回本地 eps 名


# ---------- f04：RD-1 固化断言（P2 证据） ----------

def test_fm_rd1_seeds_no_score_semantics():
    """f04/RD-1（P2：valuation.total_share EMPTY）：seeds 首调短路 → 1 行 NaN；
    ⑦『ts_now<=ts_prev』恒 False → 平台恒不加分（RD-1 登记契约固化断言，
    未来若平台补列导致行为意外变化即此处 fail → 触发重新裁决）。"""
    def fake(*a, **k):
        raise AssertionError("seeds 短路不应调平台")

    ns = _mk(fake)  # 无 g：种子集独立生效
    v = ns["get_fundamentals"]("000001.SZ", "valuation", ["total_share"], date="20260701")
    assert len(v) == 1 and np.isnan(v["total_share"].iloc[0])
    # 模拟本地 ⑦ 判定（quantstudio/backtest/strategies/F-Score选股RSRS择时.py L162-169）
    ts_now = float(v["total_share"].iloc[0])
    assert not (ts_now is None or ts_now <= ts_now), "NaN 比较恒 False → ⑦ 不加分"
    assert len(ns["_calls"]) == 0


# ---------- f05：RD-2 固化断言（P3 证据） ----------

def test_fm_rd2_roe_passthrough_no_ann_date():
    """f05/RD-2（P3：profit_ability.roe date 模式 OK / 无 ann_date）：ROE 原样透传、
    不做 PIT ann_date 过滤（非 fin_indicator）；仅 Step5 排序消费（RD-2 固化断言）。"""
    seen = {}

    def fake(*a, **k):
        seen["fields"] = k["fields"]
        return pd.DataFrame({"roe": [10.5], "end_date": ["2026-03-31"]}, index=["000001.SZ"])

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "profit_ability", ["roe"], date="20260701")
    assert float(out["roe"].iloc[0]) == 10.5
    assert "ann_date" not in seen["fields"], "平台 ROE 走 date 模式，无 PIT 过滤字段"


# ---------- 矩阵缺口状态快照（防回归漂移） ----------

def test_fm_pit_empty_publ_date_nan_placeholder_dropped():
    """f02 PIT 值域兜底（v8.6，P5-1/P5-7 实证）：平台 list+range 模式 publ_date 全空
    （empty=18），未披露占位期（2026-06-30 值全 NaN）须被剔——否则 _latest_statement
    cur 取 NaN 期 → fscore 实跑 3（平台复算 79 的差异源，RD-4）。空串绝不能 astype 崩。"""
    import re
    import numpy as np
    import pandas as pd
    import quantstudio.strategy_compiler.source_import as si

    def fake(*a, **k):
        idx = pd.MultiIndex.from_tuples(
            [("2025-03-31", "000001.SZ"), ("2026-03-31", "000001.SZ"),
             ("2026-06-30", "000001.SZ")], names=["end_date", "secu_code"])
        return pd.DataFrame({"np_parent_company_owners": [17.5, 17.0, np.nan],
                             "publ_date": ["", "", ""]}, index=idx)

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260701", start_year=2025, end_year=2026)
    eds = sorted(_ed_dstr(x) for x in out["end_date"] if _ed_dstr(x) != "NA")
    assert "2026-06-30" not in eds, "2026-06-30 NaN 占位应被 PIT 值域兜底剔除（publ_date 全空场景）"
    assert eds == ["2025-03-31", "2026-03-31"], eds


def test_fm_list_range_small_pool():
    """f09 补证（P5-1 平台实证 shape=18 MultiIndex 6 期）：list(小池≤32)+date+range →
    multi2 拍平 per-code 多期 + PIT（未披露 NaN 占位剔）→ 每码 cur/prev 可消费。"""
    import numpy as np
    import pandas as pd

    codes = ["600000.SS", "000001.SZ"]

    def fake(*a, **k):
        idx = pd.MultiIndex.from_tuples(
            [("2025-03-31", "600000.SS"), ("2025-03-31", "000001.SZ"),
             ("2026-03-31", "600000.SS"), ("2026-03-31", "000001.SZ"),
             ("2026-06-30", "600000.SS"), ("2026-06-30", "000001.SZ")],
            names=["end_date", "secu_code"])
        return pd.DataFrame({"np_parent_company_owners": [17.5, 14.0, 17.8, 14.5,
                                                          np.nan, np.nan],
                             "publ_date": ["", "", "", "", "", ""]}, index=idx)

    ns = _mk(fake)
    out = ns["get_fundamentals"](codes, "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260701", start_year=2025, end_year=2026)
    per = {}
    for i in range(len(out)):
        c = out.index[i]
        per.setdefault(c, []).append(_ed_dstr(out["end_date"].iloc[i]))
    for c in codes:
        assert sorted(per.get(c, [])) == ["2025-03-31", "2026-03-31"], (c, per.get(c))
    assert len(ns["_calls"]) == 1, "list+range 单调用"


def test_fm_matrix_o_gap_only_f09():
    """D 底座：矩阵 ○ 缺口应为空（f09 已由 test_fm_list_range_small_pool 补 tested、
    P5-1/P5-7 已证 probed）——矩阵全绿，补证边界闭合。"""
    import yaml
    from pathlib import Path
    data = yaml.safe_load(
        Path("docs/evidence/fundamentals-contract-matrix.yaml").read_text(encoding="utf-8"))
    gaps = [r["id"] for r in data["matrix"] if not (r.get("tested") and r.get("probed"))]
    assert gaps == [], gaps


# ---------- 补充：f12 report_types / multi2 直传 / cache miss / list 批量 / eps 反证 ----------

def test_fm_report_types_filter_range():
    """f12（P1：report_types 1→0331）：B6c range 透传 + report_types 过滤后仅保留 0331 期。"""
    def fake(*a, **k):
        idx = pd.MultiIndex.from_tuples(
            [("2026-03-31", "000001.SZ"), ("2025-12-31", "000001.SZ")],
            names=["end_date", "secu_code"])
        return pd.DataFrame({"np_parent_company_owners": [3.0, 5.0],
                             "publ_date": ["2026-04-25", "2026-03-20"]}, index=idx)

    ns = _mk(fake)
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260701", start_year=2025, end_year=2026,
                                 report_types=1)
    eds = sorted(_ed_dstr(x) for x in out["end_date"] if _ed_dstr(x) != "NA")
    assert eds == ["2026-03-31"], eds  # 只留 0331 期


def test_fm_multi_flat_passthrough_and_flat():
    """B6c multi2 拍平（P1 契约）：MultiIndex(end_date,secu_code) → 普通行（end_date/code 列）；
    非 MultiIndex 原样直传。"""
    ns = _mk(lambda *a, **k: pd.DataFrame({"x": [1.0]}, index=["000001.SZ"]))
    df1 = pd.DataFrame({"x": [1.0]}, index=["000001.SZ"])
    out1 = ns["_qs_multi_flat"](df1)
    assert out1 is df1, "非 MultiIndex 原样返回"

    df2 = pd.DataFrame({"x": [1.0, 2.0]}, index=pd.MultiIndex.from_tuples(
        [("2025-03-31", "000001.SZ"), ("2026-03-31", "000001.SZ")],
        names=["end_date", "secu_code"]))
    out2 = ns["_qs_multi_flat"](df2)
    assert not isinstance(out2.index, pd.MultiIndex)
    assert list(out2.columns) == ["x", "end_date", "code"]
    assert out2["code"].tolist() == ["000001.SZ", "000001.SZ"]


def test_fm_cache_miss_fallback_platform():
    """B9 缓存未命中（字段集合不同于预取）→ 回退平台直调（不误返回缺列缓存）。"""
    codes = ["6000%02d.SS" % i for i in range(3)]

    def fake(*a, **k):
        return pd.DataFrame({"roe": [10.0, 11.0, 12.0],
                             "end_date": ["2026-03-31"] * 3}, index=codes)

    ns = _mk(fake, universe=codes)
    # 首次：小池预取（roe）→ cache 命中
    ns["get_fundamentals"](codes[0], "profit_ability", ["roe"], date="20260701")
    n = len(ns["_calls"])
    # 不同字段集合 → 缓存不适用 → 平台直调
    out = ns["get_fundamentals"](codes[0], "profit_ability", ["roe", "extra_f"], date="20260701")
    assert len(ns["_calls"]) == n + 1, "字段集合未命中缓存 → 单码直调"
    assert "extra_f" in out.columns


def test_fm_list_multi_secs_contract():
    """f10（P-D10 500 码 0.05s）：list 多码（>32 也直通）→ 平台 list 单调用、返回
    index=code 契约 df（逐股退化不触发）。"""
    codes = ["6000%02d.SS" % i for i in range(40)]

    def fake(*a, **k):
        return pd.DataFrame({"float_value": [float(i) for i in range(40)],
                             "end_date": ["2026-03-31"] * 40}, index=codes)

    ns = _mk(fake)
    out = ns["get_fundamentals"](codes, "valuation", ["float_value"], date="20260701")
    assert len(out) == 40 and list(out.index) == codes
    assert len(ns["_calls"]) == 1, "list 批量单调用"


def test_fm_eps_passthrough_no_translation():
    """f07 反证（P-A2 默认 passthrough）：eps_basis=passthrough → 请求字段 eps 原样
    （不翻译 basic_eps）、返回平台 eps 列不逆翻译——双语对照锁定 f07 语义。"""
    def fake(*a, **k):
        assert k["fields"] == ["eps", "publ_date", "end_date"], k
        return pd.DataFrame({"eps": [0.75], "publ_date": ["2026-04-25"],
                             "end_date": ["2026-03-31"]}, index=["000001.SZ"])

    ns = _mk(fake, eps_basis="passthrough")
    out = ns["get_fundamentals"]("000001.SZ", "eps",
                                 fields=["eps", "publ_date", "end_date"], date="20260701")
    assert float(out["eps"].iloc[0]) == 0.75