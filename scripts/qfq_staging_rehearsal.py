"""QFQ 常驻编排器 staging 演练（首轮：核心守恒闭环）。

在带 marker 的安全 staging 副本上验证 reconcile-once 一轮协调周期的数据守恒性。
证据固化到 output/qfq_staging_rehearsal_20260729/。全程不写正式库。

设计见 docs/superpowers/specs/2026-07-29-qfq-staging-rehearsal-design.md。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import duckdb

logger = logging.getLogger("qfq_staging_rehearsal")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")

BJ_TZ = timezone(timedelta(hours=8))

# —— 路径常量 ——
ROOT = Path(__file__).resolve().parent.parent
FORMAL_DB = ROOT / "data" / "quantstudio.db"
FORMAL_AUX = ROOT / "data" / "qfq_aux.db"
STAMP = "20260729"
STAGING_DIR = ROOT / "data" / f"staging_qfq_rehearsal_{STAMP}"
STAGING_DB = STAGING_DIR / "quantstudio.db"
STAGING_AUX = STAGING_DIR / "qfq_aux.db"
STAGING_MARKER = STAGING_DIR / ".quantstudio_staging.json"
OUTPUT_DIR = ROOT / "output" / f"qfq_staging_rehearsal_{STAMP}"

# —— 候选证券 ——
STOCK_CODES = ["000012", "000025", "000060", "600000"]
ETF_CODES = ["510300", "159919"]
ALL_CODES = STOCK_CODES + ETF_CODES
PRICE_TABLES = ["stock_daily", "etf_daily", "stock_minutes", "etf_minutes"]
# 每张表对应的资产类型与代码集（决定抽哪些码）
TABLE_CODES = {
    "stock_daily": STOCK_CODES, "stock_minutes": STOCK_CODES,
    "etf_daily": ETF_CODES, "etf_minutes": ETF_CODES,
}
# raw + back + front 列（守恒比对用）
PRICE_COLS = ["code", "time", "open", "high", "low", "close",
              "open_front", "high_front", "low_front", "close_front",
              "open_back", "high_back", "low_back", "close_back"]


def _now_iso() -> str:
    return datetime.now(BJ_TZ).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    """大文件分块 SHA256（正式库 13.8GB 不可一次读入）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight_checks() -> dict:
    """安全前置：daemon 无 lock + 正式库存在。返回环境信息。"""
    info = {"started_at": _now_iso(), "formal_db": str(FORMAL_DB)}
    if not FORMAL_DB.exists():
        sys.exit(f"❌ 正式库不存在: {FORMAL_DB}")
    if not FORMAL_AUX.exists():
        sys.exit(f"❌ 正式 aux 不存在: {FORMAL_AUX}")
    lock = ROOT / "data" / "collector_run.lock"
    if lock.exists():
        sys.exit(f"❌ daemon lock 存在（{lock}），拒绝演练以免与采集冲突")
    info["formal_db_sha256"] = _sha256_file(FORMAL_DB)
    info["formal_db_size_mb"] = round(FORMAL_DB.stat().st_size / 1e6, 1)
    logger.info(f"前置检查通过：正式库 SHA={info['formal_db_sha256'][:16]}... "
                f"({info['formal_db_size_mb']} MB)")
    return info


def _copy_aux_full() -> None:
    """全量复制 qfq_aux.db（810MB，因子/observation 主战场）。"""
    size_mb = FORMAL_AUX.stat().st_size // (1 << 20)
    logger.info(f"复制 aux 全量 → {STAGING_AUX}（~{size_mb} MB）")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FORMAL_AUX, STAGING_AUX)


