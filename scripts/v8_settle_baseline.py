# -*- coding: utf-8 -*-
"""V8 回测哈希基线捕获 / 比对（settle 丙方案 冻结前后逐位一致验收）。

口径**全部复用既有实现**，不自造：
  · 哈希算法  : scripts/baseline_rerun_20260827.py 的 dir_hash（相对路径 + 文件字节 → sha256）
  · 回测入口  : python -m quantstudio.backtest.run_ptrade_strategy <策略> <start> <end> [--profile ...]
  · 策略三元组: 同 baseline_rerun_20260827.py 的 STRATS

复现性前提（2026-11-12 04:15 实测，etf_theme 连跑两次）：
  7 产物文件逐文件哈希全同，dir_hash 全同 ⇒ 同代码同数据 ⇒ 同哈希。
  config.csv / trades.csv / daily_stats.csv / ptrade_metrics.json 均无生成时刻戳
  （ptrade_metrics.json 的 start_date/buy_date 是业务日期，不随时间变）。

用法：
  python scripts/v8_settle_baseline.py --capture
      # 跑三策略，落 docs/evidence/v8-settle-baseline-<date>.json
  python scripts/v8_settle_baseline.py --compare [--baseline <json>]
      # 重跑三策略，与基线逐策略比对 dir_hash；不一致 exit 1（V8 失败 ⇒ 按方案 R2 立即回退）

注意：
  · 只读回测（不写库）；但仍会读生产库 data/quantstudio.db。
  · 须在采集轮之外运行（06:00–16:00 为轮次窗），否则两次跑的读数据可能不同 ⇒ 假 FAIL。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_ROOT = ROOT / "output" / "backtest_results"
EVID = ROOT / "docs" / "evidence"
CST = timezone(timedelta(hours=8))

# 与 scripts/baseline_rerun_20260827.py 完全一致的三策略（含 extra 参数）
STRATS = [
    ("etf_theme", "quantstudio/backtest/strategies/etf_theme_rotation_quantstudio.py", []),
    ("smallcap", "quantstudio/backtest/strategies/小市值策略ptrade.py", []),
    ("overnight_s7", "quantstudio/backtest/strategies/smallcap_overnight_scalp_7_quantstudio.py",
     ["--profile", "minute-bar-v1"]),
]
WINDOW = ["2026-07-01", "2026-07-31"]

# 数据态探测表（只这四张有 time 列；source_watermark 里其余表不得跑 MAX(time)）
_DATA_PROBE_TABLES = ["stock_minutes", "etf_minutes", "stock_daily", "etf_daily"]


def dir_hash(d: Path) -> str:
    """逐字复用 baseline_rerun_20260827.py 的 dir_hash 口径。"""
    h = hashlib.sha256()
    for p in sorted(q for q in d.rglob("*") if q.is_file()):
        h.update(str(p.relative_to(d)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def file_hashes(d: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(q for q in d.rglob("*") if q.is_file())}


def code_state() -> dict:
    """记录代码态（HEAD + 关键文件 sha256），保证基线可解释、可复用。"""
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True,
                                  text=True, encoding="utf-8").stdout.strip()
        except Exception:
            return ""
    key = {}
    for rel in ("quantstudio/pipeline/qfq_reanchor_engine.py",
                "quantstudio/backtest/backtest_engine.py",
                "quantstudio/pipeline/daemon.py"):
        p = ROOT / rel
        if p.exists():
            key[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"head": git("rev-parse", "--short", "HEAD"),
            "head_subject": git("log", "-1", "--format=%s"),
            "dirty": bool(git("status", "--porcelain")),
            "files": key}


def data_state() -> dict:
    """记录数据态（只读）：四张行情表的水位 + MAX(bar 时间)，供基线可比性判断。

    只取 _DATA_PROBE_TABLES，不遍历 source_watermark 全表——库里还有
    balance_statement 之类**无 time 列**的表（用 update_time），对它们跑
    MAX(time) 会 BinderException（2026-11-12 04:17 首跑即踩此坑）。
    逐表 try/except：单表失败不影响其余（缺口写入 errors，不静默）。
    """
    out, errs = {}, {}
    try:
        import duckdb
        con = duckdb.connect(str(ROOT / "data" / "quantstudio.db"), read_only=True)
    except Exception as e:      # noqa: BLE001  库被占/其他 ⇒ 记不可用，不阻断
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        for t in _DATA_PROBE_TABLES:
            try:
                wm = con.execute(
                    "SELECT last_date FROM source_watermark WHERE table_name=? AND source='mcp'",
                    [t]).fetchone()
                mx = con.execute(f"SELECT MAX(time) FROM {t}").fetchone()[0]
                out[t] = {
                    "watermark": (datetime.fromtimestamp(int(wm[0]) / 1000, CST).strftime("%Y-%m-%d")
                                  if wm and wm[0] else None),
                    "max": (datetime.fromtimestamp(mx / 1000, CST).strftime("%Y-%m-%d")
                            if mx else None),
                }
            except Exception as e:      # noqa: BLE001  单表失败留痕，不中断
                errs[t] = f"{type(e).__name__}: {str(e)[:100]}"
    finally:
        con.close()
    return {"ok": True, "tables": out, "errors": errs}


def daemon_alive() -> list:
    """探测 daemon 进程（双形态：路径形态 daemon.py / 模块形态 -m quantstudio.pipeline.daemon）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'quantstudio[./\\\\]pipeline[./\\\\]daemon' } | "
             "ForEach-Object { $_.ProcessId.ToString() }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
    except Exception:      # noqa: BLE001
        return []


