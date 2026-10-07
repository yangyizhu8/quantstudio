#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S1 · 双端对齐差异报告生成器（P1 治理闭环，2026-10-07）。

输入：本地回测产物目录（run_ptrade_strategy 输出：daily_stats.csv / ptrade_metrics.json /
trades.csv）+ S2 平台日志 JSON（scripts/ptrade_log_parse.py 产物）。

产出（Markdown 报告 + JSON）：
  1. 终值指标对比（本地 metrics vs 平台 nav 序列推得）
  2. 委托序列逐笔对齐 diff（同日同码同向合并拆单后比对；首分歧定位）
  3. 逐日净值对账（首个非零偏差日定位 + 偏差序列摘要）
  4. 现金流对账（共同交易日 cash 逐位）
  5. 市值差分解（平台 positions 现价快照 × 本地隐含市值反推）
  6. 三态初步分类（已对齐 / 归因候选 / 不可归因→立新案）——供 diff-triage 仲裁

用法：
  python scripts/align_diff_report.py --local <产物目录> --platform <S2.json> [-o report.md]

知识库关联：knowledge/probes/README.md §S1、docs/alignment-governance-final-plan.md §S1-S6。
"""
import argparse
import csv
import json
from pathlib import Path

TOL_NAV = 0.005          # 逐日净值容差（元，半分级）
TOL_QTY = 0              # 委托数量容差（股，严格）


def load_local(d: Path) -> dict:
    daily = []
    with open(d / "daily_stats.csv", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            daily.append({"date": row["date"],
                          "total_asset": float(row["total_asset"]),
                          "cash": float(row["cash"])})
    metrics = json.loads((d / "ptrade_metrics.json").read_text(encoding="utf-8"))
    trades = []
    tp = d / "trades.csv"
    if tp.exists():
        with open(tp, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                trades.append(row)
    return {"dir": str(d), "daily": daily, "metrics": metrics, "trades": trades}


def norm_code(c: str) -> str:
    c = (c or "").strip()
    if "." in c:
        num, suf = c.split(".", 1)
        suf = {"SS": "SH", "XSHG": "SH", "SH": "SH", "SZ": "SZ", "XSHE": "SZ",
               "BJ": "BJ"}.get(suf.upper(), suf.upper())
        return num + suf
    return c


def bucket_orders(orders):
    """同日同码同向合并（平台拆单等价性）。"""
    b = {}
    for o in orders:
        key = (o["date"], norm_code(o["code"])[:6], o["side"])
        b[key] = b.get(key, 0) + int(o["qty"])
    return b


def main():
    ap = argparse.ArgumentParser(description="S1 双端对齐差异报告生成器")
    ap.add_argument("--local", required=True, help="本地产物目录")
    ap.add_argument("--platform", required=True, help="S2 平台 JSON")
    ap.add_argument("-o", "--output", help="Markdown 报告输出路径")
    ap.add_argument("--json", dest="as_json", help="结构化结果 JSON 输出路径")
    args = ap.parse_args()

    loc = load_local(Path(args.local))
    pf = json.loads(Path(args.platform).read_text(encoding="utf-8"))
    pf_daily = {e["date"]: e for e in pf["eod"]}
    loc_daily = {r["date"]: r for r in loc["daily"]}
    common = sorted(set(pf_daily) & set(loc_daily))

    rep, J = [], {}
    rep.append("# 双端对齐差异报告（S1 自动生成）")
    rep.append(f"\n- 本地：`{loc['dir']}`\n- 平台：`{pf['meta']['source']}` "
               f"（{pf['meta']['orders']} 委托 / {pf['meta']['eod_days']} 盘后日）"
               f"\n- 共同交易日：{len(common)}（{common[0]} ~ {common[-1]}）")

    # ---- 1. 逐日净值对账：首偏日定位 ----
    diffs = [(d, loc_daily[d]["total_asset"] - pf_daily[d]["nav"]) for d in common]
    nz = [(d, x) for d, x in diffs if abs(x) > TOL_NAV]
    first_div = nz[0] if nz else None
    rep.append("\n## 1. 逐日净值对账（首偏日定位）")
    if first_div:
        d0, x0 = first_div
        rep.append(f"- **首个非零偏差日：{d0}，本地−平台 = {x0:+.2f} 元**")
        prev = [d for d in common if d < d0]
        if prev:
            rep.append(f"- 前一共同交易日 {prev[-1]}：偏差 "
                       f"{loc_daily[prev[-1]]['total_asset'] - pf_daily[prev[-1]]['nav']:+.2f}（≈0 则该日突现）")
        big = max(nz, key=lambda t: abs(t[1]))
        last = nz[-1]
        rep.append(f"- 偏差峰值：{big[0]} {big[1]:+.2f}；末日：{last[0]} {last[1]:+.2f}；"
                   f"非零天数 {len(nz)}/{len(common)}")
        seg = [f"{d}:{x:+.0f}" for d, x in nz[:3]] + ["…"] + [f"{d}:{x:+.0f}" for d, x in nz[-3:]]
        rep.append("- 偏差序列（头尾各3）：`" + " ".join(seg) + "`")
    else:
        rep.append("- 全程逐日一致（容差 ±0.005 元）✅")
    J["nav_diff"] = {"first_div_date": first_div[0] if first_div else None,
                     "first_div": first_div[1] if first_div else 0.0,
                     "nonzero_days": len(nz)}

    # ---- 2. 现金流对账 ----
    rep.append("\n## 2. 现金流对账（共同交易日 cash）")
    cash_nz = [(d, loc_daily[d]["cash"] - pf_daily[d]["cash"])
               for d in common if abs(loc_daily[d]["cash"] - pf_daily[d]["cash"]) > TOL_NAV]
    if cash_nz:
        rep.append(f"- 首个现金偏差日：{cash_nz[0][0]}，差 {cash_nz[0][1]:+.2f} 元；"
                   f"共 {len(cash_nz)} 天非零")
    else:
        rep.append("- 全程现金逐位一致 ✅（成交金额/费用完全对齐）")
    J["cash_diff_first"] = cash_nz[0][0] if cash_nz else None

    # ---- 3. 委托序列对齐 diff ----
    rep.append("\n## 3. 委托序列逐笔对齐（同日同码同向合并）")
    pb = bucket_orders(pf["orders"])
    lt = {}
    for row in loc["trades"]:
        side = "buy" if str(row.get("action", "")).lower().startswith("buy") else "sell"
        key = ((row.get("datetime") or "")[:10], norm_code(row.get("code", ""))[:6], side)
        lt[key] = lt.get(key, 0) + int(float(row.get("volume") or 0))
    odiffs = []
    for k in sorted(set(pb) | set(lt)):
        p, l = pb.get(k, 0), lt.get(k, 0)
        if abs(p - l) > TOL_QTY:
            odiffs.append((k, p, l))
    if odiffs:
        rep.append(f"- **委托分歧 {len(odiffs)} 组**（日期/码/方向：平台量 vs 本地量）：")
        for (d, c, s), p, l in odiffs[:12]:
            rep.append(f"  - {d} {c} {s}: 平台 {p} vs 本地 {l}")
        rep.append(f"- 首分歧委托日：{odiffs[0][0][0]}")
    else:
        rep.append("- 全部委托（合并后）逐笔一致 ✅")
    J["order_div_count"] = len(odiffs)
    J["order_first_div"] = odiffs[0][0][0] if odiffs else None
    # S3 路由消费（件 B 2026-10-07）：委托分歧明细（--as-json 新增字段，报告文本不变）
    J["order_divs"] = [[list(k), p, l] for (k, p, l) in odiffs]

    # ---- 4. 市值差分解（首偏日与末日）----
    rep.append("\n## 4. 市值差分解（本地隐含 vs 平台快照）")
    for tag, d in [("首偏日", first_div[0] if first_div else common[-1]),
                   ("末日", common[-1])]:
        e = pf_daily.get(d, {})
        lmv = loc_daily[d]["total_asset"] - loc_daily[d]["cash"]
        pmv = e.get("market_value", 0.0)
        rep.append(f"- {tag} {d}：本地市值 {lmv:.2f} vs 平台 {pmv:.2f}（差 {lmv-pmv:+.2f}）；"
                   f"平台持仓："
                   + "，".join(f"{p['code'][:6]}×{p['volume']}@{p['last_price']}"
                               for p in e.get("positions", [])))

    # ---- 5. 三态初步分类 ----
    rep.append("\n## 5. 三态初步分类（供 diff-triage 仲裁）")
    if not nz and not odiffs and not cash_nz:
        verdict = "已对齐"
    elif first_div and not cash_nz and not odiffs:
        verdict = ("不可归因→立新案（净值偏差但委托/现金全一致 ⇒ 估值口径类（DAT）候选；"
                   "用 §4 分解定位标的级来源）")
    elif odiffs and cash_nz and first_div and odiffs[0][0][0] <= first_div[0] <= (cash_nz[0][0] if cash_nz else "9999"):
        verdict = "归因候选（委托分歧在先 ⇒ 触发链回溯：订单语义/API/风控状态（POS/ENG/API 类））"
    else:
        verdict = "混合形态（需人工仲裁）"
    rep.append(f"- **判定：{verdict}**")
    J["verdict"] = verdict

    # ---- 6. 案件路由 S3（件 B，2026-10-07；§1-5 逐字节不变，本节纯追加）----
    try:
        from align_triage_rules import triage
        route = triage(J)
        rep.append("\n## 6. 案件路由（S3 判别特征规则库 · 机判人核，非终判）")
        # 残差达标判定（对齐生命周期 aligned 态的机检判据 <0.5%；§5 零偏差口径之外
        # 的达标语义由本节承载——尾差级噪声带内即达标，防误 BLOCK 已达标策略）
        last_dev = nz[-1][1] if nz else 0.0
        total_asset = loc_daily[common[-1]]["total_asset"] if common else 0.0
        residual_pct = abs(last_dev) / total_asset if total_asset else 0.0
        route["residual_pct"] = round(residual_pct, 6)
        route["residual_ok"] = residual_pct < 0.005
        if route["residual_ok"] and nz:
            rep.append(f"- **残差达标 ✅**：末值 {last_dev:+.2f} 元 / 总资产 {total_asset:.2f} "
                       f"= {residual_pct:.4%} < 0.5%（对齐门 aligned 判据满足）")
        if not route["candidates"] and not route["new_case"]:
            rep.append("- 已对齐，无需路由 ✅")
        elif route["candidates"]:
            for c in route["candidates"]:
                rep.append(f"- 候选 `{c['id']}` → {c['case_type']}：{c['reason']}")
                rep.append(f"  - 证据：{c['evidence']}")
            if route["residual_ok"]:
                rep.append("- 残差已达对齐门判据：候选若属台账 §4 登记噪声带（尾差级）可直接判 aligned")
        else:
            rep.append("- **无规则命中 → 立新案**（进根因证实，铁律 L2 人审：未证实不得修）")
        J["triage"] = route
    except Exception as exc:            # 路由失败不拖垮 S1 报告
        rep.append(f"\n## 6. 案件路由（S3）\n- 路由器不可用：{exc}")

    out = "\n".join(rep)
    print(out)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"\n→ 报告写入 {args.output}")
    if args.as_json:
        Path(args.as_json).write_text(json.dumps(J, ensure_ascii=False, indent=1),
                                      encoding="utf-8")


if __name__ == "__main__":
    main()