#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_dependency_closure.py —— 依赖闭包门（R66-G2 修复 · 计划项 b，fail-closed）

方案：docs/r66-dependency-closure-gap-repair-design-20261102.md §三(b)。push 前校验
「本次待推送文件集」的 import 闭包是否完整落在 <head> 树内，拦截「导入方已提交、被引用
模块未入库 ⇒ 远端 CI 必挂」断裂（簇 A qmt_deploy / 簇 B delivery 同型）。
用法：python scripts/check_dependency_closure.py --base <base> [--head HEAD] [--json]
      python scripts/check_dependency_closure.py --files <路径>... [--allow 条目...]
      python scripts/check_dependency_closure.py --allowlist-file <json> | --self-test
判定（A4 三态）：∈ 待推集 ⇒ OK（随推）；已在 <head> 树 ⇒ OK；工作区存在但不在 <head> 树
⇒ 缺口（exit 2）；一级包 {quantstudio, tests, scripts, config} 模块解析不到任何文件 ⇒ 缺口；其余（标准库/三方）⇒ 跳过。相对导入按文件所在包上溯换算为仓库相对路径。
白名单：默认 scripts/dependency_closure_allowlist.json（缺失即空）；字符串列表或
{"allow":[...]}；条目支持精确路径/fnmatch/目录前缀/模块名；命中 ⇒ 豁免不阻断。
退出码：0=通过；2=存在未豁免缺口（fail-closed 阻断）；1=脚本自身错误（不得静默放行）。"""
from __future__ import annotations
import argparse
import ast
import fnmatch
import json
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

PKG_ROOTS = ("quantstudio", "tests", "scripts")                   # 绝对导入候选拼接根（A3）
FIRST_PARTY_TOPS = {"quantstudio", "tests", "scripts", "config"}  # 一等方顶层名（A5）
DEFAULT_ALLOWLIST = "scripts/dependency_closure_allowlist.json"
SCRIPT_PATH = Path(__file__).resolve()
REASON_TXT = {"gap_worktree": "工作区存在但未入库", "gap_missing": "一等方模块未解析到文件",
              "gap_parse": "待推文件语法解析失败（import 闭包不可判定）"}


def run_git(repo: Path, args: list, check: bool = True):
    """统一 git 调用：禁 shell=True、UTF-8 解码、关 quotepath 保中文路径原样。"""
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(repo),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("git %s 失败（rc=%d）：%s"
                           % (" ".join(args), proc.returncode, proc.stderr.strip()))
    return proc


def resolve_repo_root(explicit: str | None) -> Path:
    """仓库根定位：--repo 显式指定 > git 顶层目录 > 脚本上级目录兜底（A9）。"""
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise RuntimeError("--repo 指定的目录不存在：%s" % explicit)
        return root
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return SCRIPT_PATH.parent.parent


def load_allowlist(path: Path) -> list:
    """白名单 JSON：顶层字符串列表或 {"allow": [...]}；缺失视为空；格式错即脚本错误（A6）。"""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("白名单文件读取/解析失败（%s）：%s" % (path, exc))
    entries = data if isinstance(data, list) else data.get("allow") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(isinstance(x, str) for x in entries):
        raise RuntimeError('白名单格式不支持（须为字符串列表或 {"allow": [...]}）：%s' % path)
    return [x for x in entries if x.strip()]


def match_allow(ref_path: str | None, module: str, entries: list) -> str | None:
    """白名单匹配：精确路径 / fnmatch / 目录前缀 / 模块名（精确、点前缀或 fnmatch）。"""
    for entry in entries:
        pat = entry.replace("\\", "/").rstrip("/")
        if not pat:
            continue
        if ref_path and (ref_path == pat or fnmatch.fnmatch(ref_path, pat)
                         or ref_path.startswith(pat + "/")):
            return entry
        if module and (module == pat or module.startswith(pat + ".") or fnmatch.fnmatch(module, pat)):
            return entry
    return None


def parse_imports(source: str, rel_path: str) -> list:
    """AST 解析 import → [{line, module, level, names}]。

    BOM 容错：去掉首部 U+FEFF（Windows 编辑器常见）——否则 ast.parse 报
    invalid non-printable character U+FEFF 造成假阻断。
    语法错误仍 fail-closed：抛 RuntimeError，由调用方记为缺口（阻断推送）。
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
                         "names": [a.name for a in node.names]})
    return refs


