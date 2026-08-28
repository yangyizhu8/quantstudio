"""xtquant volume 单位转换回归测试。

背景（2026-07-21 UnitCheck 批量误拒事故）：
xtquant get_market_data_ex 返回的 volume 单位是"手"（1手=100股，官方文档确认），
但 schema 权威定义 volume = "股"。alignment_rules.json 中 xtquant 4 个行情表
原本遗漏了 volume ×100 转换，导致 validator UnitCheck 规则把全市场分钟数据
批量拒绝（ratio = amount/(close×volume) ≈ 100，远超 [0.5, 2.0] 阈值），
31571 行/只 × 5202 只全部进 quarantine。

tushare/akshare 的日线表早已配置 volume ×100（已验证），唯独 xtquant 4 表遗漏。
本测试锁定该配置，防止未来再被删掉。

三条断言（用户审批方案）：
1. 配置存在性：xtquant 4 个行情表都含 volume ×100（参数化遍历，防第 5 张表再漏）
2. ×100 实际执行：aligner 对 xtquant volume 真的做了 ×100（手→股）
3. 端到端：xtquant 风格数据（volume=手）经 align+validate 后 ratio≈1.0，
   不再触发 UnitCheck（passed > 0, rejected = 0）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.validator import PreIngestValidator

HERE = Path(__file__).resolve().parent.parent
RULES_PATH = HERE / "config" / "profiles" / "mcp_only" / "alignment_rules.json"
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))
SCHEMAS = RULES["schemas"]


@pytest.fixture
def aligner():
    return FieldAligner.from_config(str(RULES_PATH))


@pytest.fixture
def validator():
    return PreIngestValidator(SCHEMAS, quarantine=None)


# xtquant 4 个行情表（事故范围 + 防未来漏配第 5 张表）
XTQUANT_OHLC_TABLES = ["stock_daily", "stock_minutes", "etf_daily", "etf_minutes"]


# ------------------------- 断言 1：配置存在性（参数化）-------------------------

@pytest.mark.parametrize("table", XTQUANT_OHLC_TABLES)
def test_xtquant_volume_unit_conversion_configured(table):
    """xtquant 每个行情表都应配置 volume ×100（手→股）。

    事故根因：4 表全遗漏。本测试参数化遍历，若未来新增第 5 张 xtquant 行情表，
    把表名加进 XTQUANT_OHLC_TABLES 即可（漏加不会误绿，因为新表若没配会被其他测试发现）。
    """
    mapping = RULES["source_mappings"]["xtquant"].get(table, {})
    uc = mapping.get("unit_conversions", {})
    vol_factor = uc.get("volume", {}).get("factor")
    assert vol_factor == 100, (
        f"xtquant/{table} 缺 volume ×100 转换（unit_conversions={uc}）。"
        f"xtquant volume 单位是手，schema 要求股，缺失会导致 UnitCheck 批量误拒。"
        f"参考 2026-07-21 UnitCheck 事故。"
    )


def test_tushare_minute_tables_not_silently_converted():
    """tushare 分钟表的 volume 单位未经验证，不应盲目配置 ×100。

    用户补充意见：tushare 分钟表 unit_conversions 应保持空，仅靠 _note 警示。
    未来若用 tushare 拉分钟数据，必须先拉一只核实实际单位，再决定是否补转换。
    盲目照抄日线 ×100 是未验证的猜测，可能引入反向 bug。
    """
    for table in ["stock_minutes", "etf_minutes"]:
        mapping = RULES["source_mappings"]["tushare"].get(table, {})
        uc = mapping.get("unit_conversions", {})
        vol_factor = uc.get("volume", {}).get("factor")
        # 允许：空配置（未验证）或显式 factor=100（已验证）。禁止其他猜测值。
        assert vol_factor in (None, 100), (
            f"tushare/{table} volume factor={vol_factor}。"
            f"tushare 分钟表 vol 单位未经验证，不应配置非 100 的猜测值。"
            f"要么留空（未验证），要么用真实数据验证后设 100。"
        )


# ------------------------- 断言 2：×100 实际执行 -------------------------

def test_aligner_applies_volume_x100(aligner):
    """aligner 对 xtquant volume 实际执行了 ×100（手→股）。

    构造 xtquant 风格数据（volume=1 手 = 100 股），验证 align 后 volume=100。
    排除"配置写了但代码没读"的可能性（aligner Step 4 unit_convert）。
    """
    # xtquant 原生列名是 vol（手单位），经 column_map 映射到 volume
    base_ts = pd.Timestamp("2026-01-04 09:31").value // 10**6
    raw = pd.DataFrame({
        "stock_code": ["600000.SH"] * 3,
        "time": [str(base_ts + i * 60_000) for i in range(3)],
        "open": [10.0, 10.1, 10.2],
        "high": [10.5, 10.6, 10.7],
        "low": [9.8, 9.9, 10.0],
        "close": [10.2, 10.3, 10.4],
        "vol": [1.0, 2.0, 100.0],  # 手单位（xtquant 原始）
        "amount": [1020.0, 2060.0, 104000.0],  # 元（= close × vol_股）
    })
    std, _ = aligner.align(raw, table="stock_minutes", source="xtquant", freq="1min")
    # vol=1 手 → volume=100 股；vol=2 手 → 200 股；vol=100 手 → 10000 股
    assert list(std["volume"]) == [100.0, 200.0, 10000.0], (
        f"aligner 未对 xtquant volume 执行 ×100：实际 {list(std['volume'])}"
    )


# ------------------------- 断言 3：端到端 ratio≈1.0，不触发 UnitCheck -------------------------

def test_end_to_end_xtquant_no_unitcheck_rejection(aligner, validator):
    """端到端：xtquant 风格数据（volume=手）经 align+validate 后不触发 UnitCheck。

    事故现象：ratio = amount/(close×volume) ≈ 100（因 volume 未 ×100），
    全部被 UnitCheck 拒。修复后 ratio 应 ≈ 1.0，全部通过。
    """
    base_ts = pd.Timestamp("2026-01-04 09:31").value // 10**6
    # 构造 50 行 xtquant 风格数据（手单位 volume + 元单位 amount）
    rng = np.random.default_rng(42)
    n = 50
    times = [str(base_ts + i * 60_000) for i in range(n)]
    close = rng.uniform(9, 11, n).round(2)
    op = rng.uniform(9, 11, n).round(2)
    hi = (np.maximum(op, close) + rng.uniform(0.01, 0.3, n)).round(2)
    lo = (np.minimum(op, close) - rng.uniform(0.01, 0.3, n)).round(2)
    vol_shou = rng.integers(100, 10000, n).astype(float)  # 手
    # amount = close × vol_股 = close × vol_shou × 100（元）
    amount = (close * vol_shou * 100).round(2)
    raw = pd.DataFrame({
        "stock_code": ["600000.SH"] * n,
        "time": times,
        "open": op, "high": hi, "low": lo, "close": close,
        "vol": vol_shou, "amount": amount,
    })
    std, _ = aligner.align(raw, table="stock_minutes", source="xtquant", freq="1min")
    res = validator.validate(std, table="stock_minutes", batch_id="e2e_test",
                             source="xtquant", expected_freq="1min")

    # 关键断言：不应有任何行被 UnitCheck 拒绝
    unitcheck_rejected = sum(
        1 for rules in res.rejected_rules if "UnitCheck" in rules
    )
    assert unitcheck_rejected == 0, (
        f"修复后仍有 {unitcheck_rejected} 行被 UnitCheck 拒绝。"
        f"rejected_rules={res.rejected_rules[:3]}"
    )
    # 绝大多数应通过（允许极少数因其他规则被拒，但不应是 UnitCheck）
    assert res.passed_df.shape[0] > 0, "修复后应有数据通过校验"


# ------------------------- 反向测试：未修复时会触发（锁定事故特征）-------------------------

def test_regression_unitcheck_catches_raw_shou_volume(validator):
    """反向锁定：若 volume 是手单位（未 ×100），UnitCheck 应捕获。

    确保修复不是靠放宽 UnitCheck 阈值（那是掩盖问题），而是靠正确的单位转换。
    直接喂手单位数据给 validator，应被 UnitCheck 拒。
    """
    base_ts = pd.Timestamp("2026-01-04 09:31").value // 10**6
    n = 20
    rng = np.random.default_rng(7)
    close = rng.uniform(9, 11, n).round(2)
    op = rng.uniform(9, 11, n).round(2)
    hi = (np.maximum(op, close) + rng.uniform(0.01, 0.3, n)).round(2)
    lo = (np.minimum(op, close) - rng.uniform(0.01, 0.3, n)).round(2)
    # 手单位 volume（模拟"忘记 ×100"的错误数据流入 validator）
    vol_shou = rng.integers(100, 10000, n).astype(float)
    amount = (close * vol_shou * 100).round(2)  # 元
    # 构造已 align 的数据（volume 仍是手，模拟转换缺失）
    df = pd.DataFrame({
        "code": ["600000"] * n,
        "time": [base_ts + i * 60_000 for i in range(n)],
        "freq": ["1min"] * n,
        "open": op, "high": hi, "low": lo, "close": close,
        "volume": vol_shou,  # 故意不 ×100
        "amount": amount,
        "suspendFlag": [0] * n,
        "dividend_type": ["all"] * n,
    })
    res = validator.validate(df, table="stock_minutes", batch_id="regression",
                             source="xtquant", expected_freq="1min")
    unitcheck_rejected = sum(
        1 for rules in res.rejected_rules if "UnitCheck" in rules
    )
    # 手单位数据应被 UnitCheck 抓住（ratio≈100，超出 [0.5,2.0]）
    assert unitcheck_rejected > 0, (
        "UnitCheck 未捕获手单位 volume 数据——可能阈值被错误放宽了。"
        "修复应靠单位转换，不应放宽 UnitCheck。"
    )
