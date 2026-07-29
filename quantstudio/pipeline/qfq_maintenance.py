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
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
from quantstudio._paths import db_path
from .aligner import raw_to_tushare_ts_code
ROOT = Path(__file__).resolve().parent.parent.parent


class FactorRefreshError(Exception):
    """某资产类别**全部逐码请求**都失败时抛出（任务2 冲突点1 修复）。

    与"正常无复权数据"严格区分：
    - 正常无数据：Tushare 返回空 DataFrame（不抛异常）→ dfs 非空 → 正常 return 0；
    - 全部请求失败：逐码全部抛异常 → dfs 为空 → 抛本异常 → 上层 refresh 进入 degraded。

    仅"全部失败"才抛；部分码失败仍返回成功部分（保留隔离性，残余风险见 refresh 文档）。
    """
    pass


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
            # ETF/基金复权因子表（仅 ETF；与 adj_factor 结构完全一致，彻底隔离）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fund_adj (
                    code TEXT, time INTEGER, adj_factor REAL,
                    PRIMARY KEY (code, time)
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
        failed_count = 0
        for code in codes:
            try:
                adapter.rate_limiter.acquire()
                df = api(ts_code=code, start_date=start.replace("-", ""),
                         end_date=end.replace("-", ""))
                dfs.append(df)
            except Exception as e:
                failed_count += 1
                logger.warning(f"[QFQ] {code} {api_name} failed: {e}")
        # 任务2 冲突点1：某资产类别全部逐码请求失败 → 抛 FactorRefreshError，
        # 让上层 refresh 进入 degraded（禁止把旧快照解释成"今天没有事件"）。
        # 防 0==0 误抛：codes 为空时不抛（维持原契约：空 codes 返回 0）。
        # 正常无数据（空 df）→ dfs 非空 → 不会走到这里。
        if codes and failed_count == len(codes):
            raise FactorRefreshError(
                f"{api_name} 全部 {failed_count}/{len(codes)} 码拉取失败")
        if not dfs:
            return 0
        df = pd.concat(dfs, ignore_index=True)
        records = []
        for _, r in df.iterrows():
            raw_code = normalize_code(str(r["ts_code"]), "tushare_to_raw")
            ms = to_ms_timestamp(str(r["trade_date"]))
            if raw_code and ms:
                records.append((raw_code, ms, float(r["adj_factor"])))
        # 任务1.2：股票写 adj_factor；ETF 写 fund_adj。两张表彻底隔离，
        # 不得再把 ETF 因子写入 adj_factor。
        table = "fund_adj" if is_etf else "adj_factor"
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", records)
            conn.commit()
        logger.debug(f"[QFQ] fetched {len(records)} {api_name} rows ({len(codes)} codes) → {table}")
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


# ----------------------------------------------------------------------------
# 任务1.5：universe 过滤助手（来源为已存在的正式表，不新增数据源）
# ----------------------------------------------------------------------------
def get_etf_universe(main_db_path) -> set:
    """返回 ETF 裸码集合（来自 etf_basic 正式表）。缺失/异常时返回空集合（不阻断）。"""
    try:
        import duckdb
        with duckdb.connect(str(main_db_path), read_only=True) as conn:
            df = conn.execute("SELECT DISTINCT code FROM etf_basic").fetchdf()
        return {str(c) for c in df["code"].tolist()}
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"[QFQ] get_etf_universe 失败（返回空集）: {e}")
        return set()


def get_stock_universe(main_db_path) -> set:
    """返回股票裸码集合（来自 index_constituents 正式表）。缺失/异常时返回空集合。"""
    try:
        import duckdb
        with duckdb.connect(str(main_db_path), read_only=True) as conn:
            df = conn.execute("SELECT DISTINCT code FROM index_constituents").fetchdf()
        return {str(c) for c in df["code"].tolist()}
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"[QFQ] get_stock_universe 失败（返回空集）: {e}")
        return set()


def _resolve_etf_universe(aux_db_path, etf_universe) -> set:
    """etf_universe 为 None 时，尝试从同目录正式库推导 ETF universe。"""
    if etf_universe is not None:
        return set(etf_universe)
    main_db = Path(aux_db_path).parent / "quantstudio.db"
    if main_db.exists():
        return get_etf_universe(main_db)
    return set()