class ClosureChecker:
    """闭包判定器：持有 待推集 / <head> 树文件集 / 工作区 三通道，负责解析与三态判定。"""

    def __init__(self, repo: Path, head_arg: str, push_set: set) -> None:
        self.repo = repo
        self.head_sha = run_git(repo, ["rev-parse", "--verify", "%s^{commit}" % head_arg]).stdout.strip()
        self.push_set = push_set
        # <head> 树全量路径集（等价逐路径 git cat-file -e <head>:<path>，单次取齐）
        self.head_files = {ln.strip() for ln in run_git(repo, ["ls-tree", "-r", "--name-only",
                       self.head_sha]).stdout.splitlines() if ln.strip()}

    def exists_somewhere(self, rel: str) -> bool:
        """候选路径命中三通道之一：待推集 / <head> 树 / 工作区。"""
        return rel in self.push_set or rel in self.head_files or (self.repo / rel).is_file()

    def classify(self, target: str) -> str:
        """三态判定（A4）：ok_push > ok_head > gap_worktree；不可确认一律按缺口（fail-closed）。"""
        if target in self.push_set:
            return "ok_push"
        if target in self.head_files:
            return "ok_head"
        return "gap_worktree" if (self.repo / target).is_file() else "gap_missing"  # 后者理论不可达

    def resolve_abs(self, module: str) -> str | None:
        """绝对模块名 → 仓库内文件路径；仅一等方根参与候选拼接（A3）。"""
        parts = [p for p in module.split(".") if p]
        if not parts or parts[0] not in PKG_ROOTS:
            return None
        rel = "/".join(parts)
        for cand in (rel + ".py", rel + "/__init__.py"):
            if self.exists_somewhere(cand):
                return cand
        return None

    def _disp(self, path: str, fallback: str) -> str:
        p = path[:-len("/__init__.py")] if path.endswith("/__init__.py") else path[:-3]
        dotted = p.replace("/", ".")
        return dotted if dotted.split(".")[0] in PKG_ROOTS else fallback

    def resolve_ref(self, ref: dict, importer_rel: str) -> list:
        """单条 import → [(目标文件|None, 模块显示名, 是否相对导入)]。from X import a, b：
        X 本体与可解析子模块 X.a 均产出判定目标（X 必须可导入，属性名由 X 本体覆盖）；
        相对导入按文件所在包上溯换算，越界/顶层裸文件 ⇒ 未解析（保守判缺口）。"""
        out = []
        if ref["level"] == 0:
            base = ref["module"]
            base_hit = self.resolve_abs(base)
            if base_hit:
                out.append((base_hit, base, False))
            n0 = len(out)
            for name in ref["names"]:
                hit = self.resolve_abs(base + "." + name)
                if hit:
                    out.append((hit, base + "." + name, False))
            if not base_hit and len(out) == n0:
                out.append((None, base, False))
            return out
        cur = PurePosixPath(importer_rel).parent
        up = ref["level"] - 1
        display = "." * ref["level"] + (("." + ref["module"]) if ref["module"] else "")
        if up > len(cur.parts) or not cur.parts:
            return [(None, display, True)]
        for _ in range(up):
            cur = cur.parent
        if not cur.parts:
            return [(None, display, True)]
        base_rel = "/".join([*cur.parts, *(p for p in ref["module"].split(".") if p)])
        base_cands = ((base_rel + "/__init__.py",) if not ref["module"]
                      else (base_rel + ".py", base_rel + "/__init__.py"))
        base_hit = next((c for c in base_cands if self.exists_somewhere(c)), None)
        if base_hit:
            out.append((base_hit, self._disp(base_hit, display), True))
        sub_hit = False
        for name in ref["names"]:
            for cand in (base_rel + "/" + name + ".py", base_rel + "/" + name + "/__init__.py"):
                if self.exists_somewhere(cand):
                    out.append((cand, self._disp(cand, display + "." + name), True))
                    sub_hit = True
                    break
        if not base_hit and not sub_hit:
            out.append((None, display, True))
        return out

    def read_source(self, rel: str, prefer_worktree: bool) -> str:
        """读待推 .py：files 模式优先工作区；diff 模式取 <head> 树（被推即已提交内容）。"""
        path = self.repo / rel
        if prefer_worktree and path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise RuntimeError("待推文件读取失败（%s）：%s" % (rel, exc))
        if rel in self.head_files:
            return run_git(self.repo, ["show", "%s:%s" % (self.head_sha, rel)]).stdout
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError("待推 .py 不可读（工作区与 <head> 树均不存在）：%s" % rel)


