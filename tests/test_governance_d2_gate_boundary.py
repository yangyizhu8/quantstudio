# -*- coding: utf-8 -*-
"""D2 门槛脚本窗口边界单测（DSH 第 4 轮审计要求）

背景：v2 脚本 duck 侧公共窗口过滤 epoch_ms(strptime(...)) 按 UTC 解释边界，
每日 0 点 CST 时间戳被错切（首日整日被排除）。修复后改为 strftime 日期字符串
比较。本测试构造 CST 午夜时间戳的合成数据，验证首末日边界行为。
"""
import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_conn():
    con = duckdb.connect()
    con.execute("CREATE TABLE t(code VARCHAR, time BIGINT, v DOUBLE)")
    # 三个"交易日"，时间戳均为 CST 午夜 00:00（= UTC 前一日 16:00）
    cst_midnights = [
        ("000001", 1782835200000),  # 2026-07-01 00:00 CST
        ("000001", 1783094400000),  # 2026-07-04 00:00 CST
        ("000001", 1783267200000),  # 2026-07-06 00:00 CST
    ]
    for c, t in cst_midnights:
        con.execute("INSERT INTO t VALUES (?, ?, 1.0)", [c, t])
    return con


def test_window_includes_first_and_last_day_cst_midnight():
    """strftime 口径：窗口 [首日, 末日] 必须包含首末日全部行（CST 午夜时间戳）"""
    con = _make_conn()
    w_lo = con.execute(
        "select strftime(to_timestamp(min(time)/1000),'%Y-%m-%d') from t").fetchone()[0]
    w_hi = con.execute(
        "select strftime(to_timestamp(max(time)/1000),'%Y-%m-%d') from t").fetchone()[0]
    assert (w_lo, w_hi) == ("2026-07-01", "2026-07-06")
    n = con.execute(
        f"select count(*) from t where strftime(to_timestamp(time/1000),'%Y-%m-%d')"
        f" between '{w_lo}' and '{w_hi}'").fetchone()[0]
    assert n == 3, "首末日 CST 午夜行必须全部落入窗口"


def test_old_epoch_pattern_undercounts_first_day():
    """回归锚定：旧 epoch 口径会漏首日（证明 bug 存在，防止回退）"""
    con = _make_conn()
    n_old = con.execute(
        "select count(*) from t where time >= epoch_ms(strptime('2026-07-01','%Y-%m-%d')::timestamp)"
        " and time < epoch_ms((strptime('2026-07-06','%Y-%m-%d')::timestamp) + interval 1 day)"
    ).fetchone()[0]
    # 2026-07-01 00:00 CST = 2026-06-30 16:00 UTC < 2026-07-01 00:00 UTC → 首日被排除
    assert n_old == 2, "旧口径必须复现首日丢失（bug 锚定）"


def test_single_day_window_minutes():
    """分钟表口径：同日多 bar 全量计入且窗口首末 bar 不丢"""
    con = _make_conn()
    # 当日 09:30 与 15:00 CST 两个 bar（在 07-01 首日上）
    con.execute("INSERT INTO t VALUES ('000002', 1782869400000, 1.0)")  # 09:30 CST
    con.execute("INSERT INTO t VALUES ('000002', 1782889200000, 1.0)")  # 15:00 CST
    n = con.execute(
        "select count(*) from t where strftime(to_timestamp(time/1000),'%Y-%m-%d')"
        " between '2026-07-01' and '2026-07-01'").fetchone()[0]
    assert n == 3, "首日日线 1 行 + 分钟 2 bar 均须计入"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
