"""行业成分区间规范器（transform）。

职责：将 Tushare `index_member` 返回的原始成分区间（in_date / out_date）规整为
可入库的 canonical 区间（effective_from / effective_to）。

⚠️ F4 语义边界（审核结论，2026-07-27）：
官方 Tushare `index_member` 接口**只提供 in_date / out_date 两个字段**，**没有任何关于
"同一证券在同一层级出现多条重叠成分区间时如何裁决"的官方规则**。因此本模块**严禁应用任何
项目自定义的冲突裁决**（例如早期版本中的"effective_from 较新者胜"段重写）。

我们严格 1:1 保留来源给出的每个原始区间：
    effective_from = in_date（毫秒）
    effective_to   = out_date（毫秒；NULL 表示至今）
重叠区间（如 SW2021 行业重新分类导致同一证券在某日同时属于新旧两类）作为**原始事实
原样保留**，仅记录到 stats 供质量门控与 capability 报告使用。因此 canonical
industry_membership 在该情形下**不是 PIT READY**，而是 APPROXIMATION_REQUIRES_CONFIRMATION
（详见 docs/data-pipeline-contract.md F4 / docs/strategy_toolbox.md get_industry）。

唯一的硬丢弃：明确非法的区间（effective_from > effective_to，来源脏数据）。
"""
from __future__ import annotations

import logging
from typing import Dict, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class MembershipIntervalError(ValueError):
    """区间规整阶段的可恢复错误（如全部区间非法）。"""


# 当前（NULL effective_to）视为无穷大的毫秒上界
_NULL_TO_BOUND_MS = 9_999_999_999_999_999
_ONE_DAY_MS = 86_400_000
_INF = 2**62


def _to_ms(value) -> int:
    """将 in_date / out_date 字符串规整为毫秒时间戳。

    空 / None / 非法 → None（表示至今）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in ("NONE", "NULL", "NAN"):
        return None
    try:
        ts = pd.Timestamp(str(value))
    except Exception:
        return None
    return int(ts.timestamp() * 1000)


def resolve_membership_intervals(df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """将原始行业成分区间规整为可入库区间（transform）。

    规则（见模块 docstring）：
    - 1:1 保留每个原始区间（in_date→effective_from，out_date→effective_to）；
    - 丢弃 effective_from > effective_to 的非法区间（计数）；
    - **不**应用任何自定义冲突裁决；重叠区间原样保留并统计：
      ambiguous_positive_pairs（正持续时间重叠对）、
      ambiguous_boundary_pairs（仅共享单一边界日的区间对）、
      ambiguous_codes（涉及证券数）、multi_current_codes（多个 NULL 的证券数）。

    Args:
        df: 含 classification_system/classification_version/industry_level/
            industry_code/code/effective_from/effective_to 列的 DataFrame
            （毫秒，effective_to 允许 None/NaN 表示至今）。

    Returns:
        (clean_df, stats)
    """
    stats = {"total": 0, "dropped_bad_ranges": 0,
             "ambiguous_positive_pairs": 0, "ambiguous_boundary_pairs": 0,
             "ambiguous_codes": 0, "multi_current_codes": 0}
    if df is None or len(df) == 0:
        return df, stats

    out = df.copy()
    stats["total"] = len(out)
    # 非法区间剔除（None/NaN effective_to 不参与比较，视为至今）
    from_num = pd.to_numeric(out["effective_from"], errors="coerce")
    to_num = pd.to_numeric(out["effective_to"], errors="coerce")
    bad = from_num.notna() & to_num.notna() & (from_num > to_num)
    stats["dropped_bad_ranges"] = int(bad.sum())
    out = out[~bad].reset_index(drop=True)

    # 歧义统计（原样保留，不裁决）
    group_cols = ["classification_system", "classification_version",
                  "industry_level", "code"]
    ambiguous_codes = set()
    multi_current = 0
    for keys, g in out.groupby(group_cols, sort=False):
        rows = []
        for _, r in g.iterrows():
            f = r["effective_from"]
            t = r["effective_to"]
            f = int(f) if f is not None and pd.notna(f) else None
            t = int(t) if t is not None and pd.notna(t) else None
            if f is None:
                continue
            rows.append((f, t, str(r["industry_code"])))
        nulls = sum(1 for _, t, _ in rows if t is None)
        if nulls > 1:
            multi_current += 1
            ambiguous_codes.add(keys)
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                f1, t1, ind1 = rows[i]
                f2, t2, ind2 = rows[j]
                if ind1 == ind2:
                    continue
                lo = max(f1, f2)
                hi = min(t1 if t1 is not None else _INF,
                         t2 if t2 is not None else _INF)
                if hi - lo > 0:
                    stats["ambiguous_positive_pairs"] += 1
                    ambiguous_codes.add(keys)
                elif hi - lo == 0:
                    stats["ambiguous_boundary_pairs"] += 1
                    ambiguous_codes.add(keys)
    stats["ambiguous_codes"] = len(ambiguous_codes)
    stats["multi_current_codes"] = multi_current
    if stats["dropped_bad_ranges"] or ambiguous_codes:
        logger.info(f"[industry-transform] raw-preserving stats: {stats}")
    return out, stats
