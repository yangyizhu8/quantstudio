"""回测前分钟数据覆盖闸（新增检测型，QD-2026-0015 三型判据）——纯函数实现。

方案：docs/backtest-minute-coverage-gate-design-20261030.md（六步流水线 ①，②审通过 2026-10-30）
本文件是闸的**唯一实现**：`quantstudio/backtest/run_ptrade_strategy.py` 的前置检查与
`scripts/check_minute_coverage.py` CLI 均复用 `check_minute_coverage()`，禁两份判据。

知识反哺：[命中] QD-2026-0015（qd/D1/knowledge/qmt/QD-2026-0015.md，2026-10-30）——三型判据来源
知识反哺：[命中] QD-2026-0011（qd/D1/knowledge/qmt/QD-2026-0011.md，2026-10-30）——日键须 CST 口径（避免 T/T-1 与 UTC 偏移族错误）

②审条件② 核验结论（2026-10-30，实施首步完成）：
  `scripts/audit_watermark_coverage.py` 是**水位溯源审计**（watermark 行存在性 + COUNT(*) +
  MAX(time_col)，经 writer.execute_read），**没有**逐日全市场 distinct code 覆盖查询，无可复用
  底层查询；引擎层 `duckdb_data_access.query_industry_membership_quality` 的 COUNT(DISTINCT code)
  属 industry_membership 质量门（表/语义不同）。逐日覆盖判据的唯一同源先例是 QD-2026-0015 的
  取证 fixture `agent_workspace/first_board_high_open_1to2/_minute_day_cov3.py`（evidence 探针，
  非框架代码，不可 import）。故本闸自带 SQL，日键公式与该 fixture 逐字同源：
  ``dkey = (time + 28_800_000) // 86_400_000``（CST epoch-day；28_800_000 = UTC+8ms）。

②审条件③ 日键口径一致性（引擎来源为准 + 差异写明）：
  引擎交易日**不读 trade_calendar**——`BacktestEngine._get_trade_days` →
  `DuckDBCalendarProvider.get_trade_days` → `query_trade_days_range` =
  ``SELECT DISTINCT time FROM stock_daily WHERE time BETWEEN start_ms AND end_ms``，
  日期换算 `pd.Timestamp(v, unit='ms', tz='Asia/Shanghai')`。因此本闸：
  · **①整日缺/②低覆盖的交易日基准 = 窗口内 stock_daily 的 CST distinct 日**（= 引擎实际会
    迭代的日；与设计稿「trade_calendar.is_open=1 为基准」的差异：引擎用别的日键来源时以引擎
    来源为准——②审条件③ 明文要求）；
  · trade_calendar 仅做**日历一致性对拍**（日历开市日 vs 日线日，不一致 WARN，方案 §七-1），
    trade_calendar 缺表/未覆盖窗口时跳过对拍并说明，绝不据此误报；
  · 日键换算经 `time_axis`（CST 日界唯一真相源，恒等式见 tests/test_coverage_gate.py 专项用例）。

三型判据（QD-2026-0015）逐交易日：
  ① 整日缺：该日 stock_minutes count(distinct code)=0 ⇒ BLOCK（fail-closed，退出码非 0）；
     `QS_MINUTE_COVERAGE_STRICT=0` 降为 WARN。缺 stock_minutes 表 = 全窗口零覆盖，同判。
  ② 低覆盖：分钟 distinct code ÷ 同日 stock_daily distinct code < 阈值 ⇒ WARN（列日期与覆盖率，
     不阻断）；`QS_MINUTE_COVERAGE_MIN_RATIO` 默认 0.5，设 0 则不报。注意：与 QD-2026-0015
     evidence 探针的「峰值 50%」口径不同——闸以方案定稿的「同日日线分母」为准（逐日更精确）。
  ③ 副本陈旧：主库该日覆盖达标而所用库不达标 ⇒ WARN（提示刷新副本）；仅主库可读且 ≠ 所用库时
     判定，主库不可读（daemon 独占）/缺表 ⇒ 跳过该型并说明，不得误报。
  跳过：`QS_MINUTE_COVERAGE_SKIP=1` 整闸跳过（默认 0 = fail-closed）；调用方必须在 stdout 与
     结果目录留痕「已跳过 + 原因」（见 run_ptrade_strategy._leave_minute_gate_trace）。

数据访问纪律：一律 `duckdb.connect(read_only=True)`，禁写禁 CHECKPOINT，不停 daemon。
性能预算：方案拟值 60s/窗口；实测值见 scripts/check_minute_coverage.py --json 报告的
  timings 字段与 tests/test_coverage_gate.py 模块注释（②审条件①：不得把拟值当实测）。
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, List, Optional

import duckdb

# CST 日界唯一真相源（time_axis 只依赖 pandas，无循环导入；本闸**不得**另写日界算式）
from quantstudio.backtest.providers.time_axis import (
    CST_OFFSET_MS, DAY_MS, end_ms as _end_ms, start_ms as _start_ms,
)

GATE_NAME = "minute-coverage-gate"
REPORT_SCHEMA_VERSION = 1

MINUTE_PROFILE = "minute-bar-v1"

ENV_SKIP = "QS_MINUTE_COVERAGE_SKIP"
ENV_STRICT = "QS_MINUTE_COVERAGE_STRICT"
ENV_MIN_RATIO = "QS_MINUTE_COVERAGE_MIN_RATIO"
DEFAULT_MIN_RATIO = 0.5

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

EPOCH_DATE = date(1970, 1, 1)


# ---------------------------------------------------------------------------
# 日键工具（dkey = 自 1970-01-01 起的 CST 天序号；与 time_axis.day_start_ms 恒等）
# ---------------------------------------------------------------------------

def dkey_to_date(dkey: int) -> str:
    """CST epoch-day → 'YYYY-MM-DD'。"""
    return (EPOCH_DATE + timedelta(days=int(dkey))).isoformat()


def dkey_to_ms_range(dkey: int) -> tuple:
    """CST epoch-day → 该 CST 自然日的 [零点 ms, 末毫秒 ms]（闭区间）。"""
    lo = int(dkey) * DAY_MS - CST_OFFSET_MS
    return lo, lo + DAY_MS - 1


def date_to_dkey(day_str: str) -> int:
    """'YYYY-MM-DD' → CST epoch-day。"""
    return (_start_ms(str(day_str)[:10]) + CST_OFFSET_MS) // DAY_MS


# ---------------------------------------------------------------------------
# 开关解析（env 映射注入以便测试；默认读 os.environ）
# ---------------------------------------------------------------------------

def _read_env(env: Optional[dict]) -> dict:
    return os.environ if env is None else env


def _flag(env: Optional[dict], name: str, default: bool) -> bool:
    raw = _read_env(env).get(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return default


def _read_min_ratio(env: Optional[dict], notes: List[str]) -> float:
    raw = _read_env(env).get(ENV_MIN_RATIO)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MIN_RATIO
    try:
        return float(str(raw).strip())
    except ValueError:
        notes.append(f"{ENV_MIN_RATIO}={raw!r} 不是数字，回退默认 {DEFAULT_MIN_RATIO}")
        return DEFAULT_MIN_RATIO


# ---------------------------------------------------------------------------
# 底层查询（与 QD-2026-0015 fixture 同源；只读）
# ---------------------------------------------------------------------------

_DAY_KEY_EXPR = f"((time + {CST_OFFSET_MS}) // {DAY_MS})"


def _table_exists(con, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table]).fetchone()
    return bool(row and int(row[0]) > 0)


def _per_day_codes(con, table: str, start_ms_v: int, end_ms_v: int) -> dict:
    """窗口内逐 CST 日 count(distinct code)。表缺/查询异常 → {}（由调用方记 note）。"""
    return dict(con.execute(
        f"SELECT {_DAY_KEY_EXPR} AS dkey, COUNT(DISTINCT code) AS n "
        f"FROM {table} WHERE time >= ? AND time <= ? GROUP BY dkey",
        [start_ms_v, end_ms_v]).fetchall())


def _calendar_open_dkeys(con, start_ms_v: int, end_ms_v: int) -> Optional[dict]:
    """trade_calendar 开市日 dkey 集合；缺表 → None（跳过对拍）。"""
    if not _table_exists(con, "trade_calendar"):
        return None
    rows = con.execute(
        f"SELECT ((cal_date + {CST_OFFSET_MS}) // {DAY_MS}) AS dkey "
        "FROM trade_calendar WHERE is_open AND cal_date >= ? AND cal_date <= ?",
        [start_ms_v, end_ms_v]).fetchall()
    return {int(r[0]) for r in rows}


def _flagged_day_counts(con, table: str, dkeys: List[int]) -> dict:
    """仅取指定 dkey 的逐日 distinct code（主库对拍用；OR-BETWEEN 保留 time 列 zonemap）。"""
    if not dkeys:
        return {}
    clauses, params = [], []
    for d in sorted(dkeys):
        lo, hi = dkey_to_ms_range(d)
        clauses.append("(time BETWEEN ? AND ?)")
        params.extend([lo, hi])
    sql = (f"SELECT {_DAY_KEY_EXPR} AS dkey, COUNT(DISTINCT code) AS n "
           f"FROM {table} WHERE {' OR '.join(clauses)} GROUP BY dkey")
    return dict(con.execute(sql, params).fetchall())


# ---------------------------------------------------------------------------
# 闸主函数（纯函数：不打印、不抛判定类异常；BLOCK 经 report['verdict'] 传达）
# ---------------------------------------------------------------------------

def check_minute_coverage(
    db_path,
    start,
    end,
    engine_profile: str,
    *,
    main_db_path=None,
    env: Optional[dict] = None,
) -> dict:
    """对 [start, end]（'YYYY-MM-DD'，闭区间，CST 日界）执行分钟覆盖闸，返回报告 dict。

    - 仅 ``engine_profile == 'minute-bar-v1'`` 生效（applies=True）；其余 profile
      applies=False 且**不打开任何数据库**（日线 profile 零行为改变的硬保证）。
    - main_db_path=None 时经 `quantstudio._paths.db_path()` 解析主库（type ③ 对拍）。
    - 判定结果全量写入 report；调用方（回测前置检查 / CLI）自行决定 raise / exit code。
    """
    t_begin = time.perf_counter()
    notes: List[str] = []
    timings: dict = {}

    base = {
        "gate": GATE_NAME,
        "schema_version": REPORT_SCHEMA_VERSION,
        "db_path": str(db_path),
        "start": str(start)[:10],
        "end": str(end)[:10],
        "engine_profile": str(engine_profile),
    }

    # —— profile 门（在一切 IO 之前：日线 profile 零行为变化）——
    if engine_profile != MINUTE_PROFILE:
        base.update(applies=False, skipped=False, skip_reason=None,
                    verdict="PASS", zero_coverage_days=[], low_coverage_days=[],
                    stale_copy={"performed": False, "reason": "profile 非 minute-bar-v1",
                                "days": []},
                    calendar_check={"performed": False, "reason": None,
                                    "missing_daily_days": [], "extra_daily_days": []},
                    notes=["分钟覆盖闸仅 minute-bar-v1 profile 生效，本次跳过（零行为变化）"],
                    elapsed_sec=round(time.perf_counter() - t_begin, 4))
        return base
    base["applies"] = True

    # —— 开关（默认 fail-closed：SKIP=0 / STRICT=1 / MIN_RATIO=0.5）——
    skip = _flag(env, ENV_SKIP, False)
    strict = _flag(env, ENV_STRICT, True)
    min_ratio = _read_min_ratio(env, notes)
    base["knobs"] = {ENV_SKIP: skip, ENV_STRICT: strict, ENV_MIN_RATIO: min_ratio}

    if skip:
        reason = (f"{ENV_SKIP}=1：整闸跳过（仅限排障/已知受控场景；默认 fail-closed）。"
                  "跳过不改变回测行为，但缺数日可能静默少买/错选（QD-2026-0015）。")
        base.update(skipped=True, skip_reason=reason, verdict="SKIPPED",
                    strict=strict, min_ratio=min_ratio,
                    zero_coverage_days=[], low_coverage_days=[],
                    stale_copy={"performed": False, "reason": "闸整体跳过", "days": []},
                    calendar_check={"performed": False, "reason": "闸整体跳过",
                                    "missing_daily_days": [], "extra_daily_days": []},
                    notes=notes, elapsed_sec=round(time.perf_counter() - t_begin, 4))
        return base
    base.update(skipped=False, skip_reason=None, strict=strict, min_ratio=min_ratio)

    start_ms_v = _start_ms(str(start)[:10])
    end_ms_v = _end_ms(str(end)[:10])
    base["start_ms"], base["end_ms"] = start_ms_v, end_ms_v
    base["day_key_formula"] = _DAY_KEY_EXPR + "  # CST 日键（time_axis 同源）"
    base["day_source"] = ("stock_daily DISTINCT CST days（引擎 query_trade_days_range 同源；"
                          "trade_calendar 仅对拍，见模块 docstring ②审条件③）")

    def _fail(error: str, exc: Optional[Exception] = None) -> dict:
        base.update(verdict="ERROR", error=f"{error}: {exc}" if exc else error,
                    zero_coverage_days=[], low_coverage_days=[],
                    stale_copy={"performed": False, "reason": "闸 ERROR", "days": []},
                    calendar_check={"performed": False, "reason": "闸 ERROR",
                                    "missing_daily_days": [], "extra_daily_days": []},
                    notes=notes, timings=timings,
                    elapsed_sec=round(time.perf_counter() - t_begin, 4))
        return base

    # —— 所用库逐日统计（只读）——
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as e:  # noqa: BLE001
        return _fail("所用库打开失败（read_only）", e)

    try:
        try:
            t0 = time.perf_counter()
            daily_counts = (_per_day_codes(con, "stock_daily", start_ms_v, end_ms_v)
                            if _table_exists(con, "stock_daily") else None)
            timings["daily_scan"] = round(time.perf_counter() - t0, 4)
            if daily_counts is None:
                notes.append("stock_daily 缺表：窗口内无引擎交易日，引擎将自行报 "
                             "「No trading days」；闸无可比基准，不判 ①/②")
            t0 = time.perf_counter()
            if _table_exists(con, "stock_minutes"):
                minute_counts = _per_day_codes(con, "stock_minutes", start_ms_v, end_ms_v)
            else:
                minute_counts = {}
                notes.append("stock_minutes 缺表：按全窗口零覆盖判 ①（fail-closed）")
            timings["minute_scan"] = round(time.perf_counter() - t0, 4)
        except Exception as e:  # noqa: BLE001
            return _fail("逐日覆盖查询失败", e)

        # 日历一致性对拍（trade_calendar 仅交叉核对，不参与 ①/② 基准）
        calendar_check = {"performed": False, "reason": None,
                          "missing_daily_days": [], "extra_daily_days": []}
        engine_dkeys = sorted(daily_counts.keys()) if daily_counts else []
        try:
            t0 = time.perf_counter()
            cal_dkeys = _calendar_open_dkeys(con, start_ms_v, end_ms_v)
            timings["calendar_scan"] = round(time.perf_counter() - t0, 4)
        except Exception as e:  # noqa: BLE001
            cal_dkeys = None
            notes.append(f"trade_calendar 读取异常，跳过日历对拍：{e}")
        if cal_dkeys is None:
            calendar_check["reason"] = ("trade_calendar 缺表/读取异常，跳过日历一致性对拍"
                                        "（不影响 ①/②，其基准=引擎来源 stock_daily）")
        else:
            calendar_check["performed"] = True
            cal_set, eng_set = set(cal_dkeys), set(engine_dkeys)
            calendar_check["missing_daily_days"] = \
                [dkey_to_date(d) for d in sorted(cal_set - eng_set)]
            calendar_check["extra_daily_days"] = \
                [dkey_to_date(d) for d in sorted(eng_set - cal_set)]
            if calendar_check["missing_daily_days"]:
                notes.append("日历开市但 stock_daily 无数据的日（引擎不迭代这些日；"
                             "属日线缺数问题，WARN 提示）：" +
                             ",".join(calendar_check["missing_daily_days"][:10]))
            if calendar_check["extra_daily_days"]:
                notes.append("stock_daily 有数据但日历非开市/无行的日（日键口径差异信号，"
                             "WARN 提示）：" + ",".join(calendar_check["extra_daily_days"][:10]))
    finally:
        con.close()

    # ④验收失败修复（2026-10-30）：主路径此前只把 calendar_check 算进局部变量，
    # 从未写回报告 dict ⇒ 返回结构缺 calendar_check 键，与 tests/test_coverage_gate.py
    # 契约（r["calendar_check"]["performed"] / ["missing_daily_days"]）不自洽。
    # 早退路径（profile 门/SKIP/ERROR）本就带该键；此处补齐主路径。
    base["calendar_check"] = calendar_check

    # —— ① 整日缺 / ② 低覆盖（基准 = 引擎日 = 窗口内 stock_daily 日）——
    zero_days: List[str] = []
    low_days: List[dict] = []
    days_detail: List[dict] = []
    for d in engine_dkeys:
        d_str = dkey_to_date(d)
        n_min = int(minute_counts.get(d, 0) or 0)
        n_day = int(daily_counts.get(d, 0) or 0)
        ratio = (round(n_min / n_day, 6) if n_day > 0 else None)
        verdict = "ok"
        if n_min == 0:
            verdict = "zero"
            zero_days.append(d_str)
        elif min_ratio > 0 and n_day > 0 and n_min / n_day < min_ratio:
            verdict = "low"
            low_days.append({"date": d_str, "minute_codes": n_min,
                             "daily_codes": n_day, "ratio": ratio})
        days_detail.append({"date": d_str, "minute_codes": n_min,
                            "daily_codes": n_day, "ratio": ratio, "verdict": verdict})
    base["engine_day_count"] = len(engine_dkeys)
    base["days"] = days_detail
    base["zero_coverage_days"] = zero_days
    base["low_coverage_days"] = low_days

    # —— ③ 副本陈旧（仅主库可读且 ≠ 所用库时判定；不得误报）——
    stale = {"performed": False, "reason": None, "days": []}
    flagged_dkeys = [d for d, det in zip(engine_dkeys, days_detail)
                     if det["verdict"] != "ok"]
    resolved_main = _resolve_main_db(main_db_path, base["db_path"])
    base["main_db_path"] = (str(resolved_main) if resolved_main is not None else None)
    if resolved_main is None:
        stale["reason"] = "所用库即主库（或无法解析主库路径）：③ 不适用"
    else:
        try:
            t0 = time.perf_counter()
            mcon = duckdb.connect(str(resolved_main), read_only=True)
            try:
                if not (_table_exists(mcon, "stock_minutes")
                        and _table_exists(mcon, "stock_daily")):
                    stale["reason"] = "主库缺 stock_minutes/stock_daily 表：跳过 ③"
                else:
                    m_min = _flagged_day_counts(mcon, "stock_minutes", flagged_dkeys)
                    m_day = _flagged_day_counts(mcon, "stock_daily", flagged_dkeys)
                    for d in flagged_dkeys:
                        d_str = dkey_to_date(d)
                        mm = int(m_min.get(d, 0) or 0)
                        md = int(m_day.get(d, 0) or 0)
                        main_ok = (mm > 0) if d_str in zero_days else \
                            (md > 0 and mm / md >= min_ratio)
                        if main_ok:
                            stale["days"].append(d_str)
                    stale["performed"] = True
            finally:
                mcon.close()
            timings["main_db_scan"] = round(time.perf_counter() - t0, 4)
            if not stale["performed"]:
                notes.append(f"副本陈旧判定跳过：{stale['reason']}")
        except Exception as e:  # noqa: BLE001
            stale["reason"] = (f"主库不可读（可能被 daemon 独占/文件锁），跳过 ③ 不误报："
                               f"{type(e).__name__}")
            notes.append(stale["reason"] +
                         f"（可用独立 CLI 在停服窗补跑：scripts/check_minute_coverage.py）")
    base["stale_copy"] = stale

    # —— 汇总判定 ——
    calendar_mismatch = bool(calendar_check["performed"] and
                             (calendar_check["missing_daily_days"]
                              or calendar_check["extra_daily_days"]))
    if zero_days and strict:
        base["verdict"] = "BLOCK"
        base["block_reason"] = (
            f"分钟数据整日缺 {len(zero_days)} 个交易日（{','.join(zero_days[:10])}"
            f"{'…' if len(zero_days) > 10 else ''}）——缺数日分钟过滤静默失效（QD-2026-0015）。"
            f"修复数据后重跑；确属受控场景可 {ENV_STRICT}=0 降级 WARN 或 {ENV_SKIP}=1 跳过（须留痕）。")
    elif zero_days or low_days or stale["days"] or calendar_mismatch:
        base["verdict"] = "WARN"
    else:
        base["verdict"] = "PASS"
    base["notes"] = notes
    base["timings"] = timings
    base["elapsed_sec"] = round(time.perf_counter() - t_begin, 4)
    return base


def _resolve_main_db(main_db_path, used_db_path: str) -> Optional[Path]:
    """解析主库路径；与所用库同文件 → None（③ 不适用）。"""
    if main_db_path is None:
        try:
            from quantstudio._paths import db_path as _qs_db_path
            main_db_path = _qs_db_path()
        except Exception:  # noqa: BLE001
            return None
    p = Path(str(main_db_path))
    try:
        if p.resolve() == Path(used_db_path).resolve():
            return None
    except OSError:
        pass
    return p


# ---------------------------------------------------------------------------
# 人读摘要（回测前置检查与 CLI 共用，单一格式源）
# ---------------------------------------------------------------------------

def format_summary(report: dict) -> List[str]:
    """报告 → stdout 摘要行列表（调用方打印；留痕义务在调用方）。"""
    head = f"[minute-gate] db={report.get('db_path')} window={report.get('start')}~{report.get('end')}"
    lines = [head]
    if not report.get("applies"):
        lines.append("[minute-gate] 非 minute-bar-v1 profile：闸不适用（零行为变化）")
        return lines
    if report.get("skipped"):
        lines.append(f"[minute-gate] ⚠️ 已跳过：{report.get('skip_reason')}")
        return lines
    zero = report.get("zero_coverage_days") or []
    low = report.get("low_coverage_days") or []
    stale = report.get("stale_copy") or {}
    cal = report.get("calendar_check") or {}
    lines.append(f"[minute-gate] 交易日基准={report.get('engine_day_count', 0)} 天"
                 f"（引擎来源 stock_daily；日历对拍{'已做' if cal.get('performed') else '跳过'}）"
                 f" strict={report.get('strict')} min_ratio={report.get('min_ratio')}")
    lines.append(f"[minute-gate] ①整日缺={len(zero)} ②低覆盖={len(low)}"
                 f" ③副本陈旧={len(stale.get('days') or [])} 耗时={report.get('elapsed_sec')}s")
    for tag, items, fmt in (
            ("①整日缺", zero, None),
            ("②低覆盖", [f"{x['date']}({x['minute_codes']}/{x['daily_codes']}="
                        f"{x['ratio']:.1%})" for x in low], None),
            ("③副本陈旧", stale.get("days") or [], None),
            ("日历缺日线日", cal.get("missing_daily_days") or [], None),
            ("日线无日历日", cal.get("extra_daily_days") or [], None)):
        if items:
            shown = [fmt(x) if fmt else str(x) for x in items[:20]]
            more = f" …+{len(items) - 20}" if len(items) > 20 else ""
            lines.append(f"[minute-gate] {tag}: {', '.join(shown)}{more}")
    if report.get("verdict") == "BLOCK":
        lines.append(f"[minute-gate] ❌ BLOCK：{report.get('block_reason')}")
    elif report.get("verdict") == "WARN":
        lines.append("[minute-gate] ⚠️ WARN：以上问题不阻断回测，但缺数日结论可能失真")
    elif report.get("verdict") == "PASS":
        lines.append("[minute-gate] ✅ PASS：窗口内分钟覆盖无整日缺/低覆盖告警")
    for n in (report.get("notes") or []):
        lines.append(f"[minute-gate] note: {n}")
    return lines
