#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""census 批次一致性契约 v1 · 离线校验器

铁律约束（写死，防误用）：
  * 零网络：不 import urllib/requests/socket
  * 零数据库：不连 QuestDB / DuckDB / SQLite
  * 只读本地夹具与本文件内联构造的合成数据
  * 不写生产目录；输出仅 stdout，由调用方重定向到 docs/evidence/

用途：
  1) 段完整性五查（C1 唯一键 / C2 预期集合 / C3 漏段 / C4 重复段 / C5 求和断言）
  2) 快照强度分级（consistent / provisional / stale），落实契约第 2 条 R2-1..R2-4
  3) 四类反例（CE-1..CE-4）的捕获能力自测 —— 证明 pre/post 相等不充分
  4) batch04 DEF-001（无 segments[]）的不可判定性

运行：python census-contract-validator-20261114.py
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# 安全哨兵：静态自证「零网络零数据库」
#
# 教训（2026-11-14 实测）：不能用「禁用模块是否在 sys.modules 中」作绝对判据——
# Python 3.11 解释器启动时 **已自带** urllib / urllib.parse 在 sys.modules 里
# （实测裸 python 即 True/True），据此会把自己的进程判成"联网"，是假阳性。
# 正确判据 = **基线差集**：本脚本 import 前后 sys.modules 的新增项中是否出现
# 真正做 I/O 的模块（socket / ssl / http.client / urllib.request / DB 驱动）。
# 另保留 socket / ssl 的绝对存在检查作为第二道（哪怕基线带来也算警告）。
# ---------------------------------------------------------------------------
_BASELINE_MODULES = frozenset(sys.modules)

import hashlib  # noqa: E402
import json  # noqa: E402
from datetime import datetime  # noqa: E402


def _io_modules_touched() -> dict:
    """返回新增 I/O 模块 + 绝对存在的 socket/ssl（双判据）。

    顶层根只取真正做 I/O 的；`urllib` 作为根**故意排除**（urllib.parse 纯字符串），
    urllib.request 只按**全名精确**匹配。
    """
    io_roots = {
        "socket", "ssl", "requests", "httpx", "aiohttp", "ftplib",
        "telnetlib", "smtplib", "psycopg2", "duckdb", "sqlite3",
        "pymysql", "sqlalchemy", "http", "urllib2",
    }
    io_exact = {"urllib.request", "http.client"}

    added = sorted(
        m for m in (frozenset(sys.modules) - _BASELINE_MODULES)
        if (m.split(".")[0] in io_roots) or (m in io_exact)
    )
    absolute_io = sorted(m for m in ("socket", "ssl") if m in sys.modules)
    return {
        "added_since_baseline": added,
        "absolutely_present_io": absolute_io,
        # 网络自由 = 无新增 I/O 模块 且 socket/ssl 绝对不在
        "network_free": (not added) and (not absolute_io),
        "db_connection_attempted": False,
        "production_write": False,
        "baseline_note": (
            "urllib/urllib.parse 由解释器启动带入（实测裸 python 即已存在），非本脚本引入；"
            "静态面唯一命中为禁用清单字符串字面量（自指），非调用"
        ),
    }

STALE_AFTER_HOURS_DEFAULT = 12.0  # 契约第 4 条：仅是重取调度策略，非安全门


