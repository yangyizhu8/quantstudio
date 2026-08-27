"""TD-D2 步骤1：gen1 因子灌数 = 方案 B 迁移脚本（临时脚本，不入库）。

任务书 v1.1 §3 步骤 1 方案 B + 用户 4 条实施要求：
  1. 一致性读取：legacy 只读连接（mode=ro）+ 单个读事务（BEGIN...COMMIT 快照隔离），
     避免与 daemon 正在进行的因子注入产生半读状态；
  2. 完整性验证：迁移后两表 COUNT/SUM(adj_factor)/MAX(time) 与 legacy 逐位一致
     （Phase 2 验收口径复用）；
  3. 边界：只读 legacy + 只写 gen1，不碰主库、不碰 qfq_aux_paths.json
     （released 保持 false）——与线1 恢复任务零冲突。
  4.（增量同步设计为独立函数 migrate_incremental()，供 ⑤ 切换前最后一次补差用
     ——TD-D2 步骤 4 复验清单事项）

规模：legacy adj_factor 18,956,607 行 + fund_adj 8,959,558 行（≈2791 万行）。
"""
import sqlite3
import sys
from pathlib import Path

LEGACY = Path("data/qfq_aux.db")
GEN1 = Path("data/qfq_aux_mcp_gen1.db")
PAGE = 500_000
TABLES = ("adj_factor", "fund_adj")


def migrate_full(legacy: Path = LEGACY, gen1: Path = GEN1) -> dict:
    # —— 一致性读取（要求1）：legacy mode=ro + 单读事务（快照隔离）——
    src = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True, timeout=60)
    src.execute("PRAGMA busy_timeout=60000")
    # gen1 写连接
    dst = sqlite3.connect(str(gen1), timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA busy_timeout=60000")
    result = {}
    try:
        src.execute("BEGIN")
        for tbl in TABLES:
            # gen1 表结构（与 mcp_adapter 注入口径一致：(code,time) PK）
            dst.execute(f"CREATE TABLE IF NOT EXISTS {tbl} ("
                        f"code TEXT, time INTEGER, adj_factor REAL, "
                        f"PRIMARY KEY (code, time))")
            total = 0
            last_rowid = 0
            dst.execute("BEGIN")
            while True:
                rows = src.execute(
                    f"SELECT rowid, code, time, adj_factor FROM {tbl} "
                    f"WHERE rowid > ? ORDER BY rowid LIMIT {PAGE}",
                    [last_rowid]).fetchall()
                if not rows:
                    break
                dst.executemany(
                    f"INSERT OR REPLACE INTO {tbl} (code, time, adj_factor) "
                    f"VALUES (?,?,?)",
                    [(r[1], r[2], r[3]) for r in rows])
                last_rowid = rows[-1][0]
                total += len(rows)
                if total % (PAGE * 4) == 0:
                    print(f"  [{tbl}] 已迁移 {total:,} 行 ...", flush=True)
            dst.commit()
            result[tbl] = {"migrated": total}
            print(f"  [{tbl}] 迁移完成: {total:,} 行")
        src.execute("COMMIT")
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()

    # —— 完整性验证（要求2）：COUNT/SUM/MAX 逐位一致 ——
    verify = {}
    dst_src = sqlite3.connect(f"file:{gen1}?mode=ro", uri=True, timeout=30)
    src2 = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True, timeout=30)
    ok_all = True
    for tbl in TABLES:
        v1 = src2.execute(f"SELECT COUNT(*), SUM(adj_factor), MAX(time) "
                          f"FROM {tbl}").fetchone()
        v2 = dst_src.execute(f"SELECT COUNT(*), SUM(adj_factor), MAX(time) "
                             f"FROM {tbl}").fetchone()
        same = v1 == v2
        ok_all = ok_all and same
        verify[tbl] = {"legacy": v1, "gen1": v2, "identical": same}
        print(f"  [{tbl}] COUNT/SUM/MAX: legacy={v1[0]:,}/{v1[1]:.6f}/{v1[2]} "
              f"gen1={v2[0]:,}/{v2[1]:.6f}/{v2[2]} → {'一致✅' if same else '❌不一致'}")
    dst_src.close(); src2.close()
    dst.close()
    result["verify"] = verify
    result["all_identical"] = ok_all
    return result


def migrate_incremental(legacy: Path = LEGACY, gen1: Path = GEN1,
                        since_rowid_by_table: dict | None = None) -> dict:
    """⑤ 切换前最后一次增量补差（TD-D2 步骤 4 复验清单事项）。

    设计：按 (time) 水线把 legacy 中新因子（迁移完成后 daemon 继续注入的）
    补到 gen1。参数 since_rowid_by_table 为上次全量迁移后的 rowid 水线
    （None → 用 gen1 现有 MAX(rowid 对应的 time) 推导为时间水线更稳，
    按 time > max_gen1_time 补差；rowid 在 INSERT OR REPLACE 下语义不稳，
    时间水线+同一时刻重复行 INSERT OR REPLACE 幂等覆盖）。
    """
    src = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True, timeout=60)
    src.execute("PRAGMA busy_timeout=60000")
    dst = sqlite3.connect(str(gen1), timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA busy_timeout=60000")
    out = {}
    try:
        src.execute("BEGIN")
        for tbl in TABLES:
            # 时间水线：gen1 现有最大 time（空库=0，全量等价）
            mt = dst.execute(f"SELECT MAX(time) FROM {tbl}").fetchone()[0] or 0
            if since_rowid_by_table and tbl in since_rowid_by_table:
                # 提供 rowid 水线时优先（更精确，但需调用方持有同一快照语义）
                rows = src.execute(
                    f"SELECT code, time, adj_factor FROM {tbl} "
                    f"WHERE rowid > ? ORDER BY rowid",
                    [since_rowid_by_table[tbl]]).fetchall()
            else:
                rows = src.execute(
                    f"SELECT code, time, adj_factor FROM {tbl} "
                    f"WHERE time >= ? ORDER BY time", [mt]).fetchall()
            dst.execute("BEGIN")
            dst.executemany(
                f"INSERT OR REPLACE INTO {tbl} (code, time, adj_factor) "
                f"VALUES (?,?,?)", [(r[0], r[1], r[2]) for r in rows])
            dst.commit()
            out[tbl] = {"incremental_rows": len(rows), "watermark_time": mt}
            print(f"  [{tbl}] 增量补差: {len(rows):,} 行（time>={mt} 幂等覆盖）")
        src.execute("COMMIT")
    finally:
        src.close(); dst.close()
    return out


if __name__ == "__main__":
    print("=" * 60)
    print("TD-D2 步骤1：legacy → gen1 因子全量迁移（方案 B）")
    print(f"  legacy: {LEGACY.resolve()}")
    print(f"  gen1:   {GEN1.resolve()}")
    print("=" * 60)
    r = migrate_full()
    print("=" * 60)
    if r["all_identical"]:
        print("✅ 迁移完成 + 完整性验证全部一致（COUNT/SUM/MAX 逐位一致）")
        print("待 ⑤ 释放前执行 migrate_incremental() 补最后一次增量（步骤4 复验清单）")
    else:
        print("❌ 完整性验证失败——禁止推进，排查差异")
        sys.exit(2)
