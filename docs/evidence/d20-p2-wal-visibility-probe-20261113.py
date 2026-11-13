# -*- coding: utf-8 -*-
"""D20 P-2 隔离探针：WAL 两表分 commit 的可见性窗口实测。

环境：隔离 QuestDB 实例 127.0.0.1:19000（数据根 D:\\questdb\\_d20probe）
      绝不触碰生产实例（9000 / D:\\questdb\\quest_database）。

依据（Context7 /websites/questdb）:
  - /docs/connect/java-embedded: "WAL writer commits aren't immediately visible to readers.
    The committed data becomes visible once it was applied by the ApplyWal2TableJob job."
  - /docs/cookbook/operations/check-transaction-applied: writerTxn=已 applied 可读最后一笔,
    sequencerTxn=已 commit 进 WAL 最后一笔; 相等 => 全可见
  - /docs/query/functions/meta: wait_wal_table(t, seqTxn?) 阻塞至 applied

输出：逐次记录 (t_commit_us, anchor可见行数, target可见行数, anchor的writerTxn/sequencerTxn,
      target的writerTxn/sequencerTxn) —— 用于判定"两表分 commit 是否出现读侧不一致窗口"。
"""
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:19000/exec"


def q(sql, timeout=60):
    u = BASE + "?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wal_state(name):
    """返回 (writerTxn, sequencerTxn)；非 WAL 表返回 None"""
    r = q("select name, writerTxn, sequencerTxn from wal_tables() where name = '%s'" % name)
    if not r.get("count"):
        return None
    row = r["dataset"][0]
    return (row[1], row[2])


def count(name):
    return q("select count() c from %s" % name)["dataset"][0][0]


def main():
    out = {"env": "isolated 127.0.0.1:19000", "steps": []}

    # --- 建表：模拟 sidecar 锚表 + 目标表，均 WAL（cairo.wal.enabled.default=true） ---
    q("drop table if exists anchor_wal")
    q("drop table if exists target_wal")
    q("create table anchor_wal (sym symbol, adj double, ts timestamp) timestamp(ts) partition by day wal")
    q("create table target_wal (sym symbol, px double, ts timestamp) timestamp(ts) partition by day wal")
    out["step0_created"] = [wal_state("anchor_wal"), wal_state("target_wal")]

    # --- 实验 A：逐轮"写锚表 -> 立刻读"，捕捉 commit/is-applied 的窗口 ---
    gaps = []
    n_mismatch = 0
    for i in range(20):
        ts = "2026-11-13T10:%02d:00.000000Z" % i
        q("insert into anchor_wal values ('A', %f, '%s')" % (1.0 + i * 0.01, ts))
        st = wal_state("anchor_wal")
        if st is None:
            continue
        w, s = st
        gaps.append(s - w)
        if w != s:
            n_mismatch += 1
    out["expA"] = {
        "rounds": len(gaps),
        "mismatch_rounds(writerTxn!=sequencerTxn)": n_mismatch,
        "max_gap": max(gaps) if gaps else None,
        "gap_hist": {str(g): gaps.count(g) for g in sorted(set(gaps))} if gaps else {},
    }

    # --- 实验 B：大事务（触发 WAL segment rollover，默认 rollover.row.count=200000）
    #     在锚表与目标表分别提交，随后立即同时读两表计数，看是否存在"一表已见、另一表未见" ---
    big_rows = 250000
    # 服务端生成，避免超长 URL（250k 行字面量约 10MB 会被连接重置）
    t0 = time.perf_counter()
    q(
        "insert into anchor_wal select 'B', 1.0, "
        "timestamp_sequence('2026-11-13T11:00:00', 1000) "
        "from long_sequence(%d)" % big_rows,
        timeout=300,
    )
    t_anchor_commit = time.perf_counter()
    q(
        "insert into target_wal select 'B', 2.0, "
        "timestamp_sequence('2026-11-13T11:00:00', 1000) "
        "from long_sequence(%d)" % big_rows,
        timeout=300,
    )
    t_target_commit = time.perf_counter()

    # 立即快照：提交刚返回，apply 可能还没追上
    snap = {}
    for name in ("anchor_wal", "target_wal"):
        st = wal_state(name)
        # 计数与 wal 状态同一时刻尽量贴近取
        c = count(name)
        snap[name] = {"writerTxn": st[0] if st else None, "sequencerTxn": st[1] if st else None,
                      "visible_rows": c}
    out["expB"] = {
        "rows_per_table": big_rows,
        "anchor_commit_sec": round(t_anchor_commit - t0, 3),
        "target_commit_sec": round(t_target_commit - t_anchor_commit, 3),
        "immediate_snapshot": snap,
        "inconsistency_observed": (
            (snap["anchor_wal"]["writerTxn"] != snap["anchor_wal"]["sequencerTxn"])
            or (snap["target_wal"]["writerTxn"] != snap["target_wal"]["sequencerTxn"])
        ),
    }

    # --- 实验 B2：提交后立即高频轮询，量化 apply 滞后（捕捉 writerTxn != sequencerTxn 的窗口） ---
    polls = []
    t_poll0 = time.perf_counter()
    for k in range(300):
        a = wal_state("anchor_wal")
        b = wal_state("target_wal")
        if a is None or b is None:
            break
        mismatch = (a[0] != a[1]) or (b[0] != b[1])
        polls.append({
            "i": k,
            "t_ms": round((time.perf_counter() - t_poll0) * 1000, 2),
            "a_w": a[0], "a_s": a[1], "b_w": b[0], "b_s": b[1],
            "mismatch": mismatch,
            # 两表之间是否出现"一表已应用、另一表未应用"的分叉
            "cross_table_divergence": (a[0] == a[1]) != (b[0] == b[1]),
        })
        if not mismatch and k > 0:
            break
    out["expB2_apply_lag"] = {
        "polls": len(polls),
        "mismatch_polls": sum(1 for p in polls if p["mismatch"]),
        "cross_table_divergence_polls": sum(1 for p in polls if p["cross_table_divergence"]),
        "first_polls": polls[:8],
        "last_poll": polls[-1] if polls else None,
    }

    # --- 实验 C：wait_wal_table 是否收敛（写后读的正确原语） ---
    t1 = time.perf_counter()
    w_anchor = q("select wait_wal_table('anchor_wal') r")
    w_target = q("select wait_wal_table('target_wal') r")
    t2 = time.perf_counter()
    st_after = {n: wal_state(n) for n in ("anchor_wal", "target_wal")}
    out["expC"] = {
        "wait_anchor": w_anchor["dataset"][0][0],
        "wait_target": w_target["dataset"][0][0],
        "wait_elapsed_sec": round(t2 - t1, 3),
        "wal_after": st_after,
        "both_converged": all(v and v[0] == v[1] for v in st_after.values()),
        "counts_after": {n: count(n) for n in ("anchor_wal", "target_wal")},
    }

    # --- 实验 D：commit.mode 实测（本实例默认 nosync；查运行期可见配置） ---
    try:
        r = q("select * from configuration() where key like '%commit%'")
        out["expD_commit_config"] = r.get("dataset")
    except Exception as e:  # noqa: BLE001
        out["expD_commit_config"] = "query failed: %s" % e

    with open(r"D:\questdb\_d20probe\p2_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