# ---------------------------------------------------------------------------
# 第 5 条 · 段完整性五查
# ---------------------------------------------------------------------------
def check_segments(manifest: dict, stale_after_hours: float = STALE_AFTER_HOURS_DEFAULT) -> dict:
    """对单个表的 manifest 记录执行五查。返回逐查结论 + 总体判定。"""
    segs = manifest.get("segments")
    rows_sum = manifest.get("rows_sum")
    expected = manifest.get("expected_days")

    res = {
        "table": manifest.get("table"),
        "checks": {},
        "verdict": None,
        "notes": [],
    }

    # --- 无 segments[]：C1..C5 全部不可判定（DEF-001 形态） ---
    if segs is None or (isinstance(segs, list) and len(segs) == 0 and not manifest.get("segments_present")):
        for c in ("C1", "C2", "C3", "C4", "C5"):
            res["checks"][c] = {"status": "unverifiable",
                                "reason": "manifest 未落 segments[]（DEF-001）"}
        res["verdict"] = "incomplete_unverifiable"
        res["notes"].append("求和与分段均无法校验；不得宣称完整")
        return res

    days = [s.get("day") for s in segs]

    # --- C1 唯一键 ---
    dup_days = sorted({d for d in days if days.count(d) > 1})
    res["checks"]["C1"] = {
        "status": "pass" if not dup_days else "fail",
        "duplicate_keys": dup_days,
        "rule": "segments[].day 必须唯一",
    }

    # --- C4 重复段（与 C1 同源但独立成查，便于定位"重复计数抵消缺段"）---
    res["checks"]["C4"] = {
        "status": "pass" if not dup_days else "fail",
        "counts_gt1": {d: days.count(d) for d in dup_days},
        "rule": "任一 day 出现次数 <= 1",
    }

    # --- C2 预期集合 ---
    if expected is None:
        res["checks"]["C2"] = {
            "status": "unverifiable",
            "reason": "manifest 未落 expected_days（须由捕获时探针写入，离线不自行连库生成）",
        }
        res["checks"]["C3"] = {
            "status": "unverifiable",
            "reason": "缺 expected_days，无法判漏段",
        }
    else:
        exp, act = set(expected), set(days)
        missing = sorted(exp - act)
        extra = sorted(act - exp)
        res["checks"]["C2"] = {
            "status": "pass" if not missing and not extra else "fail",
            "expected_count": len(exp),
            "actual_count": len(act),
            "missing_from_actual": missing,
            "extra_beyond_expected": extra,
            "rule": "set(actual_days) == set(expected_days)",
        }
        # --- C3 漏段（含 rows>0 校验）---
        empty_rows = sorted({s.get("day") for s in segs if (s.get("rows") or 0) <= 0})
        res["checks"]["C3"] = {
            "status": "pass" if not missing and not empty_rows else "fail",
            "missing_days": missing,
            "zero_row_segments": empty_rows,
            "rule": "expected - actual == 空集，且每段 rows > 0",
        }

    # --- C5 求和断言 ---
    calc = sum((s.get("rows") or 0) for s in segs)
    res["checks"]["C5"] = {
        "status": "pass" if rows_sum is not None and calc == rows_sum else "fail",
        "sum_of_segments": calc,
        "rows_sum": rows_sum,
        "rule": "sum(segments[].rows) == rows_sum",
    }

    # --- 总体：C5 必要非充分 ---
    statuses = {c: v["status"] for c, v in res["checks"].items()}
    if any(s == "fail" for s in statuses.values()):
        res["verdict"] = "failed"
    elif any(s == "unverifiable" for s in statuses.values()):
        res["verdict"] = "incomplete_unverifiable"
    else:
        res["verdict"] = "complete"

    if statuses.get("C5") == "pass" and res["verdict"] == "failed":
        res["notes"].append(
            "★ C5 通过但总体不通过 —— 实证「求和相等不能证明分段正确」"
        )
    return res


# ---------------------------------------------------------------------------
# 第 2/3/4 条 · 快照强度分级
# ---------------------------------------------------------------------------
def classify_snapshot(pre: dict, post: dict, version_bound: bool,
                      age_hours: float,
                      stale_after_hours: float = STALE_AFTER_HOURS_DEFAULT) -> dict:
    """按契约第 2 条给状态；第 3 条限制 version_bound 上限；第 4 条只由 age 触发 stale。

    R2-1: 未取得版本绑定 -> 最高 provisional
    R2-3: 必须落 snapshot_strength
    """
    fields = ("rows", "ts_max_ms", "schema_sha")
    changed = {f: (pre.get(f), post.get(f)) for f in fields if pre.get(f) != post.get(f)}

    out = {"changed_fields": changed, "snapshot_strength": None,
           "state": None, "stale_reason": None, "notes": []}

    if changed:
        out["state"] = "stale"
        out["stale_reason"] = "fingerprint_changed_during_query"
        out["snapshot_strength"] = "prepost_only"
        out["notes"].append("CE-3 形态：指纹可发现，须重取")
        return out

    # 指纹完全相同 —— 但这不是"没变"的证明
    if age_hours > stale_after_hours:
        out["state"] = "stale"
        out["stale_reason"] = "age"
        out["snapshot_strength"] = "prepost_only"
        out["notes"].append(
            "仅由超龄策略触发重取；超龄策略不证明任何时段内无变化，非安全门"
        )
        return out

    if version_bound:
        out["state"] = "consistent"
        out["snapshot_strength"] = "version_bound"
        out["notes"].append("三前提齐备方可至此态（当前未证，见契约第 3 条）")
    else:
        out["state"] = "provisional"
        out["snapshot_strength"] = "prepost_only"
        out["notes"].append(
            "R2-1：无版本绑定 -> 上限 provisional；CE-1/CE-2 在此不可分辨"
        )
    return out


