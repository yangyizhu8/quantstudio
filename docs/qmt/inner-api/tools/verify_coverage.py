# -*- coding: utf-8 -*-
"""Completeness check: every visible line of every innerApi page must occur in the local markdown.

Method: render each page (share link) -> take innerText of div.theme-default-content -> normalise
BOTH sides with ONE common normaliser (drop markdown syntax + whitespace, keep punctuation) ->
require every page line (len>=6) to be a substring of the normalised markdown.

Rendered-only chrome (copy button, '在新窗口打开', tab labels) and CSS-rendered list markers are
normalised away on both sides, so a failure means REAL missing content.
Writes docs/evidence/qmt-inner-api-text-coverage-20261006.json
"""

# -*- coding: utf-8 -*-
"""Final completeness classification (v6): drop our own synthetic markers, then check coverage."""
import re, os, json
from playwright.sync_api import sync_playwright

DOC = r"D:\miniQMT策略实盘\QuantStudio\docs\qmt\inner-api"
EVID = r"D:\miniQMT策略实盘\QuantStudio\docs\evidence"
BASE = "https://dict.thinktrader.net/innerApi/"
SHARE = "?id=70GYeq"
BT = chr(96)
STRIP = re.compile("[#*|\uff5c>" + BT + "\\s\u3000\u200b]+")   # incl. full-width ｜ used as our tab separator
CHROME = ["在新窗口打开", "复制代码", "已复制", "选项卡："]
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

MAP = [("start_now","01-快速开始.md"),("user_attention","02-使用须知.md"),("variable_convention","03-变量约定.md"),
       ("data_structure","04-数据结构.md"),("enum_constants","05-枚举常量.md"),("system_function","06-系统函数.md"),
       ("data_function","07-行情函数.md"),("trading_function","08-交易函数.md"),("quote_function","09-引用函数.md"),
       ("drawing_function","10-绘图函数.md"),("callback_function","11-成交回报实时主推函数.md"),
       ("code_examples","12-完整示例.md"),("related_instructions","13-相关说明.md"),
       ("question_answer","14-常见问题.md"),("interface_operation","15-界面操作.md")]

def N_md(t):
    t = re.sub(r"(?m)^\*\*\[[^\]]+\]\*\*\s*$\n?", "", t)   # our own synthetic tab marker line
    t = re.sub(r"(?m)^\s*[-*+]\s+", "", t)
    t = re.sub(r"(?m)^\s*\d+\.\s+", "", t)
    t = re.sub(r"(?m)^\s*>\s?", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = LINK.sub(r"\1", t)
    t = re.sub(r"(?m)^\|[\s\-:|]+\|$", "", t)
    for c in CHROME: t = t.replace(c, "")
    return STRIP.sub("", t)

def N_line(l):
    l = LINK.sub(r"\1", l)
    for c in CHROME: l = l.replace(c, "")
    l = re.sub(r"^\s*(?:\d+\.|[-*+])\s*", "", l)   # list markers are CSS-rendered
    return STRIP.sub("", l)

def render(pg, url):
    for _ in range(4):
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_selector("div.theme-default-content", timeout=40000)
            pg.wait_for_timeout(3000)
            r = pg.evaluate("""() => { const n = document.querySelector('div.theme-default-content');
                return n ? {text: n.innerText} : null; }""")
            if r: return r
        except Exception:
            pg.wait_for_timeout(3000)
    return None

rows, gaps, tot = [], [], 0
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    for slug, fname in MAP:
        page = render(pg, BASE + slug + ".html" + SHARE)
        if page is None: print(fname, "RENDER FAIL"); continue
        M = N_md(open(os.path.join(DOC, fname), encoding="utf-8").read())
        lines = [N_line(l) for l in page["text"].split("\n")]
        lines = [l for l in lines if len(l) >= 6]
        tot += len(lines)
        miss = [l for l in lines if l not in M]
        rows.append({"file": fname, "lines": len(lines), "miss": len(miss),
                     "coverage_pct": round(100.0*(len(lines)-len(miss))/len(lines), 3), "miss_samples": miss[:6]})
        gaps += [{"file": fname, "line": l} for l in miss]
        print(fname, len(lines), "lines |", len(miss), "unmatched |", rows[-1]["coverage_pct"], "%")
    b.close()
summary = {"pages": len(rows), "page_lines": tot, "unmatched": len(gaps),
           "line_coverage_pct": round(100.0*(tot-len(gaps))/tot, 3),
           "min_page_coverage_pct": min(r["coverage_pct"] for r in rows),
           "pages_100": [r["file"] for r in rows if r["miss"] == 0]}
print(json.dumps({k: v for k, v in summary.items() if k != "pages_100"}, ensure_ascii=False))
print("unmatched lines:")
for g in gaps[:25]:
    print("   -", g["file"], "|", g["line"][:130])
json.dump({"summary": summary, "pages": rows, "unmatched": gaps},
          open(os.path.join(EVID, "qmt-inner-api-text-coverage-20261006.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)