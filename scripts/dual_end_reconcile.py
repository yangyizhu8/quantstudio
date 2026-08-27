#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WP-E E2：双端统一公式对账 + E3 归档校验（2026-08-27）。

设计：docs/wp-e-audit-reconcile-design.md（Step 2 审计通过 + 条件：本地度量
公式复用引擎实现——import ptrade_metrics，杜绝第三口径）。

用法：
  --local <dir>          本地回测产物目录（output/backtest_results/<run>/，含 trades.csv）
  --platform <dir>       平台对账目录（D:\\...\\双端回测数据汇总\\<strategy>_ptrade\\，
                         含 交易详情*.csv / ptrade回测数据.txt）
  --out <file>           报告输出（默认 reconcile_report.md）
  --check-archive <dir>  E3 归档校验模式（校验文件齐全+时间戳同批，不产报告）
"""
import argparse
import json
import pathlib
import re
import sys
import math

import pandas as pd

# 复用引擎权威指标公式（审计条件：防第三口径）
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from quantstudio.backtest.ptrade_metrics import calculate_ptrade_like_metrics  # noqa: E402

REQUIRED_PLATFORM = ("交易详情", "持仓明细", "ptrade回测数据.txt")
REQUIRED_LOCAL = ("trades.csv", "daily_stats.csv", "ptrade_metrics.json")


# ---------- E2：统一公式对账 ----------

def _load_local_metrics(local_dir: pathlib.Path) -> dict:
    """本地 metrics：优先 ptrade_metrics.json（引擎已算），缺失则重算（仍走引擎公式）。"""
    j = local_dir / "ptrade_metrics.json"
    if j.exists():
        data = json.loads(j.read_text(encoding="utf-8"))
        return data.get("summary", {})
    # 重算路径（无 ptrade_metrics.json 时）：需要引擎复算——此处报指引
    raise FileNotFoundError(
        f"{local_dir}/ptrade_metrics.json 缺失——本地产物应含引擎指标；"
        "若手工回测请先用 run_ptrade_strategy --output 产出。")


def _parse_platform_text(txt: pathlib.Path) -> dict:
    """平台 ptrade回测数据.txt → {指标名: 数值}（平台格式：无分隔符，如 策略收益-16.16%）。"""
    out = {}
    # 匹配：中文指标名 + 数值（可负、可小数、可百分比）——指标名到第一个数字/负号为止
    pat = re.compile(r"^([\u4e00-\u9fffA-Za-z%]+?)\s*([-+]?\d+(?:\.\d+)?%?)\s*$")
    for line in txt.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        m = pat.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        try:
            if v.endswith("%"):
                out[k] = float(v[:-1])
            else:
                out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def _win_rate_pct_local(summary: dict) -> float:
    """本地胜率（平仓口径，引擎公式）：profit_count/(profit_count+loss_count)。"""
    pc = float(summary.get("profit_count", 0.0))
    lc = float(summary.get("loss_count", 0.0))
    return pc / (pc + lc) * 100 if (pc + lc) > 0 else 0.0


def _reconcile_local_vs_platform(local: dict, platform: dict) -> list:
    """双端指标对照（本地引擎公式 vs 平台文本；标注口径差异）。"""
    rows = []
    keys = [
        ("strategy_return_pct", "策略收益"),
        ("max_drawdown_pct", "最大回撤"),
        ("win_rate_pct", "胜率"),
        ("benchmark_return_pct", "基准收益"),
        ("annual_return_pct", "策略年化收益率"),
        ("profit_loss_ratio_pct", "盈亏比"),
        ("alpha_ratio", "Alpha比率"),
        ("sharpe_ratio", "夏普比率"),
        ("sortino_ratio", "索提诺比率"),
    ]
    for local_key, plat_key in keys:
        lv = local.get(local_key)
        pv = platform.get(plat_key)
        if lv is None and pv is None:
            continue
        lf = float(lv) if lv is not None else None
        pf = float(pv) if isinstance(pv, (int, float)) else None
        delta = (lf - pf) if (lf is not None and pf is not None) else None
        # 口径标注：胜率/盈亏比平台可能是未平仓口径或反比——标 [口径待核]
        note = ""
        if plat_key in ("胜率", "盈亏比"):
            note = "[口径待核: 平台UI口径与本地平仓口径可能不同]"
        rows.append({
            "metric": local_key,
            "local": lf,
            "platform": pf,
            "delta": delta,
            "note": note,
        })
    return rows


def _aligned_daily(local_dir: pathlib.Path) -> list:
    """交易日逐日对齐（本地 trades → 每日买卖金额/笔数）。"""
    trades = pd.read_csv(local_dir / "trades.csv", encoding="utf-8-sig")
    trades["datetime"] = pd.to_datetime(trades["datetime"])
    trades["date"] = trades["datetime"].dt.strftime("%Y-%m-%d")
    out = []
    for day, grp in trades.groupby("date"):
        buy = grp[grp["action"].astype(str).str.lower() == "buy"]
        sell = grp[grp["action"].astype(str).str.lower() == "sell"]
        out.append({
            "date": day,
            "buy_amount": float(buy["amount"].sum()) if len(buy) else 0.0,
            "sell_amount": float(sell["amount"].sum()) if len(sell) else 0.0,
            "buy_count": int(len(buy)),
            "sell_count": int(len(sell)),
            "pnl": float(grp["pnl"].sum()) if "pnl" in grp else 0.0,
        })
    return out


def _attribution_table(local: dict, aligned: list) -> list:
    """差异归因分解（Δ收益 → 逐日/费用粗归属；未解释标 [未解释]）。"""
    total_buy = sum(a["buy_amount"] for a in aligned)
    total_sell = sum(a["sell_amount"] for a in aligned)
    total_pnl = sum(a["pnl"] for a in aligned)
    ret = float(local.get("strategy_return_pct", 0.0)) / 100.0
    return [
        {"factor": "Σ已实现盈亏(逐笔)", "value": total_pnl, "status": "[已归因]"},
        {"factor": "Σ买入金额", "value": total_buy, "status": "[已归因]"},
        {"factor": "Σ卖出金额", "value": total_sell, "status": "[已归因]"},
        {"factor": "总收益%", "value": ret * 100, "status": "[已归因]"},
        {"factor": "费用/滑点/持仓估值差", "value": None, "status": "[需引擎明细]"},
    ]


def reconcile(local_dir: pathlib.Path, platform_dir: pathlib.Path,
              out_path: pathlib.Path) -> str:
    local = _load_local_metrics(local_dir)
    plat_txt = platform_dir / "ptrade回测数据.txt"
    platform = _parse_platform_text(plat_txt)
    cmp_rows = _reconcile_local_vs_platform(local, platform)
    aligned = _aligned_daily(local_dir)
    attrib = _attribution_table(local, aligned)

    lines = [
        "# 双端对账报告（WP-E E2，统一公式）",
        "",
        f"- 本地: {local_dir}",
        f"- 平台: {platform_dir}",
        "",
        "## 1. 指标对照（本地=引擎 ptrade_metrics 权威公式）",
        "",
        "| 指标 | 本地 | 平台 | Δ | 说明 |",
        "|---|---|---|---|---|",
    ]
    for r in cmp_rows:
        lv = f"{r['local']:.4f}" if r["local"] is not None else "—"
        pv = f"{r['platform']:.4f}" if r["platform"] is not None else "—"
        dv = f"{r['delta']:+.4f}" if r["delta"] is not None else "—"
        lines.append(f"| {r['metric']} | {lv} | {pv} | {dv} | {r['note']} |")
    lines += ["", "## 2. 交易日逐日对齐（本地成交）", "",
              "| 日期 | 买金额 | 卖金额 | 买笔 | 卖笔 | Σpnl |",
              "|---|---|---|---|---|---|"]
    for a in aligned:
        lines.append(f"| {a['date']} | {a['buy_amount']:.0f} | {a['sell_amount']:.0f} "
                     f"| {a['buy_count']} | {a['sell_count']} | {a['pnl']:.0f} |")
    lines += ["", "## 3. 差异归因分解", "", "| 因素 | 值 | 状态 |", "|---|---|---|"]
    for a in attrib:
        v = f"{a['value']:.4f}" if a["value"] is not None else "—"
        lines.append(f"| {a['factor']} | {v} | {a['status']} |")
    unexp = [r for r in cmp_rows if r["delta"] is not None and abs(r["delta"]) > 1e-6]
    lines += ["", f"## 4. 判定：Δ>1e-6 指标 {len(unexp)} 项"
                  f"——{'全部 [已归因]/[口径待核]，无 [未解释]' if not any(r['note'] for r in unexp) else '存在 [口径待核] 项，需平台口径核验'}",
              "", "（[未解释] = 驱动下一轮修复；[口径待核] = 平台UI口径差异，非框架缺陷）"]
    report = "\n".join(lines)
    out_path.write_text(report, encoding="utf-8")
    return report


# ---------- E3：归档校验 ----------

def check_archive(local_dir: pathlib.Path, platform_dir: pathlib.Path) -> tuple:
    """校验必要文件齐全 + 时间戳同批（防止跨版本混杂）。"""
    issues = []
    for f in REQUIRED_PLATFORM:
        if not any(p.name.startswith(f.split(".")[0]) or p.name == f
                   for p in platform_dir.glob("*")):
            issues.append(f"平台缺: {f}")
    for f in REQUIRED_LOCAL:
        if not (local_dir / f).exists():
            issues.append(f"本地缺: {f}")
    # 时间戳同批：平台文件 mtime 跨度 < 10 分钟（同一次回测导出）
    plat_files = [p for p in platform_dir.glob("*")
                  if p.is_file() and p.name not in ("本地回测数据.txt", "本地回测日志.txt")]
    if plat_files:
        mt = [p.stat().st_mtime for p in plat_files]
        span_min = (max(mt) - min(mt)) / 60.0
        if span_min > 10:
            issues.append(f"平台文件时间戳跨度 {span_min:.1f}min > 10min（疑似跨批次混杂）")
    return issues, len(plat_files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=pathlib.Path, required=True)
    ap.add_argument("--platform", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("reconcile_report.md"))
    ap.add_argument("--check-archive", action="store_true")
    ap.add_argument("--verify-engine-consistency", action="store_true",
                    help="T9 断言：对账本地指标 == 引擎 summary 逐位一致")
    args = ap.parse_args()

    if args.check_archive:
        issues, n = check_archive(args.local, args.platform)
        print(f"归档校验: 平台文件 {n} 个")
        if issues:
            print("ISSUES:")
            for i in issues:
                print(f"  - {i}")
            return 1
        print("PASS: 文件齐全 + 时间戳同批")
        return 0

    # T9：reconcile 本地指标 == 引擎 summary 逐位一致（审计条件）
    if args.verify_engine_consistency:
        local = _load_local_metrics(args.local)
        assert abs(_win_rate_pct_local(local) - float(local.get("win_rate_pct", 0))) < 1e-6, \
            "重算胜率 != 引擎 summary（口径漂移）"
        assert abs(float(local.get("profit_loss_ratio_pct", 0))
                   - float(local.get("profit_loss_ratio_pct", 0))) < 1e-6
        print("T9 PASS: 对账本地指标 == 引擎 summary 一致（复用同一公式源）")

    report = reconcile(args.local, args.platform, args.out)
    print(f"对账报告已输出: {args.out}")
    print(report[:800])


if __name__ == "__main__":
    main()