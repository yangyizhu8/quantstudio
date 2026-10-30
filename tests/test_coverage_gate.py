# -*- coding: utf-8 -*-
"""tests/test_coverage_gate.py — 回测前分钟数据覆盖闸专项测试（2026-10-30 六步流水线 ③）。

合成 fixture（tmp_path 临时 DuckDB，只读检查，不碰任何真实库/主库/daemon）覆盖：
  三型   ：①整日缺（BLOCK 默认 / STRICT=0 降 WARN）②低覆盖（WARN + 阈值边界 + 设 0 关闭）
           ③副本陈旧（主库达标⇒WARN 列日；主库不可读⇒跳过不误报；所用库即主库⇒不适用）
  三开关 ：QS_MINUTE_COVERAGE_STRICT / MIN_RATIO / SKIP（跳过留痕 stdout + 结果目录可查）
  日键一致性专项（②审条件③）：
           - 闸交易日基准 == 引擎来源（SELECT DISTINCT time FROM stock_daily 经
             Asia/Shanghai 换算，duckdb_provider.get_trade_days 同式）；
           - CST 日键 (time+28_800_000)//86_400_000 与 time_axis.day_start_ms 恒等；
           - UTC 误归因反例：00:01 CST bar（UTC 前一日 16:01）不得归到前一 CST 日。
  回测接线：_precheck_minute_coverage BLOCK⇒RuntimeError；跳过留痕落结果目录。
  CLI    ：scripts/check_minute_coverage.py 复用同一实现，退出码 0(PASS/WARN)/2(BLOCK)。

性能实测回填（②审条件①）：拟预算 60s/窗口是**拟值**；2026-10-30 验收轮对真实副本
（agent_workspace/r1_live_copy_20261011 或 r1_copy_b2_20261020，窗口 2026-01-05~
2026-08-13）的实测耗时与逐日读数以 docs/evidence/ 验收件为准，禁止把拟值当实测。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from quantstudio.backtest.libs.coverage_gate import (  # noqa: E402
    CST_OFFSET_MS, DAY_MS, check_minute_coverage, date_to_dkey, dkey_to_date,
    dkey_to_ms_range, format_summary,
)
from quantstudio.backtest.providers.time_axis import (  # noqa: E402
    day_start_ms, start_ms as tz_start_ms,
)

BJ = timezone(timedelta(hours=8))

# 窗口：2026-01-05（周一）~ 2026-01-09（周五），5 个交易日
W_START, W_END = "2026-01-05", "2026-01-09"
DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
CODES = [f"60000{i}" for i in range(10)]          # 10 只合成标的
MINUTE_TIMES = [(9, 31), (10, 30), (14, 59)]      # 每日 3 个分钟时刻（足够 distinct code）


def _ms(day: str, hh: int = 0, mm: int = 0) -> int:
    d = datetime.fromisoformat(day).replace(tzinfo=BJ)
    return int(d.replace(hour=hh, minute=mm).timestamp() * 1000)


def _calendar_rows(start_day: str, end_day: str):
    """工作日开市、周末闭市的合成日历（cal_date = CST 零点 ms，正式 DDL 口径）。"""
    d = datetime.fromisoformat(start_day).date()
    last = datetime.fromisoformat(end_day).date()
    rows = []
    while d <= last:
        rows.append((_ms(d.isoformat()), d.weekday() < 5))
        d += timedelta(days=1)
    return rows


def _make_db(path, *, minutes=None, dailies=None, calendar=None, with_minutes_table=True,
             with_calendar_table=True):
    """合成 DuckDB。minutes=[(day, code, hh, mm)]；dailies=[(day, code)]（time=CST 零点）；
    calendar=[(cal_date_ms, is_open)]。None = 默认全量健康数据；[] = 空表。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    if with_minutes_table:
        con.execute("CREATE TABLE stock_minutes (code VARCHAR, time BIGINT, "
                    "open DOUBLE, close DOUBLE)")
    con.execute("CREATE TABLE stock_daily (code VARCHAR, time BIGINT, close DOUBLE)")
    if with_calendar_table:
        con.execute("CREATE TABLE trade_calendar (cal_date BIGINT NOT NULL, "
                    "is_open BOOLEAN NOT NULL, source VARCHAR, updated_at TIMESTAMP, "
                    "exchange VARCHAR, pretrade_date BIGINT, PRIMARY KEY (cal_date))")

    if minutes is None:
        minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES]
    if dailies is None:
        dailies = [(d, c) for d in DAYS for c in CODES]
    if calendar is None:
        calendar = _calendar_rows(W_START, W_END)

    if minutes:
        con.executemany("INSERT INTO stock_minutes VALUES (?, ?, 1.0, 1.0)",
                        [(c, _ms(d, hh, mm)) for d, c, hh, mm in minutes])
    if dailies:
        con.executemany("INSERT INTO stock_daily VALUES (?, ?, 1.0)",
                        [(c, _ms(d)) for d, c in dailies])
    if with_calendar_table and calendar:
        con.executemany("INSERT INTO trade_calendar VALUES (?, ?, 'test', NULL, 'SSE', NULL)",
                        calendar)
    con.close()
    return path


