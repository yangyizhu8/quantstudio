"""分表型质量门禁回归测试（客户反馈包 2026-09-23 问题3，用户裁定选项1）。

语义边界（必须守住）：
  本项只调整「批次判负线」，**不改变任何行的判拒结果**——越界行仍由 validator
  照常 REJECT → 进隔离区；不因阈值放宽而入库。

阈值取值依据（实测 40 个 1 日窗，见
docs/evidence/mcp-pull-four-issues-acceptance-20260923.md）：
  etf_minutes 窗级 P50=4.38% / P99=5.05% / max=5.06%
  stock_minutes 窗级 P50=0.15% / P99=2.03% / max=2.03%
  ⇒ max(P99)×1.58 ≈ 8%（单一值覆盖两表；真实单位错时拒绝率 100%，阈值远低于它）
"""
from __future__ import annotations

from quantstudio.pipeline.daemon import _MINUTE_FREQS, ResidentCollector


def _gate(quality_gate=None):
    """绕过 __init__，仅注入 tasks_cfg 以测纯阈值逻辑。"""
    d = ResidentCollector.__new__(ResidentCollector)
    d.tasks_cfg = {"quality_gate": quality_gate or {}}
    return d


TOTAL = 100_000


def test_minute_freq_uses_calibrated_threshold():
    """分钟表：1.4985%（客户实测判负点）在新阈值下不再判负。"""
    g = _gate()
    rejected = int(TOTAL * 0.014985)          # 客户实测 etf_minutes 全窗拒绝率
    ok, rate, thr = g._failure_gate({}, rejected, TOTAL, source="mcp",
                                    is_reject=True, freq="1min")
    assert ok is True, (rate, thr)
    assert thr == 0.08, thr
    assert 0.0149 < rate < 0.0151, rate      # int() 截断，故给区间


def test_all_five_minute_freqs_covered():
    """五频段全部命中分钟阈值（防漏配某一频段）。"""
    g = _gate()
    for f in ("1min", "5min", "15min", "30min", "60min"):
        assert f in _MINUTE_FREQS, f
        _, _, thr = g._failure_gate({}, 1, 100, source="mcp", is_reject=True, freq=f)
        assert thr == 0.08, (f, thr)


def test_daily_freq_keeps_one_percent_unchanged():
    """日线保持 1%（逐位不变，回归钉）。"""
    g = _gate()
    for f in ("daily", None):
        _, _, thr = g._failure_gate({}, 1, 100, source="mcp", is_reject=True, freq=f)
        assert thr == 0.01, (f, thr)


def test_minute_threshold_still_catches_real_unit_error():
    """真单位错（手/股 ×100）时拒绝率≈100% ⇒ 仍必须判负（门禁不被架空）。"""
    g = _gate()
    ok, rate, thr = g._failure_gate({}, TOTAL, TOTAL, source="mcp",
                                    is_reject=True, freq="1min")
    assert ok is False, (rate, thr)
    assert rate == 1.0
    # 新阈值距 100% 仍有 12.5 倍余量
    assert thr * 12 < 1.0


def test_minute_threshold_configurable():
    """配置可覆写（回退护栏）。"""
    g = _gate({"max_reject_rate_minute": 0.03})
    _, _, thr = g._failure_gate({}, 1, 100, source="mcp", is_reject=True, freq="1min")
    assert thr == 0.03, thr


def test_fetch_failure_rate_not_relaxed_for_minutes():
    """拉取失败率（is_reject=False）仍 0.01% 严格 —— 本项不触碰。"""
    g = _gate()
    _, _, thr = g._failure_gate({}, 1, 100, source="mcp",
                                is_reject=False, freq="1min")
    assert thr == 0.0001, thr


def test_xtquant_minute_uses_max_of_both_overrides():
    """xtquant 源 + 分钟表：取两者较大者（既有 xtquant 放宽不被削弱）。"""
    g = _gate()
    _, _, thr = g._failure_gate({}, 1, 100, source="xtquant",
                                is_reject=True, freq="1min")
    assert thr == 0.08, thr


def test_task_level_override_still_respected():
    """task 级 max_failure_rate 仍参与 max()（既有语义不变）。"""
    g = _gate()
    _, _, thr = g._failure_gate({"max_failure_rate": 0.5}, 1, 100,
                                source="mcp", is_reject=True, freq="1min")
    assert thr == 0.5, thr


def test_threshold_change_does_not_alter_row_level_rejection(monkeypatch):
    """**核心守护**：阈值只影响批次判负线，不改变 validator 的行级判拒。

    构造与客户实测同形的行（amount/(close*vol) ≈ 8.97），断言：
      - 行仍被 UnitCheck 判拒（进隔离区）
      - 但批次门禁在新阈值下不再判负
    """
    import json
    from pathlib import Path

    import pandas as pd

    from quantstudio.pipeline.validator import PreIngestValidator

    root = Path(__file__).resolve().parent.parent
    schemas = json.loads(
        (root / "config" / "profiles" / "mcp_only" / "alignment_rules.json")
        .read_text(encoding="utf-8"))["schemas"]
    v = PreIngestValidator(schemas, quarantine=None)

    # 客户实测 bar（159327.SZ 2026-01-05 10:03）
    n = 100
    base_ms = pd.Timestamp("2026-01-05 10:00").value // 10**6
    df = pd.DataFrame({
        "code": ["159327"] * n,
        "time": [base_ms + i * 60_000 for i in range(n)],
        "freq": ["1min"] * n,
        "open": [0.214] * n, "high": [0.214] * n, "low": [0.213] * n,
        "close": [0.214] * n, "volume": [550300.0] * n,
        "amount": [1056648.8] * n,
        "suspendFlag": [0] * n, "dividend_type": ["all"] * n,
    })
    res = v.validate(df, "stock_minutes", "gate_test", "mcp", expected_freq="1min")
    rejected = len(res.rejected_rows)
    assert rejected == n, f"该异常行必须全部被拒（真阳性），实际 {rejected}"
    assert all("UnitCheck" in rs for rs in res.rejected_rules)

    # 门禁：同样 100% 拒绝率 + 分钟阈值 ⇒ 仍判负（真单位错场景）
    g = _gate()
    ok, rate, thr = g._failure_gate({}, rejected, len(df), source="mcp",
                                    is_reject=True, freq="1min")
    assert ok is False, (rate, thr)

    # 而客户实测比例（1.5%）在新阈值下不判负 —— 行级判拒不变、批次不再阻塞
    ok2, rate2, thr2 = g._failure_gate({}, 932382, 62_200_000, source="mcp",
                                       is_reject=True, freq="1min")
    assert ok2 is True, (rate2, thr2)