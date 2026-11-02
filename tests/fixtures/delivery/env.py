# -*- coding: utf-8 -*-
"""W1/W2/W3 共用的测试环境装配（`tmp_path` 内造库造配置造包，绝不触生产）。

设计要点：
- 主库走项目自身 DDL（`init_duckdb_schema`）⇒ `detect_schema_status` 能识别；
- 默认把 `_acquire_lock`/`_release_lock` 换成 no-op、把 daemon/写锁探测换成"无" ⇒
  测试与真实进程解耦（W1 已实测：这套装配在 daemon 运行中也能稳定跑）；
- `make_package()` 走**真实导出内核** ⇒ 包与导出口径同源（W0 时因内核未落地而延后，现补齐）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .main_db import build_aux_db, build_main_db

TABLE_MAP: Dict[str, Dict] = {
    "stock_daily": {
        "source_table": "stock_daily",
        "target_table": "stock_daily",
        "source": "questdb",
        "freq": "daily",
        "dataset_kind": "passthrough",
        "task_name": "mcp_stock_daily",
        "watermark_basis": "trade_date",
    },
    # 配置登记但库里不存在 ⇒ 必须被表集裁剪（Q2：map ∩ 实际存在）
    "stock_minute": {
        "source_table": "stock_minute",
        "target_table": "stock_minute",
        "source": "questdb",
        "freq": "minute",
        "task_name": "mcp_stock_minute",
        "watermark_basis": "ingest_time",
    },
}


def write_config(
    config_root: Path,
    *,
    released: bool = False,
    generations: Optional[Dict[str, str]] = None,
    write_aux_paths: bool = True,
) -> Path:
    prof = Path(config_root) / "profiles" / "mcp_only"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "questdb_table_map.json").write_text(
        json.dumps({"schema_version": 1, "tables": TABLE_MAP}, ensure_ascii=False),
        encoding="utf-8",
    )
    if write_aux_paths:
        doc: Dict[str, Any] = {"released": bool(released)}
        if generations:
            doc["generations"] = dict(generations)
        (prof / "qfq_aux_paths.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
    (prof / "collector_tasks.json").write_text(
        json.dumps({"tasks": {"mcp_stock_daily": {}, "mcp_stock_minute": {}}}),
        encoding="utf-8",
    )
    return Path(config_root)


def seed_business_table(path: Path, *, watermark: bool = True) -> None:
    """给 `complete_2_1` 主库加一张业务表 + 水位行（`last_date` 为 epoch ms）。"""
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS stock_daily(trade_date DATE, close DOUBLE)")
        con.execute("INSERT INTO stock_daily VALUES ('2026-10-29',1.0),('2026-10-30',2.0)")
        if watermark:
            con.execute(
                "INSERT INTO source_watermark(source,table_name,freq,last_date,last_batch_id,"
                "updated_at,source_generation,cutover_id) VALUES "
                "('questdb','stock_daily','daily', 1793308800000, 'b1', "
                "TIMESTAMP '2026-10-30 00:00:00', 'legacy', 'c0')"
            )
    finally:
        con.close()


def seed_cutover(
    path: Path,
    *,
    generation: str = "b6_formal_20260807_v2",
    cutover_id: str = "c1",
    price_source: str = "mcp",
) -> None:
    """给主库写入 active cutover（让 `resolve_runtime_aux_path` 走到 gen1 分支）。"""
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO qfq_source_cutover(cutover_id,price_source,source_generation,"
            "cutover_time,baseline_version,schema_version,status,created_at,updated_at) VALUES "
            "(?,?,?, TIMESTAMP '2026-10-30 00:00:00','b1','2.1','active',"
            "TIMESTAMP '2026-10-30 00:00:00', TIMESTAMP '2026-10-30 00:00:00')",
            [cutover_id, price_source, generation],
        )
        con.execute(
            "INSERT INTO qfq_active_cutover(price_source,cutover_id,activated_at) VALUES "
            "(?,?, TIMESTAMP '2026-10-30 00:00:00')",
            [price_source, cutover_id],
        )
    finally:
        con.close()


def rewrite_manifest(bundle: Path, mutate) -> Dict:
    """改写包内 manifest（模拟"另一版程序导出的包"或 estimate 降级包）。"""
    import json as _json

    path = Path(bundle) / "manifest.json"
    doc = _json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(
        _json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return doc


def refresh_manifest_files(bundle: Path) -> Dict:
    """按磁盘实际重算 manifest.files 的 bytes/sha256（用于构造"内容合法但 schema 不对"的包）。"""
    import hashlib as _hashlib
    import json as _json

    path = Path(bundle) / "manifest.json"
    doc = _json.loads(path.read_text(encoding="utf-8"))
    for entry in doc["files"]:
        target = Path(bundle) / entry["path"]
        data = target.read_bytes()
        entry["bytes"] = len(data)
        entry["sha256"] = _hashlib.sha256(data).hexdigest()
    doc["export"]["copy_bytes_total"] = sum(e["bytes"] for e in doc["files"])
    path.write_text(
        _json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return doc


class Env:
    """一套完整的临时环境：data_root（主库+两代因子库）+ config + export_dir。"""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        schema: str = "complete_2_1",
        watermark: bool = True,
        released: bool = False,
        write_aux_paths: bool = True,
    ):
        self.tmp = Path(tmp_path)
        self.root = self.tmp / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        self.main_db = build_main_db(self.root / "quantstudio.db", schema=schema)
        if schema == "complete_2_1" and watermark:
            seed_business_table(self.main_db, watermark=watermark)
        build_aux_db(self.root / "qfq_aux.db")
        build_aux_db(self.root / "qfq_aux_mcp_gen1.db", rows=1)
        self.config_root = write_config(
            self.tmp / "config", released=released, write_aux_paths=write_aux_paths
        )
        self.export_dir = self.tmp / "export"
        self.monkeypatch = monkeypatch
        self.patch_runtime()

    def patch_runtime(self) -> None:
        from quantstudio.delivery import export_kernel as E
        from quantstudio.delivery import probe as P

        mp = self.monkeypatch
        mp.setattr("quantstudio._paths.DATA_ROOT", self.root, raising=False)
        mp.setattr(E, "_acquire_lock", lambda **kw: {"task_id": "test"})
        mp.setattr(E, "_release_lock", lambda: None)
        mp.setattr(P, "daemon_running", lambda *a, **k: P.DaemonState(False, {"status_file": "absent"}))
        mp.setattr(P, "write_lock_holder", lambda *a, **k: None)

    # ── 导出 ────────────────────────────────────────────────────────────────
    def export(self, **kwargs):
        from quantstudio.delivery import export_kernel as E

        return E.export_bundle(
            export_dir=kwargs.pop("export_dir", self.export_dir),
            config_dir=kwargs.pop("config_dir", self.config_root),
            data_root=kwargs.pop("data_root", self.root),
            **kwargs,
        )

    def make_package(self, name: str = "pkg") -> Path:
        """造一个交付包并返回**包目录**（走真实导出内核）。"""
        export_dir = self.tmp / name
        result = self.export(export_dir=export_dir)
        return result.bundle_dir

    # ── 校验 ────────────────────────────────────────────────────────────────
    def verify(self, import_dir: Optional[Path] = None, **kwargs):
        """对 `import_dir`（默认 = 包目录的父目录）做只读校验。"""
        from quantstudio.delivery import verify as V

        return V.verify_bundle(
            import_dir=Path(kwargs.pop("import_dir", import_dir or self.export_dir)),
            config_dir=kwargs.pop("config_dir", self.config_root),
            data_root=kwargs.pop("data_root", self.root),
            **kwargs,
        )

    # ── 客户端 ──────────────────────────────────────────────────────────────
    def make_client(self, mode: str = "update", name: str = "client") -> Path:
        from .main_db import make_client_data_root

        return make_client_data_root(
            self.tmp / name,
            mode=mode,
            source_main=self.root / "quantstudio.db",
            source_aux=self.root / "qfq_aux.db",
        )

    def client_fingerprint(self, client_root: Path) -> Dict[str, Any]:
        """SC-005 的六元组指纹（路径/大小/mtime_ns/sha256/条目数/整体哈希）。"""
        import hashlib

        root = Path(client_root)
        entries = []
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_file():
                stat = path.stat()
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                entries.append(
                    {
                        "path": rel,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": sha,
                    }
                )
                digest.update(f"{rel}|{stat.st_size}|{sha}".encode("utf-8"))
            else:
                entries.append({"path": rel, "dir": True})
                digest.update(f"{rel}|dir".encode("utf-8"))
        return {"entries": entries, "count": len(entries), "digest": digest.hexdigest()}


def make_package(
    root: Path, config_root: Path, export_dir: Path, *, monkeypatch
) -> Path:
    """便捷入口：给定 data_root/config/export_dir 造包（T011 的 make_package）。"""
    env = Env.__new__(Env)  # 复用 Env 的装配逻辑但不重建库
    env.tmp = Path(root).parent
    env.root = Path(root)
    env.config_root = Path(config_root)
    env.export_dir = Path(export_dir)
    env.monkeypatch = monkeypatch
    env.patch_runtime()
    return env.make_package("manual_pkg")


def delete_package_part(bundle: Path, name: str) -> None:
    Path(bundle, name).unlink()


def copy_package(bundle: Path, target: Path) -> Path:
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(bundle, target)
    return target
