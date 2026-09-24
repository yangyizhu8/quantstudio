"""6 表 schema 探测：确认各表的时间列/代码列真实名称（修 6 表规则的异构假设缺陷）。

背景：首版 rowcount_watermark 规则假设 6 表同构（trade_date/ts_code）——实测
  index_constituents 用 code/time（canonical 风格）、stk_factor_pro 本机不存在 ⇒ 假设错误。
本脚本枚举 6 表的存在性、行数、时间列候选与代码列候选。
"""
import sys
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
DB = ROOT / "data" / "quantstudio.db"
TABLES = ["cyq_chips", "ths_daily", "ths_hot", "sw_daily", "stk_factor_pro",
          "index_constituents"]
TIME_CANDIDATES = ["trade_date", "date", "time", "cal_date", "end_date", "trade_time"]
CODE_CANDIDATES = ["ts_code", "code", "index_code", "stock_code"]

import duckdb  # noqa: E402

con = duckdb.connect(str(DB), read_only=True)
print(f"库={DB.name}")
for t in TABLES:
    try:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    except Exception as e:
        print(f"\n### {t}: 不存在/不可读 ({type(e).__name__})")
        continue
    n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    tcol = next((c for c in TIME_CANDIDATES if c in cols), None)
    ccol = next((c for c in CODE_CANDIDATES if c in cols), None)
    print(f"\n### {t}  rows={n:,}")
    print(f"    时间列={tcol}  代码列={ccol}")
    print(f"    全部列({len(cols)}): {cols[:14]}{' …' if len(cols) > 14 else ''}")
    if tcol:
        try:
            nd = con.execute(f'SELECT COUNT(DISTINCT "{tcol}") FROM "{t}"').fetchone()[0]
            mx = con.execute(f'SELECT MAX("{tcol}") FROM "{t}"').fetchone()[0]
            print(f"    distinct({tcol})={nd:,}  max={mx}")
        except Exception as e:
            print(f"    时间列统计失败: {e}")
con.close()