#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 raw 准入预检（只读 · 断点续跑 · 事务式证据冻结）。

目的：生产启用前验证全市场（stock_basic ∪ etf_basic，约 6807 只）的
fresh xtquant none raw 与库内 raw 是否逐 bar 对齐，判定 rebase 准入。

铁律（只读预检任务）：
  - 全程 read_only=True 直连 duckdb，绝不写正式库、绝不改引擎/配置。
  - 不 commit / push / 不建分支；qfq_orchestrator.enabled 保持 false。
  - fresh 下载仅经 XtquantFreshFetcher（不落库）。

三段式证据工作流（与 9 证券版一致的语义，但无 fixture 冻结）：
  preflight       下载 fresh + 对齐库内 + 逐券追加证据（支持断点续跑，默认）
  verify          重读 manifest/CSV 校验一致性（不下载）
  update-evidence 从现有证据重算报告 + 准入名单（不下载）

断点续跑：manifest.json 记录每 code 的段级结果；中断后重跑自动跳过已完成 code。
每次 flush 先写 .tmp 再 rename（事务式，避免半成品）。

用法：
  # 全量（miniQMT 须运行），后台运行，断点续跑
  python scripts/preflight_raw_fullmarket.py --mode preflight

  # 冒烟：只跑前 20 只，验证环境与映射
  python scripts/preflight_raw_fullmarket.py --mode preflight --limit 20

  # 指定 code 子集（如重跑失败部分）
  python scripts/preflight_raw_fullmarket.py --mode preflight --codes-file codes.txt

  # 仅验证环境/映射/库内覆盖，不下载
  python scripts/preflight_raw_fullmarket.py --mode preflight --dry-run

  # 重算报告 + 准入名单（不下载）
  python scripts/preflight_raw_fullmarket.py --mode update-evidence
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import datetime
from pathlib import Path

# 复用 9 证券预检的纯函数（不触发其 __main__ 块）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preflight_raw_admission import (  # noqa: E402
    _align_segment, _derive_status, _fetch_fresh, FREQ_TO_PERIOD,
)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

try:
    from quantstudio.pipeline.qfq_fresh_capture import (  # noqa: E402
        XtquantFreshFetcher, FakeFreshFetcher,
    )
except Exception:  # pragma: no cover
    XtquantFreshFetcher = None
    FakeFreshFetcher = None

from quantstudio.pipeline.qfq_maintenance import resolve_ts_codes  # noqa: E402

ASSET_TYPES = ("STOCK", "ETF")
RAW_COLUMNS = ["time", "open", "high", "low", "close", "volume", "amount"]
# 表映射（大写 asset_type → (daily, minute)）。注意：导入的 _tables() 以小写
# "stock"/"etf" 比较，此处显式用大写键，避免大小写踩坑。
RAW_TABLES = {
    "STOCK": ("stock_daily", "stock_minutes"),
    "ETF": ("etf_daily", "etf_minutes"),
}
DS_DEFAULT = "20000101"
SEGMENT_EXCLUDED = ("NO_DATA",)  # 库内无该段数据 → 不参与对齐/不阻断


def log(*a):
    print(f"[{datetime.datetime.now():%H:%M:%S}]", *a, flush=True)


_TS_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def _is_mappable(code):
    """code 是否为可映射格式：6 位裸码 或 合法 ts_code（带后缀）。"""
    c = (code or "").strip()
    if c.isdigit() and len(c) == 6:
        return True
    return bool(_TS_RE.match(c.upper()))


