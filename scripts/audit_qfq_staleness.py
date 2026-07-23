"""只读实证审计 v3：fresh xtquant 前复权 vs canonical DuckDB 前复权差异。

PR2 Commit 1整改（评审 11 项）：
  1. ETF 因子改用 sqlite3 查统一 adj_factor 表（非 duckdb/fund_adj）
  2. ETF universe = canonical DISTINCT ∪ xtquant（参数/mock 注入，不读 codes=["ALL"]）
  3. ETF LAG 窗口边界修正（先全历史 LAG 再 WHERE）
  4. 股票候选 past/today/future 分类（默认只 past+today）
  5. --as-of-date 参数（可复现，默认北京时间今天）
  6. time-key merge（v2 已改，补异常测试）
  7. NULL 状态变化识别（null_mismatch vs numeric_diff）
  8. SQL 参数化 + 表名白名单（stock_daily/etf_daily）
  9. 市场代码转换复用 security_code_rules.normalize_to_qmt（含北交所）
  10. full-history/rolling-window 语义准确（start=as_of-2y，区分 window/canonical_earliest）
  11. download_history_data 副作用准确描述（可能刷新 xtquant 本地缓存，非完全只读）

不写正式 Canonical DuckDB；可能刷新 xtquant 本地缓存（download_history_data 副作用）。

用法：
    python scripts/audit_qfq_staleness.py
    python scripts/audit_qfq_staleness.py --as-of-date 2026-07-23
    python scripts/audit_qfq_staleness.py --stocks 600875,600039
    python scripts/audit_qfq_staleness.py --etfs 510210,159928
    python scripts/audit_qfq_staleness.py --full-history
    python scripts/audit_qfq_staleness.py --no-download
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

from quantstudio._paths import db_path, DATA_ROOT

BJ_TZ = timezone(timedelta(hours=8))
_ALLOWED_TABLES = frozenset({"stock_daily", "etf_daily"})  # 表名白名单


def ms_to_bj(ms) -> str:
    """毫秒 epoch → 北京时间 YYYY-MM-DD 字符串。"""
    if ms is None:
        return "None"
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=BJ_TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return "?"


def to_qmt_code(code: str) -> str:
    """市场代码转换，复用项目权威 security_code_rules（含北交所 920/legacy）。

    不再硬编码 5/6/9→.SH 规则（会把 920xxx 北交所错误映射为 .SH）。
    """
    from quantstudio.backtest.libs.security_code_rules import normalize_to_qmt
    return normalize_to_qmt(code)


# ---------------------------------------------------------------------------
# DB 元信息查询（参数化 SQL，表名白名单）
# ---------------------------------------------------------------------------

def _validate_table(table: str) -> str:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"非法表名 {table!r}，仅允许 {sorted(_ALLOWED_TABLES)}")
    return table


def get_canonical_meta(conn, table: str) -> dict:
    """输出 Canonical 表关键元信息。表名经白名单校验。"""
    table = _validate_table(table)
    meta = {"table": table}
    try:
        meta["max_time"] = conn.execute(f"SELECT MAX(time) FROM {table}").fetchone()[0]
    except Exception:
        meta["max_time"] = None
    try:
        meta["row_count"] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        meta["row_count"] = 0
    try:
        meta["max_update_time"] = conn.execute(
            f"SELECT MAX(update_time) FROM {table}").fetchone()[0]
    except Exception:
        meta["max_update_time"] = None
    try:
        meta["source_dist"] = conn.execute(
            f"SELECT data_source, COUNT(*) FROM {table} GROUP BY data_source"
        ).fetchall()
    except Exception:
        meta["source_dist"] = []
    return meta


def get_watermark(conn, source: str, table: str, freq: str = "daily"):
    try:
        r = conn.execute(
            "SELECT last_date FROM source_watermark WHERE source=? AND table_name=? AND freq=?",
            [source, table, freq]).fetchone()
        return r[0] if r else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 股票候选（评审 4: past/today/future 分类）
# ---------------------------------------------------------------------------

def normalize_ex_date(ex_date) -> int:
    """归一化 stock_dividend.ex_date。

    **生产口径（audit-fix2 阻断 3 修正）：只支持 epoch ms。**
    正式库 stock_dividend.ex_date 实际是 epoch ms（如 1784649600000=2026-07-22，已确认）。

    本函数对 8 位 YYYYMMDD 整数做防御性兜底转换（仅 Python 侧，不覆盖 SQL 路径），
    但**不构成对 YYYYMMDD 旧数据的兼容声明** —— select_stock_candidates 的 SQL 用
    epoch ms 下界/上界过滤，YYYYMMDD 整数（如 20260701≈2e7）远小于 cutoff_ms（≈1.78e12），
    会在 SQL 阶段被直接过滤，根本进不到本函数。因此纯 YYYYMMDD 旧数据会漏选，
    这是明确局限，非支持声明。

    判定依据：8 位且以 19/20 开头 → 视为 YYYYMMDD（兜底）；否则视为 epoch ms（直通）。
    """
    if ex_date is None:
        return 0
    try:
        v = int(ex_date)
    except (TypeError, ValueError):
        return 0
    s = str(v)
    if len(s) == 8 and s.startswith(("20", "19")):
        try:
            dt = datetime.strptime(s, "%Y%m%d").replace(tzinfo=BJ_TZ)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return 0
    # 否则视为 epoch ms（正式库 stock_dividend.ex_date 实际是 epoch ms）
    return v


def classify_ex_date(ex_ms: int, as_of_ms: int) -> str:
    """除权日分类：past / today / future（评审 4）。

    默认 stale 审计候选只含 past+today；future 单独输出 upcoming。
    """
    if ex_ms == 0:
        return "unknown"
    ex_day = datetime.fromtimestamp(ex_ms / 1000, tz=BJ_TZ).strftime("%Y-%m-%d")
    as_of_day = datetime.fromtimestamp(as_of_ms / 1000, tz=BJ_TZ).strftime("%Y-%m-%d")
    if ex_day < as_of_day:
        return "past"
    if ex_day == as_of_day:
        return "today"
    return "future"


def select_stock_candidates(conn, as_of_dt: datetime, n: int = 10,
                             days_back: int = 30, include_future: bool = False) -> tuple:
    """从 stock_dividend 查除权股票，按 past/today/future 分类。

    **audit-fix2 阻断 3 修正：明确只支持 epoch ms（生产口径），不支持 mixed。**
    stock_dividend.ex_date 正式库实际是 epoch ms（已确认）。本查询用 epoch ms 的下界
    与上界做范围过滤。YYYYMMDD 整数（如 20260701≈2e7）远小于 cutoff_ms（≈1.78e12），
    会在 SQL 阶段被直接过滤掉 —— 这是已知局限，非兼容声明。若未来库中出现纯 YYYYMMDD
    旧数据，需先用 epoch ms 重建，本脚本不做 mixed 兼容。

    审计-fix 阻断 1 修正（已实现，保留）：
    - active 查询加 upper bound（as_of 当天 23:59），使 future epoch-ms 记录不会
      占满 LIMIT 挤掉 today/past active 事件。
    - future 用独立查询（无 upper bound），不与 active 竞争 LIMIT。

    返回 (active_candidates, upcoming_candidates)。
    active = past + today（默认 stale 审计候选，上限 as_of 当天）。
    upcoming = future（独立查询，不计入 stale 结论）。
    """
    as_of_ms = int(as_of_dt.timestamp() * 1000)
    cutoff_ms = int((as_of_dt - timedelta(days=days_back)).timestamp() * 1000)
    # as_of 当天 23:59:59 北京时间（active 上界，防 future 占满 LIMIT）
    as_of_day_end = (as_of_dt.replace(hour=23, minute=59, second=59))
    as_of_end_ms = int(as_of_day_end.timestamp() * 1000)

    active, upcoming = [], []

    # active 查询：cutoff <= ex_date <= as_of_day_end（含 past + today，排除 future）
    # epoch ms 范围过滤（audit-fix2 阻断 3：只支持 epoch ms；YYYYMMDD 旧数据会被过滤）
    active_rows = conn.execute("""
        SELECT code, ex_date, cash_div, stk_div FROM stock_dividend
        WHERE ex_date >= ? AND ex_date <= ?
          AND (cash_div > 0.05 OR stk_div > 0.05)
        ORDER BY ex_date DESC LIMIT ?
    """, [cutoff_ms, as_of_end_ms, n]).fetchall()
    for code, ex_date, cash_div, stk_div in active_rows:
        ex_ms = normalize_ex_date(ex_date)
        if not ex_ms:
            continue
        status = classify_ex_date(ex_ms, as_of_ms)
        # active 查询理论上只返回 past+today；double-check
        if status in ("past", "today"):
            active.append((code, ex_ms, float(cash_div or 0), float(stk_div or 0), status))

    # upcoming 查询：ex_date > as_of_day_end（future，独立 LIMIT 不与 active 竞争）
    upcoming_rows = conn.execute("""
        SELECT code, ex_date, cash_div, stk_div FROM stock_dividend
        WHERE ex_date > ?
          AND (cash_div > 0.05 OR stk_div > 0.05)
        ORDER BY ex_date ASC LIMIT ?
    """, [as_of_end_ms, n]).fetchall()
    for code, ex_date, cash_div, stk_div in upcoming_rows:
        ex_ms = normalize_ex_date(ex_date)
        if not ex_ms:
            continue
        status = classify_ex_date(ex_ms, as_of_ms)
        if status == "future":
            upcoming.append((code, ex_ms, float(cash_div or 0), float(stk_div or 0), status))

    return active, upcoming


# ---------------------------------------------------------------------------
# ETF 因子查询（评审 1/2/3: sqlite3 + 统一 adj_factor + LAG 窗口修正）
# ---------------------------------------------------------------------------

def select_etf_candidates_from_adj_factor(etf_universe: list, as_of_dt: datetime,
                                          days_back: int = 30,
                                          factor_epsilon: float = 1e-9) -> tuple:
    """从 qfq_aux.db（SQLite）的统一 adj_factor 表查 ETF 因子变化。

    审计-fix 阻断 2/6 修正：
    - 阻断 2：正确区分三类 ETF：
        * changed_candidates：窗口内有因子变化（> epsilon）
        * stable_with_record：有完整因子历史但窗口内无变化
        * no_record：完全无 adj_factor 记录（报告"无法判断"非"无变化"）
    - 阻断 6：factor_epsilon 参数化（默认 1e-9 精度 epsilon，非 0.001 严重度阈值）。
      epsilon 用于"是否发生版本变化"；严重度阈值（如 0.1%）应在报告层标注，不在此判定。

    audit-fix2 阻断 4 修正（确定性 as-of 语义）：
    - 两处 SQL 都加 `time <= as_of_end_ms` 上界（as_of 当天 23:59:59 北京时间）。
      固定 --as-of-date 时，as_of 之后的未来因子变化不会进入 changed，
      future-only 记录不会被视为 as-of 时点"已有记录"（归入 no_record）。
    - changed_candidates 按 ETF code 去重（同一 ETF 多次因子变化只报一只一次）。

    评审 1: sqlite3 + 统一 adj_factor 表（epoch-ms time，qfq_maintenance.py:57）。
    评审 3: 先全历史 LAG，再外层 WHERE time >= cutoff（窗口边界修正）。

    返回 (changed_candidates, stable_with_record, no_record)。
    """
    if not etf_universe:
        return [], [], []
    qfq_db = DATA_ROOT / "qfq_aux.db"
    if not qfq_db.exists():
        logger.warning("qfq_aux.db 不存在，ETF 因子查询跳过")
        return [], [], list(etf_universe)
    cutoff_ms = int((as_of_dt - timedelta(days=days_back)).timestamp() * 1000)
    # 阻断 4: as-of 上界（as_of 当天 23:59:59 北京时间），确定性 as-of 语义
    as_of_end_ms = int(as_of_dt.replace(hour=23, minute=59, second=59).timestamp() * 1000)
    try:
        conn = sqlite3.connect(str(qfq_db))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "adj_factor" not in tables:
            logger.warning("qfq_aux.db 无 adj_factor 表")
            conn.close()
            return [], [], list(etf_universe)
        ph = ",".join("?" * len(etf_universe))
        # 先查 as-of 时点已有记录的 code（阻断 4: 加 time <= as_of_end_ms 上界，
        # future-only 记录不算 as-of 时点已记录，归入 no_record）
        recorded_rows = conn.execute(f"""
            SELECT DISTINCT code FROM adj_factor
            WHERE code IN ({ph}) AND time <= ?
        """, list(etf_universe) + [as_of_end_ms]).fetchall()
        codes_with_any_record = {r[0] for r in recorded_rows}
        # 评审 3 + 阻断 6: LAG 全历史 + epsilon 判定；阻断 4: change 查询加 time <= as_of_end_ms
        change_rows = conn.execute(f"""
            WITH t AS (
                SELECT code, time, adj_factor,
                       LAG(adj_factor) OVER (
                           PARTITION BY code ORDER BY time
                       ) AS prev_adj
                FROM adj_factor
                WHERE code IN ({ph})
            )
            SELECT code, time, adj_factor, prev_adj
            FROM t
            WHERE time >= ?
              AND time <= ?
              AND prev_adj IS NOT NULL
              AND ABS(adj_factor - prev_adj) > ?
            ORDER BY time DESC
        """, list(etf_universe) + [cutoff_ms, as_of_end_ms, factor_epsilon]).fetchall()
        conn.close()
    except Exception as e:
        logger.warning("adj_factor 查询失败: " + str(e))
        return [], [], list(etf_universe)
    # 阻断 2: 三类分离；阻断 4 顺手修复: changed_candidates 按 ETF code 去重
    codes_with_recent_change = set()
    changed_candidates = []
    for code, t, adj, prev in change_rows:
        if code in codes_with_recent_change:
            continue  # 同一 ETF 多次因子变化只报一次（去重）
        codes_with_recent_change.add(code)
        changed_candidates.append((code, int(t), abs(float(adj) - float(prev))))
    stable_with_record = sorted(codes_with_any_record - codes_with_recent_change)
    no_record = sorted(set(etf_universe) - codes_with_any_record)
    return changed_candidates, stable_with_record, no_record


# ---------------------------------------------------------------------------
# xtquant fresh 拉取（time-key merge，评审 6；download 副作用说明，评审 11）
# ---------------------------------------------------------------------------

def fetch_fresh_front_xtquant(codes, start_date: str, end_date: str,
                               table: str = "stock_daily",
                               do_download: bool = True,
                               xtdata_client=None) -> tuple:
    """从 xtquant 拉取 fresh 三段式数据，按 time 键 merge。

    **副作用说明（评审 11）**：do_download=True 时会调用 download_history_data，
    可能刷新 miniQMT 本地行情缓存。不写正式 Canonical DuckDB，但非完全只读。

    xtdata_client：可注入 mock（测试用，不连 live QMT）。None 时 import xtquant.xtdata。

    返回 (df, freshness_meta)。
    """
    if xtdata_client is None:
        try:
            import xtquant.xtdata as xtdata
            xtdata_client = xtdata
        except ImportError as e:
            logger.error("xtquant 未安装: " + str(e))
            return pd.DataFrame(), {"error": str(e)}

    period = "1d" if table in ("stock_daily", "etf_daily") else "1m"
    freshness = {
        "connect_time": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "download_performed": do_download,
        "per_code": {},
    }

    frames = []
    for bare in codes:
        xc = to_qmt_code(bare)
        code_meta = {"raw": 0, "front": 0, "back": 0, "max_time": None}
        try:
            if do_download:
                try:
                    xtdata_client.download_history_data(xc, period, start_date, end_date)
                except Exception as e:
                    logger.warning("  " + bare + " download 失败（用本地缓存）: " + str(e))
            none_d = xtdata_client.get_market_data_ex(stock_list=[xc], period=period,
                start_time=start_date, end_time=end_date, dividend_type="none")
            front_d = xtdata_client.get_market_data_ex(stock_list=[xc], period=period,
                start_time=start_date, end_time=end_date, dividend_type="front")
            back_d = xtdata_client.get_market_data_ex(stock_list=[xc], period=period,
                start_time=start_date, end_time=end_date, dividend_type="back")
            if xc not in none_d or len(none_d[xc]) == 0:
                logger.warning("  " + bare + ": xtquant raw 无数据")
                freshness["per_code"][bare] = code_meta
                continue
            raw = none_d[xc].reset_index().copy()
            raw.columns = [str(c).lower() for c in raw.columns]
            if "time" not in raw.columns:
                raw = raw.rename(columns={raw.columns[0]: "time"})
            raw["time"] = raw["time"].astype("int64")
            raw["code"] = bare
            code_meta["raw"] = len(raw)
            code_meta["max_time"] = int(raw["time"].max())
            # 评审 6: 按 time 键 merge（非数组位置赋值）
            for label, dataset in (("front", front_d), ("back", back_d)):
                if xc in dataset and len(dataset[xc]) > 0:
                    adj = dataset[xc].reset_index().copy()
                    adj.columns = [str(c).lower() for c in adj.columns]
                    if "time" not in adj.columns:
                        adj = adj.rename(columns={adj.columns[0]: "time"})
                    adj["time"] = adj["time"].astype("int64")
                    adj = adj.drop_duplicates(subset=["time"], keep="last")  # keep=last
                    code_meta[label] = len(adj)
                    price_cols = [c for c in ("open", "high", "low", "close") if c in adj.columns]
                    adj_sub = adj[["time"] + price_cols].rename(
                        columns={c: c + "_" + label for c in price_cols})
                    raw = raw.merge(adj_sub, on="time", how="left")
            frames.append(raw)
            freshness["per_code"][bare] = code_meta
        except Exception as e:
            logger.warning("  " + bare + " fresh 拉取失败: " + str(e))
            freshness["per_code"][bare] = code_meta
    if not frames:
        return pd.DataFrame(), freshness
    df = pd.concat(frames, ignore_index=True)
    return df, freshness


# ---------------------------------------------------------------------------
# canonical 读取（参数化 SQL，评审 8）
# ---------------------------------------------------------------------------

def read_canonical(conn, codes, table: str) -> pd.DataFrame:
    """参数化查询（评审 8）。表名白名单校验。空 code list 返回空 DataFrame。"""
    table = _validate_table(table)
    if not codes:
        return pd.DataFrame()
    cols = ("code, time, open, high, low, close, volume, amount, "
            "open_front, high_front, low_front, close_front, "
            "open_back, high_back, low_back, close_back, preClose, pctChg")
    ph = ",".join("?" * len(codes))
    sql = f"SELECT {cols} FROM {table} WHERE code IN ({ph}) ORDER BY code, time"
    return conn.execute(sql, list(codes)).fetchdf()


# ---------------------------------------------------------------------------
# 对比分析（评审 7: NULL mismatch 识别 + unique/cells 分离）
# ---------------------------------------------------------------------------

def compare_front(canon_df, fresh_df, table: str) -> dict:
    """对比 canonical vs fresh front 列，区分 numeric_diff 与 null_mismatch。

    审计-fix 阻断 4/5 修正：
    - 阻断 4：null_mismatch_cells / numeric_diff_cells 是**单元格数**（per-col 求和），
      不是"任意列发生该类型的唯一行数"。四列同时 NULL mismatch 应=4 单元格，非 1。
      新增 null_mismatch_unique_rows / numeric_diff_unique_rows 表示唯一行数。
    - 阻断 5：canonical_earliest / fresh_earliest 在 merge 前分别计算；
      overlap_earliest / overlap_latest 在 merge 后计算。三者不再恒等。
    """
    if canon_df.empty or fresh_df.empty:
        return {"error": "empty dataframe", "table": table}
    front_cols = ["open_front", "high_front", "low_front", "close_front"]
    available = [c for c in front_cols if c in fresh_df.columns]
    if not available:
        return {"error": "fresh 无 front 列", "table": table}
    # 阻断 5: merge 前分别计算 earliest/latest
    canonical_earliest = int(canon_df["time"].min()) if len(canon_df) else None
    canonical_latest = int(canon_df["time"].max()) if len(canon_df) else None
    fresh_earliest = int(fresh_df["time"].min()) if len(fresh_df) else None
    fresh_latest = int(fresh_df["time"].max()) if len(fresh_df) else None
    merged = canon_df[["code", "time"] + [c for c in front_cols if c in canon_df.columns]].merge(
        fresh_df[["code", "time"] + available], on=["code", "time"],
        suffixes=("_canon", "_fresh"), how="inner")
    result = {
        "table": table,
        "canonical_rows": len(canon_df),
        "fresh_rows": len(fresh_df),
        "overlap_rows": len(merged),
        "canonical_earliest": canonical_earliest,
        "canonical_latest": canonical_latest,
        "fresh_earliest": fresh_earliest,
        "fresh_latest": fresh_latest,
        "overlap_earliest": int(merged["time"].min()) if len(merged) else None,
        "overlap_latest": int(merged["time"].max()) if len(merged) else None,
        "rows_compared": len(merged),  # = overlap_rows（保留旧名兼容）
    }
    any_diff_mask = pd.Series(False, index=merged.index)
    null_mismatch_unique_mask = pd.Series(False, index=merged.index)
    numeric_diff_unique_mask = pd.Series(False, index=merged.index)
    per_col = {}
    total_null_cells = 0   # 阻断 4: 单元格求和
    total_numeric_cells = 0
    max_abs = 0.0
    max_rel = 0.0
    for col in available:
        cc, fc = col + "_canon", col + "_fresh"
        if cc not in merged.columns or fc not in merged.columns:
            continue
        canon_na = merged[cc].isna()
        fresh_na = merged[fc].isna()
        col_null_mismatch = canon_na ^ fresh_na
        both_valid = (~canon_na) & (~fresh_na)
        diff = (merged[fc] - merged[cc]).abs()
        col_numeric_diff = both_valid & (diff > 1e-6)
        col_any = col_null_mismatch | col_numeric_diff
        n_null = int(col_null_mismatch.sum())
        n_num = int(col_numeric_diff.sum())
        total_null_cells += n_null      # 阻断 4: 单元格累加
        total_numeric_cells += n_num
        per_col[col] = {"cells": n_null + n_num, "null_mismatch": n_null,
                        "numeric_diff": n_num,
                        "max_abs": float(diff[both_valid].max()) if both_valid.any() else 0.0}
        any_diff_mask |= col_any
        null_mismatch_unique_mask |= col_null_mismatch
        numeric_diff_unique_mask |= col_numeric_diff
        if both_valid.any():
            max_abs = max(max_abs, float(diff[both_valid].max()))
            rel = diff[both_valid] / merged.loc[both_valid, fc].abs().replace(0, float("nan"))
            max_rel = max(max_rel, float(rel.max()) if not rel.empty else 0.0)
    # 阻断 4: unique_rows（任意列有差异）vs cells（单元格求和）
    result["affected_unique_rows"] = int(any_diff_mask.sum())
    result["affected_cells"] = total_null_cells + total_numeric_cells
    result["null_mismatch_cells"] = total_null_cells                    # 单元格数
    result["numeric_diff_cells"] = total_numeric_cells                  # 单元格数
    result["null_mismatch_unique_rows"] = int(null_mismatch_unique_mask.sum())  # 唯一行数
    result["numeric_diff_unique_rows"] = int(numeric_diff_unique_mask.sum())    # 唯一行数
    result["affected_code_count"] = int(merged.loc[any_diff_mask, "code"].nunique())
    result["affected_codes"] = sorted(merged.loc[any_diff_mask, "code"].unique().tolist())
    result["max_abs_diff"] = max_abs
    result["max_rel_diff_pct"] = max_rel * 100
    if any_diff_mask.any():
        result["earliest_diff_time"] = int(merged.loc[any_diff_mask, "time"].min())
    else:
        result["earliest_diff_time"] = None
    result["per_column"] = per_col
    return result


def analyze_ex_date_returns(canon_df, fresh_df, candidates, table: str) -> list:
    """除权日收益率精确分析（评审 5: 精确匹配 ex_date，无最近邻替代）。"""
    results = []
    for entry in candidates:
        # 兼容 4 元组（旧）和 5 元组（新，含 status）
        code = entry[0]; ex_ms = entry[1]; cash_div = entry[2]; stk_div = entry[3]
        status = entry[4] if len(entry) > 4 else None
        entry_out = {"code": code, "ex_date": ms_to_bj(ex_ms), "ex_ms": ex_ms,
                     "cash_div": cash_div, "stk_div": stk_div, "classify_status": status}
        canon_ex = canon_df[(canon_df["code"] == code) & (canon_df["time"] == ex_ms)]
        fresh_ex = fresh_df[(fresh_df["code"] == code) & (fresh_df["time"] == ex_ms)] \
            if "close_front" in fresh_df.columns else pd.DataFrame()
        if canon_ex.empty:
            entry_out["status"] = "pending_ex_date_ingestion"
            entry_out["note"] = "Canonical 未含除权日 " + ms_to_bj(ex_ms) + " 行"
            results.append(entry_out)
            continue
        ex_row = canon_ex.iloc[0]
        prev = canon_df[(canon_df["code"] == code) & (canon_df["time"] < ex_ms)].sort_values("time")
        if prev.empty:
            entry_out["status"] = "no_prev_trading_day"
            results.append(entry_out)
            continue
        prev_row = prev.iloc[-1]
        canon_raw_ret = (ex_row["close"] / prev_row["close"] - 1) * 100 if prev_row["close"] else None
        canon_front_ret = ((ex_row["close_front"] / prev_row["close_front"] - 1) * 100
                           if prev_row["close_front"] and ex_row["close_front"] else None)
        fresh_front_ret = None
        boundary_gap = None
        fresh_prev = fresh_df[(fresh_df["code"] == code) & (fresh_df["time"] < ex_ms)].sort_values("time") \
            if not fresh_df.empty else pd.DataFrame()
        if not fresh_ex.empty and not fresh_prev.empty and "close_front" in fresh_df.columns:
            fe = fresh_ex.iloc[0]
            fp = fresh_prev.iloc[-1]
            fresh_front_ret = ((fe["close_front"] / fp["close_front"] - 1) * 100
                               if fp["close_front"] and fe["close_front"] else None)
            if prev_row["close_front"] and fp["close_front"]:
                boundary_gap = float(prev_row["close_front"]) - float(fp["close_front"])
        entry_out.update({
            "status": "analyzed",
            "canon_raw_return_pct": canon_raw_ret,
            "canon_front_return_pct": canon_front_ret,
            "fresh_front_return_pct": fresh_front_ret,
            "pctchg_recorded": ex_row.get("pctChg"),
            "boundary_gap_canon_minus_fresh_at_prev_day": boundary_gap,
        })
        results.append(entry_out)
    return results


def analyze_signal_impact(canon_df, fresh_df, table: str) -> dict:
    """对 5/20/60 日均线、20 日动量的实际影响。"""
    if canon_df.empty or fresh_df.empty or "close_front" not in fresh_df.columns:
        return {"status": "skip", "reason": "无 fresh close_front"}
    merged = canon_df[["code", "time", "close_front"]].merge(
        fresh_df[["code", "time", "close_front"]], on=["code", "time"],
        suffixes=("_canon", "_fresh"))
    # 评审 7: diff 含 NULL mismatch
    canon_na = merged["close_front_canon"].isna()
    fresh_na = merged["close_front_fresh"].isna()
    diff = (merged["close_front_fresh"] - merged["close_front_canon"]).abs()
    both_valid = (~canon_na) & (~fresh_na)
    diff_mask = (canon_na ^ fresh_na) | (both_valid & (diff > 1e-6))
    codes_with_diff = sorted(merged.loc[diff_mask, "code"].unique().tolist())
    if not codes_with_diff:
        return {"status": "no_diff", "codes_checked": sorted(merged["code"].unique().tolist())[:5]}
    impact = {"status": "has_diff", "codes_with_front_diff": codes_with_diff}
    sample = codes_with_diff[0]
    for label, src in (("canonical", canon_df), ("fresh", fresh_df)):
        sub = src[src["code"] == sample].sort_values("time").reset_index(drop=True)
        if "close_front" not in sub.columns or len(sub) < 5:
            continue
        cf = sub["close_front"].astype(float)
        impact[label] = {
            "ma5_last": float(cf.rolling(5).mean().iloc[-1]) if len(cf) >= 5 else None,
            "ma20_last": float(cf.rolling(20).mean().iloc[-1]) if len(cf) >= 20 else None,
            "ma60_last": float(cf.rolling(60).mean().iloc[-1]) if len(cf) >= 60 else None,
            "momentum_20d_pct": float(cf.iloc[-1] / cf.iloc[-20] - 1) * 100 if len(cf) >= 20 else None,
        }
    impact["sample_code"] = sample
    return impact


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _build_default_xtquant_etf_provider():
    """构造默认 xtquant ETF provider（无参数 callable，返回 list[str]）。

    audit-fix2 阻断 1 修正：默认生产路径必须真正调用 xtquant ETF provider，
    得到 Canonical ∪ xtquant union（而非 provider=None 导致的 canonical-only）。

    audit-fix3 勘误：sources_config.json 的 "sources" 是 **dict**（key 为源名，
    如 {"xtquant": {...}}），不是 list。旧实现按 list 遍历（src.get("name")）会
    对 dict 的 key 字符串调用 .get() → 'str' object has no attribute 'get'，
    provider 永远为空 → 默认路径静默降级 canonical-only，阻断 1 实际未关闭。

    本实现与 daemon._get_adapter（daemon.py:1178）口径一致：
      sources = cfg.get("sources", {})
      xt_cfg  = sources.get("xtquant", {})   # dict schema（权威）
    并对 ${ENV_VAR} 占位符做与 daemon 相同的展开。同时对历史 list schema 做防御
    兼容（不假定一定是 dict）。

    - 从 config/sources_config.json 读 xtquant 配置构造 XtquantAdapter（懒连接），
      调其 get_etf_codes()（返回带 .SH/.SZ 后缀的代码，由 _default_etf_universe 归一化裸码）。
    - 任一步失败（无 config / xtquant 未启用 / xtquant 未安装 / QMT 未连接）返回空 list，
      _default_etf_universe 会降级为 canonical-only（不静默吞错，会打 WARNING）。
    """
    import os
    try:
        import json
        cfg_path = _ROOT / "config" / "sources_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        sources = cfg.get("sources", {})
        # 权威 schema: dict（key 为源名）。与 daemon._get_adapter 一致。
        if isinstance(sources, dict):
            src_cfg = sources.get("xtquant", {})
        elif isinstance(sources, list):
            # 防御：兼容历史 list schema（每项含 "name" 字段）
            src_cfg = next((s for s in sources
                            if isinstance(s, dict) and s.get("name") == "xtquant"), {})
        else:
            src_cfg = {}
        if not src_cfg:
            logger.warning("sources_config.json 无 xtquant 源配置，ETF provider 返回空（降级 canonical-only）")
            return lambda: []
        if not src_cfg.get("enabled", False):
            logger.warning("sources_config.json 中 xtquant 未 enabled，ETF provider 返回空（降级 canonical-only）")
            return lambda: []
        # 展开 ${ENV_VAR} 占位符（与 daemon.py:1182-1188 一致）
        expanded = {}
        for k, v in src_cfg.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                expanded[k] = os.environ.get(v[2:-1], "")
            else:
                expanded[k] = v
        expanded["name"] = "xtquant"
        from quantstudio.pipeline.sources.xtquant_adapter import XtquantAdapter
        adapter = XtquantAdapter(expanded)  # 懒连接，构造不立即连 QMT
        return adapter.get_etf_codes
    except Exception as e:
        logger.warning("构造 xtquant ETF provider 失败（降级 canonical-only）: " + str(e))
        return lambda: []


def run_audit(args, etf_universe_provider=None, xtdata_client=None,
              xtquant_etf_provider=None):
    """主流程。

    etf_universe_provider/xtquant_etf_provider/xtdata_client 可注入（测试用）。

    audit-fix2 阻断 1 修正：默认生产路径（三个注入参数均为 None）必须真正调用
    xtquant ETF provider，得到 Canonical ∪ xtquant union。
    - etf_universe_provider：完全替换 universe 计算的 callable（conn → list），优先级最高。
    - xtquant_etf_provider：仅注入 _default_etf_universe 的 xtquant 源（无参数 → list）。
      默认 None 时，run_audit 内部调用 _build_default_xtquant_etf_provider() 构造真实 provider。
    """
    print("=" * 80)
    print("只读实证审计 v3：fresh xtquant 前复权 vs canonical DuckDB 前复权")
    print("=" * 80)
    print()

    # 评审 5: as_of_date 统一使用
    if args.as_of_date:
        as_of_dt = datetime.strptime(args.as_of_date, "%Y-%m-%d").replace(tzinfo=BJ_TZ)
    else:
        as_of_dt = datetime.now(BJ_TZ)
    print(f"as_of_date: {as_of_dt.strftime('%Y-%m-%d')} (北京时间)")
    print()

    db = str(db_path())
    conn = duckdb.connect(db, read_only=True)
    print("=== Canonical 元信息 ===")
    for tbl in ("stock_daily", "etf_daily"):
        meta = get_canonical_meta(conn, tbl)
        print(f"  {tbl}:")
        print(f"    max(time) = {meta['max_time']} = {ms_to_bj(meta['max_time'])}")
        print(f"    row_count = {meta['row_count']}")
        print(f"    max(update_time) = {meta['max_update_time']}")
        print(f"    source_dist = {meta['source_dist']}")
    print("  source_watermark:")
    for src, tbl in [("xtquant", "stock_daily"), ("xtquant", "etf_daily")]:
        wm = get_watermark(conn, src, tbl)
        print(f"    {src}/{tbl} = {wm} = {ms_to_bj(wm)}")
    print()

    # 阻断 7: 窗口解析用纯函数 resolve_audit_window（可测试）
    start_date, end_date, window_desc = resolve_audit_window(
        as_of_dt, args.full_history)
    print(f"审计窗口: {window_desc}")
    print("  注：默认模式仅证明'窗口内历史行受影响'；--full-history 才能写'全历史受影响'")
    print()

    # ---------- 股票（评审 4: past/today/future 分类）----------
    stock_active, stock_upcoming = select_stock_candidates(
        conn, as_of_dt, n=10, days_back=30, include_future=False)
    if args.stocks:
        override = [s.strip() for s in args.stocks.split(",")]
        stock_active = [(c, 0, 0, 0, "override") for c in override]
        stock_upcoming = []
    print(f"【股票样本】active (past+today): {len(stock_active)} 只")
    for c, ex_ms, cd, sd, status in stock_active[:10]:
        print(f"  {c} ex={ms_to_bj(ex_ms)} cash={cd} stk={sd} [{status}]")
    if stock_upcoming:
        print(f"【股票 upcoming (future，不计入 stale 结论)】{len(stock_upcoming)} 只:")
        for c, ex_ms, cd, sd, status in stock_upcoming[:5]:
            print(f"  {c} ex={ms_to_bj(ex_ms)} cash={cd} stk={sd} [{status}]")
    print()

    stock_codes = [c[0] for c in stock_active]
    if stock_codes:
        print("  [1/3] fresh xtquant 拉取（time-key merge，可能刷新本地缓存）...")
        fresh_s, fresh_meta_s = fetch_fresh_front_xtquant(
            stock_codes, start_date, end_date, "stock_daily",
            do_download=not args.no_download, xtdata_client=xtdata_client)
        print(f"  fresh 行数: {len(fresh_s)}")
        print(f"  新鲜度: download_performed={fresh_meta_s.get('download_performed')}")
        print()
        print("  [2/3] canonical 读取...")
        canon_s = read_canonical(conn, stock_codes, "stock_daily")
        print(f"  canonical 行数: {len(canon_s)}")
        print()
        print("  [3/3] 对比分析:")
        diff_s = compare_front(canon_s, fresh_s, "stock_daily")
        print_diff_summary(diff_s)
        print()
        print("  除权日收益率精确分析:")
        disc_s = analyze_ex_date_returns(canon_s, fresh_s, stock_active, "stock_daily")
        for d in disc_s:
            print(f"    {d}")
        print()
        print("  信号影响（均线/动量）:")
        imp_s = analyze_signal_impact(canon_s, fresh_s, "stock_daily")
        print(f"    {imp_s}")
    print()

    # ---------- ETF（阻断 3: universe = canonical ∪ xtquant）----------
    # audit-fix2 阻断 1：默认路径必须真正调用 xtquant ETF provider（非 canonical-only）
    if etf_universe_provider is not None:
        etf_universe = etf_universe_provider(conn)
    else:
        # 默认生产路径：构造真实 xtquant ETF provider（除非显式注入替代）
        provider = (xtquant_etf_provider
                    if xtquant_etf_provider is not None
                    else _build_default_xtquant_etf_provider())
        etf_universe = _default_etf_universe(conn, xtquant_etf_provider=provider)
    print(f"【ETF universe】{len(etf_universe)} 只（canonical DISTINCT ∪ xtquant）")
    print(f"  示例: {etf_universe[:5]}")
    # 阻断 2: 三类分离（changed/stable/no_record）
    etf_cands, etf_stable, etf_no_record = select_etf_candidates_from_adj_factor(
        etf_universe, as_of_dt, days_back=30)
    print(f"【ETF 因子变化】{len(etf_cands)} 只（changed，来自 adj_factor）")
    for c, t, delta in etf_cands[:10]:
        print(f"  {c} change={ms_to_bj(t)} delta={delta}")
    if etf_stable:
        print(f"【ETF 有记录无变化】{len(etf_stable)} 只（stable_with_record）:")
        print(f"  示例: {etf_stable[:5]}")
    if etf_no_record:
        print(f"【ETF 无因子记录】{len(etf_no_record)} 只（no_record，报告'无法判断'非'无变化'）:")
        print(f"  示例: {etf_no_record[:5]}")
    print()
    # ETF 候选 = 因子变化的 + 无记录的（无记录的也走哨兵采样兜底，评审 2）
    etf_sample = [c[0] for c in etf_cands][:10]
    if not etf_sample and etf_no_record:
        etf_sample = etf_no_record[:10]  # 无因子变化时审计无记录的（兜底）
    if args.etfs:
        etf_sample = [s.strip() for s in args.etfs.split(",")]
    if etf_sample:
        print(f"【ETF 审计样本】{len(etf_sample)} 只: {etf_sample[:5]}")
        print("  [1/3] fresh xtquant ETF 拉取...")
        fresh_e, _ = fetch_fresh_front_xtquant(
            etf_sample, start_date, end_date, "etf_daily",
            do_download=not args.no_download, xtdata_client=xtdata_client)
        print(f"  fresh 行数: {len(fresh_e)}")
        print()
        print("  [2/3] canonical ETF 读取...")
        canon_e = read_canonical(conn, etf_sample, "etf_daily")
        print(f"  canonical 行数: {len(canon_e)}")
        print()
        print("  [3/3] 对比分析:")
        diff_e = compare_front(canon_e, fresh_e, "etf_daily")
        print_diff_summary(diff_e)
        print()
        print("  信号影响:")
        imp_e = analyze_signal_impact(canon_e, fresh_e, "etf_daily")
        print(f"    {imp_e}")

    conn.close()
    print()
    print("=" * 80)
    print("审计完成。未修改正式 Canonical 数据库；可能刷新 xtquant 本地缓存（download_history_data）。")
    print("=" * 80)


def resolve_audit_window(as_of_dt: datetime, full_history: bool,
                          history_start: str = "20180101",
                          rolling_days: int = 730) -> tuple:
    """纯函数：解析审计窗口（评审 7 阻断 7：抽出可测试）。

    返回 (start_date_yyyymmdd, end_date_yyyymmdd, window_description)。
    - full_history=True: 20180101 ~ as_of
    - full_history=False: as_of - rolling_days ~ as_of（滚动 2 年）
    """
    end_date = as_of_dt.strftime("%Y%m%d")
    if full_history:
        return history_start, end_date, f"完整历史（{history_start} ~ {end_date}）"
    start_dt = as_of_dt - timedelta(days=rolling_days)
    start_date = start_dt.strftime("%Y%m%d")
    return start_date, end_date, f"滚动 {rolling_days} 天窗口（{start_date} ~ {end_date}）"


def _default_etf_universe(conn, xtquant_etf_provider=None) -> list:
    """默认 ETF universe = canonical etf_daily DISTINCT ∪ xtquant codes。

    审计-fix 阻断 3 修正：真正实现 union（非 canonical only）。
    - xtquant_etf_provider：可注入的 provider（无参数，返回 list[str]）。
      测试用 mock 注入；生产可传入调用 XtquantAdapter 的闭包。
    - 不读 codes=["ALL"]（评审 2）。
    - 任一源失败时降级用另一源（不静默吞错）。

    audit-fix2 阻断 2 修正（裸码归一化去重）：
    - adj_factor 权威口径是裸码（qfq_maintenance.py:57），canonical etf_daily.code 也是裸码。
    - 但 xtquant（XtquantAdapter.get_etf_codes）返回带后缀的 510050.SH / 159919.SZ，
      若直接 union 会导致同一 ETF 重复（510050 + 510050.SH），且带后缀的 code 查 adj_factor
      查不到 → 被错误归入 no_record。
    - 修复：所有源代码统一过 quantstudio.backtest.libs.security_code_rules.bare_code()
      归一化为裸码后再 union，确保去重且与 adj_factor 口径一致。
    """
    from quantstudio.backtest.libs.security_code_rules import bare_code

    codes = set()
    # 源 1: canonical DISTINCT（裸码）
    try:
        rows = conn.execute("SELECT DISTINCT code FROM etf_daily").fetchall()
        for r in rows:
            codes.add(bare_code(r[0]))
    except Exception as e:
        logger.warning("读取 canonical etf_daily 失败: " + str(e))
    # 源 2: xtquant provider（注入）→ 归一化为裸码后 union（阻断 2）
    if xtquant_etf_provider is not None:
        try:
            xt_codes = xtquant_etf_provider()
            if xt_codes:
                for c in xt_codes:
                    codes.add(bare_code(c))
        except Exception as e:
            logger.warning("xtquant ETF provider 失败（仅用 canonical）: " + str(e))
    return sorted(codes)


def print_diff_summary(diff: dict):
    print("  === 差异汇总 ===")
    if "error" in diff:
        print("    错误: " + diff["error"])
        return
    # 阻断 5: 元数据分开（canonical/fresh/overlap）
    print(f"    canonical_rows={diff.get('canonical_rows')} earliest={ms_to_bj(diff.get('canonical_earliest'))} latest={ms_to_bj(diff.get('canonical_latest'))}")
    print(f"    fresh_rows={diff.get('fresh_rows')} earliest={ms_to_bj(diff.get('fresh_earliest'))} latest={ms_to_bj(diff.get('fresh_latest'))}")
    print(f"    overlap_rows={diff.get('overlap_rows')} earliest={ms_to_bj(diff.get('overlap_earliest'))} latest={ms_to_bj(diff.get('overlap_latest'))}")
    print(f"    受影响代码数: {diff['affected_code_count']}")
    print(f"    受影响代码: {','.join(diff.get('affected_codes', [])[:10])}")
    print(f"    受影响唯一历史行 (code,time): {diff['affected_unique_rows']}")
    print(f"    受影响 front 字段单元格总数: {diff['affected_cells']}")
    print(f"      其中 NULL mismatch 单元格: {diff['null_mismatch_cells']} (唯一行: {diff.get('null_mismatch_unique_rows')})")
    print(f"      其中 numeric diff 单元格: {diff['numeric_diff_cells']} (唯一行: {diff.get('numeric_diff_unique_rows')})")
    print(f"    最大绝对差: {round(diff['max_abs_diff'], 6)}")
    print(f"    最大相对差: {round(diff['max_rel_diff_pct'], 4)}%")
    if diff.get("earliest_diff_time"):
        print(f"    最早差异时间: {ms_to_bj(diff['earliest_diff_time'])}")
    print("    各列差异:")
    for col, s in diff.get("per_column", {}).items():
        print(f"      {col}: cells={s['cells']} null_mismatch={s['null_mismatch']} "
              f"numeric_diff={s['numeric_diff']} max_abs={round(s['max_abs'], 6)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读 QFQ 陈旧度审计 v3")
    parser.add_argument("--as-of-date", default=None,
                        help="YYYY-MM-DD（默认北京时间今天，测试可固定）")
    parser.add_argument("--stocks", default=None, help="逗号分隔股票代码")
    parser.add_argument("--etfs", default=None, help="逗号分隔 ETF 代码")
    parser.add_argument("--full-history", action="store_true", help="完整历史（默认滚动 2 年）")
    parser.add_argument("--no-download", action="store_true",
                        help="跳过 download_history_data（用本地缓存，副作用最小）")
    args = parser.parse_args()
    run_audit(args)
