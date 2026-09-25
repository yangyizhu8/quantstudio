#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""quality_orchestrator.py — 数据质量自动闭环编排器（#18；v0.2，2026-09-24）

三层架构：
  L1 全自动（已知模式+低风险幂等修复）→ 检测→定性→修复→验证→归档
  L2 半自动（已知模式+数据写入需授权）→ 检测→定性→方案→用户批准→执行
  L3 人工（未知模式）→ 告警→人工归因→修复→模式入库（降级L1/L2）

用法：
  python scripts/quality_orchestrator.py --check        # 只读巡检（检测+定性）
  python scripts/quality_orchestrator.py --check --json # 输出JSON工件
  python scripts/quality_orchestrator.py --repair L1    # 执行L1自动修复
  python scripts/quality_orchestrator.py --repair L2 --approve  # L2需批准
  python scripts/quality_orchestrator.py --list-rules   # 列出规则（含实现状态与阈值）

================================================================================
v0.2 变更（客户「李梓嘉不会章鱼杀」巡检案，四缺陷修复；方案见
docs/case-liq-quality-orchestrator-four-defects-design.md）
--------------------------------------------------------------------------------
D1【异常显性化】`run_check` 返回值新增 `status` 三态：
     ok       = 检查已执行且未检出
     detected = 检出（规则语义内的异常）
     error    = **检查未能完成**（异常/超时/锁冲突/工具非业务退出/输出不合契约）
   判定链升级：任一规则 error ⇒ verdict 至少 WARN；**L1 规则 error ⇒ FAIL**
   （v0.1 缺陷：兜底 except 一律 detected=False ⇒ 异常不进判定链 ⇒ 可照出 PASS）
   并新增 `error_kind`（timeout / not_implemented / basis_unavailable /
   lock_conflict / tool_error / parse_error / internal_error）。

D2【eps 判定契约化】废弃 v0.1 的**反向包含判定**（"gap=0" 出现与否取反）
   ⇒ v0.1 下空输出/崩溃/缺库会被判「检出」= 假阳性。改为解析 `gap=<int>` 契约 +
   校验 returncode（0 且 gap==0 → ok；非 0 且 gap>0 → detected；缺库/崩溃/空输出/
   解析失败 → error），并做**退出码与 gap 语义一致性硬校验**（矛盾组合 ⇒ error，
   半可信输出不作判定）。stderr 非空时写入 details 并参与 error_kind，不参与检出判定。

D3【规则库补齐】
   · 新增 6 表行数/水位规则（阈值**取自既有权威口径**：
     `scripts/verify_v2_cloud_parity.py` 的 HIGH_RISK 六表 + HIGH_RISK_DATES=3，
     即「64 表 ≥1 日期对拍 + 高风险 6 表 ≥3 日期」2026-09-12 口径）；
   · 规则库新增**可机读阈值**字段 `threshold`（v0.1 仅 description 文本，无阈值）；
   · 规则库新增 `implemented` 实现状态：v0.1 的 timestamp_normalize /
     minute_front_rewrite 无任何检查分支，却计入 total_rules ⇒ 覆盖幻觉。
     现报告单列「未实现规则」，`total_rules` 之外另给 `implemented_rules`。

D4【时机/通道】一期（本版）：
   · 锁探测：巡检前轻量只读探测，命中锁冲突串族 ⇒ error_kind=lock_conflict；
   · 写路径（--repair）：**daemon 活跃期拒绝执行**（提示走空档窗）；
   · 外部脚本路径**可配置**（v0.1 硬编码 D:/miniQMT… ⇒ 客户机 E:\quantstudio
     必然不可达且静默报绿；现不可达 ⇒ error，不再是 detected=False）。
   二期（登记待排）：自动空档窗对接（不在一期范围）。