def run_check(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args.repo)
    if not args.files and not args.base:
        raise RuntimeError("须给出 --base <base>（diff 模式）或 --files <路径>...（显式待推集）之一")
    mode = "files" if args.files else "diff"
    if args.files:
        push_list = []
        for raw in args.files:
            p = Path(raw)
            try:
                p = p.resolve().relative_to(repo) if p.is_absolute() else PurePosixPath(p.as_posix())
            except ValueError:
                raise RuntimeError("--files 路径不在仓库内：%s" % raw)
            push_list.append(p.as_posix())
    else:
        run_git(repo, ["rev-parse", "--verify", "%s^{commit}" % args.base])  # base 无效即错误
        out = run_git(repo, ["diff", "--name-only", "%s..%s" % (args.base, args.head)]).stdout
        push_list = [ln.strip() for ln in out.splitlines() if ln.strip()]
    push_set = set(push_list)
    checker = ClosureChecker(repo, args.head, push_set)
    if args.files:  # 显式待推集须真实可读（fail-closed：传错路径不得静默通过）
        missing = [p for p in push_list if not (repo / p).is_file() and p not in checker.head_files]
        if missing:
            raise RuntimeError("--files 文件不存在（工作区与 <head> 树均无）：%s" % ", ".join(missing))
    allowlist_path = Path(args.allowlist_file) if args.allowlist_file else repo / DEFAULT_ALLOWLIST
    allow_entries = load_allowlist(allowlist_path) + list(args.allow or [])

    rows, counts, parsed = {}, {"ok_push": 0, "ok_head": 0, "skip_ext": 0}, []
    for rel in sorted(push_set):
        if not rel.endswith(".py") or (mode == "diff" and rel not in checker.head_files):
            continue  # 非 .py 不解析；区间内被删/改名旧路径无内容可解析
        parsed.append(rel)
        source = checker.read_source(rel, prefer_worktree=(mode == "files"))
        try:
            refs = parse_imports(source, rel)
        except RuntimeError as exc:
            # fail-closed：待推 .py 语法错误 ⇒ import 闭包不可判定 ⇒ 记为缺口（阻断）
            rows[(rel, "module::PARSE_ERROR")] = {"importer": rel, "line": 1, "target": None,
                                                  "module": "(语法解析失败) " + str(exc),
                                                  "status": "gap_parse"}
            continue
        for ref in refs:
            for target, module, is_rel in checker.resolve_ref(ref, rel):
                if target is None:
                    if not (is_rel or module.split(".", 1)[0] in FIRST_PARTY_TOPS):
                        counts["skip_ext"] += 1
                        continue
                    status = "gap_missing"
                else:
                    status = checker.classify(target)
                key = (rel, target if target else "module::" + module)
                row = rows.setdefault(key, {"importer": rel, "line": ref["line"], "target": target,
                                            "module": module, "status": status})
                row["line"] = min(row["line"], ref["line"])

    gaps, whitelisted = [], []
    for key in sorted(rows):
        row = rows[key]
        if row["status"] in ("ok_push", "ok_head"):
            counts[row["status"]] += 1
            continue
        basis = match_allow(row["target"], row["module"], allow_entries)
        if basis:
            row["allow"] = basis
            whitelisted.append(row)
        else:
            gaps.append(row)

    report = {"ok": not gaps, "base": args.base, "head": args.head, "head_sha": checker.head_sha,
              "mode": mode, "files": sorted(push_set), "parsed_py": parsed,
              "gaps": [{k: r[k] for k in ("importer", "line", "target", "module", "status")} for r in gaps],
              "whitelisted": [{k: r[k] for k in ("importer", "line", "target", "module", "status", "allow")}
                              for r in whitelisted],
              "counts": {**counts, "gaps": len(gaps), "whitelisted": len(whitelisted)},
              "allowlist": str(allowlist_path)}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if gaps else 0
    print("[dep-closure] 仓库: %s | 模式: %s | base: %s | head: %s(%s)"
          % (repo, mode, args.base or "-", args.head, checker.head_sha[:12]))
    print("[dep-closure] 待推文件 %d 个（.py 解析 %d 个）| 随推 %d / 已在树 %d / 外部跳过 %d / 缺口 %d / 豁免 %d"
          % (len(push_set), len(parsed), counts["ok_push"], counts["ok_head"],
             counts["skip_ext"], len(gaps), len(whitelisted)))
    for row in whitelisted + gaps:
        extra = " | 依据: %s" % row["allow"] if row.get("allow") else ""
        print("[dep-closure] [%s] %s:%d → %s（%s%s）"
              % ("已豁免" if row.get("allow") else "缺口", row["importer"], row["line"],
                 row["target"] or "(模块未解析) " + row["module"], REASON_TXT[row["status"]], extra))
    if gaps:
        print("[dep-closure] RESULT: FAIL（未豁免缺口 %d 项，fail-closed 阻断，退出码 2）" % len(gaps))
    else:
        print("[dep-closure] RESULT: PASS（依赖闭包完整；豁免 %d 项）" % len(whitelisted))
    return 2 if gaps else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="依赖闭包门：校验待推送文件集 import 闭包完整落在 <head> 树（fail-closed）。")
    parser.add_argument("--repo", help="仓库根（默认 git 顶层，失败回退脚本上级目录）")
    parser.add_argument("--base", help="diff 模式基线（git diff --name-only <base>..<head>）")
    parser.add_argument("--head", default="HEAD", help="上界提交（默认 HEAD）")
    parser.add_argument("--files", nargs="+", action="extend", help="显式待推集（仓库根相对路径，覆盖 --base）")
    parser.add_argument("--allow", action="append", help="白名单条目（路径或模块名，可重复）")
    parser.add_argument("--allowlist-file", help="白名单 JSON（默认 scripts/dependency_closure_allowlist.json）")
    parser.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    parser.add_argument("--self-test", action="store_true", help="合成仓库自证三态（不碰本仓）")
    return parser


