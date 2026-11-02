#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付特性纯增益审计：SC-013 基线相对 diff 审计（FR-041）。

用法：
    python scripts/delivery_pure_gain_audit.py --mode baseline \
        --out docs/evidence/delivery-pure-gain-baseline-YYYYMMDD.json

    python scripts/delivery_pure_gain_audit.py --mode audit \
        --baseline docs/evidence/delivery-pure-gain-baseline-YYYYMMDD.json \
        [--out docs/evidence/delivery-waveN-YYYYMMDD.md]

判定口径（spec FR-041 / SC-013，共享工作区基线相对）：
- PROTECTED（写入路径：writers.py + qfq_*.py + *upsert*.py）逐文件 diff 哈希与基线**逐字一致** ⇒ PASS。
- 不一致 ⇒ 必须分类：
    SELF_CHANGED  本会话改动清单里出现该文件（违规，退出码 1）
    OTHER_LINE    本会话未动、可见他线证据（例外，须留证）
    UNKNOWN       无法判定（fail-closed 按 SELF_CHANGED 上报）
- APPEND_ONLY（②审 D-6 裁定：main_window.py / workers.py / pyproject.toml / .gitignore）
  **只核不判**：记录读数供人工对照，不参与 PASS/FAIL。
- 受保护目录内新增未跟踪文件 ⇒ 视为 SELF_CHANGED（基线里已记录者除外）。

本脚本不写任何受保护文件；只读 git 与工作区，产出证据文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# 写入路径（SC-013 判据范围，唯一权威定义处）
PROTECTED_GLOBS = (
    "quantstudio/pipeline/writers.py",
    "quantstudio/pipeline/qfq_*.py",
    "quantstudio/pipeline/*upsert*.py",
)

# 追加型改动文件（②审 D-6：只核不判）
APPEND_ONLY = (
    "quantstudio/gui/main_window.py",
    "quantstudio/gui/workers.py",
    "pyproject.toml",
    ".gitignore",
)

# 受保护目录（未跟踪文件扫描范围）
PROTECTED_DIRS = ("quantstudio/pipeline",)

TOUCHED_LIST_PATH = "docs/evidence/delivery-session-touched-files.txt"


# ── git 与哈希 ───────────────────────────────────────────────────────────────
def _git(*args: str) -> str:
    """执行 git 并返回 stdout（失败抛 RuntimeError，不静默）。"""
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败（exit {proc.returncode}）："
            f"{proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return proc.stdout.decode("utf-8", "replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def protected_files() -> List[str]:
    """展开 PROTECTED_GLOBS 为具体文件清单（git ls-files，保持稳定序）。"""
    out = _git("ls-files", *PROTECTED_GLOBS)
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def file_entry(rel_path: str) -> Dict:
    """单个文件的基线条目：工作树哈希 + diff 哈希/长度。"""
    abs_path = REPO_ROOT / rel_path
    worktree_sha = (
        sha256_bytes(abs_path.read_bytes()) if abs_path.exists() else None
    )
    diff_text = _git("diff", "--", rel_path) if _is_tracked(rel_path) else ""
    return {
        "path": rel_path,
        "tracked": _is_tracked(rel_path),
        "worktree_sha256": worktree_sha,
        "diff_sha256": sha256_bytes(diff_text.encode("utf-8")),
        "diff_bytes": len(diff_text),
    }


def _is_tracked(rel_path: str) -> bool:
    return bool(_git("ls-files", "--", rel_path).strip())


def untracked_in_protected_dirs() -> List[str]:
    out = _git("ls-files", "--others", "--exclude-standard", *PROTECTED_DIRS)
    return sorted(line.strip() for line in out.splitlines() if line.strip())


ATTRIBUTION_MAP_PATH = "docs/evidence/session-attribution.json"


