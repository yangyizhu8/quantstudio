"""tests/test_qfq_reanchor_batch2.py — QFQ 重锚子系统「第二批：单证券修正引擎」测试。

覆盖模块：quantstudio/pipeline/qfq_reanchor_engine.py

对应用户批准的第二批强制实现边界（2026-07-26）+ 第二轮对抗审核修复（2026-07-27）：
1. 日线只能更新四个 front 列（其它列逐值未变证明）；
2. 分钟 R 方法 B（按重叠交易日/按 freq、OHLC 交叉验证、NULL 不伪造、分母 finite>0、
   单一稳定簇、bootstrap 才允许多簇、不用 Tushare A/B 直接乘价）；
3. 方法 A 黄金抽验 —— **严格硬下限**：任一 needs_update 区间 <3 个真实交易日，或任一
   抽验日 <5 根有效连续竞价 bar → 整券 BLOCK；09:30 不得补足连续竞价样本数；
   （第三轮）连续竞价窗口 session-aware：[09:31,11:30] ∪ [13:01,15:00]（end-labeled），
   午间 11:31–12:59 bar 不计；黄金抽样与 cross_table_overlap 共用同一判断；
   真实方法 A 黄金数据 = **独立采集的 fresh xtquant 前复权输出**（固化 parquet +
   sha256），禁止 stored_raw × daily_scale 同源合成；
4. 分段计划 (code,freq,t_start,t_end,ratio)、左闭右开、精确变点非事件日±1；
5. 单证券事务（失败 ROLLBACK + 独立短事务记录，绝不推进 anchor）；
6. COMMIT 前硬门禁：
   - front-chain：**首个 staged 交易日必须校验修正范围外的真实上一交易日**（缺行
     front_chain_missing_prev；第三轮起唯一豁免 = 调用方传入 security master
     ``list_date_ms`` 且该日恰为上市首日——本地 MIN(time) **不构成证据**，截断
     数据不得被误当上市首日；日历无法证明上一交易日时同样 fail-closed 回滚）；
   - daily_staged_match：staged_count==matched、missing_target==0、mismatch==0、
     staged 日期必须为真实交易日 —— staged 未命中行不得被静默忽略；
7. canonical freq：事务前 canonicalize+去重；("1m","1min")、重复 "1min"、1min+5min
   独立计划；重复别名不得覆盖真实 ratio plan；
8. bootstrap 多簇：**逐变点解释证据**（ex_dates_ms），解释缺失/部分解释 → 整券
   BLOCK（changepoint_unexplained）；解释仅写入事件审计，UPDATE 边界仍取 R 精确变点；
9. 五项 postcheck（daily_staged_match/scale_consistency/kline_relation/
   row_conservation/cross_table_overlap）逐项实际故障注入 → 全表回滚证明；
10. 600875/600039/002864 **真实数据隔离回归**：行取自只读证据源
    D:/miniQMT策略实盘/qs_iso_a/data/quantstudio.db（2026-07-26 快照），固化为
    tests/fixtures/qfq_real_reanchor/*.parquet + metadata.json（含 sha256）；
    测试前校验 hash，全程仅写 tmp_path 临时库。

全部 hermetic：tmp_path 临时 DuckDB。不连正式 data/quantstudio.db、不写证据源、
不碰 daemon、不 stage/commit/push。
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import duckdb

from quantstudio.pipeline import qfq_reanchor_schema as SCHEMA
from quantstudio.pipeline import qfq_reanchor_engine as ENG
from quantstudio.pipeline.qfq_calendar import CalendarService, _at, TZ
from quantstudio.pipeline.writers import DDL_DUCKDB as PRICE_DDL
from quantstudio.pipeline.qfq_reanchor_engine import (
    FRONT_COLS,
    ReanchorBlocked,
    ReanchorTolerances,
    _check_minute_cov_raw,
    _raw_match_eps_minute,
    apply_reanchor_for_security,
)

FIXDIR = Path(__file__).resolve().parent / "fixtures" / "qfq_real_reanchor"

# ---------------------------------------------------------------------------
# 合成行情常量（结构对齐真实案例；日线 time = 当日 00:00 +08 epoch-ms）
# ---------------------------------------------------------------------------

DAYS5 = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"]
OPEN_DAYS = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"] + DAYS5
DAY_MS = [_at(d, 0, 0, 0) for d in DAYS5]
D1, D2, D3, D4, D5 = DAY_MS
D0 = _at("2026-07-17", 0, 0, 0)            # DAYS5 之前的真实交易日
D_SAT = _at("2026-07-18", 0, 0, 0)         # 周六（非交易日）

RAW_CLOSE = {D1: 10.0, D2: 10.1, D3: 10.2, D4: 10.3, D5: 9.9}
# 默认：D5 为除息日（分红 0.5 → preClose(D5)=9.80），其余日 preClose=前收
PRECLOSE_EXD5 = {D1: 9.95, D2: 10.0, D3: 10.1, D4: 10.2, D5: 9.8}
# 无公司行为链（002864 场景：窗口内不含除权，preClose=前日 close）
PRECLOSE_PLAIN = {D1: 9.95, D2: 10.0, D3: 10.1, D4: 10.2, D5: 10.3}

F_600875 = 9.8 / 10.3        # 合成结构：2026-07-24 除息后 D1..D4 目标前复权因子
F_600039 = 10.0 / 10.3       # 合成结构变体：分红 0.3
F_002864 = 0.7625            # 合成结构：daily 已正确、minute 陈旧的目标因子

# bootstrap 扩展窗口（两个修正段各 ≥3 交易日 + 末尾 noop）
EXT_DAYS = ["2026-07-15", "2026-07-16", "2026-07-17",
            "2026-07-20", "2026-07-21", "2026-07-22",
            "2026-07-23", "2026-07-24"]
EXT_MS = [_at(d, 0, 0, 0) for d in EXT_DAYS]
E1, E2, E3, E4, E5, E6, E7, E8 = EXT_MS
EXT_CLOSE = {E1: 10.0, E2: 10.1, E3: 10.2, E4: 9.3,
             E5: 9.4, E6: 9.5, E7: 9.1, E8: 9.2}
F1_EXT, F2_EXT = 0.9, 0.95   # E4 除权因子 0.9、E7 除权因子 0.95
EXT_PRECLOSE = {E1: 9.9, E2: 10.0, E3: 10.1, E4: round(10.2 * F1_EXT, 6),
                E5: 9.3, E6: 9.4, E7: round(9.5 * F2_EXT, 6), E8: 9.1}
EXT_SCALES = {E1: F1_EXT * F2_EXT, E2: F1_EXT * F2_EXT, E3: F1_EXT * F2_EXT,
              E4: F2_EXT, E5: F2_EXT, E6: F2_EXT, E7: 1.0, E8: 1.0}

# bar 为 end-labeled；09:30 为集合竞价附加样本；连续竞价合法窗口 session-aware：
# [09:31,11:30] ∪ [13:01,15:00]（下列合成时刻均落于合法窗口内，共 5 根）
BAR_CLOCKS = [(9, 30), (9, 31), (10, 0), (13, 1), (14, 59), (15, 0)]
BAR_CLOCKS_5MIN = [(9, 35), (10, 0), (11, 0), (13, 5), (14, 55), (15, 0)]
# 合成 fixture 语义：D1/E1 即证券上市首日 —— 测试通过 list_date_ms 显式传入
# security master 证据（第三轮起 MIN(time) 不再构成上市首日豁免依据）。


def _day_str(day_ms: int) -> str:
    return pd.Timestamp(int(day_ms), unit="ms", tz=TZ).strftime("%Y-%m-%d")


def _bars_of_day(day_str: str, close: float, clocks=BAR_CLOCKS):
    """某交易日合成 bar：(time_ms, open, high, low, close)。15:00 收盘=日线收盘。"""
    offs = [-0.04, -0.03, -0.02, -0.01, -0.005, 0.0]
    out = []
    for (h, m), off in zip(clocks, offs):
        c = close + off
        out.append((_at(day_str, h, m, 0), c - 0.01, c + 0.02, c - 0.03, c))
    return out


def _persist_full_window(svc: CalendarService, open_day_strs):
    open_ms = sorted(_at(d, 0, 0, 0) for d in open_day_strs)
    lo, hi = open_ms[0], open_ms[-1]
    start = pd.Timestamp(lo, unit="ms", tz=TZ).strftime("%Y-%m-%d")
    end = pd.Timestamp(hi, unit="ms", tz=TZ).strftime("%Y-%m-%d")
    all_days = [int(pd.Timestamp(d, tz=TZ).timestamp() * 1000)
                for d in pd.date_range(start, end, freq="D")]
    open_set = set(open_ms)
    closed = [d for d in all_days if d not in open_set]
    conn = svc._connect()
    svc.persist_trade_days_on_conn(conn, open_ms, closed_ms=closed)
    conn.commit()
    conn.close()


class _FakeCalendarProvider:
    name = "fake"

    def __init__(self, open_days):
        self._open = set(open_days)

    def get_trade_days(self, start, end):
        days = pd.date_range(start[:10], end[:10], freq="D")
        return [d.strftime("%Y-%m-%d") for d in days
                if d.strftime("%Y-%m-%d") in self._open]


# ---------------------------------------------------------------------------
# 落库 / DataFrame 构造
# ---------------------------------------------------------------------------

def _ins(conn, table: str, row: dict):
    cols = ",".join(f'"{c}"' for c in row)
    ph = ",".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(row.values()))


def _daily_row(code: str, day_ms: int, close: float, preclose: float,
               scale: float = 1.0, asset: str = "STOCK") -> dict:
    o, h, l = close - 0.05, close + 0.10, close - 0.15
    row = {
        "code": code, "time": day_ms, "open": o, "high": h, "low": l,
        "close": close, "volume": 12345.0, "amount": close * 12345.0,
        "preClose": preclose,
        "open_front": o * scale, "high_front": h * scale,
        "low_front": l * scale, "close_front": close * scale,
        "open_back": o * 1.2, "high_back": h * 1.2, "low_back": l * 1.2,
        "close_back": close * 1.2,
        "open_front_ratio": 1.0, "high_front_ratio": 1.0,
        "low_front_ratio": 1.0, "close_front_ratio": 1.0,
        "turn": 1.5, "pctChg": (close / preclose - 1.0) * 100.0,
        "isST": 0, "dividend_type": "none", "update_time": "orig-2026-07-25",
    }
    if asset == "STOCK":
        row["suspendFlag"] = 0
    return row


def _seed_security(conn, code: str, *, asset: str = "STOCK",
                   days=None, closes=None, preclose: dict | None = None,
                   daily_scale: dict | None = None, minute_scale: float = 1.0,
                   skip_days=(), null_bars=(), drop_bars=(),
                   freqs=("1min",)):
    """写入 stored 日线 + 分钟（含 back 列/ratio/估值等**不得被触碰**的列）。

    days/closes：交易日与收盘价（默认 DAYS5/RAW_CLOSE）。
    daily_scale：day_ms→stored 日线 front 因子（默认 1.0 = 陈旧 front==raw）。
    minute_scale：stored 分钟 front 因子（默认 1.0 = 陈旧）。
    null_bars：[(day_str,h,m)] 该 bar OHLC 与 front 全 NULL（不伪造回归用）。
    drop_bars：[(day_str,h,m)] 完全不落库该 bar（构造连续竞价 bar 不足）。
    freqs：写入的分钟频率（1min 用 BAR_CLOCKS，5min 用 BAR_CLOCKS_5MIN）。
    """
    days = list(days or DAY_MS)
    closes = closes or RAW_CLOSE
    daily_scale = daily_scale or {}
    preclose = preclose or PRECLOSE_EXD5
    daily_t = "stock_daily" if asset == "STOCK" else "etf_daily"
    minute_t = "stock_minutes" if asset == "STOCK" else "etf_minutes"
    null_set = {_at(d, h, m, 0) for d, h, m in null_bars}
    drop_set = {_at(d, h, m, 0) for d, h, m in drop_bars}

    for day in days:
        if day in skip_days:
            continue
        c = closes[day]
        s = float(daily_scale.get(day, 1.0))
        _ins(conn, daily_t, _daily_row(code, day, c, preclose[day], s, asset))
        for freq in freqs:
            clocks = BAR_CLOCKS if freq == "1min" else BAR_CLOCKS_5MIN
            for t, bo, bh, bl, bc in _bars_of_day(_day_str(day), c, clocks):
                if t in drop_set:
                    continue
                if t in null_set:
                    vals = dict(open=None, high=None, low=None, close=None,
                                open_front=None, high_front=None,
                                low_front=None, close_front=None)
                else:
                    vals = dict(open=bo, high=bh, low=bl, close=bc,
                                open_front=bo * minute_scale,
                                high_front=bh * minute_scale,
                                low_front=bl * minute_scale,
                                close_front=bc * minute_scale)
                mrow = {
                    "code": code, "time": t, "freq": freq,
                    **vals,
                    "volume": 100.0, "amount": (bc or 0) * 100.0,
                    "preClose": None, "suspendFlag": 0,
                    "open_back": (bo or 0) * 1.2, "high_back": (bh or 0) * 1.2,
                    "low_back": (bl or 0) * 1.2, "close_back": (bc or 0) * 1.2,
                    "open_front_ratio": 1.0, "high_front_ratio": 1.0,
                    "low_front_ratio": 1.0, "close_front_ratio": 1.0,
                    "dividend_type": "none", "update_time": "orig-2026-07-25",
                }
                _ins(conn, minute_t, mrow)


def _fresh_daily(code: str, target_scale: dict, *, days=None, closes=None,
                 skip_days=()) -> pd.DataFrame:
    """xtquant fresh 日线（raw 与 stored 相同；front=raw×目标因子）。"""
    days = list(days or DAY_MS)
    closes = closes or RAW_CLOSE
    rows = []
    for day in days:
        if day in skip_days:
            continue
        c = closes[day]
        o, h, l = c - 0.05, c + 0.10, c - 0.15
        s = float(target_scale[day])
        rows.append(dict(code=code, time=day, open=o, high=h, low=l, close=c,
                         open_front=o * s, high_front=h * s, low_front=l * s,
                         close_front=c * s))
    return pd.DataFrame(rows)


def _golden_minutes(target_scale: dict, *, days=None, closes=None,
                    skip_days=(), null_bars=(), freqs=("1min",)) -> pd.DataFrame:
    """fresh xtquant 分钟黄金数据：front = raw bar close × 当日目标因子。"""
    days = list(days or DAY_MS)
    closes = closes or RAW_CLOSE
    null_set = {_at(d, h, m, 0) for d, h, m in null_bars}
    rows = []
    for day in days:
        if day in skip_days:
            continue
        for freq in freqs:
            clocks = BAR_CLOCKS if freq == "1min" else BAR_CLOCKS_5MIN
            for t, _o, _h, _l, bc in _bars_of_day(_day_str(day), closes[day],
                                                  clocks):
                if t in null_set:
                    continue
                rows.append(dict(time=t, freq=freq,
                                 close_front=bc * float(target_scale[day])))
    return pd.DataFrame(rows)


def _snap(conn, table: str) -> pd.DataFrame:
    order = "code, time, freq" if table.endswith("_minutes") else "code, time"
    return conn.execute(f"SELECT * FROM {table} ORDER BY {order}").df()


def _assert_nonfront_unchanged(pre: pd.DataFrame, post: pd.DataFrame):
    """除四个 front 列外，全表逐列逐值一致（原始 OHLC/preClose/pctChg/volume 等未变证明）。"""
    assert list(pre.columns) == list(post.columns)
    assert len(pre) == len(post)
    for col in pre.columns:
        if col in FRONT_COLS:
            continue
        pd.testing.assert_series_equal(pre[col], post[col], check_names=False), col


def _minute_front(conn, table: str, code: str, freq: str = "1min"):
    rows = conn.execute(
        f"SELECT time, open_front, high_front, low_front, close_front "
        f"FROM {table} WHERE code=? AND freq=? ORDER BY time",
        [code, freq]).fetchall()
    return {int(r[0]): r[1:] for r in rows}


def _anchor_rows(conn, code: str):
    return conn.execute(
        "SELECT anchor_version, status, last_event_id FROM qfq_anchor_state "
        "WHERE code=?", [code]).fetchall()


def _event_rows(conn, code: str):
    return conn.execute(
        "SELECT event_id, status, block_reason, minute_ratio_plan "
        "FROM qfq_reanchor_event WHERE code=? ORDER BY created_at", [code]).fetchall()


def _make_env(tmp_path, open_days):
    main = tmp_path / "quantstudio.db"
    SCHEMA.init_all_from_paths(main_db=main)
    conn = duckdb.connect(str(main))
    for t in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
        conn.execute(PRICE_DDL[t])
    svc = CalendarService(main_db=main,
                          calendar_provider=_FakeCalendarProvider(open_days))
    _persist_full_window(svc, open_days)
    return SimpleNamespace(conn=conn, calendar=svc, main=main)


@pytest.fixture()
def env(tmp_path):
    e = _make_env(tmp_path, OPEN_DAYS)
    yield e
    e.conn.close()


REAL_OPEN_DAYS = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
                  "2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22",
                  "2026-07-23", "2026-07-24"]


@pytest.fixture()
def renv(tmp_path):
    e = _make_env(tmp_path, REAL_OPEN_DAYS)
    yield e
    e.conn.close()


def _scales_exd5(f: float) -> dict:
    """D5 除权：D1..D4 目标因子 f，D5=1.0。"""
    return {D1: f, D2: f, D3: f, D4: f, D5: 1.0}


# ===========================================================================
# 1. 日线 staged fresh update：只动四个 front 列（合成结构测试）
# ===========================================================================
class TestDailyStagedUpdate:

    @pytest.mark.parametrize("code,f,pre5", [("600875", F_600875, 9.8),
                                             ("600039", F_600039, 10.0)])
    def test_committed_only_four_front_cols_change(self, env, code, f, pre5):
        """合成结构：除息伪跳空消失 + 其它列逐值未变。

        合成收益期望（勘误，两案例不同）：
          600875: 9.9/9.8-1  = +1.020408%
          600039: 9.9/10.0-1 = -1.000000%
        """
        conn, cal = env.conn, env.calendar
        preclose = dict(PRECLOSE_EXD5)
        preclose[D5] = pre5
        _seed_security(conn, code, preclose=preclose)
        _seed_security(conn, "000001", preclose=PRECLOSE_EXD5)  # 对照证券：全程不许动
        scales = _scales_exd5(f)

        # 修复前：front 链在 D5 出现伪跳空（front==raw → 9.9/10.3-1 ≈ -3.88%）
        fake_gap = RAW_CLOSE[D5] / RAW_CLOSE[D4] - 1.0
        true_ret = RAW_CLOSE[D5] / pre5 - 1.0
        assert abs(fake_gap - true_ret) > 0.02

        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            freqs=("1min",), golden_minutes=_golden_minutes(scales),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        assert res.daily_rows_updated == 5

        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # —— 列级证明：除 4 个 front 列外全部逐值未变（两张表）——
        _assert_nonfront_unchanged(pre_d, post_d)
        _assert_nonfront_unchanged(pre_m, post_m)

        # —— 日线 front 逐值 = staged fresh（D1..D4 raw×f；D5 raw×1）——
        for day in DAY_MS:
            c = RAW_CLOSE[day]
            got = conn.execute(
                "SELECT open_front, high_front, low_front, close_front "
                "FROM stock_daily WHERE code=? AND time=?", [code, day]).fetchone()
            s = scales[day]
            exp = ((c - 0.05) * s, (c + 0.10) * s, (c - 0.15) * s, c * s)
            assert got == pytest.approx(exp, rel=1e-12)

        # —— 分钟 front = raw×当日目标因子 ——
        fmap = _minute_front(conn, "stock_minutes", code)
        for day in DAY_MS:
            for t, bo, bh, bl, bc in _bars_of_day(_day_str(day), RAW_CLOSE[day]):
                s = scales[day]
                assert fmap[t] == pytest.approx(
                    (bo * s, bh * s, bl * s, bc * s), rel=1e-12)

        # —— 伪跳空消失：front 链 D5 收益 == close/preClose 真实收益 ——
        cf4, cf5 = (conn.execute(
            "SELECT close_front FROM stock_daily WHERE code=? AND time=?",
            [code, d]).fetchone()[0] for d in (D4, D5))
        assert cf5 / cf4 - 1.0 == pytest.approx(true_ret, abs=1e-9)

        # —— 对照证券 000001 完全未动 ——
        pd.testing.assert_frame_equal(
            pre_d[pre_d["code"] == "000001"].reset_index(drop=True),
            post_d[post_d["code"] == "000001"].reset_index(drop=True))
        pd.testing.assert_frame_equal(
            pre_m[pre_m["code"] == "000001"].reset_index(drop=True),
            post_m[post_m["code"] == "000001"].reset_index(drop=True))

        # —— 事件 + anchor 同事务提交；staged 临时表已清理 ——
        evs = _event_rows(conn, code)
        assert [e[1] for e in evs] == ["committed"]
        plan = json.loads(evs[0][3])
        assert "1min" in plan and any(s["needs_update"] for s in plan["1min"])
        assert _anchor_rows(conn, code) == [(1, "ok", res.event_id)]
        tabs = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert f"qfq_staged_fresh_daily_{code}" not in tabs
        # postcheck 六项齐全 + chain_start=D1（证券数据起点唯一豁免，显式记录）
        assert set(res.postchecks) == {
            "daily_staged_match", "front_chain_return", "scale_consistency",
            "kline_relation", "row_conservation", "cross_table_overlap"}
        assert res.postchecks["front_chain_return"]["chain_start"] == D1
        dsm = res.postchecks["daily_staged_match"]
        assert dsm == {"staged_count": 5, "matched": 5,
                       "missing_target": 0, "mismatch": 0}

    def test_no_insert_no_delete(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre = (conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0],
               conn.execute("SELECT COUNT(*) FROM stock_minutes").fetchone()[0])
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        post = (conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM stock_minutes").fetchone()[0])
        assert pre == post
        rc = res.postchecks["row_conservation"]
        assert rc["pre"] == rc["post"]


# ===========================================================================
# 2. 分钟 R 方法 B（合成结构测试）
# ===========================================================================
class TestMethodB:

    def test_single_segment_exact_changepoint(self, env):
        """分段边界 = R 序列精确变点（D5 首个 bar），非事件日 ±1 模糊边界。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        segs = res.plans["1min"]
        assert len(segs) == 2
        upd, noop = segs[0], segs[1]
        assert upd.needs_update and upd.ratio == pytest.approx(F_600875, rel=1e-9)
        assert not noop.needs_update and noop.ratio == pytest.approx(1.0, rel=1e-9)
        first_bar_d1 = _at(DAYS5[0], 9, 30, 0)
        first_bar_d5 = _at(DAYS5[4], 9, 30, 0)
        assert upd.t_start == first_bar_d1
        assert upd.t_end == first_bar_d5          # 左闭右开精确变点
        assert noop.t_start == first_bar_d5
        assert upd.dispersion <= 5e-4
        assert upd.bar_count == 4 * len(BAR_CLOCKS)

    def test_002864_daily_correct_minute_stale(self, env):
        """002864 合成结构：daily fresh/stored≈1 也必须触发分钟修正（R≈0.7625）。"""
        conn, cal = env.conn, env.calendar
        scales = {d: F_002864 for d in DAY_MS}     # 窗口内无除权，因子恒定
        # stored 日线 front 已正确（=raw×0.7625），分钟 front 陈旧（=raw）
        _seed_security(conn, "002864", daily_scale=scales, minute_scale=1.0,
                       preclose=PRECLOSE_PLAIN)
        pre_d = _snap(conn, "stock_daily")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="002864",
            fresh_daily=_fresh_daily("002864", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), list_date_ms=D1)
        assert res.status == "committed"
        segs = [s for s in res.plans["1min"] if s.needs_update]
        assert len(segs) == 1
        assert segs[0].ratio == pytest.approx(F_002864, rel=1e-9)
        # daily fresh 与 stored 几乎一致 → daily 数值不变，但分钟必须被修正
        post_d = _snap(conn, "stock_daily")
        pd.testing.assert_frame_equal(pre_d, post_d)   # 日线值不变（本来已正确）
        fmap = _minute_front(conn, "stock_minutes", "002864")
        for day in DAY_MS:
            for t, bo, bh, bl, bc in _bars_of_day(_day_str(day), RAW_CLOSE[day]):
                assert fmap[t] == pytest.approx(
                    (bo * F_002864, bh * F_002864, bl * F_002864, bc * F_002864),
                    rel=1e-12)
        assert _anchor_rows(conn, "002864") == [(1, "ok", res.event_id)]

    def test_coverage_gap_blocks(self, env):
        """staged span 内存在无 fresh 日线的交易日 → 整券 BLOCK，不动任何数据。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales, skip_days=(D3,)),
            calendar=cal, golden_minutes=_golden_minutes(scales))
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_coverage_gap"
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["blocked"]

    def test_multi_cluster_blocked_without_flag(self, env):
        """两个需修正比率簇（两次除权补账）：非 bootstrap → BLOCK。"""
        conn, cal = env.conn, env.calendar
        pre3, pre5 = 9.5, 9.8
        preclose = {D1: 9.95, D2: 10.0, D3: pre3, D4: 10.2, D5: pre5}
        _seed_security(conn, "600111", preclose=preclose)
        f_b = pre5 / 10.3
        f_a = (pre3 / 10.1) * f_b
        scales = {D1: f_a, D2: f_a, D3: f_b, D4: f_b, D5: 1.0}
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600111",
            fresh_daily=_fresh_daily("600111", scales), calendar=cal)
        assert res.status == "blocked"
        assert res.block_reason == "ratio_multi_cluster"
        assert _anchor_rows(conn, "600111") == []

    def test_null_bars_not_fabricated(self, env):
        """NULL bar 跳过计算且修正后仍为 NULL（不伪造）。"""
        conn, cal = env.conn, env.calendar
        nb = [("2026-07-21", 10, 30)]
        # 追加一根全 NULL bar（额外时间点，避开采样 bar）
        _seed_security(conn, "600875", null_bars=())
        _ins(conn, "stock_minutes", {
            "code": "600875", "time": _at(*nb[0], 0), "freq": "1min",
            "open": None, "high": None, "low": None, "close": None,
            "open_front": None, "high_front": None, "low_front": None,
            "close_front": None, "volume": 0.0, "amount": 0.0,
            "dividend_type": "none", "update_time": "orig"})
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        got = conn.execute(
            "SELECT open, close, open_front, close_front FROM stock_minutes "
            "WHERE code='600875' AND time=?", [_at(*nb[0], 0)]).fetchone()
        assert got == (None, None, None, None)

    def test_ohlc_scale_inconsistent_blocks(self, env):
        """stored 分钟 open_front 缩放与 close 缩放矛盾 → BLOCK。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        t_bad = _at("2026-07-21", 10, 0, 0)
        conn.execute("UPDATE stock_minutes SET open_front = open_front * 1.5 "
                     "WHERE code='600875' AND time=?", [t_bad])
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales))
        assert res.status == "blocked"
        assert res.block_reason == "minute_ohlc_scale_inconsistent"
        assert _anchor_rows(conn, "600875") == []


