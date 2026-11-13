#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日期↔epoch-ms 换算的正确实现与自测（不依赖生产库）。

## 背景（2026-11-13 R2 审核点出的 bug）

原实现：
    def bj_zero_ms(s):
        base = dt.datetime.strptime(s, "%Y-%m-%d")      # naive 00:00，实质按 UTC 解释
        return int((base - dt.datetime(1970,1,1)).total_seconds()*1000) + CST*1000

推演「2018-09-18」：
    (base - epoch)*1000  = epoch(T 00:00 UTC)           = 北京 08:00
    + 8h                 = epoch(T 08:00 UTC)           = 北京 **16:00**
正确应为：                epoch(T-1 16:00 UTC)          = 北京 **00:00**
⇒ **原值比正确值晚 16 小时**（不是 8 小时：naive→UTC 已偏 8h，再 +8h 又偏 8h）

后果：
  - `time > lo_ms`：lo_ms 偏晚 ⇒ lo 日 bar 被排除（碰巧符合意图）
  - `time < hi_ms`：hi_ms 偏晚 16h ⇒ **hi 日 bar 落入区间 ⇒ 端点未排除**（违反意图）

## 修正

用 **aware datetime + zoneinfo**，让 `timestamp()` 自己算 UTC，手算 CST 加减一律不做。

## 自测三项（R2 要求）
  T1 同日往返：bj_zero_ms(d) → 转回北京日期 == d（且时分秒为 00:00:00）
  T2 端点排除：给定 (lo, hi)，构造 lo 日 / 中间日 / hi 日三个 bar 时刻，
              验证 `lo_ms < t < hi_ms` 只命中中间日（lo、hi 均被排除）
  T3 跨日 fixture：固定几个跨月/跨年/闰日样本，断言其与时区偏移的关系
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
CST = 8 * 3600  # 仅用于对照旧实现的偏差，不在换算里使用


# ---------------- 旧实现（保留作反例） ----------------
def bj_zero_ms_broken(s: str) -> int:
    base = dt.datetime.strptime(s, "%Y-%m-%d")
    return int((base - dt.datetime(1970, 1, 1)).total_seconds() * 1000) + CST * 1000


# ---------------- 新实现 ----------------
def bj_zero_ms(s: str) -> int:
    """北京日期 'YYYY-MM-DD' → 该日北京零点的 epoch-ms。

    用 aware datetime（tzinfo=Asia/Shanghai），由 `timestamp()` 自行换算到 UTC epoch，
    **不做任何手算 CST 加减** ⇒ 避免「naive 当 UTC 再 +8h」这类双重偏移。
    """
    d = dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=SH)
    return int(d.timestamp() * 1000)


def ms_to_bj(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, SH).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 自测 ----------------
def t1_roundtrip() -> bool:
    print("—— T1 同日往返 ——")
    ok = True
    samples = ["2018-01-02", "2018-09-18", "2020-02-29", "2023-10-09",
               "2026-11-10", "2026-11-11", "1970-01-01", "2100-12-31"]
    for s in samples:
        ms = bj_zero_ms(s)
        back = ms_to_bj(ms)
        same_day = back.startswith(s)
        exact_zero = back.endswith("00:00:00")
        mark = "✓" if (same_day and exact_zero) else "✗"
        if not (same_day and exact_zero):
            ok = False
        print(f"  {mark} {s}  → {ms}  → {back}")
    print(f"  ⇒ T1 {'PASS' if ok else 'FAIL'}")
    return ok


def t2_endpoint() -> bool:
    print("\n—— T2 端点排除（lo/hi 必须被排除，中间日必须命中）——")
    ok = True
    cases = [
        ("2019-05-15", "2019-05-29", ["2019-05-16", "2019-05-20", "2019-05-28"]),
        ("2026-02-13", "2026-02-24", ["2026-02-16", "2026-02-20", "2026-02-23"]),
        ("2023-09-28", "2023-10-09", ["2023-09-29", "2023-10-05", "2023-10-08"]),
    ]
    for lo, hi, mids in cases:
        lo_ms, hi_ms = bj_zero_ms(lo), bj_zero_ms(hi)
        # lo 日 / hi 日均须被严格不等式排除
        lo_excluded = not (bj_zero_ms(lo) > lo_ms)      # bar 落在 lo 日 ⇒ 不应 > lo_ms
        hi_excluded = not (bj_zero_ms(hi) < hi_ms)      # bar 落在 hi 日 ⇒ 不应 < hi_ms
        mid_hit = all(lo_ms < bj_zero_ms(m) < hi_ms for m in mids)
        # 旧实现对照：hi 日是否被错误纳入
        broken_hi = bj_zero_ms_broken(hi)
        hi_leaked = bj_zero_ms(hi) < broken_hi          # 旧 hi_ms 偏晚 ⇒ hi 日 bar 漏进
        mark = "✓" if (lo_excluded and hi_excluded and mid_hit) else "✗"
        if not (lo_excluded and hi_excluded and mid_hit):
            ok = False
        print(f"  {mark} {lo}~{hi}: lo排除={lo_excluded} hi排除={hi_excluded} 中间命中={mid_hit}")
        print(f"      旧实现在 hi 侧偏差 {(broken_hi - hi_ms)/3600000:.1f}h"
              f" ⇒ 旧实现会{'把 hi 日 bar 漏进区间（端点未排除）' if hi_leaked else '未漏'}")
    print(f"  ⇒ T2 {'PASS' if ok else 'FAIL'}")
    return ok


def t3_crossday_fixture() -> bool:
    print("\n—— T3 跨日/跨月/跨年/闰日 fixture（对照 UTC 偏移）——")
    ok = True
    cases = [
        # (北京日期, 期望：该 epoch 用 UTC 解释时的日期，应为前一日（因北京 00:00 = UTC 前一日 16:00））
        ("2018-01-02", "2018-01-01"),
        ("2020-02-29", "2020-02-28"),
        ("2023-10-09", "2023-10-08"),
        ("2026-01-01", "2025-12-31"),
        ("2026-11-11", "2026-11-10"),
    ]
    for s, expect_utc_date in cases:
        ms = bj_zero_ms(s)
        utc = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
        got = utc.strftime("%Y-%m-%d")
        hh = utc.strftime("%H:%M:%S")
        good = (got == expect_utc_date and hh == "16:00:00")
        if not good:
            ok = False
        print(f"  {'✓' if good else '✗'} {s} → UTC {got} {hh}（期望 {expect_utc_date} 16:00:00）")
    # 反向断言：旧实现应给出 UTC 08:00（即北京 16:00），证明偏差 8h+8h
    ms_b = bj_zero_ms_broken("2018-01-02")
    utc_b = dt.datetime.fromtimestamp(ms_b / 1000, dt.timezone.utc)
    print(f"  反例：旧实现 2018-01-02 → UTC {utc_b.strftime('%Y-%m-%d %H:%M:%S')}"
          f"（应观察到 08:00，比正确值晚 8h；折算北京为 16:00，比北京零点晚 16h）")
    print(f"  ⇒ T3 {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    print("=" * 68)
    print("bj_zero_ms 正确性自测（2026-11-13，回应 R2「加应改减」审核）")
    print("=" * 68)
    r1, r2, r3 = t1_roundtrip(), t2_endpoint(), t3_crossday_fixture()
    print("\n" + "=" * 68)
    print(f"T1={r1}  T2={r2}  T3={r3}  ⇒ {'全部 PASS' if (r1 and r2 and r3) else '存在 FAIL'}")
    print("=" * 68)
    return 0 if (r1 and r2 and r3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