# --------------------------------------------------------------------------- #
# 只读库内读取（read_only 直连，不复用 DuckDBDataAccess 以免任何写）
# --------------------------------------------------------------------------- #
def open_ro(db_path: str):
    """以 read_only 打开正式库。失败时明确抛出（不创建库）。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"正式库不存在：{db_path}")
    return duckdb.connect(db_path, read_only=True)


def load_all_codes(db_ro):
    """从 stock_basic/etf_basic 读全部 (code, asset_type)。"""
    out = []
    for at, table in (("STOCK", "stock_basic"), ("ETF", "etf_basic")):
        try:
            rows = db_ro.execute(f"SELECT code FROM {table}").fetchall()
            for (c,) in rows:
                if c is not None:
                    out.append((str(c).strip(), at))
        except Exception as e:  # 表缺失则跳过该资产类
            log(f"[WARN] 读取 {table} 失败（跳过该类）：{e}")
    return out


def distinct_minute_freqs(db_ro, code, asset_type):
    """库内某证券存在的全部分钟 freq（排除 daily）。列名为 freq。"""
    minute_table = RAW_TABLES[asset_type][1]
    try:
        rows = db_ro.execute(
            f"SELECT DISTINCT freq FROM {minute_table} WHERE code=?",
            [code],
        ).fetchall()
        return sorted(str(x[0]) for x in rows if x[0] and x[0] != "daily")
    except Exception:
        return []


def read_lib_ro(db_ro, table, code, freq=None):
    """读库内某表某 code 的 raw 列（read_only）。分钟表按 freq 过滤。"""
    try:
        if freq is None:
            sql = (f"SELECT time, open, high, low, close, volume, amount "
                   f"FROM {table} WHERE code=? ORDER BY time")
            params = [code]
        else:
            sql = (f"SELECT time, open, high, low, close, volume, amount "
                   f"FROM {table} WHERE code=? AND freq=? ORDER BY time")
            params = [code, freq]
        return db_ro.execute(sql, params).fetchdf()
    except Exception:
        return pd.DataFrame(columns=RAW_COLUMNS)


# --------------------------------------------------------------------------- #
# 段对齐（复用 _align_segment 纯计算；download 经 fetcher）
# --------------------------------------------------------------------------- #
def _blank_seg(freq, n, download_ok, note):
    """构造空对齐段（库空 / 下载失败 / fresh 空）。"""
    return {
        "download_ok": download_ok, "segment": freq, "target_count": n,
        "fresh_count": 0, "matched_count": 0, "missing_target": [],
        "missing_fresh": [], "duplicate_target": 0, "duplicate_fresh": 0,
        "fully_time_covered": False, "open_mismatch": 0, "high_mismatch": 0,
        "low_mismatch": 0, "close_mismatch": 0, "max_abs_diff_open": 0.0,
        "max_abs_diff_high": 0.0, "max_abs_diff_low": 0.0,
        "max_abs_diff_close": 0.0, "fully_ohlc_aligned": False,
        "merged": None, "download_note": note,
    }


def fetch_segment(asset_type, xt_code, code, freq, db_ro, fetcher):
    """freq: 'daily' 或 '1min' 等库内段标识。返回 (seg_dict, status_str)。

    复用 9 证券预检的 _fetch_fresh（含 xtquant 周期映射 1d/1m + 同窗口裁剪）
    与 _align_segment / _derive_status 判定契约。
    """
    daily_table, minute_table = RAW_TABLES[asset_type]
    is_daily = (freq == "daily")
    table = daily_table if is_daily else minute_table
    lib_df = read_lib_ro(db_ro, table, code, freq=None if is_daily else freq)
    if len(lib_df) == 0:
        return (_blank_seg(freq, 0, True, "library empty for segment"), "NO_DATA")
    tmin = int(lib_df["time"].min())
    tmax = int(lib_df["time"].max())
    period_fresh = FREQ_TO_PERIOD.get(freq, "1m")  # daily→1d, 1min→1m
    try:
        fresh_df = _fetch_fresh(fetcher, asset_type, xt_code, period_fresh, tmin, tmax)
        if fresh_df is None:
            fresh_df = pd.DataFrame(columns=["open", "high", "low", "close"])
        dl_ok = True
        dl_note = ""
    except Exception as e:
        fresh_df = pd.DataFrame(columns=["open", "high", "low", "close"])
        dl_ok = False
        dl_note = f"{type(e).__name__}: {e}"
    if dl_ok and len(fresh_df):
        seg = _align_segment(lib_df, fresh_df, freq)
        seg["download_ok"] = True
        seg["segment"] = freq
        seg["download_note"] = dl_note
        return (seg, _derive_status(seg))
    # 下载失败 / 下载成功但 fresh 空
    return (_blank_seg(freq, len(lib_df), dl_ok, dl_note),
            _derive_status(_blank_seg(freq, len(lib_df), dl_ok, dl_note)))


# --------------------------------------------------------------------------- #
# 证券级聚合：BLOCK / MIGRATE / ADMISSIBLE / DOWNLOAD_FAILED / NO_DATA
# --------------------------------------------------------------------------- #
def aggregate_security(segs):
    """segs: list of (period, seg_dict, status)。

    证券级最终判定（最严重优先）：
      DOWNLOAD_FAILED > OHLC_MISMATCH(BLOCK) > TIME_MISMATCH{BLOCK|MIGRATE} > ADMISSIBLE
      NO_DATA：所有段库内均无数据（不参与 rebase）。
    """
    applicable = [(p, s, st) for (p, s, st) in segs if st not in SEGMENT_EXCLUDED]
    if not applicable:
        return "NO_DATA", "库内该证券无任何 raw 段数据", {}
    if any(st == "DOWNLOAD_FAILED" for _, _, st in applicable):
        return "DOWNLOAD_FAILED", "fresh 下载失败（需重跑）", {}
    if any(st == "OHLC_MISMATCH" for _, _, st in applicable):
        return "BLOCK", "价格（OHLC）不一致，fresh 与库内 raw 信任边界失效", {}
    time_segs = [(p, s) for (p, s, st) in applicable if st == "TIME_MISMATCH"]
    if time_segs:
        # 任一 TIME_MISMATCH 段：库内存在而 fresh 缺失（missing_target>0）
        # → xtquant 无此数据，rebase 无源 → BLOCK
        if any(len(s.get("missing_target", []) or []) > 0 for _, s in time_segs):
            return "BLOCK", "库内存在 fresh 缺失的 bar（xtquant 无数据，rebase 无源）", {}
        # 仅 fresh 比库内更全（missing_fresh>0，库内历史缺口）→ 先迁移 raw 补齐 → MIGRATE
        return "MIGRATE", "fresh 包含库内缺失的 bar（库内历史缺口，需先迁移 raw）", {}
    return "ADMISSIBLE", "全部段完全覆盖 + OHLC 逐 bar 对齐", {}


# --------------------------------------------------------------------------- #
# 证据写入（事务式：.tmp → rename）
# --------------------------------------------------------------------------- #
SUMMARY_FIELDS = [
    "code", "asset_type", "xt_code", "period", "status",
    "target_count", "fresh_count", "matched_count", "missing_target",
    "missing_fresh", "duplicate_target", "duplicate_fresh",
    "fully_time_covered", "open_mismatch", "high_mismatch", "low_mismatch",
    "close_mismatch", "max_abs_diff_close", "fully_ohlc_aligned",
    "download_ok", "download_note",
]


def write_summary_atomic(path, rows):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, str(path))


def write_json_atomic(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(path))


def flush_evidence(summary_csv, manifest_path, all_rows, manifest):
    write_summary_atomic(summary_csv, all_rows)
    write_json_atomic(manifest_path, manifest)


# --------------------------------------------------------------------------- #
# 运行模式
# --------------------------------------------------------------------------- #
def run_dry_run(db_ro, all_codes, xt_map, args, today):
    """仅统计库内覆盖 + 映射（不下载、不判定准入）。用于环境/映射校验。"""
    rows = []
    for (code, asset_type) in all_codes:
        xt_code = xt_map.get((code, asset_type), code)
        daily_df = read_lib_ro(db_ro, RAW_TABLES[asset_type][0], code)
        n_daily = len(daily_df)
        freqs = distinct_minute_freqs(db_ro, code, asset_type)
        n_min = 0
        for _p in freqs:
            n_min += len(read_lib_ro(db_ro, RAW_TABLES[asset_type][1], code, freq=_p))
        rows.append((code, asset_type, xt_code, n_daily, ",".join(freqs), n_min))
    with_daily = sum(1 for r in rows if r[3] > 0)
    with_min = sum(1 for r in rows if r[5] > 0)
    log(f"[dry-run] code={len(rows)} 有 daily={with_daily} 有 minute={with_min}")
    log("[dry-run] 映射样本(前10)：")
    for r in rows[:10]:
        log(f"  {r[0]} {r[1]} -> {r[2]} daily={r[3]} freqs=[{r[4]}] min_rows={r[5]}")
    cov = Path(args.summary_csv).parent / "coverage_dryrun.csv"
    with open(cov, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "asset_type", "xt_code", "n_daily",
                    "minute_freqs", "n_minute_rows"])
        for r in rows:
            w.writerow(r)
    log(f"[dry-run] 库内覆盖统计已写：{cov}")


def run_preflight(args, db_path, today_yyyymmdd):
    db_ro = open_ro(db_path)
    try:
        # 1) 加载全量 code
        if args.codes_file:
            wanted = []
            for line in open(args.codes_file, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                code = parts[0].strip()
                at = parts[1].strip().upper() if len(parts) > 1 else None
                wanted.append((code, at or "STOCK"))
            all_codes = [(c, a) for (c, a) in wanted]
        else:
            all_codes = load_all_codes(db_ro)

        if args.asset:
            all_codes = [(c, a) for (c, a) in all_codes if a == args.asset]

        manifest = load_manifest(args.manifest)

        # 2) 过滤非法 code（临时代码 T 前缀等无法映射），记录为 SKIPPED
        skipped = [(c, a) for (c, a) in all_codes if not _is_mappable(c)]
        for (c, a) in skipped:
            manifest.setdefault("done", {})[f"{c}|{a}"] = {
                "asset_type": a, "xt_code": c, "final": "SKIPPED",
                "reason": "code 格式非法，无法映射 ts_code（如临时代码 T 前缀）",
                "segments": {},
            }
        if skipped:
            log(f"[WARN] 跳过 {len(skipped)} 只非法 code（无法映射 ts_code）")
        all_codes = [(c, a) for (c, a) in all_codes if _is_mappable(c)]

        # 3) 批量映射 ts_code（按资产类）
        by_asset = {"STOCK": [], "ETF": []}
        for c, a in all_codes:
            by_asset.setdefault(a, []).append(c)
        xt_map = {}
        for a in ("STOCK", "ETF"):
            codes_a = by_asset.get(a, [])
            if codes_a:
                ts = resolve_ts_codes(codes_a, a, db_path)
                for c, t in zip(codes_a, ts):
                    xt_map[(c, a)] = t

        if args.dry_run:
            run_dry_run(db_ro, all_codes, xt_map, args, today_yyyymmdd)
            return

        # 3) 断点续跑：读取已有 manifest 的 done
        done = set(manifest.get("done", {}).keys())
        # 若 manifest 无 done 但 summary csv 存在，从 csv 重建 done（容错）
        if not done and args.summary_csv.exists():
            done = {(r["code"], r.get("asset_type", "STOCK"))
                    for r in read_summary_rows(args.summary_csv)}

        pending = [(c, a) for (c, a) in all_codes
                   if (c, a) not in done and (c, a) in xt_map]
        # 注意：xt_map 必有该 code（刚映射）；done 用 (code,asset_type) 键

        # 4) fetcher（真实预检需 miniQMT/xtquant）
        if args.dry_run:
            fetcher = None
            log("[dry-run] 不下载 fresh，仅统计库内覆盖与映射")
        elif XtquantFreshFetcher is None:
            log("[ERROR] 真实预检需要 xtquant（miniQMT 运行）且 XtquantFreshFetcher 可导入；"
                "当前不可用，退出。请确认 miniQMT 客户端已启动且 xtquant 在 PYTHONPATH。")
            sys.exit(2)
        else:
            try:
                fetcher = XtquantFreshFetcher()
            except Exception as e:
                log(f"[ERROR] 连接 miniQMT 失败（未启动 QMT？）：{e}")
                sys.exit(2)

        # 5) 内存中的全部段行（用于断点续跑重建）
        all_rows = list(read_summary_rows(args.summary_csv)) if args.summary_csv.exists() else []

        total = len(pending)
        log(f"总 code={len(all_codes)} 资产分布 "
            f"STOCK={len(by_asset['STOCK'])} ETF={len(by_asset['ETF'])}；"
            f"待处理={total} 已完成={len(done)}")
        if args.limit:
            pending = pending[:args.limit]
            total = len(pending)
            log(f"[--limit] 仅处理前 {total} 只")

        ds = args.start or DS_DEFAULT
        de = args.end or today_yyyymmdd
        processed = 0
        try:
            for (code, asset_type) in pending:
                xt_code = xt_map.get((code, asset_type), code)
                freqs = ["daily"] + distinct_minute_freqs(db_ro, code, asset_type)
                segs = []
                for period in freqs:
                    seg, status = fetch_segment(asset_type, xt_code, code, period,
                                                 db_ro, fetcher)
                    segs.append((period, seg, status))
                    all_rows.append({
                        "code": code, "asset_type": asset_type,
                        "xt_code": xt_code, "period": period, "status": status,
                        "target_count": seg.get("target_count", 0),
                        "fresh_count": seg.get("fresh_count", 0),
                        "matched_count": seg.get("matched_count", 0),
                        "missing_target": len(seg.get("missing_target", []) or []),
                        "missing_fresh": len(seg.get("missing_fresh", []) or []),
                        "duplicate_target": seg.get("duplicate_target", 0),
                        "duplicate_fresh": seg.get("duplicate_fresh", 0),
                        "fully_time_covered": seg.get("fully_time_covered", False),
                        "open_mismatch": seg.get("open_mismatch", 0),
                        "high_mismatch": seg.get("high_mismatch", 0),
                        "low_mismatch": seg.get("low_mismatch", 0),
                        "close_mismatch": seg.get("close_mismatch", 0),
                        "max_abs_diff_close": round(seg.get("max_abs_diff_close", 0.0) or 0.0, 8),
                        "fully_ohlc_aligned": seg.get("fully_ohlc_aligned", False),
                        "download_ok": seg.get("download_ok", False),
                        "download_note": seg.get("download_note", "") or "",
                    })
                final, reason, _ = aggregate_security(segs)
                manifest.setdefault("done", {})[f"{code}|{asset_type}"] = {
                    "asset_type": asset_type, "xt_code": xt_code,
                    "final": final, "reason": reason,
                    "segments": {p: st for (p, _s, st) in segs},
                }
                processed += 1
                if processed % args.batch_size == 0:
                    flush_evidence(args.summary_csv, args.manifest, all_rows, manifest)
                    log(f"进度 {processed}/{total} 已 flush（{final}←{code}）")
                if args.rate > 0 and not args.dry_run:
                    time.sleep(args.rate)
        except KeyboardInterrupt:
            log("收到中断，flush 当前进度后退出（可重跑续跑）")
        finally:
            flush_evidence(args.summary_csv, args.manifest, all_rows, manifest)
            log(f"preflight 结束：本次处理 {processed}/{total}；"
                f"累计完成 {len(manifest.get('done', {}))}")
    finally:
        db_ro.close()


def run_verify(args):
    """重读 manifest/CSV 校验一致性（不下载）。"""
    manifest = load_manifest(args.manifest)
    done = manifest.get("done", {})
    rows = list(read_summary_rows(args.summary_csv)) if args.summary_csv.exists() else []
    log(f"manifest 完成 code 数={len(done)}；summary csv 行数={len(rows)}")
    # 一致性：csv 中每只 code 的最终判定应与 manifest 一致
    csv_final = {}
    for r in rows:
        csv_final.setdefault((r["code"], r.get("asset_type", "STOCK")), r["status"])
    mismatch = 0
    for key, v in done.items():
        code, at = key.split("|", 1)
        # csv 段级状态不直接影响证券级 final；此处仅校验 code 覆盖
        if (code, at) not in csv_final and v["final"] != "NO_DATA":
            mismatch += 1
    log(f"verify 完成：code 覆盖不一致 {mismatch}；manifest final 分布：")
    dist = {}
    for v in done.values():
        dist[v["final"]] = dist.get(v["final"], 0) + 1
    for k in sorted(dist):
        log(f"  {k}: {dist[k]}")


def run_update_evidence(args, today_yyyymmdd):
    """从 manifest 重算报告 + 准入名单（不下载）。"""
    manifest = load_manifest(args.manifest)
    done = manifest.get("done", {})
    if not done:
        log("[WARN] manifest 为空，先跑 preflight")
        return
    # 统计
    dist = {}
    by_asset = {"STOCK": {}, "ETF": {}}
    for key, v in done.items():
        code, at = key.split("|", 1)
        dist[v["final"]] = dist.get(v["final"], 0) + 1
        by_asset.setdefault(at, {}).setdefault(v["final"], 0)
        by_asset[at][v["final"]] = by_asset[at].get(v["final"], 0) + 1

    admissible = [k.split("|", 1)[0] for k, v in done.items()
                  if v["final"] == "ADMISSIBLE"]
    admissible_by_asset = {"STOCK": [], "ETF": []}
    for k, v in done.items():
        if v["final"] == "ADMISSIBLE":
            admissible_by_asset[v["asset_type"]].append(k.split("|", 1)[0])

    total = len(done)
    adm_rate = (len(admissible) / total * 100) if total else 0.0
    log(f"全市场预检：{total} 只；ADMISSIBLE={len(admissible)} "
        f"({adm_rate:.2f}%)；分布={dist}")

    # 准入名单
    admissible_path = args.admissible_out
    write_json_atomic(admissible_path, {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "preflight_raw_fullmarket.py",
        "db": str(args.db),
        "total_checked": total,
        "total_admissible": len(admissible),
        "admissible_rate_pct": round(adm_rate, 4),
        "by_asset": {a: sorted(codes) for a, codes in admissible_by_asset.items()},
        "codes": sorted(admissible),
    })
    log(f"准入名单已写：{admissible_path}")

    # 报告
    write_report(args.report, done, dist, by_asset, admissible, adm_rate,
                 today_yyyymmdd, args)
    log(f"报告已写：{args.report}")


def write_report(report_path, done, dist, by_asset, admissible, adm_rate,
                 today, args):
    total = len(done)
    lines = []
    lines.append(f"# 全市场 raw 准入预检报告（{today}）\n")
    lines.append(f"> 生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}  ")
    lines.append(f"> 数据源：fresh xtquant none raw vs 库内 raw（逐 bar 对齐）  ")
    lines.append(f"> 只读预检，未写正式库；`qfq_orchestrator.enabled=false`\n")
    lines.append("## 1. 全市场汇总\n")
    lines.append(f"- 检查证券总数：**{total}**")
    lines.append(f"- **ADMISSIBLE（满足 rebase 准入）：{len(admissible)} "
                 f"（{adm_rate:.2f}%）**\n")
    lines.append("| 最终状态 | 含义 | 数量 |")
    lines.append("|---|---|---|")
    meaning = {
        "ADMISSIBLE": "daily+minute 完全覆盖且 OHLC 逐 bar 对齐，rebase 可用",
        "BLOCK": "价格不一致 / 库内存在 fresh 缺失的 bar（rebase 不处理）",
        "MIGRATE": "fresh 含库内缺失 bar（库内历史缺口，需先迁移 raw）",
        "DOWNLOAD_FAILED": "fresh 下载失败（需重跑，未定论）",
        "NO_DATA": "库内无任何 raw 段数据（不参与 rebase）",
    }
    for k in ("ADMISSIBLE", "BLOCK", "MIGRATE", "DOWNLOAD_FAILED", "NO_DATA", "SKIPPED"):
        lines.append(f"| {k} | {meaning.get(k,'')} | {dist.get(k,0)} |")
    lines.append("")

    lines.append("## 2. 按资产类型统计\n")
    lines.append("| 资产类型 | 总数 | ADMISSIBLE | BLOCK | MIGRATE | "
                 "DOWNLOAD_FAILED | NO_DATA |")
    lines.append("|---|---|---|---|---|---|---|")
    for at in ("STOCK", "ETF"):
        d = by_asset.get(at, {})
        n = sum(d.values())
        lines.append(f"| {at} | {n} | {d.get('ADMISSIBLE',0)} | {d.get('BLOCK',0)} | "
                     f"{d.get('MIGRATE',0)} | {d.get('DOWNLOAD_FAILED',0)} | "
                     f"{d.get('NO_DATA',0)} |")
    lines.append("")

    lines.append("## 3. 不一致证券清单（BLOCK / MIGRATE / DOWNLOAD_FAILED）\n")
    lines.append("| code | 资产 | 最终状态 | 原因 | 段状态 |")
    lines.append("|---|---|---|---|---|")
    for key, v in sorted(done.items()):
        if v["final"] in ("BLOCK", "MIGRATE", "DOWNLOAD_FAILED"):
            code, at = key.split("|", 1)
            seg = ",".join(f"{p}:{s}" for p, s in v.get("segments", {}).items())
            lines.append(f"| {code} | {at} | {v['final']} | {v.get('reason','')} | {seg} |")
    lines.append("")

    lines.append("## 4. 结论\n")
    if adm_rate >= 90:
        lines.append(f"全市场 **{adm_rate:.2f}%** 证券满足 rebase 准入条件，"
                     f"可进入生产启用（按 runbook 部署门控 + 三步渐进）。")
    else:
        lines.append(f"⚠️ 全市场 ADMISSIBLE 率 **{adm_rate:.2f}% < 90%**。")
        lines.append("主要阻断原因（按数量）：")
        for k in ("BLOCK", "MIGRATE", "DOWNLOAD_FAILED"):
            if dist.get(k, 0):
                lines.append(f"- {k}: {dist[k]} 只 —— {meaning.get(k,'')}")
        lines.append("建议：BLOCK 证券编排器跳过（不处理）；MIGRATE 证券需先迁移 raw；"
                     "DOWNLOAD_FAILED 重跑预检补齐。")
    lines.append("")
    lines.append("## 5. 准入名单\n")
    lines.append(f"- 文件：`{os.path.basename(str(args.admissible_out))}`")
    lines.append(f"- ADMISSIBLE 证券数：{len(admissible)}")
    lines.append("- 生产启用时编排器只处理名单内证券（名单外直接跳过）。")
    lines.append("")
    lines.append("> 证据：admission_summary.csv（逐只逐段）、preflight_manifest.json"
                 "（断点续跑进度）、mismatch_details.csv（仅不一致段明细）。")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# manifest / summary 读取
# --------------------------------------------------------------------------- #
def load_manifest(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return {"done": {}}
    return {"done": {}}


def read_summary_rows(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield r


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="全市场 raw 准入预检（只读）")
    ap.add_argument("--db", default="quantstudio.db", help="正式库路径")
    ap.add_argument("--mode", choices=["preflight", "verify", "update-evidence"],
                    default="preflight")
    ap.add_argument("--codes-file", default=None,
                    help="指定 code 子集（每行 code[,ASSET_TYPE]）")
    ap.add_argument("--asset", default=None, choices=["STOCK", "ETF"])
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（冒烟）")
    ap.add_argument("--start", default=DS_DEFAULT, help="下载起始 yyyymmdd")
    ap.add_argument("--end", default=None, help="下载结束 yyyymmdd（默认今天）")
    ap.add_argument("--rate", type=float, default=0.05,
                    help="每证券限速 sleep 秒（默认 0.05）")
    ap.add_argument("--batch-size", type=int, default=50,
                    help="每 N 只 flush 证据一次（断点续跑粒度）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅统计库内覆盖与映射，不下载 fresh")
    ap.add_argument("--out-dir", default=None, help="证据/报告输出目录")
    ap.add_argument("--admissible-out", default=None,
                    help="准入名单 json 输出路径（默认 config/qfq_rebase_admissible_securities.json）")
    args = ap.parse_args()

    today = datetime.datetime.now().strftime("%Y%m%d")
    out_dir = Path(args.out_dir or f"docs/evidence/qfq_raw_admission_fullmarket_{today}")
    out_dir.mkdir(parents=True, exist_ok=True)
    args.summary_csv = out_dir / "admission_summary.csv"
    args.manifest = str(out_dir / "preflight_manifest.json")
    args.report = Path(f"docs/qfq-raw-admission-fullmarket-{today}.md")
    args.admissible_out = Path(args.admissible_out
                               or "config/qfq_rebase_admissible_securities.json")

    db_path = args.db
    log(f"mode={args.mode} db={db_path} out-dir={out_dir}")

    if args.mode == "preflight":
        run_preflight(args, db_path, today)
        # preflight 后自动重算报告 + 名单（不下载）；dry-run 不重算
        if not args.dry_run:
            run_update_evidence(args, today)
    elif args.mode == "verify":
        run_verify(args)
    elif args.mode == "update-evidence":
        run_update_evidence(args, today)


if __name__ == "__main__":
    main()