# C3：裸码 → Tushare ts_code 的合法格式（仅 6 位裸码 + .SH/.SZ/.BJ）
_TS_CODE_VALID_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def resolve_ts_codes(codes, asset_type: str, main_db) -> List[str]:
    """把裸码/混合码列表解析为 Tushare ts_code。

    优先级（严格）：
    1. **已带合法 Tushare 后缀（.SH/.SZ/.BJ，含规范化 .SS→.SH）的输入 → 幂等保留**，
       不查元数据、不被覆盖、不计 miss。
    2. **裸码 → 优先用元数据表权威 ts_code**（STOCK→stock_basic、ETF→etf_basic）。
    3. **裸码元数据 miss（NULL/空/非法/与裸码不一致）或表缺失 → 前缀规则 fallback**，
       不丢弃任何码。
    4. **未知前缀**（不属于资产类型合法首位：STOCK 6/0/3/4/8；ETF 5/1）→ 防御性
       fallback 到 .BJ，并聚合输出一次 WARNING（可观测性）。

    契约：
    - 输出顺序与数量与输入完全一致（不因 SQL 返回顺序重排、不丢码）；
    - 已带合法后缀的输入幂等保留在原位置，**绝不**被元数据覆盖；
    - 不修改调用者传入的集合/列表；
    - SQL 参数化（DuckDB 列表参数 ``WHERE code IN (SELECT unnest(?))``），不拼字符串；
    - 元数据值必须通过 ``^\\d{6}\\.(SH|SZ|BJ)$`` 校验且裸码部分与查询 code 一致才算 hit；
    - 裸码 miss 日志按资产类别聚合一次（INFO，不逐码刷屏）；表缺失/异常 warning 一次后全量 fallback；
    - 未知前缀的防御性 fallback 聚合输出一次 WARNING；
    - 不创建不存在的 quantstudio.db；查询连接必关闭。

    Args:
        codes: 裸码/带后缀码的可迭代对象（如 sorted(stock_universe)）。
        asset_type: "STOCK" 或 "ETF"。
        main_db: quantstudio.db 路径（元数据表所在库）。

    Returns:
        与输入等长、同序的 Tushare ts_code 列表。
    """
    input_codes = [str(c).strip() for c in codes]
    if not input_codes:
        return []

    # 1) 先用前缀规则对全部输入做兜底解析（建立 base）。
    base_map: Dict[str, str] = {}
    for c in input_codes:
        if c not in base_map:
            base_map[c] = raw_to_tushare_ts_code(c, asset_type)

    # 2) 只收集【裸码】加入元数据查询集合（已带合法后缀的输入不查、不覆盖）
    bare_to_query = set()
    for c in input_codes:
        normalized = c.upper().replace(".SS", ".SH")
        if _TS_CODE_VALID_RE.fullmatch(normalized):
            continue  # 已带合法后缀 → 幂等保留，不参与元数据查询
        if c.isdigit() and len(c) == 6:
            bare_to_query.add(c)
        # 既非合法 ts_code 也非 6 位裸码 → base 已处理（抛错或给结果），不查元数据

    if not bare_to_query:
        # 无裸码待查 → 全部走 base（含显式后缀幂等 + 前缀）
        out = [base_map[c] for c in input_codes]
        _log_unknown_prefix_warnings(input_codes, base_map, asset_type)
        return out

    # 3) 元数据查询（参数化，单次）——仅查裸码
    table = "stock_basic" if asset_type.strip().upper() == "STOCK" else "etf_basic"
    metadata_map: Dict[str, str] = {}  # bare_code -> validated ts_code
    metadata_ok = True
    try:
        import duckdb
        with duckdb.connect(str(main_db), read_only=True) as conn:
            rows = conn.execute(
                f"SELECT code, ts_code FROM {table} "
                f"WHERE code IN (SELECT unnest(?))",
                [sorted(bare_to_query)],
            ).fetchall()
        for code_val, ts_val in rows:
            if ts_val is None:
                continue
            ts = str(ts_val).strip().upper().replace(".SS", ".SH")
            if not _TS_CODE_VALID_RE.match(ts):
                continue  # 非法元数据 → miss
            # 裸码一致性校验：元数据 ts_code 的裸码部分必须与 code 一致，防串码
            if ts[:6] != str(code_val).strip():
                logger.warning(
                    f"[QFQ] {table} 元数据串码：code={code_val} 但 ts_code={ts_val}，"
                    f"该码 fallback 到前缀规则")
                continue
            metadata_map[str(code_val).strip()] = ts
    except Exception as e:  # 表缺失/查询异常 → 全量 fallback（warning 一次）
        metadata_ok = False
        logger.warning(
            f"[QFQ] resolve_ts_codes 元数据查询失败（{table}），"
            f"全部 {len(input_codes)} 码使用前缀 fallback: {e}")

    # 4) 组装输出（按原输入顺序/数量）
    #    显式合法后缀 → 幂等保留（base_map）；裸码命中元数据 → 元数据；其余 → base（前缀）
    result: List[str] = []
    miss_samples: List[str] = []
    for c in input_codes:
        normalized = c.upper().replace(".SS", ".SH")
        if _TS_CODE_VALID_RE.fullmatch(normalized):
            # 调用者已提供合法 ts_code → 幂等保留，不被元数据覆盖
            result.append(base_map[c])
            continue
        bare = c if (c.isdigit() and len(c) == 6) else None
        if bare is not None and bare in metadata_map:
            result.append(metadata_map[bare])
        else:
            result.append(base_map[c])
            if bare is not None and bare not in metadata_map:
                miss_samples.append(bare)

    # 裸码 miss 聚合日志（INFO）；仅当元数据查询本身成功时才有意义统计 miss
    if metadata_ok and miss_samples:
        logger.info(
            f"[QFQ] {asset_type} ts_code 元数据 miss {len(miss_samples)}/"
            f"{len(input_codes)}，已使用前缀 fallback，sample={miss_samples[:10]}")

    # 未知前缀的防御性 fallback WARNING（与普通 miss 分开）
    _log_unknown_prefix_warnings(input_codes, base_map, asset_type)
    return result


