# -*- coding: utf-8 -*-
"""P0 源端验证（MCP vs QuestDB 源错位）—— L0 只读，总调度 2026-11-10 照准。

目的：确认「170 对例外」与「抽样 100 个早于源端首日的对」在 **MCP** 是否也 0 行。
  MCP 全 0    ⇒ QuestDB 判定成立，settle 方案（8b8f613）可送②审
  MCP 有数据  ⇒ 源错位确认，转回填路线重算

模式：
  --smoke  只测 3 个样本（验证接口通不通）
  默认     全量（170 + 100 抽样）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
CST = timezone(timedelta(hours=8))

EXC = ROOT / "docs" / "evidence" / "exception-170-probe-20261110.json"
PROBE = ROOT / "docs" / "evidence" / "qfq-gap-probe-20261110.json"


def build_client():
    """构造 MCP 客户端（读 profile 配置）。

    注意（2026-11-10 实测）：类名是 `MCPClient`（全大写），首参名为 `endpoint`
    而非 `base_url`——照 config 的 base_url 键取值后按位置/关键字传给 endpoint。
    """
    from quantstudio.pipeline.mcp.client import MCPClient  # noqa: E402

    cfg_path = ROOT / "config" / "profiles" / "mcp_only" / "sources_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    mcp = cfg["sources"]["mcp"]
    cli = MCPClient(endpoint=mcp["base_url"], tls_verify=bool(mcp.get("tls_verify", True)))
    cli.endpoint_label = mcp["base_url"]
    return cli


def mcp_rows_on(cli, dataset_id: str, code: str, day: str) -> int | str:
    """查 MCP 某 code 某日行数。stock_daily 用 trade_date 过滤。"""
    ts_code = f"{code}.SH" if str(code).startswith(("6", "9")) else f"{code}.SZ"
    try:
        page = cli.query_snapshot(
            dataset_id,
            columns=["ts_code", "trade_date"],
            limit=10,
            filters=[
                {"column": "ts_code", "op": "=", "value": ts_code},
                {"column": "trade_date", "op": "=", "value": day},
            ],
        )
        return len(page.rows)
    except Exception as e:  # noqa: BLE001
        return f"ERR({type(e).__name__}: {str(e)[:60]})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="只测 3 个样本")
    ap.add_argument("--sample", type=int, default=100, help="抽样「早于源端首日」的数量")
    args = ap.parse_args()

    print("=" * 78)
    print("P0 源端验证（MCP vs QuestDB）")
    print("=" * 78)

    cli = build_client()
    print(f"  base_url = {getattr(cli, 'base_url', '?')}")

    # ---- 握手 + 健康检查（必须先 handshake 拿 session，否则工具调用 400 Missing session ID）----
    try:
        info = cli.handshake()
        print(f"  ✅ handshake OK: {info}")
    except Exception as e:
        print(f"  ❌ handshake 失败: {type(e).__name__}: {str(e)[:200]}")
        return 3
    try:
        h = cli.ping_health()
        print(f"  ✅ ping_health OK: {h}")
    except Exception as e:
        print(f"  ⚠ ping_health 失败（握手已通，继续）: {type(e).__name__}: {str(e)[:120]}")

    exc = json.loads(EXC.read_text(encoding="utf-8"))
    targets_170 = [(e["code"], e["day"]) for e in exc["no_source"]]

    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    # 抽样「早于源端首日」的对（这些是 5,816 的主体）
    early = []
    fd_cache = {}
    for g in probe["STOCK_daily"]["no_source"]:
        for code in g["codes"]:
            early.append((str(code), g["day"]))
    sample = early[: args.sample]

    todo = targets_170 + sample
    if args.smoke:
        todo = targets_170[:2] + sample[:1]

    print(f"\n  待测: {len(todo)} 对（170 例外 {len(targets_170)} + 抽样 {len(sample)}）")

    t0 = time.time()
    results = []
    mcp_hit = 0
    mcp_zero = 0
    mcp_err = 0

    for i, (code, day) in enumerate(todo, 1):
        n = mcp_rows_on(cli, "stock_daily", code, day)
        rec = {"code": code, "day": day, "mcp_rows": n}
        results.append(rec)
        if isinstance(n, int):
            if n > 0:
                mcp_hit += 1
            else:
                mcp_zero += 1
        else:
            mcp_err += 1

        if i % 20 == 0 or i == len(todo) or args.smoke:
            el = time.time() - t0
            print(f"  [{i}/{len(todo)}] MCP有={mcp_hit} 0行={mcp_zero} 错误={mcp_err} "
                  f"已用 {el/60:.1f}min")

    print("\n" + "=" * 78)
    print("判定")
    print("=" * 78)
    print(f"  MCP 有数据（⇒ 源错位，转回填）: {mcp_hit}")
    print(f"  MCP 0 行（⇒ QuestDB 判定成立）: {mcp_zero}")
    print(f"  查询错误: {mcp_err}")

    if mcp_err > len(todo) * 0.3:
        verdict = "INCONCLUSIVE_ERRORS"
        print(f"\n  ⚠ 判定 = {verdict}（错误率 > 30%，接口或鉴权有问题）")
    elif mcp_hit == 0:
        verdict = "MCP_ALSO_ZERO"
        print(f"\n  ✅ 判定 = {verdict} ⇒ settle 方案（8b8f613）前提成立，可送②审")
    else:
        verdict = "MCP_HAS_DATA"
        print(f"\n  🔴 判定 = {verdict} ⇒ 源错位确认，转回填路线重算")

    tag = "smoke" if args.smoke else "full"
    out = ROOT / "docs" / "evidence" / f"p0-mcp-verify-{tag}-{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "mode": tag,
        "dataset": "stock_daily",
        "verdict": verdict,
        "counts": {"tested": len(todo), "mcp_hit": mcp_hit,
                   "mcp_zero": mcp_zero, "mcp_err": mcp_err},
        "elapsed_sec": round(time.time() - t0, 1),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  证据: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
