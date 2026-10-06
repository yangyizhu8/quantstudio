
# -*- coding: utf-8 -*-
"""Fetch the XunTou knowledge-base "built-in Python" (innerApi) pages and convert to Markdown.

Source of truth = the share link's **client-rendered DOM** (what a reader actually sees):
    https://dict.thinktrader.net/innerApi/<page>.html?id=70GYeq
Rendering (Playwright/Chromium) is required because the site exposes, only client-side:
  * code-block language labels  -> used as the fenced-code language
  * tab labels (vp-tabs)        -> preserved as explicit 选项卡 group labels
Deterministic conversion (markdownify) - no LLM rewriting, no summarising.

Usage:
  python fetch_inner_api.py            # report only (compare against existing files)
  python fetch_inner_api.py --apply    # write/update the page files in this directory
"""
import os, re, sys, json, hashlib
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from markdownify import markdownify as mdfy

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(DOC, "..", "..", ".."))
APPLY = "--apply" in sys.argv
BASE = "https://dict.thinktrader.net/innerApi/"
SHARE = "?id=70GYeq"
BT3 = chr(96) * 3

MAP = [
    ("start_now", "01-快速开始.md", "快速开始"), ("user_attention", "02-使用须知.md", "使用须知"),
    ("variable_convention", "03-变量约定.md", "变量约定"), ("data_structure", "04-数据结构.md", "数据结构"),
    ("enum_constants", "05-枚举常量.md", "枚举常量"), ("system_function", "06-系统函数.md", "系统函数"),
    ("data_function", "07-行情函数.md", "行情函数"), ("trading_function", "08-交易函数.md", "交易函数"),
    ("quote_function", "09-引用函数.md", "引用函数"), ("drawing_function", "10-绘图函数.md", "绘图函数"),
    ("callback_function", "11-成交回报实时主推函数.md", "成交回报实时主推函数"),
    ("code_examples", "12-完整示例.md", "完整示例"), ("related_instructions", "13-相关说明.md", "相关说明"),
    ("question_answer", "14-常见问题.md", "常见问题"), ("interface_operation", "15-界面操作.md", "界面操作"),
]

DROP_SELECTORS = ["script", "style", "nav", "footer", "div.page-nav", "div.page-edit",
                  "button.copy-code-button", "div.line-numbers", "button", "a.header-anchor"]

def _lang_cb(el):
    """markdownify: derive the fenced-code language from the highlighted code element."""
    node = el
    while node is not None and getattr(node, "name", None) not in (None, "[document]"):
        dl = node.get("data-lang") if hasattr(node, "get") else None
        if dl:
            return dl
        cls = node.get("class") if hasattr(node, "get") else None
        if cls:
            for c in cls:
                if isinstance(c, str) and c.startswith("language-"):
                    return c[len("language-"):]
        node = node.parent
    return ""

def _expand_tabs(node):
    """Replace vp-tabs components with explicit, faithful Markdown-able groups."""
    changed = True
    while changed:
        changed = False
        for tabs in node.select("div.vp-tabs"):
            if tabs.select("div.vp-tabs"):      # handle innermost first
                continue
            nav = tabs.select_one("div.vp-tabs-nav")
            labels = [b.get_text(" ", strip=True) for b in nav.select("button")] if nav else []
            panels = tabs.select("div.vp-tab")
            frag = ""
            if labels:
                frag += "<p><strong>选项卡：" + " ｜ ".join(labels) + "</strong></p>\n"
            for i, p in enumerate(panels):
                lab = labels[i] if i < len(labels) else ("选项%d" % (i + 1))
                frag += "<p><strong>[%s]</strong></p>\n%s\n" % (lab, p.decode_contents())
            tabs.replace_with(BeautifulSoup(frag, "lxml"))
            changed = True

def _tag_code_langs(node):
    for d in node.select("div[class*=language-]"):
        cls = d.get("class") or []
        lang = next((c[len("language-"):] for c in cls if isinstance(c, str) and c.startswith("language-")), None)
        if not lang:
            continue
        for code in d.select("code"):
            code["data-lang"] = lang

