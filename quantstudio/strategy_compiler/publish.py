"""Publish rendered strategy entry points to user-facing project directories."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from .render import output_filename


class StrategyPublishError(RuntimeError):
    """Raised when a stable published strategy path cannot be written safely."""


def _same_content(source: Path, target: Path) -> bool:
    if not target.is_file() or source.stat().st_size != target.stat().st_size:
        return False
    return hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest()


def _atomic_publish(source: Path, target: Path, *, overwrite: bool) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _same_content(source, target):
            return target
        if not overwrite:
            raise StrategyPublishError(
                f"published strategy already exists with different content: {target}; "
                "set output.overwrite=true to replace it"
            )
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def publish_strategy_entry_points(
    spec: dict[str, Any],
    package_dir: str | Path,
    project_root: str | Path,
) -> dict[str, Path]:
    """Publish package entry points to the stable GUI/PTrade directories.

    QuantStudio files are published to ``quantstudio/backtest/strategies`` so
    the PyQt backtest strategy selector sees them immediately after refresh.
    PTrade files are published to the project-root ``ptrade`` directory.
    """
    strategy_id = spec["strategy_id"]
    package_dir = Path(package_dir).resolve()
    project_root = Path(project_root).resolve()
    overwrite = bool(spec.get("output", {}).get("overwrite", False))

    qs_name = output_filename(strategy_id, "quantstudio")
    pt_name = output_filename(strategy_id, "ptrade-default")
    qs_source = package_dir / qs_name
    pt_source = package_dir / pt_name
    missing = [str(path) for path in (qs_source, pt_source) if not path.is_file()]
    if missing:
        raise StrategyPublishError(f"package entry point(s) missing: {missing}")

    qs_target = project_root / "quantstudio" / "backtest" / "strategies" / qs_name
    pt_target = project_root / "ptrade" / pt_name
    return {
        "quantstudio": _atomic_publish(qs_source, qs_target, overwrite=overwrite),
        "ptrade-default": _atomic_publish(pt_source, pt_target, overwrite=overwrite),
    }
