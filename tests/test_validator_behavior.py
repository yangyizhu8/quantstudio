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
# 客户反馈包 2026-09-23 问题3：UnitCheck 判据归一（方案 a）回归钉
# ---------------------------------------------------------------------------
# 机理：amount/volume 是不复权量，而 close 经 MCP 线1「qfq→raw 还原」后是 raw；
# 二者不同基准 ⇒ 原判据在还原表上恒 = adj_latest/adj_i，凡该比值 ∉ [0.5,2.0]
# 的行被系统性误拒（客户 etf_minutes 2026 全年 1.4985% = 932,382 行）。
# 归一：乘上适配器随行附带的 adj_i/adj_latest（列 _UNIT_CHECK_FACTOR_COL）。
# ===========================================================================

def _qfq_restored_minute_df(n=200, factor_ratio=3.0):
    """构造「云端 qfq → 客户端还原 raw」之后的分钟数据（三步保真）。

    factor_ratio = **还原乘数** adj_latest / adj_i（例如 adj_latest=3.0 / adj_i=1.0），
    即 mcp_adapter 随行附带的 __qs_unit_factor_ratio__ 语义。
    真实链条（实测：云端 qfq_close ≡ amount/volume，恒等比值 1.0）：
        ① 基表 close 取作**云端 qfq 价**（未复权、可成交口径）
        ② amount = qfq_close × volume      （金额/成交量在复权下不变）
        ③ raw_close = qfq_close × factor_ratio
           （客户端还原：raw = qfq × adj_latest/adj_i）
           价格谱系（OHLC）同步 × factor_ratio，保持 OHLC 自洽
    ⇒ 还原后 amount/(raw_close×volume) = 1/factor_ratio ∉ [0.5,2.0]（旧行为误拒），
      乘 factor_ratio 归一后 = 1.0（应放行）。
    """
    df = _base_minute_df(n)
    qfq_close = df["close"].astype(float)          # ① 云端 qfq 价
    df["amount"] = (qfq_close * df["volume"]).round(2)   # ② 金额按 qfq 价计
    for col in ("open", "high", "low", "close"):         # ③ 还原为 raw
        df[col] = (df[col].astype(float) * factor_ratio).round(3)
    df["close"] = (qfq_close * factor_ratio).round(3)    # 与 amount 严格同源
    if "pre_close" in df.columns:
        df["pre_close"] = (df["pre_close"].astype(float) * factor_ratio).round(3)
    return df, factor_ratio


def test_unit_check_false_positive_removed_with_factor_ratio(validator):
    """**假阳性消除**：行内带因子比 ⇒ 还原表合法行不再被误拒。"""
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df, fr = _qfq_restored_minute_df(200)
    # 未附因子比列 → 旧行为：全部被 UnitCheck 误拒
    res_old = validator.validate(df.copy(), "stock_minutes", "b", "mcp",
                                 expected_freq="1min")
    flat_old = {r for rs in _fingerprint(res_old)["rejected_rule_sets"] for r in rs}
    assert "UnitCheck" in flat_old, "无因子比列时应维持旧行为（回归钉）"

    # 附因子比列 → 归一后全部通过
    df[_UNIT_CHECK_FACTOR_COL] = fr
    res_new = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    fp_new = _fingerprint(res_new)
    assert fp_new["rejected"] == 0, fp_new
    assert fp_new["passed"] == 200, fp_new


def test_unit_check_true_positive_still_caught_with_factor_ratio(validator):
    """**真阳性保留**：单位真错（手 vs 股 ×100）+ 正确因子比 ⇒ 仍被拦。

    这是方案 a 相对「跳过检查」的关键差异——归一不削弱真阳性能力。
    """
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df, fr = _qfq_restored_minute_df(50)
    df[_UNIT_CHECK_FACTOR_COL] = fr
    # 注入真实单位错：volume 少 ×100（= 手单位未换算）⇒ 归一后比值仍 ≈100
    df["volume"] = df["volume"] / 100.0
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    fp = _fingerprint(res)
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "UnitCheck" in flat, fp
    assert fp["passed"] == 0, fp


def test_unit_check_factor_ratio_out_of_range_still_rejects(validator):
    """因子比本身无法救回的真异常（金额错 4 倍）仍被拦。"""
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df, fr = _qfq_restored_minute_df(20)
    df[_UNIT_CHECK_FACTOR_COL] = fr
    df.loc[3, "amount"] = df.loc[3, "amount"] * 4.0   # 归一后比值 ≈4 → 越界
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    fp = _fingerprint(res)
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "UnitCheck" in flat, fp
    assert fp["rejected"] == 1, fp


def test_unit_check_no_factor_column_identical_to_legacy(validator):
    """**无因子比列 ⇒ 逐行等同旧行为**（非 MCP 还原表零影响，回归钉）。"""
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df = _base_minute_df(100)
    assert _UNIT_CHECK_FACTOR_COL not in df.columns
    res = validator.validate(df, "stock_minutes", "b", "xtquant", expected_freq="1min")
    fp = _fingerprint(res)
    assert fp["passed"] == 100 and fp["rejected"] == 0, fp


def test_unit_check_factor_ratio_column_passthrough_documented(validator):
    """归一辅助列经 validator 原样传递（不静默改写列集）——锁定当前契约。

    该列仅用于 UnitCheck 归一；写库侧只落 schema 声明列（writers 按 schema 取列），
    故不会污染 DuckDB。若未来改为在 validator 内剔除，请同步更新本断言与
    `_restore_to_raw` 的 docstring。
    """
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df, fr = _qfq_restored_minute_df(30)
    df[_UNIT_CHECK_FACTOR_COL] = fr
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    assert len(res.passed_df) == 30
    assert _UNIT_CHECK_FACTOR_COL in res.passed_df.columns


def test_unit_check_nonfinite_factor_ratio_degrades_to_legacy(validator):
    """因子比非有限/非正 ⇒ 该行按旧行为判定（不静默放过）。"""
    from quantstudio.pipeline.validator import _UNIT_CHECK_FACTOR_COL
    df, fr = _qfq_restored_minute_df(20)
    df[_UNIT_CHECK_FACTOR_COL] = np.nan      # 归一信息缺失
    res = validator.validate(df, "stock_minutes", "b", "mcp", expected_freq="1min")
    fp = _fingerprint(res)
    # 旧行为下这 20 行比值 = 1/3 ∉ [0.5,2.0] ⇒ 全部被 UnitCheck 拒
    assert fp["rejected"] == 20, fp
    flat = {r for rs in fp["rejected_rule_sets"] for r in rs}
    assert "UnitCheck" in flat, fp