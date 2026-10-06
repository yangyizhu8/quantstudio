
# -*- coding: utf-8 -*-
"""Extract the API/structure/constant quick-index from the transcription set.

Reads the 15 faithfully-transcribed pages in this directory and writes 00-速查索引.md.
Deterministic: purely heading/structure based, no LLM rewriting.
"""
import os, re, json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # docs/qmt/inner-api
OUT = os.path.join(HERE, "00-速查索引.md")
BT = chr(96)

def heads(path, levels):
    t = open(os.path.join(HERE, path), encoding="utf-8").read()
    out = []
    for m in re.finditer(r"(?m)^(#{2,4}) (.+?)\s*$", t):
        lvl, txt = len(m.group(1)), m.group(2).strip()
        if lvl in levels:
            out.append((lvl, txt))
    return out

SEP = re.compile(r"^(.+?)\s*[-–—:：]\s*(.*)$")

def split_name(txt):
    m = SEP.match(txt)
    if not m:
        return txt, ""
    name, desc = m.group(1).strip(), m.group(2).strip()
    if re.search(r"[\u4e00-\u9fff]", name) and "_" not in name and "." not in name:
        return txt, ""
    return name, desc

def table(rows):
    head = "| 名称 | 说明 | 所属文档 |\n| --- | --- | --- |\n"
    return head + "".join("| " + BT + n + BT + " | " + (d or "—") + " | " + doc + " |\n" for n, d, doc in rows)

struct = []
for lvl, txt in heads("04-数据结构.md", (3,)):
    n, d = split_name(txt)
    struct.append((n, d, "[04-数据结构](04-数据结构.md)"))

enums = []
for lvl, txt in heads("05-枚举常量.md", (2, 3)):
    n, d = split_name(txt)
    if lvl == 3 and not n.startswith("enum_"):
        continue
    enums.append((n, d, "[05-枚举常量](05-枚举常量.md)"))

groups = [("06-系统函数.md", "系统函数"), ("07-行情函数.md", "行情函数"),
          ("08-交易函数.md", "交易函数"), ("09-引用函数.md", "引用函数"),
          ("10-绘图函数.md", "绘图函数"), ("11-成交回报实时主推函数.md", "成交回报实时主推函数")]
NOISE = ("调试运行模式", "回测模式", "模拟信号模式", "实盘交易模式", "用法1", "用法2",
         "资产负债表", "利润表", "现金流量表", "股本表", "主要指标", "十大股东", "股东数",
         "财务数据字段表", "交易所代码", "交易标的代码", "symbol示例", "单股交易", "组合交易",
         "单股下单时", "组合下单时", "获取")
funcs, seen = [], set()
for path, zh in groups:
    label = "[" + path.replace(".md", "") + "](" + path + ")"
    for lvl, txt in heads(path, (2, 3)):
        n, d = split_name(txt)
        if not n or n in NOISE or any(n.startswith(x) for x in NOISE):
            continue
        key = (n, zh)
        if key in seen:
            continue
        seen.add(key)
        funcs.append((n, d, label))

lines = []
lines.append("# 大 QMT 内置 Python — 数据结构 / 枚举常量 / API 速查索引\n")
lines.append("> **覆盖范围**：迅投知识库「内置 Python」（`/innerApi/`）整节 15 个页面，与站点路由清单逐页核对一致。")
lines.append("> **生成方式**：由同目录 15 份忠实转写文档按标题结构自动抽取（`tools/build_index.py`），非人工改写。")
lines.append("> **逐页正文**：见 `01-` ~ `15-` 各文件；原始来源与抓取日期见各文件页首。\n")
lines.append("\n## 一、数据结构（对象 / 结构体）— 共 %d 项\n" % len(struct))
lines.append(table(struct))
lines.append("\n## 二、枚举常量 — 共 %d 项\n" % len(enums))
lines.append(table(enums))
lines.append("\n## 三、API 函数 — 共 %d 项\n" % len(funcs))
lines.append(table(funcs))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(json.dumps({"struct": len(struct), "enum": len(enums), "func": len(funcs), "out": OUT}, ensure_ascii=False))
