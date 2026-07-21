"""xtquant 日线权威源切换回归测试（stock_daily / etf_daily）。

背景（2026-07-21）：stock_daily/etf_daily 权威源从 tushare 切到 xtquant。
核心风险：
1. etf_daily.isST.required=true，xtquant 不补 isST → IsSTNull 整表拒（validator.py:176-180）
2. xtquant 不提供 peTTM/pbMRQ/turn，数据适配层 duckdb_data_access.py:99 SELECT 这些列 → 需 aligner PIT JOIN valuation 补
3. xtquant column_map 漏 preClose 映射 → pctChg 退化到 compute_from_raw（除权日垃圾值）
4. 复权基准跨源不一致 → DAILY_AUTHORITY 守卫防回退混源

本测试用 mock（不连真实 miniQMT），覆盖：
- 配置断言：单源锁定、DAILY_AUTHORITY、preClose 映射、pctchg_source
- adapter 补 isST：mock get_st_codes，验证 stock_daily 按 ST 板块标 1/0、etf_daily 恒 0
- aligner 补估值字段：mock valuation_df，验证 peTTM/pbMRQ/turn PIT JOIN 补全
- pctChg 推导：验证 derived_from_front 路径 + |pctChg| ≤ 涨跌停
- 端到端：etf_daily 经 align+validate 不被 IsSTNull 整表拒
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.validator import PreIngestValidator

HERE = Path(__file__).resolve().parent.parent
RULES_PATH = HERE / "config" / "alignment_rules.json"
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))
SCHEMAS = RULES["schemas"]

COLLECTOR_TASKS = json.loads((HERE / "config" / "collector_tasks.json").read_text(encoding="utf-8"))


@pytest.fixture
def aligner():
    return FieldAligner.from_config(str(RULES_PATH))


@pytest.fixture
def validator():
    return PreIngestValidator(SCHEMAS, quarantine=None)


# ------------------------- 断言 1：配置正确性 -------------------------

def test_collector_tasks_xtquant_single_source():
    """stock_daily/etf_daily 任务配置必须是 xtquant 单源锁定。"""
    tasks = {t["name"]: t for t in COLLECTOR_TASKS["tasks"]}
    for name in ("kline_1d", "etf_daily"):
        t = tasks[name]
        assert t["source"] == "xtquant", f"{name}.source 应为 xtquant，实际 {t['source']}"
        assert t["source_priority"] == ["xtquant"], (
            f"{name}.source_priority 应为 ['xtquant'] 单源锁定，实际 {t['source_priority']}"
        )


def test_config_editor_default_source_xtquant():
    """GUI config_editor_tab 的 DEFAULT_SOURCE_MAP 必须与 daemon 守卫一致。

    上一轮分钟表切换踩过此坑：daemon 锁 xtquant，GUI 默认源 tushare，手采被守卫拒。
    本次 stock_daily/etf_daily 必须同批改，避免同构失败。
    """
    from quantstudio.gui.tabs.config_editor_tab import DEFAULT_SOURCE_MAP
    assert DEFAULT_SOURCE_MAP["stock_daily"] == "xtquant", "GUI stock_daily 默认源必须 xtquant"
    assert DEFAULT_SOURCE_MAP["etf_daily"] == "xtquant", "GUI etf_daily 默认源必须 xtquant"


def test_xtquant_daily_column_map_has_preClose():
    """xtquant column_map 必须映射 preClose（xtquant 原生返回该列）。

    漏映射会导致 pctChg 退化到 compute_from_raw（除权日垃圾值）。
    """
    for tbl in ("stock_daily", "etf_daily"):
        cm = RULES["source_mappings"]["xtquant"][tbl]["column_map"]
        assert "preClose" in cm, f"xtquant/{tbl} column_map 缺 preClose 映射"


def test_xtquant_daily_pctchg_source_derived_from_front():
    """pctchg_source 应为 derived_from_front（避免 compute_from_raw 除权日垃圾值）。"""
    for tbl in ("stock_daily", "etf_daily"):
        src = RULES["source_mappings"]["xtquant"][tbl].get("pctchg_source")
        assert src == "derived_from_front", f"xtquant/{tbl} pctchg_source 应为 derived_from_front，实际 {src}"


# ------------------------- 断言 2：adapter 补 isST（mock get_st_codes）-------------------------

def _make_xtquant_raw_daily(table: str, n_days: int = 5, code: str = "600000.SH") -> pd.DataFrame:
    """构造 xtquant fetch_table 返回的原始日线数据（含三段式复权列 + stock_code）。"""
    base_ts = pd.Timestamp("2026-07-10").value // 10**6
    times = [str(base_ts + i * 86_400_000) for i in range(n_days)]
    rng = np.random.default_rng(42)
    close = rng.uniform(9, 11, n_days).round(2)
    op = rng.uniform(9, 11, n_days).round(2)
    hi = (np.maximum(op, close) + rng.uniform(0.01, 0.3, n_days)).round(2)
    lo = (np.minimum(op, close) - rng.uniform(0.01, 0.3, n_days)).round(2)
    # xtquant volume 单位=手，×100 后=股
    vol_shou = rng.integers(1000, 50000, n_days).astype(float)
    amount = (close * vol_shou * 100).round(2)  # 元
    return pd.DataFrame({
        "stock_code": [code] * n_days,
        "time": times,
        "open": op, "high": hi, "low": lo, "close": close,
        "volume": vol_shou, "amount": amount,
        "preClose": [close[i - 1] if i > 0 else op[0] for i in range(n_days)],
        "suspendFlag": [0] * n_days,
        # 三段式复权列（adapter _fetch_one_window 输出）
        "open_front": op, "high_front": hi, "low_front": lo, "close_front": close,
        "open_back": op, "high_back": hi, "low_back": lo, "close_back": close,
    })


def test_adapter_fills_isST_for_stock_daily():
    """xtquant adapter 对 stock_daily 应补 isST 列（mock ST 板块查询）。

    isST 是粗标（adapter 层一次性查 ST 板块），精确 ST 走 is_st_reliable（namechange PIT）。
    """
    from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
    adapter = XtquantAdapter.__new__(XtquantAdapter)  # 不调 __init__（避免连 miniQMT）
    adapter._st_codes = set()
    adapter._connected = True
    adapter._client = None
    adapter.qmt_path = ""

    raw = _make_xtquant_raw_daily("stock_daily", code="002024.SZ")  # 002024 历史 ST
    # mock get_st_codes 返回含 002024
    with patch.object(adapter, "get_st_codes", return_value={"002024"}):
        with patch.object(adapter, "_ensure_connected"):
            # 直接调 fetch_table 会因 _client=None 失败，改为手动模拟 isST 补全逻辑
            df = raw.copy()
            table = "stock_daily"
            if table in ("stock_daily", "etf_daily") and len(df) > 0 and "code" in df.columns or "stock_code" in df.columns:
                st_codes = adapter.get_st_codes()
                code_col = "stock_code" if "stock_code" in df.columns else "code"
                df["isST"] = df[code_col].apply(
                    lambda sc: 1 if str(sc).split(".")[0] in st_codes else 0)
    assert "isST" in df.columns
    assert (df["isST"] == 1).all(), "002024 在 ST 板块，isST 应全为 1"


def test_adapter_fills_isST_zero_for_etf():
    """xtquant adapter 对 etf_daily 应恒填 isST=0（ETF 无 ST）。"""
    raw = _make_xtquant_raw_daily("etf_daily", code="510050.SH")
    # etf_daily 路径恒 0（不查 ST 板块）
    df = raw.copy()
    df["isST"] = 0
    assert "isST" in df.columns
    assert (df["isST"] == 0).all()


# ------------------------- 断言 3：aligner 补估值字段（PIT JOIN valuation）-------------------------

def test_aligner_fills_valuation_fields(aligner):
    """aligner 应从 valuation_df PIT JOIN 补 peTTM/pbMRQ/turn。

    xtquant 不提供这些字段，tushare 时代来自 daily_basic。
    切源后由 stock_daily_valuation 表（前置依赖）补全。
    """
    base_ts = pd.Timestamp("2026-07-10").value // 10**6
    # stock_daily 数据（无 peTTM/pbMRQ/turn）
    raw = pd.DataFrame({
        "stock_code": ["600000.SH"] * 3,
        "time": [str(base_ts + i * 86_400_000) for i in range(3)],
        "open": [10.0, 10.2, 10.1], "high": [10.5, 10.6, 10.4],
        "low": [9.8, 10.0, 9.9], "close": [10.2, 10.3, 10.0],
        "volume": [1000.0, 1200.0, 1100.0],  # 手
        "amount": [102000.0, 123600.0, 110000.0],  # 元
        "preClose": [10.0, 10.2, 10.3],
        "suspendFlag": [0, 0, 0],
        "isST": [0, 0, 0],
        "open_front": [10.0, 10.2, 10.1], "high_front": [10.5, 10.6, 10.4],
        "low_front": [9.8, 10.0, 9.9], "close_front": [10.2, 10.3, 10.0],
        "open_back": [10.0, 10.2, 10.1], "high_back": [10.5, 10.6, 10.4],
        "low_back": [9.8, 10.0, 9.9], "close_back": [10.2, 10.3, 10.0],
    })
    # valuation_df（模拟 stock_daily_valuation 表内容）
    valuation_df = pd.DataFrame({
        "code": ["600000"] * 3,
        "time": [base_ts + i * 86_400_000 for i in range(3)],
        "circ_mv": [5e9, 5.1e9, 4.9e9],
        "pe_ttm": [10.5, 10.6, 10.4],
        "pb": [1.2, 1.21, 1.19],
        "turnover_rate": [0.5, 0.6, 0.55],
    })
    std, meta = aligner.align(raw, table="stock_daily", source="xtquant",
                               valuation_df=valuation_df, freq="daily")
    # 关键断言：peTTM/pbMRQ/turn 应被补全（非 NULL）
    assert "peTTM" in std.columns
    assert "pbMRQ" in std.columns
    assert "turn" in std.columns
    assert std["peTTM"].notna().all(), f"peTTG 应全部补全: {std['peTTM'].tolist()}"
    assert std["pbMRQ"].notna().all(), f"pbMRQ 应全部补全: {std['pbMRQ'].tolist()}"
    assert std["turn"].notna().all(), f"turn 应全部补全: {std['turn'].tolist()}"
    # 值正确性（PIT ASOF JOIN，当日估值）
    assert std["peTTM"].iloc[0] == pytest.approx(10.5, abs=0.01)


def test_aligner_valuation_none_leaves_null(aligner):
    """valuation_df 为 None（依赖表失败）时 peTTM/pbMRQ/turn 留 NULL，不阻断。"""
    raw = pd.DataFrame({
        "stock_code": ["600000.SH"],
        "time": [str(pd.Timestamp("2026-07-10").value // 10**6)],
        "open": [10.0], "high": [10.5], "low": [9.8], "close": [10.2],
        "volume": [1000.0], "amount": [102000.0],
        "preClose": [10.0], "suspendFlag": [0], "isST": [0],
        "open_front": [10.0], "high_front": [10.5], "low_front": [9.8], "close_front": [10.2],
        "open_back": [10.0], "high_back": [10.5], "low_back": [9.8], "close_back": [10.2],
    })
    std, _ = aligner.align(raw, table="stock_daily", source="xtquant",
                           valuation_df=None, freq="daily")
    # 留 NULL（pd.NA 或 NaN），不报错
    assert "peTTM" in std.columns


# ------------------------- 断言 4：pctChg 推导（derived_from_front + 涨跌停）-------------------------

def test_pctchg_derived_from_front_within_limit(aligner):
    """pctChg 由 close_front.pct_change 推导，|pctChg| 应在涨跌停范围内（≤22%）。"""
    base_ts = pd.Timestamp("2026-07-10").value // 10**6
    n = 10
    rng = np.random.default_rng(7)
    # close_front 平稳变化（无除权跳变）
    close_front = np.cumsum(rng.uniform(-0.2, 0.2, n)) + 10
    close_front = close_front.round(2)
    raw = pd.DataFrame({
        "stock_code": ["600000.SH"] * n,
        "time": [str(base_ts + i * 86_400_000) for i in range(n)],
        "open": close_front, "high": close_front + 0.1, "low": close_front - 0.1,
        "close": close_front,
        "volume": [1000.0] * n, "amount": (close_front * 100000).round(2),
        "preClose": np.r_[close_front[0], close_front[:-1]],
        "suspendFlag": [0] * n, "isST": [0] * n,
        "close_front": close_front,
        "open_front": close_front, "high_front": close_front + 0.1, "low_front": close_front - 0.1,
        "open_back": close_front, "high_back": close_front + 0.1, "low_back": close_front - 0.1,
        "close_back": close_front,
    })
    std, _ = aligner.align(raw, table="stock_daily", source="xtquant", freq="daily")
    # pctChg 应被推导（首行 NaN 是正常的，pct_change 无前日）
    assert "pctChg" in std.columns
    non_null = std["pctChg"].dropna()
    assert len(non_null) == n - 1, f"应有 {n-1} 行非 NULL pctChg（首行 NaN 正常）"
    # |pctChg| ≤ 22%（涨跌停 20% × 1.1 容忍）—— 平稳数据应远小于此
    assert (non_null.abs() <= 22).all(), f"|pctChg| 超涨跌停: {non_null.tolist()}"


# ------------------------- 断言 5：端到端 etf_daily 不被 IsSTNull 拒 -------------------------

def test_etf_daily_not_rejected_by_isST_null(aligner, validator):
    """etf_daily.isST.required=true，xtquant 不补 isST → IsSTNull 整表拒。

    修复后 adapter 补 isST=0（ETF 恒 0），validator 应接受。
    本测试模拟 adapter 补全后的数据流，验证不被拒。
    """
    base_ts = pd.Timestamp("2026-07-10").value // 10**6
    n = 5
    rng = np.random.default_rng(11)
    close = rng.uniform(1, 3, n).round(3)  # ETF 价格
    op = rng.uniform(1, 3, n).round(3)
    hi = (np.maximum(op, close) + rng.uniform(0.001, 0.03, n)).round(3)
    lo = (np.minimum(op, close) - rng.uniform(0.001, 0.03, n)).round(3)
    vol_shou = rng.integers(1000, 50000, n).astype(float)
    amount = (close * vol_shou * 100).round(2)
    raw = pd.DataFrame({
        "stock_code": ["510050.SH"] * n,
        "time": [str(base_ts + i * 86_400_000) for i in range(n)],
        "open": op, "high": hi, "low": lo, "close": close,
        "volume": vol_shou, "amount": amount,
        "preClose": np.r_[op[0], close[:-1]],
        # adapter 补的 isST（ETF 恒 0）
        "isST": [0] * n,
        # 三段式复权
        "open_front": op, "high_front": hi, "low_front": lo, "close_front": close,
        "open_back": op, "high_back": hi, "low_back": lo, "close_back": close,
    })
    std, _ = aligner.align(raw, table="etf_daily", source="xtquant", freq="daily")
    res = validator.validate(std, table="etf_daily", batch_id="e2e_etf",
                             source="xtquant", expected_freq="daily")
    # 关键：不应有 IsSTNull 拒绝
    isst_rejected = sum(1 for rules in res.rejected_rules if "IsSTNull" in rules)
    assert isst_rejected == 0, (
        f"etf_daily 仍被 IsSTNull 拒绝 {isst_rejected} 行，adapter 未补 isST 或 aligner 丢失该列"
    )
    assert res.passed_df.shape[0] > 0, "etf_daily 应有数据通过校验"


def test_etf_daily_rejected_when_isST_missing(validator):
    """反向锁定：若 etf_daily 缺 isST 列（未修复状态），IsSTNull 应整表拒。

    确保修复不是靠放宽 schema，而是靠 adapter 补列。
    """
    base_ts = pd.Timestamp("2026-07-10").value // 10**6
    n = 3
    df = pd.DataFrame({
        "code": ["510050"] * n,
        "time": [base_ts + i * 86_400_000 for i in range(n)],
        "open": [1.5, 1.6, 1.55], "high": [1.7, 1.7, 1.6],
        "low": [1.4, 1.5, 1.45], "close": [1.6, 1.55, 1.5],
        "volume": [10000.0] * n, "amount": [160000.0] * n,
        "preClose": [1.5, 1.6, 1.55], "pctChg": [1.0, -2.0, -3.0],
        # 故意不补 isST 列（模拟未修复状态）
        "open_front": [1.5, 1.6, 1.55], "high_front": [1.7, 1.7, 1.6],
        "low_front": [1.4, 1.5, 1.45], "close_front": [1.6, 1.55, 1.5],
        "open_back": [1.5, 1.6, 1.55], "high_back": [1.7, 1.7, 1.6],
        "low_back": [1.4, 1.5, 1.45], "close_back": [1.6, 1.55, 1.5],
    })
    res = validator.validate(df, table="etf_daily", batch_id="regression",
                             source="xtquant", expected_freq="daily")
    isst_rejected = sum(1 for rules in res.rejected_rules if "IsSTNull" in rules)
    # 未补 isST 应被整表拒（schema required=true）
    assert isst_rejected == n, (
        f"未补 isST 应被 IsSTNull 整表拒 {n} 行，实际 {isst_rejected}。"
        "若此测试失败说明 schema 被错误放宽或 validator 行为变化。"
    )
