#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""线1「is_qfq 还原 raw 方案」黄金验收脚本。

任务书：docs/mcp_migration/is_qfq_restore-raw-task.md

背景（一句话）：QuestDB 存的是 qfq 而非 raw，MCP 管线直接消费导致双重复权。
adapter 侧还原公式：

    raw_i = qfq_i * adj_factor_latest_global / adj_factor_i

其中 adj_factor_latest_global 必须取**云端因子系列的全局最新**（来自 qfq_aux.db
完整因子历史），**绝不能**用本次 export 分片内最后一行的因子（codex P0-①）。

验收断言（对应 codex 硬条件）：

    A1 [P0-①] _get_adj_latest_global('300750') == 1.9495（全局最新，非窗口末行）
    A2 [黄金]  2025-04-21 qfq 222.519 / adj 1.8750 -> raw 231.36 (tol 0.01)
    A3 [黄金]  2025-04-22 qfq 226.312 / adj 1.9125 -> raw 230.69 (tol 0.01)
    A4 [P0-①反例] 2024-06 历史窗口（不含最新日期）还原 2024-06-03 得 202.5001，
                  而非误用分片末行因子 1.8660 得到的 193.8267（差 8.67 元）
    A5 [P1-⑥] metadata 追溯字段：is_qfq_restored / restored_rows / adj_latest_source
    A6 [P1-③] is_qfq=False 抽样对照（需 DuckDB 主库可读，锁占用时降级 SKIP）
    A7 [P1-⑤] etf_minutes / fund_adj 因子覆盖检查

用法：
    # 离线核心断言（不连 MCP、不需要网络）
    python scripts/verify_mcp_qfq_restore.py

    # 叠加 DuckDB raw 对照（主库被 qfq_orchestrator/daemon 占用时自动降级 SKIP）
    python scripts/verify_mcp_qfq_restore.py --duckdb-compare --sample-n 20

    # 端到端在线验证（真连 MCP 拉 300750 窗口）
    python scripts/verify_mcp_qfq_restore.py --online

退出码：0 = 全部 PASS（SKIP 不算失败）；1 = 存在 FAIL。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --- Windows 控制台 UTF-8 ---------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd  # noqa: E402

CST = _dt.timezone(_dt.timedelta(hours=8))

# ---------------------------------------------------------------------------
# 黄金常量（来源：任务书 §1 + 附录 C，2026-08-02 ZCode 实测）
# ---------------------------------------------------------------------------
GOLD_CODE = "300750"          # 宁德时代
GOLD_TS_CODE = "300750.SZ"
PRICE_TOL = 0.01              # 1 tick = A 股最小价格变动单位

# 全局最新 adj_factor（云端因子系列；tushare 系列为 1.9816，差 1.6% 属设计内）
GOLD_ADJ_LATEST_GLOBAL = 1.9495
TUSHARE_SERIES_LATEST = 1.9816

# 附录 C.2 日线黄金数字
GOLD_DAILY = [
    # (trade_date, qfq_close, adj_i, expected_raw_close)
    (20250421, 222.519, 1.8750, 231.36),
    (20250422, 226.312, 1.9125, 230.69),
]

# 附录 C.3 P0-① 反例（2024-06 窗口）
CASE_A4 = {
    "trade_date": 20240603,
    "qfq_close": 193.8890,        # 由 202.5001 * 1.8666 / 1.9495 反推，闭环校验见下
    "adj_i": 1.8666,              # qfq_aux.db 实测 2024-06-03 因子
    "expected_raw": 202.5001,     # 正确：用全局 latest 1.9495
    "wrong_shard_tail_adj": 1.8660,   # 2024-06 分片末行因子（6-26~6-28 实测）
    "wrong_raw": 193.8267,        # 错误：误用分片末行做 latest
    "min_delta": 8.0,             # 正确与错误相差 8.67 元，远超 1 tick
}

DEFAULT_MAIN_DB = "data/quantstudio.db"
DEFAULT_AUX_DB = "data/qfq_aux.db"

# 云端原始列名（adapter 层还原作用于原始列，映射由 aligner 在 daemon 侧完成）
PRICE_COLS_DAILY = ["open", "high", "low", "close", "pre_close"]
PRICE_COLS_MINUTE = ["open", "high", "low", "close"]


# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------
class Results:
    def __init__(self) -> None:
        self.items: List[Dict] = []

    def add(self, cid: str, name: str, status: str, detail: str = "",
            evidence: Optional[Dict] = None) -> None:
        self.items.append({
            "id": cid, "name": name, "status": status,
            "detail": detail, "evidence": evidence or {},
        })
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]",
                "SKIP": "[SKIP]", "WARN": "[WARN]"}.get(status, "[????]")
        print(f"{icon} {cid} {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {line}")

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i["status"] == "FAIL")

    def summary(self) -> str:
        c = {}
        for i in self.items:
            c[i["status"]] = c.get(i["status"], 0) + 1
        return " / ".join(f"{k}={v}" for k, v in sorted(c.items()))


def _fmt(v) -> str:
    return "None" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


# ---------------------------------------------------------------------------
# 被测对象加载（未实现时返回 None，让断言变红而不是脚本崩溃）
# ---------------------------------------------------------------------------
def load_adapter(main_db: str):
    """构造 MCPAdapter（lazy client：不触发网络握手）。"""
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
    return MCPAdapter({
        "main_db": main_db,
        "enable_qfq_injection": False,   # 验收脚本不写库
        "tls_verify": False,
    })


def has_method(obj, name: str) -> bool:
    return callable(getattr(obj, name, None))


def make_daily_df(rows: List[Tuple[int, float, float]],
                  ts_code: str = GOLD_TS_CODE,
                  is_qfq: bool = True) -> pd.DataFrame:
    """构造云端 stock_daily 形态的 DataFrame（原始列名）。

    rows: [(trade_date, qfq_close, adj_i), ...]
    OHLC 全部填同一 qfq 价，便于逐列校验还原是否覆盖全部价格列。
    """
    recs = []
    for d, px, adj in rows:
        recs.append({
            "ts_code": ts_code,
            "trade_date": d,
            "open": px, "high": px, "low": px, "close": px, "pre_close": px,
            "vol": 1000.0, "amount": px * 1000.0, "pct_chg": 0.0,
            "adj_factor": adj,
            "is_qfq": is_qfq,
        })
    return pd.DataFrame(recs)


# ---------------------------------------------------------------------------
# A1 [P0-①] 全局最新因子取法
# ---------------------------------------------------------------------------
def case_a1(res: Results, adapter, aux_db: str) -> None:
    cid, name = "A1", "[P0-①] _get_adj_latest_global 取全局最新因子(1.9495)"
    if not has_method(adapter, "_get_adj_latest_global"):
        res.add(cid, name, "FAIL", "MCPAdapter._get_adj_latest_global 未实现（RED）")
        return
    try:
        got = adapter._get_adj_latest_global([GOLD_CODE], asset_type="STOCK")
        val = got.get(GOLD_CODE) if isinstance(got, dict) else got
        ok = val is not None and abs(float(val) - GOLD_ADJ_LATEST_GLOBAL) < 1e-6
        # 交叉核对：直接查 aux 库全历史最大 time 的因子
        with sqlite3.connect(f"file:{aux_db}?mode=ro", uri=True) as con:
            ref = con.execute(
                "SELECT adj_factor FROM adj_factor WHERE code=? "
                "ORDER BY time DESC LIMIT 1", (GOLD_CODE,)).fetchone()
        ref_val = float(ref[0]) if ref else None
        detail = (f"got={_fmt(val)}  expected={GOLD_ADJ_LATEST_GLOBAL}  "
                  f"aux_db全历史末行={_fmt(ref_val)}\n"
                  f"注意 tushare 系列最新={TUSHARE_SERIES_LATEST}（不得混用）")
        res.add(cid, name, "PASS" if ok else "FAIL", detail,
                {"got": val, "expected": GOLD_ADJ_LATEST_GLOBAL, "aux_ref": ref_val})
    except Exception as e:
        res.add(cid, name, "FAIL", f"调用异常: {type(e).__name__}: {e}\n"
                                   f"{traceback.format_exc(limit=3)}")


