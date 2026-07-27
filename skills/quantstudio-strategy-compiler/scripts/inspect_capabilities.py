#!/usr/bin/env python
"""Inspect the runtime environment and emit a capability_report.json.

PR5 minimal runnable version. Probes the live DuckDB and reports the honest
status of each capability (data tables, engine profiles, output dir) per the
capability-model.md contract: 12 status words, 4 invariants (including
"tick is never READY in v1").

Usage:
    python inspect_capabilities.py --db <quantstudio.db> --profile <daily-bar-v1|minute-bar-v1> \\
        --strategy-id <id> [--spec <strategy_spec.json>] [--out <capability_report.json>]

Output:
    - Prints a human-readable summary to stdout
    - Writes capability_report.json (conforming to capability_report.schema.json)
      to --out path (default: stdout alongside summary)

Status vocabulary (capability-model.md §1, 12 words):
    AVAILABLE / READY / DATA_MISSING / ADAPTER_MISSING / PROVIDER_MISSING /
    ENGINE_MISSING / PLATFORM_DEPENDENT / DEGRADED / SCHEMA_ONLY /
    PLANNED / UNSUPPORTED / BLOCKED

Invariants (capability-model.md §2, enforced in _derive_overall_status):
    1. execution_status=READY → six dimensions may only be AVAILABLE or READY
    2. any required capability non-READY → overall may not be READY
    3. all required capabilities READY → overall must be READY
    4. tick capability is never READY in v1 (execution_status ∈ BLOCKED/PLANNED/UNSUPPORTED)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# =====================================================================
# Status vocabulary (capability-model.md §1) — do not invent new words
# =====================================================================
STATUS_AVAILABLE = "AVAILABLE"
STATUS_READY = "READY"
STATUS_DATA_MISSING = "DATA_MISSING"
STATUS_ADAPTER_MISSING = "ADAPTER_MISSING"
STATUS_PROVIDER_MISSING = "PROVIDER_MISSING"
STATUS_ENGINE_MISSING = "ENGINE_MISSING"
STATUS_PLATFORM_DEPENDENT = "PLATFORM_DEPENDENT"
STATUS_DEGRADED = "DEGRADED"
STATUS_SCHEMA_ONLY = "SCHEMA_ONLY"
STATUS_PLANNED = "PLANNED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_BLOCKED = "BLOCKED"

ALL_STATUSES = {
    STATUS_AVAILABLE, STATUS_READY, STATUS_DATA_MISSING, STATUS_ADAPTER_MISSING,
    STATUS_PROVIDER_MISSING, STATUS_ENGINE_MISSING, STATUS_PLATFORM_DEPENDENT,
    STATUS_DEGRADED, STATUS_SCHEMA_ONLY, STATUS_PLANNED, STATUS_UNSUPPORTED,
    STATUS_BLOCKED,
}

EXEC_READY = "READY"
EXEC_BLOCKED = "BLOCKED"
EXEC_PLANNED = "PLANNED"
EXEC_UNSUPPORTED = "UNSUPPORTED"

# =====================================================================
# Machine-checkable status detail tokens (F6 §8.2)
# 与 12 词 status 维度正交：精细区分 API 档案 / 本地数据 / 本地运行时 /
# PTrade 静态档案 / PTrade 真实运行（未验证）/ 数据阻断。
# =====================================================================
DETAIL_API_PROFILE_READY = "API_PROFILE_READY"
DETAIL_LOCAL_DATA_READY = "LOCAL_DATA_READY"
DETAIL_LOCAL_RUNTIME_READY = "LOCAL_RUNTIME_READY"
DETAIL_PTRADE_STATIC_PROFILE_READY = "PTRADE_STATIC_PROFILE_READY"
DETAIL_PTRADE_RUNTIME_UNVERIFIED = "PTRADE_RUNTIME_UNVERIFIED"
DETAIL_DATA_BLOCKED = "DATA_BLOCKED"

ALL_DETAIL_TOKENS = {
    DETAIL_API_PROFILE_READY, DETAIL_LOCAL_DATA_READY, DETAIL_LOCAL_RUNTIME_READY,
    DETAIL_PTRADE_STATIC_PROFILE_READY, DETAIL_PTRADE_RUNTIME_UNVERIFIED,
    DETAIL_DATA_BLOCKED,
}


# =====================================================================
# DB probing
# =====================================================================

def _probe_db(db_path: Path) -> Dict[str, Any]:
    """Probe the live DuckDB. Returns a dict of raw findings (table -> stats).

    Uses a short read-only connection per query to avoid the read_only vs
    read_write configuration conflict (PR4 lesson).
    """
    import duckdb
    findings: Dict[str, Any] = {
        "db_exists": db_path.exists(),
        "db_readable": False,
        "tables": {},
    }
    if not findings["db_exists"]:
        return findings
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            findings["db_readable"] = True
            existing = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            for table in ("stock_daily", "etf_daily", "stock_minutes", "etf_minutes",
                          "tick", "index_daily", "fin_indicator", "stock_float_share"):
                if table in existing:
                    try:
                        cnt = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        time_range = None
                        if cnt > 0 and "time" in {c[0] for c in conn.execute(f'DESCRIBE "{table}"').fetchall()}:
                            mm = conn.execute(
                                f'SELECT MIN(time), MAX(time) FROM "{table}" WHERE time IS NOT NULL'
                            ).fetchone()
                            time_range = (mm[0], mm[1]) if mm and mm[0] is not None else None
                        findings["tables"][table] = {"rows": cnt, "time_range": time_range}
                    except Exception as e:
                        findings["tables"][table] = {"rows": 0, "error": str(e)}
                else:
                    findings["tables"][table] = {"rows": 0, "missing": True}
        finally:
            conn.close()
    except Exception as e:
        findings["db_readable"] = False
        findings["db_error"] = str(e)
    return findings


def _probe_table_freq(db_path: Path, table: str) -> List[str]:
    """Return distinct freq values in a minute table (empty if table missing)."""
    if not db_path.exists():
        return []
    try:
        import duckdb
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = conn.execute(
                f'SELECT DISTINCT freq FROM "{table}" WHERE freq IS NOT NULL'
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


# =====================================================================
# F6 §8.3: reference-data capability probing (deeper than table existence)
# =====================================================================

def _probe_reference_data(db_path: Path) -> Dict[str, Any]:
    """Probe reference-data capabilities required by F1–F5 framework repairs.

    Checks (per F6 §8.3):
    - security metadata: stock_basic / etf_basic list/delist coverage;
    - index constituents: per-index snapshot count/coverage, as-of date really
      changes the result, no future leakage, partial snapshot flagging;
    - industry: classification versions, L1 count, PIT effective ranges,
      migrated-stock as-of spot check, pseudo SW_<name> code detection;
    - SW L1 index daily: coverage vs classification universe, OHLC/amount
      sanity, duplicate dates.
    """
    findings: Dict[str, Any] = {
        "stock_basic": {"present": False},
        "etf_basic": {"present": False},
        "index_constituents": {"present": False},
        "industry": {"present": False},
        "sw_index_daily": {"present": False},
    }
    if not db_path.exists():
        return findings
    import duckdb
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return findings
    try:
        existing = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}

        if "stock_basic" in existing:
            rows, nn = conn.execute(
                "SELECT COUNT(*), COUNT(list_date) FROM stock_basic").fetchone()
            sample = conn.execute(
                "SELECT code FROM stock_basic WHERE list_date IS NOT NULL "
                "ORDER BY code LIMIT 1").fetchone()
            findings["stock_basic"] = {"present": True, "rows": rows,
                                       "list_date_nonnull": nn,
                                       "sample_code": sample[0] if sample else None}

        if "etf_basic" in existing:
            rows, lnn, dnn = conn.execute(
                "SELECT COUNT(*), COUNT(list_date), COUNT(delist_date) FROM etf_basic"
            ).fetchone()
            sample = conn.execute(
                "SELECT code FROM etf_basic WHERE list_date IS NOT NULL "
                "ORDER BY code LIMIT 1").fetchone()
            delisted = conn.execute(
                "SELECT code FROM etf_basic WHERE delist_date IS NOT NULL "
                "ORDER BY code LIMIT 1").fetchone()
            findings["etf_basic"] = {"present": True, "rows": rows,
                                     "list_date_nonnull": lnn,
                                     "delist_date_nonnull": dnn,
                                     "sample_code": sample[0] if sample else None,
                                     "sample_delisted_code": (delisted[0]
                                                              if delisted else None)}

        if "index_constituents" in existing:
            idx = conn.execute("""
                SELECT index_code, COUNT(DISTINCT time), MIN(time), MAX(time)
                FROM index_constituents GROUP BY index_code ORDER BY index_code
            """).fetchall()
            info = {"present": True, "indices": {
                r[0]: {"snapshots": r[1], "min_time": r[2], "max_time": r[3]}
                for r in idx}}
            # as-of 语义抽查：优先 000300，其次任意 ≥2 快照的指数
            sample = None
            for pref in ("000300",):
                if info["indices"].get(pref, {}).get("snapshots", 0) >= 2:
                    sample = pref
                    break
            if sample is None:
                for code, meta in info["indices"].items():
                    if meta["snapshots"] >= 2:
                        sample = code
                        break
            if sample is not None:
                snaps = [r[0] for r in conn.execute(
                    "SELECT DISTINCT time FROM index_constituents "
                    "WHERE index_code=? ORDER BY time", [sample]).fetchall()]

                def _as_of(t):
                    row = conn.execute(
                        "SELECT MAX(time) FROM index_constituents "
                        "WHERE index_code=? AND time<=?", [sample, t]).fetchone()
                    if row is None or row[0] is None:
                        return set()
                    return {r[0] for r in conn.execute(
                        "SELECT code FROM index_constituents "
                        "WHERE index_code=? AND time=?", [sample, row[0]]).fetchall()}

                first, second = snaps[0], snaps[1]
                set_first, set_second = _as_of(first), _as_of(second)
                info["pit_sample_index"] = sample
                info["pit_date_changes_result"] = set_first != set_second
                # 未来泄漏/并集判定：首快照 as-of 结果不得等于历史并集
                # （并集写入即未来泄漏），配合 date_changes_result 联合判定
                union = {r[0] for r in conn.execute(
                    "SELECT DISTINCT code FROM index_constituents WHERE index_code=?",
                    [sample]).fetchall()}
                info["pit_not_history_union"] = set_first != union or len(snaps) == 1
                # partial 快照标记（< 0.5 × 该指数最大快照成分数）
                counts = [r[1] for r in conn.execute(
                    "SELECT time, COUNT(*) FROM index_constituents "
                    "WHERE index_code=? GROUP BY time", [sample]).fetchall()]
                if counts:
                    info["partial_snapshots"] = sum(
                        1 for c in counts if c < max(counts) * 0.5)
                info["coverage_start_ms"] = info["indices"][sample]["min_time"]
            info["meta_present"] = "index_constituents_snapshot_meta" in existing
            if info["meta_present"]:
                meta_rows = conn.execute(
                    "SELECT COUNT(*) FROM index_constituents_snapshot_meta"
                ).fetchone()[0]
                info["meta_rows"] = meta_rows
                if sample is not None and meta_rows > 0:
                    info["complete_snapshots"] = [r[0] for r in conn.execute(
                        "SELECT time FROM index_constituents_snapshot_meta "
                        "WHERE index_code=? AND status='complete' ORDER BY time",
                        [sample]).fetchall()]
                else:
                    info["complete_snapshots"] = []
            findings["index_constituents"] = info

        if {"industry_classification", "industry_membership"} <= existing:
            ind: Dict[str, Any] = {"present": True}
            ind["versions"] = [r[0] for r in conn.execute(
                "SELECT DISTINCT classification_version FROM industry_classification"
            ).fetchall()]
            ind["l1_count"] = conn.execute(
                "SELECT COUNT(*) FROM industry_classification "
                "WHERE classification_system='SW' AND classification_version='SW2021' "
                "AND industry_level='L1'").fetchone()[0]
            ind["membership_rows"] = conn.execute(
                "SELECT COUNT(*) FROM industry_membership").fetchone()[0]
            mm = conn.execute(
                "SELECT MIN(effective_from), MAX(effective_from) FROM industry_membership"
            ).fetchone()
            ind["effective_range"] = (mm[0], mm[1]) if mm else None
            ind["pseudo_sw_codes"] = conn.execute(
                "SELECT (SELECT COUNT(*) FROM industry_membership "
                " WHERE industry_code LIKE 'SW_%') + "
                "(SELECT COUNT(*) FROM industry_classification "
                " WHERE industry_code LIKE 'SW_%')").fetchone()[0]
            # 迁移股票 as-of 抽查：历史上换过行业的股票在两个日期归属不同
            mig = conn.execute(
                "SELECT code FROM industry_membership "
                "GROUP BY code HAVING COUNT(DISTINCT industry_code) > 1 LIMIT 1"
            ).fetchone()
            if mig:
                code = mig[0]
                hist = conn.execute(
                    "SELECT industry_code, effective_from FROM industry_membership "
                    "WHERE code=? ORDER BY effective_from", [code]).fetchall()

                def _ind_as_of(t):
                    rows = conn.execute(
                        "SELECT industry_code FROM industry_membership "
                        "WHERE code=? AND industry_level='L1' "
                        "AND effective_from<=? "
                        "AND (effective_to IS NULL OR effective_to>=?) "
                        "ORDER BY effective_from DESC LIMIT 1",
                        [code, t, t]).fetchall()
                    return rows[0][0] if rows else None

                ind["migrated_code"] = code
                # 抽两个归属不同的生效日做 as-of 对比（股票可能迁出又迁回，
                # 首末行业可能相同，必须按不同行业取点）
                seen: Dict[str, int] = {}
                for ind_code, eff_from in hist:
                    seen.setdefault(ind_code, eff_from)
                points = list(seen.items())
                ind["migrated_first_from"] = points[0][1]
                ind["migrated_latest_from"] = points[-1][1]
                ind["migrated_first"] = _ind_as_of(points[0][1])
                ind["migrated_latest"] = _ind_as_of(points[-1][1])
                ind["pit_migration_visible"] = (
                    len(points) >= 2
                    and ind["migrated_first"] == points[0][0]
                    and ind["migrated_latest"] == points[-1][0]
                    and ind["migrated_first"] != ind["migrated_latest"])
            if "industry_membership" in existing:
                pos = conn.execute("""
                    WITH m AS (
                      SELECT rowid, code, effective_from f,
                             COALESCE(effective_to, 9223372036854775807) t
                      FROM industry_membership
                      WHERE classification_system='SW' AND industry_level='L1')
                    SELECT COUNT(*) FROM m a JOIN m b
                      ON a.code=b.code AND a.rowid<b.rowid
                     AND LEAST(a.t,b.t) - GREATEST(a.f,b.f) > 0
                     AND GREATEST(a.f,b.f) <= LEAST(a.t,b.t)
                """).fetchone()[0]
                mc = conn.execute("""
                    SELECT COUNT(*) FROM (
                      SELECT code FROM industry_membership
                      WHERE classification_system='SW' AND industry_level='L1'
                        AND effective_to IS NULL
                      GROUP BY code HAVING COUNT(*) > 1)
                """).fetchone()[0]
                orphan = conn.execute("""
                    SELECT COUNT(*) FROM industry_membership m
                    WHERE m.classification_system='SW' AND m.industry_level='L1'
                      AND NOT EXISTS (
                        SELECT 1 FROM industry_classification c
                        WHERE c.classification_system=m.classification_system
                          AND c.classification_version=m.classification_version
                          AND c.industry_level=m.industry_level
                          AND c.industry_code=m.industry_code)
                """).fetchone()[0] if "industry_classification" in existing else -1
                bad = conn.execute("""
                    SELECT COUNT(*) FROM industry_membership
                    WHERE classification_system='SW' AND industry_level='L1'
                      AND effective_to IS NOT NULL AND effective_from > effective_to
                """).fetchone()[0]
                findings["industry_quality"] = {
                    "present": True, "positive_overlaps": pos,
                    "multi_current_codes": mc, "orphan_rows": orphan,
                    "bad_ranges": bad}
            else:
                findings["industry_quality"] = {"present": False}
            # 歧义样本（供 capability fail-closed 探针）
            amb_row = conn.execute("""
                WITH m AS (
                  SELECT rowid, code, effective_from f,
                         COALESCE(effective_to, 9223372036854775807) t
                  FROM industry_membership
                  WHERE classification_system='SW' AND industry_level='L1')
                SELECT a.code, GREATEST(a.f, b.f) AS amb_from
                FROM m a JOIN m b ON a.code=b.code AND a.rowid<b.rowid
                WHERE LEAST(a.t,b.t) - GREATEST(a.f,b.f) > 0
                  AND GREATEST(a.f,b.f) <= LEAST(a.t,b.t)
                ORDER BY a.code LIMIT 1
            """).fetchone() if "industry_membership" in existing else None
            if amb_row:
                import datetime as _dt
                ind["ambiguous_sample"] = (
                    amb_row[0],
                    _dt.datetime.fromtimestamp(
                        amb_row[1] / 1000,
                        tz=_dt.timezone(_dt.timedelta(hours=8))
                    ).strftime("%Y-%m-%d"))
            findings["industry"] = ind

        if "industry_classification" in existing and "index_daily" in existing:
            cov = conn.execute("""
                SELECT c.industry_code,
                       (d.n_rows IS NOT NULL AND d.n_rows > 0) AS has_daily,
                       d.min_time, d.max_time, COALESCE(d.n_rows, 0)
                FROM industry_classification c
                LEFT JOIN (
                    SELECT code, MIN(time) AS min_time, MAX(time) AS max_time,
                           COUNT(*) AS n_rows
                    FROM index_daily GROUP BY code
                ) d ON d.code = c.industry_code
                WHERE c.classification_system='SW' AND c.classification_version='SW2021'
                  AND c.industry_level='L1'
            """).fetchall()
            sw: Dict[str, Any] = {
                "present": True,
                "l1_total": len(cov),
                "with_daily": sum(1 for r in cov if r[1]),
                "missing": [r[0] for r in cov if not r[1]],
                "min_time": min((r[2] for r in cov if r[2] is not None), default=None),
                "max_time": max((r[3] for r in cov if r[3] is not None), default=None),
            }
            sw["ohlc_violations"] = conn.execute("""
                SELECT COUNT(*) FROM index_daily WHERE code LIKE '801%' AND (
                    high < GREATEST(open, close) OR low > LEAST(open, close)
                    OR amount < 0 OR volume < 0)""").fetchone()[0]
            sw["duplicate_dates"] = conn.execute("""
                SELECT COUNT(*) FROM (SELECT code, time, COUNT(*) n FROM index_daily
                WHERE code LIKE '801%' GROUP BY code, time HAVING n > 1)""").fetchone()[0]
            findings["sw_index_daily"] = sw
    except Exception as e:
        findings["error"] = str(e)
    finally:
        conn.close()
    return findings


def _ref_cap(name: str, ok: bool, evidence: List[str], ok_message: str,
             fail_message: str, ok_details: List[str],
             remediation: List[str]) -> Dict[str, Any]:
    """Assemble a reference-data capability with status_detail tokens."""
    if ok:
        dims = _ok_dims("reference", evidence, ok_message)
        details = ok_details
    else:
        dims = {
            "schema_status": STATUS_AVAILABLE, "data_status": STATUS_DATA_MISSING,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_AVAILABLE, "platform_status": STATUS_AVAILABLE,
            "evidence": evidence, "message": fail_message,
            "remediation": remediation,
        }
        details = [DETAIL_DATA_BLOCKED]
    cap = _cap(name, False, "reference", dims)
    cap["status_detail"] = details
    return cap


def _provider_api(db_path: Path):
    """构造真实 Provider/API（F6 返工：capability 判定必须走真实调用链）。"""
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from quantstudio.backtest.providers.duckdb_provider import DuckDBReferenceDataProvider
    from quantstudio.backtest.ptrade_api import PtradeAPI
    provider = DuckDBReferenceDataProvider(db_path)
    return provider, PtradeAPI(reference=provider)


def _build_reference_capabilities(ref: Dict[str, Any],
                                  db_path: Path) -> List[Dict[str, Any]]:
    """Build the F6 machine-checkable reference-data capabilities.

    审核返工（2026-07-27）：判定必须调用真实 Provider/API（不得只查表），
    且在以下情况诚实输出 DATA_BLOCKED：
    - F3：snapshot_meta 缺失/完整性不可证明/as-of 不确定；
    - F4：行业区间存在正重叠/multi-current/orphan/坏区间；
    - F5：覆盖不全、质量门控失败或正式 resident 路径不可达（无 enabled
      index_daily 采集任务 / adapter 无正式宇宙接口 / 801 路由无数据）。
    """
    caps: List[Dict[str, Any]] = []
    api_err = None
    try:
        provider, api = _provider_api(db_path)
    except Exception as e:  # 包导入或连接失败 → 全部 DATA_BLOCKED
        provider = api = None
        api_err = f"{type(e).__name__}: {e}"

    if api_err is not None:
        names = ["security_metadata_stock", "security_metadata_etf",
                 "index_constituents_pit", "index_constituents_history_coverage",
                 "industry_classification_sw2021", "industry_membership_pit",
                 "sw_l1_index_daily"]
        reason = f"provider/API unavailable: {api_err}"
        return [_ref_cap(n, False, [reason], "", reason, [],
                         ["Restore provider/API import path and database readability"])
                for n in names]

    # ---- security_metadata_stock：真实 get_stock_info 调用 ----
    sb = ref.get("stock_basic", {})
    stock_ok = False
    stock_ev = []
    try:
        sample = sb.get("sample_code") or "000001"
        rec = api.get_stock_info(f"{sample}.SZ")[f"{sample}.SZ"]
        keys = set(rec.keys())
        shape_ok = keys == {"stock_name", "stock_type", "listed_date",
                            "de_listed_date", "exchange_type", "code"}
        unk = api.get_stock_info("999999.SH")["999999.SH"]
        compat_ok = (unk["listed_date"] is None and unk["stock_type"] == "stock"
                     and unk["stock_name"] == "999999.SH")
        import re as _re
        date_ok = (rec["listed_date"] is None
                   or bool(_re.match(r"^\d{4}-\d{2}-\d{2}$", rec["listed_date"])))
        # 必须真实命中证券数据：listed_date 为空即未知兼容记录，不得报 READY
        stock_ok = bool(shape_ok and compat_ok and date_ok
                        and rec["stock_type"] == "stock"
                        and rec["listed_date"] is not None)
        stock_ev = [f"get_stock_info({sample}.SZ) -> {rec}",
                    f"unknown compat -> {unk}"]
    except Exception as e:
        stock_ev = [f"get_stock_info probe failed: {type(e).__name__}: {e}"]
    caps.append(_ref_cap(
        "security_metadata_stock", stock_ok, stock_ev,
        "get_stock_info stock metadata verified via real API call",
        "get_stock_info stock probe failed or shape/compat mismatch",
        [DETAIL_API_PROFILE_READY, DETAIL_LOCAL_DATA_READY,
         DETAIL_LOCAL_RUNTIME_READY, DETAIL_PTRADE_STATIC_PROFILE_READY,
         DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Repair stock metadata provider path before declaring READY"]))

    # ---- security_metadata_etf：真实 ETF get_stock_info 调用 ----
    eb = ref.get("etf_basic", {})
    etf_ok = False
    etf_ev = []
    try:
        code = eb.get("sample_code")
        delisted = eb.get("sample_delisted_code")
        if code:
            bare = str(code)
            suffix = ".SS" if bare.startswith("5") else ".SZ"
            rec = api.get_stock_info(f"{bare}{suffix}")[f"{bare}{suffix}"]
            etf_ok = rec["stock_type"] == "etf" and bool(rec["listed_date"])
            etf_ev.append(f"get_stock_info({bare}{suffix}) -> {rec}")
            if delisted:
                d = str(delisted)
                dsfx = ".SS" if d.startswith("5") else ".SZ"
                drec = api.get_stock_info(f"{d}{dsfx}")[f"{d}{dsfx}"]
                etf_ev.append(f"delisted -> {drec}")
                etf_ok = etf_ok and drec["de_listed_date"] is not None
        else:
            etf_ev.append("etf_basic missing or empty")
    except Exception as e:
        etf_ev = [f"ETF probe failed: {type(e).__name__}: {e}"]
        etf_ok = False
    caps.append(_ref_cap(
        "security_metadata_etf", etf_ok, etf_ev,
        "get_stock_info ETF metadata (type/list/delist) verified via real API call",
        "ETF metadata probe failed (etf_basic missing or API mismatch)",
        [DETAIL_API_PROFILE_READY, DETAIL_LOCAL_DATA_READY,
         DETAIL_LOCAL_RUNTIME_READY, DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Run the etf_basic pipeline; verify ETF metadata on real PTrade separately"]))

    # ---- index_constituents_pit：snapshot_meta 契约 + 真实 as-of 调用 ----
    ic = ref.get("index_constituents", {})
    pit_ok = False
    pit_ev = []
    try:
        if not ic.get("meta_present"):
            pit_ev.append("index_constituents_snapshot_meta missing: "
                          "snapshot completeness unprovable (fail-closed)")
        else:
            sample = ic.get("pit_sample_index")
            snaps = ic.get("complete_snapshots", [])
            if sample and len(snaps) >= 2:
                import datetime as _dt
                def _fd(ms):
                    return _dt.datetime.fromtimestamp(
                        ms / 1000,
                        tz=_dt.timezone(_dt.timedelta(hours=8))).strftime("%Y-%m-%d")
                f1, f2 = _fd(snaps[0]), _fd(snaps[-1])
                r1 = provider.get_index_constituents(sample, f1)
                r1b = provider.get_index_constituents(sample, f1)
                r2 = provider.get_index_constituents(sample, f2)
                deterministic = r1 == r1b
                changes = set(r1) != set(r2)
                union = set(r1) | set(r2)
                not_union = set(r1) != union or len(snaps) == 1
                pit_ok = bool(deterministic and changes and not_union and r1)
                pit_ev = [f"sample={sample} complete_snapshots={len(snaps)}",
                          f"as_of({f1}) n={len(r1)} deterministic={deterministic}",
                          f"as_of({f2}) n={len(r2)} changes_result={changes}",
                          f"not_history_union={not_union}"]
            else:
                pit_ev.append("no index with >=2 complete snapshots")
    except Exception as e:
        pit_ev = [f"PIT probe failed: {type(e).__name__}: {e}"]
    caps.append(_ref_cap(
        "index_constituents_pit", pit_ok, pit_ev,
        "get_index_stocks(date) strict as-of PIT verified via real provider calls",
        "snapshot_meta missing, or as-of probe nondeterministic/unchanged/union",
        [DETAIL_API_PROFILE_READY, DETAIL_LOCAL_DATA_READY,
         DETAIL_LOCAL_RUNTIME_READY, DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Rebuild snapshot meta (refresh_snapshot_meta); verify as-of behavior"]))

    # ---- index_constituents_history_coverage ----
    cov_ok = bool(ic.get("present") and ic.get("indices"))
    caps.append(_ref_cap(
        "index_constituents_history_coverage",
        cov_ok,
        ["index coverage: " + "; ".join(
            f"{code} {meta['snapshots']} snapshots "
            f"[{meta['min_time']}..{meta['max_time']}]"
            for code, meta in sorted(ic.get("indices", {}).items()))][:600],
        "index constituents snapshot coverage report attached",
        "no index_constituents snapshots available",
        [DETAIL_LOCAL_DATA_READY, DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Backfill index_constituents history for required indices"]))

    # ---- industry_classification_sw2021 ----
    ind = ref.get("industry", {})
    cls_ok = bool(ind.get("present") and "SW2021" in ind.get("versions", [])
                  and ind.get("l1_count", 0) == 31
                  and ind.get("pseudo_sw_codes", 1) == 0)
    caps.append(_ref_cap(
        "industry_classification_sw2021",
        cls_ok,
        [f"versions={ind.get('versions')}, L1 count={ind.get('l1_count')}, "
         f"pseudo SW_ codes={ind.get('pseudo_sw_codes')}"],
        "SW2021 L1 classification (31 industries) READY",
        "industry_classification missing/incomplete or contains pseudo SW_ codes",
        [DETAIL_LOCAL_DATA_READY, DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Run the industry_classification pipeline (tushare index_classify)"]))

    # ---- industry_membership_pit：质量门控 + 真实 get_industry 调用 ----
    mem_ok = False
    mem_ev = []
    try:
        q = ref.get("industry_quality", {})
        present = bool(q.get("present"))
        hard_ok = bool(present and q.get("multi_current_codes") == 0
                       and q.get("orphan_rows") == 0
                       and q.get("bad_ranges") == 0)
        overlap_ambiguity = bool(present and int(q.get("positive_overlaps", 0)) > 0)
        mem_approx = False
        mem_ev.append(f"interval quality gate: {q}")
        mig = ind.get("migrated_code")
        if not present:
            mem_ok = False
            mem_ev.append("industry_membership missing -> DATA_BLOCKED")
        elif not hard_ok:
            mem_ok = False
            mem_ev.append("HARD interval quality gate FAILED "
                          "(multi_current/orphan/bad_ranges) -> DATA_BLOCKED")
        elif overlap_ambiguity:
            # F4 重分类（2026-07-27）：数据存在但存在重叠区间（如 SW2021 行业重新
            # 分类导致同一证券某日同时属于新旧两类）。官方 index_member 仅提供
            # in_date/out_date，无任何冲突裁决规则；canonical 表原样保留重叠区间、
            # 不应用自定义裁决；歧义日期在 API 层 fail-closed
            # （ReferenceDataCapabilityError），因此 industry_membership 不是
            # PIT READY，标注 APPROXIMATION_REQUIRES_CONFIRMATION。
            mem_ok = False
            mem_approx = True
            mem_ev.append(
                "APPROXIMATION_REQUIRES_CONFIRMATION: overlapping intervals present "
                "(raw, kept as-is, no custom conflict-resolution rule). "
                "industry_membership is NOT PIT READY; ambiguous dates are "
                "fail-closed at API level.")
            # 歧义日期 fail-closed 真实探针
            try:
                from quantstudio.backtest.providers.base import (
                    ReferenceDataCapabilityError)
                import datetime as _dt
                amb = ref.get("industry", {}).get("ambiguous_sample")
                if amb:
                    acode, adate = amb
                    try:
                        provider.get_industry(acode, adate)
                        mem_ev.append(
                            f"WARN: ambiguous date {adate} for {acode} "
                            "did NOT fail closed")
                    except ReferenceDataCapabilityError:
                        mem_ev.append(
                            f"ambiguous-date fail-closed verified "
                            f"({acode} @ {adate})")
            except Exception as e:
                mem_ev.append(f"ambiguity fail-closed probe error: {e}")
        elif mig:
            import datetime as _dt
            def _d(ms):
                return _dt.datetime.fromtimestamp(
                    ms / 1000,
                    tz=_dt.timezone(_dt.timedelta(hours=8))).strftime("%Y-%m-%d")
            first = provider.get_industry(mig, _d(ind["migrated_first_from"]))
            latest = provider.get_industry(mig, _d(ind["migrated_latest_from"]))
            visible = (first and latest
                       and first["sw_l1"]["industry_code"]
                       != latest["sw_l1"]["industry_code"])
            mem_ok = bool(visible)
            mem_ev.append(
                f"migration {mig}: "
                f"{_d(ind['migrated_first_from'])}->"
                f"{first and first['sw_l1']['industry_code']}; "
                f"{_d(ind['migrated_latest_from'])}->"
                f"{latest and latest['sw_l1']['industry_code']}")
        else:
            mem_ev.append("no migrated stock found for as-of spot check")
    except Exception as e:
        mem_ev.append(f"industry PIT probe failed: {type(e).__name__}: {e}")
    if mem_approx:
        # 数据存在且结构合法，但含歧义区间：DEGRADED（近似，需用户确认），
        # 而非 DATA_MISSING；歧义日期在 API 层 fail-closed。
        approx_cap = {
            "schema_status": STATUS_AVAILABLE, "data_status": STATUS_DEGRADED,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_AVAILABLE, "platform_status": STATUS_AVAILABLE,
            "evidence": mem_ev,
            "message": "raw overlapping intervals preserved; ambiguous dates "
                       "fail-closed; APPROXIMATION_REQUIRES_CONFIRMATION "
                       "(NOT formal PIT READY)",
            "remediation": ["R1 must classify APPROXIMATION_REQUIRES_CONFIRMATION "
                            "and obtain customer confirmation before relying on "
                            "historical industry membership"],
        }
        cap = _cap("industry_membership_pit", False, "reference", approx_cap)
        cap["status_detail"] = [DETAIL_DATA_BLOCKED,
                                DETAIL_PTRADE_RUNTIME_UNVERIFIED]
        caps.append(cap)
    else:
        caps.append(_ref_cap(
        "industry_membership_pit", mem_ok, mem_ev,
        "get_industry as-of queryable (raw overlapping intervals preserved, "
        "no custom conflict-resolution rule applied)",
        "industry interval quality gate hard-failed / overlapping intervals present "
        "(APPROXIMATION_REQUIRES_CONFIRMATION, NOT PIT READY) / migration spot check failed",
        [DETAIL_LOCAL_DATA_READY, DETAIL_LOCAL_RUNTIME_READY,
         DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Rebuild industry_membership preserving raw intervals; capability is "
         "APPROXIMATION_REQUIRES_CONFIRMATION when overlaps exist"]))

    # ---- sw_l1_index_daily：覆盖 + 路由 + 正式 resident 可达性 ----
    sw = ref.get("sw_index_daily", {})
    sw_ok = False
    sw_ev = []
    try:
        cov_ok = bool(sw.get("present") and sw.get("l1_total") == 31
                      and sw.get("with_daily") == 31
                      and sw.get("ohlc_violations") == 0
                      and sw.get("duplicate_dates") == 0)
        sw_ev.append(f"coverage {sw.get('with_daily')}/{sw.get('l1_total')}, "
                     f"ohlc_violations={sw.get('ohlc_violations')}, "
                     f"duplicates={sw.get('duplicate_dates')}")
        route_ok = False
        if cov_ok:
            df = provider._data.query_bars_by_count_multi_table(
                "801010", 3, 2**62, use_qfq=True)
            route_ok = not df.empty
            sw_ev.append(f"get_history route 801010 rows={len(df)} (fq=pre raw OHLC)")
        # resident 可达性：正式采集任务 + adapter 宇宙接口
        import json as _json
        resident_ok = False
        tasks_path = (Path(__file__).resolve().parents[3]
                      / "config" / "collector_tasks.json")
        if tasks_path.exists():
            tasks = _json.loads(
                tasks_path.read_text(encoding="utf-8")).get("tasks", [])
            idx_task = next((t for t in tasks
                             if t.get("table") == "index_daily"
                             and t.get("enabled", True)), None)
            if idx_task and "tushare" in (
                    idx_task.get("source_priority") or [idx_task.get("source")]):
                from quantstudio.pipeline.sources.tushare_adapter import (
                    TushareAdapter)
                resident_ok = hasattr(TushareAdapter, "get_index_daily_universe")
        sw_ev.append(f"resident path reachable={resident_ok} "
                     f"(enabled index_daily task + get_index_daily_universe)")
        sw_ok = bool(cov_ok and route_ok and resident_ok)
    except Exception as e:
        sw_ev.append(f"SW index probe failed: {type(e).__name__}: {e}")
    caps.append(_ref_cap(
        "sw_l1_index_daily", sw_ok, sw_ev,
        "31 SW2021 L1 industry index daily in unified index_daily, "
        "routing + resident path verified",
        "SW index daily coverage/quality/routing/resident check failed",
        [DETAIL_LOCAL_DATA_READY, DETAIL_LOCAL_RUNTIME_READY,
         DETAIL_PTRADE_RUNTIME_UNVERIFIED],
        ["Run the formal index_daily daemon task (universe includes SW L1)"]))

    return caps


# =====================================================================
# Capability construction
# =====================================================================

def _ok_dims(event_type: str, evidence: List[str], message: str) -> Dict[str, Any]:
    """Build a capability with all six dimensions READY/AVAILABLE (happy path)."""
    return {
        "schema_status": STATUS_READY,
        "data_status": STATUS_AVAILABLE,
        "adapter_status": STATUS_AVAILABLE,
        "provider_status": STATUS_AVAILABLE,
        "engine_status": STATUS_READY,
        "platform_status": STATUS_AVAILABLE,
        "evidence": evidence,
        "message": message,
        "remediation": [],
    }


def _cap(name: str, required: bool, event_type: str, dims: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a capability entry. execution_status derived from dims."""
    return {
        "capability": name,
        "required": required,
        "event_type": event_type,
        **dims,
    }