# ---------------------------------------------------------------------------
# 第 1 条 · 四类反例
# ---------------------------------------------------------------------------
def build_counterexamples() -> list:
    base = {"rows": 1000, "ts_max_ms": 1794382200000, "schema_sha": "abc123"}

    cases = []

    # CE-1 A→B→A：中途变成 B 又回到 A，pre/post 完全相等
    cases.append({
        "id": "CE-1",
        "name": "A→B→A 往返",
        "detail": "查询中途写入成 B（rows 1200），结束前回退/删除回到 A 的聚合特征",
        "pre": dict(base),
        "post": dict(base),
        "intermediate_state": {"rows": 1200, "ts_max_ms": 1794382200000, "schema_sha": "abc123"},
        "expect_detectable": False,
        "age_hours": 1.0,
    })

    # CE-2 同计数内容替换：行数/时间戳不变，值被等量替换
    cases.append({
        "id": "CE-2",
        "name": "同计数内容替换",
        "detail": "某批行被 UPDATE/替换（等量改值，如因子修正），rows 与 ts_max 均不变",
        "pre": dict(base),
        "post": dict(base),
        "content_swapped": True,
        "expect_detectable": False,
        "age_hours": 1.0,
    })

    # CE-3 查询期间写入：rows 增
    cases.append({
        "id": "CE-3",
        "name": "查询期间写入",
        "detail": "查询中途新增一个日段入库",
        "pre": dict(base),
        "post": {"rows": 2200, "ts_max_ms": 1794468600000, "schema_sha": "abc123"},
        "expect_detectable": True,
        "age_hours": 1.0,
    })

    # CE-4 恢复后重用：身份一致 + 内容已换 + 未超龄
    cases.append({
        "id": "CE-4",
        "name": "恢复后重用",
        "detail": "崩溃续跑，旧 done 键被当新结果重用；表已按 CE-2 形态变化但指纹未变",
        "pre": dict(base),
        "post": dict(base),
        "content_swapped": True,
        "resumed": True,
        "expect_detectable": False,
        "age_hours": 1.0,
    })

    return cases


def run_counterexample_suite() -> list:
    results = []
    for c in build_counterexamples():
        cls = classify_snapshot(c["pre"], c["post"], version_bound=False,
                               age_hours=c["age_hours"])
        detectable = bool(cls["changed_fields"])
        results.append({
            "id": c["id"],
            "name": c["name"],
            "detail": c["detail"],
            "detectable_by_prepost": detectable,
            "expect_detectable": c["expect_detectable"],
            "expectation_met": detectable == c["expect_detectable"],
            "assigned_state": cls["state"],
            "snapshot_strength": cls["snapshot_strength"],
            "stale_reason": cls["stale_reason"],
            "implication": (
                "指纹不可发现 -> 结果最高只能 provisional（R2-1）"
                if not detectable else
                "指纹可发现 -> 判 stale 并重取"
            ),
        })

    # 附加：CE-2 形态但超龄（证明 12h 只按 age 兜底，不是验证）
    cls_age = classify_snapshot(dict(rows=1000, ts_max_ms=1, schema_sha="s"),
                                dict(rows=1000, ts_max_ms=1, schema_sha="s"),
                                version_bound=False, age_hours=24.0)
    results.append({
        "id": "CE-2-age",
        "name": "同计数内容替换 × 超龄 24h",
        "detail": "内容已换但指纹不可见；仅因超过 12h 才被重取",
        "detectable_by_prepost": False,
        "expect_detectable": False,
        "expectation_met": True,
        "assigned_state": cls_age["state"],
        "snapshot_strength": cls_age["snapshot_strength"],
        "stale_reason": cls_age["stale_reason"],
        "implication": "12h 是重取调度策略，不证明 12h 内无变化，非 L2 安全门",
    })
    return results