def _log_unknown_prefix_warnings(input_codes, base_map, asset_type) -> None:
    """资产类型不支持的未知首位前缀 → 聚合 WARNING（防御性 fallback 可观测性）。

    合法首位：STOCK 6/0/3/4/8；ETF 5/1。其它首位（如 2/7/9）的裸码会被
    raw_to_tushare_ts_code 防御性 fallback 到 .BJ，此处聚合记录一次 WARNING。
    普通元数据 miss（合法首位但元数据无记录）仍为 INFO，不在此处理。
    """
    at = str(asset_type).strip().upper()
    legal = {"6", "0", "3", "4", "8"} if at == "STOCK" else {"5", "1"}
    unknown: List[str] = []
    seen = set()
    for c in input_codes:
        if not (c.isdigit() and len(c) == 6):
            continue
        if c[0] in legal:
            continue
        # 仅当该裸码的 base 结果确实落到了 .BJ（防御性 fallback）才告警
        base = base_map.get(c, "")
        if base.endswith(".BJ") and c not in seen:
            unknown.append(c)
            seen.add(c)
    if unknown:
        logger.warning(
            f"[QFQ] {asset_type} 出现 {len(unknown)} 个未知前缀代码，"
            f"已防御性 fallback 到 BJ，sample={unknown[:10]}")


# ----------------------------------------------------------------------------
# 任务1.4：staging-only 历史混数据隔离工具（正式库禁止调用）
# ----------------------------------------------------------------------------
def migrate_split_etf_factors(aux_db_path, etf_universe=None, *, dry_run=True):
    """从旧 adj_factor 中识别 ETF 裸码，迁移到 fund_adj，并从 adj_factor 删除。

    仅用于 staging 环境（正式库绝不可调用）。dry_run=True 时只返回
    (moved_rows, sample) 而不真正写入。

    Returns:
        (moved_rows, sample) 其中 sample 为前若干条被迁移行的 [(code,time,adj_factor), ...]
    """
    aux_db_path = Path(aux_db_path)
    if aux_db_path.name == "quantstudio.db":
        aux_db_path = aux_db_path.parent / "qfq_aux.db"
    etf_set = _resolve_etf_universe(aux_db_path, etf_universe)
    with sqlite3.connect(aux_db_path, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(
            "SELECT code, time, adj_factor FROM adj_factor").fetchall()
        etf_rows = [(c, t, a) for (c, t, a) in rows if c in etf_set]
        sample = etf_rows[:5]
        if not dry_run:
            if etf_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO fund_adj VALUES (?,?,?)", etf_rows)
                etf_codes = {r[0] for r in etf_rows}
                ph = ",".join(["?"] * len(etf_codes))
                conn.execute(
                    f"DELETE FROM adj_factor WHERE code IN ({ph})",
                    list(etf_codes))
            conn.commit()
    return (len(etf_rows), sample)


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