# ===========================================================================
# 3. 方法 A 黄金抽验（含严格硬下限反例）
# ===========================================================================
class TestGoldenCheck:

    def test_sample_coverage(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        rep = res.golden_report["1min"]
        kinds = {s["kind"] for s in rep["samples"]}
        assert "representative" in kinds and "auction_extra" in kinds
        # 区间首/尾覆盖按时间断言（首/尾 bar 可能已被 auction/representative 先采样去重）
        times = {s["time"] for s in rep["samples"]}
        assert _at(DAYS5[0], 9, 30, 0) in times      # 修正区间首 bar
        assert _at(DAYS5[3], 15, 0, 0) in times      # 修正区间尾 bar（D4 收盘）
        # ≥3 交易日 × ≥5 连续竞价代表 bar（严格下限）
        days = {pd.Timestamp(s["time"], unit="ms", tz=TZ).strftime("%Y-%m-%d")
                for s in rep["samples"] if s["kind"] == "representative"}
        assert len(days) >= 3
        n_rep = sum(1 for s in rep["samples"] if s["kind"] == "representative")
        assert n_rep >= 3 * 5
        # 除权日前一交易日（D4）必被覆盖
        assert DAYS5[3] in {pd.Timestamp(s["time"], unit="ms", tz=TZ)
                            .strftime("%Y-%m-%d") for s in rep["samples"]}
        assert rep["max_dev"] <= 1e-9

    def test_golden_mismatch_blocks_entire_security(self, env):
        """单个样本超容差 → 整券 BLOCK（不取平均），数据完全回滚。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        g = _golden_minutes(scales)
        t0 = _at(DAYS5[0], 9, 31, 0)                 # 必然入样的连续竞价代表 bar
        g.loc[g["time"] == t0, "close_front"] *= 1.01
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=g, ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "blocked"
        assert res.block_reason == "golden_mismatch"
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["blocked"]

    def test_golden_missing_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", _scales_exd5(F_600875)),
            calendar=cal, golden_minutes=None)
        assert res.status == "blocked"
        assert res.block_reason == "golden_data_missing"

    def test_noop_needs_no_golden(self, env):
        """全 noop（fresh 与 stored 完全一致）：无需黄金数据也可提交，分钟不动。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600519", preclose=PRECLOSE_PLAIN)
        pre_m = _snap(conn, "stock_minutes")
        scales = {d: 1.0 for d in DAY_MS}
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600519",
            fresh_daily=_fresh_daily("600519", scales), calendar=cal,
            golden_minutes=None, list_date_ms=D1)
        assert res.status == "committed"
        assert all(not s.needs_update for s in res.plans["1min"])
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600519") == [(1, "ok", res.event_id)]