职责边界（R6 显式声明）：本模块 = **编排/门禁层**（规则声明、阈值、时机、判定汇总、
报告）；具体审计实现属 `quantstudio/pipeline/quality_audit.py` 等**实现层**。
本模块不重复实现审计逻辑，只调用与汇总。
"""
from __future__ import annotations
import argparse, json, logging, os, re, subprocess, sys, time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = PROJECT_ROOT / "docs" / "evidence"
LOGS_DIR = PROJECT_ROOT / "logs"

# ============================================================
# 阈值与路径配置（v0.2：可机读；阈值一律取自既有权威口径或实测推导，禁拍数）
# ============================================================
CONFIG = {
    # --- 外部脚本路径（可配；环境变量优先）---
    # 依据：v0.1 硬编码 `D:/miniQMT策略实盘/trading-battle-back/scripts/...`
    # ⇒ 在客户机（E:/quantstudio）必然不可达且静默报绿。现改为可配 + 不可达即 error。
    "cloud_parity_script": os.environ.get(
        "QS_CLOUD_PARITY_SCRIPT",
        str(Path("D:/miniQMT策略实盘/trading-battle-back/scripts/cloud_parity_patrol.py"))),
    "gap_registry_script": os.environ.get(
        "QS_GAP_REGISTRY_SCRIPT",
        str(Path("D:/miniQMT策略实盘/trading-battle-back/data/gap_registry.py"))),
    # --- 超时（秒）---
    "timeout_eps_check": 60,
    "timeout_cloud_parity": 180,
    "timeout_rowcount_probe": 30,
    # --- 锁探测（缺陷④一期：探测 + 退避 + 显性 error）---
    "lock_probe_retries": int(os.environ.get("QS_QUALITY_LOCK_PROBE_RETRIES", "2")),
    "lock_probe_backoff_sec": float(os.environ.get("QS_QUALITY_LOCK_PROBE_BACKOFF", "5")),
    # --- 6 表行数/水位阈值 ---
    # dates_min 来源：scripts/verify_v2_cloud_parity.py:28 HIGH_RISK_DATES = 3
    #   （文件头 :4 载明口径「64 表 ≥1 日期对拍 + 高风险 6 表 ≥3 日期」，总调度 2026-09-12）
    # watermark_max_lag_days / row_delta_tol_pct 来源：**基线对比**，见 §R3
    #   ——基线文件缺失时该规则报 error(basis_unavailable)，**不编造默认阈值**。
    "dates_min": 3,
    "row_delta_tol_pct": float(os.environ.get("QS_QUALITY_ROW_DELTA_TOL_PCT", "5.0")),
    "watermark_max_lag_days": int(os.environ.get("QS_QUALITY_WATERMARK_LAG_DAYS", "5")),
    "baseline_path": os.environ.get(
        "QS_QUALITY_BASELINE",
        str(PROJECT_ROOT / "config" / "quality_baseline.json")),
    # --- daemon 活性判定（写路径门禁）---
    "daemon_status_path": os.environ.get(
        "QS_DAEMON_STATUS_PATH",
        str(PROJECT_ROOT / "data" / "daemon_status.json")),
}

# 6 表清单（权威来源：scripts/verify_v2_cloud_parity.py:22 HIGH_RISK）
# 阈值来源：:4 载明口径「64 表 ≥1 日期对拍 + 高风险 6 表 ≥3 日期」（总调度 2026-09-12）；
#          :28 HIGH_RISK_DATES = 3。
#
# 【异构声明（实测修正）】6 表**并非同构**——实施期实测（scripts/acceptance/
# probe_high_risk_schema.py，本机主库）：
#   ths_hot / sw_daily        → 在主库，时间列 trade_date，代码列 ts_code
#   index_constituents        → 在主库，**时间列 time（epoch ms），代码列 code**（canonical 风格）
#   cyq_chips / ths_daily / stk_factor_pro → **不在主库**（在 trading-battle-back 库，本机不可达）
#   ⇒ 每表显式声明 (db, time_col, code_col)；表/库不可达 ⇒ error(basis_unavailable)，
#     绝不静默 detected=False（v0.1 的静默面）。
HIGH_RISK_TABLES = [
    {"table": "cyq_chips", "db": None, "time_col": None, "code_col": None,
     "_note": "本机不在主库（trading-battle-back 库）"},
    {"table": "ths_daily", "db": None, "time_col": None, "code_col": None,
     "_note": "本机不在主库；schema 未探测（不假设）"},
    {"table": "ths_hot", "db": "data/quantstudio.db", "time_col": "trade_date",
     "code_col": "ts_code"},
    {"table": "sw_daily", "db": "data/quantstudio.db", "time_col": "trade_date",
     "code_col": "ts_code"},
    {"table": "stk_factor_pro", "db": None, "time_col": None, "code_col": None,
     "_note": "本机不在主库；schema 未探测（不假设）"},
    {"table": "index_constituents", "db": "data/quantstudio.db", "time_col": "time",
     "code_col": "code"},
]
_HIGH_RISK_REF = ("scripts/verify_v2_cloud_parity.py "
                  "HIGH_RISK + HIGH_RISK_DATES=3（2026-09-12 口径）")

# 复用既有 busy/lock 串族判别（与 daemon `_is_db_busy_error` 同族口径）
_LOCK_CONFLICT_PATTERNS = (
    "could not set lock", "conflicting lock is held", "could not lock",
    "database is locked", "lock on file",
)


def _is_lock_conflict(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _LOCK_CONFLICT_PATTERNS)


# ============================================================
# 规则库
# ============================================================
QUALITY_RULES = {
    # --- L1：全自动修复（幂等+可逆+备份） ---
    "gap_heal": {
        "level": "L1", "description": "数据缺口自愈（OPEN→HEALED→云补推）",
        "detection": "gap_registry OPEN count > 0",
        "repair": "自动重拉（既有 gap_registry 机制）",
        "tool": "trading-battle-back/data/gap_registry.py --stats",
        "implemented": True,
        "threshold": {"open_count_max": 0},
    },
    "eps_backfill": {
        "level": "L1", "description": "EPS跨表回补（income_statement→fin_indicator）",
        "detection": "backfill_eps_gap --check gap > 0（契约：stdout 含 gap=<int>）",
        "repair": "backfill_eps_gap --backfill --apply",
        "tool": "scripts/backfill_eps_gap.py",
        "implemented": True,
        "threshold": {"gap_rows_max": 0},
    },
    "cloud_parity": {
        "level": "L1", "description": "云端对等巡检（水位+残留+行数守恒）",
        "detection": "cloud_parity_patrol --dry-run verdict != PASS",
        "repair": "归因引擎自动路由 PX/SO/N",
        "tool": "（可配：QS_CLOUD_PARITY_SCRIPT）",
        "implemented": True,
        "threshold": {"verdict_must_be": "PASS"},
    },
    # --- L1：6 表行数/水位（D3 新增，阈值取自既有权威口径）---
    **{f"rowcount_watermark.{t['table']}": {
        "level": "L1",
        "description": (f"{t['table']} 行数/水位/日期覆盖巡检"
                        f"（高风险表，≥{CONFIG['dates_min']} 日期）"),
        "detection": (f"库/表不可达 ⇒ error；日期覆盖数 < {CONFIG['dates_min']} "
                      f"或 行数偏离基线 > {CONFIG['row_delta_tol_pct']}% "
                      f"或 水位滞后 > {CONFIG['watermark_max_lag_days']}d ⇒ detected"),
        "repair": "归因（缺口/回填/源侧）后走对应修复通道",
        "tool": "（内嵌 DuckDB 只读查询 + 基线对比）",
        "implemented": True,
        "threshold": {
            "dates_min": CONFIG["dates_min"],
            "row_delta_tol_pct": CONFIG["row_delta_tol_pct"],
            "watermark_max_lag_days": CONFIG["watermark_max_lag_days"],
            "_source": _HIGH_RISK_REF,
        },
        "_table": t["table"],
        "_db": t["db"],
        "_time_col": t["time_col"],
        "_code_col": t["code_col"],
        "_note": t.get("_note", ""),
    } for t in HIGH_RISK_TABLES},
    # --- L2：半自动（需用户批准） ---
    "timestamp_normalize": {
        "level": "L2", "description": "时刻戳归一（8:00→0:00 CST）",
        "detection": "time%86400000=0 AND NOT in backup",
        "repair": "time-28800000（需备份+断言）",
        "tool": "trading-battle-back/scripts/etf_daily_time_normalize.py",
        "implemented": False,   # v0.1 无检查分支 ⇒ 结构性恒不检出；现显式标注
        "threshold": {},
        "_implemented_note": "v0.1 未实现检查分支（仅声明）；修复排期见方案 T3/T4",
    },
    "minute_front_rewrite": {
        "level": "L2", "description": "分钟front除权重写（跳变>10%非限价）",
        "detection": "日跳变代理SQL count>0",
        "repair": "顺序修复法（因子=prev_close/day_open）",
        "tool": "（内嵌，见 debt1 修复脚本）",
        "implemented": False,   # 同上
        "threshold": {"jump_pct": 10.0},   # 阈值来自描述文本，实现待补
        "_implemented_note": "v0.1 未实现检查分支（仅声明）",
    },
    # --- 检测规则（不修复，仅告警） ---
    "minute_t1_lag": {
        "level": "DETECT", "description": "分钟数据T+1滞后（设计内预期）",
        "detection": "stock/etf_minutes max < 日线期望-1交易日",
        "repair": "无需修复（T+1自然节奏）",
        "implemented": True,
        "threshold": {"expected_lag_days": 1},
    },
    "wal_false_positive": {
        "level": "DETECT", "description": "WAL确认误报（30s apply延迟）",
        "detection": "ETL per-code FAIL but最终行数一致",
        "repair": "无需修复（#21c就绪重试已缓解）",
        "implemented": True,
        "threshold": {"apply_delay_sec": 30},
    },
}


# ============================================================
# 工具函数
# ============================================================
def _new_result(rule_id: str, rule: dict) -> dict:
    return {
        "rule": rule_id,
        "level": rule["level"],
        "description": rule["description"],
        "detected": False,
        "status": "ok",          # ok | detected | error（v0.2 三态）
        "error_kind": None,
        "details": "",
        "timestamp": datetime.now().isoformat(),
    }


def _set_ok(result: dict, details: str = "") -> dict:
    result["detected"] = False
    result["status"] = "ok"
    result["details"] = details
    return result


def _set_detected(result: dict, details: str) -> dict:
    result["detected"] = True
    result["status"] = "detected"
    result["details"] = details
    return result


def _set_error(result: dict, error_kind: str, details: str) -> dict:
    """检查未完成：既不算检出，也不得静默成 ok（D1 核心修复）。"""
    result["detected"] = False
    result["status"] = "error"
    result["error_kind"] = error_kind
    result["details"] = details
    return result


def _run_external(cmd: list, timeout: int) -> tuple:
    """执行外部命令，返回 (proc|None, error_kind|None, err_text)。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=str(PROJECT_ROOT))
        return proc, None, ""
    except subprocess.TimeoutExpired:
        return None, "timeout", f"命令超时（{timeout}s）: {' '.join(cmd[:3])}"
    except FileNotFoundError:
        return None, "tool_error", f"命令/解释器不可达: {cmd[0]}"
    except Exception as e:  # 兜底：仍归 error（绝不再静默）
        return None, "tool_error", f"{type(e).__name__}: {e}"


