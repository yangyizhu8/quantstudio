"""
QFQ 维护模块 [E-3] — adj_factor 增量拉取 + 批次边界复权跳变检测

强制原则（基线 §1.2 第7条 + [E-3]）：
- 禁用跨批次 QFQ close.pct_change() 计算收益率（批次边界复权跳变会污染）
- 真实涨跌幅一律用官方 pct_chg 字段
- 本模块负责：① 增量拉取 adj_factor 入库；② 检测批次边界跳变（pct_chg vs close变化率偏差）
- 检测到跳变 → 写入 qfq_audit 表 + Quarantine 告警

两道检测：
1. 批次边界跳变：隔夜 |pct_chg| > 20%（复权跳变特征）
2. pct_chg vs close 变化率偏差：|pct_chg - (close/pre_close-1)*100| > 1%（pct_chg 用错或 pre_close 污染）
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
from quantstudio._paths import db_path
ROOT = Path(__file__).resolve().parent.parent.parent


class QFQMaintenance:
    """前复权维护 + 批次边界跳变检测 [E-3]

    使用：
        qfq = QFQMaintenance(ROOT/"data"/"quantstudio.db")
        qfq.fetch_adj_factor(adapter, codes=["600000.SH"], start="2020-01-01")
        report = qfq.detect_jumps()
    """

    def __init__(self, db_path: str | Path, jump_threshold_pct: float = 20.0,
                 pctchg_diff_threshold: float = 1.0):
        # QFQ 辅助表（adj_factor / qfq_jump_audit）存独立 SQLite，
        # 与 DuckDB 主库分离（避免与 DuckDB 的 quantstudio.db 冲突）
        self.db_path = Path(db_path)
        if self.db_path.name == "quantstudio.db":
            self.db_path = self.db_path.parent / "qfq_aux.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jump_threshold_pct = jump_threshold_pct
        self.pctchg_diff_threshold = pctchg_diff_threshold
        self._init_tables()

    def _init_tables(self):
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            # WAL 模式 + busy_timeout（减少多线程并发写锁冲突）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            # adj_factor 存储表（统一口径：code 裸码，time 毫秒时间戳）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS adj_factor (
                    code TEXT, time INTEGER, adj_factor REAL,
                    PRIMARY KEY(code, time)
                )""")
            # QFQ 跳变审计
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qfq_jump_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT, time INTEGER,
                    pctChg REAL, close_change_pct REAL, diff REAL,
                    jump_type TEXT,  -- 'qfq_batch_boundary' / 'pctchg_mismatch'
                    detected_at TEXT
                )""")
            # 复权因子快照（全量拉取时冻结，增量复用，避免基准日期漂移）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS adj_factor_snapshot (
                    code TEXT PRIMARY KEY,
                    adj_latest REAL,
                    adj_earliest REAL,
                    snapshot_date TEXT
                )""")
            conn.commit()

    # ---------------- adj_factor 增量拉取 ----------------
    def fetch_adj_factor(self, adapter, codes: List[str], start: str,
                         end: Optional[str] = None, is_etf: bool = False):
        """从 tushare 拉取复权因子增量入库。adapter 须为 TushareAdapter。
        股票用 adj_factor API，ETF/基金用 fund_adj API（字段同名 adj_factor）。
        入库时 code 转裸码、trade_date 转毫秒时间戳（统一口径）。"""
        end = end or datetime.now().strftime("%Y-%m-%d")
        api_name = "fund_adj" if is_etf else "adj_factor"
        api = getattr(adapter._client, api_name)
        from .aligner import normalize_code, to_ms_timestamp
        dfs = []
        for code in codes:
            try:
                adapter.rate_limiter.acquire()
                df = api(ts_code=code, start_date=start.replace("-", ""),
                         end_date=end.replace("-", ""))
                dfs.append(df)
            except Exception as e:
                logger.warning(f"[QFQ] {code} {api_name} failed: {e}")
        if not dfs:
            return 0
        df = pd.concat(dfs, ignore_index=True)
        records = []
        for _, r in df.iterrows():
            raw_code = normalize_code(str(r["ts_code"]), "tushare_to_raw")
            ms = to_ms_timestamp(str(r["trade_date"]))
            if raw_code and ms:
                records.append((raw_code, ms, float(r["adj_factor"])))
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executemany(
                "INSERT OR REPLACE INTO adj_factor VALUES (?,?,?)", records)
            conn.commit()
        logger.debug(f"[QFQ] fetched {len(records)} {api_name} rows ({len(codes)} codes)")
        return len(records)

    def save_snapshot(self, adj_df: pd.DataFrame, snapshot_date: str):
        """全量拉取后保存复权因子快照（adj_latest/adj_earliest per code）。
        adj_df 含 code/time/adj_factor 列（全量拉取的完整 adj_factor 时间序列）。
        snapshot_date 是快照基准日期（通常=end_date）。
        增量模式复用此快照，避免基准日期漂移。"""
        if adj_df is None or len(adj_df) == 0:
            return
        # 锚点按时间首尾取值；因子数值并不保证单调，不能用 min/max 代替日期。
        ordered = adj_df.sort_values("time")
        earliest = ordered.groupby("code").first().reset_index()[["code", "adj_factor"]]
        earliest.columns = ["code", "adj_earliest"]
        latest = ordered.groupby("code").last().reset_index()[["code", "adj_factor"]]
        latest.columns = ["code", "adj_latest"]
        snapshot = earliest.merge(latest, on="code", how="outer")
        snapshot["snapshot_date"] = snapshot_date
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("DELETE FROM adj_factor_snapshot")  # 清旧快照
            conn.executemany(
                "INSERT OR REPLACE INTO adj_factor_snapshot VALUES (?,?,?,?)",
                snapshot[["code", "adj_latest", "adj_earliest", "snapshot_date"]].values.tolist())
            conn.commit()
        logger.info(f"[QFQ] 快照已保存: {len(snapshot)} 只股票, 基准日={snapshot_date}")

    def load_snapshot(self) -> tuple:
        """读取快照，返回 (adj_latest_map, adj_earliest_map) 两个 dict。
        全量拉取前调用，如果无快照返回 (None, None)。"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            df = pd.read_sql_query("SELECT code, adj_latest, adj_earliest FROM adj_factor_snapshot", conn)
        if len(df) == 0:
            return None, None
        latest_map = dict(zip(df["code"], df["adj_latest"]))
        earliest_map = dict(zip(df["code"], df["adj_earliest"]))
        return latest_map, earliest_map

    def get_adj_factor(self, code: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """取某 code 的 adj_factor（start_ms/end_ms 为毫秒时间戳）"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            df = pd.read_sql_query(
                "SELECT * FROM adj_factor WHERE code=? AND time>=? AND time<=? "
                "ORDER BY time", conn, params=[code, start_ms, end_ms])
        return df

    # ---------------- 批次边界跳变检测 [E-3 核心] ----------------
    def detect_jumps(self, duckdb_path: Optional[str | Path] = None) -> Dict:
        """检测两类跳变：
        1. qfq_batch_boundary: 隔夜 |pctChg| > jump_threshold_pct（默认20%，复权跳变特征）
        2. pctchg_mismatch: |pctChg - (close/preClose-1)*100| > diff_threshold（pctChg 用错）

        Args:
            duckdb_path: stock_daily 所在的 DuckDB 路径（默认 data/quantstudio.db）

        Returns:
            {qfq_jumps: n, pctchg_mismatches: n, samples: [...]}
        """
        duckdb_path = duckdb_path or (db_path())
        try:
            import duckdb
        except ImportError as e:
            raise ImportError("需安装 duckdb") from e

        report = {"qfq_jumps": 0, "pctchg_mismatches": 0, "samples": [],
                  "checked_at": datetime.now().isoformat()}

        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            try:
                df = conn.execute(
                    "SELECT code, time, open, close, pctChg FROM stock_daily "
                    "ORDER BY code, time").fetchdf()
            except Exception:
                logger.warning("[QFQ] stock_daily 表不存在或为空，跳过检测")
                return report

        if len(df) == 0:
            return report

        # time 是毫秒时间戳，转可读日期用于审计展示
        df["date_str"] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert(
            "Asia/Shanghai").dt.strftime("%Y-%m-%d")
        now = datetime.now()
        samples = []

        # 检测 1: 隔夜 |pctChg| > 阈值（复权批次边界特征）
        for code, grp in df.groupby("code"):
            grp = grp.sort_values("time")
            big = grp[grp["pctChg"].abs() > self.jump_threshold_pct]
            for _, r in big.iterrows():
                samples.append({
                    "code": code, "time": int(r["time"]), "date": r["date_str"],
                    "pctChg": round(float(r["pctChg"]), 4),
                    "jump_type": "qfq_batch_boundary"})
                with sqlite3.connect(self.db_path, timeout=30) as conn:
                    conn.execute(
                        "INSERT INTO qfq_jump_audit (code,time,pctChg,jump_type,detected_at) "
                        "VALUES (?,?,?,?,?)",
                        (code, int(r["time"]), float(r["pctChg"]),
                         "qfq_batch_boundary", now.isoformat()))
                    conn.commit()
            report["qfq_jumps"] += len(big)

        # 检测 2: pctChg vs close 变化率偏差（需 preClose）
        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            try:
                df2 = conn.execute(
                    "SELECT code, time, close, preClose, pctChg FROM stock_daily "
                    "WHERE preClose IS NOT NULL AND preClose > 0 "
                    "ORDER BY code, time").fetchdf()
            except Exception:
                df2 = pd.DataFrame()

        if len(df2) > 0:
            df2["date_str"] = pd.to_datetime(df2["time"], unit="ms", utc=True).dt.tz_convert(
                "Asia/Shanghai").dt.strftime("%Y-%m-%d")
            df2["close_chg"] = (df2["close"] / df2["preClose"] - 1) * 100
            df2["diff"] = (df2["pctChg"] - df2["close_chg"]).abs()
            bad = df2[df2["diff"] > self.pctchg_diff_threshold]
            for _, r in bad.iterrows():
                samples.append({
                    "code": r["code"], "time": int(r["time"]), "date": r["date_str"],
                    "pctChg": round(float(r["pctChg"]), 4),
                    "close_change_pct": round(float(r["close_chg"]), 4),
                    "diff": round(float(r["diff"]), 4),
                    "jump_type": "pctchg_mismatch"})
                with sqlite3.connect(self.db_path, timeout=30) as conn:
                    conn.execute(
                        "INSERT INTO qfq_jump_audit "
                        "(code,time,pctChg,close_change_pct,diff,jump_type,detected_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (r["code"], int(r["time"]),
                         float(r["pctChg"]), float(r["close_chg"]), float(r["diff"]),
                         "pctchg_mismatch", now.isoformat()))
                    conn.commit()
            report["pctchg_mismatches"] = len(bad)

        report["samples"] = samples[:20]  # 截断样本
        logger.info(f"[QFQ] detect_jumps: qfq_jumps={report['qfq_jumps']} "
                    f"pctchg_mismatches={report['pctchg_mismatches']}")
        if report["qfq_jumps"] or report["pctchg_mismatches"]:
            logger.warning(f"[QFQ] ⚠ 检测到跳变！qfq_jumps={report['qfq_jumps']} "
                           f"pctchg_mismatches={report['pctchg_mismatches']}（详见 qfq_jump_audit 表）")
        return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    qfq = QFQMaintenance(db_path())

    # 对已入库的数据做跳变检测（应无跳变）
    print("=== 检测已入库数据的 QFQ 跳变 ===")
    report = qfq.detect_jumps()
    print(f"qfq_jumps (|pctChg|>20%): {report['qfq_jumps']}")
    print(f"pctchg_mismatches: {report['pctchg_mismatches']}")
    if report["samples"]:
        print("样本:")
        for s in report["samples"][:5]:
            print(f"  {s}")
    else:
        print("✅ 无跳变（数据干净）")

    # 测试：构造一个假跳变验证检测能力（统一口径：code 裸码，time 毫秒时间戳）
    print("\n=== 跳变检测能力验证（构造假跳变）===")
    import duckdb, tempfile, os
    from .aligner import to_ms_timestamp
    tmp = tempfile.mktemp(suffix=".db")
    t1 = to_ms_timestamp("2026-07-07")
    t2 = to_ms_timestamp("2026-07-08")
    t3 = to_ms_timestamp("2026-07-09")
    with duckdb.connect(tmp) as conn:
        conn.execute("""CREATE TABLE stock_daily(
            code VARCHAR, time BIGINT, open DOUBLE, high DOUBLE,
            low DOUBLE, close DOUBLE, pctChg DOUBLE, volume DOUBLE, amount DOUBLE)""")
        conn.execute(f"""INSERT INTO stock_daily VALUES
            ('600000',{t1},8.89,8.97,8.78,8.89,-0.3363,51779400,458252573),
            ('600000',{t2},8.85,9.03,8.79,9.00,1.2373,54468700,488055513),
            ('600000',{t3},30.0,30.5,29.8,30.2,235.0,49029700,438686945)""")
        # ↑ 第3行 close 从 9.0 跳到 30.2，但 pctChg=235%（典型 QFQ 批次边界跳变）
    qfq2 = QFQMaintenance(tempfile.mktemp(suffix=".db"))
    r2 = qfq2.detect_jumps(duckdb_path=tmp)
    print(f"构造跳变检测: qfq_jumps={r2['qfq_jumps']}（应为 1）")
    assert r2["qfq_jumps"] == 1, "❌ 跳变检测失败"
    print("✅ 跳变检测能力验证通过")
    os.remove(tmp)
