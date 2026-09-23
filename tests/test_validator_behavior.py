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
# 2026-08-27 legacy profile 废弃后，权威 alignment_rules 迁至 mcp_only profile
SCHEMAS = json.loads((HERE / "config" / "profiles" / "mcp_only" / "alignment_rules.json").read_text(encoding="utf-8"))["schemas"]


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
    flats = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "OHLCLogic" in flats, fp


def test_price_nonpositive_rejected(validator):
    """RangeCheck（gt:0）：close ≤ 0 应被拒。"""
    df = _base_minute_df(10)
    df.loc[2, "close"] = -1.0
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] >= 1, fp


def test_duplicate_key_fixed(validator):
    """DuplicateKey 规则：重复主键应被 FIX（去重保留首行）。"""
    df = _base_minute_df(10)
    df.loc[9] = df.loc[8]  # 制造完全重复行
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    assert res.fixed_count >= 1, res
    assert len(res.passed_df) == 9, len(res.passed_df)


def test_freq_enum_rejected(validator):
    """EnumCheck：freq 不在白名单应被拒。"""
    df = _base_minute_df(10)
    df.loc[7, "freq"] = "7min"
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] >= 1, fp


def test_freq_grid_rejected(validator):
    """FrequencyGrid：时间戳非 freq 整数倍应被拒。"""
    df = _base_minute_df(10)
    df.loc[6, "time"] = df.loc[6, "time"] + 30_000  # 偏移 30s，非 1min 倍数
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["rejected"] >= 1, fp


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


# ===========================================================================
# 客户反馈包 2026-09-23 问题3：**「朴素归一」路径证伪**回归钉
# ---------------------------------------------------------------------------
# 结论（2026-09-23 云端只读实测 + 步骤4 验收反证；T3 实现已整体回退）：
#   云端 etf_minutes 的 close **本身就是可成交价口径**（与 amount/vol 同基准），
#   不需要任何复权归一。UnitCheck 的拒绝是真阳性——数据/因子本身矛盾。
#
# 实测证据（三项独立取样，见 docs/mcp-pull-four-issues-fix-design.md §八）：
#   ① close/(amount/vol) ≈ 1 的行占比：2025-06 84.40% / 2026-01 92.03% /
#      2026-09 99.94%（close 已是可成交价）
#   ② 单码日内实证（159388.SZ 2025-06-03）：close/close_2159 = 0.376/0.9404
#      = 0.3998 = adj_i/adj_latest(1/2.5011) —— 同日内恒定比例，证明 close 是
#      「已复权到最新锚」的可成交价，而非需再乘倍数的 qfq
#   ③ 归一实现前后的交叉表（旧拒 → 新拒）：2025-06 4575→7173 ；
#      2026-01 6789→7527 ；2026-09 0→0。A 类「归一消除的误拒」=3427，
#      D 类「归一后新增拒」=6763 ⇒ **净增拒**，修复方向被证伪，实现整体回退。
#
# 本测试的作用：把该结论固化为可复现判据，防止后人再次以相同思路「修」UnitCheck。
# ===========================================================================

# 云端实测：2026-01-05 窗，159327.SZ 一分钟 bar（trade_time 10:03:00）
#   open/high/low/close = 0.213/0.214/0.213/0.214，vol = 550300，
#   amount = 1056648.8，adj_factor = 1.0
# 该行被 UnitCheck 判拒，属**真阳性**：amount/(close×vol) ≈ 8.97，
# 与「手/股 ×100」「千元/元 ×1000」等已知单位错配倍率均不吻合。
_CLOUD_MINUTE_BAR = {
    "close": 0.214, "volume": 550300.0, "amount": 1056648.8,
    "adj_factor": 1.0, "adj_latest": 3.0,
}


def test_unitchk_rejection_on_cloud_bar_is_true_positive_not_anchor_artifact(validator):
    """该云端 bar 的 UnitCheck 拒绝在「归一」前后都成立 ⇒ 归一救不了它。"""
    b = _CLOUD_MINUTE_BAR
    base_ratio = b["amount"] / (b["close"] * b["volume"])
    normalized = base_ratio * (b["adj_latest"] / b["adj_factor"])
    assert base_ratio > 2.0, f"实测 base ratio={base_ratio:.4f} 应越界"
    assert normalized > 2.0, (
        f"归一后 ratio={normalized:.4f} 仍越界 —— 归一无法消除该拒绝")

    df = _base_minute_df(3)
    df.loc[1, "close"] = b["close"]
    df.loc[1, "volume"] = b["volume"]
    df.loc[1, "amount"] = b["amount"]
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    flat = {r for rs in res.rejected_rules for r in rs}
    assert "UnitCheck" in flat, "该行必须被判拒（真阳性守护）"


def test_unitchk_must_not_be_normalized_by_adjustment_factor(validator):
    """守护：UnitCheck 判据**不得**乘复权因子（close 已是可成交价口径）。

    实测量：close/(amount/vol) ≈ 1 的行占比 84.40%~99.94%；对绝大多数行乘因子
    会把 ratio 推离 1.0（净增误拒 6763 行 vs 消除 3427 行）。
    """
    df = _base_minute_df(50)
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    assert len(res.rejected_rows) == 0, "干净数据（close 与 amount/vol 同基准）必须通过"

    # adj_factor 列存在本身不得改变 UnitCheck 判定（判据与复权无关）
    df2 = df.copy()
    df2["adj_factor"] = 3.0
    res2 = validator.validate(df2, "stock_minutes", "b", "mcp", expected_freq="1min")
    assert len(res2.rejected_rows) == 0, (
        "存在 adj_factor 列不得改变 UnitCheck 判定")

