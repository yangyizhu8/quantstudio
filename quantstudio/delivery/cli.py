# -*- coding: utf-8 -*-
"""CLI 薄壳：`qs-deliver`（单一内核 + 薄壳，壳层零业务判断）。

本轮先落地 **`export` / `status`** 两个子命令（L3 窗口需要 CLI 两次串行导出）；
`verify` / `import` / `recover` / `set` 随 W3/W4 补齐（契约见 `contracts/cli.md`）。

退出码（契约 §2）：0 成功 / 2 用法 / 3 前置拒绝 / 4 包问题 / 5 配置 / 6 版本 /
7 残留 journal / 8 内部错误 / 9 失败已回滚（或产出不合格）/ 10 回滚失败 / 130 用户中断。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config as delivery_config
from . import probe as P
from . import verify as V
from .constants import Exit
from .export_kernel import (
    ExportAborted,
    ExportFailed,
    ExportRefused,
    ExportResult,
    export_bundle,
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _emit_json(subcommand: str, ok: bool, exit_code: int, payload: Any) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "ok": ok,
                "exit_code": exit_code,
                "subcommand": subcommand,
                "result": _jsonable(payload),
            },
            ensure_ascii=False,
        )
        + "\n"
    )


class _Progress:
    """默认进度：每 2 s 一行走 stderr（stdout 只留结果，便于 `| jq`）。"""

    def __init__(self, quiet: bool = False):
        import time

        self.quiet = quiet
        self.last = 0.0
        self.last_phase = ""
        self.time = time

    def __call__(self, done: int, total: int, phase: str) -> None:
        now = self.time.monotonic()
        if phase != self.last_phase:
            self.last_phase = phase
            sys.stderr.write(f"[{phase}]\n")
            self.last = now
            return
        if self.quiet or total <= 0:
            return
        if now - self.last < 2.0:
            return
        self.last = now
        pct = done * 100.0 / total if total else 0.0
        sys.stderr.write(f"[{phase}] {done / 2**30:.2f} GiB / {total / 2**30:.2f} GiB ({pct:.1f}%)\n")


def _fmt_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{value} B"


def _cmd_export(args: argparse.Namespace) -> int:
    progress = _Progress(quiet=args.quiet)
    try:
        result: ExportResult = export_bundle(
            export_dir=Path(args.export_dir),
            config_dir=Path(args.config_dir) if args.config_dir else None,
            progress=progress,
            accept_sync_dir=args.accept_sync_dir,
            verify_hashes=args.verify_hashes,
        )
    except ExportRefused as exc:
        code = Exit.REFUSED
        if args.json:
            _emit_json("export", False, code, {"code": exc.code, "message": exc.message, "readings": exc.readings})
        else:
            sys.stderr.write(f"[refused:{exc.code}] {exc.message}\n")
            if exc.readings:
                sys.stderr.write("  " + json.dumps(exc.readings, ensure_ascii=False) + "\n")
        return code
    except ExportAborted as exc:
        if args.json:
            _emit_json("export", False, Exit.INTERRUPTED, {"message": str(exc)})
        else:
            sys.stderr.write(f"[aborted] {exc}\n")
        return Exit.INTERRUPTED
    except ExportFailed as exc:
        if args.json:
            _emit_json("export", False, Exit.INTERNAL, {"phase": exc.phase, "message": str(exc)})
        else:
            sys.stderr.write(f"[failed:{exc.phase}] {exc}\n")
        return Exit.INTERNAL
    except Exception as exc:  # 不静默
        if args.json:
            _emit_json("export", False, Exit.INTERNAL, {"message": f"{type(exc).__name__}: {exc}"})
        else:
            sys.stderr.write(f"[error] {type(exc).__name__}: {exc}\n")
        return Exit.INTERNAL

    code = Exit.OK if result.qualified else Exit.ROLLED_BACK
    if args.json:
        _emit_json("export", result.qualified, code, result)
        return code
    sys.stdout.write(f"[delivery] export  → {result.bundle_dir}\n")
    for meta in result.files:
        sys.stdout.write(
            f"  {meta['path']:<22} {_fmt_bytes(meta['bytes']):>12}  "
            f"sha256={meta['sha256'][:16]}…  ok\n"
        )
    red = result.self_check["red_line"]
    sys.stdout.write(
        f"  红线自检: {'通过' if red['zero_leak'] else '不通过'}  "
        f"(白名单外={red['outside_whitelist']} env={red['env_hits']} shm={red['shm_hits']} log={red['log_hits']})\n"
    )
    sys.stdout.write(f"  耗时 {result.duration_sec} s   产出目录: {result.bundle_dir}\n")
    for warning in result.warnings:
        sys.stderr.write(f"[warn] {warning}\n")
    return code


def _cmd_status(args: argparse.Namespace) -> int:
    cfg_root = Path(args.config_dir) if args.config_dir else None
    cfg, warnings = delivery_config.load(cfg_root)
    root = P.data_root()
    try:
        daemon = P.daemon_running()
        daemon_doc = {
            "running": daemon.running,
            "pid": daemon.detail.get("pid"),
            "started_at": daemon.detail.get("started_at"),
        }
    except Exception as exc:
        daemon_doc = {"running": None, "error": f"{type(exc).__name__}: {exc}"}
    holder = P.write_lock_holder()
    payload = {
        "data_root": str(root),
        "export_dir": cfg.export_dir,
        "import_dir": cfg.import_dir,
        "daemon": daemon_doc,
        "write_lock": holder,
        "warnings": warnings,
    }
    if args.json:
        _emit_json("status", True, Exit.OK, payload)
        return Exit.OK
    sys.stdout.write(f"DATA_ROOT   : {root}\n")
    sys.stdout.write(f"export_dir  : {cfg.export_dir or '(未选择)'}\n")
    sys.stdout.write(f"import_dir  : {cfg.import_dir or '(未选择)'}\n")
    running = daemon_doc.get("running")
    state = "unknown" if running is None else ("running" if running else "not running")
    sys.stdout.write(f"daemon      : {state}")
    if daemon_doc.get("pid"):
        sys.stdout.write(f" (pid={daemon_doc['pid']}, started_at={daemon_doc.get('started_at')})")
    sys.stdout.write("\n")
    sys.stdout.write(f"write_lock  : {'free' if not holder else holder}\n")
    for warning in warnings:
        sys.stdout.write(f"[warn] {warning}\n")
    return Exit.OK



def _state_counts(tables) -> dict:
    """逐表四态汇总（一行 groupby）：固定键序，零值也显示，便于肉眼比对两次运行。"""
    order = (
        "OK",
        "BEHIND",
        "AHEAD",
        "MISSING",
        "UNKNOWN-SOURCE",
        "UNKNOWN-BASIS",
        "NO-WATERMARK-SYSTEM",
        "EMPTY",
    )
    counts = {key: 0 for key in order}
    for row in tables or ():
        state = str(row.get("state") or "")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _attention_rows(tables, limit: int = 8):
    """需关注的表（BEHIND / AHEAD / MISSING）：带上水位与数据的日期，直接指出该修哪张。"""
    picked = [r for r in (tables or ()) if r.get("state") in ("BEHIND", "AHEAD", "MISSING")]
    picked.sort(key=lambda r: str(r.get("table") or ""))
    return picked[:limit], max(0, len(picked) - limit)


def _cmd_verify(args: argparse.Namespace) -> int:
    """只读校验交付包（FR-010）：逐文件哈希 + schema + 路由 + 逐表四态。"""
    import_dir = Path(args.import_dir)
    cfg_root = Path(args.config_dir) if args.config_dir else None
    report = V.verify_bundle(
        import_dir=import_dir,
        config_dir=cfg_root,
        data_root=P.data_root(),
        progress=_Progress(quiet=args.quiet),
    )
    payload = {
        "ok": report.ok,
        "package_dir": str(report.package_dir) if report.package_dir else None,
        "failure_kind": report.failure_kind,
        "message": report.message,
        "reasons": report.reasons,
        "warnings": report.warnings,
        "manifest_schema_version": report.manifest_schema_version,
        "files_checked": report.files_checked,
        "tables": report.tables,
        "state_counts": _state_counts(report.tables),
        "attention": [
            r for r in report.tables if r.get("state") in ("BEHIND", "AHEAD", "MISSING")
        ],
        "table_set": report.table_set,
        "elapsed_sec": report.elapsed_sec,
    }
    if args.json:
        _emit_json("verify", report.ok, report.exit_code, payload)
        return report.exit_code
    if report.ok:
        counts = _state_counts(report.tables)
        sys.stdout.write(f"校验通过：{report.package_dir}\n")
        sys.stdout.write(f"  文件 {report.files_checked} 个；表 {len(report.tables)} 张\n")
        sys.stdout.write("  状态汇总：" + "  ".join(f"{k}={v}" for k, v in counts.items() if v) + "\n")
        picked, rest = _attention_rows(report.tables)
        if picked:
            sys.stdout.write("  需关注（水位 vs 数据）：\n")
            for row in picked:
                sys.stdout.write(
                    f"    - {row.get('table')}: {row.get('state')}"
                    f"（水位 {row.get('watermark_last_date') or '-'}"
                    f" / 数据 {row.get('max_date') or '-'}"
                    f" / 基准列 {row.get('date_basis_col')}）\n"
                )
            if rest:
                sys.stdout.write(f"    …另有 {rest} 张，详见 --json 的 attention 字段\n")
    else:
        sys.stderr.write(f"校验失败[{report.failure_kind}]：{report.message}\n")
        for reason in report.reasons:
            sys.stderr.write(f"  - {reason}\n")
    for warning in report.warnings:
        sys.stderr.write(f"[warn] {warning}\n")
    return report.exit_code


def _cmd_import(args: argparse.Namespace) -> int:
    """导入交付包（FR-013 九步）：dry-run 只做只读校验，不取锁不写盘。"""
    from .import_kernel import import_bundle

    cfg_root = Path(args.config_dir) if args.config_dir else None
    result = import_bundle(
        import_dir=Path(args.import_dir),
        config_dir=cfg_root,
        data_root=P.data_root(),
        dry_run=bool(args.dry_run),
        allow_repair=not bool(args.no_repair),
        progress=_Progress(quiet=args.quiet),
    )
    payload = {
        "ok": result.ok,
        "mode": result.mode,
        "dry_run": bool(args.dry_run),
        "failure_kind": result.failure_kind,
        "reasons": result.reasons,
        "warnings": result.warnings,
        "backup": result.backup,
        "reverify": {
            "can_incremental": result.reverify.get("can_incremental"),
            "summary": result.reverify.get("summary"),
            "tables": result.reverify.get("tables"),
        },
        "baseline": result.baseline,
        "forensic": result.forensic,
        "journal_finalized": result.journal_finalized,
        "elapsed_sec": result.elapsed_sec,
    }
    if args.json:
        _emit_json("import", result.ok, result.exit_code, payload)
        return result.exit_code
    if result.ok:
        sys.stdout.write(f"导入完成（模式 {result.mode}）\n")
        if result.backup.get("dir"):
            sys.stdout.write(f"  备份：{result.backup['dir']}（方式 {result.backup.get('method')}）\n")
        sys.stdout.write(f"  {result.reverify.get('summary') or ''}\n")
        if result.forensic:
            sys.stdout.write(f"  取证：{result.forensic}\n")
    else:
        sys.stderr.write(f"导入未完成[{result.failure_kind}]\n")
        for reason in result.reasons:
            sys.stderr.write(f"  - {reason}\n")
    note = result.baseline.get("note")
    if note:
        sys.stderr.write(f"[warn] {note}\n")
    for warning in result.warnings:
        sys.stderr.write(f"[warn] {warning}\n")
    return result.exit_code


def _cmd_recover(args: argparse.Namespace) -> int:
    """journal 恢复二选一（FR-036）：continue 完成 / rollback 回滚。"""
    from .import_kernel import recover_journal

    cfg_root = Path(args.config_dir) if args.config_dir else None
    result = recover_journal(action=args.action, config_dir=cfg_root, data_root=P.data_root())
    payload = {
        "ok": result.ok,
        "journal_found": result.journal_found,
        "phase_before": result.phase_before,
        "action": result.action,
        "restored_files": result.restored_files,
        "deleted_created_files": result.deleted_created_files,
        "reasons": result.reasons,
    }
    if args.json:
        _emit_json("recover", result.ok, result.exit_code, payload)
        return result.exit_code
    if not result.journal_found:
        sys.stderr.write("无残留 journal：无需恢复\n")
    elif result.ok:
        sys.stdout.write(f"恢复完成（{result.action}，原相位 {result.phase_before}）\n")
        if result.restored_files:
            sys.stdout.write(f"  已还原：{result.restored_files}\n")
    else:
        sys.stderr.write("恢复失败\n")
        for reason in result.reasons:
            sys.stderr.write(f"  - {reason}\n")
    return result.exit_code


def _cmd_set(args: argparse.Namespace) -> int:
    """写入两个路径（仅这两项；原子写、失败即报）。"""
    cfg_root = Path(args.config_dir) if args.config_dir else None
    cfg, warnings = delivery_config.load(cfg_root)
    if args.export_dir is not None:
        cfg.export_dir = args.export_dir or None
    if args.import_dir is not None:
        cfg.import_dir = args.import_dir or None
    path = delivery_config.save(cfg, cfg_root)
    payload = {
        "config_path": str(path),
        "export_dir": cfg.export_dir,
        "import_dir": cfg.import_dir,
        "warnings": warnings,
    }
    if args.json:
        _emit_json("set", True, Exit.OK, payload)
        return Exit.OK
    sys.stdout.write(f"已写入 {path}\n")
    sys.stdout.write(f"  export_dir = {cfg.export_dir}\n")
    sys.stdout.write(f"  import_dir = {cfg.import_dir}\n")
    for warning in warnings:
        sys.stderr.write(f"[warn] {warning}\n")
    return Exit.OK


def _cmd_unlock(args: argparse.Namespace) -> int:
    """写锁诊断与自助回收（A-3）。

    硬条件（用户裁定 2026-11-01）：
      - **禁止裸删锁文件**：回收必须经 `try_reclaim_stale()`（CAS 三字段比对 + 审计留痕）；
      - `--force` 仅在诊断判定可回收（pid 已死 / 心跳陈旧）时执行；持有者存活一律拒绝。
    """
    from quantstudio.pipeline import snapshot_lock as SL

    holder = SL.read_holder()
    if not holder:
        if args.json:
            _emit_json("unlock", True, Exit.OK, {"held": False})
        else:
            sys.stdout.write("写锁目录干净：无持有者\n")
        return Exit.OK

    diag = SL.diagnose_holder(holder)
    payload = {
        "held": True,
        "holder": holder,
        "diagnosis": diag,
        "reclaimable": bool(diag.get("reclaimable")),
    }
    if not args.force:
        if args.json:
            _emit_json("unlock", True, Exit.OK, payload)
            return Exit.OK
        sys.stdout.write(f"写锁被持有：{holder.get('task_id')}\n")
        sys.stdout.write(f"  pid={holder.get('pid')}（心跳年龄 "
                         f"{diag.get('heartbeat_age')}s / host={holder.get('host')}）\n")
        sys.stdout.write(f"  判定：{diag.get('verdict')}｜可回收={payload['reclaimable']}"
                         f"{('｜' + str(diag.get('detail'))) if diag.get('detail') else ''}\n")
        if payload["reclaimable"]:
            sys.stdout.write("  处置：确认无写入后执行 `qs-deliver unlock --force`\n")
        else:
            sys.stdout.write("  处置：持有者仍存活或不可判定 ⇒ 先停该任务（勿强删锁文件）\n")
        return Exit.OK

    if not payload["reclaimable"]:
        sys.stderr.write("拒绝回收：诊断判定持有者仍存活或不可判定（见上）\n")
        return Exit.REFUSED
    try:
        SL.try_reclaim_stale(holder, diag, reclaimer_task="qs-deliver unlock")
    except Exception as exc:
        sys.stderr.write(f"回收失败：{type(exc).__name__}: {exc}\n")
        return Exit.REFUSED
    still = SL.read_holder()
    ok = not bool(still)
    payload["reclaimed"] = ok
    payload["holder_after"] = still
    if args.json:
        _emit_json("unlock", ok, Exit.OK if ok else Exit.REFUSED, payload)
        return Exit.OK if ok else Exit.REFUSED
    sys.stdout.write("已回收写锁（经 CAS + 审计留痕）\n" if ok else "回收未生效：锁仍被持有\n")
    return Exit.OK if ok else Exit.REFUSED


def _cmd_cutover_diag(args: argparse.Namespace) -> int:
    """S1 只读诊断（**零写入**）：报告 cutover 记录的跨平台可迁移性。"""
    from quantstudio.pipeline.qfq_cutover_tools import diagnose

    report = diagnose(data_root=Path(args.data_root), config_root=Path(args.config_dir))
    if args.json:
        _emit_json("cutover-diag", bool(report.get("ok")), Exit.OK if report.get("ok") else Exit.REFUSED, report)
        return Exit.OK if report.get("ok") else Exit.REFUSED
    if report.get("error"):
        sys.stdout.write(f"诊断未完成：{report['error']}\n")
        return Exit.REFUSED
    sys.stdout.write("QFQ 路由诊断（只读，未写入任何内容）\n")
    for rec in report.get("records") or []:
        flag = "可迁移" if rec.get("migratable") else "不可迁移"
        sys.stdout.write(f"  [{flag}] {rec.get('cutover_id')} ({rec.get('source_generation')}, {rec.get('status')})\n")
        sys.stdout.write(f"        aux_db_path = {rec.get('aux_db_path')}\n")
        for reason in rec.get("reasons") or []:
            sys.stdout.write(f"        · {reason}\n")
        sug = rec.get("suggested") or {}
        if sug.get("aux_db_path"):
            sys.stdout.write(f"        建议值 = {sug['aux_db_path']}（存在：{sug.get('exists')}）\n")
    sys.stdout.write(f"写入次数：{report.get('writes')}\n")
    return Exit.OK


def _cmd_cutover_rehost(args: argparse.Namespace) -> int:
    """S2 执行校正（换代语义；默认 dry-run；fail-closed）。"""
    from quantstudio.pipeline.qfq_cutover_tools import CutoverToolError, apply_rehost

    if not args.apply:
        sys.stdout.write(
            "dry-run：将执行（换代）——\n"
            f"  旧记录 {args.cutover_id} 的 aux_db_path = {args.expect_aux_db_path}\n"
            f"  新 aux_db_path = {args.new_aux_db_path}\n"
            "  备份三档：b(完整库副本, 主) + a(parquet 逻辑导出目录, 余量≥2×need_b 时追加) + c(记录双份, 必做)\n"
            "  客户授权依据 = " + (args.authorization or "(缺)") + "\n"
            "加 --apply 才会真正执行（无 force 通道）。\n")
        return Exit.OK
    try:
        result = apply_rehost(
            data_root=Path(args.data_root), config_root=Path(args.config_dir),
            cutover_id=args.cutover_id,
            expected_aux_db_path=args.expect_aux_db_path,
            new_aux_db_path=args.new_aux_db_path,
            authorization=args.authorization or "",
            mode=args.backup_mode,
        )
    except CutoverToolError as exc:
        sys.stderr.write(f"拒绝执行：{exc}\n")
        return Exit.REFUSED
    if args.json:
        _emit_json("cutover-rehost", True, Exit.OK, result)
        return Exit.OK
    sys.stdout.write(f"换代完成：{result['old_cutover_id']} → {result['new_cutover_id']}\n")
    sys.stdout.write(f"  新 aux_db_path = {result['new_aux_db_path']}\n")
    sys.stdout.write(f"  备份目录 = {result['backups'].get('_dir')}\n")
    return Exit.OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="qs-deliver", description="QuantStudio 数据交付包工具")
    sub = ap.add_subparsers(dest="command", required=True)

    e = sub.add_parser("export", help="导出交付包（主库 + 两代因子库 + manifest）")
    e.add_argument("--export-dir", required=True, help="导出根目录（不得位于 DATA_ROOT 内）")
    e.add_argument("--config-dir", help="配置根目录（默认 config/）")
    e.add_argument("--accept-sync-dir", action="store_true", help="同步盘警示的显式确认")
    e.add_argument("--verify-hashes", action="store_true", help="复制后再做一次全量哈希复算")
    e.add_argument("--json", action="store_true", help="机读输出（stdout 只输出 JSON）")
    e.add_argument("--quiet", action="store_true", help="只打印相位切换")
    e.set_defaults(handler=_cmd_export)

    s = sub.add_parser("status", help="打印 DATA_ROOT / 配置 / daemon / 写锁状态")
    s.add_argument("--config-dir")
    s.add_argument("--json", action="store_true")
    s.set_defaults(handler=_cmd_status)

    v = sub.add_parser("verify", help="只读校验交付包（不取锁、不写盘）")
    v.add_argument("--import-dir", required=True, help="包路径或其父目录")
    v.add_argument("--config-dir")
    v.add_argument("--json", action="store_true")
    v.add_argument("--quiet", action="store_true")
    v.set_defaults(handler=_cmd_verify)

    i = sub.add_parser("import", help="导入交付包（唯一会写 DATA_ROOT 的命令）")
    i.add_argument("--import-dir", required=True)
    i.add_argument("--config-dir")
    i.add_argument("--dry-run", action="store_true", help="只做只读校验，不取锁不写盘")
    i.add_argument("--no-repair", action="store_true", help="禁止修复模式（坏库时拒绝而非取证修复）")
    i.add_argument("--json", action="store_true")
    i.add_argument("--quiet", action="store_true")
    i.set_defaults(handler=_cmd_import)

    r = sub.add_parser("recover", help="journal 恢复（continue / rollback）")
    r.add_argument("--action", choices=("continue", "rollback"), required=True)
    r.add_argument("--config-dir")
    r.add_argument("--json", action="store_true")
    r.set_defaults(handler=_cmd_recover)

    u = sub.add_parser("unlock", help="写锁诊断与自助回收（默认只诊断；--force 走 CAS 回收）")
    u.add_argument("--force", action="store_true", help="经 try_reclaim_stale 回收（持有者存活则拒绝）")
    u.add_argument("--json", action="store_true")
    u.set_defaults(handler=_cmd_unlock)

    cdg = sub.add_parser("cutover-diag", help="QFQ 路由只读诊断（零写入）")
    cdg.add_argument("--data-root", default="data")
    cdg.add_argument("--config-dir", default="config")
    cdg.add_argument("--json", action="store_true")
    cdg.set_defaults(handler=_cmd_cutover_diag)

    chr_ = sub.add_parser("cutover-rehost", help="cutover 记录换代校正（默认 dry-run）")
    chr_.add_argument("--data-root", default="data")
    chr_.add_argument("--config-dir", default="config")
    chr_.add_argument("--cutover-id", required=True)
    chr_.add_argument("--expect-aux-db-path", required=True, help="CAS：库内现值必须等于它")
    chr_.add_argument("--new-aux-db-path", required=True)
    chr_.add_argument("--authorization", default="", help="客户授权依据（审计行必填）")
    chr_.add_argument("--backup-mode", default="auto", choices=["auto", "vacuum", "copy"])
    chr_.add_argument("--apply", action="store_true", help="真正执行（无 force 通道）")
    chr_.add_argument("--json", action="store_true")
    chr_.set_defaults(handler=_cmd_cutover_rehost)

    st = sub.add_parser("set", help="设置 export_dir / import_dir")
    st.add_argument("--export-dir")
    st.add_argument("--import-dir")
    st.add_argument("--config-dir")
    st.add_argument("--json", action="store_true")
    st.set_defaults(handler=_cmd_set)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 用法错误 ⇒ 2
        return int(exc.code or 0)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
