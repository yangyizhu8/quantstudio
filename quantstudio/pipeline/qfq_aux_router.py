"""B-5 SQLite auxiliary-database routing.

The MCP generation owns a complete auxiliary SQLite database.  This module is the
single path resolver used by daemon/CLI code; it deliberately does not create a
missing generation database implicitly.  Creation/initialisation is an explicit
cutover/bootstrap action so a typo cannot silently produce an empty baseline.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path, init_sqlite_schema


class AuxRouteError(RuntimeError):
    """Raised when an auxiliary-generation route is invalid or unsafe."""


@dataclass(frozen=True)
class AuxRoute:
    source_generation: str
    cutover_id: str
    path: Path
    exists: bool


class AuxDbRouter:
    """Resolve ``source_generation`` to an isolated SQLite database path.

    ``explicit_default`` is useful for the legacy ``xtquant-legacy`` path and for
    hermetic tests.  The generation map is loaded from ``qfq_aux_paths.json``;
    relative entries are resolved relative to the configuration file directory.
    """

    def __init__(self, *, main_db: Optional[str | Path] = None,
                 config_path: Optional[str | Path] = None,
                 explicit_default: Optional[str | Path] = None,
                 routes: Optional[Dict[str, str | Path]] = None):
        self.main_db = Path(main_db).resolve() if main_db is not None else None
        self.config_path = Path(config_path).resolve() if config_path is not None else None
        self.explicit_default = Path(explicit_default).resolve() if explicit_default else None
        self._routes: Dict[str, Path] = {}
        if routes:
            self._routes.update({str(k): self._resolve_path(v) for k, v in routes.items()})
        if self.config_path is not None:
            self._load(self.config_path)

    @classmethod
    def from_config_dir(cls, config_dir: Optional[str | Path], *,
                        main_db: Optional[str | Path] = None,
                        explicit_default: Optional[str | Path] = None) -> "AuxDbRouter":
        cdir = Path(config_dir).resolve() if config_dir else None
        candidates = []
        if cdir is not None:
            candidates.append(cdir / "qfq_aux_paths.json")
        if cdir is not None and cdir.name == "mcp_only":
            candidates.append(cdir.parent / "qfq_aux_paths.json")
        for p in candidates:
            if p.exists():
                return cls(main_db=main_db, config_path=p,
                           explicit_default=explicit_default)
        return cls(main_db=main_db, explicit_default=explicit_default)

    def _resolve_path(self, raw: str | Path) -> Path:
        p = Path(raw)
        if p.is_absolute():
            return p.resolve()
        base = self.config_path.parent if self.config_path is not None else None
        if base is None and self.main_db is not None:
            base = self.main_db.parent
        return (base / p if base is not None else p).resolve()

    def _load(self, path: Path) -> None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AuxRouteError(f"无法读取 qfq_aux_paths.json: {path}: {exc}") from exc
        if not isinstance(doc, dict):
            raise AuxRouteError("qfq_aux_paths.json 必须是对象")
        default = doc.get("default")
        if default is not None and self.explicit_default is None:
            self.explicit_default = self._resolve_path(default)
        generations = doc.get("generations", {}) or {}
        if not isinstance(generations, dict):
            raise AuxRouteError("qfq_aux_paths.json.generations 必须是对象")
        for generation, raw in generations.items():
            if not isinstance(generation, str) or not generation.strip():
                raise AuxRouteError(f"非法 source_generation: {generation!r}")
            self._routes[generation.strip()] = self._resolve_path(raw)

    def path_for(self, source_generation: str, *, require_exists: bool = False) -> Path:
        generation = str(source_generation).strip()
        if not generation:
            raise AuxRouteError("source_generation 不能为空")
        path = self._routes.get(generation)
        if path is None and generation == "xtquant-legacy":
            path = self.explicit_default or (aux_db_path(self.main_db) if self.main_db else None)
        if path is None:
            raise AuxRouteError(
                f"未配置 source_generation={generation!r} 的辅助库路径；拒绝回退到其它世代")
        path = Path(path).resolve()
        if require_exists and not path.is_file():
            raise AuxRouteError(
                f"source_generation={generation!r} 的辅助库不存在: {path}；"
                "必须先显式完成 MCP bootstrap/init")
        return path

    def resolve(self, *, source_generation: str, cutover_id: str,
                require_exists: bool = False) -> AuxRoute:
        path = self.path_for(source_generation, require_exists=require_exists)
        return AuxRoute(source_generation=str(source_generation),
                        cutover_id=str(cutover_id), path=path, exists=path.is_file())

    def connect(self, *, source_generation: str, cutover_id: str,
                read_only: bool = False, require_exists: bool = True) -> sqlite3.Connection:
        route = self.resolve(source_generation=source_generation,
                             cutover_id=cutover_id, require_exists=require_exists)
        try:
            conn = sqlite3.connect(str(route.path), timeout=30,
                                   uri=False, check_same_thread=False)
        except Exception as exc:
            raise AuxRouteError(f"打开辅助库失败: {route.path}: {exc}") from exc
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize_explicit(self, *, source_generation: str, cutover_id: str) -> AuxRoute:
        """Explicitly create/init a generation database; never called implicitly."""
        route = self.resolve(source_generation=source_generation,
                             cutover_id=cutover_id, require_exists=False)
        route.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(route.path), timeout=30)
        try:
            init_sqlite_schema(conn)
            conn.commit()
        finally:
            conn.close()
        return self.resolve(source_generation=source_generation,
                            cutover_id=cutover_id, require_exists=True)

    @property
    def routes(self) -> Dict[str, Path]:
        return dict(self._routes)


# ===========================================================================
# TD-D2 统一运行时路由（docs/mcp_migration/wp7e3-TD-D2-task.md v1.1 §2.1）
# ===========================================================================
def resolve_runtime_aux_path(*, main_db, duckdb_read=None,
                             price_source: str = "mcp",
                             config_path=None,
                             logger=None) -> tuple:
    """统一 QFQ 因子库路由：写入锚/读取锚/防线监测/refresher 全部走这里。

    双条件（全部满足才返回世代库，任一不满足/不可判定 → fail-secure legacy）：

      ① ⑤ 释放门：qfq_aux_paths.json 顶层 ``"released": true``
         （缺省/缺文件/解析异常 → false）。
         信号形式说明（任务书 §3 步骤 2 前置核实结论）：release_watermark 的
         授权证据当前落 evidence 文件、无主库可查询信号，故按任务书退化方案
         采用显式配置开关——⑤ 释放流程完成后置 ``released: true``，
         仍满足"切配置不切代码"。
      ② active cutover：主库 ``qfq_active_cutover`` 存在记录（经 duckdb_read
         执行只读 SQL；无回调/查询异常 → 不可判定 → legacy）。
         ⚠️ 当前真实态（2026-08-15）：b6_formal_20260807_v2 已 active
         （v6.7.43），条件②为真——因此条件①是防误切的必要条件，缺它
         路由会立即指向空 gen1 库（0 行因子 → align fail-fast / 防线退化）。

    防仅凭配置误切：即使有人误置 released=true，只要主库无 active cutover
    记录仍返回 legacy（两条件独立相与）。

    返回 (path, reason)：reason 供审计日志（"gen1:<generation>" /
    "legacy:released=false" / "legacy:no_active_cutover" / "legacy:fail-secure:<why>"）。
    """
    from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path as _legacy
    legacy = _legacy(main_db)
    try:
        cfg = Path(config_path) if config_path else None
        if cfg is None or not cfg.exists():
            return legacy, "legacy:fail-secure:no_aux_paths_config"
        doc = json.loads(cfg.read_text(encoding="utf-8"))
        released = bool(doc.get("released", False)) if isinstance(doc, dict) else False
        if not released:
            return legacy, "legacy:released=false"
        if duckdb_read is None:
            return legacy, "legacy:fail-secure:no_duckdb_read"
        try:
            rows = duckdb_read(
                "SELECT a.cutover_id, c.source_generation FROM qfq_active_cutover a "
                "JOIN qfq_source_cutover c ON c.cutover_id=a.cutover_id "
                "WHERE a.price_source=? LIMIT 1", [str(price_source)])
        except Exception as exc:
            if logger:
                logger.warning(f"[QFQ-AuxRoute] active cutover 查询失败，fail-secure "
                               f"回 legacy: {exc}")
            return legacy, f"legacy:fail-secure:cutover_query_error"
        if not rows:
            return legacy, "legacy:no_active_cutover"
        generation = str(rows[0][1])
        router = AuxDbRouter(main_db=main_db, config_path=cfg)
        path = router.path_for(generation)
        return path, f"gen1:{generation}"
    except Exception as exc:
        if logger:
            logger.warning(f"[QFQ-AuxRoute] 路由解析异常，fail-secure 回 legacy: {exc}")
        return legacy, "legacy:fail-secure:exception"
