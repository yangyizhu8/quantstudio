#!/usr/bin/env python
"""QFQ rebase 精度验证：只读分析 xtquant 前复权 add_dev 敏感性 + raw 来源/对齐预检。

不改引擎、不改生产配置、不写正式库（只读）。
证据在 docs/evidence/qfq_rebase_precision_validation_20260729/（受版本管理）。

三模式（互斥）：
  默认 / --verify      只读冻结 fixture 离线复算 + 与 tracked evidence 对比；不连库/xtquant；不写文件
  --preflight-raw      只读正式库 + xtquant，执行 raw_source/raw_alignment 预检（输出 raw_preflight_manifest）
  --refresh            xtquant 重新采集 fixture + capture_manifest（须配合 --update-evidence 写盘）
  --update-evidence    允许写/覆写 tracked evidence（仅与 --refresh 配合）

退出码 fail-closed：任一关键步骤失败返回非零。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

logger = logging.getLogger("qfq_rebase_validate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

BJ_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "qfq_rebase_precision_validation_20260729"
FIXTURE = ROOT / "tests" / "fixtures" / "qfq_rebase_precision" / "fresh_daily"
FORMAL_DB = ROOT / "data" / "quantstudio.db"
D_EPS = 1e-6

SAMPLES = [
    ("000012.SZ", "STOCK", "低价多次分红"), ("600000.SH", "STOCK", "银行多次分红"),
    ("600875.SH", "STOCK", "分红（精度反例）"), ("600039.SH", "STOCK", "分红"),
    ("002864.SZ", "STOCK", "送转/混合"), ("600519.SH", "STOCK", "高价股"),
    ("510300.SH", "ETF", "沪市ETF"), ("159919.SZ", "ETF", "深市ETF"),
]


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _sha_df(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()


def _xtquant_env_info() -> dict:
    """探测 xtquant 实际版本/服务信息（无法获取时注明来源，不伪装）。"""
    info = {"probe_source": "xtquant 包导入探测"}
    try:
        import xtquant
        info["xtquant_package"] = getattr(xtquant, "__version__", "未知（无__version__）")
    except Exception as e:
        info["xtquant_package"] = f"导入失败: {e}"
    info["note"] = "miniQMT 服务信息需 --refresh 时 xtquant.connect 返回；此处不伪装动态探测"
    return info


def add_dev_analysis(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["D_close"] = out["close"].astype(float) - out["close_front"].astype(float)
    out["D_abs"] = out["D_close"].abs()
    out["intraday_range"] = (out["high"].astype(float) - out["low"].astype(float)) / out["close"].astype(float)
    for col in ["open", "high", "low"]:
        out[f"add_dev_{col}"] = (
            out[col].astype(float) - out[f"{col}_front"].astype(float) - out["D_close"]).abs()
        out[f"ratio_{col}"] = np.where(out["D_abs"] > D_EPS, out[f"add_dev_{col}"] / out["D_abs"], np.nan)
    # 逐行综合指标：三列最大值
    out["add_dev_row_max"] = out[["add_dev_open", "add_dev_high", "add_dev_low"]].max(axis=1)
    return out


def load_fixtures() -> dict:
    per = {}
    for xt_code, atype, desc in SAMPLES:
        bare = xt_code.split(".")[0]
        p = FIXTURE / f"{bare}.csv.gz"
        if p.exists():
            per[bare] = (pd.read_csv(p), atype, desc, xt_code)
        else:
            logger.warning(f"fixture 缺失: {p}")
    return per


def collect_summary(per_sample: dict):
    rows = []
    total_obs = total_over = 0
    sec_pcts = []
    for bare, (df, atype, desc, xt_code) in per_sample.items():
        d = add_dev_analysis(df)
        all_add = pd.concat([d["add_dev_open"], d["add_dev_high"], d["add_dev_low"]])
        over = int((all_add > 0.01).sum())
        pct = float((all_add > 0.01).mean() * 100)
        total_obs += len(all_add); total_over += over; sec_pcts.append(pct)
        all_ratio = pd.concat([d["ratio_open"], d["ratio_high"], d["ratio_low"]]).dropna()
        dz = d[d["D_abs"] <= D_EPS]
        dz_apos = int((dz[["add_dev_open", "add_dev_high", "add_dev_low"]] > 0).any(axis=1).sum())
        rows.append({
            "code": bare, "xt_code": xt_code, "type": atype, "desc": desc, "rows": len(d),
            "D_min": round(float(d["D_close"].min()), 4), "D_max": round(float(d["D_close"].max()), 4),
            "add_dev_max": round(float(all_add.max()), 6), "add_dev_p99": round(float(all_add.quantile(0.99)), 6),
            "add_dev_over_0_01": over, "add_dev_over_0_01_pct": round(pct, 2),
            "row_ratio_p99": round(float(all_ratio.quantile(0.99)), 4) if len(all_ratio) else None,
            "row_ratio_max": round(float(all_ratio.max()), 4) if len(all_ratio) else None,
            "D_near_zero_and_add_dev_pos_rows": dz_apos,
            "price_min": round(float(d["close"].min()), 2), "price_max": round(float(d["close"].max()), 2),
            # P2: 相关性用综合指标 add_dev_row_max（非仅 open）
            "corr_rowmax_D": round(float(d["add_dev_row_max"].corr(d["D_abs"])), 3),
            "corr_rowmax_range": round(float(d["add_dev_row_max"].corr(d["intraday_range"])), 3),
            "fresh_sha256": _sha_df(df),
        })
    overall = {
        "total_observations": int(total_obs), "total_over_tick": int(total_over),
        "weighted_false_positive_pct": round(total_over / total_obs * 100, 2) if total_obs else 0,
        "unweighted_security_mean_pct": round(float(np.mean(sec_pcts)), 2) if sec_pcts else 0,
    }
    return pd.DataFrame(rows), overall


def fault_sensitivity(per_sample: dict) -> pd.DataFrame:
    """add_dev 规则故障敏感性分析（非确定性门禁实测）。

    P2: 故障阈值用污染后 D'（k×D 规则真实执行方式）；同步偏移盲区进 CSV。
    """
    rows = []
    d = per_sample["000012"][0].reset_index(drop=True).copy()
    base_open = d.loc[0, "open"]; base_of = d.loc[0, "open_front"]
    base_close = d.loc[0, "close"]; base_cf = d.loc[0, "close_front"]
    base_dev = abs(base_open - base_of - (base_close - base_cf))
    tick = 0.01

    # close_front 单点污染（用污染后 D' 算 k×D 阈值）
    for n in [1, 2, 5, 10, 20]:
        polluted_cf = base_cf + n * tick
        D_polluted = base_close - polluted_cf  # 污染后 D'
        dev = abs(base_open - base_of - D_polluted)
        kD_thresh = 0.06 * abs(D_polluted)
        rows.append({"fault": f"close_front +{n}tick", "domain": "source_semantic",
                     "baseline_add_dev": round(float(base_dev), 6),
                     "polluted_add_dev": round(float(dev), 6), "delta_add_dev": round(float(dev - base_dev), 6),
                     "D_polluted": round(float(D_polluted), 4), "kD_thresh_0.06": round(float(kD_thresh), 4),
                     "detected_by_kD": bool(dev > kD_thresh), "detected_by_tick": bool(dev > tick),
                     "note": "阈值用污染后D'；add_dev敏感性分析非门禁实测"})

    # 整段四 front 同步 +0.5（盲区进 CSV）
    d2 = d.copy()
    d2.loc[:, ["open_front", "high_front", "low_front", "close_front"]] = d2[
        ["open_front", "high_front", "low_front", "close_front"]].astype(float) + 0.5
    D_p = d2.loc[0, "close"] - d2.loc[0, "close_front"]
    dev2 = abs(d2.loc[0, "open"] - d2.loc[0, "open_front"] - D_p)
    base_dev = abs(base_open - base_of - (base_close - base_cf))
    rows.append({"fault": "整段四front同步+0.5", "domain": "source_semantic",
                 "baseline_add_dev": round(float(base_dev), 6), "polluted_add_dev": round(float(dev2), 6),
                 "delta_add_dev": round(float(dev2 - base_dev), 6),
                 "D_polluted": round(float(D_p), 4), "kD_thresh_0.06": round(float(0.06 * abs(D_p)), 4),
                 "detected_by_kD": bool(dev2 > 0.06 * abs(D_p)), "detected_by_tick": bool(dev2 > tick),
                 "note": "同步偏移盲区：delta_add_dev 应≈0（open/close同步偏移，add_dev不变）"})
    return pd.DataFrame(rows)


def raw_source_preflight(errors: list) -> dict:
    import duckdb
    out = {}
    try:
        c = duckdb.connect(str(FORMAL_DB), read_only=True)
        for tbl in ["stock_daily", "etf_daily", "stock_minutes", "etf_minutes"]:
            try:
                df = c.execute(f"SELECT data_source, COUNT(*) n FROM {tbl} GROUP BY data_source").fetchdf()
                out[tbl] = df.to_dict("records")
            except Exception as e:
                errors.append(f"raw_source {tbl}: {e}"); out[tbl] = {"error": str(e)}
        c.close()
    except Exception as e:
        errors.append(f"正式库不可用: {e}"); out["error"] = str(e)
    return out


def raw_alignment_check(errors: list) -> list:
    import duckdb
    from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher, _to_fresh_frame, _ms_to_yyyymmdd
    out = []
    try:
        c = duckdb.connect(str(FORMAL_DB), read_only=True)
        f = XtquantFreshFetcher()
        for bare, atype, xt_code in [("000012", "STOCK", "000012.SZ"), ("510300", "ETF", "510300.SH")]:
            tbl = "stock_daily" if atype == "STOCK" else "etf_daily"
            db_df = c.execute(f"SELECT time, open, high, low, close FROM {tbl} WHERE code=? ORDER BY time", [bare]).fetchdf()
            if len(db_df) == 0:
                errors.append(f"raw_alignment {bare}: 库内无数据"); continue
            tmin, tmax = int(db_df.time.min()), int(db_df.time.max())
            none_df, _ = f.fetch_none_front(atype, xt_code, "1d", _ms_to_yyyymmdd(tmin), _ms_to_yyyymmdd(tmax))
            fresh = _to_fresh_frame(none_df, none_df.copy(), atype, bare)
            db_t = set(db_df["time"].astype(int)); fr_t = set(fresh["time"].astype(int))
            r = {"code": bare, "target_count": len(db_df), "fresh_count": len(fresh),
                 "matched_count": len(db_t & fr_t), "missing_target": len(db_t - fr_t),
                 "missing_fresh": len(fr_t - db_t), "duplicate_target": int(db_df["time"].duplicated().sum()),
                 "duplicate_fresh": int(fresh["time"].duplicated().sum())}
            m = db_df.merge(fresh[["time", "open", "high", "low", "close"]].rename(
                columns={"open": "fo", "high": "fh", "low": "fl", "close": "fc"}), on="time", how="inner")
            for col, fc in [("open", "fo"), ("high", "fh"), ("low", "fl"), ("close", "fc")]:
                diff = (m[col].astype(float) - m[fc].astype(float)).abs()
                r[f"{col}_mismatch"] = int((diff > 1e-6).sum()); r[f"{col}_max_abs_diff"] = round(float(diff.max()), 6)
            r["fully_aligned"] = (r["fresh_count"] == r["target_count"] == r["matched_count"]
                and r["missing_target"] == 0 and r["missing_fresh"] == 0 and r["duplicate_target"] == 0
                and r["duplicate_fresh"] == 0 and all(r[f"{c}_mismatch"] == 0 for c in ["open", "high", "low", "close"]))
            if not r["fully_aligned"]:
                errors.append(f"raw_alignment {bare}: 未完全对齐 {r}")
            out.append(r)
        c.close()
    except Exception as e:
        errors.append(f"raw_alignment 失败: {e}"); out.append({"error": str(e)})
    return out


def mode_verify(errors: list) -> int:
    """默认模式：只读 fixture 离线复算 + 与全部 tracked evidence 对比，不写文件。

    校验链（阻断1+2）：
    - summary: 列集完整（computed==tracked，防缺列静默忽略）+ 逐列值一致（除 SHA 列另验）
    - fault: 列集完整 + 逐行逐值一致（重算 vs tracked fault_sensitivity.csv）
    - fixture SHA 三分串联: 当前 fixture canonical SHA == capture_manifest.fixture_canonical == summary.fresh_sha256
    - analysis_manifest: summary_sha/fault_sha 与重算一致 + completed==requested==8 + overall_stats 一致
    """
    logger.info("模式: verify（离线复算 + 对比全部 tracked evidence，不写文件）")
    for f in ["summary.csv", "fault_sensitivity.csv", "analysis_manifest.json", "capture_manifest.json"]:
        if not (EVIDENCE / f).exists():
            errors.append(f"tracked {f} 不存在"); 
    if errors:
        return 1
    per = load_fixtures()
    if len(per) != len(SAMPLES):
        errors.append(f"fixture 不完整: {len(per)}/{len(SAMPLES)}")
    summary_df, overall = collect_summary(per)
    fault_df = fault_sensitivity(per)

    # (1) summary 对比：列集完整 + 逐列值（规范 code dtype）
    tracked_summary = pd.read_csv(EVIDENCE / "summary.csv", dtype={"code": str})
    summary_df_n = summary_df.copy(); summary_df_n["code"] = summary_df_n["code"].astype(str)
    if set(summary_df_n.columns) != set(tracked_summary.columns):
        errors.append(f"summary 列集不一致: computed={set(summary_df_n.columns)} tracked={set(tracked_summary.columns)}")
    else:
        for col in summary_df_n.columns:
            if col == "fresh_sha256":
                continue  # SHA 列由 fixture 串联校验
            s = summary_df_n[col].astype(str); t = tracked_summary[col].astype(str)
            if not s.equals(t):
                errors.append(f"summary 列 {col} 与 tracked 不一致")

    # (2) fault 对比：列集完整 + 逐行逐值一致
    tracked_fault = pd.read_csv(EVIDENCE / "fault_sensitivity.csv")
    if set(fault_df.columns) != set(tracked_fault.columns):
        errors.append(f"fault 列集不一致")
    elif len(fault_df) != len(tracked_fault):
        errors.append(f"fault 行数不一致: computed={len(fault_df)} tracked={len(tracked_fault)}")
    else:
        for col in fault_df.columns:
            if not fault_df[col].astype(str).equals(tracked_fault[col].astype(str)):
                errors.append(f"fault 列 {col} 与 tracked 不一致")

    # (3) fixture SHA 三分串联 + 证券集合 + 文件字节校验
    capture_m = json.loads((EVIDENCE / "capture_manifest.json").read_text(encoding="utf-8"))
    cap_map = {p["code"]: p for p in capture_m.get("per_security", [])}
    # 证券集合校验（防 manifest 多/少证券被忽略）
    expected_codes = {s.split(".")[0] for s, _, _ in SAMPLES}
    cap_codes = set(cap_map.keys())
    if cap_codes != expected_codes:
        errors.append(f"capture_manifest 证券集合不一致: cap={cap_codes} expected={expected_codes}")
    if set(per.keys()) != expected_codes:
        errors.append(f"fixture 证券集合不一致: loaded={set(per.keys())} expected={expected_codes}")
    for bare, (df, *_) in per.items():
        canonical = _sha_df(df)  # fixture 读回后的 canonical SHA
        t_sum = tracked_summary.loc[tracked_summary.code == str(bare), "fresh_sha256"]
        t_cap = cap_map.get(bare, {})
        sum_sha = t_sum.iloc[0] if len(t_sum) else None
        if sum_sha != canonical:
            errors.append(f"fixture {bare}: canonical({canonical[:10]}) != summary.fresh_sha256({(sum_sha or 'NA')[:10]})")
        if t_cap.get("fixture_canonical_sha256") != canonical:
            errors.append(f"fixture {bare}: canonical({canonical[:10]}) != capture.fixture_canonical({(t_cap.get('fixture_canonical_sha256') or 'NA')[:10]})")
        # 文件字节 SHA 校验（检测 gzip header 等文件级篡改，canonical 不变但文件已变）
        fixture_path = FIXTURE / f"{bare}.csv.gz"
        if fixture_path.exists():
            actual_file_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            if actual_file_sha != t_cap.get("fixture_file_sha256"):
                errors.append(f"fixture {bare}: file SHA({actual_file_sha[:10]}) != capture.fixture_file({(t_cap.get('fixture_file_sha256') or 'NA')[:10]})")
        else:
            errors.append(f"fixture {bare}: 文件不存在 {fixture_path}")

    # (4) analysis_manifest: SHA 串联 + completed/requested + overall
    analysis_m = json.loads((EVIDENCE / "analysis_manifest.json").read_text(encoding="utf-8"))
    recomputed_summary_sha = _sha_df(pd.read_csv(EVIDENCE / "summary.csv"))
    recomputed_fault_sha = _sha_df(pd.read_csv(EVIDENCE / "fault_sensitivity.csv"))
    if analysis_m.get("summary_sha") != recomputed_summary_sha:
        errors.append("analysis_manifest.summary_sha 与 tracked summary.csv 不一致")
    if analysis_m.get("fault_sha") != recomputed_fault_sha:
        errors.append("analysis_manifest.fault_sha 与 tracked fault_sensitivity.csv 不一致")
    if int(analysis_m.get("completed_samples", 0)) != len(SAMPLES) or int(analysis_m.get("requested_samples", 0)) != len(SAMPLES):
        errors.append("analysis_manifest completed/requested != 全样本")
    # overall 对比（重算 vs manifest 记录）
    am_overall = analysis_m.get("overall_stats", {})
    for k in ["total_observations", "total_over_tick", "weighted_false_positive_pct", "unweighted_security_mean_pct"]:
        if am_overall.get(k) != overall.get(k):
            errors.append(f"analysis_manifest overall.{k} ({am_overall.get(k)}) != 重算 ({overall.get(k)})")

    if errors:
        for e in errors:
            logger.error(e)
        return 1
    logger.info(f"verify PASS: overall={overall}, 全 evidence 串联校验通过")
    return 0


def mode_refresh(update_evidence: bool, errors: list) -> int:
    """--refresh：xtquant 重新采集，事务式原子更新。

    流程（阻断2修复：发布阶段也事务式）：
    1. 临时 bundle 采集全部 fixture + 生成全部 evidence（capture/analysis/summary/fault）
    2. 临时 bundle 内 verify（重读 fixture 复算 + SHA 串联自检）
    3. 全 PASS → 备份正式文件 → 逐文件发布（shutil.copy2）→ 发布后 verify → 失败恢复完整备份
    4. 任一失败（含发布阶段）→ 从备份恢复全部正式文件
    采集或发布任一失败都不改 tracked evidence。
    """
    import shutil
    logger.info("模式: refresh（xtquant 重新采集，事务式原子更新）")
    from quantstudio.pipeline.qfq_fresh_capture import XtquantFreshFetcher, _to_fresh_frame
    if not update_evidence:
        errors.append("--refresh 须配合 --update-evidence 才能写盘")
        return 1
    f = XtquantFreshFetcher()
    tmp_root = FIXTURE.parent / "_tmp_refresh_bundle"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_fixture = tmp_root / "fixtures"
    tmp_evidence = tmp_root / "evidence"
    tmp_fixture.mkdir(parents=True)
    tmp_evidence.mkdir(parents=True)

    # —— 阶段1：临时 bundle 采集 + 生成全部文件 ——
    capture = {"captured_at": _now_iso(), "xtquant_env": _xtquant_env_info(),
               "download_range": "20150101-20260726", "per_security": []}
    for xt_code, atype, desc in SAMPLES:
        try:
            none_df, front_df = f.fetch_none_front(atype, xt_code, "1d", "20150101", "20260726")
            fresh = _to_fresh_frame(none_df, front_df, atype, xt_code.split(".")[0])
            bare = xt_code.split(".")[0]
            cols = ["time", "open", "high", "low", "close",
                    "open_front", "high_front", "low_front", "close_front"]
            sub = fresh[cols]
            fpath = tmp_fixture / f"{bare}.csv.gz"
            sub.to_csv(fpath, index=False, compression="gzip")
            source_sha = _sha_df(sub)
            canonical_sha = _sha_df(pd.read_csv(fpath))
            file_sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
            capture["per_security"].append({
                "code": bare, "rows": len(fresh),
                "source_dataframe_sha256": source_sha,
                "fixture_canonical_sha256": canonical_sha,
                "fixture_file_sha256": file_sha,
            })
            logger.info(f"采集 {xt_code}: {len(fresh)} 行")
        except Exception as e:
            errors.append(f"采集 {xt_code} 失败: {e}")
    # 重复下载稳定性
    if not errors:
        n1, fr1 = f.fetch_none_front("STOCK", "000012.SZ", "1d", "20250101", "20260726")
        n2, fr2 = f.fetch_none_front("STOCK", "000012.SZ", "1d", "20250101", "20260726")
        f1 = _to_fresh_frame(n1, fr1, "STOCK", "000012"); f2 = _to_fresh_frame(n2, fr2, "STOCK", "000012")
        capture["repeat_download_identical"] = bool(f1.equals(f2))
        capture["repeat_download_sha1"] = _sha_df(f1); capture["repeat_download_sha2"] = _sha_df(f2)
        if not capture["repeat_download_identical"]:
            errors.append("重复下载不一致")
    # 阶段1 失败 → 清临时，不改 tracked
    if errors:
        shutil.rmtree(tmp_root)
        for e in errors: logger.error(e)
        return 1

    # —— 阶段2：临时 bundle 内生成 evidence + verify ——
    # 写临时 capture_manifest
    (tmp_evidence / "capture_manifest.json").write_text(
        json.dumps(capture, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # 从临时 fixture 生成 summary/fault/analysis（复用逻辑，但指向 tmp 路径）
    tmp_per = {p["code"]: (pd.read_csv(tmp_fixture / f"{p['code']}.csv.gz"), at, desc, xc)
               for p in capture["per_security"]
               for s, at, desc in [(x, y, z) for x, y, z in SAMPLES if x.split(".")[0] == p["code"]]
               for xc in [s]}
    summary_df, overall = collect_summary(tmp_per)
    fault_df = fault_sensitivity(tmp_per)
    summary_df.to_csv(tmp_evidence / "summary.csv", index=False)
    fault_df.to_csv(tmp_evidence / "fault_sensitivity.csv", index=False)
    analysis = {"analyzed_at": _now_iso(), "overall_stats": overall,
                "summary_sha": _sha_df(pd.read_csv(tmp_evidence / "summary.csv")),
                "fault_sha": _sha_df(pd.read_csv(tmp_evidence / "fault_sensitivity.csv")),
                "completed_samples": len(tmp_per), "requested_samples": len(SAMPLES)}
    (tmp_evidence / "analysis_manifest.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    # 临时 bundle 自检：fixture SHA 三分串联
    cap_map = {p["code"]: p for p in capture["per_security"]}
    for bare, (df, *_) in tmp_per.items():
        canonical = _sha_df(df)
        if cap_map[bare]["fixture_canonical_sha256"] != canonical:
            errors.append(f"tmp bundle {bare}: canonical 串联失败")
        fsha = hashlib.sha256((tmp_fixture / f"{bare}.csv.gz").read_bytes()).hexdigest()
        if cap_map[bare]["fixture_file_sha256"] != fsha:
            errors.append(f"tmp bundle {bare}: file SHA 串联失败")
    if len(tmp_per) != len(SAMPLES):
        errors.append(f"tmp bundle fixture 不完整: {len(tmp_per)}/{len(SAMPLES)}")
    if errors:
        shutil.rmtree(tmp_root)
        for e in errors: logger.error(e)
        return 1
    logger.info("临时 bundle 生成 + 自检 PASS")

    # —— 阶段3：事务式发布（备份 → 替换 → verify → 失败恢复）——
    FIXTURE.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # 备份将被替换的正式文件
    backup_dir = FIXTURE.parent / "_backup_refresh"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir(parents=True)
    backup_fixture = backup_dir / "fixtures"; backup_evidence = backup_dir / "evidence"
    shutil.copytree(FIXTURE, backup_fixture, dirs_exist_ok=True)
    shutil.copytree(EVIDENCE, backup_evidence, dirs_exist_ok=True)
    publish_errors: list = []

    def _restore_from_backup():
        """发布失败时从备份恢复全部正式文件。"""
        for old in FIXTURE.glob("*"):
            old.unlink()
        for b in backup_fixture.glob("*"):
            shutil.copy2(b, FIXTURE / b.name)
        for old in EVIDENCE.glob("*"):
            old.unlink()
        for b in backup_evidence.glob("*"):
            shutil.copy2(b, EVIDENCE / b.name)

    try:
        # 替换 fixture
        for old in FIXTURE.glob("*.csv.gz"):
            old.unlink()
        for tmp in tmp_fixture.glob("*.csv.gz"):
            shutil.copy2(tmp, FIXTURE / tmp.name)
        # 替换 evidence（capture/analysis/summary/fault；保留 raw_preflight_manifest）
        for name in ["capture_manifest.json", "analysis_manifest.json", "summary.csv", "fault_sensitivity.csv"]:
            shutil.copy2(tmp_evidence / name, EVIDENCE / name)
    except Exception as e:
        publish_errors.append(f"发布失败: {e}")
        _restore_from_backup()
        shutil.rmtree(backup_dir); shutil.rmtree(tmp_root)
        for pe in publish_errors: logger.error(pe)
        return 1
    # 发布后 verify
    verify_errors: list = []
    vrc = mode_verify(verify_errors)
    if vrc != 0:
        logger.error("发布后 verify 失败，执行恢复")
        _restore_from_backup()
        shutil.rmtree(backup_dir); shutil.rmtree(tmp_root)
        for ve in verify_errors: logger.error(ve)
        return 1
    # 成功：清理备份 + 临时
    shutil.rmtree(backup_dir)
    shutil.rmtree(tmp_root)
    logger.info("refresh 事务式发布成功（备份→替换→verify 全 PASS）")
    return 0


def mode_preflight_raw(errors: list) -> int:
    """--preflight-raw：只读正式库 + xtquant raw 预检，输出 raw_preflight_manifest。"""
    logger.info("模式: preflight-raw（正式库 + xtquant raw 预检）")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    preflight = {"preflight_at": _now_iso(), "raw_source": raw_source_preflight(errors),
                 "raw_alignment": raw_alignment_check(errors)}
    preflight["validation_status"] = "PASS" if not errors else "FAIL"
    preflight["blocking_errors"] = errors
    (EVIDENCE / "raw_preflight_manifest.json").write_text(json.dumps(preflight, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if errors:
        for e in errors: logger.error(e)
        return 1
    logger.info("preflight-raw PASS")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--preflight-raw", action="store_true")
    ap.add_argument("--update-evidence", action="store_true")
    ap.add_argument("--verify-hashes", action="store_true", help="等同默认 verify 模式")
    args = ap.parse_args()
    errors: list = []
    modes = sum([args.refresh, args.preflight_raw, bool(args.verify_hashes)])
    if modes > 1:
        logger.error("--refresh/--preflight-raw/--verify-hashes 互斥"); return 1
    if args.refresh:
        return mode_refresh(args.update_evidence, errors)
    if args.preflight_raw:
        return mode_preflight_raw(errors)
    # 默认 verify（含 --verify-hashes）
    return mode_verify(errors)


if __name__ == "__main__":
    sys.exit(main())
