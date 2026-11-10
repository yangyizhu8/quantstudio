#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-staged-scope —— 提交范围门（门1，2026-11-11 派单）

比对 **git index 变更集** 是否全部落在「本会话声明的路径白名单」内；不符即拒。
存在动因：多会话共享一个工作树，他线已 staged 的文件会被 `git commit -F msg`（不带 -a）一并带走
（2026-11-02 AQ-DW 实测 4 个 review-kb/ 文件；2026-11-10 批五十一自纠再发），
「我只 add 了三个路径」不构成安全推定。

用法（挂 .githooks/pre-commit，亦可手动跑）：
  STAGED_SCOPE_ALLOW="scripts/kb_hook.py tests/ docs/evidence/" python scripts/check-staged-scope.py
  python scripts/check-staged-scope.py --allow scripts/kb_hook.py --allow "tests/"
  python scripts/check-staged-scope.py --allow-file .session-scope.txt
  python scripts/check-staged-scope.py --index-file <列表文件>     # 自测/离线核对（不读 git）
  python scripts/check-staged-scope.py --self-test

白名单语义（任一条命中即放行）：
  - 单值 `*`                             → 全员放行（显式声明，审计可见）
  - 精确路径 `scripts/a.py`               → 该文件
  - 目录前缀 `tests/`（或以该目录开头）   → 该目录下全部
  - glob `docs/handoff/*.md`             → fnmatch 匹配

退出码：0＝全部在白名单内｜1＝有越界或未声明白名单（拒）｜2＝环境错误（非 git 仓库等）
纪律：**未声明白名单 ⇒ 拒**（fail-closed）。这是门，不是建议——跳过必须显式写 `*`。
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
from fnmatch import fnmatch

DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_index(repo, index_file=None):
    """读 index 变更集（相对路径列表）。index_file 供离线/自测注入。"""
    if index_file:
        with io.open(index_file, encoding="utf-8") as fh:
            return [ln.strip().replace("\\", "/") for ln in fh if ln.strip()]
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "diff", "--cached", "--name-only",
             "--diff-filter=ACMRTUXB"], stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError) as e:
        raise RuntimeError("读取 git index 失败：%s" % e)
    text = out.decode("utf-8", errors="replace")
    return [ln.strip().replace("\\", "/") for ln in text.splitlines() if ln.strip()]


def parse_allowlist(args_allow, allow_file, env_value):
    """合并三处来源 → 规整后的白名单条目列表。"""
    items = []
    for v in (args_allow or []):
        items.append(v)
    if allow_file:
        with io.open(allow_file, encoding="utf-8-sig") as fh:
            for ln in fh:
                s = ln.strip()
                if s and not s.startswith("#"):
                    items.append(s)
    if env_value:
        for part in env_value.replace("\n", ";").split(";"):
            s = part.strip()
            if s:
                items.append(s)
    return [i.replace("\\", "/").strip() for i in items if i.strip()]


def covered(path, allow):
    """单路径是否被白名单覆盖。"""
    for a in allow:
        if a == "*":
            return True
        if a.endswith("/"):
            if path == a[:-1] or path.startswith(a):
                return True
        elif path == a:
            return True
        elif "*" in a or "?" in a or "[" in a:
            if fnmatch(path, a):
                return True
        elif path.startswith(a + "/"):   # 目录写法不带斜杠也认
            return True
    return False


def check(index_paths, allow, label=""):
    """→ (ok, out_of_scope[], payload)。allow 为空 ⇒ 拒（fail-closed）。

    例外：index 为空 ⇒ 放行（无内容可提交，pre-commit 不应无意义阻断）；
    空 index + 未声明白名单仍记 `empty-index` 供审计。
    """
    payload = {
        "label": label,
        "declared": allow,
        "index_count": len(index_paths),
        "index": index_paths,
        "out_of_scope": [],
        "verdict": "PASS",
        "reason": "",
    }
    if not index_paths:
        payload["reason"] = "empty-index"
        return True, [], payload
    if not allow:
        payload["verdict"] = "FAIL"
        payload["reason"] = "no-allowlist-declared"
        return False, list(index_paths), payload
    oos = [p for p in index_paths if not covered(p, allow)]
    if oos:
        payload["verdict"] = "FAIL"
        payload["reason"] = "out-of-scope"
        payload["out_of_scope"] = oos
        return False, oos, payload
    return True, [], payload


