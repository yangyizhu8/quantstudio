#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统推第 0 步机检：远程是否发生「非预期推进」。

背景（2026-10-29 立）
--------------------
统推批原用「逐笔 ⊆ 已批池」判据，只防「把未批内容混进本批」，**不防「内容绕过批次被
直接推到远程」**。实证：`76e9e50..fa82445` 七笔在 #8 出仓后由他线自行推至远程，未经
任何统推批；若非清点时恰发现 `origin/main != 上批锚`，该绕过行为不会被任何判据捕获。

判据（写死于本脚本，禁口头约定）
------------------------------
第 0 步：`fetch` → 读上批留痕锚 → 比对 `origin/main` → **不相等即停报**。

判据三形态对齐
--------------
① 输入形态门：`expect` 与 `actual` 必须都是 40 位小写 hex，否则报「无法判定」(exit 3)，
   **绝不把无法判定当作不一致**；
② 单值严格比对：不使用前缀/区间模糊比较（留痕里的 8 位缩写先展开为全 SHA 再比）；
③ 判定失败分报：fetch / rev-parse 异常 → exit 3，与非预期推进 (exit 2) 分离。

退出码
------
  0  一致（远程 = 上批锚，预期推进量 0）→ 可继续本批
  2  非预期推进（远程 != 上批锚）→ **停推上报**（无论推进笔是否在已批池）
  3  无法判定（形态门失败 / 留痕不可读 / fetch 或 rev-parse 异常）→ fail-closed，停推

用法
----
  python scripts/check_remote_advance.py                 # 自动取上批留痕锚
  python scripts/check_remote_advance.py --expect <SHA>  # 显式指定（8-40 位均可）
  python scripts/check_remote_advance.py --json          # 机读输出
  python scripts/check_remote_advance.py --no-fetch      # 跳过 fetch（仅验证本地引用）

配套：`docs/handoff/push-batch-execution-card.md` 第 0 步。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

RULINGS_LOG = "docs/handoff/push-bundle-rulings.log"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LOG_SHA_RE = re.compile(r"commit=([0-9a-f]{7,40})")


def _run(args):
    """执行 git 命令，返回 (rc, stdout, stderr)。异常不外抛，交由调用方判 exit 3。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as exc:  # noqa: BLE001 — 任何执行异常都是「无法判定」
        return -1, "", "%s: %s" % (type(exc).__name__, exc)


def read_last_anchor(path=RULINGS_LOG):
    """从上批留痕读最近一条 commit=<SHA>；读不到返回 (None, 原因)。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return None, "留痕文件不存在：%s" % path
    except Exception as exc:  # noqa: BLE001
        return None, "留痕不可读：%s: %s" % (type(exc).__name__, exc)
    # 只认正式统推批次行（含 ruling=）；跳过追认/勘误等辅助行——它们也带 commit=<SHA>，
    # 若不区分会把「追认=fa82445 ⑤=...」误读为上批锚（2026-10-29 首用实测暴露）。
    for ln in reversed(lines):
        if 'ruling=' not in ln:
            continue
        m = LOG_SHA_RE.search(ln)
        if m:
            return m.group(1), None
    return None, "留痕中未找到含 ruling= 的批次行（commit=<SHA>）：%s" % path


def expand(sha):
    """把 7-40 位缩写展开为 40 位全 SHA；失败返回 (None, 原因)。"""
    rc, out, err = _run(["git", "rev-parse", "--verify", "%s^{commit}" % sha])
    if rc != 0 or not SHA_RE.match(out):
        return None, "无法展开为 40 位 SHA（rc=%s）：%s" % (rc, (err or out)[:200])
    return out, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="统推第 0 步：检出远程非预期推进")
    ap.add_argument("--expect", help="上批锚（8-40 位 SHA）；缺省自动读留痕")
    ap.add_argument("--log", default=RULINGS_LOG, help="留痕文件路径")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 git fetch")
    ap.add_argument("--json", action="store_true", help="机读输出")
    a = ap.parse_args(argv)

    result = {"verdict": None, "exit": 3, "expect": None, "actual": None, "advanced": [], "reason": None}

    # ---- 取 expect ----
    exp_raw = a.expect
    if not exp_raw:
        exp_raw, why = read_last_anchor(a.log)
        if exp_raw is None:
            result["reason"] = why
            return _emit(result, a.json)
    exp_sha, why = expand(exp_raw)
    if exp_sha is None:
        result["reason"] = "expect 展开失败：%s" % why
        return _emit(result, a.json)
    result["expect"] = exp_sha

    # ---- fetch ----
    if not a.no_fetch:
        rc, _, err = _run(["git", "fetch", "origin"])
        if rc != 0:
            result["reason"] = "git fetch 失败（rc=%s）：%s" % (rc, err[:200])
            return _emit(result, a.json)

    # ---- 取 actual ----
    rc, act_raw, err = _run(["git", "rev-parse", "origin/main"])
    if rc != 0:
        result["reason"] = "rev-parse origin/main 失败（rc=%s）：%s" % (rc, err[:200])
        return _emit(result, a.json)

    # ---- 形态门（判据三形态 ①）----
    if not SHA_RE.match(act_raw):
        result["reason"] = "actual 非 40 位小写 hex：%r" % act_raw[:60]
        return _emit(result, a.json)
    result["actual"] = act_raw

    # ---- 严格比对（形态②）----
    if exp_sha == act_raw:
        result.update(verdict="consistent", exit=0, reason="远程 = 上批锚，预期推进量 0")
        return _emit(result, a.json)

    # ---- 非预期推进：逐笔取证 ----
    result.update(verdict="unexpected-advance", exit=2,
                  reason="远程 != 上批锚 ⇒ 停推上报（无论推进笔是否在已批池）")
    rc, log_out, _ = _run(["git", "log", "%s..%s" % (exp_sha, act_raw), "--format=%h|%ad|%s", "--date=format:%m-%d %H:%M"])
    if rc == 0 and log_out:
        result["advanced"] = [ln.split("|", 2) for ln in log_out.splitlines() if ln.strip()]
    return _emit(result, a.json)


def _emit(result, as_json):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[check-remote-advance] verdict=%s exit=%d" % (result["verdict"], result["exit"]))
        if result["expect"]:
            print("  上批锚  : %s" % result["expect"])
        if result["actual"]:
            print("  origin/main: %s" % result["actual"])
        if result["reason"]:
            print("  判定    : %s" % result["reason"])
        if result["advanced"]:
            print("  非预期推进 %d 笔：" % len(result["advanced"]))
            for row in result["advanced"]:
                print("    %s | %s | %s" % (row[0], row[1], row[2][:88] if len(row) > 2 else ""))
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