class TestGoldenStrictFloor:
    """对抗反例 #1：抽验下限必须是严格硬下限，不得"有多少抽多少"。"""

    def test_needs_segment_with_two_trading_days_blocks(self, env):
        """需修正段只有 2 个真实交易日 → 整券 BLOCK（此前误 committed）。"""
        conn, cal = env.conn, env.calendar
        preclose = {D1: 9.95, D2: 10.0, D3: 10.1, D4: 10.2 * 0.9, D5: 10.3 * 0.9}
        _seed_security(conn, "600222", preclose=preclose)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # D4 除权后重锚：仅 D4/D5 需修正（2 个交易日 < 硬下限 3）
        scales = {D1: 1.0, D2: 1.0, D3: 1.0, D4: 0.9, D5: 0.9}
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600222",
            fresh_daily=_fresh_daily("600222", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales))
        assert res.status == "blocked"
        assert res.block_reason == "golden_insufficient"
        assert "2 个真实交易日" in (res.error or "")
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600222") == []
        assert [e[1] for e in _event_rows(conn, "600222")] == ["blocked"]

    def test_sampled_day_with_four_cont_bars_blocks(self, env):
        """抽验日只有 4 根有效连续竞价 bar → BLOCK；09:30 不得补足。"""
        conn, cal = env.conn, env.calendar
        # D1（必为抽验首日）删掉一根连续竞价 bar → 连续竞价仅 4 根；09:30 仍在
        _seed_security(conn, "600333", drop_bars=[("2026-07-20", 10, 0)])
        pre_m = _snap(conn, "stock_minutes")
        n_d1 = conn.execute(
            "SELECT COUNT(*) FROM stock_minutes WHERE code='600333' "
            "AND time BETWEEN ? AND ?",
            [_at("2026-07-20", 9, 30, 0), _at("2026-07-20", 15, 0, 0)]).fetchone()[0]
        assert n_d1 == 5      # 09:30 + 4 根连续竞价 = 5（若 09:30 可补足则不会 BLOCK）
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600333",
            fresh_daily=_fresh_daily("600333", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "blocked"
        assert res.block_reason == "golden_insufficient"
        assert "仅 4 根" in (res.error or "")
        assert "09:30" in (res.error or "")
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600333") == []

    def test_bootstrap_two_day_segments_block(self, env):
        """（期望纠正）bootstrap 双段各 2 交易日：即使变点全部可解释，
        仍因黄金抽验硬下限（<3 日）→ 整券 BLOCK。"""
        conn, cal = env.conn, env.calendar
        pre3, pre5 = 9.5, 9.8
        preclose = {D1: 9.95, D2: 10.0, D3: pre3, D4: 10.2, D5: pre5}
        _seed_security(conn, "600111", preclose=preclose)
        f_b = pre5 / 10.3
        f_a = (pre3 / 10.1) * f_b
        scales = {D1: f_a, D2: f_a, D3: f_b, D4: f_b, D5: 1.0}
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600111",
            fresh_daily=_fresh_daily("600111", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales),
            ex_dates_ms=(D3, D5), allow_multi_segment=True)
        assert res.status == "blocked"
        assert res.block_reason == "golden_insufficient"
        assert "2 个真实交易日" in (res.error or "")
        assert _anchor_rows(conn, "600111") == []


# ===========================================================================
# 3b. session-aware 连续竞价窗口（第三轮对抗审核反例）
# ===========================================================================

def _ins_extra_bar(conn, code: str, day_str: str, h: int, m: int, close: float):
    """插入一根额外分钟 bar（OHLC/front 自洽，front 陈旧=raw）。"""
    t = _at(day_str, h, m, 0)
    _ins(conn, "stock_minutes", {
        "code": code, "time": t, "freq": "1min",
        "open": close - 0.01, "high": close + 0.02, "low": close - 0.03,
        "close": close,
        "open_front": close - 0.01, "high_front": close + 0.02,
        "low_front": close - 0.03, "close_front": close,
        "volume": 100.0, "amount": close * 100.0,
        "dividend_type": "none", "update_time": "orig-2026-07-25"})
    return t


