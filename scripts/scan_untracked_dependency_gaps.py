#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_untracked_dependency_gaps.py —— 未跟踪依赖缺口扫描器（R66-G3 修复 · 计划项 d）

方案：docs/r66-dependency-closure-gap-repair-design-20261102.md §三(d)。CI/周期盘点
（不挂 pre-push）：扫描「已跟踪 .py 文件引用了未跟踪模块」的依赖缺口，让任何此类
断裂在入库/推送前即被看见，而不是等远端 CI 挂掉后才发现（簇 A/簇 B 同型）。

用法：
  python scripts/scan_untracked_dependency_gaps.py [--max-gaps 0] [--json]
  python scripts/scan_untracked_dependency_gaps.py --self-test        # 合成仓库自证

规则：未跟踪集 = git ls-files --others --exclude-standard -- '*.py'；已跟踪集 =
  git ls-files -- '*.py'；对已跟踪 .py 做 AST import 解析（解析规则与
  check_dependency_closure.py 一致：仓库根 + 一级包目录 quantstudio/tests/scripts 候选
  拼接，命中 <pkg>/<a>/<b>.py 或 __init__.py；相对导入按文件所在包上溯换算；本文件
  独立实现、不与其共享代码）。缺口 = 解析到的文件 ∈ 未跟踪集 且 ∉ 已跟踪集；
  排序按（导入方路径、被引用路径）字典序稳定输出。

退出码：0=缺口数 ≤ --max-gaps（默认 0）；3=超出阈值；1=脚本自身错误。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

PKG_ROOTS = ("quantstudio", "tests", "scripts")  # 绝对导入候选拼接根（与 A 脚本同规则）
SCRIPT_PATH = Path(__file__).resolve()


def run_git(repo: Path, args: list, check: bool = True):
    """统一 git 调用：禁 shell=True、UTF-8 解码、关 quotepath 保中文路径原样。"""
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(repo),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("git %s 失败（rc=%d）：%s"
                           % (" ".join(args), proc.returncode, proc.stderr.strip()))
    return proc


def resolve_repo_root(explicit: str | None) -> Path:
    """仓库根定位：--repo 显式指定 > git 顶层目录 > 脚本上级目录兜底。"""
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise RuntimeError("--repo 指定的目录不存在：%s" % explicit)
        return root
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return SCRIPT_PATH.parent.parent


def parse_imports(source: str, rel_path: str) -> list:
    """AST 解析 import 语句 → [{line, module, level, names}]。

    BOM 容错：去掉首部 U+FEFF（Windows 编辑器常见）——否则 ast.parse 报
    invalid non-printable character U+FEFF 造成整轮扫描中断。
    真实语法错误抛 RuntimeError，由调用方按「解析跳过」记录（不静默吞）。
    """
    if source.startswith("\ufeff"):
        source = source[1:]
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        raise RuntimeError("文件语法解析失败（%s:%s）：%s" % (rel_path, exc.lineno, exc.msg))
    refs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append({"line": node.lineno, "module": alias.name, "level": 0, "names": []})
        elif isinstance(node, ast.ImportFrom):
            refs.append({"line": node.lineno, "module": node.module or "", "level": node.level,
                         "names": [alias.name for alias in node.names]})
    return refs


def to_dotted(rel_path: str) -> str:
    """仓库相对路径 → 点分模块名（缺口明细展示用）。"""
    p = rel_path[:-len("/__init__.py")] if rel_path.endswith("/__init__.py") else rel_path[:-3]
    return p.replace("/", ".")


_DIR_CACHE: dict = {}


def worktree_has_exact(repo: Path, rel: str) -> bool:
    """工作区精确存在性（大小写敏感）：逐段以磁盘真实条目名比对。

    Windows 工作区大小写不敏感 ⇒ 直接 Path.is_file() 会把 `Env.py` 误判为 `env.py`；
    Linux CI 语义为大小写敏感。本门须与 CI 语义一致，否则假缺口会污染扫描结论。
    Path.name 是词法串（不反映磁盘真实大小写）⇒ 用 scandir 条目名比对。
    """
    cur = repo
    for part in PurePosixPath(rel).parts:
        if part in ("", "."):
            continue
        names = _DIR_CACHE.get(cur)
        if names is None:
            try:
                names = {e.name for e in os.scandir(cur)}
            except OSError:
                return False
            _DIR_CACHE[cur] = names
        if part not in names:
            return False
        cur = cur / part
    return cur.is_file()


def resolve_abs(module: str, repo: Path, tracked: set, untracked: set) -> str | None:
    """绝对模块名 → 仓库内文件路径；仅一等方根参与候选拼接（与 A 脚本同规则）。"""
    parts = [p for p in module.split(".") if p]
    if not parts or parts[0] not in PKG_ROOTS:
        return None
    rel = "/".join(parts)
    for cand in (rel + ".py", rel + "/__init__.py"):
        if worktree_has_exact(repo, cand) or cand in tracked or cand in untracked:
            return cand
    return None