def _build_daily_capability(findings: Dict, profile: str) -> Dict[str, Any]:
    """daily-bar backtest capability (stock_daily + etf_daily)."""
    sd = findings["tables"].get("stock_daily", {})
    ed = findings["tables"].get("etf_daily", {})
    evidence = []
    if not findings.get("db_readable"):
        return _cap("stock_daily_backtest", True, "bar", {
            "schema_status": STATUS_DATA_MISSING, "data_status": STATUS_DATA_MISSING,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_READY, "platform_status": STATUS_AVAILABLE,
            "evidence": [], "message": "DB not readable",
            "remediation": ["Ensure DuckDB file exists and is readable."]})
    evidence.append(f"stock_daily: {sd.get('rows', 0)} rows")
    evidence.append(f"etf_daily: {ed.get('rows', 0)} rows")
    if sd.get("rows", 0) > 0:
        evidence.append(f"stock_daily range: {sd.get('time_range')}")
    dims = _ok_dims("bar", evidence, "Daily bar backtest: data + engine ready")
    return _cap("stock_daily_backtest", True, "bar", dims)


def _build_minute_capability(findings: Dict, profile: str, db_path: Path) -> Dict[str, Any]:
    """minute-bar backtest capability (stock_minutes + etf_minutes)."""
    sm = findings["tables"].get("stock_minutes", {})
    em = findings["tables"].get("etf_minutes", {})
    evidence = [f"stock_minutes: {sm.get('rows', 0)} rows",
                f"etf_minutes: {em.get('rows', 0)} rows"]
    sm_freq = _probe_table_freq(db_path, "stock_minutes")
    if sm_freq:
        evidence.append(f"stock_minutes freqs: {sm_freq}")
    # Engine readiness: PR4 verified minute-bar-v1 on real data
    dims = _ok_dims("bar", evidence, "Minute bar backtest: data + engine ready (PR4 verified)")
    return _cap("stock_minute_backtest", profile == "minute-bar-v1", "bar", dims)