class TestSessionAwareContWindow:
    """（第三轮）连续竞价合法窗口 = [09:31,11:30] ∪ [13:01,15:00]（end-labeled）。

    此前 [571,900] 单一区间把 11:31–12:59 午间休市计为有效连续竞价：
    - 4 根真实连续竞价 bar + 1 根 12:00 bar 被凑成 5 根 → 误 committed；
    - 12:00 bar 可被选为黄金样本；
    - 仅 09:30 bar 的共存日 cross_table_overlap checked=0 仍 committed。"""

    def test_cont_clock_set_cadence(self):
        """频率 cadence 单元断言：合法 end-labeled 收盘时刻集合。"""
        c1 = ENG._cont_clock_set("1min")
        c5 = ENG._cont_clock_set("5min")
        assert len(c1) == 240 and len(c5) == 48
        for cs in (c1, c5):
            assert 12 * 60 not in cs                    # 午间 12:00
            assert 11 * 60 + 31 not in cs               # 11:31（午间开始）
            assert 12 * 60 + 59 not in cs               # 12:59
            assert 9 * 60 + 30 not in cs                # 集合竞价 09:30
            assert 13 * 60 not in cs                    # 13:00（下午开盘时刻本身）
            assert 15 * 60 + 1 not in cs
        assert {9 * 60 + 31, 11 * 60 + 30, 13 * 60 + 1, 15 * 60} <= c1
        assert {9 * 60 + 35, 11 * 60 + 30, 13 * 60 + 5, 15 * 60} <= c5
        assert 9 * 60 + 31 not in c5                    # 偏离 5min cadence
        assert 13 * 60 + 1 not in c5

    def test_midday_bar_cannot_pad_cont_floor(self, env):
        """反例：4 根真实连续竞价 bar + 1 根 12:00 午间 bar → 仍只算 4 根，
        黄金硬下限 BLOCK（此前 12:00 落在 [571,900] 被凑成 5 根误 committed）。"""
        conn, cal = env.conn, env.calendar
        # D1 删一根连续竞价 bar（余 4 根）+ 插一根 12:00 午间 bar
        _seed_security(conn, "600661", drop_bars=[("2026-07-20", 10, 0)])
        _ins_extra_bar(conn, "600661", "2026-07-20", 12, 0, RAW_CLOSE[D1])
        pre_m = _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600661",
            fresh_daily=_fresh_daily("600661", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "blocked"
        assert res.block_reason == "golden_insufficient"
        assert "仅 4 根" in (res.error or "")
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600661") == []

    def test_midday_bar_never_selected_as_golden_sample(self, env):
        """反例：存在 12:00 bar 且黄金数据**不含**该时刻 → 若引擎仍把午间
        bar 当连续竞价样本，会因黄金缺样本 BLOCK；session-aware 后正常
        committed 且样本中无任何午间时刻。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600662")                   # 5 根合法连续竞价 bar 齐全
        mids = [_ins_extra_bar(conn, "600662", d, 12, 0, RAW_CLOSE[_at(d, 0, 0, 0)])
                for d in DAYS5]
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600662",
            fresh_daily=_fresh_daily("600662", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales),      # 不含 12:00 时刻
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        stimes = {s["time"] for s in res.golden_report["1min"]["samples"]}
        assert stimes.isdisjoint(set(mids))              # 午间 bar 绝不入样
        # 午间 bar 属于修正区间 → front 仍按段 R 修正（数据本身要改对）
        got = conn.execute(
            "SELECT close_front FROM stock_minutes WHERE code='600662' AND time=?",
            [mids[0]]).fetchone()[0]
        assert got == pytest.approx(RAW_CLOSE[D1] * F_600875, rel=1e-9)

    def test_only_auction_bar_day_rolls_back(self, env):
        """反例：某共存日分钟仅剩 09:30 集合竞价 bar → cross_table_overlap
        必须回滚（此前 checked=0 仍 committed 静默通过）。"""
        conn, cal = env.conn, env.calendar
        # D5 删光全部连续竞价 bar，仅剩 09:30（D5 为 noop 段，不影响黄金下限）
        _seed_security(conn, "600663", drop_bars=[
            ("2026-07-24", 9, 31), ("2026-07-24", 10, 0), ("2026-07-24", 13, 1),
            ("2026-07-24", 14, 59), ("2026-07-24", 15, 0)])
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600663",
            fresh_daily=_fresh_daily("600663", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "cross_table_overlap"
        assert "无有效连续竞价" in (res.error or "")
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600663") == []
        assert [e[1] for e in _event_rows(conn, "600663")] == ["rolled_back"]

    def test_5min_session_independent_of_1min(self, env):
        """1min/5min session 对照：各自按自身 cadence 判定合法连续竞价时刻，
        双 freq 同时修正互不串扰（BAR_CLOCKS_5MIN 的 09:35/13:05 合法，
        但它们不是 1min 集合之外多余时刻的借口）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600664", freqs=("1min", "5min"))
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600664",
            fresh_daily=_fresh_daily("600664", scales), calendar=cal,
            freqs=("1min", "5min"),
            golden_minutes=_golden_minutes(scales, freqs=("1min", "5min")),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        for fq in ("1min", "5min"):
            stimes = {s["time"] for s in res.golden_report[fq]["samples"]}
            cs = ENG._cont_clock_set(fq)
            for t in stimes:
                ts = pd.Timestamp(t, unit="ms", tz=TZ)
                cm = ts.hour * 60 + ts.minute
                assert cm == 570 or cm in cs, (fq, ts)   # 09:30 或合法 cadence


# ===========================================================================
# 4. front-chain：首个 staged 交易日必须校验范围外真实上一交易日（对抗反例 #2）
# ===========================================================================
class TestFrontChainFirstDay:

    def test_window_from_d2_stale_d1_rolls_back(self, env):
        """修正窗口从 D2 开始、D1 front 陈旧：D2/D1 front-chain 偏差必须被抓住
        → rolled_back（此前 prev_td < span_lo 被 continue 跳过、误 committed）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600444")            # D1..D5 全部落库，front 陈旧
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(       # fresh 窗口只覆盖 D2..D5
            conn, asset_type="STOCK", code="600444",
            fresh_daily=_fresh_daily("600444", scales, skip_days=(D1,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D1,)),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "front_chain_return"
        # 全量回滚：包括已执行的 D2..D4 分钟/日线 front UPDATE
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600444") == []
        assert [e[1] for e in _event_rows(conn, "600444")] == ["rolled_back"]

    def test_window_from_d2_missing_prev_row_rolls_back(self, env):
        """范围外真实上一交易日 D1 无日线行（而证券有更早历史）→
        front_chain_missing_prev，不得静默当停牌。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600555", skip_days=(D1,))    # D1 日线+分钟都缺
        # 更早历史存在（D0=2026-07-17）→ 排除"数据起点"豁免
        _ins(conn, "stock_daily", _daily_row("600555", D0, 9.9, 9.85, 1.0))
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600555",
            fresh_daily=_fresh_daily("600555", scales, skip_days=(D1,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D1,)),
            ex_dates_ms=(D5,))       # 无 list_date 证据（证券实际上市早于 D0）
        assert res.status == "rolled_back"
        assert res.block_reason == "front_chain_missing_prev"
        assert str(D1) in (res.error or "")
        assert _anchor_rows(conn, "600555") == []

    def test_truncated_data_not_mistaken_for_list_date(self, env):
        """（第三轮对抗审核反例）截断数据不得被误当上市首日：
        本地表删除 D1 只保留 D2..D5，CalendarService 知道 D1 是 D2 的真实
        上一交易日 —— 无 security master list_date 证据时**必须**
        front_chain_missing_prev 回滚，绝不允许 committed。
        此前 MIN(time)==t 通用豁免会把 D2 误判为数据起点而静默放行。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600123", skip_days=(D1,))   # 截断：D1 日线+分钟全缺
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600123",
            fresh_daily=_fresh_daily("600123", scales, skip_days=(D1,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D1,)),
            ex_dates_ms=(D5,))                            # 无 list_date_ms 证据
        assert res.status == "rolled_back"
        assert res.block_reason == "front_chain_missing_prev"
        assert str(D1) in (res.error or "")               # 指认缺失的真实上一交易日
        assert "list_date" in (res.error or "")           # 指明唯一豁免凭据
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600123") == []

        # 错误的 list_date 证据（声称上市日=D1 但数据始于 D2）同样回滚
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600123",
            fresh_daily=_fresh_daily("600123", scales, skip_days=(D1,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D1,)),
            ex_dates_ms=(D5,), list_date_ms=D1)           # 证据与数据首日不符
        assert res2.status == "rolled_back"
        assert res2.block_reason == "front_chain_missing_prev"

    def test_calendar_unprovable_prev_fails_closed(self, env):
        """（第三轮）staged 首日 = provider 已知最早开市日：日历**无法证明**
        其真实上一交易日（向前延伸持续为空）→ 必须确定性回滚
        front_chain_missing_prev，绝不允许 LookupError 崩穿或静默 committed；
        仅 security master list_date 证据可豁免。"""
        conn, cal = env.conn, env.calendar
        p_days = [_at(d, 0, 0, 0) for d in OPEN_DAYS[:5]]   # 07-14 起（最早开市日）
        closes = {d: 10.0 + 0.1 * i for i, d in enumerate(p_days)}
        preclose = {p_days[0]: 9.95}
        for i in range(1, 5):
            preclose[p_days[i]] = closes[p_days[i - 1]]
        _seed_security(conn, "600125", days=p_days, closes=closes,
                       preclose=preclose)
        scales = {d: 1.0 for d in p_days}                   # 全 noop（无需黄金）
        fresh = _fresh_daily("600125", scales, days=p_days, closes=closes)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600125",
            fresh_daily=fresh, calendar=cal, golden_minutes=None)
        assert res.status == "rolled_back"                  # 不是 crash
        assert res.block_reason == "front_chain_missing_prev"
        assert "无法确认" in (res.error or "")
        assert _anchor_rows(conn, "600125") == []
        # 提供 list_date 明确证据（07-14 为上市首日）→ 豁免生效
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600125",
            fresh_daily=fresh, calendar=cal, golden_minutes=None,
            list_date_ms=p_days[0])
        assert res2.status == "committed"
        assert res2.postchecks["front_chain_return"]["chain_start"] == p_days[0]

    def test_list_date_evidence_allows_true_first_day(self, env):
        """正例：security master 证据 list_date_ms==D2 且数据恰始于 D2 →
        chain_start 豁免生效并被显式记录（唯一合法豁免路径）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600124", skip_days=(D1,))
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600124",
            fresh_daily=_fresh_daily("600124", scales, skip_days=(D1,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D1,)),
            ex_dates_ms=(D5,), list_date_ms=D2)           # 上市首日=D2（明确证据）
        assert res.status == "committed"
        assert res.postchecks["front_chain_return"]["chain_start"] == D2

    def test_missing_prev_inside_window_rolls_back(self, env):
        """窗口内部缺真实上一交易日（缺 D3）→ 同样 front_chain_missing_prev。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875", skip_days=(D3,))   # 日线+分钟都缺 D3
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales, skip_days=(D3,)),
            calendar=cal,
            golden_minutes=_golden_minutes(scales, skip_days=(D3,)),
            ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "front_chain_missing_prev"
        assert _anchor_rows(conn, "600875") == []


# ===========================================================================
# 5. staged daily 全覆盖（对抗反例 #3）
# ===========================================================================
class TestStagedFullCoverage:

    def test_staged_unmatched_row_rolls_back(self, env):
        """staged 6 行、正式日线只命中 5 行 → 必须回滚（此前静默忽略误 committed）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600666")            # 正式日线只有 D1..D5
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        fresh = _fresh_daily("600666", scales)
        # staged 追加真实交易日 D0=2026-07-17（正式日线无此行 → 未命中）
        extra = pd.DataFrame([dict(
            code="600666", time=D0, open=9.85, high=10.0, low=9.75, close=9.9,
            open_front=9.85 * F_600875, high_front=10.0 * F_600875,
            low_front=9.75 * F_600875, close_front=9.9 * F_600875)])
        fresh6 = pd.concat([extra, fresh], ignore_index=True)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600666",
            fresh_daily=fresh6, calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "daily_staged_match"
        assert "staged_count=6" in (res.error or "")
        assert "matched=5" in (res.error or "")
        assert "missing_target=1" in (res.error or "")
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600666") == []
        assert [e[1] for e in _event_rows(conn, "600666")] == ["rolled_back"]

    def test_staged_non_trading_day_rolls_back(self, env):
        """staged 含非交易日（周六）行 → daily_staged_match 回滚。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600777")
        # 存储侧被污染出一条周六日线（front 陈旧）
        _ins(conn, "stock_daily", _daily_row("600777", D_SAT, 10.05, 10.0, 1.0))
        scales = _scales_exd5(F_600875)
        fresh = _fresh_daily("600777", scales)
        sat = pd.DataFrame([dict(
            code="600777", time=D_SAT, open=10.0, high=10.15, low=9.9,
            close=10.05, open_front=10.0 * F_600875,
            high_front=10.15 * F_600875, low_front=9.9 * F_600875,
            close_front=10.05 * F_600875)])
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600777",
            fresh_daily=pd.concat([sat, fresh], ignore_index=True), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "daily_staged_match"
        assert "非交易日" in (res.error or "")
        assert _anchor_rows(conn, "600777") == []


# ===========================================================================
# 6. canonical freq（对抗反例 #4）
# ===========================================================================
class TestCanonicalFreqs:

    def test_freq_alias_1m_canonicalized(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1m",),                       # 别名 → canonical 1min
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        assert list(res.plans) == ["1min"]
        assert all(s.freq == "1min" for s in res.plans["1min"])

    @pytest.mark.parametrize("freqs", [("1m", "1min"), ("1min", "1min")])
    def test_duplicate_alias_dedup_no_plan_overwrite(self, env, freqs):
        """重复 freq 别名：只跑一轮；真实 ratio plan 不被 noop 覆盖；
        分钟只按 R 修正一次（否则值会变成 raw×R²）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=freqs,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        assert list(res.plans) == ["1min"]
        upd = [s for s in res.plans["1min"] if s.needs_update]
        assert len(upd) == 1
        assert upd[0].ratio == pytest.approx(F_600875, rel=1e-9)   # 非 noop 覆盖
        # 分钟值 = raw×R（只修正一次，未被第二轮重复乘）
        t = _at(DAYS5[0], 15, 0, 0)
        got = conn.execute(
            "SELECT close_front FROM stock_minutes WHERE code='600875' "
            "AND freq='1min' AND time=?", [t]).fetchone()[0]
        assert got == pytest.approx(RAW_CLOSE[D1] * F_600875, rel=1e-12)
        # committed event 的审计计划 = 真实 ratio plan（不失真）
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["committed"]
        plan = json.loads(evs[0][3])
        assert list(k for k in plan if not k.startswith("changepoint")
                    and not k.startswith("model")) == ["1min"]
        assert plan["model"] == "ratio"          # 模型选择显式写入事件审计
        assert any(s["needs_update"] and
                   abs(s["ratio"] - F_600875) < 1e-9 for s in plan["1min"])
        # 行数守恒明细共用同一 canonical 列表（只有一个 minute_code@1min 键）
        rc_keys = [k for k in res.rows if k.startswith("minute_code@")]
        assert rc_keys == ["minute_code@1min"]

    def test_1min_5min_independent_plans(self, env):
        """1min + 5min：两个 canonical freq 独立计算/独立 UPDATE/独立审计。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875", freqs=("1min", "5min"))
        scales = _scales_exd5(F_600875)
        golden = _golden_minutes(scales, freqs=("1min", "5min"))
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1m", "5min"),
            golden_minutes=golden, ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        assert list(res.plans) == ["1min", "5min"]
        for fq in ("1min", "5min"):
            upd = [s for s in res.plans[fq] if s.needs_update]
            assert len(upd) == 1
            assert upd[0].freq == fq
            assert upd[0].ratio == pytest.approx(F_600875, rel=1e-9)
            assert upd[0].bar_count == 4 * 6
        # 两个 freq 的分钟 bar 各自被正确修正
        for fq, clocks in (("1min", BAR_CLOCKS), ("5min", BAR_CLOCKS_5MIN)):
            fmap = _minute_front(conn, "stock_minutes", "600875", fq)
            for day in DAY_MS:
                s = scales[day]
                for t, bo, bh, bl, bc in _bars_of_day(
                        _day_str(day), RAW_CLOSE[day], clocks):
                    assert fmap[t] == pytest.approx(
                        (bo * s, bh * s, bl * s, bc * s), rel=1e-12)
        # 行数/审计按 freq 独立
        assert res.rows["minute_code@1min"] == 5 * 6
        assert res.rows["minute_code@5min"] == 5 * 6
        plan = json.loads(_event_rows(conn, "600875")[0][3])
        assert set(k for k in plan if not k.startswith("changepoint")
                   and not k.startswith("model")) == {"1min", "5min"}

    def test_daily_freq_rejected(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        with pytest.raises(ValueError):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875",
                fresh_daily=_fresh_daily("600875", _scales_exd5(F_600875)),
                calendar=cal, freqs=("daily",))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["failed"]

    def test_etf_smoke(self, env):
        conn, cal = env.conn, env.calendar
        code = "510300"
        _seed_security(conn, code, asset="ETF")
        pre_d, pre_m = _snap(conn, "etf_daily"), _snap(conn, "etf_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="ETF", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "committed"
        _assert_nonfront_unchanged(pre_d, _snap(conn, "etf_daily"))
        _assert_nonfront_unchanged(pre_m, _snap(conn, "etf_minutes"))
        fmap = _minute_front(conn, "etf_minutes", code)
        t = _at(DAYS5[0], 15, 0, 0)
        assert fmap[t][3] == pytest.approx(RAW_CLOSE[D1] * F_600875, rel=1e-12)
        # 股票表未被 ETF 修正波及
        assert conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0] == 0


# ===========================================================================
# 7. bootstrap 多簇逐变点解释（对抗反例 #5）
# ===========================================================================

def _seed_ext(conn, code):
    _seed_security(conn, code, days=EXT_MS, closes=EXT_CLOSE,
                   preclose=EXT_PRECLOSE)


def _ext_fresh(code):
    return _fresh_daily(code, EXT_SCALES, days=EXT_MS, closes=EXT_CLOSE)


def _ext_golden():
    return _golden_minutes(EXT_SCALES, days=EXT_MS, closes=EXT_CLOSE)


class TestChangepointExplanation:

    def test_multicluster_without_exdates_blocks(self, env):
        """（此前反例）allow_multi_segment=True 但不传任何 ex_dates_ms：
        两个需修正簇必须 BLOCK（changepoint_unexplained），不得无条件放行。"""
        conn, cal = env.conn, env.calendar
        _seed_ext(conn, "600888")
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600888",
            fresh_daily=_ext_fresh("600888"), calendar=cal,
            golden_minutes=_ext_golden(),
            ex_dates_ms=(), allow_multi_segment=True)
        assert res.status == "blocked"
        assert res.block_reason == "changepoint_unexplained"
        assert "2/2" in (res.error or "")
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600888") == []
        assert [e[1] for e in _event_rows(conn, "600888")] == ["blocked"]

    def test_partial_explanation_blocks(self, env):
        """只能解释部分变点（只给第一个除权日）→ 整券 BLOCK。"""
        conn, cal = env.conn, env.calendar
        _seed_ext(conn, "600999")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600999",
            fresh_daily=_ext_fresh("600999"), calendar=cal,
            golden_minutes=_ext_golden(),
            ex_dates_ms=(E4,), allow_multi_segment=True)
        assert res.status == "blocked"
        assert res.block_reason == "changepoint_unexplained"
        assert "1/2" in (res.error or "")
        assert _anchor_rows(conn, "600999") == []

    def test_full_explanation_commits_with_audit(self, env):
        """两个变点均可被除权事件解释 → committed；解释写入事件审计；
        UPDATE 边界仍取 R 序列精确变点（段首 bar 时刻，非事件日±1）。"""
        conn, cal = env.conn, env.calendar
        _seed_ext(conn, "601000")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="601000",
            fresh_daily=_ext_fresh("601000"), calendar=cal,
            golden_minutes=_ext_golden(),
            ex_dates_ms=(E4, E7), allow_multi_segment=True, list_date_ms=E1)
        assert res.status == "committed"
        segs = res.plans["1min"]
        upd = [s for s in segs if s.needs_update]
        assert len(upd) == 2
        f_a, f_b = F1_EXT * F2_EXT, F2_EXT
        assert upd[0].ratio == pytest.approx(f_a, rel=1e-9)
        assert upd[1].ratio == pytest.approx(f_b, rel=1e-9)
        # 精确变点：段边界 = R 序列切换时刻（E4/E7 首个 bar 09:30）
        assert upd[0].t_end == _at(EXT_DAYS[3], 9, 30, 0)
        assert upd[1].t_start == _at(EXT_DAYS[3], 9, 30, 0)
        assert upd[1].t_end == _at(EXT_DAYS[6], 9, 30, 0)
        # 事件审计：逐变点解释证据
        plan = json.loads(_event_rows(conn, "601000")[0][3])
        exp = plan["changepoint_explanations"]["1min"]
        assert len(exp) == 2
        assert exp[0]["boundary_time"] == _at(EXT_DAYS[3], 9, 30, 0)
        assert exp[0]["boundary_day"] == E4
        assert exp[0]["explained_by_ex_date"] == E4
        assert exp[0]["prev_ratio"] == pytest.approx(f_a, rel=1e-9)
        assert exp[0]["next_ratio"] == pytest.approx(f_b, rel=1e-9)
        assert exp[1]["boundary_day"] == E7
        assert exp[1]["explained_by_ex_date"] == E7
        # 分钟数据按段正确修正
        fmap = _minute_front(conn, "stock_minutes", "601000")
        for day in EXT_MS:
            s = EXT_SCALES[day]
            for t, bo, bh, bl, bc in _bars_of_day(_day_str(day), EXT_CLOSE[day]):
                assert fmap[t] == pytest.approx(
                    (bo * s, bh * s, bl * s, bc * s), rel=1e-12)
        assert _anchor_rows(conn, "601000") == [(1, "ok", res.event_id)]


# ===========================================================================
# 8. 单证券事务 / 通用回滚故障注入
# ===========================================================================
class TestTransactionAndPostcheck:

    def test_postcheck_front_chain_rolls_back(self, env):
        """故障注入：stored preClose(D3) 破坏 → postcheck 失败 → 全量回滚。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        conn.execute("UPDATE stock_daily SET preClose = 5.0 "
                     "WHERE code='600875' AND time=?", [D3])
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == "front_chain_return"
        # 回滚证明：分钟/日线（含已执行的 front UPDATE）全部还原
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["rolled_back"]

    def test_unexpected_exception_records_failed_and_reraises(self, env, monkeypatch):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        def _boom(*a, **k):
            raise RuntimeError("injected-postcheck-crash")

        monkeypatch.setattr(ENG, "run_postchecks", _boom)
        scales = _scales_exd5(F_600875)
        with pytest.raises(RuntimeError, match="injected-postcheck-crash"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875",
                fresh_daily=_fresh_daily("600875", scales), calendar=cal,
                golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["failed"]

    def test_failure_then_success_on_same_connection(self, env):
        """失败事件用独立短事务记录；同一连接随后可正常完成修正。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        r1 = apply_reanchor_for_security(          # 先触发 blocked（缺黄金数据）
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=None)
        assert r1.status == "blocked"
        r2 = apply_reanchor_for_security(          # 再补齐 → committed
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert r2.status == "committed"
        assert [e[1] for e in _event_rows(conn, "600875")] == ["blocked", "committed"]
        assert _anchor_rows(conn, "600875") == [(1, "ok", r2.event_id)]


# ===========================================================================
# 9. 五项 postcheck 独立故障注入（对抗反例 #7）
# ===========================================================================

def _inj_daily_staged(conn):
    """事务内破坏日线 front（模拟 UPDATE 缺陷）→ daily_staged_match。"""
    conn.execute("UPDATE stock_daily SET close_front = close_front * 1.01 "
                 "WHERE code='600875' AND time=?", [D2])


def _inj_scale(conn):
    """事务内破坏单 bar open_front 缩放 → scale_consistency。"""
    conn.execute("UPDATE stock_minutes SET open_front = open_front * 1.5 "
                 "WHERE code='600875' AND freq='1min' AND time=?",
                 [_at("2026-07-21", 10, 0, 0)])


def _inj_kline(conn):
    """事务内制造 low_front > open/close_front（缩放保持一致）→ kline_relation。"""
    conn.execute(
        "UPDATE stock_minutes SET low = 20.0, low_front = ? "
        "WHERE code='600875' AND freq='1min' AND time=?",
        [20.0 * F_600875, _at("2026-07-21", 10, 0, 0)])


def _inj_row(conn):
    """事务内插入多余分钟行（15:01，缩放/K线一致）→ row_conservation。"""
    conn.execute(
        "INSERT INTO stock_minutes SELECT * REPLACE (time + 60000 AS time) "
        "FROM stock_minutes WHERE code='600875' AND freq='1min' AND time=?",
        [_at("2026-07-21", 15, 0, 0)])


def _inj_cross(conn):
    """事务内把 D2 收盘 bar 四个 front 同乘 1.005（缩放/K线仍一致）→
    与日线 front 跨表偏差 0.5% → cross_table_overlap。"""
    conn.execute(
        "UPDATE stock_minutes SET open_front = open_front * 1.005, "
        "high_front = high_front * 1.005, low_front = low_front * 1.005, "
        "close_front = close_front * 1.005 "
        "WHERE code='600875' AND freq='1min' AND time=?",
        [_at("2026-07-21", 15, 0, 0)])


class TestPostcheckInjections:

    @pytest.mark.parametrize("inject,reason", [
        (_inj_daily_staged, "daily_staged_match"),
        (_inj_scale, "scale_consistency"),
        (_inj_kline, "kline_relation"),
        (_inj_row, "row_conservation"),
        (_inj_cross, "cross_table_overlap"),
    ], ids=["daily_staged_match", "scale_consistency", "kline_relation",
            "row_conservation", "cross_table_overlap"])
    def test_each_postcheck_rolls_back_independently(self, env, monkeypatch,
                                                     inject, reason):
        """五项 postcheck 各自实际故障注入：
        全表回滚 + rolled_back 事件 + 正确 block_reason + anchor 不推进 +
        同连接随后可继续成功。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)

        orig = ENG.update_daily_front_from_staged

        def _wrapped(c, asset_type, code, staged):
            n = orig(c, asset_type, code, staged)
            inject(c)          # 事务内、postcheck 前注入
            return n

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", _wrapped)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res.status == "rolled_back"
        assert res.block_reason == reason
        # 全表回滚证明（含注入本身也被回滚）
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        # anchor 不推进 + rolled_back 事件独立短事务落库
        assert _anchor_rows(conn, "600875") == []
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["rolled_back"]
        assert evs[0][2] == reason
        # 同连接撤销注入后可继续成功
        monkeypatch.undo()
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            golden_minutes=_golden_minutes(scales), ex_dates_ms=(D5,), list_date_ms=D1)
        assert res2.status == "committed"
        assert [e[1] for e in _event_rows(conn, "600875")] == [
            "rolled_back", "committed"]
        assert _anchor_rows(conn, "600875") == [(1, "ok", res2.event_id)]