def read_attribution_map() -> Dict[str, List[str]]:
    """读机读会话归属映射（SC-013 修正令 2026-11-03）。返回 {session: [前缀...]}。

    映射为**唯一权威**：审计直接消费，不依赖任何手记备注；文件缺失 ⇒ 空表（回退旧清单）。
    """
    path = REPO_ROOT / ATTRIBUTION_MAP_PATH
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"会话归属映射解析失败：{path}: {exc}") from exc
    out: Dict[str, List[str]] = {}
    for name, spec in (doc.get("sessions") or {}).items():
        paths = [str(p).strip() for p in (spec.get("paths") or []) if str(p).strip()]
        if paths:
            out[str(name)] = paths
    return out


def read_touched_list() -> List[str]:
    """本会话改动清单 = 机读映射（所有域并集） ∪ 旧文本清单（向后兼容）。"""
    prefixes: List[str] = []
    for paths in read_attribution_map().values():
        prefixes.extend(paths)
    path = REPO_ROOT / TOUCHED_LIST_PATH
    if path.exists():
        prefixes.extend(
            line.strip()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return sorted(set(prefixes))


def _is_self_path(path: str, touched: List[str]) -> bool:
    """前缀匹配：path == 前缀 或 path.startswith(前缀)（覆盖目录/文件/词干三种登记形态）。"""
    return any(path == p or path.startswith(p) for p in touched)


# ── 判定 ────────────────────────────────────────────────────────────────────
@dataclass
class Classification:
    label: str            # SAME / SELF_CHANGED / OTHER_LINE / UNKNOWN
    reason: str = ""
    evidence: Dict = field(default_factory=dict)


def classify(
    entry: Dict, base_entry: Dict, touched: List[str], untracked_now: List[str]
) -> Classification:
    """比对单文件并分类（纯函数，便于单测）。"""
    path = entry["path"]
    same = (
        entry["diff_sha256"] == base_entry.get("diff_sha256")
        and entry["worktree_sha256"] == base_entry.get("worktree_sha256")
    )
    if same:
        return Classification("SAME", "diff 与工作树哈希均与基线逐字一致")
    if _is_self_path(path, touched):
        return Classification(
            "SELF_CHANGED",
            "本会话改动清单含该文件，且与基线不一致",
            {"baseline": base_entry, "now": entry},
        )
    return Classification(
        "OTHER_LINE",
        "本会话改动清单不含该文件；按他线推进处置（须留证并人工复核）",
        {"baseline": base_entry, "now": entry},
    )


def audit(baseline: Dict) -> Tuple[int, Dict]:
    """执行审计，返回 (退出码, 报告)。退出码：0 通过 / 1 违规 / 2 无法判定。"""
    touched = read_touched_list()
    base_protected = {e["path"]: e for e in baseline.get("protected", [])}
    base_untracked = set(baseline.get("untracked_protected", []))

    results: List[Dict] = []
    verdict = "PASS"
    exit_code = 0
    for path in protected_files():
        base_entry = base_protected.get(path)
        if base_entry is None:
            results.append(
                {
                    "path": path,
                    "label": "UNKNOWN",
                    "reason": "基线中没有该文件（基线后被新增的受保护路径）",
                }
            )
            verdict, exit_code = "FAIL", 1
            continue
        cls = classify(file_entry(path), base_entry, touched, [])
        results.append(
            {"path": path, "label": cls.label, "reason": cls.reason}
        )
        if cls.label == "SELF_CHANGED":
            verdict, exit_code = "FAIL", 1

    missing = [p for p in base_protected if p not in {r["path"] for r in results}]
    for path in missing:
        results.append(
            {"path": path, "label": "UNKNOWN", "reason": "基线存在但当前已消失"}
        )
        verdict, exit_code = "FAIL", 1

    untracked_now = untracked_in_protected_dirs()
    new_untracked = [p for p in untracked_now if p not in base_untracked]
    if new_untracked:
        verdict = "FAIL"
        exit_code = 1

    append_only = [
        {"path": p, "now": file_entry(p), "baseline": _find(baseline, "append_only", p)}
        for p in APPEND_ONLY
    ]

    report = {
        "verdict": verdict,
        "exit_code": exit_code,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD").strip(),
        "protected_total": len(base_protected),
        "protected_changed": [r for r in results if r["label"] != "SAME"],
        "untracked_new": new_untracked,
        "append_only": append_only,
        "touched_list": touched,
        "results": results,
    }
    return exit_code, report


def _find(baseline: Dict, section: str, path: str) -> Optional[Dict]:
    for e in baseline.get(section, []):
        if e.get("path") == path:
            return e
    return None


# ── 基线 ────────────────────────────────────────────────────────────────────
def build_baseline(note: str = "") -> Dict:
    files = protected_files()
    return {
        "kind": "delivery-pure-gain-baseline",
        "taken_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_head": _git("rev-parse", "HEAD").strip(),
        "session_note": note,
        "protected": [file_entry(p) for p in files],
        "append_only": [file_entry(p) for p in APPEND_ONLY],
        "untracked_protected": untracked_in_protected_dirs(),
    }


# ── 输出 ────────────────────────────────────────────────────────────────────
def render_text(baseline: Dict) -> str:
    dirty = [e for e in baseline["protected"] if e["diff_bytes"]]
    lines = [
        f"[baseline] 保护路径 {len(baseline['protected'])} 个，其中开工即脏 "
        f"{len(dirty)} 个；APPEND_ONLY {len(baseline['append_only'])} 个；"
        f"未跟踪(受保护目录) {len(baseline['untracked_protected'])} 个",
        f"[baseline] git HEAD {baseline['git_head']}",
    ]
    for e in dirty:
        lines.append(
            f"  - {e['path']}  diff_bytes={e['diff_bytes']}  "
            f"diff_sha256={e['diff_sha256'][:12]}  tracked={e['tracked']}"
        )
    for e in baseline["append_only"]:
        if e["diff_bytes"]:
            lines.append(
                f"  ~ append_only {e['path']}  diff_bytes={e['diff_bytes']}  "
                f"diff_sha256={e['diff_sha256'][:12]}"
            )
    return "\n".join(lines)


def render_audit(report: Dict) -> str:
    lines = [
        f"## SC-013 基线相对审计（{report['checked_at']}）",
        "",
        f"- 结论：**{report['verdict']}**（退出码 {report['exit_code']}）",
        f"- 保护路径总数：{report['protected_total']}；有变化：{len(report['protected_changed'])}",
        f"- 新增未跟踪（受保护目录）：{report['untracked_new'] or '无'}",
        "",
        "| 文件 | 判定 | 说明 |",
        "|---|---|---|",
    ]
    for r in report["results"]:
        if r["label"] != "SAME":
            lines.append(f"| `{r['path']}` | {r['label']} | {r['reason']} |")
    if not report["protected_changed"]:
        lines.append("| — | SAME | 全部与基线逐字一致 |")
    lines += ["", "### APPEND_ONLY 对照读数（只核不判）", "", "| 文件 | 基线 diff_bytes | 当前 diff_bytes | 判定 |", "|---|---|---|---|"]
    for e in report["append_only"]:
        b = (e["baseline"] or {}).get("diff_bytes")
        n = e["now"]["diff_bytes"]
        lines.append(f"| `{e['path']}` | {b} | {n} | {'一致' if b == n else '已变化'} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="交付特性纯增益审计（SC-013 基线相对）")
    ap.add_argument("--mode", required=True, choices=["baseline", "audit"])
    ap.add_argument("--baseline", help="audit 模式必填：基线 json 路径")
    ap.add_argument("--out", help="输出路径（baseline=json；audit=md）")
    ap.add_argument("--note", default="", help="基线备注（写进 session_note）")
    args = ap.parse_args(argv)

    try:
        if args.mode == "baseline":
            baseline = build_baseline(note=args.note)
            text = render_text(baseline)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                text += f"\n[baseline] 已写入 {out}"
            print(text)
            return 0

        if not args.baseline:
            print("audit 模式必须给 --baseline", file=sys.stderr)
            return 2
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        exit_code, report = audit(baseline)
        text = render_audit(report)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if out.exists() else "w"
            with out.open(mode, encoding="utf-8", newline="\n") as fh:
                fh.write(("\n" if mode == "a" else "") + text + "\n")
            text += f"\n[audit] 已追加到 {out}"
        print(text)
        return exit_code
    except Exception as exc:  # 不静默
        print(f"[pure-gain-audit] 失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