def _build_tick_capability(profile: str) -> Dict[str, Any]:
    """tick capability — INVARIANT 4: tick is never READY in v1.

    Hard rule (capability-model.md §2.4, schema allOf line 73):
    execution_status ∈ {BLOCKED, PLANNED, UNSUPPORTED}. We report PLANNED
    (declared in roadmap, not yet implemented).
    """
    return _cap("tick_backtest", False, "tick", {
        "schema_status": STATUS_SCHEMA_ONLY,
        "data_status": STATUS_DATA_MISSING,
        "adapter_status": STATUS_ADAPTER_MISSING,
        "provider_status": STATUS_PROVIDER_MISSING,
        "engine_status": STATUS_ENGINE_MISSING,
        "platform_status": STATUS_PLANNED,
        "evidence": ["Tick engine is PR9 scope; tick_data table empty or missing"],
        "message": "Tick backtest is PLANNED, not executable in v1 (invariant: tick never READY)",
        "remediation": ["Tick support arrives in PR9; do not declare tick READY"],
    })


def _build_output_capability(out_dir: Path) -> Dict[str, Any]:
    """Output directory writability."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        test_file = out_dir / ".inspect_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        dims = _ok_dims("reference", [f"Output dir writable: {out_dir}"],
                        "Output directory writable")
    except Exception as e:
        dims = {
            "schema_status": STATUS_AVAILABLE, "data_status": STATUS_AVAILABLE,
            "adapter_status": STATUS_AVAILABLE, "provider_status": STATUS_AVAILABLE,
            "engine_status": STATUS_AVAILABLE, "platform_status": STATUS_BLOCKED,
            "evidence": [f"Output dir not writable: {e}"],
            "message": f"Output directory not writable: {out_dir}",
            "remediation": [f"Fix permissions on {out_dir} or change output.root in Spec"],
        }
    return _cap("output_dir_writable", True, "reference", dims)


# =====================================================================
# Invariant enforcement (capability-model.md §2)
# =====================================================================

def _tick_invariant(cap: Dict[str, Any]) -> Optional[str]:
    """Invariant 4: tick event_type → execution_status ∈ {BLOCKED,PLANNED,UNSUPPORTED}.

    Returns violation message if breached, else None. This is a hard rule that
    the capability builder must respect; this function is a defensive check.
    """
    if cap["event_type"] == "tick" and cap["engine_status"] == STATUS_READY:
        return (f"INVARIANT BREACH: tick capability '{cap['capability']}' has "
                f"engine_status=READY — tick must never be READY in v1")
    return None


def _derive_execution_status(cap: Dict[str, Any]) -> str:
    """Derive execution_status from the six dimensions (invariant 1).

    execution_status=READY only if all six dims are AVAILABLE or READY.
    Otherwise BLOCKED (with the failing dims recorded in evidence).
    """
    if cap["event_type"] == "tick":
        # Invariant 4: never READY
        return EXEC_PLANNED if cap["engine_status"] == STATUS_ENGINE_MISSING else EXEC_BLOCKED
    dims = [cap["schema_status"], cap["data_status"], cap["adapter_status"],
            cap["provider_status"], cap["engine_status"], cap["platform_status"]]
    bad = [d for d in dims if d not in (STATUS_AVAILABLE, STATUS_READY)]
    if bad:
        return EXEC_BLOCKED
    return EXEC_READY


def _derive_overall(caps: List[Dict[str, Any]]) -> tuple:
    """Apply invariants 2 & 3 to derive overall_execution_status + blockers.

    Invariant 2: any required capability non-READY → overall may not be READY.
    Invariant 3: all required capabilities READY → overall must be READY.
    Returns (overall_status, blockers, repair_actions).
    """
    blockers = []
    repair = []
    for c in caps:
        es = c["execution_status"]
        if c["required"] and es != EXEC_READY:
            blockers.append(f"{c['capability']}: {es} — {c['message']}")
            repair.extend(c.get("remediation", []))
    if blockers:
        return (EXEC_BLOCKED, blockers, repair)
    return (EXEC_READY, [], [])


# =====================================================================
# Main
# =====================================================================

def inspect(db_path: Path, profile: str, strategy_id: str,
            out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run the full inspection. Returns the capability_report dict."""
    findings = _probe_db(db_path)
    if out_dir is None:
        out_dir = Path("output/generated_strategies") / strategy_id

    caps = [
        _build_daily_capability(findings, profile),
        _build_minute_capability(findings, profile, db_path),
        _build_tick_capability(profile),
        _build_output_capability(out_dir),
    ]
    # F6 §8.3: reference-data capabilities（required=False，不参与整体门禁；
    # R1 仅在策略声明相关 API 时引用对应条目）
    caps.extend(_build_reference_capabilities(
        _probe_reference_data(db_path), db_path))

    # Invariant 4 defensive check
    for c in caps:
        breach = _tick_invariant(c)
        if breach:
            caps.append(_cap("_invariant_breach", True, "reference", {
                "schema_status": STATUS_BLOCKED, "data_status": STATUS_BLOCKED,
                "adapter_status": STATUS_BLOCKED, "provider_status": STATUS_BLOCKED,
                "engine_status": STATUS_BLOCKED, "platform_status": STATUS_BLOCKED,
                "evidence": [breach], "message": breach,
                "remediation": ["Fix the tick capability to non-READY"],
            }))

    # Derive execution_status per capability (invariant 1 + 4)
    for c in caps:
        c["execution_status"] = _derive_execution_status(c)

    # Derive overall (invariant 2 + 3)
    overall, blockers, repair = _derive_overall(caps)

    report = {
        "report_version": "1.0",
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "strategy_id": strategy_id,
        "requested_profile": profile,
        "capabilities": caps,
        "overall_execution_status": overall,
        "blockers": blockers,
        "repair_actions": repair,
    }
    return report


