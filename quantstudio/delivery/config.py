# -*- coding: utf-8 -*-
"""交付包配置（`config/delivery_config.json`）：两项路径 + 原子写 + 缺失容忍。

对应需求：FR-020（仅两项，禁 `backup_mode`）、FR-021（原子写；缺失/空值视为未选择，
不得报错启动失败）、FR-024（选择后回显）、SC-010（删配置/空字段/错路径都能正常启动）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .constants import CONFIG_VERSION, DELIVERY_CONFIG_NAME

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = _REPO_ROOT / "config"


@dataclass
class DeliveryConfig:
    """两项配置。空串 = 未选择（不是错误，只是按钮应禁用）。"""

    export_dir: str = ""
    import_dir: str = ""

    def export_path(self) -> Optional[Path]:
        return Path(self.export_dir) if self.export_dir.strip() else None

    def import_path(self) -> Optional[Path]:
        return Path(self.import_dir) if self.import_dir.strip() else None


def config_path(config_dir: Optional[Path] = None) -> Path:
    """配置文件路径（默认 `config/delivery_config.json`）。"""
    return Path(config_dir or DEFAULT_CONFIG_DIR) / DELIVERY_CONFIG_NAME


def load(config_dir: Optional[Path] = None) -> Tuple[DeliveryConfig, List[str]]:
    """读配置；**永不抛**（缺失/损坏 ⇒ 视为未选择 + 返回告警）。"""
    path = config_path(config_dir)
    warnings: List[str] = []
    if not path.exists():
        return DeliveryConfig(), warnings
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # 损坏 ⇒ 未选择，不阻断启动
        warnings.append(f"配置文件无法解析，已视为未选择：{exc}")
        return DeliveryConfig(), warnings
    if not isinstance(doc, dict):
        warnings.append("配置文件根节点不是对象，已视为未选择")
        return DeliveryConfig(), warnings

    version = doc.get("config_version")
    if version is not None and version != CONFIG_VERSION:
        warnings.append(
            f"配置版本 {version!r} 与程序支持版本 {CONFIG_VERSION} 不同，已按当前口径读取"
        )
    if "backup_mode" in doc:
        warnings.append("配置含已废弃字段 backup_mode（v3 复审裁定删除），已忽略")

    def _str(key: str) -> str:
        value = doc.get(key, "")
        return value.strip() if isinstance(value, str) else ""

    return DeliveryConfig(export_dir=_str("export_dir"), import_dir=_str("import_dir")), warnings


def save(cfg: DeliveryConfig, config_dir: Optional[Path] = None) -> Path:
    """原子写（tmp + os.replace）；目录不存在则创建。"""
    path = config_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "config_version": CONFIG_VERSION,
        "export_dir": cfg.export_dir.strip(),
        "import_dir": cfg.import_dir.strip(),
    }
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)
    return path


def buttons_enabled(cfg: DeliveryConfig) -> Tuple[bool, bool]:
    """(导出按钮可用, 导入按钮可用)——壳层只读这里，禁止各自判空（FR-022）。"""
    return cfg.export_path() is not None, cfg.import_path() is not None

def resolve_config_root(config_dir=None):
    """把用户给的 `--config-dir` 归一为**配置根**（含 profiles/mcp_only/ 的那一层）。

    实测坑（2026-11-01）：用户按直觉传 `config/profiles/mcp_only`，而内部表/水位真源
    都按 `config_root / TABLE_MAP_REL` 解析 ⇒ 拼成 `profiles/mcp_only/profiles/mcp_only/...`。
    两种都接受：
      - root/profiles/mcp_only/questdb_table_map.json 存在 → root 就是配置根；
      - root/questdb_table_map.json 存在（root 已是 profile 目录）→ 退两级回到 config/；
      - 其它情况原样返回（由调用方按缺文件报错）。
    """
    root = Path(config_dir) if config_dir is not None else DEFAULT_CONFIG_DIR
    marker = root / "profiles" / "mcp_only" / "questdb_table_map.json"
    if marker.exists():
        return root
    if (root / "questdb_table_map.json").exists():
        return root.parent.parent
    return root