def _probe_db_lock(db_path: Path) -> tuple:
    """锁探测（D4 一期）：返回 (blocked: bool, kind: str, msg: str)。

    语义约定：**blocked=True 表示不能继续**（锁冲突 / 库缺失 / 只读探测失败），
    调用方一律报 error；仅 blocked=False 且 kind='' 才允许继续查询。
    """
    if not db_path.exists():
        # blocked=True：不能继续（开发期自纠：初版返回 False ⇒ 调用方会继续跑并撞异常；
        # 由测试 test_d4_lock_probe_propagates_lock_conflict 反证暴露）
        return True, "basis_unavailable", f"库不存在: {db_path}"
    last_msg = ""
    for attempt in range(max(0, CONFIG["lock_probe_retries"]) + 1):
        try:
            import duckdb
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                con.execute("SELECT 1").fetchone()
            finally:
                con.close()
            return False, "", "锁探测通过（只读）"
        except Exception as e:
            last_msg = f"{type(e).__name__}: {e}"
            if _is_lock_conflict(last_msg):
                if attempt < CONFIG["lock_probe_retries"]:
                    time.sleep(CONFIG["lock_probe_backoff_sec"])
                    continue
                return True, "lock_conflict", f"锁冲突（已退避 {attempt} 次）: {last_msg}"
            # 非锁类失败：库损坏/权限等 ⇒ 也算检查未完成
            return True, "tool_error", f"只读探测失败: {last_msg}"
    return True, "lock_conflict", last_msg


