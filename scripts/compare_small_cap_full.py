"""小市值策略：本地回测 vs Ptrade 平台 全周期（到 2026-07-13）净值对照。

背景：
- 本地回测框架（ptrade_api + backtest_engine）跑 小市值策略ptrade.py。
- Ptrade 平台导出三件套（交易详情/持仓明细 仅到 04-29；Log.txt 订单流到 07-13）。
- 持仓明细只能重建到 04-29 的净值，故用 Log.txt 订单流重建全周期净值，
  并在重叠区间 [01-05, 04-29] 交叉校验重建准确性。

用法：
    python scripts/compare_small_cap_full.py <本地回测结果目录> <ptrade_samples目录>
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from quantstudio._paths import db_path
from quantstudio.backtest.ptrade_baseline import PtradeBaseline, PTRADE_INITIAL_CAPITAL


def load_local_nav(result_dir: Path) -> pd.DataFrame:
    """从本地回测结果的 daily_stats.csv 读取净值（total_asset / init_capital）。"""
    ds = pd.read_csv(result_dir / "daily_stats.csv")
    ds['date'] = pd.to_datetime(ds['date'])
    ds['nav'] = ds['total_asset'] / PTRADE_INITIAL_CAPITAL
    return ds[['date', 'nav', 'total_asset']]


def main():
    if len(sys.argv) < 3:
        print("用法: python scripts/compare_small_cap_full.py <本地结果目录> <ptrade_samples目录>")
        sys.exit(1)
    result_dir = Path(sys.argv[1])
    ptrade_dir = Path(sys.argv[2])

    # ---- Ptrade 基准 ----
    bl = PtradeBaseline().load_dir(ptrade_dir)
    # 持仓明细路径净值（到 04-29，ground truth）
    nav_holdings = bl.compute_nav()
    # 以持仓明细末态为起点，重放 Log.txt 后续订单（04-30→07-13）
    nav_log = bl.rebuild_nav_from_log(db_path())
    # 拼接全周期 Ptrade 净值
    full_ptrade = pd.concat([nav_holdings, nav_log]).drop_duplicates('date').sort_values('date').reset_index(drop=True)
    print(f"[Ptrade] 全周期净值: {full_ptrade['date'].min().date()} ~ {full_ptrade['date'].max().date()} "
          f"({len(full_ptrade)} 天)，持仓明细段 {len(nav_holdings)} 天 + Log.txt 段 {len(nav_log)} 天")

    # ---- 本地净值 ----
    local = load_local_nav(result_dir)

    # ---- 对齐到共同日期 ----
    ptrade = full_ptrade.rename(columns={'nav': 'nav_ptrade', 'total': 'total_ptrade'})[['date', 'nav_ptrade']]
    df = local.merge(ptrade, on='date', how='inner')
    df = df.sort_values('date').reset_index(drop=True)

    local_end = df['nav'].iloc[-1]
    ptrade_end = df['nav_ptrade'].iloc[-1]
    local_ret = local_end - 1
    ptrade_ret = ptrade_end - 1
    end_diff_pp = (local_ret - ptrade_ret) * 100

    # 逐日净值偏差（相对 Ptrade）
    df['nav_dev'] = (df['nav'] - df['nav_ptrade']).abs() / df['nav_ptrade']
    max_dev = df['nav_dev'].max()
    mean_dev = df['nav_dev'].mean()
    max_dev_date = df.loc[df['nav_dev'].idxmax(), 'date'].date()

    # 04-29 重叠点偏差（应与持仓明细路径吻合）
    tail = df[df['date'] <= pd.Timestamp('2026-04-29')]
    dev_0429 = abs(tail['nav'].iloc[-1] - tail['nav_ptrade'].iloc[-1]) / tail['nav_ptrade'].iloc[-1]

    print("=" * 64)
    print("小市值策略 本地回测 vs Ptrade 平台 全周期对照")
    print("=" * 64)
    print(f"对齐区间      : {df['date'].min().date()} ~ {df['date'].max().date()} ({len(df)} 交易日)")
    print(f"本地 期末净值 : {local_end:.4f}  (收益率 {local_ret*100:+.2f}%)")
    print(f"Ptrade期末净值: {ptrade_end:.4f}  (收益率 {ptrade_ret*100:+.2f}%)")
    print(f"期末收益差    : {end_diff_pp:+.2f} pp   (阈值 ±5% = ±0.05)")
    print(f"04-29 节点偏差: {dev_0429*100:.3f}%  (应≈0，验证前期对齐)")
    print(f"逐日净值最大偏差: {max_dev*100:.2f}%  (日期 {max_dev_date})")
    print(f"逐日净值平均偏差: {mean_dev*100:.2f}%")
    # 相关性
    corr = df['nav'].corr(df['nav_ptrade'])
    print(f"净值序列相关系数: {corr:.4f}")

    verdict = "PASS(≤5%)" if abs(end_diff_pp) <= 5.0 else "FAIL(>5%)"
    print(f"\n结论: 期末收益差 {abs(end_diff_pp):.2f}pp → {verdict}")

    # 输出合并净值到 csv 便于核查
    out = result_dir / "compare_nav_full.csv"
    df[['date', 'nav', 'nav_ptrade', 'nav_dev']].to_csv(out, index=False)
    print(f"合并净值已导出: {out}")


if __name__ == "__main__":
    main()