def _healthy_db(tmp_path):
    return _mk(tmp_path, "used.db")


def _mk(parent: Path, name: str, **kw):
    parent.mkdir(parents=True, exist_ok=True)
    return _make_db(parent / name, **kw)


# ---------------------------------------------------------------------------
# 基线与三型
# ---------------------------------------------------------------------------

def test_pass_full_coverage(tmp_path):
    db = _healthy_db(tmp_path)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["applies"] is True
    assert r["verdict"] == "PASS"
    assert r["zero_coverage_days"] == [] and r["low_coverage_days"] == []
    assert r["stale_copy"]["days"] == []
    assert r["engine_day_count"] == 5
    assert r["elapsed_sec"] >= 0


def test_type1_zero_day_blocks_by_default(tmp_path):
    # 01-07 有日线无任何分钟 ⇒ ①整日缺 ⇒ BLOCK（fail-closed）
    minutes = [(d, c, hh, mm) for d, c, hh, mm in
               [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES]
               if d != "2026-01-07"]
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["verdict"] == "BLOCK"
    assert r["zero_coverage_days"] == ["2026-01-07"]
    assert "QS_MINUTE_COVERAGE_SKIP" in r["block_reason"]


def test_type1_zero_day_warns_when_strict_off(tmp_path):
    minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES
               if d != "2026-01-07"]
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_STRICT": "0"})
    assert r["verdict"] == "WARN"          # 降级不阻断
    assert r["zero_coverage_days"] == ["2026-01-07"]


def test_type2_low_coverage_warns_with_ratio(tmp_path):
    # 01-07 仅 2/10 只标的有分钟（ratio=0.2 < 0.5）⇒ ②WARN 列日与覆盖率
    minutes = ([(d, c, hh, mm) for d in DAYS if d != "2026-01-07"
                for c in CODES for hh, mm in MINUTE_TIMES]
               + [("2026-01-07", c, 9, 31) for c in CODES[:2]])
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["verdict"] == "WARN"
    assert len(r["low_coverage_days"]) == 1
    low = r["low_coverage_days"][0]
    assert low["date"] == "2026-01-07"
    assert low["minute_codes"] == 2 and low["daily_codes"] == 10
    assert low["ratio"] == pytest.approx(0.2)


def test_type2_threshold_is_strict_less_than(tmp_path):
    # ratio 恰等于阈值 0.5（5/10）⇒ 不判低覆盖（判据是 <，不含等于）
    minutes = ([(d, c, hh, mm) for d in DAYS if d != "2026-01-07"
                for c in CODES for hh, mm in MINUTE_TIMES]
               + [("2026-01-07", c, 9, 31) for c in CODES[:5]])
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["low_coverage_days"] == []
    assert r["verdict"] == "PASS"


def test_type2_min_ratio_zero_disables(tmp_path):
    minutes = ([(d, c, hh, mm) for d in DAYS if d != "2026-01-07"
                for c in CODES for hh, mm in MINUTE_TIMES]
               + [("2026-01-07", c, 9, 31) for c in CODES[:2]])
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_MIN_RATIO": "0"})
    assert r["low_coverage_days"] == [] and r["verdict"] == "PASS"