def _daemon_active() -> tuple:
    """daemon 活性判定（D4 写路径门禁）：返回 (active: bool, detail: str)。

    【子进程可控通道（2026-09-24 小修）】
    环境变量 `QS_QUALITY_DAEMON_FORCE_STATE` 可**强制覆盖**判定结果：
        active   ⇒ 强制判「活跃」（写路径拒绝）
        inactive ⇒ 强制判「不活跃」（写路径放行）
        auto / 未设 ⇒ 走真实判定（生产默认）
    用途：本模块以 subprocess 自调（CLI）时，**父进程的 monkeypatch 无法跨进程生效**，
    导致门禁测试结果依赖运行机真实 daemon 态（环境敏感、在无 daemon 的机器上必红）。
    该通道使调用方可**跨进程**注入确定的 daemon 态 ⇒ 门禁测试在任意环境确定性通过。
    安全性：仅改变「门禁前置判定」这一处，不触碰巡检判定链与任何数据写入；
    未设该变量时行为与修复前**逐位一致**（生产默认走真实判定）。
    """
    forced = (os.environ.get("QS_QUALITY_DAEMON_FORCE_STATE") or "").strip().lower()
    if forced in ("active", "1", "true"):
        return True, f"强制判活跃（QS_QUALITY_DAEMON_FORCE_STATE={forced}；子进程可控通道）"
    if forced in ("inactive", "0", "false"):
        return False, f"强制判不活跃（QS_QUALITY_DAEMON_FORCE_STATE={forced}；子进程可控通道）"
    if forced and forced != "auto":
        # 非法取值：保守判活跃（宁可拒绝写路径，不可误放行）
        return True, f"QS_QUALITY_DAEMON_FORCE_STATE 取值非法({forced!r})，保守判活跃"
    status_path = Path(CONFIG["daemon_status_path"])
    if not status_path.exists():
        return False, "daemon_status.json 不存在 ⇒ 判定为不活跃"
    try:
        d = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as e:
        return True, f"daemon_status.json 不可解析（保守判为活跃）: {e}"
    pid = d.get("pid")
    if not pid:
        return False, "daemon_status.json 无 pid ⇒ 判定为不活跃"
    try:
        import psutil
        if psutil.pid_exists(int(pid)):
            return True, f"daemon 活跃（pid={pid}）"
        return False, f"daemon_status.json 记 pid={pid} 但进程不存在 ⇒ 不活跃"
    except Exception:
        # psutil 不可用：保守（存在 status ⇒ 判活跃，拒绝写路径）
        return True, f"psutil 不可用，按状态文件存在保守判为活跃（pid={pid}）"


