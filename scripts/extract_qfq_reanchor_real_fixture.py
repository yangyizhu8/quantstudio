"""从只读证据源提取 600875/600039/002864 真实行，固化为 batch2 回归 fixture。

证据源（只读）：D:/miniQMT策略实盘/qs_iso_a/data/quantstudio.db
  —— QuantStudio 正式库的隔离快照副本（2026-07-26 03:02 落盘）。
  本脚本以 read_only=True 打开，禁止写证据源；输出仅落
  tests/fixtures/qfq_real_reanchor/（parquet + metadata.json 含 sha256）。

提取窗口：
  daily   : 2026-07-13 00:00 +08 <= time <= 2026-07-24 00:00 +08（10 个交易日；
            2026-07-13 为 predecessor 行 —— 第三轮对抗审核要求：front-chain 首日
            必须能对真实上一交易日行做 ext 校验，不得依赖 chain_start 豁免）
  minutes : freq='1min'，2026-07-14 00:00 <= time < 2026-07-25 00:00（9 日 × 241 bar）

用法：python scripts/extract_qfq_reanchor_real_fixture.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import duckdb

SRC = Path("D:/miniQMT策略实盘/qs_iso_a/data/quantstudio.db")
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "qfq_real_reanchor"
CODES = ("600875", "600039", "002864")

DAILY_LO = 1783872000000   # 2026-07-13 00:00 +08（predecessor 行起）
DAILY_HI = 1784822400000   # 2026-07-24 00:00 +08
MIN_LO = 1783958400000
MIN_HI = 1784908800000     # 2026-07-25 00:00 +08（左闭右开）


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    assert SRC.exists(), f"证据源不存在: {SRC}"
    OUT.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(SRC), read_only=True)

    meta = {
        "source_path": str(SRC),
        "source_size_bytes": SRC.stat().st_size,
        "source_mtime": datetime.fromtimestamp(SRC.stat().st_mtime).isoformat(),
        "extracted_at": datetime.now().isoformat(),
        "window": {
            "daily": {"lo_ms": DAILY_LO, "hi_ms": DAILY_HI, "inclusive": True},
            "minutes": {"lo_ms": MIN_LO, "hi_ms": MIN_HI, "inclusive": "[lo, hi)",
                        "freq": "1min"},
        },
        "files": {},
        "sanity": {},
    }

    for code in CODES:
        dq = (f"SELECT * FROM stock_daily WHERE code = '{code}' "
              f"AND time BETWEEN {DAILY_LO} AND {DAILY_HI} ORDER BY time")
        mq = (f"SELECT * FROM stock_minutes WHERE code = '{code}' AND freq = '1min' "
              f"AND time >= {MIN_LO} AND time < {MIN_HI} ORDER BY time")
        dp = OUT / f"{code}_daily.parquet"
        mp = OUT / f"{code}_minutes.parquet"
        conn.execute(f"COPY ({dq}) TO '{dp.as_posix()}' (FORMAT PARQUET)")
        conn.execute(f"COPY ({mq}) TO '{mp.as_posix()}' (FORMAT PARQUET)")
        n_d = conn.execute(f"SELECT COUNT(*) FROM ({dq})").fetchone()[0]
        n_m = conn.execute(f"SELECT COUNT(*) FROM ({mq})").fetchone()[0]
        meta["files"][dp.name] = {"sql": dq, "rows": int(n_d), "sha256": _sha256(dp)}
        meta["files"][mp.name] = {"sql": mq, "rows": int(n_m), "sha256": _sha256(mp)}

        # sanity 指标（写入 metadata 供审计）：
        san = {}
        san["daily_scale_by_day"] = {
            str(int(r[0])): (None if r[1] is None or r[2] in (None, 0)
                             else float(r[1]) / float(r[2]))
            for r in conn.execute(
                f"SELECT time, close_front, close FROM ({dq}) ORDER BY time").fetchall()}
        san["minute_scale_minmax"] = [
            float(x) for x in conn.execute(
                f"SELECT MIN(close_front/close), MAX(close_front/close) "
                f"FROM ({mq}) WHERE close IS NOT NULL AND close > 0").fetchone()]
        san["daily_preclose_close"] = {
            str(int(r[0])): {"close": float(r[1]), "preClose": float(r[2])}
            for r in conn.execute(
                f"SELECT time, close, preClose FROM ({dq}) ORDER BY time").fetchall()}
        meta["sanity"][code] = san
        print(f"{code}: daily={n_d} rows, minutes={n_m} rows, "
              f"minute_scale={san['minute_scale_minmax']}")

    conn.close()
    (OUT / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"metadata.json 写入 {OUT}")


if __name__ == "__main__":
    main()
