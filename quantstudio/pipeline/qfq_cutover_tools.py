# -*- coding: utf-8 -*-
"""N-子8 · QFQ 辅助库路由诊断与 cutover 记录换代校正（S1 只读诊断 / S2 执行校正）。

设计红线（2026-11-03 节点⑳ ②审裁定）：
- **只换代，不原地改写**：`qfq_source_cutover` 是不可变路由记录；校正 = 新建一条记录 +
  由 `activate_cutover` 把旧记录置 `superseded`，保留审计链。
- **S1 零写入**：只读连接；不得建文件、不得写库。
- **S2 fail-closed**：备份（b 主 / a 兜底可配置 / c 必做）任一缺失 ⇒ 拒跑；
  CAS（`--expect-aux-db-path`）不符 ⇒ 拒跑且库零变化；**无 force 通道**。
- 审计行含「客户授权依据」字段（②审增补）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from quantstudio.pipeline.qfq_aux_router import (
    AuxDbRouter,
    looks_foreign_absolute,
)

CUTOVER_TABLE = "qfq_source_cutover"
AUDIT_LOG_NAME = "qfq_cutover_repair_audit.log"
REHOST_SNAPSHOT_DIRNAME = "qfq_cutover_repair"


class CutoverToolError(RuntimeError):
    """工具层错误（fail-closed：一切以拒跑结束）。"""


@dataclass
class BackupPlan:
    """备份三档 + 前置余量判定（§10.2）。"""

    db_path: Path
    data_root: Path
    db_size: int
    wal_size: int
    free: int
    need_b: int
    need_a: int
    mode: str = "auto"          # auto | minimal | export
    keep_copy: bool = False     # auto 模式下 free ≥ 2×need_b 才追加 a 档

    @property
    def shortfall(self) -> int:
        return max(0, self.need_b - self.free)

    def as_dict(self) -> Dict:
        return {
            "db_path": str(self.db_path),
            "db_size": self.db_size,
            "wal_size": self.wal_size,
            "free": self.free,
            "need_b": self.need_b,
            "need_a": self.need_a,
            "mode": self.mode,
            "keep_copy": self.keep_copy,
            "shortfall": max(0, self.need_b - self.free),
        }


def _disk_free(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return 0


def plan_backup(db_path: Path, data_root: Path, mode: str = "auto") -> BackupPlan:
    """备份前置余量检查（fail-closed：free < need_b ⇒ 调用方必须拒跑）。"""
    db_path = Path(db_path)
    db_size = db_path.stat().st_size if db_path.exists() else 0
    wal = db_path.with_name(db_path.name + ".wal")
    wal_size = wal.stat().st_size if wal.exists() else 0
    need_b = int(db_size * 1.02) + 512 * 1024 * 1024
    need_a = db_size + wal_size + 512 * 1024 * 1024
    free = _disk_free(data_root)
    # auto：余量够（free ≥ 2×need_b）才追加 a 档导出；minimal：只 b+c；export：强制追加 a
    keep_copy = mode == "export" or (mode == "auto" and free >= 2 * need_b)
    return BackupPlan(db_path=db_path, data_root=data_root, db_size=db_size,
                      wal_size=wal_size, free=free, need_b=need_b, need_a=need_a,
                      mode=mode, keep_copy=keep_copy)


# ── S1 只读诊断 ─────────────────────────────────────────────────────────────
def _read_cutover_rows(db_path: Path) -> Tuple[List[Dict], Optional[str]]:
    import duckdb

    if not db_path.exists():
        return [], f"主库不存在：{db_path}"
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        return [], f"主库不可读（可能被采集占用）：{type(exc).__name__}: {exc}"
    try:
        rows = con.execute(
            f"SELECT * FROM {CUTOVER_TABLE}").fetchall()
        cols = [d[0] for d in con.description]
    except Exception as exc:
        return [], f"读 {CUTOVER_TABLE} 失败：{type(exc).__name__}: {exc}"
    finally:
        con.close()
    out = []
    for row in rows:
        rec = dict(zip(cols, row))
        out.append({k: (str(v) if v is not None and not isinstance(v, (int, float, str)) else v)
                    for k, v in rec.items()})
    return out, None


def _aux_config_path(config_root: Path) -> Optional[Path]:
    for cand in (Path(config_root) / "qfq_aux_paths.json",
                 Path(config_root) / "profiles" / "mcp_only" / "qfq_aux_paths.json"):
        if cand.exists():
            return cand
    return None


def diagnose(*, data_root: Path, config_root: Path) -> Dict:
    """S1：只读诊断（**零写入**）。

    返回：每条 cutover 记录的 `migratable` 判定 + 依据 + 建议新值；以及本地可用的
    辅助库路径（来自 `qfq_aux_paths.json`，相对路径按配置目录解析）。
    """
    data_root = Path(data_root)
    config_root = Path(config_root)
    db_path = data_root / "quantstudio.db"
    rows, err = _read_cutover_rows(db_path)
    cfg = _aux_config_path(config_root)
    local_paths: Dict[str, Dict] = {}
    if cfg is not None:
        try:
            router = AuxDbRouter(main_db=db_path, config_path=cfg)
            for gen in ("mcp-gen1", "xtquant-legacy"):
                try:
                    p = router.path_for(gen, require_exists=False)
                except Exception as exc:
                    local_paths[gen] = {"aux_db_path": None, "exists": False,
                                        "error": f"{type(exc).__name__}: {exc}"}
                    continue
                local_paths[gen] = {"aux_db_path": str(p), "exists": Path(p).is_file()}
        except Exception as exc:
            local_paths = {"_error": f"{type(exc).__name__}: {exc}"}

    windows_host = os.name == "nt"
    records = []
    for rec in rows:
        raw = str(rec.get("aux_db_path") or "")
        foreign = looks_foreign_absolute(raw, windows_host=windows_host)
        gen = str(rec.get("source_generation") or "")
        sugg = local_paths.get(gen) or {}
        exists = bool(sugg.get("exists"))
        reasons = []
        if foreign:
            reasons.append("记录值为异平台绝对路径（迁移前遗留）")
        if not raw:
            reasons.append("记录 aux_db_path 为空")
        elif not foreign and not Path(raw).is_file():
            reasons.append("记录指向的辅助库在本机不存在")
        records.append({
            "cutover_id": rec.get("cutover_id"),
            "price_source": rec.get("price_source"),
            "source_generation": gen,
            "status": rec.get("status"),
            "aux_db_path": raw,
            "migratable": (not foreign) and bool(raw) and Path(raw).is_file(),
            "reasons": reasons,
            "suggested": {
                "aux_db_path": sugg.get("aux_db_path"),
                "exists": exists,
                "source": "qfq_aux_paths.json" if cfg else None,
            },
        })
    return {
        "ok": err is None,
        "error": err,
        "db_path": str(db_path),
        "config_path": str(cfg) if cfg else None,
        "records": records,
        "local_aux": local_paths,
        "writes": 0,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ── S2 执行校正 ─────────────────────────────────────────────────────────────
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _audit_line(log_path: Path, payload: Dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_record_snapshot(conn, out_json: Path) -> Dict:
    rows = conn.execute(f"SELECT * FROM {CUTOVER_TABLE}").fetchall()
    cols = [d[0] for d in conn.description]
    data = [dict(zip(cols, r)) for r in rows]
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    return {"path": str(out_json), "rows": len(data), "sha256": _sha256(out_json)}


def _copy_select_json(conn, out_json: Path) -> Dict:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(out_json).replace("'", "''")
    conn.execute(
        f"COPY (SELECT * FROM {CUTOVER_TABLE}) TO '{escaped}' (FORMAT JSON)")
    return {"path": str(out_json), "sha256": _sha256(out_json)}


def _ensure_capacity(plan: BackupPlan) -> None:
    """备份前置余量闸（fail-closed）：free < need_b ⇒ 拒跑，**无 force 通道**。"""
    if plan.free < plan.need_b:
        raise CutoverToolError(
            f"备份前置余量不足：需要 {plan.need_b} B，可用 {plan.free} B"
            f"（还差 {plan.shortfall} B）。**无 force 通道**，fail-closed 拒跑。")


def _make_backups(*, conn, db_path: Path, data_root: Path, stamp: str,
                  mode: str) -> Dict:
    """备份三档（§10.2 修订版）：

    - **b（主）完整库副本**：DuckDB 是单文件库 ⇒ 复制 `quantstudio.db`(+`-wal`) 即完整还原手段；
      （注：DuckDB **不支持** SQLite 的 `VACUUM INTO`，原设计的 VACUUM 快照在 DuckDB 上不可用）
    - **a（兜底，可配置）逻辑导出**：`EXPORT DATABASE <dir>`（parquet，独立可查；余量允许时追加）
    - **c（必做）记录双份**：记录快照 JSON + `COPY (SELECT * ...) TO ... (FORMAT JSON)`
    """
    plan = plan_backup(db_path, data_root, mode=mode)
    _ensure_capacity(plan)
    bdir = Path(data_root) / "backup" / f"{REHOST_SNAPSHOT_DIRNAME}_{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    made: Dict[str, Dict] = {}

    # a（兜底）：EXPORT DATABASE（逻辑快照，parquet）
    if plan.keep_copy:
        exp = bdir / "export"
        if exp.exists():
            shutil.rmtree(exp, ignore_errors=True)
        conn.execute(f"EXPORT DATABASE '{str(exp).replace(chr(39), chr(39)*2)}'")
        made["a_export"] = {"path": str(exp), "mode": "EXPORT DATABASE",
                            "note": "parquet 逻辑导出，可独立打开查询"}

    # c（必做）：记录双份（需 conn）
    made["c_record_snapshot"] = _write_record_snapshot(
        conn, bdir / "qfq_source_cutover.snapshot.json")
    made["c_record_copy"] = _copy_select_json(
        conn, bdir / "qfq_source_cutover.copy.json")

    # b（主）：完整库副本 —— **必须无打开句柄**（Windows 下 DuckDB 持锁时文件不可复制）
    made["b_full_copy"] = _full_file_copy(db_path, bdir)
    made["_dir"] = str(bdir)
    made["_plan"] = plan.as_dict()
    return made


def _full_file_copy(db_path: Path, bdir: Path) -> Dict:
    """完整库副本（db + wal）。调用方须确保此刻**没有打开该库的文件句柄**。"""
    cp = bdir / "quantstudio.db.copy"
    shutil.copy2(db_path, cp)
    entry = {"path": str(cp), "size": cp.stat().st_size, "sha256": _sha256(cp),
             "mode": "file copy（完整库）"}
    wal = db_path.with_name(db_path.name + ".wal")
    if wal.exists():
        wcp = bdir / (db_path.name + ".wal.copy")
        shutil.copy2(wal, wcp)
        entry["wal_path"] = str(wcp)
        entry["wal_sha256"] = _sha256(wcp)
    return entry


def apply_rehost(*, data_root: Path, config_root: Path, cutover_id: str,
                 expected_aux_db_path: str, new_aux_db_path: str,
                 authorization: str, mode: str = "auto",
                 operator: str = "qs-deliver") -> Dict:
    """S2：执行校正（换代语义；fail-closed）。

    - 备份三档任一缺失 ⇒ 拒跑；CAS 不符 ⇒ 拒跑且库零变化；**无 force 通道**；
    - 只用受管 API：`create_cutover` → `transition_cutover`（planned→prepared→
      baseline_building→baseline_validated）→ `activate_cutover`（旧记录自动 superseded）。
    """
    import duckdb
    from quantstudio.pipeline import qfq_cutover as QC

    if not authorization or not authorization.strip():
        raise CutoverToolError("缺少「客户授权依据」（authorization 不得为空）")
    data_root = Path(data_root)
    db_path = data_root / "quantstudio.db"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = data_root / "logs" / AUDIT_LOG_NAME

    # ① CAS 预检（只读）：记录当前值必须等于期望值
    rows, err = _read_cutover_rows(db_path)
    if err:
        raise CutoverToolError(f"CAS 预检失败（只读）：{err}")
    target = next((r for r in rows if str(r.get("cutover_id")) == cutover_id), None)
    if target is None:
        raise CutoverToolError(f"cutover 不存在：{cutover_id!r}")
    current_aux = str(target.get("aux_db_path") or "")
    if current_aux != expected_aux_db_path:
        _audit_line(log_path, {"action": "rehost", "result": "rejected",
                               "reason": "CAS mismatch", "cutover_id": cutover_id,
                               "expected_aux_db_path": expected_aux_db_path,
                               "actual_aux_db_path": current_aux,
                               "authorization": authorization, "operator": operator})
        raise CutoverToolError(
            f"CAS 不符：库内 aux_db_path={current_aux!r} ≠ 期望 {expected_aux_db_path!r}"
            "（库零变化）")

    # ② 备份（三档）：a/c 需连接；b（文件副本）必须在**关闭句柄**后做（Windows 文件锁）
    plan = plan_backup(db_path, data_root, mode=mode)
    _ensure_capacity(plan)
    bdir = Path(data_root) / "backup" / f"{REHOST_SNAPSHOT_DIRNAME}_{stamp}"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _audit_line(log_path, {"action": "rehost", "result": "rejected",
                               "reason": "backup dir unavailable",
                               "error": f"{type(exc).__name__}: {exc}",
                               "cutover_id": cutover_id,
                               "authorization": authorization, "operator": operator})
        raise CutoverToolError(
            f"备份目录不可用：{bdir}（{type(exc).__name__}: {exc}）——缺备份即拒跑")
    con = duckdb.connect(str(db_path))
    try:
        backups: Dict[str, Dict] = {}
        if plan.keep_copy:
            exp = bdir / "export"
            con.execute(f"EXPORT DATABASE '{str(exp).replace(chr(39), chr(39)*2)}'")
            backups["a_export"] = {"path": str(exp), "mode": "EXPORT DATABASE",
                                   "note": "parquet 逻辑导出，可独立打开查询"}
        backups["c_record_snapshot"] = _write_record_snapshot(
            con, bdir / "qfq_source_cutover.snapshot.json")
        backups["c_record_copy"] = _copy_select_json(
            con, bdir / "qfq_source_cutover.copy.json")
    except Exception as exc:
        _audit_line(log_path, {"action": "rehost", "result": "rejected",
                               "reason": "backup failed",
                               "error": f"{type(exc).__name__}: {exc}",
                               "cutover_id": cutover_id,
                               "authorization": authorization, "operator": operator})
        raise CutoverToolError(
            f"备份失败：{type(exc).__name__}: {exc}——缺备份即拒跑（库未改动）") from exc
    finally:
        con.close()
    try:
        backups["b_full_copy"] = _full_file_copy(db_path, bdir)
    except Exception as exc:
        _audit_line(log_path, {"action": "rehost", "result": "rejected",
                               "reason": "backup copy failed",
                               "error": f"{type(exc).__name__}: {exc}",
                               "cutover_id": cutover_id,
                               "authorization": authorization, "operator": operator})
        raise CutoverToolError(
            f"完整库副本失败：{type(exc).__name__}: {exc}——缺备份即拒跑（库未改动）") from exc
    backups["_dir"] = str(bdir)
    backups["_plan"] = plan.as_dict()

    con = duckdb.connect(str(db_path))
    try:
        # ③ 换代：新建记录 → 状态推进 → 激活（旧记录自动 superseded）
        new_id = f"{cutover_id}_rehost_{stamp}"
        created = QC.create_cutover(
            con, cutover_id=new_id,
            price_source=str(target.get("price_source") or ""),
            source_generation=str(target.get("source_generation") or ""),
            schema_version=str(target.get("schema_version") or ""),
            baseline_version=str(target.get("baseline_version") or ""),
            aux_db_path=new_aux_db_path,
            config_hash=target.get("config_hash"),
            evidence_path=str(backups.get("_dir") or ""),
        )
        for nxt in ("prepared", "baseline_building", "baseline_validated"):
            QC.transition_cutover(con, cutover_id=new_id, new_status=nxt)
        active = QC.activate_cutover(
            con, price_source=str(target.get("price_source") or ""),
            new_cutover_id=new_id, expected_old=cutover_id)
        result = {
            "ok": True,
            "old_cutover_id": cutover_id,
            "new_cutover_id": new_id,
            "old_aux_db_path": current_aux,
            "new_aux_db_path": new_aux_db_path,
            "created": created,
            "active": active,
            "backups": backups,
            "authorization": authorization,
            "operator": operator,
            "ts": stamp,
        }
        _audit_line(log_path, {
            "action": "rehost", "result": "ok",
            "old_cutover_id": cutover_id, "new_cutover_id": new_id,
            "old_aux_db_path": current_aux, "new_aux_db_path": new_aux_db_path,
            "backups": {k: v for k, v in backups.items() if not k.startswith("_")},
            "backup_dir": backups.get("_dir"),
            "authorization": authorization, "operator": operator,
        })
        return result
    except Exception as exc:
        _audit_line(log_path, {"action": "rehost", "result": "failed",
                               "error": f"{type(exc).__name__}: {exc}",
                               "cutover_id": cutover_id,
                               "authorization": authorization, "operator": operator})
        raise
    finally:
        con.close()

AUTO_REHOST_ENV = "QS_QFQ_AUTO_REHOST"


def _local_aux_candidates(config_dir: Path) -> Dict[str, str]:
    """本机可用的辅助库候选（按 `qfq_aux_paths.json` 的 generations 解析，只收**存在**的）。"""
    cfg = _aux_config_path(Path(config_dir))
    if cfg is None:
        return {}
    out: Dict[str, str] = {}
    try:
        router = AuxDbRouter(main_db=None, config_path=cfg)
    except Exception:
        return {}
    for gen in ("mcp-gen1", "xtquant-legacy"):
        try:
            p = router.path_for(gen, require_exists=False)
        except Exception:
            continue
        if p is not None and Path(p).is_file():
            out[gen] = str(p)
    return out


def ensure_local_cutover(conn, *, config_dir: Path,
                         audit_path: Optional[Path] = None,
                         operator: str = "quantstudio",
                         dry_run: bool = False) -> Dict:
    """**自动换代自愈**（D5-e）：把"异平台绝对路径"的 active 路由记录换成本机路径。

    背景（2026-11-04 客户实测）：客户机（macOS）上 `qfq_source_cutover` 的 active 记录
    `aux_db_path` 是**我方开发机（Windows）的绝对路径** ⇒ 路由解析把它当相对路径拼接成
    `data/D:\...` ⇒ 三张核心表取数前崩溃。这条坏记录是我方 2026-08-07 做 B-5 正式 cutover
    时写入并随库流到客户机的 ⇒ **属我方缺陷，应由我方程序自愈，不该让客户手工修**。

    动作（换代，不原地改）：插新记录（aux_db_path=本机候选）→ 状态推进到 baseline_validated
    → activate（旧记录自动 superseded）→ 写审计行。

    只在**两个条件同时成立**时动手（宁可不改，也不猜）：
      ① 记录里的路径确属**异平台绝对路径**（或本机不存在）；
      ② 本机 `qfq_aux_paths.json` 为该世代提供了**存在**的候选。
    否则只报告 `needs_manual`（例如文件真缺失），交人工处理。
    """
    import os as _os

    from . import qfq_cutover as QC

    result: Dict = {"repaired": [], "needs_manual": [], "skipped": [],
                    "dry_run": bool(dry_run), "details": []}
    if str(_os.environ.get(AUTO_REHOST_ENV, "1") or "1").strip().lower() in ("0", "false", "no", "off"):
        result["skipped"].append("disabled by env")
        return result
    try:
        rows = conn.execute(
            "SELECT price_source, cutover_id, source_generation, schema_version, "
            "baseline_version, aux_db_path, config_hash "
            "FROM qfq_source_cutover WHERE status='active'").fetchall()
    except Exception as exc:
        result["skipped"].append(f"cutover 表不可读：{type(exc).__name__}")
        return result
    if not rows:
        result["skipped"].append("无 active 记录")
        return result
    candidates = _local_aux_candidates(Path(config_dir))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for price_source, cutover_id, gen, schema_version, baseline_version, aux, cfg_hash in rows:
        raw = str(aux or "")
        if raw and not looks_foreign_absolute(raw, windows_host=(_os.name == "nt")) \
                and Path(raw).is_file():
            result["skipped"].append(f"{cutover_id}: 本机路径已可用")
            continue
        cand = candidates.get(str(gen))
        if not cand:
            result["needs_manual"].append(
                f"{cutover_id}: 无本机候选（generation={gen}），保持原记录")
            continue
        if cand == raw:
            result["skipped"].append(f"{cutover_id}: 已是本机路径")
            continue
        if dry_run:
            result["details"].append(
                f"{cutover_id}: {raw} → {cand}（dry-run，未执行）")
            continue
        try:
            new_id = f"{cutover_id}_rehost_{stamp}"
            QC.create_cutover(
                conn, cutover_id=new_id, price_source=str(price_source),
                source_generation=str(gen), schema_version=str(schema_version or ""),
                baseline_version=str(baseline_version or ""),
                aux_db_path=cand, config_hash=cfg_hash,
                evidence_path=str(audit_path or ""))
            for nxt in ("prepared", "baseline_building", "baseline_validated"):
                QC.transition_cutover(conn, cutover_id=new_id, new_status=nxt)
            QC.activate_cutover(conn, price_source=str(price_source),
                                new_cutover_id=new_id, expected_old=str(cutover_id))
        except Exception as exc:
            result["needs_manual"].append(
                f"{cutover_id}: 换代失败 {type(exc).__name__}: {exc}")
            continue
        result["repaired"].append(cutover_id)
        result["details"].append(f"{cutover_id}: {raw} → {cand}")
        if audit_path is not None:
            _audit_line(Path(audit_path), {
                "action": "auto-rehost", "result": "ok",
                "old_cutover_id": cutover_id, "new_cutover_id": new_id,
                "old_aux_db_path": raw, "new_aux_db_path": cand,
                "authorization": "程序自愈（异平台遗留路由记录）",
                "operator": operator, "ts": stamp,
            })
    return result