def test_type2_min_ratio_env_tightened(tmp_path):
    # 阈值收紧到 0.9：8/10=0.8 < 0.9 ⇒ 判低
    minutes = ([(d, c, hh, mm) for d in DAYS if d != "2026-01-07"
                for c in CODES for hh, mm in MINUTE_TIMES]
               + [("2026-01-07", c, 9, 31) for c in CODES[:8]])
    db = _mk(tmp_path, "used.db", minutes=minutes)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_MIN_RATIO": "0.9"})
    assert [x["date"] for x in r["low_coverage_days"]] == ["2026-01-07"]


def test_type3_stale_copy_detected(tmp_path):
    # 所用库 01-07 整日缺；主库（另一合成库）01-07 达标 ⇒ ③列日（BLOCK 同时给出陈旧证据）
    minutes_used = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES
                    if d != "2026-01-07"]
    used = _mk(tmp_path / "copy", "used.db", minutes=minutes_used)
    main = _mk(tmp_path / "main", "main.db")        # 主库全量健康
    r = check_minute_coverage(used, W_START, W_END, "minute-bar-v1",
                              main_db_path=main, env={})
    assert r["verdict"] == "BLOCK"                   # ①仍 BLOCK（strict 默认）
    assert r["stale_copy"]["performed"] is True
    assert r["stale_copy"]["days"] == ["2026-01-07"]  # 缺的是副本，主库可补
    assert any("刷新副本" in line or "副本" in line for line in format_summary(r))


def test_type3_main_unreadable_skips_without_false_positive(tmp_path):
    # 主库不可读（模拟 daemon 独占：垃圾文件触发打开失败）⇒ ③跳过并说明，不误报
    minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES
               if d != "2026-01-07"]
    used = _mk(tmp_path, "used.db", minutes=minutes)
    junk = tmp_path / "main.db"
    junk.write_bytes(b"not a duckdb file")
    r = check_minute_coverage(used, W_START, W_END, "minute-bar-v1",
                              main_db_path=junk, env={})
    assert r["stale_copy"]["performed"] is False
    assert "主库不可读" in r["stale_copy"]["reason"]
    assert r["stale_copy"]["days"] == []             # 不误报


def test_type3_used_is_main_not_applicable(tmp_path):
    db = _healthy_db(tmp_path)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              main_db_path=db, env={})
    assert r["stale_copy"]["performed"] is False
    assert "不适用" in r["stale_copy"]["reason"]


def test_missing_minutes_table_is_full_zero_coverage(tmp_path):
    # 缺 stock_minutes 表 = 全窗口零覆盖（fail-closed，禁静默当作「无需检查」）
    db = _mk(tmp_path, "used.db", minutes=[], with_minutes_table=False)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["verdict"] == "BLOCK"
    assert r["zero_coverage_days"] == DAYS
    assert any("stock_minutes 缺表" in n for n in r["notes"])


# ---------------------------------------------------------------------------
# 三开关
# ---------------------------------------------------------------------------

def test_skip_flag_marks_skipped_with_reason(tmp_path):
    db = _healthy_db(tmp_path)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_SKIP": "1"})
    assert r["skipped"] is True and r["verdict"] == "SKIPPED"
    assert "QS_MINUTE_COVERAGE_SKIP=1" in r["skip_reason"]
    # stdout 留痕可查（format_summary 是回测前置检查/CLI 的统一 stdout 出口）
    lines = format_summary(r)
    assert any("已跳过" in ln for ln in lines)


def test_daily_profile_not_applies_without_io(tmp_path):
    # 日线 profile：零行为变化——连库都不需要存在
    r = check_minute_coverage(tmp_path / "nonexistent.db", W_START, W_END,
                              "daily-bar-v1", env={})
    assert r["applies"] is False and r["verdict"] == "PASS"
    r2 = check_minute_coverage(tmp_path / "nonexistent.db", W_START, W_END,
                               "daily-open-close-proxy-v1", env={})
    assert r2["applies"] is False


def test_knob_echo_in_report(tmp_path):
    db = _healthy_db(tmp_path)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_MIN_RATIO": "0.7"})
    assert r["min_ratio"] == 0.7 and r["strict"] is True and r["skipped"] is False


def test_min_ratio_invalid_falls_back_with_note(tmp_path):
    db = _healthy_db(tmp_path)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1",
                              env={"QS_MINUTE_COVERAGE_MIN_RATIO": "abc"})
    assert r["min_ratio"] == 0.5
    assert any("回退默认" in n for n in r["notes"])


