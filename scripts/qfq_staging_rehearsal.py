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


def main() -> int:
    logger.info("=" * 60)
    logger.info("QFQ staging 演练（首轮：核心守恒闭环）")
    logger.info("=" * 60)
    env = preflight_checks()
    print(json.dumps(env, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
