# -*- coding: utf-8 -*-
"""交付包内核（单一内核；CLI 与 GUI 均为薄壳，只做参数收集与展示）。

公开 API（按波次逐步落地）：
- W0：`constants` / `config` / `manifest` / `probe` / `winenv` / `stats`
- W1：`export_bundle`
- W2：`verify_bundle`
- W3：`import_bundle` / `recover_journal` / `detect_pending_journal`

设计约束（plan §1.1）：本包**不 import** `quantstudio.pipeline.writers` /
`daemon` / `backtest.*`——「零改动写入路径」由结构保证，而不只靠人工自觉。
"""
from __future__ import annotations

from typing import Any

from . import config, constants, manifest, probe, stats, winenv
from .export_kernel import ExportAborted, ExportFailed, ExportRefused, ExportResult, export_bundle
from .import_kernel import (
    ImportRefused,
    ImportResult,
    RecoveryRequired,
    RecoveryResult,
    import_bundle,
    recover_journal,
)
from .journal import detect_pending_journal

__all__ = [
    "config",
    "constants",
    "manifest",
    "probe",
    "stats",
    "winenv",
    "export_bundle",
    "ExportResult",
    "ExportRefused",
    "ExportAborted",
    "ExportFailed",
    "verify_bundle",
    "import_bundle",
    "ImportResult",
    "ImportRefused",
    "RecoveryRequired",
    "RecoveryResult",
    "recover_journal",
    "detect_pending_journal",
]

_NOT_READY = {
    "verify_bundle": "W2（内核只读校验）",
}


def __getattr__(name: str) -> Any:  # pragma: no cover - 仅未落地阶段可达
    if name in _NOT_READY:
        raise NotImplementedError(
            f"{name} 尚未落地（计划波次：{_NOT_READY[name]}）。"
            "请按 tasks.md 的波次推进。"
        )
    raise AttributeError(name)