def _extract_small_sample_main() -> None:
    """从正式库只读抽取小样本到 staging DuckDB。"""
    logger.info(f"抽取小样本主库 → {STAGING_DB}")
    # 先在 staging 库建 QFQ 编排器自己的表（qfq_trigger_queue 等，init_duckdb_schema）
    from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema
    with duckdb.connect(str(STAGING_DB)) as w:
        init_duckdb_schema(w)
        w.commit()
    # 从正式库只读 SELECT 抽取候选证券的行情 + 元数据 + 分红 + 水位
    # 价格表/元数据表/分红表由 writers 建，不在 init_duckdb_schema 内——
    # 用 CREATE TABLE AS SELECT 让 staging 库自动按正式库 schema 建表。
    with duckdb.connect(str(FORMAL_DB), read_only=True) as r, \
         duckdb.connect(str(STAGING_DB)) as w:
        # 1) 四张价格表（候选证券全量行）——CREATE OR REPLACE 按 formal schema 建表
        for tbl, codes in TABLE_CODES.items():
            ph = ",".join([f"'{c}'" for c in codes])
            df = r.execute(f"SELECT * FROM {tbl} WHERE code IN ({ph})").fetchdf()
            w.execute(f"DROP TABLE IF EXISTS {tbl}")
            if len(df):
                w.execute(f"CREATE TABLE {tbl} AS SELECT * FROM df")
            else:
                # 无数据时按 formal schema 建空表
                empty = r.execute(f"SELECT * FROM {tbl} WHERE 1=0").fetchdf()
                w.execute(f"CREATE TABLE {tbl} AS SELECT * FROM empty")
            logger.info(f"  {tbl}: 抽取 {len(df)} 行（{len(codes)} 码）")
        # 2) stock_dividend（候选证券 + 全表 schema 保留）
        ph = ",".join([f"'{c}'" for c in STOCK_CODES])
        df = r.execute(f"SELECT * FROM stock_dividend WHERE code IN ({ph})").fetchdf()
        w.execute("DROP TABLE IF EXISTS stock_dividend")
        w.execute("CREATE TABLE stock_dividend AS SELECT * FROM df")
        logger.info(f"  stock_dividend: 抽取 {len(df)} 行")
        # 3) 元数据表（供 resolve_ts_codes）
        for meta_tbl in ("stock_basic", "etf_basic"):
            try:
                df = r.execute(f"SELECT * FROM {meta_tbl}").fetchdf()
                w.execute(f"DROP TABLE IF EXISTS {meta_tbl}")
                w.execute(f"CREATE TABLE {meta_tbl} AS SELECT * FROM df")
                logger.info(f"  {meta_tbl}: 抽取 {len(df)} 行")
            except Exception as e:
                logger.warning(f"  {meta_tbl} 抽取失败（跳过）: {e}")
        # 4) source_watermark（水位基线）
        try:
            df = r.execute("SELECT * FROM source_watermark").fetchdf()
            w.execute("DROP TABLE IF EXISTS source_watermark")
            w.execute("CREATE TABLE source_watermark AS SELECT * FROM df")
            logger.info(f"  source_watermark: 抽取 {len(df)} 行")
        except Exception as e:
            logger.warning(f"  source_watermark 抽取失败（跳过）: {e}")
        w.commit()


def build_staging_env() -> dict:
    """建 staging 环境：marker + aux 全量 + 主库小样本。返回 manifest。"""
    if STAGING_DIR.exists():
        logger.warning(f"staging 目录已存在，先清理: {STAGING_DIR}")
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    (STAGING_DIR / "config").mkdir()
    (STAGING_DIR / "logs").mkdir()

    _copy_aux_full()
    _extract_small_sample_main()

    manifest = {
        "format_version": "1.0",
        "staging_root": str(STAGING_DIR),
        "source_db": str(FORMAL_DB),
        "source_aux": str(FORMAL_AUX),
        "created_at": _now_iso(),
        "tool": "qfq_staging_rehearsal",
        "candidates": ALL_CODES,
    }
    STAGING_MARKER.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    logger.info(f"staging 环境就绪：marker={STAGING_MARKER}")
    return manifest


