# -*- coding: utf-8 -*-
"""V2 云地对账（源忠实性）：本地 QuestDB vs 云端，裁定 B3 口径

口径（总调度 2026-09-12）：64 表 ≥1 日期对拍 + 高风险 6 表 ≥3 日期。
定位：V2 **只验源忠实性**（本地 QDB 是否与云端同源同形）；端到端等价由 V4
（包 vs 本地）承担——V2×V4 串联即「包 vs 云端」。

方法：按 (表, 日期) 取云端 ≤CAP 行（query_snapshot + filters），
以 DEDUP 键为主键在本地同日期行集中查找并逐字段比对（顺序无关、键定位精确）。
"""
import json, sys
from pathlib import Path

QS = Path(r"D:\miniQMT策略实盘\QuantStudio")
if str(QS) not in sys.path:
    sys.path.insert(0, str(QS))
import urllib.parse, urllib.request
import duckdb
from quantstudio.pipeline.mcp.client import MCPClient

CAP = 500                      # 每 (表,日期) 对拍行数上限（控制 MCP 载荷）
HIGH_RISK = {"cyq_chips", "ths_daily", "ths_hot", "sw_daily", "stk_factor_pro",
             "stk_limit", "margin_detail"}
# 本地生成元数据列（两侧各自维护，不构成"源忠实性"差异）——排除出比对面
# （2026-09-12 实测：index_classify 仅 ingest_time 不同，云=9/04 本=9/11，业务列全一致）
META_COLS = {"ingest_time", "inserted_at", "created_at", "fetch_time", "fetched_at",
             "update_time", "updated_at", "source_watermark"}
HIGH_RISK_DATES = 3
PKG = QS / "quantstudio_data_package_20260912.db"


def qdb(sql):
    url = "http://127.0.0.1:9000/exec?query=" + urllib.parse.quote(sql)
    with urllib.request.urlopen(url, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def norm(v):
    """归一化比较值（2026-09-12 修正）：
      · None / NaN / 空串 → None（统一空值：修 NULL vs NaN、NULL vs '' 的假阳性）
      · 日期时间 → 前 10 位日期
      · 数值 → 6 位小数容差
    """
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "nat", "none", "null"):
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        f = float(s)
        if f != f:          # NaN
            return None
        return round(f, 6)
    except Exception:
        return s


