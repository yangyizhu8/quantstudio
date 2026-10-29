#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""duckdb 版本线 EOL 到期复核守护（T056 · 仓库内守护条目）。

只读判定 + 生成复核提醒：**不改任何既有可观察结果**（不触碰数据库、不改配置、不写业务文件），
判型 = 新增检测型（铁律 18）。

判据唯一口径 = spec 002-duckdb-1.5-upgrade FR-010（90/60/30 升级判据）：
  距 EOL > 90 天 → normal
  90 天 ≥ days > 60 → plan_due        （距 EOL 90 天：出方案）
  60 天 ≥ days > 30 → implement_due   （距 EOL 60 天：进实施）
  30 天 ≥ days ≥ 0  → close_or_accept （距 EOL 30 天：完成或书面接受风险）
  days < 0         → expired          （已过期：必须复核，且不得继续放行）

用法：
  python scripts/check_duckdb_eol_guard.py --check
  python scripts/check_duckdb_eol_guard.py --check --as-of 2026-10-18 --json
  python scripts/check_duckdb_eol_guard.py --check --patch-note "duckdb#23645 closed" \\
      --emit docs/evidence/eol-review-reminder.md

退出码：0 = 无到期复核提醒；1 = 有到期复核提醒（需人处理）；2 = 错误。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CALENDAR = os.path.join(REPO_ROOT, "docs", "duckdb-version-eol-calendar.json")

STATE_ACTION = {
    "normal": "无（距 EOL > 90 天）",
    "plan_due": "距 EOL 90 天：出方案",
    "implement_due": "距 EOL 60 天：进实施",
    "close_or_accept": "距 EOL 30 天：完成或书面接受风险",
    "expired": "已过 EOL：必须复核，且该版本线不得继续放行",
}


def _fail(msg: str) -> int:
    print("ERROR %s" % msg, file=sys.stderr)
    return 2