# ---------------------------------------------------------------------------
# A2 / A3 黄金数字：日线还原
# ---------------------------------------------------------------------------
def case_gold_daily(res: Results, adapter) -> None:
    if not has_method(adapter, "_restore_to_raw"):
        for idx, (d, qfq, adj, exp) in enumerate(GOLD_DAILY, start=2):
            res.add(f"A{idx}", f"[黄金] {d} 还原 raw close={exp}",
                    "FAIL", "MCPAdapter._restore_to_raw 未实现（RED）")
        return

    df = make_daily_df([(d, qfq, adj) for d, qfq, adj, _ in GOLD_DAILY])
    try:
        out, meta = adapter._restore_to_raw(df.copy(), "stock_daily", "daily")
    except Exception as e:
        for idx, (d, _q, _a, exp) in enumerate(GOLD_DAILY, start=2):
            res.add(f"A{idx}", f"[黄金] {d} 还原 raw close={exp}", "FAIL",
                    f"_restore_to_raw 异常: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc(limit=3)}")
        return

    for idx, (d, qfq, adj, exp) in enumerate(GOLD_DAILY, start=2):
        cid = f"A{idx}"
        name = f"[黄金] {d} qfq={qfq} adj_i={adj} -> raw close={exp} (tol {PRICE_TOL})"
        sub = out[out["trade_date"] == d]
        if len(sub) != 1:
            res.add(cid, name, "FAIL", f"还原结果行数异常: {len(sub)}")
            continue
        got = float(sub.iloc[0]["close"])
        dev = abs(got - exp)
        # 全部价格列都必须被还原（OHLC + pre_close 输入同值，输出也应同值）
        col_vals = {c: float(sub.iloc[0][c]) for c in PRICE_COLS_DAILY
                    if c in sub.columns}
        all_cols_ok = all(abs(v - exp) <= PRICE_TOL for v in col_vals.values())
        missing = [c for c in PRICE_COLS_DAILY if c not in sub.columns]
        ok = dev <= PRICE_TOL and all_cols_ok and not missing
        detail = (f"got close={got:.4f}  expected={exp}  偏差={dev:.4f}\n"
                  f"价格列还原情况: "
                  f"{ {k: round(v, 4) for k, v in col_vals.items()} }"
                  + (f"\n缺失价格列: {missing}" if missing else ""))
        res.add(cid, name, "PASS" if ok else "FAIL", detail,
                {"got": got, "expected": exp, "deviation": dev,
                 "price_cols": col_vals})

    # 非价格列必须原样保留（不得误还原 vol/amount/pct_chg）
    cid, name = "A2b", "[契约] vol/amount/pct_chg 不被还原（仅价格列参与）"
    try:
        keep_ok = (
            float(out.iloc[0]["vol"]) == 1000.0
            and abs(float(out.iloc[0]["pct_chg"]) - 0.0) < 1e-9
        )
        res.add(cid, name, "PASS" if keep_ok else "FAIL",
                f"vol={out.iloc[0]['vol']} amount={out.iloc[0]['amount']} "
                f"pct_chg={out.iloc[0]['pct_chg']}")
    except Exception as e:
        res.add(cid, name, "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# A4 [P0-①反例] 历史窗口不得使用分片末行因子
# ---------------------------------------------------------------------------
def case_a4(res: Results, adapter, aux_db: str) -> None:
    cid = "A4"
    name = ("[P0-①反例] 2024-06 历史窗口还原 2024-06-03 = 202.5001，"
            "非分片末行(1.8660)得到的 193.8267")
    c = CASE_A4

    # 先自证反例数据本身闭环（与任务书附录 C.3 对齐）
    correct = c["qfq_close"] * GOLD_ADJ_LATEST_GLOBAL / c["adj_i"]
    wrong = c["qfq_close"] * c["wrong_shard_tail_adj"] / c["adj_i"]
    base_ok = (abs(correct - c["expected_raw"]) < 0.01
               and abs(wrong - c["wrong_raw"]) < 0.01
               and (correct - wrong) > c["min_delta"])
    if not base_ok:
        res.add(cid + "-pre", "[自检] 反例常量闭环", "FAIL",
                f"correct={correct:.4f}(期望{c['expected_raw']}) "
                f"wrong={wrong:.4f}(期望{c['wrong_raw']})")

    if not has_method(adapter, "_restore_to_raw"):
        res.add(cid, name, "FAIL", "MCPAdapter._restore_to_raw 未实现（RED）")
        return

    # 构造只含 2024-06 的分片：末行因子 1.8660，若实现误用末行则必然算出 193.83
    rows = [(c["trade_date"], c["qfq_close"], c["adj_i"]),
            (20240626, 100.0, c["wrong_shard_tail_adj"]),
            (20240627, 100.0, c["wrong_shard_tail_adj"]),
            (20240628, 100.0, c["wrong_shard_tail_adj"])]
    df = make_daily_df(rows)
    try:
        out, meta = adapter._restore_to_raw(df.copy(), "stock_daily", "daily")
        sub = out[out["trade_date"] == c["trade_date"]]
        got = float(sub.iloc[0]["close"])
        ok = abs(got - c["expected_raw"]) <= PRICE_TOL
        looks_like_shard_tail = abs(got - c["wrong_raw"]) <= PRICE_TOL
        detail = (f"got={got:.4f}  正确期望={c['expected_raw']}  "
                  f"分片末行误用值={c['wrong_raw']}  差异={correct - wrong:.4f} 元\n"
                  f"窗口内因子范围: {c['adj_i']} ~ {c['wrong_shard_tail_adj']}（"
                  f"窗口不含全局最新 {GOLD_ADJ_LATEST_GLOBAL}）")
        if looks_like_shard_tail:
            detail += "\n>>> 命中反例：实现误用了分片内最后一行因子作为 latest！"
        res.add(cid, name, "PASS" if ok else "FAIL", detail,
                {"got": got, "correct": c["expected_raw"],
                 "wrong_if_shard_tail": c["wrong_raw"]})
    except Exception as e:
        res.add(cid, name, "FAIL", f"_restore_to_raw 异常: {type(e).__name__}: {e}\n"
                                   f"{traceback.format_exc(limit=3)}")


# ---------------------------------------------------------------------------
# A5 [P1-⑥] metadata 追溯字段
# ---------------------------------------------------------------------------
REQUIRED_META_KEYS = ["is_qfq_restored", "restored_rows", "adj_latest_source"]


def case_a5(res: Results, adapter) -> None:
    cid, name = "A5", "[P1-⑥] 还原追溯字段写入 metadata"
    if not has_method(adapter, "_restore_to_raw"):
        res.add(cid, name, "FAIL", "MCPAdapter._restore_to_raw 未实现（RED）")
        return
    df = make_daily_df([(d, q, a) for d, q, a, _ in GOLD_DAILY])
    try:
        _out, meta = adapter._restore_to_raw(df.copy(), "stock_daily", "daily")
        if not isinstance(meta, dict):
            res.add(cid, name, "FAIL", f"返回 meta 类型异常: {type(meta)}")
            return
        missing = [k for k in REQUIRED_META_KEYS if k not in meta]
        rows_ok = meta.get("restored_rows") == len(df)
        ok = not missing and rows_ok
        res.add(cid, name, "PASS" if ok else "FAIL",
                f"meta={json.dumps({k: str(v) for k, v in meta.items()}, ensure_ascii=False)}\n"
                + (f"缺失字段: {missing}\n" if missing else "")
                + f"restored_rows={meta.get('restored_rows')} 期望={len(df)}",
                {"meta": {k: str(v) for k, v in meta.items()}})
    except Exception as e:
        res.add(cid, name, "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# A6 [P1-③] is_qfq=False 抽样对照（DuckDB raw）
# ---------------------------------------------------------------------------
def case_a6b(res: Results, adapter) -> None:
    """[P1-③ 2026-08-03 实测定论] is_qfq=False 行也是旧基准前复权，必须走还原公式。

    旧断言「is_qfq=False 天然是 raw、原样保留」已被 300182.SZ 实测推翻：
    is_qfq=False 行是用「写入时当时最新 adj_factor」算的旧基准前复权，直通会与
    全局最新基准的新数据产生尺度断层。故无论 is_qfq 取值，全部行走
    raw = qfq × adj_latest_global / adj_i。
    """
    cid, name = "A6b", "[P1-③] is_qfq=False 行也被还原（旧基准前复权归一化）"
    if not has_method(adapter, "_restore_to_raw"):
        res.add(cid, name, "FAIL", "MCPAdapter._restore_to_raw 未实现（RED）")
        return
    # 输入：is_qfq=False 行，close=old-baseline 前复权价，adj_i=1.8750（300750 4-21 因子）
    old_qfq = 231.36
    adj_i = 1.8750
    df = make_daily_df([(20250421, old_qfq, adj_i)], is_qfq=False)
    try:
        # 取全局最新因子（300750 当前 = 1.9495，来自 qfq_aux.db）
        adj_latest_global = adapter._get_adj_latest_global(
            [GOLD_CODE], asset_type="STOCK").get(GOLD_CODE)
        if adj_latest_global is None:
            res.add(cid, name, "FAIL", "无法获取 300750 全局最新因子")
            return
        expected = old_qfq * adj_latest_global / adj_i
        out, meta = adapter._restore_to_raw(df.copy(), "stock_daily", "daily")
        got = float(out.iloc[0]["close"])
        restored = int(meta.get("restored_rows", 0))
        # 关键：① 该行必须被还原（restored_rows==1）；② 输出经公式归一化；
        #       ③ 输出 != 输入（证明不是旧断言的「原样直通」）。
        ok = (restored == 1
              and abs(got - expected) < 1e-6
              and abs(got - old_qfq) > 1e-6)
        res.add(cid, name, "PASS" if ok else "FAIL",
                f"is_qfq=False 输入 close={old_qfq} -> 输出 {got:.4f} "
                f"(公式归一化期望 {expected:.4f}, ratio={adj_latest_global/adj_i:.6f})\n"
                f"restored_rows={restored} 期望=1；"
                f"original_is_qfq_ratio={meta.get('original_is_qfq_ratio')}",
                {"got": got, "expected": expected, "restored_rows": restored,
                 "original_is_qfq_ratio": meta.get("original_is_qfq_ratio")})
    except Exception as e:
        res.add(cid, name, "FAIL", f"{type(e).__name__}: {e}")


def case_a9(res: Results, adapter) -> None:
    """[护栏] adj_i > adj_latest_global 说明本地因子快照落后于云端，必须显性失败。"""
    cid, name = "A9", "[护栏] 因子锚过期(adj_i>全局最新) 触发 fail-fast"
    if not has_method(adapter, "_restore_to_raw"):
        res.add(cid, name, "FAIL", "MCPAdapter._restore_to_raw 未实现（RED）")
        return
    # 2.05 > 1.9495：模拟云端已除权前进、qfq_aux.db 未同步
    df = make_daily_df([(20260801, 100.0, 2.05)])
    try:
        out, _meta = adapter._restore_to_raw(df.copy(), "stock_daily", "daily")
        res.add(cid, name, "FAIL",
                f"未拦截过期锚！还原后 close={float(out.iloc[0]['close']):.4f}\n"
                f"（继续放行会用过期锚产生系统性偏差）")
    except ValueError as e:
        res.add(cid, name, "PASS", f"已 fail-fast: {str(e)[:170]}")
    except Exception as e:
        res.add(cid, name, "FAIL", f"异常类型不符: {type(e).__name__}: {e}")


def case_a6(res: Results, adapter, main_db: str, sample_n: int,
            enabled: bool) -> None:
    """[P1-③] 全市场往返对照：DuckDB raw → 构造 qfq → 还原 → 必须回到原 raw。

    不依赖云端：用 qfq_aux.db 真实因子按云端存储语义
    (qfq = raw × adj_i / adj_latest) 反造 qfq，再走生产还原路径，
    验证公式在真实全市场数据上的自洽性，对照基准是 DuckDB 里的真实 raw。
    """
    cid, name = "A6", f"[P1-③] 全市场往返对照 DuckDB raw (n={sample_n})"
    if not enabled:
        res.add(cid, name, "SKIP", "未启用 --duckdb-compare")
        return
    try:
        import duckdb
    except ImportError:
        res.add(cid, name, "SKIP", "duckdb 模块不可用")
        return
    try:
        con = duckdb.connect(main_db, read_only=True)
    except Exception as e:
        res.add(cid, name, "SKIP",
                f"DuckDB 主库不可读（被常驻 qfq_orchestrator/daemon 独占）：{str(e)[:150]}\n"
                f"→ 按纪律不终止占用进程；请在其空闲期重跑本用例。")
        return
    try:
        rows = con.execute(
            "SELECT code, date, close FROM stock_daily "
            "WHERE date = 20250421 AND close IS NOT NULL AND close > 0 "
            "ORDER BY code LIMIT ?", [int(sample_n)]).fetchall()
    except Exception as e:
        res.add(cid, name, "FAIL", f"查询 DuckDB 失败: {type(e).__name__}: {e}")
        con.close()
        return
    finally:
        try:
            con.close()
        except Exception:
            pass

    if not rows:
        res.add(cid, name, "FAIL", "DuckDB 未取到样本行")
        return

    codes = [str(r[0]) for r in rows]
    latest = adapter._get_adj_latest_global(codes, asset_type="STOCK")
    # 取样本日因子
    import sqlite3 as _sq
    aux = str(Path(main_db).parent / "qfq_aux.db")
    day_ms = int(_dt.datetime(2025, 4, 21, tzinfo=CST).timestamp() * 1000)
    with _sq.connect(f"file:{aux}?mode=ro", uri=True) as acon:
        marks = ",".join(["?"] * len(codes))
        adj_i_map = {str(c): float(a) for c, a in acon.execute(
            f"SELECT code, adj_factor FROM adj_factor "
            f"WHERE time=? AND code IN ({marks})", [day_ms] + codes).fetchall()}

    recs, skipped = [], []
    for code, date, close in rows:
        ai, al = adj_i_map.get(str(code)), latest.get(str(code))
        if not ai or not al:
            skipped.append(str(code))
            continue
        recs.append({
            "ts_code": str(code), "trade_date": int(date),
            "open": close * ai / al, "high": close * ai / al,
            "low": close * ai / al, "close": close * ai / al,
            "pre_close": close * ai / al,
            "vol": 1.0, "amount": 1.0, "pct_chg": 0.0,
            "adj_factor": ai, "is_qfq": True,
            "_expected_raw": float(close),
        })
    if not recs:
        res.add(cid, name, "SKIP",
                f"样本均缺因子（skipped={len(skipped)}），无法往返对照")
        return

    df = pd.DataFrame(recs)
    expected = df.pop("_expected_raw")
    try:
        out, meta = adapter._restore_to_raw(df, "stock_daily", "daily")
    except Exception as e:
        res.add(cid, name, "FAIL", f"_restore_to_raw 异常: {type(e).__name__}: {e}")
        return
    dev = (out["close"].astype(float) - expected.astype(float)).abs()
    worst = float(dev.max())
    n_bad = int((dev > PRICE_TOL).sum())
    ok = n_bad == 0
    sample_show = [(out.iloc[i]["ts_code"], round(float(expected.iloc[i]), 4),
                    round(float(out.iloc[i]["close"]), 4))
                   for i in range(min(5, len(out)))]
    res.add(cid, name, "PASS" if ok else "FAIL",
            f"样本 {len(out)} 只（跳过缺因子 {len(skipped)} 只），"
            f"超容差 {n_bad} 只，最大偏差 {worst:.6f}（容差 {PRICE_TOL}）\n"
            f"前5对照 (code, DuckDB_raw, 还原raw): {sample_show}",
            {"n": len(out), "n_bad": n_bad, "max_dev": worst,
             "skipped_no_factor": len(skipped)})


# ---------------------------------------------------------------------------
# A7 [P1-⑤] etf_minutes / fund_adj 覆盖
# ---------------------------------------------------------------------------
def case_a7(res: Results, aux_db: str) -> None:
    cid, name = "A7", "[P1-⑤] ETF 因子表 fund_adj 覆盖检查（etf_daily/etf_minutes 还原依赖）"
    try:
        with sqlite3.connect(f"file:{aux_db}?mode=ro", uri=True) as con:
            n = con.execute("SELECT COUNT(*) FROM fund_adj").fetchone()[0]
            c = con.execute("SELECT COUNT(DISTINCT code) FROM fund_adj").fetchone()[0]
            na = con.execute("SELECT COUNT(*) FROM adj_factor").fetchone()[0]
            ca = con.execute("SELECT COUNT(DISTINCT code) FROM adj_factor").fetchone()[0]
        detail = (f"fund_adj: rows={n} codes={c}\n"
                  f"adj_factor(STOCK): rows={na} codes={ca}")
        if n == 0:
            detail += ("\n>>> fund_adj 为空：ETF 还原当前无因子来源，"
                       "必须依赖冷启动全历史注入，否则 etf_daily/etf_minutes "
                       "还原会缺因子（需 fail-fast 而非静默放行）。")
            res.add(cid, name, "WARN", detail, {"fund_adj_rows": n})
        else:
            res.add(cid, name, "PASS", detail, {"fund_adj_rows": n})
    except Exception as e:
        res.add(cid, name, "FAIL", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# A8 在线端到端（可选）
# ---------------------------------------------------------------------------
def case_online(res: Results, adapter, enabled: bool) -> None:
    cid, name = "A8", "[端到端] 在线拉取 300750 2025-04-21~04-22 还原后 raw close"
    if not enabled:
        res.add(cid, name, "SKIP", "未启用 --online")
        return
    try:
        df, meta = adapter.fetch_table("stock_daily", "2025-04-21", "2025-04-22",
                                       freq="daily", codes=[GOLD_TS_CODE])
        if df is None or len(df) == 0:
            res.add(cid, name, "FAIL", "云端返回 0 行")
            return
        dcol = "trade_date" if "trade_date" in df.columns else "date"
        got = {}
        for d, exp in ((20250421, 231.36), (20250422, 230.69)):
            sub = df[df[dcol].astype(str).str.replace("-", "").str[:8] == str(d)]
            if len(sub):
                got[d] = float(sub.iloc[0]["close"])
        ok = all(abs(got.get(d, -1) - exp) <= PRICE_TOL
                 for d, exp in ((20250421, 231.36), (20250422, 230.69)))
        res.add(cid, name, "PASS" if ok else "FAIL",
                f"got={got}  expected={{20250421: 231.36, 20250422: 230.69}}\n"
                f"meta.is_qfq_restored={meta.get('is_qfq_restored')} "
                f"restored_rows={meta.get('restored_rows')}",
                {"got": got, "meta_keys": sorted(meta.keys())})
    except Exception as e:
        res.add(cid, name, "FAIL", f"{type(e).__name__}: {e}\n"
                                   f"{traceback.format_exc(limit=3)}")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="线1 is_qfq 还原 raw 黄金验收")
    ap.add_argument("--main-db", default=DEFAULT_MAIN_DB)
    ap.add_argument("--aux-db", default=DEFAULT_AUX_DB)
    ap.add_argument("--online", action="store_true", help="连 MCP 做端到端验证")
    ap.add_argument("--duckdb-compare", action="store_true",
                    help="启用 DuckDB raw 对照（主库被占用时降级 SKIP）")
    ap.add_argument("--sample-n", type=int, default=15)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    main_db = str((_REPO / args.main_db) if not Path(args.main_db).is_absolute()
                  else Path(args.main_db))
    aux_db = str((_REPO / args.aux_db) if not Path(args.aux_db).is_absolute()
                 else Path(args.aux_db))

    print("=" * 78)
    print("线1「is_qfq 还原 raw」黄金验收")
    print(f"  公式: raw_i = qfq_i * {GOLD_ADJ_LATEST_GLOBAL}(全局最新) / adj_factor_i")
    print(f"  main_db = {main_db}")
    print(f"  aux_db  = {aux_db}")
    print(f"  容差    = {PRICE_TOL} (1 tick)")
    print("=" * 78)

    res = Results()
    try:
        adapter = load_adapter(main_db)
    except Exception as e:
        print(f"[FATAL] MCPAdapter 构造失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    case_a1(res, adapter, aux_db)
    case_gold_daily(res, adapter)
    case_a4(res, adapter, aux_db)
    case_a5(res, adapter)
    case_a6b(res, adapter)
    case_a9(res, adapter)
    case_a6(res, adapter, main_db, args.sample_n, args.duckdb_compare)
    case_a7(res, aux_db)
    case_online(res, adapter, args.online)

    print("=" * 78)
    print(f"汇总: {res.summary()}")
    print("=" * 78)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"generated_at": _dt.datetime.now(CST).isoformat(),
                        "results": res.items}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"JSON 结果 -> {args.json_out}")

    return 1 if res.failed else 0


if __name__ == "__main__":
    sys.exit(main())