def run_one(tag: str, strat: str, extra: list, logdir: Path) -> Path | None:
    """跑一次回测，返回**新增**的产物目录（跑前快照集合做差，避免选错）。

    ⚠ 必须带 --no-coexistence：run_ptrade_strategy 默认 coexistence=True，会主动请求
    daemon 优雅退出以独占库读（2026-11-12 04:12 实测：reason=backtest 致第五次以后的
    又一次 daemon 停摆）。V8 只做只读回测，不需要独占，故显式禁用。
    前置：须在 daemon 停止或无写锁时运行（否则读锁冲突会失败）。
    """
    before = {p.name for p in RESULT_ROOT.iterdir() if p.is_dir()} if RESULT_ROOT.exists() else set()
    cmd = [sys.executable, "-m", "quantstudio.backtest.run_ptrade_strategy",
           strat, *WINDOW, *extra, "--no-coexistence"]
    log = logdir / f"{tag}.log"
    logdir.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        print(f"    cmd: {' '.join(cmd[1:])}")
        rc = subprocess.call(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    after = {p.name for p in RESULT_ROOT.iterdir() if p.is_dir()}
    new = sorted(after - before)
    if rc != 0:
        print(f"    ❌ exit={rc}  日志: {log}")
        return None
    if not new:
        print(f"    ⚠ 未发现新增产物目录（可能落在既有目录），日志: {log}")
        return None
    d = RESULT_ROOT / new[-1]
    print(f"    ✅ 产物: {d.name}")
    return d


def capture(baseline_path: Path) -> int:
    logdir = ROOT / "output" / "v8_baseline_logs" / datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    print(f"=== V8 基线捕获  {datetime.now(CST):%Y-%m-%d %H:%M:%S} ===")
    print(f"窗口: {' ~ '.join(WINDOW)}   策略数: {len(STRATS)}")
    cs, ds = code_state(), data_state()
    print(f"代码态: HEAD={cs['head']} dirty={cs['dirty']}")
    print(f"数据态: ok={ds['ok']}")
    per = {}
    overall = hashlib.sha256()
    for tag, strat, extra in STRATS:
        print(f"\n  ── {tag} ──")
        d = run_one(tag, strat, extra, logdir)
        if d is None:
            print(f"  ❌ {tag} 失败 ⇒ 基线捕获中止（不留半份基线）")
            return 2
        dh = dir_hash(d)
        per[tag] = {"artifact_dir": d.name, "strategy": strat, "extra": extra,
                    "dir_hash": dh, "files": file_hashes(d)}
        overall.update(tag.encode())
        overall.update(dh.encode())
        print(f"    dir_hash = {dh}")
    payload = {
        "kind": "v8-settle-baseline",
        "captured_at": datetime.now(CST).isoformat(timespec="seconds"),
        "window": WINDOW,
        "hash_algo": "sha256(rel_path + bytes) over all files in artifact dir "
                     "(verbatim from scripts/baseline_rerun_20260827.py:dir_hash)",
        "code_state": cs,
        "data_state": ds,
        "strategies": per,
        "overall_hash": overall.hexdigest(),
        "logs_dir": str(logdir.relative_to(ROOT)),
    }
    EVID.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 基线已落盘: {baseline_path}")
    print(f"   overall_hash = {payload['overall_hash']}")
    return 0


def compare(baseline_path: Path) -> int:
    if not baseline_path.exists():
        print(f"❌ 基线不存在: {baseline_path}")
        return 2
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    logdir = ROOT / "output" / "v8_verify_logs" / datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    print(f"=== V8 冻结前后比对  基线 {base['captured_at']} ===")
    cs = code_state()
    print(f"当前代码态: HEAD={cs['head']}  (基线 HEAD={base['code_state']['head']})")
    if cs["dirty"]:
        print("  ⚠ 工作树有未提交改动（V8 比对对象即当前工作树）")
    overall = hashlib.sha256()
    fails = []
    for tag, strat, extra in STRATS:
        print(f"\n  ── {tag} ──")
        d = run_one(tag, strat, extra, logdir)
        if d is None:
            print(f"  ❌ {tag} 运行失败 ⇒ V8 判 FAIL")
            fails.append((tag, "run-failed"))
            continue
        dh = dir_hash(d)
        exp = base["strategies"].get(tag, {}).get("dir_hash")
        ok = dh == exp
        print(f"    now={dh[:32]}")
        print(f"    exp={str(exp)[:32]}")
        print(f"    ⇒ {'✅ 一致' if ok else '❌ **不一致**（V8 失败 ⇒ 按方案 R2 立即回退）'}")
        overall.update(tag.encode())
        overall.update(dh.encode())
        if not ok:
            # 逐文件定位差异
            cur = file_hashes(d)
            old = base["strategies"][tag].get("files", {})
            for n in sorted(set(cur) | set(old)):
                if cur.get(n) != old.get(n):
                    print(f"      差异文件: {n}  now={str(cur.get(n))[:16]} exp={str(old.get(n))[:16]}")
            fails.append((tag, "hash-mismatch"))
    print(f"\n本次 overall_hash = {overall.hexdigest()}")
    print(f"基线 overall_hash = {base['overall_hash']}")
    if fails or overall.hexdigest() != base["overall_hash"]:
        print(f"\n❌ V8 FAIL: {fails}")
        return 1
    print("\n✅ V8 PASS：冻结前后回测产物逐位一致")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="V8 回测哈希基线捕获/比对")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capture", action="store_true", help="捕获基线")
    g.add_argument("--compare", action="store_true", help="与基线比对")
    ap.add_argument("--baseline", default=None, help="基线 json 路径（compare 用）")
    a = ap.parse_args()
    default_bl = EVID / f"v8-settle-baseline-{datetime.now(CST):%Y%m%d}.json"
    bl = Path(a.baseline) if a.baseline else default_bl
    return capture(bl) if a.capture else compare(bl)


if __name__ == "__main__":
    sys.exit(main())
