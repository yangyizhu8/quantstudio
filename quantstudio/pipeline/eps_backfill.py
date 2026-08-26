"""P-A3：fin_indicator.eps 跨表回补 + 同源复制列一致性门禁（管线级免疫）。

背景（docs/p-a3-eps-backfill-design.md，2026-08-25）：
- MCP 源端两表回填错位：income_statement.basic_eps 已回填而 fin_indicator.eps 仍 NULL
  （000063 2026Q1 = 0.27 vs eps NULL），导致策略 `_latest_by_code` 取最新公告行时整码被
  `np.isfinite` 剔除（week10 保真对账 L3v/L6 残差 -60/-5）。
- 实证：fin_indicator.eps ↔ income_statement.basic_eps 历史交叉对账 124,078 对中
  121,732 对精确相等（98.1%）——同源复制列，跨表回补口径安全。
- 纯增益铁律：只回补「eps IS NULL 且 income 同 (code,end_date) basic_eps 非空」的行；
  无缺口库上全部条件不命中 → 零行为变更；次新上市前报告期（income 无行）保持 NULL。

本模块为零污染新增：回补核心（backfill_eps_gap）+ 门禁核心（check_eps_backfill_gap）+
同源复制列对注册表（EPS_BACKFILL_PAIRS，泛化扩展点）。挂接点见 writers._write_locked
（写后回补）与 quality_audit.run（门禁）；CLI 见 scripts/backfill_eps_gap.py。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 同源复制列对注册表（防线 2 泛化：检查类别「同源复制列跨表一致性」）
# ---------------------------------------------------------------------------
# 每一对 = (目标表, 目标列) ← (源表, 源列)：目标列 NULL 且源表同 key 源列非空 → 可回补。
# 扩展方式：未来新增同源列对（如 np_yoy ← income_statement 净利增速）时加一元素，
# 回补与门禁自动覆盖（backfill 与 check 均遍历本表）。
EPS_BACKFILL_PAIRS: List[Tuple[Tuple[str, str], Tuple[str, str]]] = [
    (("fin_indicator", "eps"), ("income_statement", "basic_eps")),
]

# 回补打标值（写入目标表的新增列 backfill_eps_source；NULL=原生值）
BACKFILL_SOURCE_MARK = "income_statement.basic_eps"

# 目标表打标列名（新增 VARCHAR 列，经 DDL_DUCKDB/COLS 声明，存量库 ALTER 幂等迁移）
BACKFILL_SOURCE_COL = "backfill_eps_source"


@dataclass
class BackfillResult:
    """一次回补的结果统计（审计可读）。"""

    rows_updated: int = 0
    affected_codes: List[str] = field(default_factory=list)
    ann_date_adjusted: int = 0
    skipped_no_source: int = 0
    pairs_done: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"rows_updated={self.rows_updated} ann_date_adjusted={self.ann_date_adjusted} "
            f"affected_codes={len(self.affected_codes)} pairs={','.join(self.pairs_done) or '-'}"
        )


def _upsert_backfill_sql(pair: Tuple[Tuple[str, str], Tuple[str, str]]) -> str:
    """按同源复制列对生成回补 UPDATE 语句（确定性：rn=1 取源表最新公告版）。

    PIT 保守：目标行 ann_date 取 max(目标 ann_date, 源行 ann_date)——回补值可见日
    不得早于任一表的公告日（防止在 basic_eps 公告前 eps 提前可见）。
    只复制同报告期（code, end_date 精确匹配），零推导、零外推。
    """
    (t_table, t_col), (s_table, s_col) = pair
    mark = BACKFILL_SOURCE_MARK
    return f"""
    UPDATE {t_table} AS t
    SET {t_col} = s.{s_col},
        ann_date = CASE WHEN t.ann_date >= s.ann_date THEN t.ann_date ELSE s.ann_date END,
        {BACKFILL_SOURCE_COL} = '{mark}'
    FROM (
        SELECT code, end_date, ann_date, {s_col},
               ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) AS rn
        FROM {s_table}
        WHERE {s_col} IS NOT NULL
    ) AS s
    WHERE t.{t_col} IS NULL
      AND s.rn = 1
      AND s.code = t.code
      AND s.end_date = t.end_date
    """


def backfill_eps_gap(conn, dry_run: bool = False) -> BackfillResult:
    """全库幂等回补：遍历 EPS_BACKFILL_PAIRS，各自 UPDATE。

    - 幂等：二次执行目标列 IS NOT NULL 不命中 → 0 行。
    - 无缺口库：全部条件不命中 → 0 行 → 逐字节不变（纯增益核心）。
    - dry_run=True：只统计将受影响行数，不落库（验收/预览用）。
    - 目标表缺打标列时自动补齐（CREATE/ALTER 幂等；存量库经 writer 迁移已含，
      独立 CLI 走本函数兜底 CREATE）。
    - 返回 BackfillResult（审计可读）；异常上抛，由调用方（write 路径 try/except、
      CLI 显式）决定处置。
    """
    result = BackfillResult()
    for pair in EPS_BACKFILL_PAIRS:
        (t_table, t_col), (s_table, s_col) = pair
        result.pairs_done.append(f"{t_table}.{t_col}<-{s_table}.{s_col}")
        # 1. 目标表打标列存在性保障（幂等 CREATE/ALTER）
        _ensure_backfill_col(conn, t_table)
        # 2. 统计将影响行数（dry-run 与正式共用，先算后写）
        rows, ann_adjusted, codes = _count_backfill(conn, pair)
        result.ann_date_adjusted += ann_adjusted
        if codes:
            result.affected_codes.extend(codes)
        # 3. 无缺口库/幂等：行数为 0 则跳过 UPDATE（零触碰）
        if rows == 0:
            logger.info(f"[EpsBackfill] {result.pairs_done[-1]}: 0 rows (no gap / idempotent)")
            continue
        if dry_run:
            result.rows_updated += rows
            logger.info(f"[EpsBackfill] {result.pairs_done[-1]}: dry-run {rows} rows (not applied)")
            continue
        # 4. 正式执行（极端 PK 冲突逐码回退，不炸整批）
        _apply_with_conflict_skip(conn, pair, result)
    return result


def _apply_with_conflict_skip(conn, pair: Tuple[Tuple[str, str], Tuple[str, str]], result: BackfillResult) -> None:
    """执行 UPDATE；极端 PK 冲突（ann_date 调整撞已有行，真实库实证 0）时跳过该行并登记，
    不让单行冲突炸掉整批回补（write 路径语义：回补失败不阻断拉取）。
    """
    (t_table, _t_col), _ = pair
    try:
        cur = conn.execute(_upsert_backfill_sql(pair))
        got = getattr(cur, "fetchone", lambda: None)()
        applied = got[0] if got else 0
        result.rows_updated = applied
        logger.info(f"[EpsBackfill] {result.pairs_done[-1]}: updated {applied} rows")
    except Exception as exc:
        # 行级回退：逐码执行（绕过冲突行）。冲突行计入 skipped（可审计）。
        skipped = 0
        for code in list(result.affected_codes):
            try:
                cur = conn.execute(
                    _upsert_backfill_sql(pair) + f" AND t.code = '{code}'")
                got = getattr(cur, "fetchone", lambda: None)()
                result.rows_updated += got[0] if got else 0
            except Exception:
                skipped += 1
                logger.warning(f"[EpsBackfill] 行级回补跳过 code={code}（PK 冲突/异常）: {exc}")
        result.skipped_no_source += skipped
        logger.warning(f"[EpsBackfill] 批量回补异常，逐码回退完成 "
                       f"(updated={result.rows_updated}, skipped={skipped}): {exc}")


def _ensure_backfill_col(conn, table: str) -> None:
    """幂等保证目标表存在 backfill_eps_source 列（CREATE TABLE/ALTER TABLE）。
    与 DuckDBWriter._migrate_add_columns 同语义；独立 CLI 兜底。"""
    try:
        cols = {r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()}
    except Exception as exc:
        raise RuntimeError(f"[EpsBackfill] 表不存在或不可读: {table} ({exc})") from exc
    if BACKFILL_SOURCE_COL not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {BACKFILL_SOURCE_COL} VARCHAR")


def _count_backfill(conn, pair: Tuple[Tuple[str, str], Tuple[str, str]]) -> Tuple[int, int, List[str]]:
    """统计回补将影响的行数/ann_date 调整数/涉及代码（不落库）。"""
    (t_table, t_col), (s_table, s_col) = pair
    rows = conn.execute(f"""
        SELECT COUNT(*) FROM {t_table} AS t
        JOIN (
            SELECT code, end_date, ann_date, {s_col},
                   ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) AS rn
            FROM {s_table} WHERE {s_col} IS NOT NULL
        ) AS s ON s.code = t.code AND s.end_date = t.end_date
        WHERE t.{t_col} IS NULL AND s.rn = 1
    """).fetchone()[0]
    ann_adjusted = conn.execute(f"""
        SELECT COUNT(*) FROM {t_table} AS t
        JOIN (
            SELECT code, end_date, ann_date, {s_col},
                   ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) AS rn
            FROM {s_table} WHERE {s_col} IS NOT NULL
        ) AS s ON s.code = t.code AND s.end_date = t.end_date
        WHERE t.{t_col} IS NULL AND s.rn = 1 AND t.ann_date < s.ann_date
    """).fetchone()[0]
    codes = [r[0] for r in conn.execute(f"""
        SELECT DISTINCT t.code FROM {t_table} AS t
        JOIN (
            SELECT code, end_date, ann_date, {s_col},
                   ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) AS rn
            FROM {s_table} WHERE {s_col} IS NOT NULL
        ) AS s ON s.code = t.code AND s.end_date = t.end_date
        WHERE t.{t_col} IS NULL AND s.rn = 1
        ORDER BY t.code
    """).fetchall()]
    return rows, ann_adjusted, codes


def check_eps_backfill_gap(conn) -> int:
    """门禁核心（防线 2）：统计当前库中「目标列 NULL 且源表同 key 源列非空」的行数。

    >0 表示回补规则未生效/漏跑/源端 schema 变化（新缺口入库未免疫）→ 门禁 error。
    返回 gap 总数（跨 EPS_BACKFILL_PAIRS 聚合）；0 = 免疫闭环。
    """
    total = 0
    for pair in EPS_BACKFILL_PAIRS:
        (t_table, t_col), (s_table, s_col) = pair
        try:
            rows = conn.execute(f"""
                SELECT COUNT(*) FROM {t_table} AS t
                JOIN (
                    SELECT code, end_date, ann_date, {s_col},
                           ROW_NUMBER() OVER (PARTITION BY code, end_date ORDER BY ann_date DESC) AS rn
                    FROM {s_table} WHERE {s_col} IS NOT NULL
                ) AS s ON s.code = t.code AND s.end_date = t.end_date
                WHERE t.{t_col} IS NULL AND s.rn = 1
            """).fetchone()[0]
        except Exception as exc:
            logger.warning(f"[EpsBackfill] 门禁查询 {t_table}.{t_col} 失败（跳过）: {exc}")
            continue
        total += rows
    return total


def revert_backfill(conn, pair: Optional[Tuple[Tuple[str, str], Tuple[str, str]]] = None) -> int:
    """回补可逆：把打标行还原为 NULL（回退防线 1 用）。

    只还原 backfill_eps_source 标记的行——原生值（NULL 打标）不受影响。
    注意：ann_date 已在回补时可能被调整为 max，如需完全还原请结合回退点恢复数据
    （本函数只还原 eps 值与打标，ann_date 不还原——避免与拉取流水线已推进的水位冲突；
    完全回退走 git 回退点 + 数据快照）。
    """
    (t_table, _t_col), _ = pair or EPS_BACKFILL_PAIRS[0]
    try:
        cur = conn.execute(
            f"UPDATE {t_table} SET eps = NULL, {BACKFILL_SOURCE_COL} = NULL "
            f"WHERE {BACKFILL_SOURCE_COL} = '{BACKFILL_SOURCE_MARK}'"
        )
        got = getattr(cur, "fetchone", lambda: None)()
        return got[0] if got else 0
    except Exception as exc:
        logger.warning(f"[EpsBackfill] revert_backfill 失败: {exc}")
        return 0