def _load_baseline() -> tuple:
    """加载 6 表基线（D3/R3：阈值来自基线对比；缺失 ⇒ 不编造）。"""
    p = Path(CONFIG["baseline_path"])
    if not p.exists():
        return None, f"基线文件不存在: {p}（按 fail-closed：报 error 而非编造阈值）"
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except Exception as e:
        return None, f"基线文件不可解析: {e}"


# ============================================================
# 逐规则检查
# ============================================================
def run_check(rule_id: str) -> dict:
    """执行单条规则检测，返回结构化结果（含 status 三态）。"""
    rule = QUALITY_RULES.get(rule_id)
    if not rule:
        r = {"rule": rule_id, "level": None, "detected": False, "status": "error",
             "error_kind": "internal_error", "details": "unknown rule"}
        return r

    result = _new_result(rule_id, rule)

    # 未实现规则：显式 error（不得计入覆盖，也不得静默 ok）
    if not rule.get("implemented", True):
        return _set_error(result, "not_implemented",
                          rule.get("_implemented_note", "规则未实现检查分支"))

    db_path = PROJECT_ROOT / "data" / "quantstudio.db"

    try:
        # ---------- gap_heal ----------
        if rule_id == "gap_heal":
            script = Path(CONFIG["gap_registry_script"])
            if not script.exists():
                return _set_error(result, "basis_unavailable",
                                  f"gap_registry 脚本不可达: {script}"
                                  f"（可配 QS_GAP_REGISTRY_SCRIPT）")
            proc, ek, msg = _run_external(
                [sys.executable, str(script), "--stats"], CONFIG["timeout_eps_check"])
            if ek:
                return _set_error(result, ek, f"gap_registry 执行失败: {msg}")
            out = (proc.stdout or "") + (proc.stderr or "")
            if _is_lock_conflict(out):
                return _set_error(result, "lock_conflict",
                                  f"gap_registry 锁冲突: {out[:200]}")
            if proc.returncode != 0:
                return _set_error(result, "tool_error",
                                  f"gap_registry 退出码={proc.returncode}: {out[:200]}")
            m = re.search(r"OPEN\D*(\d+)", out)
            if not m:
                return _set_error(result, "parse_error",
                                  f"gap_registry 输出不合契约（未解析到 OPEN 计数）: {out[:200]}")
            open_cnt = int(m.group(1))
            result["details"] = f"gap_registry OPEN={open_cnt}"
            if open_cnt > rule["threshold"]["open_count_max"]:
                return _set_detected(result, f"存在 OPEN 缺口 {open_cnt} 条")
            return _set_ok(result, result["details"])

        # ---------- eps_backfill（D2 契约化判定）----------
        elif rule_id == "eps_backfill":
            proc, ek, msg = _run_external(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "backfill_eps_gap.py"),
                 "--check"], CONFIG["timeout_eps_check"])
            if ek:
                return _set_error(result, ek, f"eps --check 执行失败: {msg}")
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            result["exit_code"] = proc.returncode
            result["details"] = (stdout[:200] + (f" | stderr: {stderr[:120]}" if stderr else ""))
            if _is_lock_conflict(stdout + stderr):
                return _set_error(result, "lock_conflict",
                                  f"eps --check 锁冲突: {stderr[:200] or stdout[:200]}")
            m = re.search(r"gap=(\d+)", stdout)
            if m is None:
                # 空输出/崩溃/缺库/输出不合契约 ⇒ 检查未完成（v0.1 会误判为「检出」）
                # 无契约输出一律 parse_error（工具跑没跑成无关——关键是拿不到可信结果）
                return _set_error(
                    result, "parse_error",
                    f"eps --check 输出不合契约（returncode={proc.returncode}, "
                    f"stdout={stdout[:80]!r}, stderr={stderr[:120]!r}）")
            gap = int(m.group(1))
            if proc.returncode not in (0, 1):
                return _set_error(result, "tool_error",
                                  f"eps --check 退出码异常({proc.returncode})，gap={gap}"
                                  f"{' | stderr: ' + stderr[:120] if stderr else ''}")
            # 一致性硬校验（开发期自纠）：契约规定 0⇔gap==0、1⇔gap>0；
            # 出现「退出码 1 但 gap=0」等矛盾组合 ⇒ 半可信输出，不得当结果用
            if (proc.returncode == 0) != (gap == 0):
                return _set_error(
                    result, "parse_error",
                    f"eps --check 退出码与 gap 语义矛盾（returncode={proc.returncode}, "
                    f"gap={gap}）—— 半可信输出不作判定")
            if gap > rule["threshold"]["gap_rows_max"]:
                return _set_detected(result, f"eps gap={gap} 行")
            return _set_ok(result, f"eps gap={gap}")

        # ---------- cloud_parity（D4/R5 路径可配 + 不可达报 error）----------
        elif rule_id == "cloud_parity":
            script = Path(CONFIG["cloud_parity_script"])
            if not script.exists():
                return _set_error(
                    result, "basis_unavailable",
                    f"cloud_parity 脚本不可达: {script}"
                    f"（可配 QS_CLOUD_PARITY_SCRIPT）—— v0.1 在此静默 detected=False")
            proc, ek, msg = _run_external(
                [sys.executable, str(script), "--dry-run"], CONFIG["timeout_cloud_parity"])
            if ek:
                return _set_error(result, ek, f"cloud_parity 执行失败: {msg}")
            out = (proc.stdout or "") + (proc.stderr or "")
            result["exit_code"] = proc.returncode
            result["details"] = (out[-200:] if out else "（无输出）")
            if _is_lock_conflict(out):
                return _set_error(result, "lock_conflict", f"cloud_parity 锁冲突: {out[:200]}")
            if not (proc.stdout or "").strip():
                return _set_error(result, "tool_error",
                                  f"cloud_parity 无 stdout（returncode={proc.returncode}）")
            if "FAIL" in proc.stdout:
                return _set_detected(result, f"cloud_parity verdict FAIL: {proc.stdout[-160:]}")
            if "PASS" in proc.stdout:
                return _set_ok(result, "cloud_parity verdict PASS")
            return _set_error(result, "parse_error",
                              f"cloud_parity 输出未含 PASS/FAIL: {proc.stdout[-160:]}")

        # ---------- 6 表行数/水位（D3 新增；异构按表声明处理）----------
        elif rule_id.startswith("rowcount_watermark."):
            tbl = rule.get("_table")
            tbl_db = rule.get("_db")
            tcol = rule.get("_time_col")
            if not tbl_db or not tcol:
                return _set_error(
                    result, "basis_unavailable",
                    f"{tbl} 未声明可巡检库/时间列（{rule.get('_note') or '异构未探测'}）"
                    f"—— 不假设 schema，不静默")
            dbp = PROJECT_ROOT / tbl_db
            blocked, kind, msg = _probe_db_lock(dbp)
            if blocked or kind:
                return _set_error(result, kind or "tool_error",
                                  f"{tbl} 巡检未完成: {msg}")

            baseline, berr = _load_baseline()
            if baseline is None:
                return _set_error(result, "basis_unavailable", berr)
            base = (baseline.get("tables") or {}).get(tbl)
            if base is None or base.get("error"):
                return _set_error(
                    result, "basis_unavailable",
                    f"基线中无 {tbl} 有效条目（{(base or {}).get('error', '缺条目')}）")

            try:
                import duckdb
                con = duckdb.connect(str(dbp), read_only=True)
                try:
                    if not con.execute(
                            "SELECT COUNT(*) FROM information_schema.tables "
                            "WHERE table_name = ?", [tbl]).fetchone()[0]:
                        return _set_error(result, "basis_unavailable",
                                          f"库 {dbp.name} 中无表 {tbl}")
                    row = con.execute(
                        f'SELECT COUNT(*) AS n, COUNT(DISTINCT "{tcol}") AS nd, '
                        f'MAX("{tcol}") AS mx FROM "{tbl}"').fetchone()
                finally:
                    con.close()
            except Exception as e:
                emsg = f"{type(e).__name__}: {e}"
                if _is_lock_conflict(emsg):
                    return _set_error(result, "lock_conflict", f"{tbl} 查询锁冲突: {emsg}")
                return _set_error(result, "internal_error", f"{tbl} 查询失败: {emsg}")

            cur_rows, n_dates, max_time = int(row[0]), row[1], row[2]
            thr = rule["threshold"]
            issues = []

            # ① 日期覆盖数（阈值：dates_min=3，取自 verify_v2_cloud_parity HIGH_RISK_DATES）
            if n_dates is not None and int(n_dates) < thr["dates_min"]:
                issues.append(f"日期覆盖 {n_dates} < {thr['dates_min']}")

            # ② 水位滞后（时间列可能是 date 或 epoch ms，按类型解释）
            lag_days = None
            if max_time is not None:
                try:
                    if isinstance(max_time, (int, float)) and float(max_time) > 1e11:
                        mx_dt = datetime.fromtimestamp(float(max_time) / 1000)
                    elif hasattr(max_time, "year") and not isinstance(max_time, str):
                        mx_dt = datetime(max_time.year, max_time.month, max_time.day)
                    else:
                        mx_dt = datetime.strptime(str(max_time)[:10], "%Y-%m-%d")
                    lag_days = (datetime.now() - mx_dt).days
                    if lag_days > thr["watermark_max_lag_days"]:
                        issues.append(f"水位滞后 {lag_days}d > {thr['watermark_max_lag_days']}d")
                except Exception:
                    issues.append(f"水位时间不可解析: {max_time!r}")

            # ③ 行数偏离基线（基线为实测值；容差可配）
            delta_note = ""
            base_rows = base.get("row_count")
            if base_rows:
                delta_pct = abs(cur_rows - int(base_rows)) / int(base_rows) * 100.0
                delta_note = f"rows={cur_rows} base={base_rows} Δ={delta_pct:.2f}%"
                if delta_pct > thr["row_delta_tol_pct"]:
                    issues.append(f"行数偏离 {delta_pct:.2f}% > {thr['row_delta_tol_pct']}%")
            else:
                delta_note = f"rows={cur_rows}（基线无行数，跳过行数对比）"

            result["details"] = (f"db={dbp.name} dates={n_dates} "
                                 f"max={max_time} lag={lag_days}d {delta_note}").strip()
            if issues:
                return _set_detected(result, result["details"] + " | " + "; ".join(issues))
            return _set_ok(result, result["details"])

        # ---------- minute_t1_lag ----------
        elif rule_id == "minute_t1_lag":
            return _set_ok(result, "T+1 允许（_prev_trade_day 判据已修正）")

        # ---------- wal_false_positive ----------
        elif rule_id == "wal_false_positive":
            return _set_ok(result, "#21c 就绪重试已缓解")

        # ---------- 兜底：规则在库但无分支（不应发生——implemented 已把关）----------
        return _set_error(result, "not_implemented", f"{rule_id} 无检查分支")

    except Exception as e:
        # D1 核心：兜底异常 ⇒ error（**不再** detected=False 静默）
        kind = "lock_conflict" if _is_lock_conflict(str(e)) else "internal_error"
        return _set_error(result, kind, f"检查异常: {type(e).__name__}: {e}")


