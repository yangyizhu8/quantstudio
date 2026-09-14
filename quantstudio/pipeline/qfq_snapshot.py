"""QFQ 全局因子快照——公共工具（T3 抽取，2026-09-14）。

纯抽取零行为变更：实现逐行来自 daemon.py 原
    ResidentCollector._load_qfq_global_snapshot  (daemon.py:1033)
    ResidentCollector._qfq_snapshot_kwargs       (daemon.py:1084)
仅把 self 依赖显式化为入参（甲形态），使**外部调用方无需实例化 ResidentCollector**
即可独立直调（回补执行线等）。

求值顺序保持：原实现在「价格表守卫」之后才解析 qfq_aux_path / writer.db_path，
故 daemon 侧委托保留同序守卫，避免对非价格表多做一次路径解析（潜在副作用面）。

用法::

    from quantstudio.pipeline.qfq_snapshot import qfq_snapshot_kwargs
    kwargs = qfq_snapshot_kwargs(
        table, batch_id,
        price_tables=PRICE_TABLES, qfq_aux_path=aux, main_db_path=main_db)
    aligner.align(df, table, source, **kwargs)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def load_qfq_global_snapshot(
        table: str, *,
        price_tables,
        qfq_aux_path,
        main_db_path) -> Tuple[Optional[dict], Optional[dict]]:
    """从 qfq_aux.db 实时读取全局因子基准（adj_latest / adj_earliest per code）。

    QFQ 复权基准 bug 修复：aligner._apply_qfq 现要求调用方必须传入全局快照，
    禁止批次内 groupby 作基准（流式分片窗口不含最新因子时 front 会被错算成 raw，
    实测 1442 万行被破坏——2026-08-14）。

    - ETF 表读 fund_adj，STOCK 表读 adj_factor（修复 ETF 读错表缺陷）；
    - 实时读 qfq_aux.db（不依赖 adj_factor_snapshot 缓存，修复快照陈旧风险）；
    - 非价格表返回 (None, None)（aligner 直通，不触发 QFQ）。

    Returns:
        (adj_latest_map, adj_earliest_map)：{裸码: 因子值}；非价格表返回 (None, None)。
    """
    if table not in price_tables:
        return None, None
    if not main_db_path:
        return None, None
    aux = qfq_aux_path   # TD-D2：统一路由（双条件，fail-secure legacy）
    if not Path(str(aux)).exists():
        logger.warning(f"[QFQ] qfq_aux.db 不存在: {aux}，无法构建全局因子快照")
        return None, None
    aux_table = "fund_adj" if table.startswith("etf_") else "adj_factor"
    conn = None
    try:
        conn = sqlite3.connect(f"file:{aux}?mode=ro", uri=True, timeout=30)
        # GROUP BY + JOIN 走 (code, time) 索引——相关子查询在 880 万行上需 50s+
        rows = conn.execute(
            f"SELECT f.code, f.adj_factor FROM {aux_table} f "
            f"JOIN (SELECT code, MAX(time) AS mt FROM {aux_table} "
            f"GROUP BY code) m ON f.code = m.code AND f.time = m.mt").fetchall()
        latest_map = {r[0]: float(r[1]) for r in rows if r[1] is not None}
        rows_e = conn.execute(
            f"SELECT f.code, f.adj_factor FROM {aux_table} f "
            f"JOIN (SELECT code, MIN(time) AS mt FROM {aux_table} "
            f"GROUP BY code) m ON f.code = m.code AND f.time = m.mt").fetchall()
        earliest_map = {r[0]: float(r[1]) for r in rows_e if r[1] is not None}
        logger.info(
            f"[QFQ] {table} 全局因子快照已构建（实时读 {aux_table}）："
            f"{len(latest_map)} code")
        return latest_map, earliest_map
    except Exception as exc:
        logger.error(f"[QFQ] 全局因子快照构建失败（{table}）: {exc}")
        return None, None
    finally:
        if conn is not None:
            conn.close()


def qfq_snapshot_kwargs(
        table: str, batch_id: str = "", *,
        price_tables,
        qfq_aux_path,
        main_db_path) -> Dict:
    """价格表 align 调用的全局快照 kwargs（非价格表返回空 dict 直通）。

    用法：aligner.align(..., **qfq_snapshot_kwargs(table, batch_id, ...))
    自动展开为 adj_latest_map=..., adj_earliest_map=...（价格表）或 {}（非价格表）。
    """
    if table not in price_tables:
        return {}
    latest, earliest = load_qfq_global_snapshot(
        table,
        price_tables=price_tables,
        qfq_aux_path=qfq_aux_path,
        main_db_path=main_db_path)
    if latest is None:
        # qfq_aux.db 不可读 → 传空 dict 会触发 aligner fail-fast（正确行为：
        # 宁可任务失败也不写坏 front）
        logger.error(
            f"[{batch_id}] [QFQ] {table} 无法构建全局因子快照，"
            f"align 将 fail-fast（防止批次内基准写坏 front）")
        return {"adj_latest_map": {}, "adj_earliest_map": {}}
    return {"adj_latest_map": latest, "adj_earliest_map": earliest}