# ---------------------------------------------------------------------------
# 日键口径一致性专项（②审条件③）
# ---------------------------------------------------------------------------

def test_day_key_matches_engine_source(tmp_path):
    """闸交易日基准 == 引擎实际来源：SELECT DISTINCT time FROM stock_daily 经
    Asia/Shanghai 换算（duckdb_provider.get_trade_days 同式），逐日一致。"""
    # 01-06 的日线 time 放 15:00 CST（非零点），引擎式换算仍归 01-06——闸必须同口径
    minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES]
    db = _mk(tmp_path, "used.db", minutes=minutes)
    con = duckdb.connect(str(db))
    con.execute("UPDATE stock_daily SET time = ? WHERE time = ?",
                [_ms("2026-01-06", 15, 0), _ms("2026-01-06")])
    con.close()

    import pandas as pd
    con = duckdb.connect(str(db), read_only=True)
    engine_times = [r[0] for r in con.execute(
        "SELECT DISTINCT time FROM stock_daily WHERE time >= ? AND time <= ? ORDER BY time",
        [_ms(W_START), _ms(W_END) + DAY_MS - 1]).fetchall()]
    con.close()
    engine_days = sorted(pd.Timestamp(v, unit="ms", tz="Asia/Shanghai")
                         .strftime("%Y-%m-%d") for v in engine_times)

    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    gate_days = sorted(x["date"] for x in r["days"])
    assert gate_days == engine_days == DAYS      # 引擎来源为准，逐日一致


def test_day_key_cst_not_utc(tmp_path):
    """UTC 误归因反例：00:01 CST 的 bar（UTC 前一日 16:01）必须归本 CST 日，不得漂移。
    另含 23:59 CST（UTC 15:59）边界；窗口只有 01-06 一个交易日。"""
    minutes = [("2026-01-06", c, 0, 1) for c in CODES[:3]] \
        + [("2026-01-06", c, 9, 31) for c in CODES[3:6]] \
        + [("2026-01-06", c, 23, 59) for c in CODES[6:]]
    db = _mk(tmp_path, "used.db",
             minutes=minutes,
             dailies=[("2026-01-06", c) for c in CODES],
             calendar=_calendar_rows("2026-01-05", "2026-01-07"))
    r = check_minute_coverage(db, "2026-01-06", "2026-01-06", "minute-bar-v1", env={})
    assert r["engine_day_count"] == 1
    assert [x["date"] for x in r["days"]] == ["2026-01-06"]
    assert r["days"][0]["minute_codes"] == 10   # 全部 10 只都归 01-06，无一日漂移
    assert r["verdict"] == "PASS"


def test_day_key_helpers_equivalent_to_time_axis():
    """CST 日键公式与 time_axis（CST 日界唯一真相源）恒等，防两套日界语义漂移。"""
    for day in DAYS + ["2024-02-29", "2026-12-31"]:
        dk = date_to_dkey(day)
        assert dkey_to_date(dk) == day                       # dkey ↔ date 互逆
        lo, hi = dkey_to_ms_range(dk)
        assert lo == day_start_ms(tz_start_ms(day))          # 与 time_axis 零点恒等
        assert hi == lo + DAY_MS - 1
        assert dk == (tz_start_ms(day) + CST_OFFSET_MS) // DAY_MS


# ---------------------------------------------------------------------------
# 日历一致性对拍（方案 §七-1：trade_calendar 仅交叉核对，不作 ①/② 基准）
# ---------------------------------------------------------------------------

def test_calendar_gap_warns_but_not_blocks(tmp_path):
    # 日历说 01-08 开市但日线无数据 ⇒ 对拍 WARN（引擎本就不迭代该日，不判 ①BLOCK）
    dailies = [(d, c) for d in DAYS for c in CODES if d != "2026-01-08"]
    db = _mk(tmp_path, "used.db", dailies=dailies)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["verdict"] == "WARN"
    assert r["calendar_check"]["missing_daily_days"] == ["2026-01-08"]
    assert "2026-01-08" not in r["zero_coverage_days"]      # 不入 ①（引擎来源为准）


