# -*- coding: utf-8 -*-
"""副本实验：WAL 语义三问（全部在临时目录，绝不触碰生产库）。

回答审核方必答项：
  Q1 崩溃/硬杀（无干净关闭）是否留下 WAL？
  Q2 read_only 打开在有 WAL 时是否回放？是否收敛 WAL（体积归零）？
  Q3 只有 read_write 打开（干净关闭）才收敛 WAL？

方法：同一临时库上依次
  ① 建表插数 → os._exit(0) 模拟硬杀 → 记录 WAL
  ② 新进程 read_only 打开 + SELECT + 干净关闭 → 记录 WAL
  ③ 新进程 read_write 打开 + SELECT + 干净关闭 → 记录 WAL
每步打印 WAL 体积与耗时。用法：python agent_workspace/wal_semantics_probe.py <step>
"""
import os
import sys
import time
from pathlib import Path

EXP_DIR = Path(os.environ.get("TEMP", ".")) / "wal_semantics_exp"
DB = EXP_DIR / "exp.db"


def wal_size() -> int:
    p = Path(str(DB) + ".wal")
    return p.stat().st_size if p.exists() else 0


def step1_create_and_hardkill() -> None:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", ".wal"):
        f = Path(str(DB) + suffix)
        if f.exists():
            f.unlink()
    import duckdb
    conn = duckdb.connect(str(DB))
    # 关闭两条自动检查点路径，确保硬杀后 WAL 真的留下（否则测不到 WAL 语义）
    for pragma in ("PRAGMA disable_checkpoint_on_shutdown",
                   "PRAGMA wal_autocheckpoint='1GB'"):
        try:
            conn.execute(pragma)
            print("[step1] %s ok" % pragma, flush=True)
        except Exception as e:
            print("[step1] %s FAILED: %s" % (pragma, e), flush=True)
    conn.execute("CREATE TABLE t AS SELECT range AS i, range*2 AS j FROM range(500000)")
    conn.execute("INSERT INTO t SELECT i+1000000, j FROM t")
    conn.execute("INSERT INTO t SELECT i+2000000, j FROM t")
    n = conn.execute("SELECT count(*) FROM t").fetchone()[0]
    print("[step1] rows=%d wal=%.2f MB (before hard kill)" % (n, wal_size() / 1e6), flush=True)
    os._exit(0)          # 硬杀：不 close、不做 shutdown checkpoint


def step2_readonly() -> None:
    import duckdb
    before = wal_size()
    t0 = time.time()
    conn = duckdb.connect(str(DB), read_only=True)
    n = conn.execute("SELECT count(*) FROM t").fetchone()[0]
    conn.close()
    after = wal_size()
    print("[step2 read_only] rows=%d  %.2fs  wal: %.2f MB -> %.2f MB  %s"
          % (n, time.time() - t0, before / 1e6, after / 1e6,
             "收敛(WAL 消失)" if after == 0 else "未收敛(WAL 仍在)"), flush=True)


def step3_readwrite() -> None:
    import duckdb
    before = wal_size()
    t0 = time.time()
    conn = duckdb.connect(str(DB))
    n = conn.execute("SELECT count(*) FROM t").fetchone()[0]
    conn.close()
    after = wal_size()
    print("[step3 read_write] rows=%d  %.2fs  wal: %.2f MB -> %.2f MB  %s"
          % (n, time.time() - t0, before / 1e6, after / 1e6,
             "收敛(WAL 消失)" if after == 0 else "未收敛(WAL 仍在)"), flush=True)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "step1"
    {"step1": step1_create_and_hardkill, "step2": step2_readonly, "step3": step3_readwrite}[which]()