def to_md(html):
    soup = BeautifulSoup(html, "lxml")
    _expand_tabs(soup)          # consume tab nav buttons BEFORE chrome removal
    for sel in DROP_SELECTORS:
        for e in soup.select(sel):
            e.decompose()
    _tag_code_langs(soup)
    for img in soup.select("img"):
        s = img.get("src", "")
        if s.startswith("/"):
            img["src"] = "https://dict.thinktrader.net" + s
    t = mdfy(str(soup), heading_style="ATX", bullets="-", strip=["a"], code_language_callback=_lang_cb)
    t = t.replace("\u200b", "")
    t = re.sub(r"\n{4,}", "\n\n\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return _unescape(t.strip() + "\n")

def _unescape(text):
    out, in_code = [], False
    for line in text.split("\n"):
        if line.startswith(BT3):
            in_code = not in_code; out.append(line); continue
        if not in_code:
            line = (line.replace("\\_", "_").replace("\\*", "*").replace("\\[", "[").replace("\\]", "]")
                        .replace("\\<", "<").replace("\\>", ">").replace("\\#", "#"))
        out.append(line)
    return "\n".join(out)

def strip_header(md):
    lines = md.split("\n"); i = 0
    while i < len(lines) and (lines[i].startswith("> **来源**") or lines[i].startswith("> **抓取日期**")
                              or lines[i].strip() in ("", "---")):
        i += 1
    return "\n".join(lines[i:])

def header(zh, slug):
    return ("> **来源**：迅投知识库 · 内置 Python · " + zh + " — " + BASE + slug + ".html" + SHARE + "\n"
            "> **抓取日期**：2026-10-06　|　**转换方式**：站点**客户端渲染 DOM** → Markdown（确定性转换，未做改写或摘要）\n\n---\n\n")

def render(pg, url):
    """Render one page; retry on navigation time-outs (the site keeps a chat widget loading)."""
    last = None
    for _ in range(3):
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_selector("div.theme-default-content", timeout=30000)
            pg.wait_for_timeout(2500)
            html = pg.evaluate("""() => { const n = document.querySelector('div.theme-default-content') || document.querySelector('main');
                return n ? n.innerHTML : null; }""")
            if html:
                return html
        except Exception as e:
            last = e
            pg.wait_for_timeout(2000)
    raise RuntimeError("render failed: %s" % last)

report = {"mode": "apply" if APPLY else "report", "pages": []}
fetched = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    for slug, fname, zh in MAP:
        try:
            html = render(pg, BASE + slug + ".html" + SHARE)
        except Exception as e:
            report["pages"].append({"file": fname, "error": str(e)[:200]}); continue
        live = to_md(html)
        fetched.append((fname, zh, live))
        path = os.path.join(DOC, fname)
        cur = strip_header(open(path, encoding="utf-8").read()) if os.path.exists(path) else ""
        a, bl = set(cur.split("\n")), set(live.split("\n"))
        only_cur = [x for x in cur.split("\n") if x.strip() and x not in bl]
        only_live = [x for x in live.split("\n") if x.strip() and x not in a]
        rec = {"file": fname, "slug": slug,
               "fences": live.count(BT3), "tab_groups": live.count("选项卡："),
               "lines_added": len(only_live), "lines_dropped": len(only_cur),
               "dropped_sample": [x[:110] for x in only_cur[:6]],
               "added_sample": [x[:110] for x in only_live[:6]],
               "sha16": hashlib.sha256(live.encode("utf-8")).hexdigest()[:16]}
        report["pages"].append(rec)
    b.close()

# two-phase: write only after every page was fetched successfully
if APPLY:
    if any("error" in r for r in report["pages"]) or len(fetched) != len(MAP):
        print("ABORT: incomplete fetch, nothing written")
        sys.exit(2)
    for fname, zh, live in fetched:
        slug = next(s for s, f, _ in MAP if f == fname)
        open(os.path.join(DOC, fname), "w", encoding="utf-8").write(header(zh, slug) + live)
print(json.dumps({k: v for k, v in report.items() if k != "pages"}, ensure_ascii=False))
for r in report["pages"]:
    if "error" in r: print(r["file"], "ERROR"); continue
    print(r["file"], "| fences:", r["fences"], "| tabgroups:", r["tab_groups"],
          "| +lines:", r["lines_added"], "| -lines:", r["lines_dropped"])
with open(os.path.join(DOC, "tools", "_last_fetch_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