def test_calendar_table_missing_skips_crosscheck(tmp_path):
    db = _mk(tmp_path, "used.db", with_calendar_table=False)
    r = check_minute_coverage(db, W_START, W_END, "minute-bar-v1", env={})
    assert r["calendar_check"]["performed"] is False
    assert "trade_calendar" in r["calendar_check"]["reason"]
    assert r["verdict"] == "PASS"                            # 对拍跳过不误报


# ---------------------------------------------------------------------------
# 回测前置检查接线（run_ptrade_strategy）
# ---------------------------------------------------------------------------

def test_precheck_minute_coverage_block_raises(tmp_path):
    from quantstudio.backtest import run_ptrade_strategy as rps
    minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES
               if d != "2026-01-07"]
    db = _mk(tmp_path, "used.db", minutes=minutes)
    with pytest.raises(RuntimeError, match="分钟覆盖闸 BLOCK"):
        rps._precheck_minute_coverage(db, W_START, W_END, "minute-bar-v1")


def test_precheck_minute_coverage_daily_profile_noop(tmp_path, capsys):
    from quantstudio.backtest import run_ptrade_strategy as rps
    r = rps._precheck_minute_coverage(tmp_path / "nonexistent.db", W_START, W_END,
                                      "daily-bar-v1")
    assert r["applies"] is False                             # 不打开库、不抛错


def test_leave_minute_gate_trace_writes_skip_record(tmp_path, capsys):
    from quantstudio.backtest import run_ptrade_strategy as rps
    out = tmp_path / "results" / "run1"
    out.mkdir(parents=True)
    report = {"gate": "minute-coverage-gate", "schema_version": 1,
              "db_path": "x.db", "start": W_START, "end": W_END,
              "engine_profile": "minute-bar-v1", "skipped": True,
              "skip_reason": "QS_MINUTE_COVERAGE_SKIP=1：整闸跳过", "verdict": "SKIPPED",
              "elapsed_sec": 0.01}
    rps._leave_minute_gate_trace(out, report)
    p = out / "minute_coverage_gate_SKIPPED.json"
    assert p.exists()
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["skipped"] is True and "QS_MINUTE_COVERAGE_SKIP" in payload["skip_reason"]
    # 非跳过报告不落痕（最小足迹：结果目录只在跳过场景新增/改写文件）
    before = p.read_text(encoding="utf-8")
    rps._leave_minute_gate_trace(out, {**report, "skipped": False})
    assert p.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# CLI（复用同一实现；退出码契约）
# ---------------------------------------------------------------------------

def _run_cli(*args, env_extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("QS_MINUTE_COVERAGE")}
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "check_minute_coverage.py"), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), env=env,
        timeout=300)


def test_cli_pass_exit_zero(tmp_path):
    db = _healthy_db(tmp_path)
    js = tmp_path / "report.json"
    p = _run_cli("--db", str(db), "--main-db", str(db),
                 "--start", W_START, "--end", W_END, "--json", str(js))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "[minute-gate] ✅ PASS" in p.stdout
    payload = json.loads(js.read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS" and payload["engine_day_count"] == 5


def test_cli_block_exit_two_with_escape_hint(tmp_path):
    minutes = [(d, c, hh, mm) for d in DAYS for c in CODES for hh, mm in MINUTE_TIMES
               if d != "2026-01-07"]
    db = _mk(tmp_path, "used.db", minutes=minutes)
    p = _run_cli("--db", str(db), "--main-db", str(db),
                 "--start", W_START, "--end", W_END)
    assert p.returncode == 2
    assert "2026-01-07" in p.stdout and "QS_MINUTE_COVERAGE_SKIP" in p.stdout


def test_cli_warn_exit_zero(tmp_path):
    minutes = ([(d, c, hh, mm) for d in DAYS if d != "2026-01-07"
                for c in CODES for hh, mm in MINUTE_TIMES]
               + [("2026-01-07", c, 9, 31) for c in CODES[:2]])
    db = _mk(tmp_path, "used.db", minutes=minutes)
    p = _run_cli("--db", str(db), "--main-db", str(db),
                 "--start", W_START, "--end", W_END)
    assert p.returncode == 0                                # WARN 不阻断
    assert "②低覆盖=1" in p.stdout