def resolve_ref(ref: dict, importer_rel: str, repo: Path, tracked: set, untracked: set) -> list:
    """单条 import → 命中的仓库内文件路径列表。

    from X import a, b：X 本体与可解析子模块 X.a 均产出候选（X 必须可导入，属性名由
    X 本体覆盖）；相对导入按文件所在包上溯换算为仓库相对路径；上溯越界/顶层裸文件
    ⇒ 不产出（B 仅以「命中未跟踪集」为缺口，未解析不判缺口，规格 B3）。
    """
    out = []
    if ref["level"] == 0:
        base = ref["module"]
        hit = resolve_abs(base, repo, tracked, untracked)
        if hit:
            out.append(hit)
        for name in ref["names"]:
            hit = resolve_abs(base + "." + name, repo, tracked, untracked)
            if hit:
                out.append(hit)
        return out
    cur = PurePosixPath(importer_rel).parent
    up = ref["level"] - 1
    if up > len(cur.parts) or not cur.parts:
        return out
    for _ in range(up):
        cur = cur.parent
    if not cur.parts:
        return out
    base_rel = "/".join([*cur.parts, *(p for p in ref["module"].split(".") if p)])
    base_cands = ((base_rel + "/__init__.py",) if not ref["module"]
                  else (base_rel + ".py", base_rel + "/__init__.py"))
    out.extend(c for c in base_cands
               if worktree_has_exact(repo, c) or c in tracked or c in untracked)
    for name in ref["names"]:
        for cand in (base_rel + "/" + name + ".py", base_rel + "/" + name + "/__init__.py"):
            if worktree_has_exact(repo, cand) or cand in tracked or cand in untracked:
                out.append(cand)
                break
    return out


def read_source(repo: Path, rel: str) -> str:
    """读取已跟踪 .py：优先工作区；被本地删除则回退 HEAD 树；均无 ⇒ 脚本错误。"""
    path = repo / rel
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise RuntimeError("已跟踪文件读取失败（%s）：%s" % (rel, exc))
    proc = run_git(repo, ["show", "HEAD:" + rel], check=False)
    if proc.returncode == 0:
        return proc.stdout
    raise RuntimeError("已跟踪 .py 不可读（工作区与 HEAD 树均不存在）：%s" % rel)


def scan(repo: Path) -> dict:
    """全仓扫描：已跟踪 .py 的 import 解析目标 vs 未跟踪集（规格 B1/B3）。"""
    tracked = {ln.strip() for ln in run_git(repo, ["ls-files", "--", "*.py"]).stdout.splitlines()
               if ln.strip()}
    untracked = {ln.strip() for ln in run_git(
        repo, ["ls-files", "--others", "--exclude-standard", "--", "*.py"]).stdout.splitlines()
        if ln.strip()}
    rows = {}
    parse_skipped = []
    for rel in sorted(tracked):
        source = read_source(repo, rel)
        try:
            refs = parse_imports(source, rel)
        except RuntimeError as exc:
            # 解析失败文件不中断整轮扫描：记入 parse_skipped（可见、可 --strict-parse 转致命）
            parse_skipped.append({"file": rel, "error": str(exc)})
            continue
        for ref in refs:
            for target in resolve_ref(ref, rel, repo, tracked, untracked):
                if target in untracked and target not in tracked:  # B3：命中未跟踪集即缺口
                    key = (rel, target)
                    row = rows.setdefault(key, {"importer": rel, "ref": target,
                                                "line": ref["line"], "module": to_dotted(target)})
                    row["line"] = min(row["line"], ref["line"])
    return {"tracked_py": len(tracked), "untracked_py": len(untracked),
            "gaps": [rows[k] for k in sorted(rows)], "parse_skipped": parse_skipped}