def _table_content_sha(conn, table: str, codes: list):
    """对指定表指定码的核心列内容做 SHA（含行顺序）。返回 (sha, rows)。"""
    ph = ",".join([f"'{c}'" for c in codes])
    df = conn.execute(
        f"SELECT {', '.join(PRICE_COLS)} FROM {table} "
        f"WHERE code IN ({ph}) ORDER BY code, time"
    ).fetchdf()
    sha = hashlib.sha256(df.to_csv(index=False).encode("utf-8")).hexdigest()
    return sha, len(df)


def snapshot_baseline() -> dict:
    """记录演练前基线：每张表的 SHA + 行数 + CSV。"""
    logger.info("记录基线快照（演练前）")
    baseline = {}
    with duckdb.connect(str(STAGING_DB), read_only=True) as conn:
        for tbl, codes in TABLE_CODES.items():
            sha, n = _table_content_sha(conn, tbl, codes)
            baseline[tbl] = {"sha256": sha, "rows": n}
            # 导出 CSV 供人工核对
            ph = ",".join([f"'{c}'" for c in codes])
            df = conn.execute(
                f"SELECT {', '.join(PRICE_COLS)} FROM {tbl} "
                f"WHERE code IN ({ph}) ORDER BY code, time"
            ).fetchdf()
            df.to_csv(OUTPUT_DIR / f"baseline_{tbl}.csv", index=False)
    (OUTPUT_DIR / "baseline_sha.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"基线 SHA 已记录: "
                f"{json.dumps({k: v['sha256'][:12] for k, v in baseline.items()})}")
    return baseline


