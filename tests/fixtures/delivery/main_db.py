# -*- coding: utf-8 -*-
"""测试夹具：小库工厂（DuckDB 主库 / SQLite 因子库 / 客户端 data_root）。

设计约束：
- 主库的 schema 状态 MUST 由**项目自身 DDL**（`qfq_reanchor_schema.init_duckdb_schema`）造出，
  否则 `detect_schema_status` 指纹判不中，fixture 就失去代表性（W0 DoD④）。
- fixture 一律建在 `tmp_path`，**绝不触碰生产 `data/`**。
- 测试可以 import 项目模块（依赖守卫只约束 `quantstudio/delivery/**`）。
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def _duckdb():
    import duckdb

    return duckdb


def build_main_db(
    path: Path,
    *,
    schema: str = "complete_2_1",
    seed: Optional[Sequence[Tuple[str, str, List[Tuple]]]] = None,
) -> Path:
    """造主库 fixture。

    schema 取值：
      ``complete_2_1``  项目 2.1 DDL 初始化（指纹可判 COMPLETE_2_1）
      ``partial_or_mixed`` 建一张 QFQ 版本化表但结构错（缺列）⇒ 指纹判 PARTIAL_OR_MIXED
      ``empty_or_new``  空 DuckDB（无表）⇒ EMPTY_OR_NEW
      ``zero_bytes``    0 字节文件（不可打开，对应修复模式）
    seed: [(table, ddl, rows), ...] 追加业务表数据（用于 stats 测试）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    duckdb = _duckdb()

    if schema == "zero_bytes":
        path.write_bytes(b"")
        return path

    if path.exists():
        path.unlink()

    if schema == "complete_2_1":
        from quantstudio.pipeline.qfq_reanchor_schema import init_duckdb_schema

        con = duckdb.connect(str(path))
        try:
            init_duckdb_schema(con)
            for ddl, rows in _seed_specs(seed):
                con.execute(ddl)
                if rows:
                    _insert(con, ddl, rows)
        finally:
            con.close()
        return path

    if schema == "partial_or_mixed":
        con = duckdb.connect(str(path))
        try:
            # qfq_source_cutover 是 QFQ 版本化表；故意缺列 ⇒ 指纹不匹配
            con.execute("CREATE TABLE qfq_source_cutover(cutover_id VARCHAR)")
        finally:
            con.close()
        return path

    if schema == "empty_or_new":
        con = duckdb.connect(str(path))
        con.close()
        return path

    raise ValueError(f"未知 schema 取值：{schema!r}")


def _seed_specs(seed) -> Iterable[Tuple[str, List[Tuple]]]:
    for item in seed or ():
        yield item[1], list(item[2])


def _table_of(ddl: str) -> str:
    tokens = ddl.replace("(", " ").split()
    for idx, tok in enumerate(tokens):
        if tok.upper() in ("TABLE", "VIEW") and idx + 1 < len(tokens):
            return tokens[idx + 1].strip('"')
    raise ValueError(f"无法从 DDL 解析表名：{ddl[:40]}…")


def _insert(con, ddl: str, rows: List[Tuple]) -> None:
    table = _table_of(ddl)
    placeholders = ", ".join("?" for _ in rows[0])
    con.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)


def add_rows(path: Path, ddl: str, rows: List[Tuple]) -> None:
    """向已有 DuckDB fixture 追加表与行（幂等：表已存在则只插行）。"""
    duckdb = _duckdb()
    con = duckdb.connect(str(path))
    try:
        con.execute(ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        if rows:
            _insert(con, ddl, rows)
    finally:
        con.close()


def build_aux_db(path: Path, *, rows: int = 3, wal: bool = True) -> Path:
    """造 SQLite 因子库 fixture（含少量行；可选 WAL 模式）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    try:
        if wal:
            con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE qfq_price_adj(stock_code TEXT NOT NULL, trade_date TEXT NOT NULL, "
            "adj_factor REAL NOT NULL, PRIMARY KEY(stock_code, trade_date))"
        )
        con.executemany(
            "INSERT INTO qfq_price_adj(stock_code, trade_date, adj_factor) VALUES (?,?,?)",
            [(f"00000{i}", f"2026-10-0{i + 1}", 1.0 + i * 0.01) for i in range(rows)],
        )
        con.commit()
    finally:
        con.close()
    return path


def make_client_data_root(root: Path, *, mode: str, source_main: Optional[Path] = None,
                          source_aux: Optional[Path] = None) -> Path:
    """造客户端 `DATA_ROOT`：mode ∈ {update, repair, install}。

    三种模式都保留 `snapshots/`、`.gitkeep`、`README.md`（FR-017 的既有件）。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "snapshots").mkdir(exist_ok=True)
    (root / ".gitkeep").write_text("", encoding="utf-8")
    (root / "README.md").write_text("# data\n", encoding="utf-8")

    if mode == "install":
        return root
    if mode == "update":
        if source_main is None or source_aux is None:
            raise ValueError("update 模式需要 source_main 与 source_aux")
        shutil.copyfile(source_main, root / "quantstudio.db")
        shutil.copyfile(source_aux, root / "qfq_aux.db")
        return root
    if mode == "repair":
        (root / "quantstudio.db").write_bytes(b"")
        if source_aux is not None:
            shutil.copyfile(source_aux, root / "qfq_aux.db")
        return root
    raise ValueError(f"未知 mode：{mode!r}")


def make_package(bundle_root: Path, **kwargs) -> Path:
    """造交付包：**依赖 W1 的 `export_bundle`**（保证与真实导出口径同源）。

    W0 阶段没有导出内核，故此处显式未实现（tasks.md T011 的 1/3 延后到 W1 T020）。
    """
    raise NotImplementedError(
        "make_package 依赖 export_kernel.export_bundle（W1 T020 落地后补齐）"
    )