def load_calendar(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def classify(days: int, ms: Dict[str, int]) -> str:
    if days < 0:
        return "expired"
    if days <= int(ms["close_or_accept_days"]):
        return "close_or_accept"
    if days <= int(ms["implement_due_days"]):
        return "implement_due"
    if days <= int(ms["plan_due_days"]):
        return "plan_due"
    return "normal"


def evaluate(cal: Dict[str, Any], as_of: dt.date, patch_note: Optional[str]) -> Dict[str, Any]:
    ms = cal.get("milestones") or {}
    for key in ("plan_due_days", "implement_due_days", "close_or_accept_days"):
        if key not in ms:
            raise KeyError("milestones.%s 缺失" % key)
    lines: List[Dict[str, Any]] = []
    due_count = 0
    for ln in cal.get("lines", []):
        eol = dt.date.fromisoformat(str(ln["eol"]))
        days = (eol - as_of).days
        state = classify(days, ms)
        due = state != "normal"
        if due:
            due_count += 1
        lines.append({
            "line": ln.get("line"),
            "eol": ln.get("eol"),
            "eol_granularity": ln.get("eol_granularity", "day"),
            "days_to_eol": days,
            "state": state,
            "action": STATE_ACTION[state],
            "allowed": ln.get("allowed"),
            "in_allow_set": ln.get("in_allow_set", True),
            "due": due,
            "notes": ln.get("notes", ""),
        })
    triggers: List[str] = []
    if patch_note:
        triggers.append("补丁/缺陷单事件：%s" % patch_note)
    if due_count:
        triggers.append("里程碑到点：%d 条版本线处于非 normal 状态" % due_count)
    return {
        "as_of": as_of.isoformat(),
        "policy_ref": cal.get("policy_ref"),
        "allow_set": cal.get("allow_set"),
        "lines": lines,
        "triggers": triggers,
        "due": bool(triggers),
        "exit_code": 1 if triggers else 0,
    }


def render_text(res: Dict[str, Any]) -> str:
    out = ["duckdb EOL 到期复核守护（判据：90/60/30 · %s）" % (res.get("policy_ref") or "-"),
           "as-of: %s   放行集合: %s" % (res["as_of"], res.get("allow_set") or "-"),
           ""]
    for ln in res["lines"]:
        out.append("  [%s] %s 线  EOL %s（%s）  距 EOL %+d 天  状态=%s  %s%s" % (
            "DUE" if ln["due"] else "  ",
            ln["line"], ln["eol"], ln["eol_granularity"], ln["days_to_eol"],
            ln["state"], ln["action"], "" if ln["in_allow_set"] else "  [不在放行集合]"))
    if res["triggers"]:
        out.append("")
        out.append("需人工处理：")
        for t in res["triggers"]:
            out.append("  - %s" % t)
    else:
        out.append("")
        out.append("无到期复核提醒（无需处理）")
    return "\n".join(out)


def render_reminder(res: Dict[str, Any]) -> str:
    lines = ["---",
             "id: EOL-REVIEW-%s" % res["as_of"].replace("-", ""),
             "type: eol_review_reminder",
             "status: open",
             "as_of: \"%s\"" % res["as_of"],
             "policy_ref: \"%s\"" % (res.get("policy_ref") or ""),
             "---",
             "",
             "## duckdb 版本线到期复核提醒（%s）" % res["as_of"],
             ""]
    for t in res["triggers"]:
        lines.append("- 触发：**%s**" % t)
    lines.append("")
    lines.append("| 版本线 | EOL | 距 EOL（天） | 状态 | 应做动作 | 放行区间 |")
    lines.append("|---|---|---|---|---|---|")
    for ln in res["lines"]:
        lines.append("| %s | %s | %+d | %s | %s | `%s` |" % (
            ln["line"], ln["eol"], ln["days_to_eol"], ln["state"], ln["action"], ln["allowed"]))
    lines.append("")
    lines.append("> 处置：按 90/60/30 升级判据推进（距 EOL 90 天出方案 / 60 天进实施 / 30 天完成或书面接受风险）；"
                 "**未完成复核的版本线不得继续放行**。承载方：总调度 EOL 日历守护 + 仓库内守护条目"
                 "（`scripts/check_duckdb_eol_guard.py`）。")
    return "\n".join(lines) + "\n"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="duckdb 版本线 EOL 到期复核守护（只读判定）")
    ap.add_argument("--check", action="store_true", help="执行判定（默认动作）")
    ap.add_argument("--as-of", default=None, help="以指定日期（YYYY-MM-DD）判定；缺省取今天")
    ap.add_argument("--patch-note", default=None, help="叠加触发：新补丁发布 / 缺陷单关闭")
    ap.add_argument("--emit", default=None, help="把复核提醒写到该文件")
    ap.add_argument("--calendar", default=DEFAULT_CALENDAR, help="日历文件（机读源）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        cal = load_calendar(args.calendar)
    except FileNotFoundError:
        return _fail("日历文件不存在: %s" % args.calendar)
    except Exception as exc:  # noqa: BLE001
        return _fail("日历文件解析失败: %s: %s" % (type(exc).__name__, exc))

    if args.as_of:
        try:
            as_of = dt.date.fromisoformat(args.as_of)
        except ValueError:
            return _fail("as-of 日期格式非法（须 YYYY-MM-DD）: %s" % args.as_of)
    else:
        as_of = dt.date.today()

    try:
        res = evaluate(cal, as_of, args.patch_note)
    except KeyError as exc:
        return _fail("日历字段缺失: %s" % exc)
    except Exception as exc:  # noqa: BLE001
        return _fail("判定失败: %s: %s" % (type(exc).__name__, exc))

    if args.emit and res["due"]:
        try:
            outp = args.emit if os.path.isabs(args.emit) else os.path.join(REPO_ROOT, args.emit)
            os.makedirs(os.path.dirname(outp), exist_ok=True)
            with open(outp, "w", encoding="utf-8", newline="\n") as f:
                f.write(render_reminder(res))
        except Exception as exc:  # noqa: BLE001
            return _fail("提醒写入失败: %s: %s" % (type(exc).__name__, exc))

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render_text(res))
        if args.emit and res["due"]:
            print("提醒已写入: %s" % args.emit)
    return int(res["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