def self_test() -> int:
    """合成 git 仓库自证（A10，自建自销不碰本仓）：缺模块⇒2 / 随待推集⇒0 / 白名单⇒0。"""
    n_pass = n_all = 0

    def check(name, ok, detail=""):
        nonlocal n_pass, n_all
        n_all += 1
        n_pass += 1 if ok else 0
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "｜" + detail[:300]))

    def gitx(repo, *a):
        subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *a],
                       check=True, capture_output=True, text=True, encoding="utf-8")

    def build(td, files, commits):
        repo = Path(td)
        gitx(repo, "init", "-q")
        for rel, text in files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        base = ""
        for paths in commits:
            gitx(repo, "add", "--", *paths)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=selftest", "-c",
                            "user.email=selftest@local", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "c"], check=True, capture_output=True,
                           text=True, encoding="utf-8")
            base = base or subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                          capture_output=True, text=True,
                                          encoding="utf-8").stdout.strip()
        return repo, base

    def run_script(repo, *extra):
        return subprocess.run([sys.executable, str(SCRIPT_PATH), "--repo", str(repo), *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def json_of(proc):
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    init = {"quantstudio/__init__.py": ""}
    # S1 缺模块（工作区未入库 + 一等方未解析 ⇒ 双缺口，exit 2）
    with tempfile.TemporaryDirectory(prefix="depcl_st1_") as td:
        repo, base = build(td, {**init, "quantstudio/imp1.py":
                                "import quantstudio.dep1\nimport quantstudio.ghost1\n",
                                "quantstudio/dep1.py": "X = 1\n"},
                           [["quantstudio/__init__.py"], ["quantstudio/imp1.py"]])
        proc = run_script(repo, "--base", base, "--head", "HEAD", "--json")
        rep = json_of(proc)
        refs = {g["target"] or g["module"] for g in rep["gaps"]} if rep else set()
        check("S1 缺模块 ⇒ exit 2（双缺口明细正确）", proc.returncode == 2 and rep is not None
              and rep["ok"] is False and len(rep["gaps"]) == 2 and "quantstudio/dep1.py" in refs
              and "quantstudio.ghost1" in refs, "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
    # S2 模块随待推集（--files 显式待推集，未提交但随推 ⇒ exit 0）
    with tempfile.TemporaryDirectory(prefix="depcl_st2_") as td:
        repo, _ = build(td, {**init, "quantstudio/imp2.py": "import quantstudio.dep2\n",
                             "quantstudio/dep2.py": "X = 2\n"}, [["quantstudio/__init__.py"]])
        proc = run_script(repo, "--files", "quantstudio/imp2.py", "quantstudio/dep2.py", "--json")
        rep = json_of(proc)
        check("S2 模块随待推集 ⇒ exit 0", proc.returncode == 0 and rep is not None
              and rep["ok"] is True, "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
    # S3 白名单 --allow 通道（diff 模式缺口 ⇒ exit 0 且人读输出标记豁免）
    with tempfile.TemporaryDirectory(prefix="depcl_st3_") as td:
        repo, base = build(td, {**init, "quantstudio/imp4.py": "import quantstudio.dep4\n",
                                "quantstudio/dep4.py": "X = 4\n"},
                           [["quantstudio/__init__.py"], ["quantstudio/imp4.py"]])
        proc = run_script(repo, "--base", base, "--head", "HEAD", "--allow", "quantstudio/dep4.py")
        check("S3 --allow 命中 ⇒ exit 0 且标记豁免", proc.returncode == 0 and "豁免" in proc.stdout
              and "quantstudio/dep4.py" in proc.stdout, "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
    # S4 白名单 allowlist 文件通道（模块名条目 ⇒ exit 0，JSON whitelisted=1）
    with tempfile.TemporaryDirectory(prefix="depcl_st4_") as td:
        repo, base = build(td, {**init, "quantstudio/imp5.py": "import quantstudio.dep5\n",
                                "quantstudio/dep5.py": "X = 5\n"},
                           [["quantstudio/__init__.py"], ["quantstudio/imp5.py"]])
        allowlist = Path(td) / "allow.json"
        allowlist.write_text(json.dumps({"allow": ["quantstudio.dep5"]}), encoding="utf-8")
        proc = run_script(repo, "--base", base, "--head", "HEAD",
                          "--allowlist-file", str(allowlist), "--json")
        rep = json_of(proc)
        check("S4 allowlist 文件命中 ⇒ exit 0 且 whitelisted=1", proc.returncode == 0
              and rep is not None and rep["ok"] is True and len(rep["whitelisted"]) == 1
              and len(rep["gaps"]) == 0, "rc=%d %s" % (proc.returncode, proc.stdout[:300]))
    print("[self-test] %s：%d/%d 通过" % ("全绿" if n_pass == n_all else "存在失败", n_pass, n_all))
    return 0 if n_pass == n_all else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 控制台中文输出兜底
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args()
    try:
        return self_test() if args.self_test else run_check(args)
    except Exception as exc:  # 统一兜底：任何异常路径都不得返回 0
        print("[dep-closure] [错误] %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
