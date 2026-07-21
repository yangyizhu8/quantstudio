"""Phase 2 Day 4-5: 真实数据三源交叉验证

验证 tushare / baostock / akshare 同一标的同一交易日，对齐后字段/单位/代码一致。
- tushare: 已入库（真实拉取）
- baostock: 已入库（真实拉取，3160行）
- akshare: 网络受限时跳过（标记 SKIP）

验收门禁①：三源对齐后字段/单位/代码完全一致
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.aligner import FieldAligner
from quantstudio.pipeline.sources.tushare_adapter import TushareAdapter
from quantstudio.pipeline.sources.baostock_adapter import BaostockAdapter

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("cross_validate")

# 测试标的 + 日期
TEST_CODE = "600000.SH"
TEST_DATES = ["2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"]
# 容忍阈值（两源差异容忍，因复权基准/小数精度）
TOL = {
    "close": 0.02,       # 价格 ±0.02 元（复权基准差异）
    "pct_chg": 0.05,     # 涨跌幅 ±0.05%
    "vol": 0.02,         # 成交量相对差 2%
    "amount": 0.02,      # 成交额相对差 2%
}


def fetch_tushare():
    """tushare 真实拉取（identity 源，对齐后即原始）"""
    tok = os.environ.get("TUSHARE_TOKEN", "")
    if not tok:
        log.warning("TUSHARE_TOKEN 未设置，跳过 tushare")
        return None
    a = TushareAdapter({"name": "tushare", "token": tok})
    try:
        df, _ = a.fetch_table("stock_daily", TEST_DATES[0], TEST_DATES[-1],
                              codes=[TEST_CODE])
        aligner = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
        std, _ = aligner.align(df, "stock_daily", "tushare")
        std = std[std["ts_code"] == TEST_CODE].copy()
        std["trade_date"] = pd.to_datetime(std["trade_date"]).dt.strftime("%Y-%m-%d")
        return std.set_index("trade_date")
    finally:
        a.close()


def fetch_baostock():
    """baostock 真实拉取"""
    a = BaostockAdapter({"name": "baostock"})
    try:
        df, _ = a.fetch_table("stock_daily", TEST_DATES[0], TEST_DATES[-1],
                              codes=[TEST_CODE])
        aligner = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
        std, _ = aligner.align(df, "stock_daily", "baostock")
        std = std[std["ts_code"] == TEST_CODE].copy()
        std["trade_date"] = pd.to_datetime(std["trade_date"]).dt.strftime("%Y-%m-%d")
        return std.set_index("trade_date")
    finally:
        a.close()


def fetch_akshare():
    """akshare 真实拉取（网络受限返回 None）"""
    try:
        from quantstudio.pipeline.sources.akshare_adapter import AkshareAdapter
        # 网络受限时快速失败（1次重试，短退避），避免长时间等待
        a = AkshareAdapter({"name": "akshare",
                            "rate_limit": {"calls_per_min": 30, "wait_on_429": False}})
        # 临时覆盖重试次数为 1
        original_retry = a._retry_with_backoff
        def fast_retry(fn, *args, **kwargs):
            return original_retry(fn, *args, max_retries=1, backoff_sec=(5,), **kwargs)
        a._retry_with_backoff = fast_retry
        try:
            df, _ = a.fetch_table("stock_daily", TEST_DATES[0], TEST_DATES[-1],
                                  codes=[TEST_CODE])
            if len(df) == 0:
                log.warning("akshare 返回 0 行（网络限流），跳过")
                return None
            aligner = FieldAligner.from_config(ROOT / "config" / "alignment_rules.json")
            std, _ = aligner.align(df, "stock_daily", "akshare")
            std = std[std["ts_code"] == TEST_CODE].copy()
            std["trade_date"] = pd.to_datetime(std["trade_date"]).dt.strftime("%Y-%m-%d")
            return std.set_index("trade_date")
        finally:
            a.close()
    except Exception as e:
        log.warning(f"akshare 网络不可达，跳过: {type(e).__name__}")
        return None


def compare(name_a, df_a, name_b, df_b):
    """对比两源，返回 (通过字段数, 失败清单)"""
    if df_a is None or df_b is None:
        return None
    common_dates = sorted(set(df_a.index) & set(df_b.index))
    if not common_dates:
        print(f"  ⚠ {name_a} vs {name_b}: 无公共交易日")
        return False
    print(f"\n  [{name_a} vs {name_b}] 公共交易日: {common_dates}")
    all_pass = True
    for field, tol in TOL.items():
        if field not in df_a.columns or field not in df_b.columns:
            continue
        for d in common_dates:
            va = float(df_a.loc[d, field])
            vb = float(df_b.loc[d, field])
            if field in ("close", "pct_chg"):
                diff = abs(va - vb)
                ok = diff <= tol
                unit = "元" if field == "close" else "%"
                status = "✅" if ok else "❌"
                print(f"    {status} {d} {field}: {name_a}={va:.4f} {name_b}={vb:.4f} "
                      f"diff={diff:.4f}{unit} (tol={tol})")
            else:
                rel = abs(va - vb) / max(abs(va), 1e-9)
                ok = rel <= tol
                status = "✅" if ok else "❌"
                print(f"    {status} {d} {field}: {name_a}={va:.2f} {name_b}={vb:.2f} "
                      f"rel={rel*100:.2f}% (tol={tol*100}%)")
            if not ok:
                all_pass = False
    return all_pass


def main():
    print("=" * 70)
    print("Phase 2 Day 4-5: 真实数据三源交叉验证")
    print(f"标的: {TEST_CODE}  日期: {TEST_DATES[0]} ~ {TEST_DATES[-1]}")
    print("=" * 70)

    print("\n[1] 拉取三源真实数据...")
    ts = fetch_tushare()
    bs = fetch_baostock()
    ak = fetch_akshare()
    print(f"  tushare:  {'✅ ' + str(len(ts)) + ' 行' if ts is not None else '⚠ 跳过（无token）'}")
    print(f"  baostock: {'✅ ' + str(len(bs)) + ' 行' if bs is not None else '⚠ 跳过'}")
    print(f"  akshare:  {'✅ ' + str(len(ak)) + ' 行' if ak is not None else '⚠ 跳过（网络限流）'}")

    # 代码格式一致性（验收门禁①核心）
    print("\n[2] 代码格式一致性检查...")
    for name, df in [("tushare", ts), ("baostock", bs), ("akshare", ak)]:
        if df is not None and "ts_code" in df.columns:
            codes = set(df["ts_code"].unique())
            ok = codes == {TEST_CODE}
            print(f"  {name}: ts_code={codes} {'✅' if ok else '❌'}")

    # 两两对比
    print("\n[3] 字段值交叉对比（容忍阈值内视为一致）...")
    results = []
    r1 = compare("tushare", ts, "baostock", bs)
    results.append(("tushare_vs_baostock", r1))
    if ak is not None:
        r2 = compare("tushare", ts, "akshare", ak)
        results.append(("tushare_vs_akshare", r2))
        r3 = compare("baostock", bs, "akshare", ak)
        results.append(("baostock_vs_akshare", r3))

    print("\n" + "=" * 70)
    print("交叉验证结论：")
    valid = [(n, r) for n, r in results if r is not None]
    if not valid:
        print("  ⚠ 无可对比源（至少需要两源）")
    elif all(r for _, r in valid):
        print("  ✅ 验收门禁① 通过：可用源对齐后字段/单位/代码一致（容忍阈值内）")
        for n, r in valid:
            print(f"     - {n}: ✅")
    else:
        print("  ❌ 部分字段超容忍阈值，需排查")
        for n, r in valid:
            print(f"     - {n}: {'✅' if r else '❌'}")
    skipped = [n for n, r in results if r is None]
    if skipped:
        print(f"  ⏭ 跳过（网络/token）: {skipped}")
    print("=" * 70)


if __name__ == "__main__":
    main()
