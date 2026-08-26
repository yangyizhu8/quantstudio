"""P-D13 审计条件①：D2 默认映射（eps=basic）的 1.9% fin/inc 差异行影响量化。

量化目标：
  1. 本地 DB 中 fin_indicator.eps vs income_statement.basic_eps 的差异行数/比例
  2. 这些差异行在 `eps > 0` 过滤（weekly L6 规则）下的**符号翻转数**（正→负或负→正）
  3. 对 6 策略中消费 eps 的策略（weekly/周频/CANSLIM）的选股影响评估

只读：read_only 连接。
"""
import duckdb
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))

# 检查日（与 weekly 策略 PIT 检查一致）
CHECK_DATES = ["2026-07-01", "2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27"]


def pit_ms(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def main():
    conn = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)

    for asof in CHECK_DATES:
        ms = pit_ms(asof)
        rows = conn.execute(f"""
            WITH fi AS (
                SELECT regexp_replace(code, '\\\\.[A-Z]+$', '') AS bare,
                       end_date, eps AS fin_eps,
                       ROW_NUMBER() OVER (PARTITION BY regexp_replace(code, '\\\\.[A-Z]+$', '')
                                          ORDER BY end_date DESC, ann_date DESC) AS rn
                FROM fin_indicator WHERE ann_date <= {ms}
            ),
            is_ AS (
                SELECT regexp_replace(code, '\\\\.[A-Z]+$', '') AS bare,
                       end_date, basic_eps AS is_eps,
                       ROW_NUMBER() OVER (PARTITION BY regexp_replace(code, '\\\\.[A-Z]+$', '')
                                          ORDER BY end_date DESC, ann_date DESC) AS rn
                FROM income_statement WHERE ann_date <= {ms}
            )
            SELECT fi.bare, fi.fin_eps, is_.is_eps, fi.end_date AS fi_end, is_.end_date AS is_end
            FROM fi JOIN is_ ON fi.bare = is_.bare AND is_.rn = 1
            WHERE fi.rn = 1
        """).fetchall()

        total = len(rows)
        diffs = [(r[0], r[1], r[2]) for r in rows
                 if r[1] is not None and r[2] is not None
                 and abs(float(r[1]) - float(r[2])) > 1e-9]
        # 符号翻转：eps>0 过滤下两端结论不同
        sign_flips = [(r[0], r[1], r[2]) for r in diffs
                      if (float(r[1]) > 0) != (float(r[2]) > 0)]
        period_mismatch = sum(1 for r in rows if r[3] != r[4])

        print(f"\n[{asof}] total_paired={total}  diff_rows={len(diffs)}"
              f" ({len(diffs)/max(total,1)*100:.1f}%)  sign_flips={len(sign_flips)}"
              f"  period_mismatch={period_mismatch}")
        if sign_flips:
            for code, fe, ie in sign_flips[:5]:
                print(f"  SIGN_FLIP {code}: fin_eps={fe} is_eps={ie}")
        if diffs and len(diffs) <= 10:
            for code, fe, ie in diffs[:5]:
                print(f"  diff {code}: fin={fe} is={ie} (same sign)")

    conn.close()
    print("\n结论：sign_flips=0 → D2 默认 basic 对 eps>0 过滤零影响；"
          "sign_flips>0 → 列出受影响代码（需合并基线重验覆盖）")


if __name__ == "__main__":
    main()
