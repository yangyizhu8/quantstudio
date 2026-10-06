# -*- coding: utf-8 -*-
"""T1 批级断点（batch_checkpoint）验收脚本  ——  规格件 §三 T1 / §六 验收 / §八 回退。

运行（副本置于 <主仓>/verify/ 后，于<主仓>根执行）：
    python verify/verify_batch_checkpoint.py
或指定仓库根：
    python verify_batch_checkpoint.py --repo <主仓根>

覆盖三口径：
  A 等价性   —— gate 关闭零回归：不建断点表；既有批写入终态逐位不变（关 vs 开）。
  B 终态指纹 —— 终态指纹比对方法：中断续跑后的表指纹 == 一次跑完的表指纹；
                且续跑确实跳过已完成批（只重跑未完成批）。
  C 断点可红 —— P-10 同款：构造"越断点"场景，验证必须阻断（不得误跳过）：
                C1 写失败路径不落断点 -> 该窗口必须重跑；
                C2 断点未推进（commit=False）-> 该窗口不得被跳过；
                C3 断点 status 非 'completed' -> 不得被跳过。

退出码：0=全部 PASS；1=存在 FAIL。输出含可核验证据行（指纹、逐日处理序列）。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import traceback
from pathlib import Path

TABLE = "stock_daily"
COLS = ["code", "time", "close", "volume", "amount", "preClose", "pctChg"]
DAYS = ["20260901", "20260902", "20260903", "20260904", "20260905"]
CODES = ("600000", "600001")

_RESULTS = []


def _record(name, ok, detail=""):
    _RESULTS.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  |  {detail}" if detail else ""))


def _ms(day):
    import pandas as pd
    return int(pd.Timestamp(day).value // 10 ** 6)


def _df(day, codes=CODES):
    import pandas as pd
    rows = [[c, _ms(day), 10.0, 100.0, 1000.0, 9.5, 0.5] for c in codes]
    return pd.DataFrame(rows, columns=COLS)


def _fingerprint(db, table=TABLE):
    """终态指纹：对 (code,time) 排序后逐列逐位规范化 repr，SHA256。"""
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            f"SELECT {', '.join(COLS)} FROM {table} ORDER BY code, time").fetchall()

        def canon(v):
            if v is None:
                return ("NULL",)
            return (type(v).__name__, repr(v))
        h = hashlib.sha256()
        for r in rows:
            h.update(repr(tuple(canon(c) for c in r)).encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()
    finally:
        con.close()


def _table_exists(db, table):
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table]).fetchone()[0] > 0
    finally:
        con.close()


def _raw_set_status(db, window, status):
    import duckdb
    con = duckdb.connect(str(db), read_only=False)
    try:
        con.execute("UPDATE batch_checkpoint SET status=? WHERE window_key=?",
                    [status, window])
    finally:
        con.close()


class _Sim:
    """模拟 daemon 日批循环（用真实 ResidentCollector._bc_filter_completed/_bc_commit）。

    · to_process = filter(已完成批)          —— 续跑裁剪
    · 逐日 writer.write；**仅写成功路径**才 commit（写失败路径不落断点，fail-closed）
    · 返回逐日处理序列，便于断言"确实跳过/确实重跑"
    """

    def __init__(self, writer, daemon_cls):
        import types
        self.w = writer
        self.dc = daemon_cls
        self.stub = types.SimpleNamespace(writer=writer)

    def run(self, days, fail_days=(), commit_false_days=()):
        to_process = self.dc._bc_filter_completed(
            self.stub, "t", TABLE, "daily", list(days))
        processed, failed, committed = [], [], []
        for day in to_process:
            try:
                if day in fail_days:
                    raise RuntimeError("inject: write failure")
                wr = self.w.write(_df(day), TABLE, f"b_{day}")
            except Exception:
                failed.append(day)                 # 写失败：不 commit（不落断点）
                continue
            processed.append(day)
            if day in commit_false_days:           # 模拟 commit 未推进（回读失败等）
                continue
            ok = self.w.batch_checkpoint_commit(
                "t", TABLE, "daily", day, f"b_{day}", int(wr))
            if ok:
                committed.append(day)
        return {"to_process": to_process, "processed": processed,
                "failed": failed, "committed": committed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="QuantStudio 主仓根（含 quantstudio/ 包）")
    args = ap.parse_args()
    root = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    import os
    import tempfile

    import duckdb  # noqa: F401
    from quantstudio.pipeline.writers import (
        DuckDBWriter, _is_batch_checkpoint_enabled)
    from quantstudio.pipeline.daemon import ResidentCollector

    ENV = "QS_BATCH_CHECKPOINT"
    tmp = Path(tempfile.mkdtemp(prefix="t1verify_"))
    print(f"repo={root}\ntmp={tmp}\n")

    def mkwriter(name):
        db = tmp / f"{name}.duckdb"
        return db, DuckDBWriter({"type": "duckdb", "path": str(db)})

    def gate(on):
        if on:
            os.environ[ENV] = "1"
        else:
            os.environ.pop(ENV, None)

    try:
        # ── A 等价性（gate 关 vs 开，既有批逐位不变）──────────────────
        gate(False)
        db_off, w_off = mkwriter("off")
        s_off = _Sim(w_off, ResidentCollector)
        s_off.run(DAYS)
        fp_off = _fingerprint(db_off)
        no_table_off = not _table_exists(db_off, "batch_checkpoint")
        # gate 仍关闭时取样：load/commit 必须降级
        degraded_off = (w_off.batch_checkpoint_load("t", TABLE, "daily") == {}
                        and w_off.batch_checkpoint_commit(
                            "t", TABLE, "daily", "x", "b", 1) is False)

        gate(True)
        db_on, w_on = mkwriter("on")
        s_on = _Sim(w_on, ResidentCollector)
        s_on.run(DAYS)
        fp_on = _fingerprint(db_on)

        _record("A1 gate 关闭不创建 batch_checkpoint 表", no_table_off)
        _record("A2 等价性：既有批写入终态逐位不变（关==开）", fp_off == fp_on,
                f"fp_off={fp_off[:16]} fp_on={fp_on[:16]}")
        _record("A3 gate 关闭 load/commit 降级（无断点）", degraded_off)

        # ── B 终态指纹：中断续跑 == 一次跑完 ────────────────────────────
        gate(True)
        db_1, w_1 = mkwriter("oneshot")
        _Sim(w_1, ResidentCollector).run(DAYS)
        fp_oneshot = _fingerprint(db_1)

        db_r, w_r = mkwriter("resume")
        sim_r = _Sim(w_r, ResidentCollector)
        first = sim_r.run(DAYS[:2])                        # 模拟崩溃（只完成前 2 批）
        ck_after_first = sorted(w_r.batch_checkpoint_load("t", TABLE, "daily"))
        fp_partial = _fingerprint(db_r)
        second = sim_r.run(DAYS)                           # 重启续跑
        fp_resume = _fingerprint(db_r)

        _record("B1 中断续跑终态指纹 == 一次跑完指纹", fp_resume == fp_oneshot,
                f"resume={fp_resume[:16]} oneshot={fp_oneshot[:16]}")
        _record("B2 续跑确实跳过已完成批（只重跑未完成批）",
                second["to_process"] == DAYS[2:] and second["processed"] == DAYS[2:],
                f"to_process={second['to_process']}")
        _record("B3 中断后断点表保留已完成批（部分指纹 != 一次跑完）",
                ck_after_first == DAYS[:2] and fp_partial != fp_oneshot,
                f"ckpt={ck_after_first} partial={fp_partial[:16]}")
        w_r.batch_checkpoint_clear("t", TABLE, "daily")
        _record("B4 任务完成后 clear 断点（load 为空）",
                w_r.batch_checkpoint_load("t", TABLE, "daily") == {})

        # ── C 断点可红（P-10 同款：越断点必须阻断）─────────────────────
        # C1 写失败路径不落断点 -> 该窗口必须重跑
        db_c1, w_c1 = mkwriter("c1")
        sim1 = _Sim(w_c1, ResidentCollector)
        r1 = sim1.run(DAYS, fail_days={DAYS[1]})
        after_c1 = w_c1.batch_checkpoint_load("t", TABLE, "daily")
        resume1 = ResidentCollector._bc_filter_completed(
            sim1.stub, "t", TABLE, "daily", DAYS)
        _record("C1 写失败批无断点", DAYS[1] not in after_c1,
                f"ckpt={sorted(after_c1)}")
        _record("C1 断点可红：越断点批必须重跑（不得被跳过）", DAYS[1] in resume1,
                f"resume={resume1}")

        # C2 断点未推进（commit=False）-> 该窗口不得被跳过
        db_c2, w_c2 = mkwriter("c2")
        sim2 = _Sim(w_c2, ResidentCollector)
        r2 = sim2.run(DAYS, commit_false_days={DAYS[2]})
        ck2 = w_c2.batch_checkpoint_load("t", TABLE, "daily")
        resume2 = ResidentCollector._bc_filter_completed(
            sim2.stub, "t", TABLE, "daily", DAYS)
        _record("C2 未推进批无 completed 断点", ck2.get(DAYS[2]) is None,
                f"ckpt={sorted(ck2)}")
        _record("C2 断点可红：未推进批必须重跑", DAYS[2] in resume2,
                f"resume={resume2}")

        # C3 status 非 completed -> 不得被跳过
        db_c3, w_c3 = mkwriter("c3")
        sim3 = _Sim(w_c3, ResidentCollector)
        sim3.run(DAYS)                                    # 全部 completed
        _raw_set_status(db_c3, DAYS[3], "failed")         # 人为改为失败态
        resume3 = ResidentCollector._bc_filter_completed(
            sim3.stub, "t", TABLE, "daily", DAYS)
        _record("C3 断点可红：status!=completed 必须重跑", DAYS[3] in resume3,
                f"resume={resume3}")
        _record("C3 对照：completed 批仍被跳过", DAYS[0] not in resume3)

        # ── 收尾 ───────────────────────────────────────────────────────
        gate(False)
        failed = [n for n, ok in _RESULTS if not ok]
        print(f"\n共 {len(_RESULTS)} 项，PASS {len(_RESULTS) - len(failed)}，FAIL {len(failed)}")
        if failed:
            print("FAILED: " + ", ".join(failed))
            return 1
        print("T1 批级断点验收：ALL PASS")
        return 0

    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
