#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""推送后「SHA → 归属线」映射件生成器（2026-11-08 总调度采纳）。

背景
----
本仓 **author 字段无法切分归属**（全仓提交 author 均为同一 git 身份 `yangyizhu8`，实测 40/40），
且主分支为**线性历史** ⇒ 一次 `git push` 会推出 `origin/main..HEAD` 的**全部**提交。
故推送后一旦需要追责、回滚或核对「某笔谁批的」，**唯一可用线索是 commit 标题**。
本脚本把该线索固化成一份机器可读＋人可读的映射件，推送后立即生成并单独提交。

用法
----
    # 预览（只打印，不写盘）
    python scripts/push_attribution_map.py

    # 推送后落盘（生成 docs/handoff/push-attribution-map-<YYYYMMDD-HHMM>.md）
    python scripts/push_attribution_map.py --write

    # 显式指定基线（默认 origin/main）
    python scripts/push_attribution_map.py --base origin/main --write

设计纪律
--------
1. **不做静默猜测**：无法按关键词归簇的提交一律落入 `未归类`，并在输出中**醒目计数**；
   未归类数 > 0 时 exit code = 2（提示人工补规则），绝不"看起来像就算了"。
2. **不改写标题**：原样保留 `git log` 的 subject，只做前缀匹配归簇。
3. **不依赖网络**：只读本地 git，不 fetch。
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 归属线规则：（线名，关键词元组）。按顺序匹配，先命中先归属。
#: 关键词大小写不敏感；匹配的是 commit subject。
#: 规则维护纪律：每次出现「未归类」⇒ 先看明细确认归属，再补关键词；**禁止**用宽泛词
#: （如单字 "art"、"方案"）以免跨线误吸。
LINE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("⑦ ptrade 部署线（qs-deploy）", (
        "ptrade-deploy", "import-freshness", "pd-0018", "pd-0019", "pd0018", "pd0019",
        "pd-2026-0018", "pd-2026-0019", "sidecar", "e7-shadow", "e′ shadow",
        # 本线备件/呈批面类（2026-11-08 补：45c0936/d4df067 曾落未归类）
        # ⚠️ 踩坑实例：曾用宽泛词 "第二批"，把他线 2d8b94e（duckdb 线分账清单
        #    「第二批推送分账清单」）误吸进本线（13 笔而实为 12 笔）⇒ 已收窄为
        #    "第二批呈批面" / "第二批备件" 等精确短语。
        "呈批面", "⑦线", "第二批呈批面", "第二批备件",
        "批准状态自证", "推送面实态", "submission-items",
        # 2026-11-09 补（归档全量基线 8099a31 下 2 笔我线提交未归类）：
        "sha→归属线映射件生成器", "归属线映射", "②审后勘误",
    )),
    ("⑳ 采集守护 / 观测线", (
        # 2026-11-09 补：day-N 观测、canary、forever 恢复、P0-D 死信归档一族
        "day-", "canary", "forever", "死信", "p0d",
        "撤销声明",
    )),
    ("跨线分账 / 白名单标准件（他线·候选归属）", (
        # 2026-11-09 补：跨线分账与显式 SHA 白名单标准件（**各线自证，我方不代认**）
        "分账", "sha 白名单", "sha白名单", "白名单法", "预检件", "收官",
        "呈总调度", "合并呈报",
    )),
    ("⑲ duckdb 升级线", (
        "duckdb", "etf_daily", "art 索引", "art索引", "art 损坏", "art损坏",
        "stock_minutes", "p0-d", "分账清单", "push-ledger",
        "upgrade window", "升级窗口", "tsv-backfill", "回填", "wal",
        "②审勘误", "根因机理",
    )),
    ("性能线（R-PERF）", (
        "r-perf", "perf-", "性能", "基线", "baseline", "分钟拉取", "水位",
    )),
    ("QMT / QFQ 线", (
        "qmt", "qfq", "复权", "front_ratio", "adj_factor", "快照",
    )),
    ("采集 / pipeline 线", (
        "pipeline", "采集", "daemon", "gate", "日历", "trade_calendar", "soft-lock",
        # 分钟/观测链（2026-11-08 补：11 笔未归类中 7 笔属此族）
        "断流", "分钟新交易日", "分钟逐日", "方案件", "obs-", "once 轮",
        "缓存回声", "动态下限", "健康基准", "s1)",
    )),
    ("复盘线（review-kb）", (
        "review-kb", "复盘", "daily report",
    )),
    ("策略 / skill 线", (
        "strategy", "策略", "skill", "robustness",
    )),
)

UNCLASSIFIED = "未归类（需人工确认）"


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {(r.stderr or '').strip()}")
    return r.stdout


