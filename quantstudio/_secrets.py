"""Load config/secrets.env into the process environment (idempotent).

QuantStudio reads runtime credentials (``TUSHARE_TOKEN``, ``QMT_PATH``,
``JQ_TOKEN``, ...) from ``os.environ`` via ``${ENV_VAR}`` placeholders in
``sources_config.json``. This module auto-injects ``config/secrets.env``
(gitignored, per-user) into ``os.environ`` so users only need to fill the
template once — no manual ``source`` / ``export`` required.

Design guarantees:
- Idempotent: loaded at most once unless ``force=True``.
- Never overwrites an existing process variable (explicit exports and test
  monkeypatching always take precedence).
- Fails soft: a missing or malformed file never blocks application startup.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_LOADED = False


def _project_root() -> Path:
    # quantstudio/_secrets.py -> <project root>/QuantStudio
    return Path(__file__).resolve().parent.parent


def load_secrets_env(force: bool = False) -> bool:
    """Load ``config/secrets.env`` into ``os.environ``.

    Returns ``True`` if a secrets.env file was found and processed (even if
    every key was skipped because it already existed in the environment).
    Returns ``False`` if no secrets.env file exists.
    """
    global _LOADED
    if _LOADED and not force:
        return True

    secrets_path = _project_root() / "config" / "secrets.env"
    if not secrets_path.is_file():
        _LOADED = True
        return False

    loaded_keys: list[str] = []
    try:
        with secrets_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.rstrip("\r\n").strip()
                if not key:
                    continue
                if key in os.environ:
                    # 不覆盖进程已有变量：显式 export / 测试 monkeypatch 优先
                    continue
                os.environ[key] = value
                loaded_keys.append(key)
    except Exception as exc:  # 加载失败不应阻断应用启动
        logger.warning("读取 secrets.env 失败，已跳过: %s", exc)
        _LOADED = True
        return False

    _LOADED = True
    if loaded_keys:
        logger.info("已从 %s 载入 %d 个凭证变量", secrets_path, len(loaded_keys))
    return True
