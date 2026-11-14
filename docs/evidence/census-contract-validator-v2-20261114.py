#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""census 批次一致性契约 v2 · 离线校验器

相对 v1 的三处**修正**（R32 指认，v1 保留不覆盖）：
  1) **证据绑定判定**：v1 的 classify_snapshot(version_bound=True) 允许调用方自带真理，
     等于被审方自证。v2 改为 decide_state(evidence=...)，由本校验器**逐项核验**
     P1/P2/P3 + 身份绑定 + 五项段完整性；缺证/伪证一律 provisional。
     调用方再传 version_bound=True 会被**显式忽略并记入 refuted_claims**。
  2) **空段/缺段/未取证三分**：v1 同时要求「allow_empty 允许空日期」与「每段 rows>0」，
     自相矛盾。v2 区分 legal_empty / missing / not_probed / zero_row_violation，
     rows>0 **只**作为「非声明空段」的判据，不再作唯一完整判据。
  3) **12h 覆盖边界**：v1 用「两次 21.5h 采样」推出「12h 覆盖每个交易日」。
     v2 分离 **coverage（是否有运行）** 与 **freshness（是否在阈值内）** 两个指标，
     并以夹具证明：阈值不能凭空产生运行；两点采样无法界定最大间隔。

铁律：零网络、零数据库、零生产写入。仅合成夹具，不读生产库。
"""

from __future__ import annotations

import sys

# --- 基线差集式网络哨兵（v1 教训：urllib/urllib.parse 由解释器启动即带入，
#     以其在 sys.modules 作绝对判据是假阳性）-----------------------------------
_BASELINE_MODULES = frozenset(sys.modules)

import json  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402


def _io_modules_touched() -> dict:
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
        "network_free": (not added) and (not absolute_io),
        "db_connection_attempted": False,
        "production_write": False,
    }


# ===========================================================================
# 第 1 部分 · P1/P2/P3 逐项核验（不再接受调用方布尔值）
# ===========================================================================
STATUS_VERIFIED = "verified"
STATUS_PARTIAL = "partial"
STATUS_UNVERIFIED = "unverified"
STATUS_REFUTED = "refuted"


def verify_p1_coverage(ev: dict | None) -> dict:
    """P1：全部相关写路径都发事务号。要求逐路径枚举 + 非WAL处置 + 证据来源。"""
    out = {"premise": "P1", "status": STATUS_UNVERIFIED, "reasons": [], "evidence_used": []}
    if not ev:
        out["reasons"].append("未提供 P1 证据对象")
        return out

    paths = ev.get("write_paths") or []
    if not paths:
        out["reasons"].append("write_paths 为空：未枚举任何写路径")
    else:
        bad = [p for p in paths if not p.get("id") or "wal" not in p or not p.get("source")]
        if bad:
            out["reasons"].append(f"{len(bad)} 条写路径缺 id/wal/source 字段")
        else:
            out["evidence_used"].append(
                f"write_paths 枚举 {len(paths)} 条，均带 wal 标记与来源"
            )
            non_wal = [p for p in paths if p.get("wal") is False]
            if non_wal:
                disp = (ev.get("non_wal_disposition") or {}).get("status")
                if disp != "excluded_with_reason":
                    out["reasons"].append(
                        f"{len(non_wal)} 条非WAL写路径未给出可接受处置"
                        f"（当前 disposition={disp!r}）"
                    )
                else:
                    out["evidence_used"].append(
                        "非WAL写路径已逐条排除并记理由"
                    )

    # 表级覆盖：本库 135 表中 19 张非 WAL，必须显式交代
    tbl = ev.get("table_level") or {}
    if "wal_table_count" not in tbl or "non_wal_table_count" not in tbl:
        out["reasons"].append("未给出表级 WAL/非WAL 计数")
    else:
        out["evidence_used"].append(
            f"表级：WAL={tbl['wal_table_count']} 非WAL={tbl['non_wal_table_count']}"
        )
        if tbl["non_wal_table_count"] > 0 and not tbl.get("non_wal_targets_confirmed"):
            out["reasons"].append(
                f"{tbl['non_wal_table_count']} 张非WAL表未确认是否含 census 目标表"
            )

    if not out["reasons"]:
        out["status"] = STATUS_VERIFIED
    elif out["evidence_used"]:
        out["status"] = STATUS_PARTIAL
    return out


def verify_p2_non_reuse(ev: dict | None) -> dict:
    """P2：事务号永不重用。要求文档明文 或（隔离实例实验 + 明确限制声明）。

    **关键限制**：进程重启实验**不能**证明断电/文件系统/硬件级不回滚；
    若证据仅覆盖进程重启，则 status 最高 partial，且必须记入 limitations。
    """
    out = {"premise": "P2", "status": STATUS_UNVERIFIED, "reasons": [],
           "limitations": [], "evidence_used": []}
    if not ev:
        out["reasons"].append("未提供 P2 证据对象")
        return out

    doc = ev.get("doc_citation")
    exp = ev.get("isolated_experiment")

    if doc and doc.get("ref"):
        out["evidence_used"].append(f"文档明文：{doc['ref']}")
        if doc.get("states_monotonic_non_reuse") is True:
            out["status"] = STATUS_VERIFIED
        else:
            out["reasons"].append("文档引用存在但未明确声称单调不复用")

    if exp:
        if exp.get("result") == "monotonic_non_reuse_observed":
            out["evidence_used"].append(
                f"隔离实例实验：{exp.get('id', '<no-id>')} 观测到单调不复用"
            )
            if out["status"] != STATUS_VERIFIED:
                out["status"] = STATUS_PARTIAL
        elif exp.get("result"):
            out["status"] = STATUS_REFUTED
            out["reasons"].append(f"隔离实验结果为 {exp['result']!r}，与不复用相悖")
        else:
            out["reasons"].append("隔离实验缺 result 字段")

        # 断电不覆盖：必须显式声明，否则不得据此宣称"不可回退"
        covers_powerloss = exp.get("covers_powerloss") is True
        if not covers_powerloss:
            out["limitations"].append(
                "进程重启/正常关闭实验**不覆盖**断电、文件系统缓存丢失、"
                "磁盘/RAID控制器写缓存、虚拟化层回滚 —— 不得据此宣称断电下不回滚"
            )
            if out["status"] == STATUS_PARTIAL:
                out["reasons"].append("实验仅覆盖进程边界，未覆盖断电边界（status 限 partial）")

    if not out["reasons"] and out["status"] == STATUS_UNVERIFIED:
        out["reasons"].append("既无文档明文也无可用实验证据")
    return out


def verify_p3_version_binding(ev: dict | None) -> dict:
    """P3：采样/查询/结果提交绑定同一版本。要求读接口具备版本钉定能力。"""
    out = {"premise": "P3", "status": STATUS_UNVERIFIED, "reasons": [], "evidence_used": []}
    if not ev:
        out["reasons"].append("未提供 P3 证据对象")
        return out

    cap = ev.get("read_pin_capability")
    if cap != "exists":
        out["status"] = STATUS_REFUTED if cap == "none" else STATUS_UNVERIFIED
        out["reasons"].append(
            f"读接口无版本钉定能力（read_pin_capability={cap!r}）"
            " ⇒ 采样/查询/落盘无法绑定同一版本"
        )
        return out

    out["evidence_used"].append(f"版本钉定接口：{ev.get('ref', '<no-ref>')}")
    if ev.get("sampling_query_commit_bound") is True and ev.get("ref"):
        out["status"] = STATUS_VERIFIED
    else:
        out["status"] = STATUS_PARTIAL
        out["reasons"].append("有钉定能力但未证明「采样/查询/提交」三者绑定同一版本")
    return out


def verify_identity_binding(ev: dict | None) -> dict:
    """身份绑定：pre/post 双采样 + schema/端点/脚本/输入指纹四要素齐备。"""
    out = {"premise": "identity", "status": STATUS_UNVERIFIED, "reasons": []}
    if not ev:
        out["reasons"].append("未提供身份绑定证据")
        return out
    need = ("schema_sha", "endpoint", "code_version", "input_fingerprint")
    missing = [k for k in need if not ev.get(k)]
    if missing:
        out["reasons"].append(f"身份四要素缺：{missing}")
        out["status"] = STATUS_PARTIAL if len(missing) < len(need) else STATUS_UNVERIFIED
        return out
    if ev.get("pre_post_sampled") is not True:
        out["reasons"].append("未做 pre/post 双采样（单点采样不构成身份绑定）")
        out["status"] = STATUS_PARTIAL
        return out
    out["status"] = STATUS_VERIFIED
    return out


# ===========================================================================
# 第 2 部分 · 段完整性五查（三分：legal_empty / missing / not_probed）
# ===========================================================================
def check_segments_v2(manifest: dict) -> dict:
    """五查 + 空段三分。rows>0 只约束「非声明空段」。

    返回 verdict ∈ {complete, failed, incomplete_unverifiable}
    """
    res = {"table": manifest.get("table"), "checks": {}, "segments_class": {},
           "verdict": None, "notes": []}

    segs = manifest.get("segments")
    rows_sum = manifest.get("rows_sum")
    expected = manifest.get("expected_days")
    allow_empty = set(manifest.get("allow_empty_days") or [])
    allow_reason = manifest.get("allow_empty_reason") or {}

    if segs is None:
        for c in ("C1", "C2", "C3", "C4", "C5"):
            res["checks"][c] = {"status": "unverifiable", "reason": "manifest 未落 segments[]"}
        res["verdict"] = "incomplete_unverifiable"
        res["notes"].append("DEF-001 形态：五查全不可判定，不得宣称完整")
        return res

    days = [s.get("day") for s in segs]
    rows_by_day = {}
    for s in segs:
        rows_by_day.setdefault(s.get("day"), []).append(s.get("rows") or 0)

    # C1 唯一键
    dup = sorted({d for d in days if days.count(d) > 1})
    res["checks"]["C1"] = {"status": "pass" if not dup else "fail", "duplicate_keys": dup}

    # C4 重复段
    res["checks"]["C4"] = {"status": "pass" if not dup else "fail",
                           "counts_gt1": {d: days.count(d) for d in dup}}

    # C2 / C3 —— 需要 expected_days
    if expected is None:
        res["checks"]["C2"] = {"status": "unverifiable",
                               "reason": "未落 expected_days（离线不自行连库生成）"}
        res["checks"]["C3"] = {"status": "unverifiable",
                               "reason": "缺 expected_days，漏段不可判"}
        res["segments_class"]["not_probed"] = "expected_days 缺失 ⇒ 整表未取证"
    else:
        exp, act = set(expected), set(days)
        missing_raw = sorted(exp - act)
        extra = sorted(act - exp)

        legal_empty, missing, not_probed = [], [], []
        for d in missing_raw:
            if d in allow_empty:
                if allow_reason.get(d):
                    legal_empty.append(d)      # 声明为空段且有理由 ⇒ 合法
                else:
                    not_probed.append(d)       # 声明为空段但无理由 ⇒ 未取证
            else:
                missing.append(d)              # 未声明且缺失 ⇒ 缺段

        # zero_row：非声明空段却 rows==0 ⇒ 违规；声明空段 rows==0 ⇒ 合法
        zero_violation = []
        for d, rs in rows_by_day.items():
            if any(r <= 0 for r in rs) and d not in allow_empty:
                zero_violation.append(d)

        # 声明空段却带正 rows ⇒ 声明与事实不符（记 note，不直接 fail）
        empty_but_nonzero = sorted(d for d in allow_empty
                                   if d in act and sum(rows_by_day.get(d, [0])) > 0)

        res["segments_class"] = {
            "legal_empty": sorted(legal_empty),
            "missing": sorted(missing),
            "not_probed": sorted(not_probed),
            "extra": sorted(extra),
            "zero_row_violation": sorted(zero_violation),
            "declared_empty_but_nonzero": empty_but_nonzero,
        }

        # C2 只判「集合不符」：missing/extra 是明确违规；
        # not_probed（声明空但无理由）属"未取证"，交 C3 判 unverifiable，
        # 不计入 C2 fail —— 未取证不等于违规（R32 要求三分）。
        c2_ok = not missing and not extra
        res["checks"]["C2"] = {
            "status": "pass" if c2_ok else "fail",
            "expected_count": len(exp), "actual_count": len(act),
            "missing_from_actual": sorted(missing),
            "not_probed_from_actual": sorted(not_probed),
            "extra_beyond_expected": extra,
            "note": "legal_empty 与 not_probed 均不计入 C2 缺失",
        }
        c3_ok = not missing and not zero_violation and not not_probed
        if missing or zero_violation:
            c3_status = "fail"
        elif not_probed:
            c3_status = "unverifiable"   # 声明空但无理由 ⇒ 未取证，非违规
        else:
            c3_status = "pass"
        res["checks"]["C3"] = {
            "status": c3_status,
            "missing_days": sorted(missing),
            "not_probed_days": sorted(not_probed),
            "zero_row_violation": sorted(zero_violation),
            "rule": "rows>0 只约束非声明空段；声明空段须有理由，无理由判未取证",
        }
        if not_probed:
            res["notes"].append(
                f"{len(not_probed)} 个日期声明为空但无理由 ⇒ 判未取证，不得计入完整"
            )

    # C5 求和
    calc = sum((s.get("rows") or 0) for s in segs)
    res["checks"]["C5"] = {
        "status": "pass" if rows_sum is not None and calc == rows_sum else "fail",
        "sum_of_segments": calc, "rows_sum": rows_sum,
    }

    st = {c: v["status"] for c, v in res["checks"].items()}
    if any(s == "fail" for s in st.values()):
        res["verdict"] = "failed"
    elif any(s == "unverifiable" for s in st.values()):
        res["verdict"] = "incomplete_unverifiable"
    else:
        res["verdict"] = "complete"

    if st.get("C5") == "pass" and res["verdict"] == "failed":
        res["notes"].append("★ C5 通过但总体不通过 —— 求和相等不证明分段正确")
    return res


# ===========================================================================
# 第 3 部分 · 状态判定（证据驱动，非布尔驱动）
# ===========================================================================
def decide_state(pre: dict, post: dict, *, evidence: dict | None = None,
                 segment_result: dict | None = None, age_hours: float = 0.0,
                 stale_after_hours: float = 12.0,
                 caller_version_bound_claim=None) -> dict:
    """判定快照强度。**不接受调用方布尔自证**。

    caller_version_bound_claim：为兼容 v1 调用而保留，但**一律忽略**并记 refuted_claims。
    """
    out = {
        "changed_fields": {},
        "premises": {},
        "snapshot_strength": "prepost_only",
        "state": None,
        "stale_reason": None,
        "blocking": [],
        "notes": [],
        "ignored_caller_claims": [],
    }

    if caller_version_bound_claim is not None:
        out["ignored_caller_claims"].append(
            "调用方传入 version_bound=%r —— 已忽略：版本绑定须由证据核验得出，"
            "不接受被审方自证（R32 指认 v1 缺陷）" % (caller_version_bound_claim,)
        )

    # 1) 指纹变化 —— 唯一能靠指纹判定的情形
    for f in ("rows", "ts_max_ms", "schema_sha"):
        if pre.get(f) != post.get(f):
            out["changed_fields"][f] = [pre.get(f), post.get(f)]
    if out["changed_fields"]:
        out["state"] = "stale"
        out["stale_reason"] = "fingerprint_changed_during_query"
        out["notes"].append("CE-3 形态：指纹可发现 ⇒ 重取。仍不可宣称一致快照")
        return out

    # 2) 逐项核验前提
    ev = evidence or {}
    out["premises"]["P1"] = verify_p1_coverage(ev.get("p1_coverage"))
    out["premises"]["P2"] = verify_p2_non_reuse(ev.get("p2_non_reuse"))
    out["premises"]["P3"] = verify_p3_version_binding(ev.get("p3_version_binding"))
    out["premises"]["identity"] = verify_identity_binding(ev.get("identity_binding"))

    for name, p in out["premises"].items():
        if p["status"] != STATUS_VERIFIED:
            out["blocking"].append(f"{name}:{p['status']}")

    # 3) 五项段完整性门
    if segment_result is None:
        out["blocking"].append("segments:not_provided")
        out["notes"].append("未提供段完整性结果，不得判 consistent")
    elif segment_result.get("verdict") != "complete":
        out["blocking"].append(f"segments:{segment_result.get('verdict')}")

    # 4) 超龄（仅调度，非验证）
    if age_hours > stale_after_hours:
        out["state"] = "stale"
        out["stale_reason"] = "age"
        out["notes"].append(
            "仅由超龄策略触发重取；超龄不证明任何时段内无变化，非安全门"
        )
        return out

    # 5) 定级
    if out["blocking"]:
        out["state"] = "provisional"
        out["snapshot_strength"] = "prepost_only"
        out["notes"].append(
            "缺证保持 provisional：阻塞项 " + ", ".join(out["blocking"])
        )
    else:
        out["state"] = "consistent"
        out["snapshot_strength"] = "version_bound"
        out["notes"].append(
            "四前提全 verified + 五查 complete ⇒ 可判 consistent（本夹具为机制验证）"
        )
    return out


# ===========================================================================
# 第 4 部分 · 12h 覆盖 vs 新鲜度（分离两指标）
# ===========================================================================
def coverage_and_freshness(run_times: list, trading_days: list,
                           stale_after_hours: float = 12.0) -> dict:
    """分离 coverage（该日是否有运行）与 freshness（收盘时点是否在阈值内）。

    口径：对每个交易日 D，取 as_of = D 23:59:59（收盘后检视）；
      last_run = 不晚于 as_of 的最近一次运行；
      covered  = last_run 落在 [D 00:00, D+1 00:00) 内；
      fresh    = (as_of - last_run) <= stale_after_hours。
    """
    runs = sorted(run_times)
    covered, uncovered, stale_at_close = [], [], []
    max_gap_h = 0.0

    # 运行间隔（跨任何日界，含周末/停机）—— 与"日内距上一次运行"是两个不同指标
    inter_run_gaps = [
        (runs[i + 1] - runs[i]).total_seconds() / 3600.0
        for i in range(len(runs) - 1)
    ]
    max_inter_run_gap_h = round(max(inter_run_gaps), 2) if inter_run_gaps else None

    for d in trading_days:
        as_of = d + timedelta(hours=23, minutes=59, seconds=59)
        prior = [r for r in runs if r <= as_of]
        if not prior:
            uncovered.append(d.isoformat())
            stale_at_close.append(d.isoformat())
            continue
        last = prior[-1]
        gap_h = (as_of - last).total_seconds() / 3600.0
        max_gap_h = max(max_gap_h, gap_h)
        if d <= last < d + timedelta(days=1):
            covered.append(d.isoformat())
            if gap_h > stale_after_hours:
                stale_at_close.append(d.isoformat())
        else:
            uncovered.append(d.isoformat())
            if gap_h > stale_after_hours:
                stale_at_close.append(d.isoformat())

    return {
        "trading_days": len(trading_days),
        "covered": len(covered),
        "uncovered": uncovered,
        "stale_at_close": stale_at_close,
        "max_gap_hours_within_day": round(max_gap_h, 2),
        "max_inter_run_gap_hours": max_inter_run_gap_h,
        "stale_after_hours": stale_after_hours,
        "coverage_ratio": round(len(covered) / len(trading_days), 4) if trading_days else None,
    }


def build_coverage_fixtures() -> list:
    base = datetime(2026, 11, 2)  # 周一
    # 交易日：两周工作日（跳过周末）
    days = []
    d = base
    while len(days) < 10:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    fx = []

    # F-CADENCE-21.5h：连续 21.5h 运行
    # 实测（非预期）：相位漂移导致 covered=9/10 —— 21.5h 间隔并不能保证每天有一次运行。
    # 这比"仅 freshness 破"更强：**coverage 本身就会丢天**。
    runs = [base + timedelta(hours=4.5 + 21.5 * i) for i in range(12)]
    r = coverage_and_freshness(runs, days, 12.0)
    fx.append({"fx": "F-CADENCE-21.5h", "result": r,
               "claim_tested": "12h 阈值能保证每个交易日均被覆盖吗",
               "expect": {"covered_lt": 10, "stale_at_close_nonempty": True},
               "why": "间隔 21.5h 相位漂移 ⇒ 已有交易日无运行（coverage 破），"
                      "且收盘时普遍超 12h（freshness 破）"})

    # F-WEEKEND-GAP：仅工作日 04:30 运行 —— 跨周末运行间隔 ~72h
    runs2 = [dd + timedelta(hours=4.5) for dd in days]
    r2 = coverage_and_freshness(runs2, days, 12.0)
    fx.append({"fx": "F-WEEKEND-GAP", "result": r2,
               "claim_tested": "跨周末的运行间隔与收盘新鲜度",
               "expect": {"covered": 10, "inter_run_gap_ge": 70, "stale_at_close_all": True},
               "why": "只在交易日 04:30 跑 ⇒ 周五->周一运行间隔约 72h；"
                      "且每日收盘距当日运行约 19.5h > 12h，全数 stale_at_close"})

    # F-NO-RUN：完全无运行 —— 阈值不能凭空制造运行
    r3 = coverage_and_freshness([], days, 12.0)
    fx.append({"fx": "F-NO-RUN", "result": r3,
               "claim_tested": "阈值能否替代运行",
               "expect": {"covered": 0},
               "why": "无运行 ⇒ coverage=0；把阈值改成 0.5h 也不会变 —— 阈值不产生运行"})

    # F-THRESHOLD-IRRELEVANT：同一 run 集，阈值 1h vs 48h，coverage 相同
    r4a = coverage_and_freshness([base + timedelta(hours=4.5)], days, 1.0)
    r4b = coverage_and_freshness([base + timedelta(hours=4.5)], days, 48.0)
    fx.append({"fx": "F-THRESHOLD-IRRELEVANT",
               "result": {"threshold_1h": r4a, "threshold_48h": r4b},
               "claim_tested": "阈值是否影响 coverage",
               "expect": {"same_covered": True},
               "why": "coverage 与阈值无关；阈值只影响 freshness 标注"})

    return fx, days


# ===========================================================================
# 第 5 部分 · 反例套件（沿用 v1 四例，改走证据驱动接口）
# ===========================================================================
def build_counterexamples() -> list:
    base = {"rows": 1000, "ts_max_ms": 1794382200000, "schema_sha": "abc123"}
    return [
        {"id": "CE-1", "name": "A→B→A 往返", "pre": dict(base), "post": dict(base),
         "expect_detectable": False, "age_hours": 1.0},
        {"id": "CE-2", "name": "同计数内容替换", "pre": dict(base), "post": dict(base),
         "content_swapped": True, "expect_detectable": False, "age_hours": 1.0},
        {"id": "CE-3", "name": "查询期间写入",
         "pre": dict(base), "post": {"rows": 2200, "ts_max_ms": 1794468600000,
                                     "schema_sha": "abc123"},
         "expect_detectable": True, "age_hours": 1.0},
        {"id": "CE-4", "name": "恢复后重用", "pre": dict(base), "post": dict(base),
         "content_swapped": True, "resumed": True, "expect_detectable": False,
         "age_hours": 1.0},
    ]


# ===========================================================================
# 第 6 部分 · 主流程
# ===========================================================================
def _complete_segment_fixture():
    return {"table": "stock_minutes", "segments": [
        {"day": "2026-11-11", "rows": 1000},
        {"day": "2026-11-12", "rows": 1000},
    ], "expected_days": ["2026-11-11", "2026-11-12"], "rows_sum": 2000}


def _full_evidence_fixture():
    """**合成**满证夹具 —— 仅用于证明判定机制非空转，不代表真实取证已达成。"""
    return {
        "_synthetic": True,
        "_disclaimer": "合成为验证判定机制可被满足而构造，非真实取证，不得当作生产已证",
        "p1_coverage": {
            "write_paths": [
                {"id": "w1", "wal": True, "source": "code:daemon.py:1792"},
                {"id": "w2", "wal": True, "source": "code:writers.py:3017"},
            ],
            "non_wal_disposition": {"status": "excluded_with_reason"},
            "table_level": {"wal_table_count": 116, "non_wal_table_count": 19,
                            "non_wal_targets_confirmed": True},
        },
        "p2_non_reuse": {
            "doc_citation": {"ref": "synthetic://doc", "states_monotonic_non_reuse": True},
            "isolated_experiment": {"id": "synthetic-exp", "result": "monotonic_non_reuse_observed",
                                    "covers_powerloss": False},
        },
        "p3_version_binding": {
            "read_pin_capability": "exists", "ref": "synthetic://pin-api",
            "sampling_query_commit_bound": True,
        },
        "identity_binding": {
            "schema_sha": "abc123", "endpoint": "synthetic://qdb",
            "code_version": "v1", "input_fingerprint": "f" * 16,
            "pre_post_sampled": True,
        },
    }


def main() -> int:
    report = {
        "validator": "census-batch-consistency-contract-v2",
        "validator_version": "2.0.0",
        "supersedes": "census-contract-validator-20261114.py (v1, 保留不覆盖)",
        "generated_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "offline_guarantees": _io_modules_touched(),
        "r32_fixes": [
            "证据绑定判定：不再接受调用方 version_bound 布尔自证",
            "空段/缺段/未取证三分：rows>0 不再作唯一完整判据",
            "coverage 与 freshness 分离：12h 不作覆盖保证",
        ],
        "state_tests": None,
        "segment_tests": None,
        "coverage_tests": None,
        "counterexamples": None,
        "assertions": {},
    }

    same = {"rows": 1000, "ts_max_ms": 1794382200000, "schema_sha": "abc123"}
    complete_seg = check_segments_v2(_complete_segment_fixture())

    # --- 状态判定测试 ---
    st = []

    # T1：调用方谎称 version_bound=True，无证据
    d1 = decide_state(same, dict(same), evidence=None, segment_result=complete_seg,
                      age_hours=1.0, caller_version_bound_claim=True)
    st.append({"id": "T1-caller-claims-true-no-evidence", "result": d1,
               "expect_state": "provisional",
               "why": "R32 核心：布尔自证必须被拒；无证据即 provisional"})

    # T2：给了证据对象但 P1/P2/P3 全空（伪证）
    d2 = decide_state(same, dict(same), evidence={"p1_coverage": {}, "p2_non_reuse": {},
                                                  "p3_version_binding": {},
                                                  "identity_binding": {}},
                      segment_result=complete_seg, age_hours=1.0)
    st.append({"id": "T2-forged-empty-evidence", "result": d2,
               "expect_state": "provisional", "why": "空证据对象 = 缺证"})

    # T3：P2 仅进程重启实验（未覆盖断电）→ 必须 partial，不得 consistent
    ev3 = _full_evidence_fixture()
    ev3["p2_non_reuse"] = {"isolated_experiment": {
        "id": "exp-restart-only", "result": "monotonic_non_reuse_observed",
        "covers_powerloss": False}}
    d3 = decide_state(same, dict(same), evidence=ev3, segment_result=complete_seg, age_hours=1.0)
    st.append({"id": "T3-p2-restart-only-no-powerloss", "result": d3,
               "expect_state": "provisional",
               "why": "进程重启不证明断电不回滚 ⇒ P2 限 partial ⇒ 不得 consistent"})

    # T4：P3 无版本钉定能力
    ev4 = _full_evidence_fixture()
    ev4["p3_version_binding"] = {"read_pin_capability": "none"}
    d4 = decide_state(same, dict(same), evidence=ev4, segment_result=complete_seg, age_hours=1.0)
    st.append({"id": "T4-p3-no-pin-capability", "result": d4,
               "expect_state": "provisional", "why": "读接口无钉定 ⇒ refuted"})

    # T5：四前提全满证但段五查不全
    incomplete_seg = check_segments_v2({
        "table": "stock_minutes",
        "segments": [{"day": "2026-11-11", "rows": 1000}],
        "expected_days": ["2026-11-11", "2026-11-12"], "rows_sum": 1000})
    d5 = decide_state(same, dict(same), evidence=_full_evidence_fixture(),
                      segment_result=incomplete_seg, age_hours=1.0)
    st.append({"id": "T5-premises-ok-segments-fail", "result": d5,
               "expect_state": "provisional",
               "why": "四前提齐备但五查未过 ⇒ 仍 provisional（段完整性是独立门）"})

    # T6：满证 + 五查 complete → consistent（证明机制非空转）
    d6 = decide_state(same, dict(same), evidence=_full_evidence_fixture(),
                      segment_result=complete_seg, age_hours=1.0)
    st.append({"id": "T6-full-evidence-and-complete", "result": d6,
               "expect_state": "consistent",
               "why": "机制可被满足（合成为证），非空转；但真实取证仍未达成"})

    # T7：超龄
    d7 = decide_state(same, dict(same), evidence=_full_evidence_fixture(),
                      segment_result=complete_seg, age_hours=24.0)
    st.append({"id": "T7-age-exceeded", "result": d7, "expect_state": "stale",
               "why": "超龄仅触发重取，不证明时段内无变化"})

    # T8：指纹变化
    d8 = decide_state(same, {"rows": 2200, "ts_max_ms": 1794468600000,
                             "schema_sha": "abc123"},
                      evidence=_full_evidence_fixture(), segment_result=complete_seg,
                      age_hours=1.0)
    st.append({"id": "T8-fingerprint-changed", "result": d8, "expect_state": "stale",
               "why": "CE-3 形态可发现"})

    report["state_tests"] = st

    # --- 段完整性测试（含三分）---
    seg_fx = [
        {"fx": "S-OK", "manifest": _complete_segment_fixture(), "expect": "complete"},
        {"fx": "S-LEGAL-EMPTY", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": ["2026-11-11", "2026-11-14"],
            "allow_empty_days": ["2026-11-14"],
            "allow_empty_reason": {"2026-11-14": "非交易日（周六），无分钟线"},
            "rows_sum": 1000}, "expect": "complete",
         "why": "声明空段且有理由 ⇒ 合法空段，不计缺失，也不要求 rows>0"},
        {"fx": "S-EMPTY-NO-REASON", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": ["2026-11-11", "2026-11-14"],
            "allow_empty_days": ["2026-11-14"],
            "allow_empty_reason": {},
            "rows_sum": 1000}, "expect": "incomplete_unverifiable",
         "why": "声明空段但无理由 ⇒ 未取证，不得当通过"},
        {"fx": "S-ZERO-ROW-VIOLATION", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-11", "rows": 0}],
            "expected_days": ["2026-11-11"], "rows_sum": 0}, "expect": "failed",
         "why": "非声明空段却 rows==0 ⇒ 违规（rows>0 判据在此生效）"},
        {"fx": "S-MISSING", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": ["2026-11-11", "2026-11-12"], "rows_sum": 1000},
         "expect": "failed", "why": "未声明且缺失 ⇒ 缺段"},
        {"fx": "S-SUMEQ-COMPOSITION", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-12", "rows": 1000},
                         {"day": "2026-11-12", "rows": 1000}],
            "expected_days": ["2026-11-11", "2026-11-12"], "rows_sum": 2000},
         "expect": "failed", "why": "漏D1+重复D2 ⇒ sum 相等但 C1/C2/C3/C4 全违规"},
        {"fx": "S-NO-EXPECTED", "manifest": {
            "table": "stock_minutes",
            "segments": [{"day": "2026-11-11", "rows": 1000}],
            "expected_days": None, "rows_sum": 1000},
         "expect": "incomplete_unverifiable", "why": "缺 expected_days ⇒ 未取证"},
        {"fx": "S-DEF-001", "manifest": {
            "table": "stock_minutes", "segment_count": 614, "rows_sum": 581305003},
         "expect": "incomplete_unverifiable", "why": "batch04 无 segments[]"},
    ]
    seg_out = []
    for f in seg_fx:
        r = check_segments_v2(dict(f["manifest"]))
        seg_out.append({"fx": f["fx"], "verdict": r["verdict"], "expect": f["expect"],
                        "met": r["verdict"] == f["expect"], "why": f.get("why"),
                        "checks": {k: v["status"] for k, v in r["checks"].items()},
                        "segments_class": r["segments_class"], "notes": r["notes"]})
    report["segment_tests"] = seg_out

    # --- 覆盖测试 ---
    cov_fx, days = build_coverage_fixtures()
    cov_out = []
    for f in cov_fx:
        r = f["result"]
        met = True
        exp = f["expect"]
        if "covered" in exp:
            met = met and (r["covered"] == exp["covered"])
        if "covered_lt" in exp:
            met = met and (r["covered"] < exp["covered_lt"])
        if exp.get("stale_at_close_nonempty"):
            met = met and bool(r["stale_at_close"])
        if exp.get("stale_at_close_all"):
            met = met and (len(r["stale_at_close"]) == r["trading_days"])
        if "inter_run_gap_ge" in exp:
            g = r.get("max_inter_run_gap_hours") or 0
            met = met and (g >= exp["inter_run_gap_ge"])
        if exp.get("same_covered"):
            met = met and (r["threshold_1h"]["covered"] == r["threshold_48h"]["covered"])
        cov_out.append({"fx": f["fx"], "met": met, "claim_tested": f["claim_tested"],
                        "why": f["why"], "result": r})
    report["coverage_tests"] = cov_out

    # --- 反例 ---
    ce_out = []
    for c in build_counterexamples():
        d = decide_state(c["pre"], c["post"], evidence=None, segment_result=complete_seg,
                         age_hours=c["age_hours"])
        det = bool(d["changed_fields"])
        ce_out.append({"id": c["id"], "name": c["name"], "detectable": det,
                       "expect_detectable": c["expect_detectable"],
                       "met": det == c["expect_detectable"],
                       "state": d["state"], "strength": d["snapshot_strength"]})
    report["counterexamples"] = ce_out

    # --- 汇总断言 ---
    a = {}
    a["A1_state_tests_all_met"] = all(x["result"]["state"] == x["expect_state"] for x in st)
    a["A2_caller_bool_ignored"] = bool(d1["ignored_caller_claims"]) and d1["state"] == "provisional"
    a["A3_restart_only_not_consistent"] = d3["state"] == "provisional"
    a["A4_mechanism_not_vacuous"] = d6["state"] == "consistent"
    a["A5_segment_tests_all_met"] = all(x["met"] for x in seg_out)
    a["A6_legal_empty_not_missing"] = any(
        x["fx"] == "S-LEGAL-EMPTY" and x["met"]
        and x["segments_class"].get("legal_empty") for x in seg_out)
    a["A7_empty_without_reason_undetermined"] = any(
        x["fx"] == "S-EMPTY-NO-REASON" and x["met"]
        and x["segments_class"].get("not_probed") for x in seg_out)
    a["A8_zero_row_only_for_non_declared"] = any(
        x["fx"] == "S-ZERO-ROW-VIOLATION" and x["met"] for x in seg_out)
    a["A9_coverage_tests_all_met"] = all(x["met"] for x in cov_out)
    a["A10_threshold_cannot_create_runs"] = any(
        x["fx"] == "F-NO-RUN" and x["met"] and x["result"]["covered"] == 0 for x in cov_out)
    a["A11_21h5_cadence_breaks_freshness"] = any(
        x["fx"] == "F-CADENCE-21.5h" and x["met"] and x["result"]["stale_at_close"]
        for x in cov_out)
    a["A12_counterexamples_all_met"] = all(x["met"] for x in ce_out)

    report["assertions"] = a
    ok = all(a.values()) and report["offline_guarantees"]["network_free"]
    report["ALL_ASSERTIONS_PASS"] = bool(ok)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