def classify(subject: str) -> str:
    low = subject.lower()
    for line, kws in LINE_RULES:
        if any(k.lower() in low for k in kws):
            return line
    return UNCLASSIFIED


def collect(base: str) -> list[dict]:
    raw = _git("log", f"{base}..HEAD", "--reverse",
               "--format=%H%x1f%h%x1f%ad%x1f%an%x1f%s", "--date=format:%Y-%m-%d %H:%M")
    out = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\x1f")
        if len(parts) != 5:
            continue
        sha, short, ad, an, subject = parts
        out.append({"sha": sha, "short": short, "date": ad, "author": an,
                    "subject": subject, "line": classify(subject)})
    return out


def render(base: str, rows: list[dict], generated_at: str) -> str:
    total = len(rows)
    by_line: dict[str, list[dict]] = {}
    for r in rows:
        by_line.setdefault(r["line"], []).append(r)
    unclassified = len(by_line.get(UNCLASSIFIED, []))
    remote = _git("rev-parse", base).strip()

    L = [
        "# 推送后 SHA → 归属线映射件",
        "",
        f"- **生成时刻**：{generated_at}",
        f"- **基线**：`{base}` = `{remote}`",
        f"- **本次推送面（`{base}..HEAD`）**：**{total} 笔**",
        f"- **HEAD**：`{_git('rev-parse', 'HEAD').strip()}`",
        f"- **未归类**：**{unclassified} 笔**" + ("（⚠️ 需人工补规则）" if unclassified else "（0，全部已归簇）"),
        "",
        "> 用途：本仓 author 字段无法切分归属（全部为同一 git 身份）且主分支为线性历史 ⇒ 一次 push 推出全部提交。",
        "> 推送后如需追责/回滚/核对批准来源，**唯一线索是 commit 标题**；本件把该线索固化为可机读映射。",
        "> 生成器：`scripts/push_attribution_map.py`（规则见脚本 `LINE_RULES`，未命中一律入「未归类」不静默猜测）。",
        "",
        "## 一、按归属线汇总",
        "",
        "| 归属线 | 笔数 | 范围（最短→最长 SHA） |",
        "|---|---|---|",
    ]
    for line, items in sorted(by_line.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        first, last = items[0], items[-1]
        rng = f"`{first['short']}` → `{last['short']}`" if first is not last else f"`{first['short']}`"
        L.append(f"| {line} | **{len(items)}** | {rng} |")
    L.append(f"| **合计** | **{total}** | — |")
    L += ["", "## 二、逐笔映射（按提交时间序，与 `git log` 一致）", "",
          "| # | SHA | 时间 | 归属线 | 标题 |", "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        subj = r["subject"].replace("|", "\\|")
        if len(subj) > 96:
            subj = subj[:96] + "…"
        L.append(f"| {i} | `{r['short']}` | {r['date']} | {r['line']} | {subj} |")
    L += ["", "## 三、未归类明细（须人工确认后再补规则）", ""]
    if unclassified:
        L.append("| SHA | 时间 | 标题 |")
        L.append("|---|---|---|")
        for r in by_line[UNCLASSIFIED]:
            _subj = r["subject"].replace("|", "\\|")[:96]
            L.append(f"| `{r['short']}` | {r['date']} | {_subj} |")
    else:
        L.append("_无_")
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="推送后生成 SHA → 归属线映射件")
    ap.add_argument("--base", default="origin/main", help="对比基线（默认 origin/main）")
    ap.add_argument("--write", action="store_true", help="落盘到 docs/handoff/")
    ap.add_argument("--out", default=None, help="显式输出路径（覆盖 --write 的默认名）")
    args = ap.parse_args(argv)

    rows = collect(args.base)
    if not rows:
        print(f"[push-map] {args.base}..HEAD 无提交（nothing to map）")
        return 0

    now = datetime.datetime.now()
    text = render(args.base, rows, now.strftime("%Y-%m-%d %H:%M:%S"))
    unclassified = sum(1 for r in rows if r["line"] == UNCLASSIFIED)

    if args.write or args.out:
        out = Path(args.out) if args.out else (
            REPO_ROOT / "docs" / "handoff" / f"push-attribution-map-{now.strftime('%Y%m%d-%H%M')}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"[push-map] 已写入 {out}")
    else:
        print(text)

    print(f"[push-map] 合计 {len(rows)} 笔；未归类 {unclassified} 笔")
    if unclassified:
        print("[push-map] ⚠️ 存在未归类提交 ⇒ 请补 scripts/push_attribution_map.py 的 LINE_RULES 后重跑",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
