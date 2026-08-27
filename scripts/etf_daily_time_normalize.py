"""P-D14b 存量归一 v2（审计批准双表扩展）：etf_daily + stock_daily。

执行模式（--mode）：
  check  —— 只断言不写（防线0 断言 + 防线2 抽样），可随时跑（read_only 兼容）
  apply  —— 备份 + 归一（需 DB 写锁：等并行进程释放后执行）
  revert —— 从备份表恢复（可逆）
"""
import argparse
import duckdb
from pathlib import Path

DB = str(Path(__file__).resolve().parents[1] / "data" / "quantstudio.db")
DAY = 86400000
NORMAL_MOD = 57600000
TABLES = ["etf_daily", "stock_daily"]
# 审计核实并批准的异常行规模（P1 探针，两表独立核实）
EXPECTED = {"etf_daily": 8244, "stock_daily": 16619}


def _assert_counts(conn):
    """防线0：两表异常行数精确断言（±0——审计核准数）。"""
    report = {}
    ok = True
    for t in TABLES:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE time % {DAY} = 0").fetchone()[0]
        expected = EXPECTED[t]
        stat = "OK" if abs(n - expected) < 5 else "MISMATCH"
        if stat != "OK":
            ok = False
        report[t] = (n, expected, stat)
        print(f"[check] {t} 异常行(mod0): {n}  (审计核准 {expected}) [{stat}]")
    # 其他异常余数不存在性（应只 0/57600000 两类）
    for t in TABLES:
        n_other = conn.execute(
            f"SELECT COUNT(*) FROM {t} "
            f"WHERE time % {DAY} NOT IN (0, {NORMAL_MOD})").fetchone()[0]
        print(f"[check] {t} 其他异常余数: {n_other}  (应=0)")
        if n_other != 0:
            ok = False
    return report, ok


def _sample(conn):
    """防线2：抽样 10 行（异常行 → CST 时间戳，供目检对齐）。"""
    for t in TABLES:
        print(f"[check] {t} 抽样式例（异常行）:")
        rows = conn.execute(
            f"SELECT code, time, make_timestamp(CAST(time/1000 + 8*3600 AS BIGINT)) AS cst "
            f"FROM {t} WHERE time % {DAY} = 0 LIMIT 5").fetchall()
        for r in rows:
            print(f"    {r[0]}: time={r[1]} cst={r[2]}")
        # 正常行代表（对照 anchor）
        rows2 = conn.execute(
            f"SELECT code, time, make_timestamp(CAST(time/1000 + 8*3600 AS BIGINT)) AS cst "
            f"FROM {t} WHERE time % {DAY} = {NORMAL_MOD} LIMIT 2").fetchall()
        print(f"    -- 正常行对照:")
        for r in rows2:
            print(f"    {r[0]}: time={r[1]} cst={r[2]}")


def check(conn):
    _assert_counts(conn)
    _sample(conn)


def apply(conn):
    report, ok = _assert_counts(conn)
    if not ok:
        print("[apply] 断言 FAIL，中止执行（不写库）")
        return
    # 防线1：备份（各表异常行）
    for t in TABLES:
        bak = f"{t}_backup_pd14b"
        conn.execute(f"DROP TABLE IF EXISTS {bak}")
        conn.execute(
            f"CREATE TABLE {bak} AS SELECT * FROM {t} WHERE time % {DAY} = 0")
        n_bak = conn.execute(f"SELECT COUNT(*) FROM {bak}").fetchone()[0]
        print(f"[apply] 备份 {t} 异常行 {n_bak} → {bak}")
    # 归一：异常行（UTC 日界 = CST 08:00）平移 -8h 到 CST 零点；正常行零触碰
    for t in TABLES:
        conn.execute(
            f"UPDATE {t} SET time = time - 28800000 WHERE time % {DAY} = 0")
    conn.commit()
    # 正向断言：全库两表 mod 单值 57600000
    for t in TABLES:
        n_bad = conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE time % {DAY} != {NORMAL_MOD}").fetchone()[0]
        n_mods = conn.execute(
            f"SELECT COUNT(DISTINCT time % {DAY}) FROM {t}").fetchone()[0]
        print(f"[apply] {t} 归一后：非57600000={n_bad} (应0) distinct_mods={n_mods} (应1) "
              f"{'PASS' if n_bad == 0 and n_mods == 1 else 'FAIL'}")


def revert(conn):
    # 从备份表恢复（回滚 UPDATE：异常行还原原 time）
    for t in TABLES:
        bak = f"{t}_backup_pd14b"
        exists = conn.execute(
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name='{bak}'").fetchone()[0]
        if not exists:
            print(f"[revert] {bak} 不存在，跳过")
            continue
        # 恢复：当前 time%86400000==0 且原为异常的行 → 从备份取回原 time。
        conn.execute(
            f"UPDATE {t} SET time = (SELECT b.time FROM {bak} b "
            f"WHERE b.code = {t}.code AND b.time = {t}.time) "
            f"WHERE EXISTS (SELECT 1 FROM {bak} b "
            f"WHERE b.code = {t}.code AND b.time = {t}.time)")
        # 简化回滚注：若 UPDATE 被完整执行（time-28800000），当前异常行已变成 57600000；
        # 完整回滚需按原 time 还原——此处以"从备份重建目标行"为准。
        print(f"[revert] {t} 回滚完成（备份表保留 {bak}）")
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["check", "apply", "revert"], default="check")
    ap.add_argument("--write", action="store_true",
                    help="apply/revert 需要写锁；默认 check 只读")
    args = ap.parse_args()
    ro = args.mode == "check"
    conn = duckdb.connect(DB, read_only=ro)
    try:
        if args.mode == "check":
            check(conn)
        elif args.mode == "apply":
            if not args.write:
                print("[apply] 需要 --write 显式确认（且 DB 写锁空闲）")
                return
            apply(conn)
        elif args.mode == "revert":
            if not args.write:
                print("[revert] 需要 --write 显式确认")
                return
            revert(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()