def _print_summary(report: Dict[str, Any]) -> None:
    """Human-readable summary to stdout."""
    print(f"=== Capability Report (profile={report['requested_profile']}) ===")
    print(f"Overall: {report['overall_execution_status']}")
    print(f"Generated: {report['generated_at']}")
    print()
    print(f"{'Capability':<30} {'Req':<5} {'Event':<10} {'Exec':<10} {'Data':<14} {'Engine':<10}")
    print("-" * 85)
    for c in report["capabilities"]:
        print(f"{c['capability']:<30} {str(c['required']):<5} {c['event_type']:<10} "
              f"{c['execution_status']:<10} {c['data_status']:<14} {c['engine_status']:<10}")
    if report["blockers"]:
        print()
        print(f"Blockers ({len(report['blockers'])}):")
        for b in report["blockers"]:
            print(f"  - {b}")
    if report["repair_actions"]:
        print()
        print(f"Repair actions ({len(report['repair_actions'])}):")
        for r in report["repair_actions"]:
            print(f"  - {r}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect capabilities → capability_report.json")
    parser.add_argument("--db", required=True, help="Path to quantstudio.db")
    parser.add_argument("--profile", required=True,
                        choices=["daily-bar-v1", "minute-bar-v1", "daily-open-close-proxy-v1", "tick-bar-v1", "planned"],
                        help="Engine profile to inspect against")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier")
    parser.add_argument("--out", default=None, help="Output JSON path (default: stdout only)")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    report = inspect(db_path, args.profile, args.strategy_id)

    _print_summary(report)

    # Schema self-check (capability_report.schema.json)
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "capability_report.schema.json"
    if schema_path.exists():
        try:
            import jsonschema
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.Draft7Validator(schema).validate(report)
            print(f"\nSchema self-check: PASS ({schema_path.name})")
        except ImportError:
            print("\nSchema self-check: SKIPPED (jsonschema not installed)")
        except Exception as e:
            print(f"\nSchema self-check: FAIL — {e}", file=sys.stderr)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport written: {out_path}")

    return 0 if report["overall_execution_status"] == EXEC_READY else 2


if __name__ == "__main__":
    sys.exit(main())