# ---------------------------------------------------------------------------
# 段完整性夹具
# ---------------------------------------------------------------------------
def build_segment_fixtures() -> list:
    fx = []

    # FX-OK：三天、唯一、齐全、求和符
    fx.append({
        "fx": "FX-OK",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": True,
            "segments": [
                {"day": "2026-11-11", "rows": 1000},
                {"day": "2026-11-12", "rows": 1000},
                {"day": "2026-11-13", "rows": 1000},
            ],
            "expected_days": ["2026-11-11", "2026-11-12", "2026-11-13"],
            "rows_sum": 3000,
        },
        "expect_verdict": "complete",
    })

    # FX-SUMEQ-COMPOSITION ★核心反例：漏一段 + 重复一段，求和仍然相等
    fx.append({
        "fx": "FX-SUMEQ-COMPOSITION",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": True,
            "segments": [
                {"day": "2026-11-12", "rows": 1000},
                {"day": "2026-11-12", "rows": 1000},
            ],
            "expected_days": ["2026-11-11", "2026-11-12"],
            "rows_sum": 2000,
        },
        "expect_verdict": "failed",
        "why": "漏 D1(1000) + 重复 D2(1000) => sum 仍等于 rows_sum，C5 通过但 C2/C3/C4 全违规",
    })

    # FX-DUP：重复段
    fx.append({
        "fx": "FX-DUP",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": True,
            "segments": [
                {"day": "2026-11-11", "rows": 500},
                {"day": "2026-11-11", "rows": 500},
            ],
            "expected_days": ["2026-11-11"],
            "rows_sum": 1000,
        },
        "expect_verdict": "failed",
        "why": "同一 day 出现两次",
    })

    # FX-MISSING：漏段
    fx.append({
        "fx": "FX-MISSING",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": True,
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": ["2026-11-11", "2026-11-12"],
            "rows_sum": 1000,
        },
        "expect_verdict": "failed",
        "why": "expected 有 D2 但 actual 无",
    })

    # FX-NO-EXPECTED：缺 expected_days -> C2/C3 不可判定（不得当通过）
    fx.append({
        "fx": "FX-NO-EXPECTED",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": True,
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": None,
            "rows_sum": 1000,
        },
        "expect_verdict": "incomplete_unverifiable",
        "why": "expected_days 缺失 => C2/C3 unverifiable，不得当通过",
    })

    # FX-DEF-001：batch04 形态，无 segments[]
    fx.append({
        "fx": "FX-DEF-001",
        "manifest": {
            "table": "stock_minutes",
            "segments_present": False,
            "segment_count": 614,
            "rows_sum": 581305003,
        },
        "expect_verdict": "incomplete_unverifiable",
        "why": "batch04 未落 segments[]，五查全不可判定",
    })

    return fx


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    report = {
        "validator": "census-batch-consistency-contract-v1",
        "validator_version": "1.0.1",
        "generated_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "offline_guarantees": None,
        "stale_after_hours_default": STALE_AFTER_HOURS_DEFAULT,
        "stale_after_hours_is_safety_gate": False,
        "counterexamples": None,
        "segment_checks": None,
        "assertions": {},
    }
    report["offline_guarantees"] = _io_modules_touched()

    # 1) 反例套件
    ce = run_counterexample_suite()
    report["counterexamples"] = ce

    # 2) 段完整性夹具
    seg_results = []
    seg_ok = True
    for item in build_segment_fixtures():
        r = check_segments(dict(item["manifest"]))
        met = (r["verdict"] == item["expect_verdict"])
        seg_ok = seg_ok and met
        seg_results.append({
            "fx": item["fx"],
            "why": item.get("why"),
            "verdict": r["verdict"],
            "expect_verdict": item["expect_verdict"],
            "expectation_met": met,
            "checks": {c: v["status"] for c, v in r["checks"].items()},
            "notes": r["notes"],
        })
    report["segment_checks"] = seg_results

    # 3) 汇总断言
    ce_met = all(x["expectation_met"] for x in ce)
    report["assertions"] = {
        "A1_counterexamples_expectations_met": ce_met,
        "A2_segment_fixtures_expectations_met": seg_ok,
        "A3_prepost_insufficient_proven": (
            sum(1 for x in ce if not x["detectable_by_prepost"]) >= 2
        ),
        "A4_sum_equality_insufficient_proven": any(
            x["fx"] == "FX-SUMEQ-COMPOSITION"
            and x["checks"].get("C5") == "pass"
            and x["verdict"] == "failed"
            for x in seg_results
        ),
        "A5_no_consistent_without_version_binding": all(
            x["snapshot_strength"] != "version_bound" for x in ce
        ),
        "A6_age_policy_not_verification": any(
            x["id"] == "CE-2-age" and x["snapshot_strength"] == "prepost_only"
            for x in ce
        ),
    }

    all_pass = all(report["assertions"].values()) and report["offline_guarantees"]["network_free"]
    report["ALL_ASSERTIONS_PASS"] = bool(all_pass)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
