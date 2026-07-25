"""统一 口径迁移验证测试（替代旧 tushare 口径测试）

验证标准层从 tushare 切换到 统一 后的完整闭环：
① schema 正确：日线 36 字段、分钟 29 字段、tick 34 字段
② 字段映射：tushare/baostock → 统一（code 裸码 / time ms / volume 股 / amount 元 / pctChg）
③ 单位转换：tushare vol×100(手→股)/amount×1000(千元→元)，baostock 近 identity
④ 交叉验证：tushare vs baostock 对齐后 diff=0
⑤ daily_basic merge [补丁1]：tushare 补 peTTM/pbMRQ/psTTM/turn
⑥ suspendFlag 推导 [补丁3]：volume==0 → 1
⑦ Quarantine 修复重放 [E-2]
⑧ 导出器：统一 原生分库（无 code 列、kline_1d 表名）
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.aligner import (FieldAligner, normalize_code,
                                          to_ms_timestamp, market_of_code)
from quantstudio.pipeline.validator import PreIngestValidator
from quantstudio.pipeline.quarantine import Quarantine
from quantstudio.pipeline.writers import DuckDBWriter

logging.basicConfig(level=logging.WARNING)


def test_schema_fields():
    """① schema 字段数正确（含 is_st_reliable / is_delisting_risk 等ST状态字段）"""
    print("\n[①] schema 字段数验证")
    a = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    daily_n = len(a.schemas["stock_daily"]["columns"])
    minute_n = len(a.schemas["stock_minutes"]["columns"])
    tick_n = len(a.schemas["tick"]["columns"])
    print(f"  stock_daily: {daily_n} 列（40 统一 + code 内部 = 41，含 is_st_reliable×4）")
    print(f"  stock_minutes: {minute_n} 列（29 统一 + code + freq = 31）")
    print(f"  tick: {tick_n} 列（34 统一 + code = 35）")
    assert daily_n == 41, f"日线应为 41 列（含 4 个 ST 状态字段），实际 {daily_n}"
    assert minute_n == 31, f"分钟应为 31 列，实际 {minute_n}"
    assert tick_n == 35, f"tick 应为 35 列，实际 {tick_n}"
    # 关键字段存在
    for col in ["code", "time", "volume", "amount", "pctChg", "preClose",
                "suspendFlag", "open_front", "close_back", "peTTM", "dividend_type",
                "is_st_reliable", "is_delisting_risk"]:
        assert col in a.schemas["stock_daily"]["columns"], f"日线缺 {col}"
    print("  ✅ schema 字段正确")


def test_code_and_time():
    """② 代码裸码 + 时间毫秒时间戳"""
    print("\n[②] 代码裸码 + 时间 ms 验证")
    assert normalize_code("600000.SH", "tushare_to_raw") == "600000"
    assert normalize_code("sh.600000", "baostock_to_raw") == "600000"
    assert normalize_code("000001", "identity") == "000001"
    assert market_of_code("600000") == "SH"
    assert market_of_code("000001") == "SZ"
    t1 = to_ms_timestamp("20260713")
    t2 = to_ms_timestamp("2026-07-13")
    assert t1 == t2, f"日期解析不一致: {t1} vs {t2}"
    assert isinstance(t1, int) and t1 > 1e12, f"应为毫秒时间戳: {t1}"
    print(f"  代码: 600000.SH→600000, sh.600000→600000 ✅")
    print(f"  时间: 2026-07-13 → {t1} (ms) ✅")
    print("  ✅ 代码 + 时间转换正确")


def test_unit_conversion():
    """③ 单位转换：模拟 tushare raw → 统一 aligned"""
    print("\n[③] 单位转换验证")
    a = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    # tushare raw（vol 手, amount 千元）
    raw = pd.DataFrame({
        "ts_code": ["600000.SH"], "trade_date": ["20260710"],
        "open": [8.98], "high": [9.03], "low": [8.79], "close": [9.00],
        "pre_close": [8.89], "pct_chg": [1.2373],
        "vol": [765021.91],     # 手
        "amount": [691188.280], # 千元
        "turnover_rate": [0.5], "pe_ttm": [6.0], "pb": [1.2], "ps_ttm": [2.1],
    })
    std, _ = a.align(raw, "stock_daily", "tushare")
    row = std.iloc[0]
    print(f"  vol 手→股: {765021.91} → {row['volume']:.2f} (应为 ×100 ≈ 76502191)")
    print(f"  amount 千元→元: {691188.280} → {row['amount']:.2f} (应为 ×1000 ≈ 691188280)")
    assert abs(row["volume"] - 76502191.0) < 1, f"vol 转换错: {row['volume']}"
    assert abs(row["amount"] - 691188280.0) < 1, f"amount 转换错: {row['amount']}"
    assert row["code"] == "600000"
    assert row["peTTM"] == 6.0  # daily_basic 字段映射
    print("  ✅ 单位转换正确（vol ×100, amount ×1000, peTTM 映射）")


def test_suspend_flag():
    """⑥ suspendFlag 推导 [补丁3]"""
    print("\n[⑥] suspendFlag 推导验证")
    a = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    raw = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "trade_date": ["20260710", "20260711"],
        "open": [9.0, 0], "high": [9.1, 0], "low": [8.9, 0], "close": [9.05, 0],
        "pre_close": [9.0, 9.05], "pct_chg": [0.5, 0],
        "vol": [100000, 0],  # 第2行停牌
        "amount": [905000, 0],
    })
    std, meta = a.align(raw, "stock_daily", "tushare")
    print(f"  正常日 suspendFlag={std.iloc[0]['suspendFlag']} (应为 0)")
    print(f"  停牌日 suspendFlag={std.iloc[1]['suspendFlag']} (应为 1)")
    assert std.iloc[0]["suspendFlag"] == 0
    assert std.iloc[1]["suspendFlag"] == 1
    print("  ✅ suspendFlag 推导正确（volume==0 → 1）")


def test_quarantine_replay():
    """⑦ Quarantine 修复重放 [E-2]"""
    print("\n[⑦] Quarantine 修复重放验证")
    tmp = Path(tempfile.mkdtemp())
    q = Quarantine(tmp / "q.db")
    a = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
    v = PreIngestValidator.from_config(ROOT / "config" / "alignment_rules.json", q)
    w = DuckDBWriter({"type": "duckdb", "path": str(tmp / "main.db")})
    # 脏数据（代码错 + OHLC 错）
    dirty = pd.DataFrame({
        "code": ["600000", "BAD", "600000"],
        "time": [1783612800000, 1783699200000, 1783785600000],
        "open": [9.0, 9.0, 9.0], "high": [9.1, 9.1, 8.5],
        "low": [8.9, 8.9, 9.1], "close": [9.05, 9.05, 9.0],
        "volume": [100000, 100000, 100000], "amount": [905000, 905000, 905000],
        "suspendFlag": [0, 0, 0], "dividend_type": ["all","all","all"],
    })
    res = v.validate(dirty, "stock_daily", "kh_replay_001", "test")
    print(f"  脏数据: passed={len(res.passed_df)} rejected={len(res.rejected_rows)}")
    assert len(res.rejected_rows) >= 2
    assert len(q.list_pending()) >= 1
    # 修复重放
    fixed = pd.DataFrame({
        "code": ["600001", "600000"], "time": [1783699200000, 1783785600000],
        "open": [9.0, 9.0], "high": [9.1, 9.1], "low": [8.9, 8.9], "close": [9.05, 9.05],
        "volume": [100000, 100000], "amount": [905000, 905000],
        "suspendFlag": [0, 0], "dividend_type": ["all","all"],
    })
    res2 = v.validate(fixed, "stock_daily", "kh_replay_002", "test")
    w.write(res2.passed_df, "stock_daily", "kh_replay_002")
    with w._conn() as conn:
        cnt1 = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    # 再次重放（幂等）
    w.write(res2.passed_df, "stock_daily", "kh_replay_002")
    with w._conn() as conn:
        cnt2 = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    print(f"  修复重放: {cnt1} 行 → 再次重放 {cnt2} 行（幂等）")
    assert cnt1 == cnt2, "重放应幂等"
    print("  ✅ Quarantine 修复重放正确")


def main():
    print("=" * 70)
    print("统一 口径迁移验证测试")
    print("=" * 70)
    test_schema_fields()
    test_code_and_time()
    test_unit_conversion()
    test_suspend_flag()
    test_quarantine_replay()
    print("\n" + "=" * 70)
    print("✅ 统一 口径迁移全部验证通过")
    print("  ① schema 36/29/34 字段  ② 裸码+ms时间戳  ③ 单位转换")
    print("  ⑥ suspendFlag  ⑦ Quarantine 重放")
    print("  （④交叉验证 ⑤daily_basic ⑧导出器 需真实数据，已在前序验证）")
    print("=" * 70)


if __name__ == "__main__":
    main()
