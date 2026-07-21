"""validator 行为锁定测试：重构向量化前后语义必须一致。

本测试构造覆盖每条校验规则的脏数据样本，捕获 validator 的输出指纹
(passed/rejected/fixed/warned + 每行命中规则集)，作为回归基线。

用途：当 validator 重构（如逐行 → 矢量化）时，跑此测试确认行为不变。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantstudio.pipeline.validator import PreIngestValidator


HERE = Path(__file__).resolve().parent.parent
SCHEMAS = json.loads((HERE / "config" / "alignment_rules.json").read_text(encoding="utf-8"))["schemas"]


@pytest.fixture
def validator():
    """无 quarantine 的 validator（测试不写库）。"""
    return PreIngestValidator(SCHEMAS, quarantine=None)


def _base_minute_df(n=200):
    """构造干净的 stock_minutes 数据（全部应通过）。

    OHLC 严格自洽：low ≤ min(open,close) ≤ max(open,close) ≤ high。
    amount/(close*vol) 比值 ≈ 1.0（落在 UnitCheck [0.5, 2.0] 内）。
    """
    base = pd.Timestamp("2026-01-04 09:31").value // 10**6
    times = [base + (i % 240) * 60_000 for i in range(n)]
    rng = np.random.default_rng(42)
    # 先生成 open/close，再由它们推导 high/low，保证 OHLC 自洽
    op = rng.uniform(9, 11, n).round(2)
    close = rng.uniform(9, 11, n).round(2)
    hi = np.maximum(op, close) + rng.uniform(0.01, 0.3, n).round(2)
    lo = np.minimum(op, close) - rng.uniform(0.01, 0.3, n).round(2)
    vol = rng.integers(100, 10000, n).astype(float)
    # amount = close * vol * (1 + 小幅波动)，保证比值 ≈ 1
    amount = (close * vol * rng.uniform(0.95, 1.05, n)).round(2)
    return pd.DataFrame({
        "code": ["600000"] * n, "time": times, "freq": ["1min"] * n,
        "open": op, "high": hi, "low": lo, "close": close,
        "volume": vol, "amount": amount,
        "suspendFlag": [0] * n,
        "dividend_type": ["all"] * n,
    })


def _fingerprint(res):
    """提取 validator 结果的稳定指纹（与行顺序无关）。"""
    passed = len(res.passed_df)
    rejected = len(res.rejected_rows)
    # 把每行命中的规则集排序后聚合成 multiset，顺序无关
    rule_sets = sorted(tuple(sorted(rs)) for rs in res.rejected_rules)
    return {
        "passed": passed,
        "rejected": rejected,
        "fixed": res.fixed_count,
        "warned": res.warned_count,
        "rejected_rule_sets": rule_sets,
    }


# ------------------------- 行为锁定用例 -------------------------

def test_clean_minute_data_all_pass(validator):
    """干净分钟数据应全部通过。"""
    df = _base_minute_df(200)
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["passed"] == 200, fp
    assert fp["rejected"] == 0, fp


def test_bad_code_rejected(validator):
    """CodeFormat 规则：非法代码格式应被拒。"""
    df = _base_minute_df(10)
    df.loc[3, "code"] = "BAD_CODE"
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] == 1, fp
    assert ("CodeFormat",) in fp["rejected_rule_sets"], fp


def test_ohlc_violation_rejected(validator):
    """OHLCLogic 规则：high < close 应被拒。
    基线行为：high 违规 + low 违规可能各 reject 一次，规则名在 hit_rules 内重复。"""
    df = _base_minute_df(10)
    df.loc[5, "high"] = df.loc[5, "close"] - 1.0  # high < close
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] == 1, fp
    # 该行命中规则集合应包含 OHLCLogic（可能出现多次）
    assert any("OHLCLogic" in rs for rs in fp["rejected_rule_sets"]), fp


def test_price_nonpositive_rejected(validator):
    """close <= 0 应被拒。
    基线行为：schema close 有 gt:0，走 PositiveNumeric 规则（规则 11），
    而非 PricePositive（规则 4，仅对 schema 显式声明 gt:0 的列触发，二者实为同一路径）。"""
    df = _base_minute_df(10)
    df.loc[2, "close"] = 0
    df.loc[2, "high"] = 0.1  # 保持 OHLC 合理，避免被 OHLCLogic 命中混淆
    df.loc[2, "low"] = -0.1
    df.loc[2, "open"] = 0.05
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] >= 1, fp
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    # close=0 至少命中 PositiveNumeric 或 PricePositive 之一
    assert flat & {"PositiveNumeric", "PricePositive"}, fp


def test_duplicate_key_fixed(validator):
    """DuplicateKey 规则：主键重复应去重(fixed)，不进 rejected。"""
    df = _base_minute_df(10)
    # 复制第 2 行（同 code/time/freq）追加到末尾
    df = pd.concat([df, df.iloc[[2]]], ignore_index=True)
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["fixed"] == 1, f"应去重 1 行，实际 {fp['fixed']}"
    assert fp["passed"] == 10, fp


def test_freq_mismatch_rejected(validator):
    """FrequencyMismatch 规则：freq 与 expected_freq 不符应被拒。
    基线行为：改 freq 但时间戳仍是 1min 网格 → 同时命中 FrequencyMismatch + FrequencyGrid。"""
    df = _base_minute_df(10)
    df.loc[1, "freq"] = "5min"
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] == 1, fp
    assert any("FrequencyMismatch" in rs for rs in fp["rejected_rule_sets"]), fp


def test_freq_grid_rejected(validator):
    """FrequencyGrid 规则：时间戳非 freq 整数倍应被拒。"""
    df = _base_minute_df(10)
    # 偏移 30 秒，使其不是 60 秒整数倍
    df.loc[7, "time"] = int(df.loc[7, "time"]) + 30_000
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] == 1, fp
    assert ("FrequencyGrid",) in fp["rejected_rule_sets"], fp


def test_unit_check_rejected(validator):
    """UnitCheck 规则：amount/(close*vol) 比值越界应被拒。"""
    df = _base_minute_df(10)
    # 构造 amount 远小于 close*vol（比值 << 0.5）
    df.loc[4, "amount"] = 1.0
    df.loc[4, "close"] = 10.0
    df.loc[4, "volume"] = 10000.0
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "UnitCheck" in flat, fp


def test_required_value_null_rejected(validator):
    """RequiredValueNull 规则：必填字段 NaN 应被拒。"""
    df = _base_minute_df(10)
    df.loc[0, "code"] = None
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "RequiredValueNull" in flat or "CodeFormat" in flat, fp


def test_perf_minute_30k(validator):
    """性能回归保护：31571 行分钟数据校验应在 2 秒内完成。

    重构前基线（逐行循环）：~50s（有复权列） / ~6s（无复权列）。
    重构后（矢量化 boolean mask）：~0.05s。
    阈值 2s 给机器波动留 40x 余量。
    """
    import time
    df = _base_minute_df(31571)
    t0 = time.time()
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    dt = time.time() - t0
    assert dt < 2.0, f"validator 31571 行耗时 {dt:.2f}s，超过 2s 阈值（向量化回归）"
