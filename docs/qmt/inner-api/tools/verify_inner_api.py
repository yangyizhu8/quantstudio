
# -*- coding: utf-8 -*-
"""Verify the transcription set against the LIVE RENDERED pages (share link, site-authoritative).

Checks per page:
  1. headings  : rendered DOM h2/h3/h4 count  == markdown heading count
  2. tables    : rendered DOM <table> count   == markdown table count
  3. code      : rendered DOM NON-EMPTY <pre> count == markdown fenced-block count
                 (the site itself ships 2 empty placeholder code blocks on 12-完整示例;
                  empty blocks carry no content and are excluded from the comparison)
  4. tabs      : rendered DOM vp-tabs groups   == markdown 选项卡 group lines, and every
                 tab label string occurs in the markdown
  5. integrity : code fences balanced, no empty page
Writes docs/evidence/qmt-inner-api-rendered-verify.json
"""
import os, re, json, datetime
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EVID = os.path.join(REPO, "docs", "evidence")
os.makedirs(EVID, exist_ok=True)
BASE = "https://dict.thinktrader.net/innerApi/"
SHARE = "?id=70GYeq"
BT3 = chr(96) * 3

MAP = [
    ("start_now", "01-快速开始.md"), ("user_attention", "02-使用须知.md"),
    ("variable_convention", "03-变量约定.md"), ("data_structure", "04-数据结构.md"),
    ("enum_constants", "05-枚举常量.md"), ("system_function", "06-系统函数.md"),
    ("data_function", "07-行情函数.md"), ("trading_function", "08-交易函数.md"),
    ("quote_function", "09-引用函数.md"), ("drawing_function", "10-绘图函数.md"),
    ("callback_function", "11-成交回报实时主推函数.md"), ("code_examples", "12-完整示例.md"),
    ("related_instructions", "13-相关说明.md"), ("question_answer", "14-常见问题.md"),
    ("interface_operation", "15-界面操作.md"),
]

DOM_JS = """() => {
  const n = document.querySelector('div.theme-default-content') || document.querySelector('main');
  if (!n) return null;
  const labels = [];
  n.querySelectorAll('div.vp-tabs').forEach(t => {
    const nav = t.querySelector('div.vp-tabs-nav');
    if (nav) nav.querySelectorAll('button').forEach(b => labels.push(b.innerText.trim()));
  });
  const presAll = Array.from(n.querySelectorAll('pre'));
  const presFilled = presAll.filter(p => (p.innerText || '').trim());
  return {h234: n.querySelectorAll('h2,h3,h4').length,
          tables: n.querySelectorAll('table').length,
          pres: presFilled.length,
          pres_empty: presAll.length - presFilled.length,
          tabgroups: n.querySelectorAll('div.vp-tabs').length,
          preTexts: Array.from(n.querySelectorAll('pre')).map(p => p.innerText.replace(/\\s+/g,' ').trim().slice(0,60)),
          labels: labels};
}"""

def md_metrics(path):
    t = open(path, encoding="utf-8").read()
    return {"h234": len(re.findall(r"(?m)^#{2,4} ", t)),
            "tables": len(re.findall(r"(?m)^\| --- \|", t)),
            "fences": len(re.findall(r"(?m)^" + BT3, t)) // 2,
            "tabgroups": len(re.findall(r"^\*\*选项卡：", t, re.M)),
            "chars": len(t), "text": t}

report = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
          "source": BASE + "start_now.html" + SHARE, "basis": "client-rendered DOM", "pages": []}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    for slug, fname in MAP:
        url = BASE + slug + ".html" + SHARE
        dom = None
        for _ in range(3):
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_selector("div.theme-default-content", timeout=30000)
                pg.wait_for_timeout(2500)
                dom = pg.evaluate(DOM_JS)
                if dom: break
            except Exception:
                pg.wait_for_timeout(2000)
        m = md_metrics(os.path.join(HERE, fname))
        txt = m.pop("text")
        if dom is None:
            report["pages"].append({"file": fname, "error": "render failed"}); continue
        missing_labels = [l for l in set(dom["labels"]) if l and l not in txt]
        fences = len(re.findall(r"(?m)^" + BT3, txt))
        rec = {"file": fname, "slug": slug,
               "src": {"h234": dom["h234"], "tables": dom["tables"], "pres": dom["pres"],
                       "pres_empty": dom.get("pres_empty", 0),
                       "tabgroups": dom["tabgroups"], "labels": len(set(dom["labels"]))},
               "md": {"h234": m["h234"], "tables": m["tables"], "fences": m["fences"],
                      "tabgroups": m["tabgroups"], "chars": m["chars"]},
               "ok_headings": dom["h234"] == m["h234"],
               "ok_tables": dom["tables"] == m["tables"],
               "ok_code": dom["pres"] == m["fences"],
               "ok_tabs": dom["tabgroups"] == m["tabgroups"] and not missing_labels,
               "labels_missing": missing_labels[:5],
               "fence_balanced": fences % 2 == 0,
               "empty": m["chars"] < 500}
        report["pages"].append(rec)
    b.close()

ok = [r for r in report["pages"] if "error" not in r]
report["summary"] = {
    "pages": len(MAP),
    "headings_ok": sum(1 for r in ok if r["ok_headings"]),
    "tables_ok": sum(1 for r in ok if r["ok_tables"]),
    "code_ok": sum(1 for r in ok if r["ok_code"]),
    "tabs_ok": sum(1 for r in ok if r["ok_tabs"]),
    "all_fences_balanced": all(r["fence_balanced"] for r in ok),
    "empty_files": [r["file"] for r in ok if r["empty"]],
    "src_pres_total": sum(r["src"]["pres"] for r in ok),
    "src_pres_empty_total": sum(r["src"].get("pres_empty", 0) for r in ok),
    "md_fences_total": sum(r["md"]["fences"] for r in ok),
    "src_tabgroups_total": sum(r["src"]["tabgroups"] for r in ok),
    "md_tabgroups_total": sum(r["md"]["tabgroups"] for r in ok),
    "src_headings_total": sum(r["src"]["h234"] for r in ok),
    "src_tables_total": sum(r["src"]["tables"] for r in ok),
    "errors": [r["file"] for r in report["pages"] if "error" in r],
}
with open(os.path.join(EVID, "qmt-inner-api-rendered-verify.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print(json.dumps(report["summary"], ensure_ascii=False, indent=1))
for r in report["pages"]:
    if "error" in r:
        print(r["file"], "ERROR"); continue
    print(r["file"], "| H", r["src"]["h234"], "/", r["md"]["h234"], "| T", r["src"]["tables"], "/", r["md"]["tables"],
          "| pre", r["src"]["pres"], "/", r["md"]["fences"], "| tabs", r["src"]["tabgroups"], "/", r["md"]["tabgroups"],
          "| ok:", r["ok_headings"], r["ok_tables"], r["ok_code"], r["ok_tabs"], "| miss:", r["labels_missing"])
