# -*- coding: utf-8 -*-
"""SC-008：跨复权世代豁免 + 传导论证（Polish）。

spec 原文：**不跨复权世代**的导入才要求「同参数回测逐位一致」；
若包携带新的复权世代，历史复权价被合法重算 ⇒ 结果理应改变，本条判据**不适用**，
改用传导论证：`文件级字节一致 ⇒ 表结构与约束一致 ⇒ 取数契约不变 ⇒ 行为等价`，以 SC-013（diff 审计为零）为证。

本测试**只做本测试能做到的那一半**：制造一次真实的「跨世代导入」，断言豁免条件成立，
并逐条断言传导链的**前提事实**（字节一致 / 结构一致）。它**不宣称**"回测结果零变化"——
那是豁免条的结论，不是本测试的读数。
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from quantstudio.delivery import import_kernel as I
from quantstudio.delivery import probe as P
from tests.fixtures.delivery import Env


def _active_cutover(db_path: Path) -> str:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT a.cutover_id, s.source_generation FROM qfq_active_cutover a "
            "JOIN qfq_source_cutover s ON s.cutover_id = a.cutover_id"
        ).fetchone()
        return f"{row[0]}|{row[1]}" if row else ""
    finally:
        con.close()


def _columns(db_path: Path) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        out = {}
        for name in tables:
            out[name] = tuple(sorted(c[0] for c in con.execute(f'DESCRIBE "{name}"').fetchall()))
        return out
    finally:
        con.close()


def _repoint_client_to_new_generation(client_db: Path) -> None:
    """把客户端切成一个"更新的复权世代"（= 导入前状态），使导入必然跨世代。"""
    con = duckdb.connect(str(client_db))
    try:
        con.execute(
            "INSERT INTO qfq_source_cutover(cutover_id,price_source,source_generation,"
            "cutover_time,baseline_version,schema_version,status,created_at,updated_at) VALUES "
            "('c_future','mcp','g_future', TIMESTAMP '2026-10-31 00:00:00','b1','2.1','active',"
            "TIMESTAMP '2026-10-31 00:00:00', TIMESTAMP '2026-10-31 00:00:00')"
        )
        con.execute("DELETE FROM qfq_active_cutover")
        con.execute(
            "INSERT INTO qfq_active_cutover(price_source,cutover_id,activated_at) "
            "VALUES ('mcp','c_future', TIMESTAMP '2026-10-31 00:00:00')"
        )
    finally:
        con.close()


def test_sc008_cross_generation_exemption_chain(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    # 先在"服务端"落下包内世代（c_pkg / g_pkg），再导出包、再复制给客户端
    from tests.fixtures.delivery import seed_cutover

    seed_cutover(server.root / "quantstudio.db", generation="g_pkg", cutover_id="c_pkg")
    bundle = server.make_package()
    client = server.make_client("update")

    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setattr(I, "_acquire_lock", lambda **kw: {"task_id": "test"})
    monkeypatch.setattr(I, "_release_lock", lambda: None)
    monkeypatch.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {}))
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: None)
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 10 ** 12)

    _repoint_client_to_new_generation(client / "quantstudio.db")
    cutover_before = _active_cutover(client / "quantstudio.db")
    columns_before = _columns(client / "quantstudio.db")

    result = I.import_bundle(import_dir=bundle.parent, config_dir=server.config_root, data_root=client)
    assert result.ok, result.reasons

    cutover_after = _active_cutover(client / "quantstudio.db")
    columns_after = _columns(client / "quantstudio.db")

    # ① 豁免条件成立：确实跨了复权世代（旧世代 → 包内世代）
    assert cutover_before != cutover_after, "本用例必须真的跨世代，否则应走 SC-008 主判据而非豁免"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert cutover_after, "导入后客户端必须回到包内世代（active cutover 不可为空）"

    # ② 传导链前提 A：文件级字节一致（新库 == 包内文件）
    from quantstudio.delivery import manifest as M

    for entry in manifest["files"]:
        assert M.sha256_path(client / entry["path"]) == entry["sha256"], f"{entry['path']} 字节不一致"

    # ③ 传导链前提 B：表结构与约束一致（DDL 未因导入而改变）
    assert columns_before == columns_after, "导入前后主库表结构必须完全一致（取数契约不变的前提）"

    # ④ 本用例**不**断言"回测结果零变化"——按 spec 该判据在跨世代时不适用
    #    （若误把它当判据，就会把合法重算误判为回归）
