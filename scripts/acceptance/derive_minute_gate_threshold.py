"""阈值推导取证：分钟表 UnitCheck 拒绝率的**多窗分布**（问题3 选项1 实证依据）。

目的：为「分钟表独立门禁阈值」提供实测分布，拒绝「直接拍 6%」。
方法：跨 2025~2026 取 N 个 1 日窗（覆盖 ETF/股票分钟两表），逐窗计算
      UnitCheck 判据越界行占比，给出分位（P50/P90/P99/max）与按窗聚合/按行聚合两口径。

判据口径与 validator 一致：amount/(close×vol) ∈ [0.5, 2.0]，close 用云端 close
（本轮已实证云端 close 与 amount/vol 同基准，占 84%~99.94%）。
分母口径同时给出两种：
  ① 按窗（本管线门禁实际口径：一次任务/一个窗的 rejected/raw）
  ② 按行（客户反馈中的整表口径：62.2M 行累计 1.4985%）
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
sys.path.insert(0, str(ROOT))
from quantstudio.pipeline.mcp.client import MCPClient, load_mcp_api_key  # noqa: E402

key = load_mcp_api_key()
cli = MCPClient(endpoint="https://124.223.159.234/mcp", api_key=key)
cli.handshake()

# 覆盖不同市场阶段的 1 日窗（含除权高发期与平稳期）
WINDOWS = [
    ("2025-01-06", "2025-01-07"), ("2025-02-10", "2025-02-11"),
    ("2025-03-10", "2025-03-11"), ("2025-04-07", "2025-04-08"),
    ("2025-05-12", "2025-05-13"), ("2025-06-03", "2025-06-04"),
    ("2025-06-16", "2025-06-17"), ("2025-07-07", "2025-07-08"),
    ("2025-08-11", "2025-08-12"), ("2025-09-08", "2025-09-09"),
    ("2025-10-13", "2025-10-14"), ("2025-11-10", "2025-11-11"),
    ("2025-12-08", "2025-12-09"), ("2026-01-05", "2026-01-06"),
    ("2026-02-09", "2026-02-10"), ("2026-03-09", "2026-03-10"),
    ("2026-04-13", "2026-04-14"), ("2026-06-08", "2026-06-09"),
    ("2026-07-13", "2026-07-14"), ("2026-09-21", "2026-09-22"),
]

LIMIT = 250_000


def pull(ds, ts, te):
    try:
        ref = cli.create_export_job(ds, page_size=50_000,
                                    time_start=ts, time_end=te, row_limit=LIMIT)
        man = cli.get_manifest(ref)
        if not man.shards:
            return pd.DataFrame()
        return pd.concat([pd.read_parquet(io.BytesIO(
            cli.get_artifact(ref, f"{ref}/{s.shard_id}").parquet_bytes))
            for s in man.shards], ignore_index=True)
    except Exception as e:
        print(f"    (pull 失败 {ts}: {type(e).__name__})", flush=True)
        return pd.DataFrame()


def window_rate(ds, ts, te):
    d = pull(ds, ts, te)
    if d.empty:
        return None
    m = (d["vol"] > 0) & (d["close"] > 0) & (d["amount"] > 0)
    if not m.any():
        return None
    n = int(m.sum())
    r = (d.loc[m, "amount"] / (d.loc[m, "close"] * d.loc[m, "vol"]))
    bad = int((~r.between(0.5, 2.0)).sum())
    return n, bad


for ds, label in (("qdb.etf_minutes", "etf_minutes"),
                  ("qdb.stock_minutes", "stock_minutes")):
    print(f"\n=== {label} 逐窗拒绝率（1 日窗）===", flush=True)
    rates = []
    agg_n = agg_bad = 0
    for ts, te in WINDOWS:
        r = window_rate(ds, ts, te)
        if r is None:
            continue
        n, bad = r
        agg_n += n
        agg_bad += bad
        rate = bad / n
        rates.append(rate)
        print(f"  {ts}: n={n:>7} rejected={bad:>6} rate={rate:.4%}", flush=True)
    if not rates:
        continue
    a = np.array(rates)
    print(f"\n  --- {label} 窗级分布（{len(a)} 窗）---")
    print(f"  P50={np.quantile(a,0.50):.4%}  P90={np.quantile(a,0.90):.4%}  "
          f"P99={np.quantile(a,0.99):.4%}  max={a.max():.4%}  "
          f"min={a.min():.4%}  mean={a.mean():.4%}")
    print(f"  --- 行级聚合（全样本累计）---")
    print(f"  累计 n={agg_n} rejected={agg_bad} 整表口径={agg_bad/agg_n:.4%}")
    # 阈值候选：窗级 P99 + 95% 相对余量（不用 max——单窗尖峰不应定义门禁）
    for mult, tag in ((1.5, "P99×1.5"), (1.95, "P99×1.95"), (2.0, "P99×2.0")):
        print(f"  候选阈值 {tag} = {np.quantile(a,0.99)*mult:.4%}")
    print(f"  参考：真单位错（手/股 ×100）时的拒绝率=100%（阈值须远低于它）")