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
        "table_set": report.table_set,
        "elapsed_sec": report.elapsed_sec,
    }
    if args.json:
        _emit_json("verify", report.ok, report.exit_code, payload)
        return report.exit_code
    if report.ok:
        sys.stdout.write(f"校验通过：{report.package_dir}\n")
        sys.stdout.write(f"  文件 {report.files_checked} 个；表 {len(report.tables)} 张\n")
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