# ===========================================================================
# 10. 真实数据隔离回归（对抗反例 #6）——600875/600039/002864
# ===========================================================================

def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _load_meta() -> dict:
    return json.loads((FIXDIR / "metadata.json").read_text(encoding="utf-8"))


def _load_real(conn, code: str):
    """把固化的真实行（parquet）装入临时库正式表。

    fixture 保留证据源完整 schema（含 data_source 等审计列），临时表为测试精简
    schema——按 BY NAME 语义只投影两侧交集列，缺失列由目标表默认 NULL 填充；
    行内容（OHLCV/preClose/front 因子等引擎输入）逐列取自真实行，未做任何改写。
    """
    for table, pq in (
        ("stock_daily", (FIXDIR / f"{code}_daily.parquet").as_posix()),
        ("stock_minutes", (FIXDIR / f"{code}_minutes.parquet").as_posix()),
    ):
        tgt_cols = [
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position", [table]
            ).fetchall()
        ]
        src_cols = [
            r[0] for r in conn.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{pq}')"
            ).fetchall()
        ]
        common = [c for c in tgt_cols if c in src_cols]
        assert common, f"{table} 与 fixture 无交集列"
        col_sql = ", ".join(f'"{c}"' for c in common)
        conn.execute(
            f"INSERT INTO {table} BY NAME SELECT {col_sql} FROM read_parquet('{pq}')"
        )


def _real_daily(conn, code: str) -> pd.DataFrame:
    return conn.execute(
        "SELECT time, open, high, low, close, preClose, "
        "open_front, high_front, low_front, close_front "
        "FROM stock_daily WHERE code=? ORDER BY time", [code]).df()


FXDIR = FIXDIR / "fresh_xtquant"


def _load_fresh_meta() -> dict:
    return json.loads((FXDIR / "metadata_fresh_xtquant.json")
                      .read_text(encoding="utf-8"))


def _fresh_xt_daily(code: str, lo_ms: int) -> pd.DataFrame:
    """staged fresh 日线 = **独立采集的 fresh xtquant 输出（8 列逐值直读）**
    （固化 parquet，sha256 记录于 metadata_fresh_xtquant.json）。

    第四轮对抗审核修复：四个 front 列逐值取自实际 xtquant
    dividend_type='front' 输出（open/high/low/close_front 各自独立采集），
    **禁止**用 close scale 合成其余三列；raw 四列取自 dividend_type='none'
    输出（与 stored 逐值一致，见 metadata sanity/timestamp_convention）。
    窗口 time >= lo_ms（07-13 predecessor 不入 staged）。"""
    x = pd.read_parquet(FXDIR / f"{code}_fresh_daily.parquet").sort_values("time")
    x = x[x["time"].astype("int64") >= lo_ms].reset_index(drop=True)
    return pd.DataFrame({
        "code": code, "time": x["time"].astype("int64"),
        "open": x["open_raw"].astype(float),
        "high": x["high_raw"].astype(float),
        "low": x["low_raw"].astype(float),
        "close": x["close_raw"].astype(float),
        "open_front": x["open_front"].astype(float),
        "high_front": x["high_front"].astype(float),
        "low_front": x["low_front"].astype(float),
        "close_front": x["close_front"].astype(float),
    })


def _fresh_xt_golden(code: str) -> pd.DataFrame:
    """方法 A 黄金分钟 = **独立 fresh xtquant 1min 前复权 close_front 直读**
    （零平移 end-labeled，与 stored 分钟表逐 bar 对齐）。

    第三轮对抗审核修复：禁止 stored_raw × daily_scale 同源合成——本函数只读
    fresh_xtquant parquet 的 close_front 列，不触碰 stored 分钟数据。"""
    m = pd.read_parquet(FXDIR / f"{code}_fresh_1min.parquet")
    m = m[["time", "close_front"]].copy()
    m["time"] = m["time"].astype("int64")
    m["freq"] = "1min"
    return m[["time", "freq", "close_front"]]


def _prealign_predecessor(conn, code: str, day_ms: int):
    """把范围外 predecessor（07-13）stored 日线 front 预修正为 fresh xtquant
    真实 front（模拟"更早批次已完成修正"的状态），使 front-chain 校验使用
    真实上一交易日行而非依赖 chain_start 豁免。

    第四轮对抗审核修复：四个 front 列逐值取实际 xtquant front 输出，
    不再用 close scale × raw 合成。"""
    x = pd.read_parquet(FXDIR / f"{code}_fresh_daily.parquet")
    row = x.loc[x["time"].astype("int64") == day_ms]
    assert len(row) == 1, f"{code} fresh daily 缺 predecessor {day_ms}"
    conn.execute(
        "UPDATE stock_daily SET open_front = ?, high_front = ?, "
        "low_front = ?, close_front = ? WHERE code = ? AND time = ?",
        [float(row["open_front"].iloc[0]), float(row["high_front"].iloc[0]),
         float(row["low_front"].iloc[0]), float(row["close_front"].iloc[0]),
         code, day_ms])


REAL_D24 = _at("2026-07-24", 0, 0, 0)
REAL_D22 = _at("2026-07-22", 0, 0, 0)
REAL_D14 = _at("2026-07-14", 0, 0, 0)
REAL_D13 = _at("2026-07-13", 0, 0, 0)

# 第四轮对抗审核（2026-07-27）设计决策——方案 A（严格方法 B）：
# xtquant 为减法复权模型（front = raw − 每股现金分红，除权日前），
# (front/raw) 随价格逐日漂移，区间内**不存在**单一稳定乘法比率簇。
# 因此真实三证券在默认 ReanchorTolerances 下 BLOCK 是**正确结果**：
#   600875: stage 即 BLOCK fresh_daily_scale_inconsistent（low dev 1.17e-3）；
#           放开 tol_scale 后方法 B BLOCK ratio_multi_cluster（5 个修正簇）
#   600039: stage 即 BLOCK fresh_daily_scale_inconsistent（open dev 2.26e-3）；
#           放开 tol_scale 后方法 B BLOCK ratio_multi_cluster（6 个修正簇）
#   002864: 方法 B BLOCK ratio_multi_cluster（3 个修正簇）
# **禁止**以"容差校准"为名放宽 ratio/golden/cross 容差吸收复权模型差异
# （第三轮 REAL_TOL 已废除）。方案 B（fresh 逐值写入 / additive delta）属
# 框架行为变更，须用户批准设计文档后才能实现，见
# docs/qfq-reanchor-minute-model-decision-20260727.md。
#
# _SCALE_RELAXED_TOL 仅用于探针：跳过 stage 的 OHLC scale 一致性检查，
# 证明即使放行 stage，方法 B 仍会 ratio_multi_cluster BLOCK——它不是验收
# 容差，其余容差保持默认严格值。
_SCALE_RELAXED_TOL = ReanchorTolerances(tol_scale=5e-3)


