"""F4 transform 单测：保留 raw 区间、不应用自定义冲突裁决、标注歧义。

审核结论（2026-07-27）：官方 index_member 仅提供 in_date/out_date，无任何冲突裁决
规则。canonical 区间必须 1:1 保留原始区间（effective_from=in_date,
effective_to=out_date），重叠区间原样保留并计入 stats，绝不应用“effective_from 较新者胜”。

列契约与上游 tushare_adapter 调 resolve_membership_intervals 一致：
classification_system / classification_version / industry_level / code /
industry_code / effective_from / effective_to（毫秒，effective_to 允许 None 表示至今）。
"""
import pandas as pd
import pytest

from quantstudio.pipeline.industry_membership_standardizer import (
    resolve_membership_intervals,
)


def _ms(s):
    return int(pd.Timestamp(s).timestamp() * 1000)


def _row(code, ind, frm, to):
    return {
        "classification_system": "SW", "classification_version": "SW2021",
        "industry_level": "L1", "code": code,
        "industry_code": ind, "industry_name": ind,
        "effective_from": _ms(frm),
        "effective_to": (None if to is None else _ms(to)),
    }


def test_keeps_raw_intervals_no_winner():
    """重叠区间原样保留（不丢弃赢家），并标注 ambiguous_positive_pairs。"""
    df = pd.DataFrame([
        _row("600519", "801010", "2018-01-01", "2021-12-10"),
        _row("600519", "801020", "2018-01-01", None),  # 与上一行重叠（SW2021 重新分类）
    ])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 2  # 两条都保留
    assert stats["ambiguous_positive_pairs"] == 1
    assert stats["ambiguous_codes"] == 1
    # 输出区间与输入 1:1 一致（未被重写）
    assert set(clean["industry_code"]) == {"801010", "801020"}


def test_drops_only_bad_ranges():
    """仅丢弃 effective_from > effective_to 的非法区间。"""
    df = pd.DataFrame([
        _row("600000", "801010", "2020-07-01", "2020-06-30"),  # 坏区间
        _row("600000", "801020", "2020-07-01", None),
    ])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 1
    assert stats["dropped_bad_ranges"] == 1
    assert stats["ambiguous_positive_pairs"] == 0
    assert stats["ambiguous_boundary_pairs"] == 0
    assert clean.iloc[0]["industry_code"] == "801020"


def test_adjacent_intervals_no_ambiguity():
    """相邻（边界相接、不重叠）区间不触发歧义。"""
    df = pd.DataFrame([
        _row("600000", "801010", "2018-01-01", "2020-06-30"),
        _row("600000", "801020", "2020-07-01", None),
    ])
    clean, stats = resolve_membership_intervals(df)
    assert len(clean) == 2
    assert stats["ambiguous_positive_pairs"] == 0
    assert stats["ambiguous_boundary_pairs"] == 0


def test_empty_returns_empty():
    clean, stats = resolve_membership_intervals(pd.DataFrame())
    assert clean.empty
    assert stats["total"] == 0
    assert stats["ambiguous_positive_pairs"] == 0
    assert stats["ambiguous_boundary_pairs"] == 0
