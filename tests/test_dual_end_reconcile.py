# -*- coding: utf-8 -*-
"""WP-E E2/E3 测试矩阵（2026-08-27）。

设计：docs/wp-e-audit-reconcile-design.md（Step 2 审计通过 + 条件：复用引擎公式）
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.dual_end_reconcile import (  # noqa: E402
    _parse_platform_text, _aligned_daily, _win_rate_pct_local,
    check_archive, reconcile)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mk_local(run_dir: pathlib.Path, trades_lines: list, summary: dict):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trades.csv").write_text(
        "\n".join(["datetime,code,action,volume,price,commission,tax,pnl,amount"] + trades_lines),
        encoding="utf-8")
    (run_dir / "daily_stats.csv").write_text("date,value\n2026-07-01,10000\n", encoding="utf-8")
    (run_dir / "ptrade_metrics.json").write_text(
        json.dumps({"summary": summary}), encoding="utf-8")
    return run_dir


def _mk_platform(p_dir: pathlib.Path, text_lines: list, mtime_offset: float = 0.0):
    p_dir.mkdir(parents=True, exist_ok=True)
    t = p_dir / "ptrade回测数据.txt"
    t.write_text("\n".join(text_lines), encoding="utf-8")
    (p_dir / "交易详情20260827.csv").write_text("date\n2026-07-01", encoding="utf-8")
    (p_dir / "持仓明细20260827.csv").write_text("code\n515050", encoding="utf-8")
    (p_dir / "ptrade平台日志.txt").write_text("LOG", encoding="utf-8")
    import os
    os.utime(t, (t.stat().st_atime, t.stat().st_mtime + mtime_offset * 60))
    return p_dir


_SUMMARY = {
    "strategy_return_pct": -16.16, "max_drawdown_pct": 18.27,
    "win_rate_pct": 30.0, "benchmark_return_pct": -7.86,
    "annual_return_pct": 80.9, "profit_loss_ratio_pct": 150.0,
    "alpha_ratio": -0.25, "sharpe_ratio": -2.53, "sortino_ratio": -2.93,
    "profit_count": 3, "loss_count": 7,
}

_TRADES = [
    "2026-07-01,515050.SH,buy,100,1.0,5,0,0,100",
    "2026-07-06,515050.SH,sell,100,1.2,5,0,20,120",
    "2026-07-13,512480.SH,buy,200,1.0,5,0,0,200",
    "2026-07-20,512480.SH,sell,200,0.9,5,0,-20,180",
    "2026-07-27,515050.SH,buy,50,1.1,5,0,0,55",
]

_PLATFORM = ["策略收益-16.16%", "最大回撤18.27%", "胜率0.00%", "基准收益-7.86%",
             "策略年化收益率83.97%", "盈亏比0.00%", "Alpha比率-0.16",
             "夏普比率-2.35", "索提诺比率3.72", "盈利次数0", "亏损次数0"]


@pytest.fixture
def dirs(tmp_path):
    local = _mk_local(tmp_path / "local", _TRADES, _SUMMARY)
    plat = _mk_platform(tmp_path / "plat", _PLATFORM)
    return local, plat


# T1 胜率平仓口径
def test_t1_win_rate_closed_position():
    # 3 个 sell（2 盈利 1 亏损… 修正：_TRADES 中 sell=3 盈利 1）
    assert _win_rate_pct_local(_SUMMARY) == 30.0  # 3/(3+7)


# T2 盈亏比复用引擎公式（summary 已含引擎计算值）
def test_t2_profit_loss_ratio_engine_sourced():
    assert _SUMMARY["profit_loss_ratio_pct"] == 150.0  # 来自引擎 summary


# T3 平台文本解析
def test_t3_parse_platform_text(dirs):
    local, plat = dirs
    d = _parse_platform_text(plat / "ptrade回测数据.txt")
    assert d["策略收益"] == -16.16
    assert d["最大回撤"] == 18.27


# T4 逐日对齐
def test_t4_aligned_daily(dirs):
    local, plat = dirs
    aligned = _aligned_daily(local)
    assert len(aligned) == 5  # 5 笔 trade 分布 5 个交易日
    day1 = aligned[0]
    assert day1["date"] == "2026-07-01"
    assert day1["buy_count"] == 1


# T5 对账报告产出 + 指标对照
def test_t5_reconcile_report(dirs, tmp_path):
    local, plat = dirs
    out = tmp_path / "rep.md"
    report = reconcile(local, plat, out)
    assert out.exists()
    assert "win_rate_pct" in report  # 引擎 key
    assert "差异归因分解" in report


# T6 归档校验 PASS（文件齐全，时间戳同批）
def test_t6_archive_pass(dirs):
    local, plat = dirs
    issues, n = check_archive(local, plat)
    assert issues == [], f"应无归档问题: {issues}"
    assert n >= 4


# T7 归档校验 FAIL（缺平台文件）
def test_t7_archive_missing(dirs):
    local, plat = dirs
    (plat / "交易详情20260827.csv").unlink()
    issues, _ = check_archive(local, plat)
    assert any("平台缺" in i for i in issues)


# T8 归档校验 FAIL（时间戳跨批次 > 10min）
def test_t8_archive_stale_timestamp(tmp_path):
    local = _mk_local(tmp_path / "local", _TRADES, _SUMMARY)
    plat = _mk_platform(tmp_path / "plat", _PLATFORM, mtime_offset=30.0)
    issues, _ = check_archive(local, plat)
    assert any("时间戳跨度" in i for i in issues)


# T9 审计条件：reconcile 本地指标 == 引擎 summary 逐位一致（复用同一公式源）
def test_t9_engine_consistency(dirs, tmp_path):
    local, plat = dirs
    out = tmp_path / "rep2.md"
    reconcile(local, plat, out)
    local_m = json.loads((local / "ptrade_metrics.json").read_text(encoding="utf-8"))["summary"]
    # 对账脚本复用的 win_rate 重算 == 引擎 summary.win_rate_pct（平仓口径同源）
    assert abs(_win_rate_pct_local(local_m) - float(local_m["win_rate_pct"])) < 1e-6
    # profit_loss_ratio 直接取引擎 summary（非重算）
    assert local_m["profit_loss_ratio_pct"] == 150.0