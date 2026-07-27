"""F4b 审核返工：industry_membership raw 保留语义测试（2026-07-27）

官方 index_member 契约只有 in_date/out_date，无冲突裁决规则。transform 必须：
- 1:1 保留原始区间（含重叠），严禁自定义裁决；
- 仅剔除坏区间（from > to）；
- 统计歧义（正重叠/边界相接/multi-current）供门控与 capability 降级。
Provider 查询侧对歧义日期 fail-closed（另有 PIT 测试覆盖）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from quantstudio.pipeline.industry_membership_standardizer import (
    resolve_membership_intervals)


def _ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="Asia/Shanghai").timestamp() * 1000)


def _df(rows):
    return pd.DataFrame(rows, columns=[
        "classification_system", "classification_version", "industry_level",
        "industry_code", "code", "effective_from", "effective_to"])


def R(code, ind, f, t):
    return ("SW", "SW2021", "L1", ind, code, _ms(f), _ms(t) if t else None)


def test_raw_intervals_preserved_1_to_1():
    """重叠区间原样保留，绝不裁剪/重写/删除。"""
    df = _df([R("600000", "801010", "2016-01-13", "2021-12-10"),
              R("600000", "801960", "2016-01-13", "2022-07-29")])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 2
    spans = {(r["industry_code"], int(r["effective_from"]),
              None if pd.isna(r["effective_to"]) else int(r["effective_to"]))
             for _, r in clean.iterrows()}
    assert spans == {("801010", _ms("2016-01-13"), _ms("2021-12-10")),
                     ("801960", _ms("2016-01-13"), _ms("2022-07-29"))}
    assert stats["ambiguous_positive_pairs"] == 1
    assert stats["ambiguous_codes"] == 1


def test_bad_range_dropped_only():
    df = _df([R("600000", "801010", "2021-01-01", "2020-01-01"),
              R("600000", "801960", "2018-01-01", None)])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 1
    assert clean.iloc[0]["industry_code"] == "801960"
    assert stats["dropped_bad_ranges"] == 1


def test_none_effective_to_robust():
    """None/NaN effective_to 不触发比较错误，且计入 multi-current。"""
    df = _df([R("600000", "801010", "2018-01-01", None),
              R("600000", "801960", "2020-06-01", None)])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 2  # 原样保留，不裁决
    assert stats["multi_current_codes"] == 1
    assert stats["ambiguous_positive_pairs"] == 1


def test_boundary_touch_counted_separately():
    df = _df([R("600000", "801010", "2018-01-01", "2020-06-30"),
              R("600000", "801020", "2020-07-01", None),
              R("600519", "801010", "2018-01-01", "2020-07-01"),
              R("600519", "801020", "2020-07-01", None)])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 4
    assert stats["ambiguous_positive_pairs"] == 0
    assert stats["ambiguous_boundary_pairs"] == 1  # 仅 600519 的共享边界日


def test_adjacent_intervals_no_ambiguity():
    df = _df([R("600000", "801010", "2018-01-01", "2020-06-30"),
              R("600000", "801020", "2020-07-01", None)])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 2
    assert stats["ambiguous_positive_pairs"] == 0
    assert stats["ambiguous_boundary_pairs"] == 0


def test_empty_input():
    clean, stats = resolve_membership_intervals(pd.DataFrame())
    assert len(clean) == 0
    assert stats["total"] == 0
