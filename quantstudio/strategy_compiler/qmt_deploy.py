# -*- coding: utf-8 -*-
"""qmt_deploy —— M5.2 件4/件5：QMT 产物部署内核（单一内核·CLI/PyQt 双壳共用）。

职责（docs/qmt-m5.2-closure-design.md §3 件4/件5）：
  1. 部署目标目录解析：CLI 显式 --qmt-path > config/qmt_deploy.json 的 deploy_dir > None（引导设置）；
     【用户裁定 2026-10-12】不做自动探测——首次由 PyQt 目录选择器/CLI 手选并写入配置，之后默认读配置。
  2. deploy_file：Copy-Item 式拷贝 + SHA-256 双端逐位核对 + deploy-manifest.json 清单登记；
     幂等覆盖（QMT 客户端跑过的文件变加密态，覆盖部署照常——CANSLIM 四轮复测先例）。
  3. 文件名规范：QMT_<标识>.py（英文标识，防中文文件名兼容性问题与多策略同名覆盖）。
  4. 层2 钩子（转换末端 --deploy-qmt）与层1 批量部署（--all/--strategy）共用本内核。

纯增益：不触碰转换管线既有行为；仅新增部署能力。
"""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_CONFIG = ROOT / "config" / "qmt_deploy.json"
QMT_IMPORT_DIR = ROOT / "output" / "qmt_import"
MANIFEST_PATH = QMT_IMPORT_DIR / "deploy-manifest.json"

_CONFIG_NOTE = ("大 QMT 客户端 python 目录（部署目标）。首次由界面目录选择器或 "
               "scripts/qmt_deploy.py --qmt-path <dir> --save 写入；之后转换/部署默认读取。")

# canonical 六策略 tag（镜像 scripts/qmt_reconvert_six.py SIX_TAGS——唯一重转入口的名单；
# 一致性由 tests/test_qmt_deploy.py 守护；产物命名 = tag + ".py"，历史变体后缀文件不在部署面）。
CANONICAL_SIX = (
    "CANSLIM突破成长选股策略",
    "fall_reversal",
    "tech_etf_mvo",
    "vol_regime_mom_rev",
    "weekly_smallcap",
    "周频三层",
)


def load_deploy_config() -> dict:
    if not DEPLOY_CONFIG.exists():
        return {"deploy_dir": None, "note": _CONFIG_NOTE}
    try:
        data = json.loads(DEPLOY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"deploy_dir": None, "note": _CONFIG_NOTE}
    data.setdefault("deploy_dir", None)
    data.setdefault("note", _CONFIG_NOTE)
    return data


def save_deploy_dir(dir_path: str) -> Path:
    """写入部署目录配置（PyQt 选择器与 CLI --qmt-path --save 共用）。"""
    p = Path(dir_path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"部署目录不存在: {p}")
    DEPLOY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    DEPLOY_CONFIG.write_text(
        json.dumps({"deploy_dir": str(p), "note": _CONFIG_NOTE}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return p


def resolve_deploy_dir(cli_dir: str | None = None) -> Path | None:
    """三级解析：CLI 显式 > 配置 > None（调用方引导设置）。"""
    if cli_dir:
        return Path(cli_dir).expanduser().resolve()
    cfg = load_deploy_config()
    if cfg.get("deploy_dir"):
        p = Path(cfg["deploy_dir"])
        return p if p.is_dir() else None
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _slugify(strategy_id: str) -> str:
    """文件标识规范化：仅折叠文件系统危险字符（空格/斜杠/冒号/引号/括号等），
    **保留 CJK 中文**——QMT 客户端实测支持中文文件名（CANSLIM 首部署
    「QMT转换测试.py」四轮复测先例，2026-10-11）；防不可辨识折叠（周频三层→strategy）。"""
    s = re.sub(r"[\s/\\:*?\"<>|()（）【】]+", "_", strategy_id.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "strategy"


def target_filename(strategy_id: str) -> str:
    return f"QMT_{_slugify(strategy_id)}.py"


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"deploys": []}


def _append_manifest(entry: dict) -> None:
    m = _load_manifest()
    m["deploys"].append(entry)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def deploy_file(src: Path, target_dir: Path, strategy_id: str | None = None,
                origin: str = "manual") -> dict:
    """部署单个产物：拷贝 + 哈希双端核对 + 清单登记。幂等覆盖。

    返回 {"src","dst","sha256","verified","strategy_id","origin","deployed_at"}。
    verified=False 时抛 AssertionError（fail-closed：核对不过即失败，不留半部署）。
    """
    if not src.is_file():
        raise FileNotFoundError(f"部署源不存在: {src}")
    sid = strategy_id or src.stem
    dst = target_dir / target_filename(sid)
    target_dir.mkdir(parents=True, exist_ok=True)
    src_sha = _sha256(src)
    shutil.copyfile(src, dst)
    dst_sha = _sha256(dst)
    verified = src_sha == dst_sha
    entry = {"src": str(src), "dst": str(dst), "sha256": src_sha, "verified": verified,
             "strategy_id": sid, "origin": origin, "deployed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _append_manifest(entry)
    if not verified:
        raise AssertionError(f"部署哈希核对失败: {src} -> {dst}（{src_sha} != {dst_sha}）")
    return entry


def find_product(strategy_id: str) -> Path | None:
    """定位策略的 QMT 产物（output/qmt_import/）。

    匹配序：canonical 命名（tag.py）> 历史命名（tag_qmt.py / tag_quantstudio_qmt.py）> slug 兜底。
    """
    if not QMT_IMPORT_DIR.is_dir():
        return None
    direct = QMT_IMPORT_DIR / f"{strategy_id}.py"
    if direct.is_file():
        return direct
    for suffix in ("_qmt.py", "_quantstudio_qmt.py"):
        alt = QMT_IMPORT_DIR / f"{strategy_id}{suffix}"
        if alt.is_file():
            return alt
    slug = _slugify(strategy_id)
    for p in sorted(QMT_IMPORT_DIR.glob("*.py")):
        if _slugify(p.stem) == slug:
            return p
    return None


def deploy_strategy(strategy_id: str, target_dir: Path, origin: str = "batch") -> dict:
    """层1：按策略标识部署 canonical 产物。"""
    src = find_product(strategy_id)
    if src is None:
        raise FileNotFoundError(
            f"策略 {strategy_id} 的 QMT 产物未找到（期望目录 {QMT_IMPORT_DIR}；"
            f"先跑 canonical 重转门 scripts/qmt_reconvert_six.py 或转换管线）")
    return deploy_file(src, target_dir, strategy_id=strategy_id, origin=origin)


def list_products(canonical_only: bool = True) -> list[str]:
    """列出可部署策略。默认只列 canonical 六 tag（产物在盘过滤，历史变体排除）；
    canonical_only=False 列目录全部 .py stem（诊断用）。"""
    if not QMT_IMPORT_DIR.is_dir():
        return []
    if canonical_only:
        return [t for t in CANONICAL_SIX if (QMT_IMPORT_DIR / f"{t}.py").is_file()]
    return sorted(p.stem for p in QMT_IMPORT_DIR.glob("*.py"))
