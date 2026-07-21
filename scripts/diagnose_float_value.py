"""D10 预研：float_value 数据源精度差异诊断。

根因分析（B5 检查点暴露）：本地 vs Ptrade 首日选股完全不同
- 本地选股: {002231, 002808, 002872, 002898}
- Ptrade 选股: {002719, 002809, 002830, 002888}

诊断方法（三层）：
1. 直接对比：Ptrade 选的票在本地 curr_float_value 排名是多少？
   - 若都在 top 10 → 微小数值差异导致的边界翻转（<1% 噪声）
   - 若排名靠后 → 数据源差异巨大（>5%）
2. 候选池排名分布：中小板综成分股按 curr_float_value 排序，看 top 15 的分布
3. 持仓市值反推：Ptrade 持仓市值 ≈ 20000/只，反推 Ptrade 的 a_floats 与本地对比

关键指标：curr_float_value = free_share × close_price（策略 handle_data 实际用的口径）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from quantstudio._paths import db_path
sys.path.insert(0, str(ROOT))

import duckdb
import pandas as pd
import numpy as np


def diagnose():
    conn = duckdb.connect(str(db_path()), read_only=True)

    # 首日：2026-01-05（B5 报告显示选股分歧的日子）
    day_str = "2026-01-05"
    prev_day_str = "2026-01-04"  # before_trading_start 用 previous_date 取市值
    day_ms = int(pd.Timestamp(day_str, tz='Asia/Shanghai').timestamp() * 1000)
    prev_ms = int(pd.Timestamp(prev_day_str, tz='Asia/Shanghai').timestamp() * 1000) + 86_399_999

    # 取中小板综（399101）成分股
    print(f"=== D10 诊断：{day_str} ===\n")
    idx_stocks = conn.execute("""
        SELECT DISTINCT code FROM index_constituents WHERE index_code='399101'
    """).fetchdf()['code'].tolist()
    print(f"中小板综成分股: {len(idx_stocks)} 只")

    # 取这些成分股前一日（2026-01-04）的流通市值 + 流通股本
    # 用临时表绕过 DuckDB 的 unnest IN 子句限制
    idx_list = [(c,) for c in idx_stocks]
    conn.register('_idx_stocks', pd.DataFrame(idx_list, columns=['code']))
    float_df = conn.execute(f"""
        SELECT s.code, s.circ_mv, s.free_share, s.total_share, s.time
        FROM stock_float_share s
        WHERE s.code IN (SELECT code FROM _idx_stocks)
          AND s.time <= {prev_ms}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY s.code ORDER BY s.time DESC) = 1
    """).fetchdf()
    print(f"前一日有流通市值数据的: {len(float_df)} 只\n")

    # 取当日（2026-01-05）收盘价（用于计算 curr_float_value）
    price_df = conn.execute(f"""
        SELECT code, close FROM stock_daily
        WHERE time = {day_ms}
          AND code IN (SELECT code FROM _idx_stocks)
    """).fetchdf()

    # 合并计算 curr_float_value = free_share × close（策略 handle_data 实际口径）
    # 单位确认：free_share 单位是"股"（circ_mv / free_share = 股价），无需额外系数
    df = float_df.merge(price_df, on='code', how='inner')
    df['curr_float_value'] = df['free_share'] * df['close']  # 单位：元（与 circ_mv 同口径）
    print(f"=== free_share / circ_mv 量级检查（前3只）===")
    print(df[['code', 'free_share', 'circ_mv', 'close', 'curr_float_value']].head(3).to_string())
    print()

    # 校准：curr_float_value 应该 ≈ circ_mv（昨日股本×今日价 vs 昨日股本×昨日价，仅价差）
    df['check_ratio'] = df['circ_mv'] / df['curr_float_value']
    print(f"circ_mv / curr_float_value 比值分布（应接近 1.0，仅反映日间价差）:")
    print(f"  median={df['check_ratio'].median():.4f}, mean={df['check_ratio'].mean():.4f}")
    print(f"  → 接近 1.0 说明 curr_float_value 口径正确\n")

    # ==== 诊断 1：Ptrade 选的票在本地排名 ====
    ptrade_picks = ['002719', '002809', '002830', '002888']  # B5 报告 Ptrade 选股
    local_picks = ['002231', '002808', '002872', '002898']   # B5 报告本地选股

    df_sorted = df.sort_values('curr_float_value').reset_index(drop=True)
    df_sorted['rank'] = range(1, len(df_sorted) + 1)

    print(f"=== 诊断 1：两边选股在本地 curr_float_value 排名 ===")
    print(f"{'票':>10} | {'curr_float_value':>16} | {'本地排名':>8} | 来源")
    for code in ptrade_picks + local_picks:
        row = df_sorted[df_sorted['code'] == code]
        if len(row) > 0:
            r = int(row['rank'].iloc[0])
            v = row['curr_float_value'].iloc[0]
            src = "Ptrade" if code in ptrade_picks else "本地"
            print(f"{code:>10} | {v:>16,.0f} | {r:>8} | {src}")
        else:
            print(f"{code:>10} | {'缺失':>16} | {'-':>8} | (本地无数据)")
    print()

    # ==== 诊断 2：top 15 排名分布 ====
    print(f"=== 诊断 2：本地 curr_float_value top 15 ===")
    print(f"{'排名':>4} | {'票':>8} | {'curr_float_value':>16} | 在哪边选中")
    top15 = df_sorted.head(15)
    for _, row in top15.iterrows():
        code = row['code']
        in_p = "Ptrade" if code in ptrade_picks else ""
        in_l = "本地" if code in local_picks else ""
        tag = f"{in_p}{in_l}" if (in_p or in_l) else "-"
        print(f"{int(row['rank']):>4} | {code:>8} | {row['curr_float_value']:>16,.0f} | {tag}")
    print()

    # ==== 诊断 3：边界翻转分析 ====
    print(f"=== 诊断 3：top 10 边界（决定选股的关键区间）===")
    top10 = df_sorted.head(10)
    if len(top10) >= 2:
        values = top10['curr_float_value'].values
        # top 5 与 top 6-10 的相对差距
        top5_max = values[:5].max()
        top6_10 = values[5:10] if len(values) >= 10 else values[5:]
        if len(top6_10) > 0:
            gap = (top6_10.min() - top5_max) / top5_max * 100
            print(f"  top5 最大市值: {top5_max:,.0f}")
            print(f"  top6-10 最小市值: {top6_10.min():,.0f}")
            print(f"  边界间距: {gap:+.2f}%（负=重叠，正=有间隙）")
            print(f"  → 若间距 < 1%，微小数据差异即可导致选股翻转")
    print()

    # ==== 诊断 4：抽样偏差分布（跨沪深300/中证500/中证1000）====
    print(f"=== 诊断 4：跨指数抽样（流通市值偏差分布）===")
    # 由于 Ptrade 无 float_value 导出，这里统计本地数据的内部一致性
    # 重点：top 10 的市值有多集中（越集中越容易因数据源差异翻转）
    top10_values = df_sorted.head(10)['curr_float_value'].values
    if len(top10_values) >= 5:
        cv = np.std(top10_values) / np.mean(top10_values)  # 变异系数
        print(f"  top 10 市值变异系数 CV: {cv:.3f}")
        print(f"  top 10 市值范围: {top10_values.min():,.0f} ~ {top10_values.max():,.0f}")
        print(f"  → CV < 0.1 表示高度集中，数据源差异极易翻转排名")
    print()

    # ==== 结论 ====
    print("="*60)
    ptrade_ranks = []
    for code in ptrade_picks:
        row = df_sorted[df_sorted['code'] == code]
        if len(row) > 0:
            ptrade_ranks.append(int(row['rank'].iloc[0]))
    local_ranks = []
    for code in local_picks:
        row = df_sorted[df_sorted['code'] == code]
        if len(row) > 0:
            local_ranks.append(int(row['rank'].iloc[0]))

    print(f"=== D10 结论 ===")
    print(f"Ptrade 选股在本地排名: {ptrade_ranks}")
    print(f"本地选股在本地排名: {local_ranks}")
    if ptrade_ranks and max(ptrade_ranks) <= 10 and local_ranks and max(local_ranks) <= 10:
        print(f"→ 两组都在 top 10 内：属 <1% 微小数据差异导致的边界翻转")
        print(f"→ 分支决策：Phase C1 scorer 引入排名缓冲带（取 top 15 再二次过滤）")
    elif ptrade_ranks and max(ptrade_ranks) <= 20:
        print(f"→ Ptrade 选股在 top 20 内：属 1-5% 中等差异")
        print(f"→ 分支决策：需进一步量化，可能需缓冲带 + 数据源优化")
    else:
        print(f"→ 排名差异显著：可能 >5%，考虑切 xtquant 权威源")

    conn.close()


if __name__ == "__main__":
    diagnose()