HINT = """\
[staged-scope] 提交被拒：index 变更集不在本会话声明的白名单内。

修复方式（三选一，推荐第一条）：
  1) 声明本会话白名单后重试提交：
       PowerShell : $env:STAGED_SCOPE_ALLOW="scripts/a.py;tests/;docs/evidence/"
       Bash       : export STAGED_SCOPE_ALLOW="scripts/a.py;tests/;docs/evidence/"
  2) 白名单写文件：printf 'scripts/a.py\\ntests/\\n' > .session-scope.txt
       （pre-commit 会读 .session-scope.txt；该文件不应提交）
  3) 确认本次确实要提交 index 内全部内容：STAGED_SCOPE_ALLOW="*"（显式全员放行，审计可见）

若越界文件是他线在途产物：请勿提交。用 `git restore --staged <path>` 移出 index 后重试。
"""


def self_test():
    """负例回归：五条断言全过才 exit 0（含派单指定负例：add -A 带入他线文件 ⇒ 拒）。"""
    cases = []

    def case(desc, index, allow, expect_ok):
        ok, oos, payload = check(index, allow, desc)
        cases.append({"case": desc, "expect_ok": expect_ok, "got_ok": ok,
                      "out_of_scope": oos, "reason": payload["reason"],
                      "status": "OK" if ok == expect_ok else "MISMATCH"})
        print("  [%s] %s → %s (expect %s)%s" % (
            cases[-1]["status"], desc, "PASS" if ok else "FAIL",
            "PASS" if expect_ok else "FAIL",
            ("  越界=%s" % oos) if oos else ""))

    # 1) 全部在白名单内：放行
    case("all-covered", ["scripts/kb_hook.py", "tests/test_x.py"],
         ["scripts/kb_hook.py", "tests/"], True)
    # 2) 派单负例：git add -A 带入他线文件 ⇒ 必须拒
    case("add-A-with-foreign-file",
         ["scripts/kb_hook.py", "review-kb/daily/2026-11-11-复盘报告.md"],
         ["scripts/kb_hook.py"], False)
    # 3) 未声明白名单 ⇒ 必须拒（fail-closed）
    case("no-allowlist", ["scripts/kb_hook.py"], [], False)
    # 4) 显式 * ⇒ 放行
    case("explicit-star", ["anything/at/all.txt", "b.md"], ["*"], True)
    # 5) glob 匹配
    case("glob-match", ["docs/handoff/x.md"], ["docs/handoff/*.md"], True)
    # 6) index 为空 ⇒ 放行（无内容可提交时不过度阻断）
    case("empty-index", [], [], True)

    bad = [c for c in cases if c["status"] != "OK"]
    if bad:
        print("SELF_TEST: FAIL %s" % [c["case"] for c in bad])
        return 1
    print("SELF_TEST: PASS (%d/%d)" % (len(cases), len(cases)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="提交范围门：index 变更集 ⊆ 会话白名单")
    ap.add_argument("--allow", action="append", default=[], help="白名单条目（可重复）")
    ap.add_argument("--allow-file", default=None, help="白名单文件（一行一条，# 注释）")
    ap.add_argument("--index-file", default=None, help="离线 index 清单（一行一路径；不给则读 git）")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="仓库根")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--label", default="", help="本次提交标识（写进输出，便于审计）")
    ap.add_argument("--self-test", action="store_true", help="内置负例回归")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    allow = parse_allowlist(a.allow, a.allow_file, os.environ.get("STAGED_SCOPE_ALLOW"))
    # 兜底：仓库根的 .session-scope.txt（多会话各自写自己的一份，但同名文件需协同）
    if not allow:
        local = os.path.join(a.repo, ".session-scope.txt")
        if os.path.isfile(local):
            allow = parse_allowlist([], local, None)

    try:
        index = read_index(a.repo, a.index_file)
    except RuntimeError as e:
        print("[staged-scope] 环境错误：%s" % e, file=sys.stderr)
        return 2

    ok, oos, payload = check(index, allow, a.label)
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[staged-scope] index=%d 件  白名单=%s  verdict=%s" % (
            len(index), ("%d 条" % len(allow)) if allow else "**未声明**", payload["verdict"]))
        for p in index:
            mark = "  " if covered(p, allow) else "!!"
            print("  %s %s" % (mark, p))
    if not ok:
        if not a.json:
            print(HINT, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
