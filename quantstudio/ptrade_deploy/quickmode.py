# -*- coding: utf-8 -*-
"""PD-0008 快捷模式内核（GUI 快捷页与 CLI 可共用的纯函数；薄壳纪律）。

职责（**零转换逻辑**——转换仍只走 `runner.run_deploy`）：
- `discover_strategies`：递归扫描 PTrade 产物目录，产出可选策略清单；
- `derive_strategy_id`：策略名 → ASCII `strategy_id`（稳定、可复跑）；
- `build_quick_config`：生成与手写 deploy.yaml **等价**的配置文本。

设计依据：docs/ptrade-deploy-quickmode-design.md D2/D5（②审通过 + 三条件）
- 递归扫描：实测 45 个产物中 22 个位于两层（`fscore_rsrs/<名>/`、
  `broken_board_reversal/strategy/`），一层 glob 会漏近半；
- `_verify*`/`_probe*` 验证副本默认过滤（实测 5 个）；
- `strategy_id` 稳定性：它参与默认 `state_file` 命名，故只由**名称**派生
  （非 ASCII 名称追加名称哈希后缀，保证不同中文名不碰撞）。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional

#: 仓库根（本文件位于 <repo>/quantstudio/ptrade_deploy/）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 缺省产物扫描根（上游「转 PTrade」输出目录）
DEFAULT_SCAN_ROOT = REPO_ROOT / "output" / "ptrade_export"

#: 快捷配置暂存目录（便于追溯与复跑；已被 .gitignore 的 output/ 规则覆盖）
DEFAULT_QUICK_DIR = REPO_ROOT / "output" / ".qs-deploy-quick"

#: 缺省执行档位（日线尾盘；P2 交叉校验兜底）
DEFAULT_PROFILE = "daily_eod_1450"

#: 与 config._STRATEGY_ID_RE 同口径（ASCII 片段抽取）
_ASCII_RUN = re.compile(r"[A-Za-z0-9_-]+")

#: 验证副本目录前缀（默认过滤）
_VERIFY_PREFIXES = ("_verify", "_probe")


def repo_root() -> Path:
    return REPO_ROOT


def discover_strategies(scan_root: Optional[str] = None,
                        include_verify: bool = False) -> List[Dict[str, object]]:
    """递归发现可部署的 PTrade 产物。

    返回项：`{"name": 策略目录名（可中文）, "product_path": 绝对路径,
    "mtime": 浮点, "strategy_id": ASCII id}`；同目录多产物取 mtime 最新。
    """
    root = Path(scan_root) if scan_root else DEFAULT_SCAN_ROOT
    if not root.is_dir():
        return []
    latest: Dict[str, Dict[str, object]] = {}
    for path in sorted(root.rglob("*_ptrade.py")):
        rel_parts = path.relative_to(root).parts[:-1]
        if not include_verify and rel_parts and any(
                part.startswith(_VERIFY_PREFIXES) for part in rel_parts):
            continue
        name = rel_parts[-1] if rel_parts else path.stem.replace("_ptrade", "")
        item = {
            "name": name,
            "product_path": str(path.resolve()),
            "mtime": path.stat().st_mtime,
            "strategy_id": derive_strategy_id(name),
        }
        current = latest.get(name)
        if current is None or item["mtime"] > current["mtime"]:
            latest[name] = item
    return sorted(latest.values(), key=lambda it: str(it["name"]))


def derive_strategy_id(name: str, max_len: int = 40) -> str:
    """策略名 → ASCII ``strategy_id``（只依赖名称 → 重转稳定）。

    - 纯 ASCII 名称：取 `[A-Za-z0-9_-]` 连续片段，压缩重复分隔符；
    - 含非 ASCII：ASCII 片段 + ``_<名称 sha1 前 6 位>``（不同中文名不碰撞，
      且名称不变则 id 不变——`state_file` 默认名随之稳定）；
    - 片段为空：``strat_<sha1 前 6 位>``。
    """
    text = str(name or "")
    ascii_part = "_".join(_ASCII_RUN.findall(text.replace(" ", "_")))
    ascii_part = re.sub(r"[_\-]{2,}", "_", ascii_part).strip("_-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    has_non_ascii = any(ord(ch) > 127 for ch in text)
    if has_non_ascii:
        base = ascii_part or "strat"
        return f"{base[:max_len - 7]}_{digest}"
    if not ascii_part:
        return f"strat_{digest}"
    return ascii_part[:max_len]


def _yaml_scalar(value: str) -> str:
    """双引号 YAML 标量（转义反斜杠与双引号），避免中文/冒号解析歧义。"""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def build_quick_config(name: str, product_path: str,
                       profile: str = DEFAULT_PROFILE,
                       output_root: Optional[str] = None,
                       keep_versions: Optional[int] = None,
                       dir_name: Optional[str] = None) -> str:
    """生成快捷模式配置文本（与手写 deploy.yaml 等价；同一内核消费）。"""
    strategy_id = derive_strategy_id(name)
    subject = name if dir_name is None else dir_name
    lines = [
        "# PD-0008 快捷模式自动生成（与手写 deploy.yaml 等价；改参数请用高级模式）",
        f"strategy_id: {strategy_id}",
        f"source: {_yaml_scalar(str(Path(product_path).resolve()))}",
        "execution:",
        f"  profile: {profile}",
        "persistence:",
        "  enabled: true",
        "output:",
        f"  root: {_yaml_scalar(output_root or '')}",
        f"  dir_name: {_yaml_scalar(subject)}",
        "  keep_versions: null" if keep_versions is None
        else f"  keep_versions: {int(keep_versions)}",
    ]
    return "\n".join(lines) + "\n"


def write_quick_config(name: str, product_path: str,
                       profile: str = DEFAULT_PROFILE,
                       config_dir: Optional[str] = None,
                       **kwargs) -> Path:
    """生成并落盘快捷配置（默认落 `output/.qs-deploy-quick/<id>.yaml`）。"""
    target_dir = Path(config_dir) if config_dir else DEFAULT_QUICK_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{derive_strategy_id(name)}.yaml"
    path.write_text(build_quick_config(name, product_path, profile=profile, **kwargs),
                    encoding="utf-8")
    return path
