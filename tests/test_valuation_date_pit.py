# -*- coding: utf-8 -*-
"""B2 常设回归：get_fundamentals(valuation) 的 date 分界契约（2026-09-04）。

契约来源：docs/valuation-date-pit-fix-design.md（审计通过 + 四项钉死）。
验收证据：docs/evidence/valuation-date-pit-acceptance.md。

契约（防漂移，任何重构若改回去，本测试即报警）：
  1. date 未传 / date == T / date == T-1 -> 预加载快照路径，结果逐位不变；
  2. date < T-1                         -> 真 as-of（query_valuation_daily_pit），与库内真值一致；
  3. date 解析失败                      -> 维持现行 fail-soft（吞异常 + warning，返回空 DataFrame），
                                           不新增也不移除 fail-soft；
  4. 空字符串 date                      -> 视为未传，走快照路径；
  5. get_fundamentals 的当日查询缓存键包含 date。
"""
import hashlib
import inspect
from pathlib import Path

import pandas as pd
import pytest

from quantstudio._paths import db_path

ROOT = Path(__file__).resolve().parents[1]
DATE, PREV, HIST = "2026-07-30", "2026-07-29", "2026-07-23"
CODES = ["600519.SS", "000060.SZ", "000001.SZ"]
FIELDS = ["float_value", "total_value", "turnover_ratio", "pe_ratio", "a_floats"]
# 2026-07-23 的 turnover_rate 真值（stock_daily_valuation），as-of 对拍锚点
EXPECT_TURNOVER = {"600519": 0.2713, "000060": 2.6914, "000001": 0.5647}


def _ensure_db():
    db = db_path()
    if not db.exists():
        pytest.skip("测试库不存在: %s" % db)
    return db


def _attach_api():
    from quantstudio.backtest.backtest_engine import BacktestEngine, EngineConfig
    from quantstudio.backtest.ptrade_api import _api
    db = _ensure_db()
    cfg = EngineConfig(db_path=db, output_dir=ROOT / "output",
                       research_dir=ROOT / "output" / "research")
    engine = BacktestEngine(db_path=str(db), strategy={}, start="2026-01-01",
                            end=DATE, config=cfg, strategy_type="ptrade")
    _api.attach(engine, None, None, DATE, PREV, {})
    return _api


def _sig(df):
    """排序无关的值指纹：列排序 + 索引排序后哈希（as-of 分支行序不保证稳定）。"""
    d = df.copy()
    d.index = [str(i) for i in d.index]
    d = d.reindex(sorted(d.columns), axis=1).sort_index()
    return hashlib.sha256(pd.util.hash_pandas_object(d, index=True).values.tobytes()).hexdigest()


def _call(api, date):
    if date is None:
        return api.get_fundamentals(CODES, "valuation", fields=FIELDS)
    return api.get_fundamentals(CODES, "valuation", fields=FIELDS, date=date)


# ---------- 1. 分界三例逐位一致 ----------

def test_boundary_unspecified_T_and_T_minus_1_are_identical():
    api = _attach_api()
    fp_none, fp_T, fp_T1 = (_sig(_call(api, None)), _sig(_call(api, DATE)), _sig(_call(api, PREV)))
    assert fp_none == fp_T == fp_T1, (
        "date 未传 / =T / =T-1 必须走同一条快照路径且结果逐位一致；"
        "不一致说明 date 分界被改坏（date<T-1 才允许走 as-of）")


def test_future_date_falls_back_to_snapshot_path():
    """date > T 落入快照路径 -> 与 T-1 一致（无未来函数泄漏）。"""
    api = _attach_api()
    assert _sig(_call(api, "2026-08-15")) == _sig(_call(api, PREV))


# ---------- 2. date < T-1 走真 as-of ----------

def test_history_date_returns_true_asof_values():
    api = _attach_api()
    df = _call(api, HIST)
    assert len(df) >= 1, "as-of 查询不应返回空表（库内该日有估值）"
    got = {str(i).split(".")[0]: round(float(r["turnover_ratio"]), 4) for i, r in df.iterrows()}
    for code, expect in EXPECT_TURNOVER.items():
        assert code in got, "as-of 结果缺少 %s" % code
        assert abs(got[code] - expect) < 1e-6, (
            "%s 的 2026-07-23 换手率应为 %.4f，实得 %s（若等于快照值说明 date 又被丢弃）"
            % (code, expect, got[code]))


def test_history_date_differs_from_snapshot():
    """修复的判别式：date < T-1 的结果必须不同于快照（否则 date 参数再次失效）。"""
    api = _attach_api()
    assert _sig(_call(api, HIST)) != _sig(_call(api, PREV)), (
        "date < T-1 返回与快照相同 -> date 分界失效（预加载短路回归）")


# ---------- 3. 解析失败 / 空串：维持现状 ----------

def test_invalid_date_fail_soft_unchanged():
    """现行行为 = 吞异常 + warning，返回空 DataFrame；本次修复不改变它。"""
    api = _attach_api()
    for bad in ("not-a-date", "2026-13-45"):
        df = api.get_fundamentals(CODES, "valuation", fields=FIELDS, date=bad)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0, "非法 date 应维持 fail-soft 空表，实得 %d 行" % len(df)


def test_empty_string_date_treated_as_snapshot():
    """空串为 falsy -> 与未传等价（快照路径）。"""
    api = _attach_api()
    assert _sig(_call(api, "")) == _sig(_call(api, None))


# ---------- 4. 缓存键含 date ----------

def test_query_cache_key_contains_date():
    """运行期验证：不同 date 产生不同缓存键，且键内含 date 字面量。

    若缓存键不含 date，不同日期的查询会互相污染，date 分界修复被架空；
    同时断言第二次调用确实命中了不同的键（而非复用第一条结果）。
    """
    api = _attach_api()
    assert hasattr(api, "_query_cache"), "_query_cache 不存在（attach 未生效）"
    fp_prev = _sig(_call(api, PREV))
    fp_hist = _sig(_call(api, HIST))
    keys = [k for k in api._query_cache if isinstance(k, tuple) and len(k) > 1 and k[0] == "fund"]
    assert len(keys) >= 2, "两次不同 date 的查询应产生至少 2 个不同缓存键，实得 %d" % len(keys)
    blob = repr(keys)
    assert PREV in blob and HIST in blob, (
        "缓存键必须包含 date 字面量；实际键 = %s" % blob[:300])
    assert fp_prev != fp_hist, (
        "date=T-1 与 date<T-1 结果相同 -> date 分界失效（预加载短路回归）")

    # 再次调用必须命中缓存（同键同值），确认缓存未跨日期串味
    assert _sig(_call(api, HIST)) == fp_hist
