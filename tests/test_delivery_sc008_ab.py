# -*- coding: utf-8 -*-
"""SC-008 主判据（不跨复权世代）——**降级申报版：传导链强化**（见 §降级理由）。

## 降级理由（实测证据，不猜）
1. 真实引擎 + 真实 provider 已成功启动并跑完（A/B 两次都跑通），但**取数面不可用**：
   `DuckDBMarketDataProvider` 的日线快照 SQL 硬编码 `WHERE time BETWEEN …`，
   而 fixture 子集库的 `stock_daily` 列名是 `trade_date`（`Candidate bindings: "trade_date"`），
   ⇒ 每个交易日都 `Binder Error: Referenced column "time" not found`，引擎报「全程零成交」。
2. 该缺口不是本次特性引入的：fixture 的 DDL 来自本仓 `qfq_reanchor_schema.init_duckdb_schema`
   （造 qfq_* 与少量业务表），**不含** canonical `stock_daily`（`time` 列版本）；
   要造 canonical 业务表得再引入 `DuckDBWriter._init_tables` 的完整 DDL 与约束，属另一件事。
3. 因此本文件按裁定「降级为传导链强化版」执行，**不宣称引擎撮合结果等价**：
   覆盖 = ① schema 状态不变；② 逐表列集不变；③ 逐表抽样行内容逐位不变；④ 三件文件字节一致。

## 未覆盖（须在真实库窗口补）
带交易策略的 A/B 回测（真实引擎撮合 + 真实 canonical 数据）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from quantstudio.delivery import import_kernel as I
from quantstudio.delivery import manifest as M
from quantstudio.delivery import probe as P
from quantstudio.delivery import verify as V
from tests.fixtures.delivery import Env

SAMPLE_LIMIT = 200


def _columns(db_path: Path) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        names = sorted(r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall())
        return {n: tuple(sorted(c[0] for c in con.execute(f'DESCRIBE "{n}"').fetchall())) for n in names}
    finally:
        con.close()


def _row_digests(db_path: Path) -> dict:
    """逐表抽样行内容摘要（ORDER BY 1..n 稳定序，取前 SAMPLE_LIMIT 行）。"""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        out = {}
        con.execute("SET threads TO 1")
        for name, cols in _columns(db_path).items():
            order = ", ".join(str(i + 1) for i in range(len(cols)))
            rows = con.execute(
                f'SELECT * FROM "{name}" ORDER BY {order} LIMIT {SAMPLE_LIMIT}'
            ).fetchall()
            payload = repr([tuple(str(v) for v in row) for row in rows]).encode("utf-8")
            out[name] = (len(rows), hashlib.sha256(payload).hexdigest()[:16])
        return out
    finally:
        con.close()


def test_sc008_no_generation_crossing_transmission_chain(tmp_path, monkeypatch):
    server = Env(tmp_path / "server", monkeypatch)
    bundle = server.make_package()
    client = server.make_client("update")

    monkeypatch.setattr("quantstudio._paths.DATA_ROOT", client, raising=False)
    monkeypatch.setattr(I, "_acquire_lock", lambda **kw: {"task_id": "test"})
    monkeypatch.setattr(I, "_release_lock", lambda: None)
    monkeypatch.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {}))
    monkeypatch.setattr(P, "write_lock_holder", lambda *a, **k: None)
    monkeypatch.setattr(P, "disk_free_bytes", lambda *a, **k: 10 ** 12)

    # 代表性情：子集库必须是 COMPLETE_2_1（W0 能力）
    status_before = V._measure_schema_status(client / "quantstudio.db")
    assert status_before.lower() == "complete_2_1", f"子集库状态不是 COMPLETE_2_1：{status_before}"

    cols_before = _columns(client / "quantstudio.db")
    digests_before = _row_digests(client / "quantstudio.db")

    result = I.import_bundle(import_dir=bundle.parent, config_dir=server.config_root, data_root=client)
    assert result.ok, result.reasons

    status_after = V._measure_schema_status(client / "quantstudio.db")
    cols_after = _columns(client / "quantstudio.db")
    digests_after = _row_digests(client / "quantstudio.db")

    # ① schema 状态不变（不跨世代）
    assert status_after == status_before
    # ② 逐表列集不变
    assert cols_after == cols_before, "导入前后列集必须逐表相等"
    # ③ 逐表抽样行内容逐位不变
    assert digests_after == digests_before, "导入前后抽样行内容必须逐位一致"
    # ④ 三件文件字节一致（与包内 manifest 对齐）
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        assert M.sha256_path(client / entry["path"]) == entry["sha256"]