def split_etf_factors() -> dict:
    """ETF 因子分库：dry_run 预检 + 实跑，验证行数守恒。"""
    from quantstudio.pipeline.qfq_maintenance import (
        migrate_split_etf_factors, get_etf_universe)
    from quantstudio.pipeline.qfq_reanchor_schema import init_sqlite_schema
    logger.info("ETF 因子分库迁移")
    # 复制来的正式 aux 可能缺 fund_adj 表（旧版），先 init_sqlite_schema 补建
    with sqlite3.connect(str(STAGING_AUX)) as a:
        init_sqlite_schema(a)
        a.commit()
    etf_universe = get_etf_universe(STAGING_DB)
    trace = {"etf_universe_size": len(etf_universe),
             "etf_universe_sample": sorted(etf_universe)[:5]}
    # dry_run 预检
    moved_dry, sample_dry = migrate_split_etf_factors(
        STAGING_AUX, etf_universe=etf_universe, dry_run=True)
    trace["dry_run"] = {"moved_rows": moved_dry, "sample": sample_dry}
    logger.info(f"  dry_run: 预计迁移 {moved_dry} 行 ETF 因子")
    # 迁移前行数
    with sqlite3.connect(str(STAGING_AUX)) as a:
        adj_before = a.execute("SELECT COUNT(*) FROM adj_factor").fetchone()[0]
        fund_before = a.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='fund_adj'"
        ).fetchone()[0]
    trace["before"] = {"adj_factor_rows": adj_before, "fund_adj_exists": bool(fund_before)}
    # 实跑
    moved_real, sample_real = migrate_split_etf_factors(
        STAGING_AUX, etf_universe=etf_universe, dry_run=False)
    trace["real"] = {"moved_rows": moved_real}
    # 迁移后行数 + 守恒校验
    with sqlite3.connect(str(STAGING_AUX)) as a:
        adj_after = a.execute("SELECT COUNT(*) FROM adj_factor").fetchone()[0]
        fund_after = a.execute("SELECT COUNT(*) FROM fund_adj").fetchone()[0]
    trace["after"] = {"adj_factor_rows": adj_after, "fund_adj_rows": fund_after}
    # 守恒：adj_before == adj_after + moved_real
    trace["conservation_ok"] = (adj_before == adj_after + moved_real)
    logger.info(f"  迁移完成：adj {adj_before}→{adj_after}，fund_adj→{fund_after}，"
                f"守恒={trace['conservation_ok']}")
    (OUTPUT_DIR / "factor_migration_trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return trace


def inject_front_pollution() -> dict:
    """对 600000 若干行 front 注入污染值，记录真实值（演练后应被修正回来）。"""
    logger.info("注入 front 污染样本（600000 最近 5 行 close_front）")
    pollution = {"code": "600000", "polluted_rows": 0, "records": []}
    with duckdb.connect(str(STAGING_DB)) as conn:
        # 取最近 5 行的 time + 真实 close_front/close
        rows = conn.execute(
            "SELECT time, close, close_front FROM stock_daily "
            "WHERE code='600000' ORDER BY time DESC LIMIT 5"
        ).fetchall()
        for t, close, close_front in rows:
            polluted = float(close) + 1.0  # 污染值 = close + 1
            conn.execute(
                "UPDATE stock_daily SET close_front=? "
                "WHERE code='600000' AND time=?", [polluted, t])
            pollution["records"].append({
                "time": t, "true_close_front": float(close_front),
                "true_close": float(close), "polluted_value": polluted})
            pollution["polluted_rows"] += 1
        conn.commit()
    (OUTPUT_DIR / "front_pollution_injected.json").write_text(
        json.dumps(pollution, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"  注入 {pollution['polluted_rows']} 行污染（close_front=close+1）")
    return pollution


def drive_reconcile() -> dict:
    """驱动 CLI reconcile-once：dry-run 先行，再 --execute。"""
    logger.info("驱动 CLI reconcile-once")
    # 全局参数（--db/--aux-db/--override/--json/--execute）必须在子命令之前
    # require_bootstrap=false：演练目的是验证重锚守恒，跳过 bootstrap fail-closed
    # （未 bootstrap 时编排器会拒绝处理任何 trigger）
    base = [sys.executable, "-m", "quantstudio.pipeline.qfq_orchestrator_cli",
            "--db", str(STAGING_DB), "--aux-db", str(STAGING_AUX),
            "--override", "enabled=true",
            "--override", "require_bootstrap=false", "--json"]
    result = {"dry_run": {}, "execute": {}}
    # dry-run（不带 --execute）
    logger.info("  [1/2] dry-run")
    p = subprocess.run(base + ["reconcile-once"],
                       capture_output=True, text=True, cwd=str(ROOT))
    result["dry_run"] = {"returncode": p.returncode,
                         "stdout_tail": p.stdout[-500:],
                         "stderr_tail": p.stderr[-500:]}
    logger.info(f"    dry-run rc={p.returncode}")
    # execute（--execute 插到子命令前）
    logger.info("  [2/2] execute")
    p = subprocess.run(base + ["--execute", "reconcile-once"],
                       capture_output=True, text=True, cwd=str(ROOT))
    result["execute"] = {"returncode": p.returncode,
                         "stdout": p.stdout,
                         "stderr_tail": p.stderr[-1500:]}
    # 尝试解析 summary
    try:
        summary = json.loads(p.stdout)
        result["summary"] = summary
        logger.info(f"    summary status={summary.get('status')} "
                    f"committed={summary.get('committed')} "
                    f"held={summary.get('watermarks_held')}")
    except Exception:
        result["summary"] = None
        logger.warning(f"    summary 解析失败，rc={p.returncode}")
    (OUTPUT_DIR / "reconcile_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    logger.info("=" * 60)
    logger.info("QFQ staging 演练（首轮：核心守恒闭环）")
    logger.info("=" * 60)
    env = preflight_checks()
    manifest = build_staging_env()
    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "staging_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "environment.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline = snapshot_baseline()
    migration = split_etf_factors()
    pollution = inject_front_pollution()
    reconcile = drive_reconcile()
    return 0


if __name__ == "__main__":
    sys.exit(main())
