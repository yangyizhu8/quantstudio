#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部贡献策略的静态准入扫描器（策略生态准入 · D2a，2026-09-19）。

定位与边界（重要）
    本扫描器是**降低误伤 + 暴露明显恶意**的静态门，**不是安全边界**。
    Python 无法在纯静态层面穷尽动态构造（字符串拼接、反射、编码执行等）；
    真正的边界是「本地沙箱试跑（D3）+ 所有者人工审查」。与方案 R4 一致。

与 StrategyIsolationGuard 的关系
    清单为 Guard 的**超集**，但**独立实现**（不 import Guard）——避免
    「用被测对象检查被测对象」。一致性由 tests/ 的 parity 测试断言防漂移。

误伤纪律（2026-09-19 首轮自测教训，已固化）
    「零误伤」是本门的一等指标，与「检出恶意」并列。首轮实现曾在存量 37 策略上
    误伤 48 项，根因三处，均已修正：
      ① 属性检查按**名字**匹配 → `str.replace` / `DataFrame.rename` 被误杀
         ⇒ 改为**模块限定**（仅当接收者是 os/shutil 等高危模块时才判）；
      ② 文本片段扫描把 `_qs_px_exec(` 误判为 `exec(` ⇒ 改为**词边界**且
         **降级为 WARNING（不阻断）**——AST 已精确覆盖真实调用；
      ③ 目录剪枝失效导致 29 个 `__pycache__/*.pyc` 被纳入 ⇒ 改用 `os.walk` 原地剪枝。

用法
    python scripts/scan_contrib_strategy.py <文件或目录> [...]
    python scripts/scan_contrib_strategy.py --no-ext-check quantstudio/backtest/strategies
    退出码：0 = 通过（可含 WARNING）；1 = 存在 FAIL；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import stat
import sys
from pathlib import Path

# ---------------------------------------------------------------- 禁用清单

# 禁止导入的模块（前缀匹配：`x` 命中 `x` 与 `x.y`）
FORBIDDEN_IMPORT_PREFIXES = (
    # —— 继承 StrategyIsolationGuard ——
    "duckdb", "sqlite3", "sqlalchemy", "psycopg2", "pymysql",
    "quantstudio.pipeline", "quantstudio.backtest.providers", "quantstudio._paths",
    # —— 网络 ——
    "socket", "requests", "urllib", "http", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "websocket", "paramiko",
    # —— 进程 / 系统 ——
    "subprocess", "multiprocessing", "pty", "ctypes", "mmap",
    # —— 动态执行 / 反射 ——
    "importlib", "imp", "runpy", "codeop",
    # —— 反序列化 ——
    "pickle", "marshal", "shelve", "dill", "jsonpickle",
    # —— 文件系统 ——
    "shutil", "tempfile", "fileinput",
    # —— 其它 ——
    "webbrowser", "winreg", "resource",
)

# 禁止直接调用的内建函数（按名字，精确匹配）
# 说明（2026-09-19 首轮自测教训）：`globals`/`locals`/`vars` **只读命名空间、不执行代码**，
# 且 `'x' in locals()` 是常见的防御性写法（存量 vol_regime_mom_rev_quantstudio.py:439
# 即此用法）⇒ 从清单移除，避免误伤。真正的高危项是 eval / exec / compile / __import__。
# `breakpoint`（交互调试器）与 `input`（读 stdin）在 CI 环境无意义且可用于阻塞，保留禁用。
FORBIDDEN_BUILTINS = {
    "eval", "exec", "compile", "__import__", "breakpoint", "input",
}

# 禁止直接调用的裸名（继承 Guard 的 I/O 类）
FORBIDDEN_BARE_CALLS = {
    "open", "read_csv", "read_parquet", "read_sql", "read_pickle",
}

# 【修正①】禁止的「模块.属性」调用——**必须接收者是这些模块名才判**，
# 避免把 str.replace / DataFrame.rename 之类常用方法误杀。
FORBIDDEN_MODULE_CALLS: dict[str, set[str]] = {
    "os": {
        "system", "popen", "remove", "unlink", "rmdir", "removedirs",
        "chmod", "chown", "link", "symlink", "rename", "replace",
        "execv", "execve", "execvp", "execl", "spawnv", "spawnl",
        "fork", "kill", "killpg", "setuid", "setgid", "putenv",
    },
    "shutil": {
        "rmtree", "copy", "copy2", "copyfile", "copytree", "move",
        "chown", "chmod", "disk_usage",
    },
    "subprocess": {"__any__"},
    "socket": {"__any__"},
    "pathlib": {"__any__"},          # Path 的文件操作经 import 层已拦，此处兜底
    "sys": {"setrecursionlimit", "settrace", "setprofile", "exit"},
    "gc": {"__any__"},
}

# 文本片段扫描：**仅作为 WARNING 提示**（词边界匹配，不阻断）
TEXT_WARN_PATTERNS = (
    r"\b__import__\s*\(",
    r"\bimportlib\b",
    r"\bos\.system\s*\(",
    r"\bos\.popen\s*\(",
    r"\bsubprocess\.\w+",
    r"\bsocket\.\w+",
    r"\bpickle\.\w+",
    r"\bmarshal\.\w+",
    r"(?<![A-Za-z0-9_])eval\s*\(",
    r"(?<![A-Za-z0-9_])exec\s*\(",
)