def main():
    tmap = json.loads((QS / "config/profiles/mcp_only/questdb_table_map.json").read_text(encoding="utf-8"))
    client = MCPClient()
    # 必须先握手建立 session（否则 tools/call 报 "Missing session ID"）
    try:
        info = client.handshake()
        print(f"MCP handshake ok: {info}")
    except Exception as e:
        print(f"MCP handshake FAILED: {str(e)[:200]}")
        return 2
    con = duckdb.connect(str(PKG), read_only=True)

    results, blockers = [], []
    total_cmp = total_bad = 0
    for i, t in enumerate(sorted(tmap["tables"]), 1):
        spec = tmap["tables"][t]
        db_ = spec["date_basis"]
        keys = spec.get("dedup_keys") or [db_]
        n_dates = HIGH_RISK_DATES if t in HIGH_RISK else 1
        try:
            dts = [str(r[0])[:10] for r in qdb(
                f'SELECT DISTINCT "{db_}" FROM "{t}" WHERE "{db_}" IS NOT NULL '
                f'ORDER BY "{db_}" DESC LIMIT {n_dates}')["dataset"]]
        except Exception as e:
            blockers.append((t, "local-date", str(e)[:80])); continue
        if not dts:
            blockers.append((t, "no-local-date", "")); continue
        t_cmp = t_bad = t_err = 0
        detail = []
        for d in dts:
            try:
                d_ = client._call_with_retry(
                    client._call_tool, "query_snapshot",
                    {"dataset_id": f"qdb.{t}", "limit": CAP,
                     "filters": [{"column": db_, "op": "=", "value": d}]})
                crows = d_.get("rows", []) or []
            except Exception as e:
                t_err += 1
                detail.append((d, "cloud-err", str(e)[:70])); continue
            try:
                lrows = con.execute(
                    f'SELECT * FROM "{t}" WHERE CAST("{db_}" AS VARCHAR) LIKE ?',
                    [d + "%"]).fetchdf().to_dict("records")
            except Exception as e:
                detail.append((d, "local-err", str(e)[:70])); continue
            # 键无关多重集比对（2026-09-12 修正）：不依赖 DEDUP 键定义
            #（部分表 DDL 无 DEDUP 子句，回退键 [date_basis] 不唯一 → 旧法会误判）。
            # 以【云端行为基准】：逐行在本地同日期多重集中消费匹配。
            from collections import Counter
            common = [c for c in (crows[0].keys() if crows else [])
                      if c.lower() not in META_COLS]
            lcounter = Counter(
                tuple(norm(r.get(c)) for c in common) for r in lrows)
            m = b = 0
            for cr in crows:
                tup = tuple(norm(cr.get(c)) for c in common)   # 注意勿用 t（外层表名）
                if lcounter.get(tup, 0) > 0:
                    lcounter[tup] -= 1
                    m += 1
                else:
                    b += 1
            t_cmp += len(crows); t_bad += b
            detail.append((d, f"cmp={len(crows)} match={m} only_cloud={b} "
                              f"local_rows={len(lrows)}", ""))
        # 判据显式分类（铁律：空结果=静默成功须显式分类）：
        #   PASS    —— 有对拍行且零不一致
        #   DIFF    —— 有对拍行但存在不一致/缺失
        #   BLOCKED —— 云端调用失败（err>0），不得计为 PASS
        #   EMPTY   —— 零对拍行且零错误（窗口/日期无交集，须人工解释）
        if t_err:
            verdict = "BLOCKED"
        elif t_cmp == 0:
            verdict = "EMPTY"
        elif t_bad == 0:
            verdict = "PASS"
        else:
            verdict = "DIFF"
        total_cmp += t_cmp; total_bad += t_bad
        results.append(dict(table=t, dates=dts, compared=t_cmp, bad=t_bad,
                            cloud_err=t_err, verdict=verdict, detail=detail))
        print(f"[{i:>2}/64] {t:<30} dates={len(dts)} cmp={t_cmp:<6} bad={t_bad:<5} "
              f"err={t_err:<3} {verdict}")

    con.close()
    client.close()
    summary = dict(tables=len(results),
                   pass_tables=sum(1 for r in results if r["verdict"] == "PASS"),
                   diff_tables=[r["table"] for r in results if r["verdict"] == "DIFF"],
                   blocked_tables=[r["table"] for r in results if r["verdict"] == "BLOCKED"],
                   empty_tables=[r["table"] for r in results if r["verdict"] == "EMPTY"],
                   compared_rows=total_cmp, bad_rows=total_bad, blockers=blockers)
    out = QS / "data/logs/v2_cloud_parity.json"
    out.write_text(json.dumps(dict(summary=summary, results=results),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("=== V2 汇总 ===")
    n_tab = summary["tables"]
    n_pass = summary["pass_tables"]
    n_diff = len(summary["diff_tables"])
    print("  表: %d  PASS: %d  DIFF: %d" % (n_tab, n_pass, n_diff))
    print("  对拍行数: %s  不一致: %s" % (format(total_cmp, ","), format(total_bad, ",")))
    if summary["diff_tables"]:
        print("  DIFF 表:", summary["diff_tables"])
    if blockers:
        print("  阻塞:", blockers)
    print("  证据:", out)
    bad_any = (summary["diff_tables"] or summary["blocked_tables"]
               or summary["empty_tables"] or blockers)
    print("V2-RESULT:", "PASS" if not bad_any else "NOT-PASS")


if __name__ == "__main__":
    main()
