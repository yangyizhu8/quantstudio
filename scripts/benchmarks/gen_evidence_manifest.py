"""门 1 证据 manifest 生成器（最新 main 重绑定版）。

用法：
    python scripts/benchmarks/gen_evidence_manifest.py [--out bench_artifacts/gate1_evidence_manifest.json]

本脚本基于「最新 main」独立 worktree 运行，产出可审计的证据清单（直接以 UTF-8 写文件）：
- 基线 commit：origin/main (b41400d)
- 9 个拟提交文件（3 tracked modified + 6 new untracked）的 SHA-256
- 测试 / 黄金 / A/B 证据的 SHA-256
- 明确的提交 / 不提交分类（用 `git` 查询判断 tracked/untracked，不依赖文件是否存在）

分类依据（git 查询，非文件存在性）：
    tracked_modified = git ls-files 命中 且 git diff HEAD 有改动
    new_untracked    = git ls-files 未命中 且 文件存在
    do_not_commit    = 本地证据 / 数据库 / output / build / 旧根目录脚本
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/benchmarks -> repo root

# 9 个拟提交文件（相对仓库根）
PROPOSED = [
    "quantstudio/backtest/providers/duckdb_data_access.py",
    "tests/test_duckdb_data_access_caching.py",
    "docs/performance_optimization.md",
    "README.md",
    "quantstudio/strategy_compiler/release/RELEASE_NOTES.md",
    "scripts/benchmarks/run_ab_benchmark.py",
    "scripts/benchmarks/run_golden.py",
    "scripts/benchmarks/parse_pytest_status.py",
    "scripts/benchmarks/gen_evidence_manifest.py",
]

# 不提交（本地证据 / 数据库 / output / build / 旧根目录脚本）
DO_NOT_COMMIT = [
    "bench_artifacts/",
    "data/quantstudio.db",
    "output/",
    "build/",
    ".pytest_cache/",
    "probe_db.py",
    "bench_smallcap.py",
    "bench_bars_cache.py",
]

# 证据文件（运行后生成于 bench_artifacts/）
EVIDENCE = [
    "bench_artifacts/test_duckdb_caching.json",
    "bench_artifacts/full_suite_baseline.json",
    "bench_artifacts/full_suite_optimized.json",
    "bench_artifacts/golden_baseline.json",
    "bench_artifacts/golden_optimized.json",
    "bench_artifacts/ab_summary.json",
    "bench_artifacts/nodeid_compare.json",
]


def _run(args):
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)


def _sha256(path: Path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def classify(path: str) -> str:
    """用 git 查询判断文件状态：tracked_modified / new_untracked / absent。"""
    tracked = _run(["git", "ls-files", "--error-unmatch", path]).returncode == 0
    if tracked:
        changed = _run(["git", "diff", "--quiet", "HEAD", "--", path]).returncode != 0
        return "tracked_modified" if changed else "tracked_unchanged"
    if (ROOT / path).exists():
        return "new_untracked"
    return "absent"


def main():
    parser = argparse.ArgumentParser(description="生成门 1 证据 manifest（直接 UTF-8 写文件）")
    parser.add_argument(
        "--out",
        default=str(ROOT / "bench_artifacts" / "gate1_evidence_manifest.json"),
        help="输出 JSON 路径（默认 bench_artifacts/gate1_evidence_manifest.json）",
    )
    args = parser.parse_args()

    origin_main = _run(["git", "rev-parse", "origin/main"]).stdout.strip()
    head = _run(["git", "rev-parse", "HEAD"]).stdout.strip()

    proposed = []
    for p in PROPOSED:
        status = classify(p)
        proposed.append({
            "path": p,
            "status": status,
            "sha256": _sha256(ROOT / p),
        })

    # 分类计数
    tracked_modified = [x for x in proposed if x["status"] == "tracked_modified"]
    new_untracked = [x for x in proposed if x["status"] == "new_untracked"]

    evidence = []
    for e in EVIDENCE:
        evidence.append({"path": e, "sha256": _sha256(ROOT / e)})

    # golden hash 回填（从实际生成的 golden_optimized.json 读取）
    golden_opt = ROOT / "bench_artifacts" / "golden_optimized.json"
    golden_hash = None
    if golden_opt.exists():
        try:
            golden_hash = json.loads(golden_opt.read_text(encoding="utf-8"))["hash"]
        except Exception:
            pass

    manifest = {
        "gate": "gate1",
        "origin_main_sha": origin_main,
        "head_sha": head,
        "proposed_files": proposed,
        "commit_scope": {
            "tracked_modified": [x["path"] for x in tracked_modified],
            "new_untracked": [x["path"] for x in new_untracked],
            "do_not_commit": DO_NOT_COMMIT,
        },
        "evidence_files": evidence,
        "golden_hash": golden_hash,
        "notes": [
            "3 tracked modified = duckdb_data_access.py / README.md / RELEASE_NOTES.md",
            "6 new untracked = test + performance_optimization.md + 4 个 benchmark 脚本",
            "分类由 git 查询判定，不依赖文件是否存在",
            "bars cache 已移除；仅保留 SHOW TABLES 表集合缓存（语义等价）",
            "调用方基数：b41400d 上 10 个（7 既有 + 3 原直接 SHOW TABLES 收敛）",
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 直接以 UTF-8 写文件，避免控制台重定向产生的编码乱码
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"manifest written to {out_path} (utf-8)")


if __name__ == "__main__":
    main()