ALLOWED_EXT = {".py", ".md"}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".idea", ".vscode"}

# ---------------------------------------------------------------- 检查实现


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return str(p)


def check_entry_type(path: Path, *, ext_check: bool = True) -> list[str]:
    """符号链接 / 非常规文件 / 扩展名（审计意见②）。"""
    out: list[str] = []
    if path.is_symlink():
        out.append("禁止符号链接：%s（实体可指向目录外，绕过路径门语义）" % path)
        return out
    try:
        st = path.lstat()
    except OSError as exc:
        return ["无法读取文件属性：%s：%s" % (path, exc)]
    if not stat.S_ISREG(st.st_mode):
        return ["非常规文件类型：%s（mode=%o，仅接受普通文件）" % (path, st.st_mode)]
    if ext_check and path.suffix.lower() not in ALLOWED_EXT:
        out.append("扩展名不允许：%s（仅接受 %s）"
                   % (path, "/".join(sorted(ALLOWED_EXT))))
    return out


def check_imports(tree: ast.AST, rel: str) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            for pref in FORBIDDEN_IMPORT_PREFIXES:
                if name == pref or name.startswith(pref + "."):
                    out.append("%s:%d FAIL 禁止导入 %s（命中 %s）"
                               % (rel, node.lineno, name, pref))
    return out


def _module_name(func: ast.AST):
    """取出 `os.system` 形式里的模块名 `os`（仅支持单层 Name.Attr）。"""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return None


def check_calls(tree: ast.AST, rel: str) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in FORBIDDEN_BUILTINS:
                out.append("%s:%d FAIL 禁止调用内建 %s()" % (rel, node.lineno, func.id))
            elif func.id in FORBIDDEN_BARE_CALLS:
                out.append("%s:%d FAIL 禁止直接 I/O 或数据读取 %s()"
                           % (rel, node.lineno, func.id))
        else:
            mod = _module_name(func)
            if mod and mod in FORBIDDEN_MODULE_CALLS:
                allowed = FORBIDDEN_MODULE_CALLS[mod]
                if "__any__" in allowed or func.attr in allowed:
                    out.append("%s:%d FAIL 禁止调用 %s.%s()（高危模块调用）"
                               % (rel, node.lineno, mod, func.attr))
    return out


def check_text_warnings(src: str, rel: str) -> list[str]:
    """文本片段扫描——**仅提示，不阻断**（AST 已精确覆盖真实调用）。"""
    out: list[str] = []
    pats = [re.compile(p) for p in TEXT_WARN_PATTERNS]
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        for pat in pats:
            m = pat.search(line)
            if m:
                out.append("%s:%d WARN 文本命中 `%s` —— 请人工确认是否为真实调用"
                           % (rel, i, m.group(0)))
                break
    return out


def check_file(path: Path, *, ext_check: bool = True) -> tuple[list[str], list[str]]:
    """返回 (fails, warns)。"""
    fails = check_entry_type(path, ext_check=ext_check)
    if fails:
        return fails, []
    if path.suffix.lower() != ".py":
        return [], []
    try:
        src = path.read_text(encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        return ["无法读取：%s：%s" % (path, exc)], []
    rel = _rel(path)
    try:
        tree = ast.parse(src, filename=rel)
    except SyntaxError as exc:
        return ["语法错误：%s:%s %s" % (rel, exc.lineno, exc.msg)], []
    fails += check_imports(tree, rel)
    fails += check_calls(tree, rel)
    return fails, check_text_warnings(src, rel)


def iter_targets(targets: list[str]) -> list[Path]:
    """目录用 os.walk 原地剪枝——修正首轮 rglob 剪枝失效（29 个 .pyc 被纳入）。"""
    out: list[Path] = []
    for t in targets:
        p = Path(t)
        if not p.is_dir():
            out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                out.append(Path(root) / name)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="外部贡献策略静态准入扫描器")
    ap.add_argument("targets", nargs="+", help="文件或目录")
    ap.add_argument("--no-ext-check", action="store_true",
                    help="跳过扩展名检查（对存量目录做基线验证时使用）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论")
    args = ap.parse_args(argv)

    files = [f for f in iter_targets(args.targets) if not f.is_dir()]
    if not files:
        print("没有可扫描的文件", file=sys.stderr)
        return 2

    n_fail = n_warn = 0
    for f in files:
        fails, warns = check_file(f, ext_check=not args.no_ext_check)
        if fails:
            n_fail += 1
            print("FAIL %s" % _rel(f), file=sys.stderr)
            for x in fails:
                print("  - %s" % x, file=sys.stderr)
        if warns and not args.quiet:
            n_warn += 1
            for x in warns[:3]:
                print("  (warn) %s" % x, file=sys.stderr)
            if len(warns) > 3:
                print("  (warn) ... 另有 %d 条同类提示" % (len(warns) - 3), file=sys.stderr)

    print("-" * 62)
    print("扫描 %d 个文件：FAIL %d 个，含 WARNING %d 个" % (len(files), n_fail, n_warn))
    if n_fail:
        print("结论：FAIL —— 存在禁用项，请修正后重新提交"
              "（本门为静态过滤，不代表安全性结论）")
        return 1
    print("结论：PASS —— 未发现禁用项"
          "（本门为静态过滤，不代表安全性结论；沙箱与人工审查另行执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
