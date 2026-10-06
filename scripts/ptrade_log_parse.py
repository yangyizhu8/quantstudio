#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S2 · PTrade 平台回测日志解析器（P1 治理闭环，2026-10-07）。

把 PTrade 回测日志（Log.txt）解析为结构化 JSON，供 S1 对齐差异报告器 / diff-triage
三态仲裁 / DAT-16 估值快照对账消费。

行型契约（2026-10-07 实测，Log.txt 1714 行样例标定）：
  1. 委托-生成订单：  生成订单，订单号:<id>，股票代码：<raw>，数量：买入<N>股
  2. 委托-已提交：    委托已提交，方向=卖出|买入，代码=<raw>，委托数量=[-]<N>，当前数量=…
  3. 盘后汇总：       [盘后] 日期=D，组合净值=F，持仓市值=F，可用资金=F，历史高点=F，当前回撤=Pct%
  4. 持仓明细：       <code>:总持仓=N,可用=N,成本=F,现价=F
  5. 风控检查：       [风控] 净值检查，当前净值=F，历史高点=F，当前回撤=Pct%，风险锁定=是|否

用法：
  python scripts/ptrade_log_parse.py <Log.txt> [-o out.json]
知识库关联：knowledge/probes/README.md §S2、knowledge/contracts/data-caliber.md
"""
import argparse
import json
import re
import sys
from pathlib import Path

RE_ORDER_GEN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} - INFO - 生成订单，订单号:(\w+)，"
    r"股票代码：(\S+?)，数量：买入(\d+)股")
RE_ORDER_SUB = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} - INFO - \[[^\]]*\]\[交易\] 委托已提交，"
    r"方向=(买入|卖出)，代码=(\S+?)，委托数量=(-?\d+)")
RE_EOD = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} - INFO - \[[^\]]*\]\[盘后\] 日期=(\d{4}-\d{2}-\d{2})，"
    r"组合净值=([\d.]+)，持仓市值=([\d.]+)，可用资金=([\d.]+)，历史高点=([\d.]+)，当前回撤=(-?[\d.]+)%")
RE_POS = re.compile(
    r"(\d{6})(?:\.(SS|SZ|XSHE|XSHG|SH|BJ))?[:：]总持仓=(\d+),可用=(\d+),成本=([\d.]+),现价=([\d.]+)")
RE_RISK = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} - DEBUG - \[[^\]]*\]\[风控\] 净值检查，"
    r"当前净值=([\d.]+)，历史高点=([\d.]+)，当前回撤=(-?[\d.]+)%，? ?(?:风险锁定=(是|否))?")

_SUFFIX_NORM = {"SS": ".SH", "SH": ".SH", "XSHG": ".SH",
                "SZ": ".SZ", "XSHE": ".SZ", "BJ": ".BJ"}


def norm_code(raw: str) -> str:
    """平台代码 → 本地规范（512890.XSHG / 512890.SS → 512890.SH）。"""
    m = re.match(r"^(\d{6})(?:\.(.+))?$", raw.strip())
    if not m:
        return raw
    num, suf = m.group(1), (m.group(2) or "")
    return num + _SUFFIX_NORM.get(suf.upper(), (("." + suf) if suf else ""))


def parse(log_path: Path) -> dict:
    orders, eod_map = [], {}
    # 编码探测（2026-10-07 实证：PTrade 导出日志为 UTF-16LE BOM；兼容 utf-8/GBK）
    head = log_path.read_bytes()[:4]
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        enc = "utf-16"
    elif head.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8-sig"
    else:
        enc = "utf-8"
    with open(log_path, "r", encoding=enc, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            m = RE_ORDER_GEN.match(line)
            if m:
                orders.append({"date": m.group(1), "side": "buy", "_src": "gen",
                               "code": norm_code(m.group(3)), "qty": int(m.group(4)),
                               "order_id": m.group(2)})
                continue
            m = RE_ORDER_SUB.match(line)
            if m:
                qty = abs(int(m.group(4)))
                side = "buy" if m.group(2) == "买入" else "sell"
                orders.append({"date": m.group(1), "side": side, "_src": "sub",
                               "code": norm_code(m.group(3)), "qty": qty,
                               "order_id": ""})
                continue
            m = RE_EOD.match(line)
            if m:
                d = eod_map.setdefault(m.group(2), {"date": m.group(2)})
                d.update({"nav": float(m.group(3)), "market_value": float(m.group(4)),
                          "cash": float(m.group(5)), "peak": float(m.group(6)),
                          "drawdown_pct": float(m.group(7)), "positions": []})
                # ⚠️ 持仓明细与盘后汇总同在一行（超长行）——不 continue，
                # 继续在本行内扫全部持仓段（2026-10-07 实证）。
                for pm in RE_POS.finditer(line):
                    if int(pm.group(3)) > 0:
                        d["positions"].append({
                            "code": norm_code(pm.group(1)),
                            "volume": int(pm.group(3)), "enable": int(pm.group(4)),
                            "cost_basis": float(pm.group(5)),
                            "last_price": float(pm.group(6))})
                continue
            m = RE_RISK.match(line)
            if m:
                d = eod_map.setdefault(m.group(1), {"date": m.group(1)})
                d["risk_locked"] = (m.group(5) == "是") if m.group(5) else None
    # 委托语义修正（2026-10-07 四象限案例 1/2 实证）：
    # ①「生成订单」行 = 真实委托（订单号全局唯一，按 order_id 去重；同量拆单是真实多笔）；
    # ②「委托已提交」行 = 确认/汇总行——同键(日期,方向,码)已有生成订单行时**忽略**
    #   （其数量可能是多笔汇总，如 27000×2 → 已提交 54000）；仅当无生成订单源时才作为
    #   独立委托计入（卖出常只有此格式）。
    gen_ids, gen_keys = set(), set()
    final_orders = []
    for o in orders:
        if o["_src"] == "gen":
            if o["order_id"] and o["order_id"] in gen_ids:
                continue
            gen_ids.add(o["order_id"])
            gen_keys.add((o["date"], o["side"], o["code"]))
            final_orders.append(o)
    for o in orders:
        if o["_src"] == "sub":
            if (o["date"], o["side"], o["code"]) in gen_keys:
                continue
            final_orders.append(o)
    final_orders.sort(key=lambda x: (x["date"], x["code"], x["side"]))
    eod = [eod_map[k] for k in sorted(eod_map)]
    dates = [o["date"] for o in final_orders]
    return {
        "meta": {"source": str(log_path), "orders": len(final_orders), "eod_days": len(eod),
                 "date_range": [min(dates or eod and [eod[0]["date"]]),
                                max(dates or [eod[-1]["date"]])] if (dates or eod) else []},
        "orders": [{k: v for k, v in o.items() if k != "_src"} for o in final_orders],
        "eod": eod,
    }


def main():
    ap = argparse.ArgumentParser(description="S2 PTrade 平台日志解析器")
    ap.add_argument("log", help="Log.txt 路径")
    ap.add_argument("-o", "--output", help="输出 JSON 路径（缺省打印摘要）")
    args = ap.parse_args()
    data = parse(Path(args.log))
    if args.output:
        Path(args.output).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"OK: {len(data['orders'])} 委托 / {len(data['eod'])} 盘后日 → {args.output}")
    else:
        print(json.dumps(data["meta"], ensure_ascii=False))
        print("首5委托:", *[f"{o['date']} {o['side']} {o['code']} x{o['qty']}"
                        for o in data["orders"][:5]], sep="\n  ")
        last = data["eod"][-1] if data["eod"] else {}
        print("末日盘后:", json.dumps(last, ensure_ascii=False)[:300])


if __name__ == "__main__":
    sys.exit(main())