def self_test() -> int:
    """合成 git 仓库自证（B6，自建自销不碰本仓）：有缺口 ⇒ exit 3；补齐入库 ⇒ exit 0。"""
    n_pass = n_all = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal n_pass, n_all
        n_all += 1
        n_pass += 1 if ok else 0
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "｜" + detail[:300]))

    def build(td: str, files: dict, commit_paths: list):
        repo = Path(td)
        run = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), "init", "-q"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if run.returncode != 0:
            raise RuntimeError("自测 git init 失败：" + run.stderr.strip())
        for rel, text in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        for paths in commit_paths:
            subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), "add", "--", *paths],
                           check=True, capture_output=True, text=True, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=selftest",
                            "-c", "user.email=selftest@local", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "c"], check=True, capture_output=True,
                           text=True, encoding="utf-8")
        return repo

    def run_script(repo: Path, *extra: str):
        return subprocess.run([sys.executable, str(SCRIPT_PATH), "--repo", str(repo), *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    files = {"quantstudio/__init__.py": "", "quantstudio/imp.py": "import quantstudio.dep\n",
             "quantstudio/dep.py": "X = 1\n"}
    # S1 有缺口（已跟踪 imp.py 引用未跟踪 dep.py ⇒ exit 3；--max-gaps 放宽 ⇒ exit 0）
    with tempfile.TemporaryDirectory(prefix="utgap_st1_") as td:
        repo = build(td, files, [["quantstudio/__init__.py", "quantstudio/imp.py"]])
        proc = run_script(repo, "--json")
        ok = proc.returncode == 3
        try:
            rep = json.loads(proc.stdout)
            ok = ok and rep["gap_count"] == 1 and rep["gaps"][0]["importer"] == "quantstudio/imp.py" \
                and rep["gaps"][0]["ref"] == "quantstudio/dep.py"
        except ValueError:
            ok = False
        check("S1 有缺口 ⇒ exit 3 且明细正确", ok, "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
        proc = run_script(repo, "--max-gaps", "1")
        check("S1b --max-gaps 1 ⇒ exit 0", proc.returncode == 0, "rc=%d" % proc.returncode)
    # S2 无缺口（dep.py 补齐入库 ⇒ exit 0）
    with tempfile.TemporaryDirectory(prefix="utgap_st2_") as td:
        repo = build(td, files, [["quantstudio/__init__.py", "quantstudio/imp.py",
                                  "quantstudio/dep.py"]])
        proc = run_script(repo)
        check("S2 无缺口 ⇒ exit 0", proc.returncode == 0 and "PASS" in proc.stdout,
              "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
    print("[self-test] %s：%d/%d 通过" % ("全绿" if n_pass == n_all else "存在失败", n_pass, n_all))
    return 0 if n_pass == n_all else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 控制台中文输出兜底
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="未跟踪依赖缺口扫描器：已跟踪 .py 引用未跟踪模块的缺口盘点（CI/周期，不挂 pre-push）。")
    parser.add_argument("--repo", help="仓库根（默认 git 顶层，失败回退脚本上级目录）")
    parser.add_argument("--max-gaps", type=int, default=0, help="允许缺口上限（默认 0，超出退出码 3）")
    parser.add_argument("--strict-parse", action="store_true",
                        help="解析失败文件视为致命（退出码 4）；默认仅记录并跳过")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    parser.add_argument("--self-test", action="store_true", help="合成仓库自证（不碰本仓）")
    args = parser.parse_args()
    try:
        if args.self_test:
            return self_test()
        if args.max_gaps < 0:
            raise RuntimeError("--max-gaps 不得为负：%d" % args.max_gaps)
        repo = resolve_repo_root(args.repo)
        result = scan(repo)
        gap_count = len(result["gaps"])
        skipped = result.get("parse_skipped", [])
        ok = gap_count <= args.max_gaps
        strict_fail = bool(args.strict_parse and skipped)
        report = {"ok": ok, "gap_count": gap_count, "max_gaps": args.max_gaps,
                  "gaps": result["gaps"], "parse_skipped_count": len(skipped),
                  "parse_skipped": skipped,
                  "tracked_py_count": result["tracked_py"], "untracked_py_count": result["untracked_py"]}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("[untracked-gaps] 仓库: %s | 已跟踪 .py %d | 未跟踪 .py %d | 引用缺口 %d（阈值 ≤ %d）"
                  % (repo, result["tracked_py"], result["untracked_py"], gap_count, args.max_gaps))
            for row in result["gaps"]:
                print("[untracked-gaps] [缺口] %s:%d → %s" % (row["importer"], row["line"], row["ref"]))
            if skipped:
                print("[untracked-gaps] [解析跳过] %d 个文件未能解析（import 不可判定）" % len(skipped))
                for item in skipped:
                    print("[untracked-gaps] [解析跳过] %s → %s" % (item["file"], item["error"][:200]))
            if strict_fail:
                print("[untracked-gaps] RESULT: FAIL（--strict-parse：解析跳过 %d 个，退出码 4）" % len(skipped))
            elif ok:
                print("[untracked-gaps] RESULT: PASS（缺口 %d ≤ 阈值 %d；解析跳过 %d）"
                      % (gap_count, args.max_gaps, len(skipped)))
            else:
                print("[untracked-gaps] RESULT: FAIL（缺口 %d > 阈值 %d，退出码 3）"
                      % (gap_count, args.max_gaps))
        if strict_fail:
            return 4
        return 0 if ok else 3
    except Exception as exc:  # 统一兜底：任何异常路径都不得返回 0
        print("[untracked-gaps] [错误] %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
