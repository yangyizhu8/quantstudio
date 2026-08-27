"""小市值策略选股差异排查脚本（临时，不入库）。
在本地引擎中模拟 before_trading_start 07-01 的完整选股链路，
打印 100 只候选 + 市值排名 + 过滤结果 + 前 10 只，供与 PTrade 对比。"""
import sys
sys.path.insert(0, '.')

# 手动构造最小环境复现 before_trading_start
from quantstudio.backtest.strategy_runner import load_strategy
from quantstudio.backtest import ptrade_api as _api

strategy_path = "quantstudio/backtest/strategies/小市值策略ptrade.py"

# 1) 加载策略（注入 API）
funcs, module = load_strategy(strategy_path)

# 2) 找到 initialize 并执行（获取 g 配置）
import types
class FakeCtx:
    previous_date = "2026-06-30"   # 07-01 的前一交易日
    current_dt = None
    portfolio = types.SimpleNamespace(cash=100000, positions={})

# 我们需要引擎环境才能执行 get_index_stocks 等，改用直接查数据库的方式
# —— 直接打印本地数据链路的原始数据
import duckdb
con = duckdb.connect('data/quantstudio.db', read_only=True)

from quantstudio.backtest.providers.duckdb_provider import _start_ms
ms_0630 = _start_ms('2026-06-30')
ms_0701 = _start_ms('2026-07-01')

print("=" * 70)
print("排查 1：399101 中小板综成分股（本地 vs PTrade）")
print("=" * 70)
# 指数成分（用最新快照）
r = con.execute("""
    SELECT COUNT(DISTINCT code) FROM index_constituents 
    WHERE index_code = '399101'
""").fetchone()
print(f"本地 399101 成分股数量: {r[0]}")
# PTrade 端请在策略中 log.info(len(g.stock_list)) 对比

print()
print("=" * 70)
print("排查 2：PTrade 选的 5 只 vs 本地选的 5 只的市值对比")
print("=" * 70)
ptrade_picks = ['003003', '002200', '002188', '002809', '002494']
local_picks  = ['002719', '002872', '002193', '003003', '002200']
all_codes = list(set(ptrade_picks + local_picks))
ph = ','.join(['?'] * len(all_codes))

# 查 stock_float_share（流通股本）+ stock_daily（收盘价）→ 流通市值
rows = con.execute(f"""
    SELECT f.code, f.float_share, d.close, 
           f.float_share * d.close AS float_value_calc
    FROM stock_float_share f
    LEFT JOIN stock_daily d ON d.code = f.code AND d.time = ?
    WHERE f.code IN ({ph}) AND f.time = (
        SELECT MAX(time) FROM stock_float_share WHERE code = f.code AND time <= ?
    )
""", [ms_0630] + all_codes + [ms_0630]).fetchall()

print(f"\n{'code':10} {'float_share':>14} {'close':>8} {'float_mv':>14} {'来源':>6}")
for code, fs, close, fmv in sorted(rows, key=lambda x: x[3] or 0):
    src = 'PTrade选' if code in ptrade_picks else ('本地选' if code in local_picks else '')
    both = '两端都选' if code in ptrade_picks and code in local_picks else src
    print(f"{code:10} {fs or 0:>14,.0f} {close or 0:>8.3f} {fmv or 0:>14,.0f} {both:>8}")

print()
print("=" * 70)
print("排查 3：本地完整市值排名（前 20，含是否在 PTrade 前 5 中）")
print("=" * 70)
# 本地排名前 20 的最小流通市值（模拟 get_fundamentals 逻辑）
top20 = con.execute(f"""
    SELECT f.code, f.float_share * d.close AS float_mv
    FROM stock_float_share f
    JOIN stock_daily d ON d.code = f.code AND d.time = ?
    JOIN (SELECT DISTINCT code FROM index_constituents WHERE index_code = '399101') ic
        ON ic.code = f.code
    WHERE f.time = (SELECT MAX(time) FROM stock_float_share WHERE code = f.code AND time <= ?)
    ORDER BY float_mv ASC
    LIMIT 20
""", [ms_0630, ms_0630]).fetchall()

print(f"\n{'rank':>4} {'code':10} {'float_mv':>14} {'标记':>10}")
for i, (code, fmv) in enumerate(top20, 1):
    tag = ''
    if code in local_picks: tag += '本地选 '
    if code in ptrade_picks: tag += 'PTrade选'
    print(f"{i:>4} {code:10} {fmv:>14,.0f} {tag:>10}")

print()
print("=" * 70)
print("排查 4：002719/002872/002193 是否在 399101 成分股中")
print("=" * 70)
for code in ['002719', '002872', '002193', '002188', '002809', '002494']:
    r = con.execute("SELECT COUNT(*) FROM index_constituents WHERE index_code='399101' AND code=?", [code]).fetchone()
    in_index = "在" if r[0] > 0 else "不在"
    print(f"  {code}: {in_index} 399101 成分股中")

con.close()
print("\n=== 排查完成 ===")
print("对比方法：将上表与 PTrade 端 log 打印的 g.stock_list / float_value 对比")