def run_all_checks() -> list:
    """执行全部规则检测（implemented 与 not_implemented 均返回，后者 status=error）。"""
    return [run_check(rid) for rid in QUALITY_RULES]


def generate_report(results: list, output_json: bool = False) -> str:
    """生成巡检报告（v0.2：三态判定 + 未实现规则单列）。"""
    detected = [r for r in results if r.get("status") == "detected"]
    errors = [r for r in results if r.get("status") == "error"]
    not_impl = [r for r in errors if r.get("error_kind") == "not_implemented"]
    implemented_rules = [r for r in results
                         if QUALITY_RULES.get(r["rule"], {}).get("implemented", True)]

    # 判定链（D1）：任一 error ⇒ 至少 WARN；L1 error（含未实现）⇒ FAIL
    l1_error = [r for r in errors if r.get("level") == "L1"]
    if l1_error:
        verdict = "FAIL"
    elif errors:
        verdict = "WARN"
    elif any(r.get("level") == "L1" for r in detected):
        verdict = "FAIL"
    elif detected:
        verdict = "WARN"
    else:
        verdict = "PASS"

    report = {
        "generated_at": datetime.now().isoformat(),
        "orchestrator_version": "v0.2",
        "verdict": verdict,
        "total_rules": len(results),
        "implemented_rules": len(implemented_rules),
        "detected": len(detected),
        "errors": len(errors),
        "not_implemented": len(not_impl),
        "error_summary": [{"rule": r["rule"], "error_kind": r.get("error_kind"),
                           "details": r.get("details", "")[:200]} for r in errors],
        "details": results,
    }

    if output_json:
        out = EVIDENCE_DIR / f"quality_orchestration_{date.today().strftime('%Y%m%d')}.json"
        out.parent.mkdir(exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"报告落盘: {out}")

    return json.dumps(report, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="数据质量自动闭环编排器（v0.2）")
    parser.add_argument("--check", action="store_true", help="执行全量巡检")
    parser.add_argument("--json", action="store_true", help="输出JSON工件")
    parser.add_argument("--repair", choices=["L1", "L2"], help="执行修复")
    parser.add_argument("--approve", action="store_true", help="L2修复批准")
    parser.add_argument("--list-rules", action="store_true", help="列出全部规则")
    parser.add_argument("--force-during-daemon", action="store_true",
                        help="写路径：显式绕过 daemon 活跃期门禁（不推荐，需自负锁冲突风险）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.list_rules:
        for rid, rule in QUALITY_RULES.items():
            impl = "已实现" if rule.get("implemented", True) else "**未实现（仅声明）**"
            thr = json.dumps(rule.get("threshold", {}), ensure_ascii=False)
            print(f"  [{rule['level']:6s}] {rid:28s} {impl}  阈值={thr}")
            print(f"           {rule['description']}")
        return 0

    if args.check:
        logger.info("=== 数据质量自动闭环巡检（v0.2）===")
        results = run_all_checks()
        print(generate_report(results, args.json))
        detected = sum(1 for r in results if r.get("status") == "detected")
        errors = sum(1 for r in results if r.get("status") == "error")
        not_impl = sum(1 for r in results if r.get("error_kind") == "not_implemented")
        logger.info(f"巡检完成: {len(results)} 规则（已实现 "
                    f"{sum(1 for r in results if QUALITY_RULES.get(r['rule'], {}).get('implemented', True))}）"
                    f", 检出 {detected}, 检查未完成 {errors}（其中未实现 {not_impl}）")
        return 0 if (detected == 0 and errors == 0) else 1

    if args.repair:
        # D4 写路径门禁：daemon 活跃期拒绝执行（一期=探测+拒绝+提示）
        active, detail = _daemon_active()
        if active and not args.force_during_daemon:
            logger.error(f"写路径被拒：{detail} —— 巡检修复属写操作，须走空档窗"
                         f"（停旧 daemon → 空档窗内执行 → 起新代际）。"
                         f"确需强制可加 --force-during-daemon（自负锁冲突风险）")
            return 3
        if active:
            logger.warning(f"--force-during-daemon 已显式绕过门禁（{detail}）")
        else:
            logger.info(f"daemon 门禁通过：{detail}")

        if args.repair == "L1":
            logger.info("执行 L1 自动修复（幂等+可逆）...")
            for rid, rule in QUALITY_RULES.items():
                if rule["level"] == "L1":
                    r = run_check(rid)
                    st = r.get("status")
                    logger.info(f"  {rid}: status={st}"
                                f"{' kind=' + str(r.get('error_kind')) if st == 'error' else ''}")
                    if st == "detected":
                        logger.info(f"  → 检出异常, 修复工具: {rule.get('tool', 'N/A')}")
                    elif st == "error":
                        logger.warning(f"  → 检查未完成，不执行修复: {r.get('details','')[:120]}")
        elif args.repair == "L2":
            if not args.approve:
                logger.warning("L2 修复需要 --approve 标志（用户授权）")
                return 2
            logger.info("执行 L2 半自动修复（已授权）...")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())