class TestRealDataRegression:
    """真实数据回归：行取自只读证据源 qs_iso_a/data/quantstudio.db 的固化 fixture。

    来源/范围/行数/hash 记录于 tests/fixtures/qfq_real_reanchor/metadata.json；
    本类测试先校验 sha256 完整性，再在 tmp_path 临时库上执行修正。
    """

    def test_fixture_integrity(self):
        meta = _load_meta()
        assert "qs_iso_a" in meta["source_path"]          # 只读证据源
        for fname, info in meta["files"].items():
            p = FIXDIR / fname
            assert p.exists(), fname
            assert _sha256(p) == info["sha256"], f"{fname} hash 不匹配"
        # 行数契约：每证券 10 个交易日日线（07-13 predecessor + 9 个 staged 日）
        # + 9×241 分钟（07-14..07-24）
        for code in ("600875", "600039", "002864"):
            assert meta["files"][f"{code}_daily.parquet"]["rows"] == 10
            assert meta["files"][f"{code}_minutes.parquet"]["rows"] == 9 * 241

    def test_fresh_xtquant_fixture_integrity(self):
        """方法 A 独立黄金数据完整性：fresh xtquant 固化输出（8 列逐值）。

        独立性证据（第三/四轮对抗审核）：接口/参数/复权方式/采集时间/客户端
        版本/行数/sha256 记录于 metadata_fresh_xtquant.json；第四轮起 daily 与
        1min 均为 OHLC 四字段 × raw/front 两复权 **8 列逐值直采**（fields=
        [open,high,low,close]），staged daily 四个 front 列不再用 close scale
        合成。同时固化减法复权模型证据：600875/600039 除权日前
        front = raw − 每股现金分红，四列同日等差。"""
        meta = _load_fresh_meta()
        assert meta["interface"] == "xtdata.get_market_data_ex"
        assert "front" in meta["dividend_type"]
        assert meta["captured_at"].startswith("2026-07-27")
        assert meta["params"]["fields"] == ["open", "high", "low", "close"]
        assert "xtquant_version" in meta["client"]
        assert "零平移" in meta["timestamp_convention"]
        for fname, info in meta["files"].items():
            p = FXDIR / fname
            assert p.exists(), fname
            assert _sha256(p) == info["sha256"], f"{fname} hash 不匹配"
        cols8 = [f"{f}_{k}" for k in ("raw", "front")
                 for f in ("open", "high", "low", "close")]
        for code in ("600875", "600039", "002864"):
            assert meta["files"][f"{code}_fresh_daily.parquet"]["rows"] == 10
            assert meta["files"][f"{code}_fresh_1min.parquet"]["rows"] == 2169
            for fname in (f"{code}_fresh_daily.parquet",
                          f"{code}_fresh_1min.parquet"):
                x = pd.read_parquet(FXDIR / fname)
                assert set(cols8) <= set(x.columns), fname   # 8 列逐值直采
                for c in cols8:
                    assert x[c].notna().all(), f"{fname}:{c}"
            # 黄金分钟时间轴与 stored fixture 逐 bar 对齐（零平移，无缺无多）
            g = _fresh_xt_golden(code)
            s = pd.read_parquet(FIXDIR / f"{code}_minutes.parquet")
            assert set(g["time"]) == set(s["time"].astype("int64"))
            assert g["close_front"].notna().all()
        # 减法复权模型证据（除权日前 raw − front == 每股分红，四列同日等差）
        for code, div, ex_ms in (("600875", 0.53, REAL_D24),
                                 ("600039", 0.46, REAL_D24)):
            x = pd.read_parquet(FXDIR / f"{code}_fresh_daily.parquet")
            pre = x[x["time"].astype("int64") < ex_ms]
            for f in ("open", "high", "low", "close"):
                gap = (pre[f"{f}_raw"].astype(float)
                       - pre[f"{f}_front"].astype(float))
                assert gap.to_numpy() == pytest.approx(div, abs=5e-3), (code, f)

    @pytest.mark.parametrize(
        "code,exp_pre,exp_prev_close,exp_clusters,exp_max_err,exp_gt_tick", [
            # 2026-07-24 除息：preClose 27.22 / 前收 27.75；真实收益
            # 修复前 −7.819820% / 修复后 −6.024982%
            ("600875", 27.22, 27.75, 5, 0.032619, 960),
            # 2026-07-24 除息：preClose 9.06 / 前收 9.52；真实收益
            # 修复前 −7.457983% / 修复后 −2.759382%
            ("600039", 9.06, 9.52, 6, 0.046205, 1491),
        ])
    def test_exdiv_real_reanchor_blocks_by_default(
            self, renv, code, exp_pre, exp_prev_close,
            exp_clusters, exp_max_err, exp_gt_tick):
        """600875/600039 真实数据（第四轮·方案 A）：默认容差下必须 BLOCK。

        xtquant 为减法复权模型（fresh fixture 已证明 600875 front=raw−0.53、
        600039 front=raw−0.46，除权日前），(front/raw) 随价格逐日漂移，区间内
        不存在单一稳定乘法比率簇 → 默认 BLOCK 是**正确结果**，三证券不得
        自动写回。禁止以"容差校准"为名放宽 ratio/golden/cross 吸收模型差异
        （第三轮 REAL_TOL 已废除）。分层断言：
        1. 默认容差：stage 即 BLOCK fresh_daily_scale_inconsistent
           （真实四列 front 的 OHLC 各列 scale 本身互不一致——减法模型直接证据）；
        2. 仅放开 tol_scale 探针：方法 B BLOCK ratio_multi_cluster
           （600875=5 簇 / 600039=6 簇），证明 BLOCK 不依赖 stage 检查；
        3. 两次 BLOCK 均未写回任何行、不推进 anchor；
        4. 若强行以单一 median ratio 写回（模拟计算，不落库），与 fresh
           xtquant front 的偏差达多 tick 量级（>0.01 元 bar 占比过半），
           固化"1% 容差不可作为源精度容差"的证据。"""
        conn, cal = renv.conn, renv.calendar
        _load_real(conn, code)
        d = _real_daily(conn, code)
        assert len(d) == 10                               # 07-13 predecessor + 9
        # 修复前结构前提：日线/分钟 front 全部陈旧（front==raw）；伪跳空显著
        assert d["close_front"].to_numpy() == pytest.approx(d["close"].to_numpy(), rel=1e-9)
        close24 = float(d.loc[d["time"] == REAL_D24, "close"].iloc[0])
        fake_gap = close24 / exp_prev_close - 1.0
        true_ret = close24 / exp_pre - 1.0
        assert abs(fake_gap - true_ret) > 0.015
        # 报告口径的真实 fixture 收益（第四轮要求逐位固化）
        if code == "600875":
            assert fake_gap == pytest.approx(-0.07819820, abs=5e-9)
            assert true_ret == pytest.approx(-0.06024982, abs=5e-9)
        else:
            assert fake_gap == pytest.approx(-0.07457983, abs=5e-9)
            assert true_ret == pytest.approx(-0.02759382, abs=5e-9)

        # 减法复权模型直接证据：四列 front 同日等差（raw − front == 每股分红）
        x = pd.read_parquet(FXDIR / f"{code}_fresh_daily.parquet")
        pre_ex = x[(x["time"].astype("int64") < REAL_D24)]
        div = {"600875": 0.53, "600039": 0.46}[code]
        for f in ("open", "high", "low", "close"):
            gap = pre_ex[f"{f}_raw"].astype(float) - pre_ex[f"{f}_front"].astype(float)
            assert gap.to_numpy() == pytest.approx(div, abs=5e-3), f
        # 由此 (front/raw) 随价格漂移 → 单一乘法比率不存在

        _prealign_predecessor(conn, code, REAL_D13)
        fresh = _fresh_xt_daily(code, REAL_D14)          # 真实 8 列逐值直读
        assert len(fresh) == 9
        golden = _fresh_xt_golden(code)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        # —— 1) 默认容差（正式行为）：stage 即 BLOCK ——
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code, fresh_daily=fresh,
            calendar=cal, freqs=("1min",), golden_minutes=golden,
            ex_dates_ms=(REAL_D24,))                      # tol=默认，不放宽
        assert res.status == "blocked"
        assert res.block_reason == "fresh_daily_scale_inconsistent"
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, code) == []             # anchor 未推进

        # —— 2) 仅放开 tol_scale 探针：方法 B 仍 BLOCK ratio_multi_cluster ——
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code, fresh_daily=fresh,
            calendar=cal, freqs=("1min",), golden_minutes=golden,
            ex_dates_ms=(REAL_D24,), tol=_SCALE_RELAXED_TOL)
        assert res2.status == "blocked"
        assert res2.block_reason == "ratio_multi_cluster"
        assert f"{exp_clusters} 个需修正比率簇" in res2.error
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, code) == []

        # —— 3) 强行单一比率写回的可观察偏差（模拟计算，不落库）——
        # median R over update-window bars（引擎同款定义；stored front==raw
        # → R=target_scale）；与 fresh xtquant front 逐 bar 比对
        sm = pre_m[(pre_m["code"] == code) & (pre_m["time"] >= REAL_D14)
                   & (pre_m["time"] < REAL_D24)]
        tsc = dict(zip(fresh["time"].astype("int64"),
                       fresh["close_front"].astype(float)
                       / fresh["close"].astype(float)))
        day = (pd.to_datetime(sm["time"].astype("int64"), unit="ms", utc=True)
               .dt.tz_convert(TZ).dt.normalize().astype("int64") // 10**6)
        r_med = float(np.median(day.map(tsc).astype(float)))
        xm = pd.read_parquet(FXDIR / f"{code}_fresh_1min.parquet")[
            ["time", "close_front"]].rename(columns={"close_front": "xt_front"})
        j = sm.merge(xm, on="time", how="inner")
        assert len(j) == 8 * 241                          # 更新窗口全 bar
        err = (j["close"].astype(float) * r_med
               - j["xt_front"].astype(float)).abs()
        assert float(err.max()) == pytest.approx(exp_max_err, abs=5e-6)
        assert int((err > 0.01).sum()) == exp_gt_tick     # 多 tick 偏差 bar 数
        # 偏差过半 bar 超 1 tick → 非浮点误差，是模型不等价的系统性偏差
        assert int((err > 0.01).sum()) > len(j) * 0.2

    def test_002864_real_daily_correct_minute_stale_blocks_by_default(self, renv):
        """002864 真实数据（第四轮·方案 A）：默认容差下方法 B 必须 BLOCK。

        结构前提：2026-07-22 除权后 daily front 已修正、分钟仍陈旧。真实
        现金分红链上 per-day scale 在 0.76244~0.76322 间漂移，默认
        ratio_rel_tol=5e-4 下形成 **3 个需修正比率簇** → ratio_multi_cluster
        BLOCK 是正确结果（非 bootstrap 不允许多簇），不得取 median 合并后
        继续写价（第三轮 REAL_TOL 放宽已废除）。"""
        conn, cal = renv.conn, renv.calendar
        _load_real(conn, "002864")
        d = _real_daily(conn, "002864")
        assert len(d) == 10                                # 07-13 predecessor + 9
        # 结构前提（真实行）：07-22 前 daily front 已修正、分钟 front 陈旧
        scale_by_day = {int(r["time"]): float(r["close_front"]) / float(r["close"])
                        for _, r in d.iterrows()}
        pre_days = [t for t in scale_by_day if t < REAL_D22]
        assert all(0.760 < scale_by_day[t] < 0.765 for t in pre_days)
        assert all(scale_by_day[t] == pytest.approx(1.0, rel=1e-9)
                   for t in scale_by_day if t >= REAL_D22)
        m_scale = conn.execute(
            "SELECT MIN(close_front/close), MAX(close_front/close) "
            "FROM stock_minutes WHERE code='002864' AND close > 0").fetchone()
        assert m_scale[0] == pytest.approx(1.0, rel=1e-9)   # 分钟陈旧
        assert m_scale[1] == pytest.approx(1.0, rel=1e-9)
        # per-day scale 漂移证据：staged 窗口除权日前 scale 极差 > 默认容差
        drift = [scale_by_day[t] for t in pre_days if t >= REAL_D14]
        assert max(drift) / min(drift) - 1.0 > 5e-4

        _prealign_predecessor(conn, "002864", REAL_D13)
        fresh = _fresh_xt_daily("002864", REAL_D14)        # 真实 8 列逐值直读
        assert len(fresh) == 9
        pre_d = _snap(conn, "stock_daily")
        pre_m = _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="002864", fresh_daily=fresh,
            calendar=cal, freqs=("1min",),
            golden_minutes=_fresh_xt_golden("002864"),     # 独立 fresh xt 黄金
            ex_dates_ms=(REAL_D22,))                       # tol=默认，不放宽
        assert res.status == "blocked"
        assert res.block_reason == "ratio_multi_cluster"
        assert "3 个需修正比率簇" in res.error
        # BLOCK 未写回任何行、不推进 anchor
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "002864") == []

        # 强行单一比率写回的可观察偏差（模拟计算，不落库）：
        # median R=0.7627533166 时 vs fresh xtquant front 多 tick 偏差
        sm = pre_m[(pre_m["code"] == "002864") & (pre_m["time"] >= REAL_D14)
                   & (pre_m["time"] < REAL_D22)]
        tsc = dict(zip(fresh["time"].astype("int64"),
                       fresh["close_front"].astype(float)
                       / fresh["close"].astype(float)))
        day = (pd.to_datetime(sm["time"].astype("int64"), unit="ms", utc=True)
               .dt.tz_convert(TZ).dt.normalize().astype("int64") // 10**6)
        r_med = float(np.median(day.map(tsc).astype(float)))
        assert r_med == pytest.approx(0.7627533166, abs=1e-9)
        xm = pd.read_parquet(FXDIR / "002864_fresh_1min.parquet")[
            ["time", "close_front"]].rename(columns={"close_front": "xt_front"})
        j = sm.merge(xm, on="time", how="inner")
        assert len(j) == 6 * 241
        err = (j["close"].astype(float) * r_med
               - j["xt_front"].astype(float)).abs()
        assert float(err.max()) == pytest.approx(0.020477, abs=5e-6)
        assert int((err > 0.01).sum()) == 349


# ===========================================================================
# 11. B-1 fresh_staged：fresh xtquant 分钟逐值写入（用户批准 2026-07-27）
# ===========================================================================

def _fresh_minutes_syn(code: str, target_scale: dict, *, days=None,
                       closes=None, freqs=("1min",)) -> pd.DataFrame:
    """合成 fresh xtquant 分钟（B-1 staged 输入）：raw 与 stored 相同（同源），
    front = raw × 当日目标因子（乘法结构，B-1 契约对模型不敏感——逐值写入）。"""
    days = list(days or DAY_MS)
    closes = closes or RAW_CLOSE
    rows = []
    for day in days:
        s = float(target_scale[day])
        for freq in freqs:
            clocks = BAR_CLOCKS if freq == "1min" else BAR_CLOCKS_5MIN
            for t, bo, bh, bl, bc in _bars_of_day(_day_str(day), closes[day],
                                                  clocks):
                rows.append(dict(
                    code=code, time=t, freq=freq,
                    open=bo, high=bh, low=bl, close=bc,
                    open_front=bo * s, high_front=bh * s,
                    low_front=bl * s, close_front=bc * s))
    return pd.DataFrame(rows)


def _fresh_xt_minutes(code: str) -> pd.DataFrame:
    """真实 fresh xtquant 1min（8 列逐值直读 → B-1 staged 输入 schema）。"""
    x = pd.read_parquet(FXDIR / f"{code}_fresh_1min.parquet").sort_values("time")
    return pd.DataFrame({
        "code": code, "time": x["time"].astype("int64"),
        "open": x["open_raw"].astype(float),
        "high": x["high_raw"].astype(float),
        "low": x["low_raw"].astype(float),
        "close": x["close_raw"].astype(float),
        "open_front": x["open_front"].astype(float),
        "high_front": x["high_front"].astype(float),
        "low_front": x["low_front"].astype(float),
        "close_front": x["close_front"].astype(float),
    })


_B1_REASON = ("xtquant 减法复权模型（front=raw−每股分红），ratio 方法 B "
              "ratio_multi_cluster BLOCK；经用户批准采用 B-1 fresh 逐值写入"
              "（2026-07-27 设计批示）")

_B1_POSTCHECKS = {
    "daily_staged_match", "front_chain_return", "scale_consistency",
    "kline_relation", "row_conservation", "cross_table_overlap",
    "minute_staged_match", "minute_raw_match", "minute_coverage",
    "minute_tick_error"}

# 第七轮阻断 1：fresh_staged 强制来源审计三元组（缺任一项事务外 ValueError）。
# 全部 fresh_staged call site 复用此常量。
_AUDIT_KW = dict(fresh_source="xtdata.get_market_data_ex",
                 fresh_capture_id="capture-20260727-golden",
                 fresh_metadata_sha256="ab" * 32)


def _cap_sha(s: str) -> str:
    """生成合法 64 位 hex 的 metadata/内容 sha（不同采集批次用不同值）。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()



class TestFreshStagedModel:
    """B-1 fresh_staged 模型（用户批准边界 9 条，2026-07-27）：

    - 保留 ratio 方法 B；模型显式 model={ratio,fresh_staged}，禁止静默切换；
    - staged 分钟主键 (code,freq,time)，只 UPDATE 四个 front 列；
    - precheck：raw 逐 bar 一致 + 完整覆盖，任一异常整券 BLOCK；
    - COMMIT 前新增 minute_staged_match/minute_raw_match/minute_coverage/
      minute_tick_error 四项 postcheck（原六项全部保留 = 10 项）；
    - 真实验收：600875/600039/002864 全部 committed，minute front vs fresh
      逐 bar ≤1 tick（bars_over_1_tick==0），002864 daily 逐值不变。

    注：minute_staged_match（IS DISTINCT FROM 精确一致）严格强于
    minute_tick_error（≤1 tick），主流程任何写入污染必先被 (7) 拦截；
    (10) 为防御纵深 + 审计证据（postcheck 详情固化 bars_over_1_tick /
    max_abs_err），committed 用例逐项断言其为 0。
    """

    # ---- 合成：正常提交 ----------------------------------------------------
    def test_fresh_staged_committed_only_four_front_cols(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        _seed_security(conn, "000001")            # 对照证券：全程不许动
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "committed"
        assert res.model == "fresh_staged"
        assert res.daily_rows_updated == 5
        assert res.plans == {}                    # 不算 R、无 ratio 分段计划

        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # 除四个 front 列外全表逐值未变（raw/volume/back/ratio/update_time 等）
        _assert_nonfront_unchanged(pre_d, post_d)
        _assert_nonfront_unchanged(pre_m, post_m)
        # 行数守恒：无 DELETE/INSERT
        assert len(pre_d) == len(post_d) and len(pre_m) == len(post_m)

        # 分钟 front 逐值 = staged fresh（含 09:30 集合竞价 bar）
        fmap = _minute_front(conn, "stock_minutes", "600875")
        assert len(fmap) == 5 * len(BAR_CLOCKS)
        for r in fm.itertuples():
            assert fmap[int(r.time)] == pytest.approx(
                (r.open_front, r.high_front, r.low_front, r.close_front),
                rel=1e-12)
        # 对照证券完全未动
        pd.testing.assert_frame_equal(
            pre_m[pre_m["code"] == "000001"].reset_index(drop=True),
            post_m[post_m["code"] == "000001"].reset_index(drop=True))

        # 覆盖统计 + 10 项 postcheck（原六项保留 + B-1 四项）
        cov = res.minute_coverage["1min"]
        n = 5 * len(BAR_CLOCKS)
        assert (cov["staged_count"], cov["target_count"],
                cov["matched_count"]) == (n, n, n)
        assert (cov["missing_target"], cov["missing_staged"],
                cov["duplicates"], cov["raw_mismatch"]) == (0, 0, 0, 0)
        assert set(res.postchecks) == _B1_POSTCHECKS
        assert res.postchecks["minute_staged_match"]["1min"]["mismatch"] == 0
        assert res.postchecks["minute_raw_match"]["1min"]["raw_mismatch"] == 0
        assert res.postchecks["minute_coverage"]["1min"]["missing_target"] == 0
        tick = res.postchecks["minute_tick_error"]["1min"]
        assert tick["bars_over_1_tick"] == 0
        assert tick["max_abs_err"] <= 1e-9        # 逐值写入 → 0 tick 误差
        assert tick["tick_size"] == 0.01

        # 事件审计：模型选择 + 原因 + 覆盖统计显式记录；anchor 同事务推进
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["committed"]
        plan = json.loads(evs[0][3])
        assert plan["model"] == "fresh_staged"
        assert plan["model_reason"] == _B1_REASON
        assert plan["minute_coverage"]["1min"]["staged_count"] == n
        assert _anchor_rows(conn, "600875") == [(1, "ok", res.event_id)]
        # staged 临时表已清理
        tabs = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert not any(t.startswith("qfq_staged_fresh_min_") for t in tabs)

    # ---- 合成：raw 不一致 → 整券 BLOCK -------------------------------------
    def test_fresh_staged_raw_mismatch_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.05   # 污染 1 根 raw
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "minute_raw_mismatch"
        # 整券回滚：两表逐值未变、anchor 未推进、失败事件独立记录
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["blocked"]

    # ---- 合成：覆盖不完整（缺 bar / 多 bar）→ 整券 BLOCK -------------------
    def test_fresh_staged_coverage_incomplete_blocks(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        # (a) fresh 缺 1 根 bar → missing_staged>0
        fm_missing = _fresh_minutes_syn("600875", scales).iloc[1:]
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm_missing,
            **{k: v for k, v in _AUDIT_KW.items()
               if k not in ("fresh_capture_id", "fresh_metadata_sha256")},
            fresh_capture_id="cap-cov-missing",
            fresh_metadata_sha256=_cap_sha("cov-missing"))
        assert res.status == "blocked"
        assert res.block_reason == "minute_coverage_incomplete"

        # (b) fresh 多 1 根合法时刻 bar（stored 无此 bar）→ missing_target>0
        fm_extra = _fresh_minutes_syn("600875", scales)
        extra = fm_extra.iloc[[2]].copy()
        extra["time"] = _at(_day_str(D1), 10, 1, 0)   # 合法连续竞价时刻
        fm_extra = pd.concat([fm_extra, extra], ignore_index=True)
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm_extra,
            **{k: v for k, v in _AUDIT_KW.items()
               if k not in ("fresh_capture_id", "fresh_metadata_sha256")},
            fresh_capture_id="cap-cov-extra",
            fresh_metadata_sha256=_cap_sha("cov-extra"))
        assert res2.status == "blocked"
        assert res2.block_reason == "minute_coverage_incomplete"

        # 两次 BLOCK 均整券回滚
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []

    # ---- 合成：staged 契约校验（session/NULL/重复 key）→ 整券 BLOCK --------
    def test_fresh_staged_contract_violations_block(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        pre_m = _snap(conn, "stock_minutes")
        cases = []
        # (a) 午间休市 bar → fresh_minutes_bad_session
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[2, "time"] = _at(_day_str(D1), 12, 0, 0)
        cases.append((fm, "fresh_minutes_bad_session"))
        # (b) front NULL → fresh_minutes_null_or_bad（fresh 采集不允许缺价）
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[3, "close_front"] = None
        cases.append((fm, "fresh_minutes_null_or_bad"))
        # (c) (code,freq,time) 重复 → fresh_minutes_dup_key
        fm = _fresh_minutes_syn("600875", scales)
        fm = pd.concat([fm, fm.iloc[[4]]], ignore_index=True)
        cases.append((fm, "fresh_minutes_dup_key"))
        for i, (fm_bad, reason) in enumerate(cases):
            res = apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875",
                fresh_daily=_fresh_daily("600875", scales), calendar=cal,
                freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
                model="fresh_staged", model_reason=_B1_REASON,
                fresh_minutes=fm_bad,
                **{k: v for k, v in _AUDIT_KW.items()
                   if k not in ("fresh_capture_id", "fresh_metadata_sha256")},
                fresh_capture_id=f"cap-viol-{i}",
                fresh_metadata_sha256=_cap_sha(f"viol-{i}"))
            assert res.status == "blocked", reason
            assert res.block_reason == reason
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []

    # ---- 合成：写入污染 → minute_staged_match 拦截 + 整券回滚 --------------
    def test_fresh_staged_postcheck_corruption_rolls_back(self, env, monkeypatch):
        """故障注入：分钟 UPDATE 后、postcheck 前污染 1 个 front 值。

        污染量取 **半 tick（0.005）**：能穿过 scale_consistency（加法偏离
        ≤1 tick 豁免）与 minute_tick_error（≤1 tick）等全部容差类门禁，
        只有 minute_staged_match（精确逐值，IS DISTINCT FROM）能拦截 →
        全表回滚。证明 (7) 是比 ≤1 tick 更强的不变量；因此主流程中
        minute_tick_error 只能以 bars_over_1_tick==0 的审计证据形式出现
        （见 committed 用例断言）。>1 tick 的粗污染则会更早被原有
        scale_consistency 门禁拦截（纵深防御，两级都验证过）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        t_hit = int(fm.loc[10, "time"])
        orig = ENG.update_daily_front_from_staged

        def corrupting(conn_, asset_type, code, staged):
            n = orig(conn_, asset_type, code, staged)
            conn_.execute(       # 模拟写入路径 bug：半 tick 污染已写分钟 front
                "UPDATE stock_minutes SET close_front = close_front + 0.005 "
                "WHERE code='600875' AND freq='1min' AND time=?", [t_hit])
            return n

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", corrupting)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "rolled_back"
        assert res.block_reason == "minute_staged_match"
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["rolled_back"]

        # 对照：>1 tick 粗污染更早被原有 scale_consistency 拦截（两级纵深）
        def corrupting_big(conn_, asset_type, code, staged):
            n = orig(conn_, asset_type, code, staged)
            conn_.execute(
                "UPDATE stock_minutes SET close_front = close_front + 0.05 "
                "WHERE code='600875' AND freq='1min' AND time=?", [t_hit])
            return n

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", corrupting_big)
        res2 = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res2.status == "rolled_back"
        assert res2.block_reason == "scale_consistency"
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []

    # ---- 防呆：禁止静默切换 -------------------------------------------------
    def test_no_silent_model_switching(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        fresh = _fresh_daily("600875", scales)
        pre_m = _snap(conn, "stock_minutes")
        # (a) ratio 模式不接受 fresh_minutes（杜绝"BLOCK 后换数据重试"）
        with pytest.raises(ValueError, match="ratio"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", fresh_daily=fresh,
                calendar=cal, freqs=("1min",), ex_dates_ms=(D5,),
                list_date_ms=D1, fresh_minutes=fm)
        # (b) fresh_staged 必须显式给出 model_reason（写入事件审计）
        with pytest.raises(ValueError, match="model_reason"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", fresh_daily=fresh,
                calendar=cal, freqs=("1min",), ex_dates_ms=(D5,),
                list_date_ms=D1, model="fresh_staged", fresh_minutes=fm)
        # (c) fresh_staged 必须提供非空 fresh_minutes
        with pytest.raises(ValueError, match="fresh_minutes"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875", fresh_daily=fresh,
                calendar=cal, freqs=("1min",), ex_dates_ms=(D5,),
                list_date_ms=D1, model="fresh_staged",
                model_reason=_B1_REASON)
        # 防呆全部发生在事务外：无写回、无事件、无 anchor
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _event_rows(conn, "600875") == []
        assert _anchor_rows(conn, "600875") == []

    # ---- ratio 路径行为不受影响（B-1 边界 1：方法 B 保留且逐位不变）--------
    def test_ratio_path_unaffected(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), golden_minutes=_golden_minutes(scales),
            ex_dates_ms=(D5,), list_date_ms=D1)          # model 默认 ratio
        assert res.status == "committed"
        assert res.model == "ratio"
        assert res.minute_coverage == {}                 # 无 B-1 覆盖统计
        # postcheck 仍为原六项集合（B-1 四项不出现在 ratio 模式）
        assert set(res.postchecks) == {
            "daily_staged_match", "front_chain_return", "scale_consistency",
            "kline_relation", "row_conservation", "cross_table_overlap"}
        plan = json.loads(_event_rows(conn, "600875")[0][3])
        assert plan["model"] == "ratio"
        assert plan["model_reason"] is None
        assert "minute_coverage" not in plan

    # ---- 真实验收：三证券 fresh_staged 全部 committed -----------------------
    @pytest.mark.parametrize("code,ex_ms,true_ret", [
        ("600875", REAL_D24, -0.06024982),
        ("600039", REAL_D24, -0.02759382),
        ("002864", REAL_D22, None),          # 002864：daily 已正确，无伪跳空修复口径
    ])
    def test_real_three_securities_fresh_staged_committed(
            self, renv, code, ex_ms, true_ret):
        """B-1 真实验收（用户边界 8）：600875/600039/002864 全部 committed；
        minute OHLC front vs fresh xtquant 全量逐 bar ≤1 tick
        （bars_over_1_tick==0）；002864 daily 逐值不变。

        对照组：同一 fixture 在 ratio 模式默认容差下 BLOCK（见
        TestRealDataRegression）——B-1 是经批准的模型切换，非容差放宽。"""
        conn, cal = renv.conn, renv.calendar
        _load_real(conn, code)
        _prealign_predecessor(conn, code, REAL_D13)
        fresh = _fresh_xt_daily(code, REAL_D14)
        fm = _fresh_xt_minutes(code)
        assert len(fm) == 9 * 241
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")

        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code, fresh_daily=fresh,
            calendar=cal, freqs=("1min",), ex_dates_ms=(ex_ms,),
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "committed", (res.block_reason, res.error)

        post_d, post_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        # 除四个 front 列外全表逐值未变
        _assert_nonfront_unchanged(pre_d, post_d)
        _assert_nonfront_unchanged(pre_m, post_m)

        # —— minute front vs fresh xtquant：全量逐 bar 精确一致（≤1 tick 的
        #    上界收敛为 0——B-1 逐值写入）——
        got = post_m[(post_m["code"] == code)][
            ["time"] + list(FRONT_COLS)].reset_index(drop=True)
        exp = fm[["time"] + list(FRONT_COLS)].reset_index(drop=True)
        j = got.merge(exp, on="time", suffixes=("", "_x"))
        assert len(j) == 9 * 241
        for c in FRONT_COLS:
            max_err = float((j[c].astype(float)
                             - j[f"{c}_x"].astype(float)).abs().max())
            assert max_err <= 1e-9, (code, c, max_err)
        tick = res.postchecks["minute_tick_error"]["1min"]
        assert tick["bars_over_1_tick"] == 0
        assert tick["max_abs_err"] <= 1e-9
        cov = res.minute_coverage["1min"]
        assert (cov["staged_count"], cov["target_count"],
                cov["matched_count"]) == (9 * 241, 9 * 241, 9 * 241)

        # —— 伪跳空消除（600875/600039）：front 链除息日收益 == 真实收益 ——
        if true_ret is not None:
            d23 = _at("2026-07-23", 0, 0, 0)
            cf_p, cf_t = (conn.execute(
                "SELECT close_front FROM stock_daily WHERE code=? AND time=?",
                [code, t]).fetchone()[0] for t in (d23, REAL_D24))
            assert cf_t / cf_p - 1.0 == pytest.approx(true_ret, abs=5e-5)

        # —— 002864：daily 已正确 → 四个 front 列也逐值不变（边界 8）——
        if code == "002864":
            pd.testing.assert_frame_equal(
                pre_d[pre_d["code"] == code].reset_index(drop=True),
                post_d[post_d["code"] == code].reset_index(drop=True),
                check_exact=False, rtol=0, atol=1e-9)

        # —— 事件审计 + anchor 同事务推进；10 项 postcheck 齐全 ——
        assert set(res.postchecks) == _B1_POSTCHECKS
        evs = _event_rows(conn, code)
        assert [e[1] for e in evs] == ["committed"]
        plan = json.loads(evs[0][3])
        assert plan["model"] == "fresh_staged"
        assert plan["model_reason"] == _B1_REASON
        assert _anchor_rows(conn, code) == [(1, "ok", res.event_id)]


# ---------------------------------------------------------------------------
# 第六轮阻断回归（2026-07-27 用户批示）
# ---------------------------------------------------------------------------

class TestRound6Blockers:
    """第六轮六项阻断的引擎侧回归：

    1. minute_raw_match 不得借 SQL 三值逻辑漏检 NULL/NaN/Inf/<=0（阻断 1）；
    2. fresh minute 逐自然日经 CalendarService 验证真实交易日（阻断 2）；
    3. blocked/rolled_back/failed 事件同样携带 model/model_reason/
       fresh_source/fresh_capture_id/metadata_sha256/freqs 审计上下文（阻断 3）；
    4. tick_size 按资产路由（STOCK=0.01 / ETF=0.001），事件记录实际 tick（阻断 4）。
    """

    # ---- 阻断 1：UPDATE 后 stored raw 被污染 → 必须回滚 --------------------
    # NULL/NaN/0 会被容差类门禁的 notna()/(>0) mask **静默排除**——正是只有
    # (8) minute_raw_match 显式检查才能兜底的漏检形态（精确断言 (8) 拦截）；
    # Inf/粗 mismatch 是有限参与运算的值，先被 scale_consistency 纵深拦截
    # （同样整券回滚）；close+3e-9 微量污染穿过全部容差门禁，唯 (8) 的
    # eps=1e-9 能拦——证明 (8) 是最后且最精确的一道防线。
    @pytest.mark.parametrize("col,sql_val,reasons,kind", [
        ("open",  "NULL",                  {"minute_raw_match"}, "invalid"),
        ("high",  "CAST('nan' AS DOUBLE)", {"minute_raw_match"}, "invalid"),
        ("low",   "0",                     {"minute_raw_match"}, "invalid"),
        ("close", "close + 3e-9",          {"minute_raw_match"}, "mismatch"),
        ("high",  "CAST('inf' AS DOUBLE)",
         {"minute_raw_match", "scale_consistency"}, "invalid_deep"),
        ("close", "close + 0.05",
         {"minute_raw_match", "scale_consistency"}, "mismatch_deep"),
    ])
    def test_raw_corruption_rolls_back(self, env, monkeypatch, col, sql_val,
                                       reasons, kind):
        """故障注入：分钟 UPDATE 后污染 1 根 stored raw。旧实现
        ABS(NULL-x)>eps 结果为 NULL → WHERE 非真过滤 → committed 漏检；
        修复后 NULL/NaN/Inf/<=0 显式拦截，mismatch 走 abs 差，均整券回滚。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        t_hit = int(fm.loc[10, "time"])
        orig = ENG.update_daily_front_from_staged

        def corrupting(conn_, asset_type, code, staged):
            n = orig(conn_, asset_type, code, staged)
            conn_.execute(   # 模拟写入路径 bug：UPDATE 误触 raw 列
                f"UPDATE stock_minutes SET {col} = {sql_val} "
                f"WHERE code='600875' AND freq='1min' AND time=?", [t_hit])
            return n

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", corrupting)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON, fresh_minutes=fm,
            **_AUDIT_KW)
        assert res.status == "rolled_back", (col, sql_val, res.status)
        assert res.block_reason in reasons, (col, sql_val, res.block_reason)
        if kind == "invalid":
            assert res.block_reason == "minute_raw_match"
            assert "NULL" in (res.error or "")     # 显式拦截，非三值逻辑漏检
        if kind == "mismatch":
            assert res.block_reason == "minute_raw_match"
        # 整券回滚：raw 污染与 front 写入全部还原；anchor 未推进
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["rolled_back"]

    # ---- 阻断 2：周六 bar（钟面合法）→ 交易日历拦截，整券 BLOCK ------------
    def test_weekend_bar_blocks(self, env):
        """用户实测：2026-07-25 类周六 09:31 bar 钟面/cadence 全合法，
        旧实现 committed 且周末 close_front 被写入。修复后 CalendarService
        逐自然日校验 → fresh_minutes_non_trading_day 整券 BLOCK。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        sat = fm.iloc[[3]].copy()                     # 周六 09:31（D_SAT 在
        sat["time"] = D_SAT + (9 * 60 + 31) * 60_000  # persist 窗口内，闭市）
        fm_bad = pd.concat([fm, sat], ignore_index=True)
        pre_d, pre_m = _snap(conn, "stock_daily"), _snap(conn, "stock_minutes")
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm_bad, **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "fresh_minutes_non_trading_day"
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
        assert _anchor_rows(conn, "600875") == []
        assert [e[1] for e in _event_rows(conn, "600875")] == ["blocked"]

    def test_unknown_day_blocks(self, env):
        """trade_calendar 未缓存且无 provider → fresh_minutes_unknown_day
        （未知日不得静默当开市/闭市）。"""
        conn = env.conn
        cal_np = CalendarService(main_db=env.main, calendar_provider=None)
        fm = _fresh_minutes_syn("600875", _scales_exd5(F_600875))
        far = fm.iloc[[3]].copy()
        far["time"] = _at("2026-08-03", 9, 31, 0)     # 窗口外周一，未缓存
        fm_bad = pd.concat([fm, far], ignore_index=True)
        with pytest.raises(ENG.ReanchorBlocked) as ei:
            ENG.stage_fresh_minutes(conn, "STOCK", "600875", "1min",
                                    fm_bad, calendar=cal_np)
        assert ei.value.reason == "fresh_minutes_unknown_day"

    def test_stage_fresh_minutes_requires_calendar(self, env):
        """calendar=None → 拒绝执行（交易日校验硬门禁，不允许静默跳过）。"""
        fm = _fresh_minutes_syn("600875", _scales_exd5(F_600875))
        with pytest.raises(ValueError, match="CalendarService"):
            ENG.stage_fresh_minutes(env.conn, "STOCK", "600875", "1min", fm)

    # ---- 阻断 3：失败事件必须携带审计上下文 --------------------------------
    _AUDIT_KW = dict(_AUDIT_KW)      # 复用模块级审计三元组

    def _assert_audit(self, plan_json, *, model="fresh_staged"):
        assert plan_json, "失败事件 minute_ratio_plan 不得为 NULL（阻断 3）"
        plan = json.loads(plan_json)
        assert plan["model"] == model
        assert plan["model_reason"] == _B1_REASON
        au = plan["model_audit"]
        assert au["fresh_source"] == self._AUDIT_KW["fresh_source"]
        assert au["fresh_capture_id"] == self._AUDIT_KW["fresh_capture_id"]
        assert au["metadata_sha256"] == self._AUDIT_KW["fresh_metadata_sha256"]
        assert au["tick_size"] == 0.01
        assert au["freqs"] == ["1min"]
        return plan

    def test_blocked_event_carries_audit(self, env):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.05   # raw mismatch
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **self._AUDIT_KW)
        assert res.status == "blocked"
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["blocked"]
        self._assert_audit(evs[0][3])

    def test_rolled_back_event_carries_audit(self, env, monkeypatch):
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        t_hit = int(fm.loc[10, "time"])
        orig = ENG.update_daily_front_from_staged

        def corrupting(conn_, asset_type, code, staged):
            n = orig(conn_, asset_type, code, staged)
            conn_.execute("UPDATE stock_minutes SET open = NULL "
                          "WHERE code='600875' AND freq='1min' AND time=?",
                          [t_hit])
            return n

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", corrupting)
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code="600875",
            fresh_daily=_fresh_daily("600875", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **self._AUDIT_KW)
        assert res.status == "rolled_back"
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["rolled_back"]
        plan = self._assert_audit(evs[0][3])
        # rolled_back 已走完 precheck → coverage 摘要也必须在
        assert plan["minute_coverage"]["1min"]["matched_count"] == 5 * len(BAR_CLOCKS)

    def test_failed_event_carries_audit(self, env, monkeypatch):
        """第七轮起非分钟 freq 已前移为事务外 ValueError（不落事件），
        failed 路径改用事务内故障注入验证：意外异常 → failed 事件仍带审计。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)

        def boom(*a, **k):                     # 事务内意外异常 → failed 路径
            raise RuntimeError("injected-tx-failure")

        monkeypatch.setattr(ENG, "update_daily_front_from_staged", boom)
        with pytest.raises(RuntimeError, match="injected-tx-failure"):
            apply_reanchor_for_security(
                conn, asset_type="STOCK", code="600875",
                fresh_daily=_fresh_daily("600875", scales), calendar=cal,
                freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
                model="fresh_staged", model_reason=_B1_REASON,
                fresh_minutes=fm, **self._AUDIT_KW)
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["failed"]
        assert evs[0][3], "failed 事件 minute_ratio_plan 不得为 NULL（阻断 3）"
        plan = json.loads(evs[0][3])
        assert plan["model"] == "fresh_staged"
        assert plan["model_audit"]["fresh_capture_id"] == \
            self._AUDIT_KW["fresh_capture_id"]

    # ---- 阻断 4：tick_size 资产路由 ----------------------------------------
    def test_resolve_tick_size_routing(self):
        assert ENG.resolve_tick_size("STOCK") == 0.01
        assert ENG.resolve_tick_size("ETF") == 0.001
        # 显式设置覆盖路由
        assert ENG.resolve_tick_size(
            "ETF", ReanchorTolerances(tick_size=0.005)) == 0.005
        # 默认 None → 按资产路由（不再统一 0.01）
        assert ENG.resolve_tick_size("ETF", ReanchorTolerances()) == 0.001
        with pytest.raises(Exception):
            ENG.resolve_tick_size("BOND")

    def test_etf_fresh_staged_tick_routed_and_audited(self, env):
        """ETF fresh_staged 合成回归：committed；postcheck 与事件审计记录
        实际 tick_size=0.001（不再统一写死 0.01）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "510300", asset="ETF")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("510300", scales)
        res = apply_reanchor_for_security(
            conn, asset_type="ETF", code="510300",
            fresh_daily=_fresh_daily("510300", scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **self._AUDIT_KW)
        assert res.status == "committed", (res.block_reason, res.error)
        tick = res.postchecks["minute_tick_error"]["1min"]
        assert tick["tick_size"] == 0.001            # ETF 路由值
        assert tick["bars_over_1_tick"] == 0
        evs = _event_rows(conn, "510300")
        assert [e[1] for e in evs] == ["committed"]
        plan = json.loads(evs[0][3])
        assert plan["model_audit"]["tick_size"] == 0.001
        assert plan["model_audit"]["fresh_source"] == \
            self._AUDIT_KW["fresh_source"]


class TestRound7AuditBlockers:
    """第七轮两个审计阻断回归：

    1. fresh_staged 来源字段（fresh_source / fresh_capture_id /
       fresh_metadata_sha256 合法 64 位 hex / freqs canonical）缺失或非法 →
       **事务外 ValueError**，绝不写价格 / 事件 / anchor；
    2. precheck BLOCK 事件携带覆盖统计（staged_count / target_count /
       matched_count / raw_mismatch）+ precheck_phase，记录"执行到哪一步、
       各统计值是多少"。
    """

    _AUDIT_KW = dict(_AUDIT_KW)      # 复用模块级审计三元组

    def _call_fresh_staged(self, conn, cal, **over):
        scales = _scales_exd5(F_600875)
        kw = dict(asset_type="STOCK", code="600875",
                  fresh_daily=_fresh_daily("600875", scales), calendar=cal,
                  freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
                  model="fresh_staged", model_reason=_B1_REASON,
                  fresh_minutes=_fresh_minutes_syn("600875", scales),
                  **self._AUDIT_KW)
        kw.update(over)
        return apply_reanchor_for_security(conn, **kw)

    # ---- 阻断 1：来源字段强制（事务外 ValueError）-------------------------
    @pytest.mark.parametrize("missing", [
        "fresh_source", "fresh_capture_id", "fresh_metadata_sha256",
    ])
    def test_fresh_staged_requires_source_fields(self, env, missing):
        """来源字段任一缺失/非法 → 事务外 ValueError，且价格/事件/anchor 一律未写。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        pre_d = _snap(conn, "stock_daily")
        pre_m = _snap(conn, "stock_minutes")
        kw = dict(self._AUDIT_KW)
        kw[missing] = None if missing != "fresh_metadata_sha256" else "not-a-hex"
        with pytest.raises(ValueError):
            self._call_fresh_staged(conn, cal, **kw)
        # 事务外：无事件、无 anchor、价格未动（证明未进入 BEGIN / 未落 event）
        assert _event_rows(conn, "600875") == []
        assert _anchor_rows(conn, "600875") == []
        pd.testing.assert_frame_equal(pre_d, _snap(conn, "stock_daily"))
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))

    def test_fresh_staged_rejects_bad_sha256(self, env):
        """fresh_metadata_sha256 非 64 位 hex → 事务外 ValueError。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        with pytest.raises(ValueError, match="fresh_metadata_sha256"):
            self._call_fresh_staged(
                conn, cal,
                fresh_source="xtdata", fresh_capture_id="cap1",
                fresh_metadata_sha256="deadbeef")   # 8 hex，非法

    def test_fresh_staged_rejects_empty_freqs(self, env):
        """freqs 为空 → 事务外 ValueError（事件 freqs 必须 canonical 去重列表）。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        with pytest.raises(ValueError, match="freqs"):
            self._call_fresh_staged(
                conn, cal, freqs=(),
                fresh_source="xtdata", fresh_capture_id="cap1",
                fresh_metadata_sha256="ab" * 32)

    def test_fresh_staged_valid_sha256_proceeds(self, env):
        """合法 64 位 hex sha256 → 不触发来源字段 ValueError，进入正常 precheck。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.05   # 注入 raw mismatch
        res = self._call_fresh_staged(
            conn, cal, fresh_minutes=fm,
            fresh_source="xtdata.get_market_data_ex",
            fresh_capture_id="capture-20260727-golden",
            fresh_metadata_sha256="f" * 64)
        assert res.status == "blocked"          # 进入 precheck，非来源字段拦截
        assert res.block_reason == "minute_raw_mismatch"

    # ---- 阻断 2：precheck BLOCK 事件携带覆盖统计 -------------------------
    def test_blocked_event_carries_coverage(self, env):
        """raw mismatch precheck → blocked 事件 minute_ratio_plan 含
        minute_coverage(各统计值) + precheck_phase。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.05   # raw mismatch
        res = self._call_fresh_staged(conn, cal, fresh_minutes=fm)
        assert res.status == "blocked"
        assert res.block_reason == "minute_raw_mismatch"
        evs = _event_rows(conn, "600875")
        assert [e[1] for e in evs] == ["blocked"]
        plan = json.loads(evs[0][3])
        cov = plan["minute_coverage"]["1min"]
        assert cov["staged_count"] == cov["target_count"] == cov["matched_count"]
        assert cov["raw_mismatch"] > 0
        assert plan["precheck_phase"] == "precheck"
        # result.minute_coverage 同样回填（供调用方审计"走到哪一步"）
        assert res.minute_coverage["1min"]["raw_mismatch"] > 0

    def test_blocked_coverage_incomplete_records_counts(self, env):
        """覆盖不完整（缺一根 bar）→ blocked 事件 coverage 记录实际
        staged/target 计数 + precheck_phase=precheck。"""
        conn, cal = env.conn, env.calendar
        _seed_security(conn, "600875")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn("600875", scales).iloc[:-1].copy()  # 缺最后一根
        res = self._call_fresh_staged(conn, cal, fresh_minutes=fm)
        assert res.status == "blocked"
        assert res.block_reason == "minute_coverage_incomplete"
        plan = json.loads(_event_rows(conn, "600875")[0][3])
        cov = plan["minute_coverage"]["1min"]
        assert cov["staged_count"] < cov["target_count"]
        assert cov["missing_staged"] > 0        # fresh 缺 bar → staged 侧缺失
        assert plan["precheck_phase"] == "precheck"
        assert res.minute_coverage["1min"]["missing_staged"] > 0


# ===========================================================================
# 12. 批次2 前置项：引擎 ETF 分钟 raw 对齐 eps 路由（2026-07-30）
# 预检 raw 对齐容差：ETF 分钟放宽到 1 个 tick（1e-3），STOCK/日线严格 1e-9。
# 验证 92 只 ADMISSIBLE_TICK_TOLERANCE ETF 在引擎内可被 committed；
# 真实不一致（>1 分钱）与 STOCK（严格）仍 BLOCK。
# ===========================================================================

class TestEtfMinuteRawEps:
    """引擎 ETF 分钟 tick eps 路由（批次2 前置项，2026-07-30）。

    预检 raw 逐 bar 对齐容差按资产路由：ETF 分钟 1e-3（1 tick），STOCK/日线严格 1e-9。
    该容差在「权威路径 _check_minute_cov_raw」与「B-1 fresh_staged 路径
    apply_fresh_minute_staged precheck + postcheck」三处一致生效。

    说明：权威路径的 fm 由 stored raw 重建，无法在测试内注入独立差异，故其 eps 路由
    用直接单测 _check_minute_cov_raw 覆盖；端到端（fresh_staged）用独立 fresh_minutes
    注入差异，是 92 只 ADMISSIBLE_TICK_TOLERANCE ETF 的生产实际路径（B-1）。
    """

    # ---- 单元级：路由 helper ----
    def test_raw_match_eps_minute_routing(self):
        assert _raw_match_eps_minute("ETF") == 1e-3
        assert _raw_match_eps_minute("etf") == 1e-3   # 大小写归一
        assert _raw_match_eps_minute("STOCK") == 1e-9
        assert _raw_match_eps_minute("stock") == 1e-9

    # ---- 直接单元：权威路径 _check_minute_cov_raw 的 eps 路由 ----
    def test_check_minute_cov_raw_etf_1tick_passes(self, env):
        """ETF 分钟 fresh raw 注入 0.5 tick（0.0005）< 1e-3 → 不抛 ReanchorBlocked。"""
        conn, cal = env.conn, env.calendar
        code = "510300"
        _seed_security(conn, code, asset="ETF")
        fm = _snap(conn, "etf_minutes").copy()
        fm["close"] = fm["close"] + 0.0005
        fm["high"] = fm["high"] + 0.0005
        _check_minute_cov_raw(conn, "ETF", code, "1min", fm, None)   # 不抛即通过

    def test_check_minute_cov_raw_etf_gt_1cent_raises(self, env):
        """ETF 分钟 fresh raw 注入 >1 分钱（0.05）> 1e-3 → 抛 ReanchorBlocked。"""
        conn, cal = env.conn, env.calendar
        code = "510300"
        _seed_security(conn, code, asset="ETF")
        fm = _snap(conn, "etf_minutes").copy()
        fm["close"] = fm["close"] + 0.05
        with pytest.raises(ReanchorBlocked):
            _check_minute_cov_raw(conn, "ETF", code, "1min", fm, None)

    def test_check_minute_cov_raw_stock_1e6_raises(self, env):
        """STOCK 分钟 fresh raw 注入 1e-6（> 严格 1e-9）→ 抛 ReanchorBlocked。"""
        conn, cal = env.conn, env.calendar
        code = "600875"
        _seed_security(conn, code)
        fm = _snap(conn, "stock_minutes").copy()
        fm["close"] = fm["close"] + 1e-6
        with pytest.raises(ReanchorBlocked):
            _check_minute_cov_raw(conn, "STOCK", code, "1min", fm, None)

    # ---- 端到端（B-1 fresh_staged，92 只 ETF 生产实际路径）----
    def test_etf_minute_raw_1tick_admitted_fresh_staged(self, env):
        """ETF 分钟 fresh raw 注入 0.5 tick（0.0005）< 1e-3 → fresh_staged 不 BLOCK。"""
        conn, cal = env.conn, env.calendar
        code = "510300"
        _seed_security(conn, code, asset="ETF")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn(code, scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.0005   # 0.5 tick
        res = apply_reanchor_for_security(
            conn, asset_type="ETF", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **_AUDIT_KW)
        assert res.status == "committed"

    def test_etf_minute_raw_gt_1cent_blocked(self, env):
        """ETF 分钟 fresh raw 注入 >1 分钱（0.05）→ 仍 BLOCK（真实不一致）。"""
        conn, cal = env.conn, env.calendar
        code = "510300"
        _seed_security(conn, code, asset="ETF")
        pre_m = _snap(conn, "etf_minutes")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn(code, scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.05
        res = apply_reanchor_for_security(
            conn, asset_type="ETF", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "minute_raw_mismatch"
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "etf_minutes"))

    def test_stock_minute_raw_1e6_still_blocked(self, env):
        """STOCK 分钟 fresh raw 注入 1e-6（> 严格 1e-9）→ 仍 BLOCK（保持严格）。"""
        conn, cal = env.conn, env.calendar
        code = "600875"
        _seed_security(conn, code)
        pre_m = _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn(code, scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 1e-6
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "minute_raw_mismatch"
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))

    def test_stock_minute_raw_1tick_still_blocked(self, env):
        """对照：STOCK 即便仅 1 tick（0.0005）注入也 BLOCK——证明 STOCK 保持严格，
        而 ETF 分钟同等 1 tick 则放行（路由确实按资产区分）。"""
        conn, cal = env.conn, env.calendar
        code = "600875"
        _seed_security(conn, code)
        pre_m = _snap(conn, "stock_minutes")
        scales = _scales_exd5(F_600875)
        fm = _fresh_minutes_syn(code, scales)
        fm.loc[7, "close"] = float(fm.loc[7, "close"]) + 0.0005
        res = apply_reanchor_for_security(
            conn, asset_type="STOCK", code=code,
            fresh_daily=_fresh_daily(code, scales), calendar=cal,
            freqs=("1min",), ex_dates_ms=(D5,), list_date_ms=D1,
            model="fresh_staged", model_reason=_B1_REASON,
            fresh_minutes=fm, **_AUDIT_KW)
        assert res.status == "blocked"
        assert res.block_reason == "minute_raw_mismatch"
        pd.testing.assert_frame_equal(pre_m, _snap(conn, "stock_minutes"))
