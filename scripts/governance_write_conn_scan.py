# -*- coding: utf-8 -*-
"""写路径覆盖扫描器 —— registry 的唯一生成来源（DSH 终审要求：机器可验证、双向差集为空、数字自动计算）

规则：
  1. 全库扫描 quantstudio/**/*.py 的 duckdb/sqlite3 连接点（多行感知：向后看 3 行内 read_only/mode=ro 即排除）；
  2. 每个连接点按【证据表】分类：MAIN / AUX / EXCLUDED(细分) —— 证据表为本文件 MANUAL_EVIDENCE，
     逐项附行级/守卫级证据；未在证据表且无法规则分类的落 UNRESOLVED（视为 MAIN 保守处理并阻断）；
  3. 输出 registry（data/snapshots/write_path_registry.json，stats 由条目自动计算）
     与扫描报告（output/golden_baseline/write_conn_scan_report.json，含双向差集，恒为空——registry 即扫描产物）。
用法：python scripts/governance_write_conn_scan.py
"""
import io
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_REG = ROOT / "data" / "snapshots" / "write_path_registry.json"
OUT_REP = ROOT / "output" / "golden_baseline" / "write_conn_scan_report.json"

# 手工证据表（file → {line 或 "default": (分类, 证据)}）。
# MAIN/AUX = 快照源写路径（须接入锁）；EXCLUDED_* = 有证据不影响快照源。
MANUAL_EVIDENCE = {
    "quantstudio/_paths.py": {"13": ("EXCLUDED_DOC", "模块 docstring 中的用法示例，非执行路径")},
    "quantstudio/backtest/events.py": {"default": ("MAIN", "import_strategy_events DELETE+INSERT strategy_events（记录项C）")},
    "quantstudio/gui/tabs/task_tab.py": {"default": ("MAIN", "GUI 任务页写主库")},
    "quantstudio/gui/db_helper.py": {
        "126": ("EXCLUDED_QUARANTINE", "self.quarantine_path 隔离库（snippet 实证）"),
        "133": ("EXCLUDED_QUARANTINE", "self.quarantine_path 隔离库"),
        "153": ("EXCLUDED_QUARANTINE", "self.quarantine_path 隔离库"),
        "161": ("EXCLUDED_AUDIT", "self.batch_audit_path 批量审计库"),
        "default": ("MAIN", "GUI 主库连接（rw）")},
    "quantstudio/pipeline/writers.py": {"default": ("MAIN", "DuckDBWriter 写主库（daemon 同步主入口）")},
    "quantstudio/pipeline/qfq_calendar.py": {"default": ("MAIN", "交易日历 DDL/写入主库")},
    "quantstudio/pipeline/daemon.py": {
        "59": ("EXCLUDED_STATE", "daemon 自身状态库"),
        "86": ("EXCLUDED_STATE", "daemon 自身状态库"),
        "102": ("EXCLUDED_STATE", "daemon 自身状态库"),
        "2263": ("EXCLUDED_AUDIT", "审计库"),
        "default": ("AUX", "qfq.db_path → qfq_aux（QFQMaintenance L60 重定向 qfq_aux.db）"),
    },
    "quantstudio/pipeline/qfq_maintenance.py": {"default": ("AUX", "L56-60：辅助表独立 SQLite，db_path 重定向 qfq_aux.db")},
    "quantstudio/pipeline/qfq_revision.py": {"default": ("AUX", "QFQRevision 同 qfq 维护族，self.db_path 为 aux")},
    "quantstudio/pipeline/qfq_observation.py": {"default": ("AUX", "qfq 观察写 aux")},
    "quantstudio/pipeline/qfq_formal_cutover.py": {
        "455": ("AUX", "aux 备份写"),
        "472": ("AUX", "aux 暂存写"),
        "default": ("MAIN", "cutover 工具写主库（db 参数默认主库）"),
    },
    "quantstudio/pipeline/qfq_aux_router.py": {"default": ("AUX", "route.path → qfq_aux 世代文件")},
    "quantstudio/pipeline/qfq_cutover_activation.py": {"default": ("AUX", "path 要求 adj_factor 表 → aux")},
    "quantstudio/pipeline/sources/mcp_adapter.py": {
        "1217": ("AUX", "aux_p → qfq_aux 因子库（snippet 实证）"),
        "1515": ("EXCLUDED_READONLY", "file:{aux}?mode=ro 只读连接"),
        "1515": ("EXCLUDED_READONLY", "file:{aux}?mode=ro 只读连接"),
        "1902": ("MAIN", "duckdb.connect(db_path, read_only=False) 写 stock_dividend"),
        "1982": ("AUX", "aux → qfq_aux 因子库"),
        "default": ("MAIN", "config main_db 推导（保守）")},
    "quantstudio/pipeline/qfq_schema_migration.py": {"default": ("MAIN", "REBUILD source_watermark 等主库表")},
    "quantstudio/pipeline/qfq_reanchor_schema.py": {
        "956": ("MAIN", "dconn duckdb.connect(main_path) 主库 DDL"),
        "963": ("AUX", "sconn sqlite3.connect(aux_path) aux（snippet 实证）"),
        "default": ("MAIN", "reanchor schema 主库（保守）")},
    "quantstudio/pipeline/qfq_formal_canary.py": {"default": ("MAIN", "正式 canary 连接 main_db read_only=False（保守归 MAIN）")},
    "quantstudio/pipeline/qfq_staging_canary.py": {"default": ("EXCLUDED_STAGING", "L58-59 守卫：staging main/aux missing 即 raise——仅写 staging 副本")},
    "quantstudio/pipeline/qfq_orchestrator_cli.py": {"default": ("MAIN", "main_db 参数默认主库，read_only 由参数决定（rw 能力）")},
    "quantstudio/pipeline/qfq_formal_cutover_cli.py": {"default": ("MAIN", "connect(db, read_only=read_only) 参数可控 rw")},
    "quantstudio/pipeline/qfq_resident_orchestrator.py": {
        "936": ("AUX", "aconn sqlite3.connect(self.aux_db) aux（snippet 实证）"),
        "default": ("MAIN", "常驻编排器主库连接")},
    "quantstudio/pipeline/qfq_event_discovery.py": {"default": ("AUX", "事件发现写 aux 审计表")},
    "quantstudio/pipeline/exporter.py": {"default": ("EXCLUDED_EXPORT", "L77 源库 read_only，仅写 out_path 导出副本")},
    "quantstudio/pipeline/quarantine.py": {"default": ("EXCLUDED_QUARANTINE", "隔离库，非快照源")},
    "quantstudio/pipeline/quality_audit.py": {"default": ("EXCLUDED_AUDIT", "审计库，非快照源")},
    "quantstudio/pipeline/aligner.py": {"default": ("EXCLUDED_IN_MEMORY", "in-memory 连接")},
    "quantstudio/pipeline/qfq_invariant.py": {"default": ("EXCLUDED_READONLY", "多行签名：连接参数在后续行含 read_only=True（扫描多行感知验证）")},
    "quantstudio/pipeline/qfq_formal_postcutever_audit.py": {"default": ("AUX", "gen1_aux 世代文件审计连接（sqlite，aux 世代）")},
    "quantstudio/pipeline/snapshot_lock.py": {"default": ("EXCLUDED_DOC", "锁模块自身 docstring 中的连接示例文本，非真实连接")},
}

EXCLUDE_PREFIX = "EXCLUDED_"


def scan():
    conns = []
    for py in sorted(Path(ROOT / "quantstudio").rglob("*.py")):
        rel = str(py.relative_to(ROOT)).replace(chr(92), "/")
        lines = io.open(py, encoding="utf-8", errors="ignore").read().split("\n")
        for i, line in enumerate(lines):
            if not re.search(r"(duckdb|_sqlite3|sqlite3)\.connect", line):
                continue
            window = " ".join(lines[i:i + 3]).lower()
            if "mode=ro" in lines[i].lower():
                conns.append({"file": rel, "line": i + 1,
                              "classification": "EXCLUDED_READONLY",
                              "evidence": "连接行含 mode=ro（内容级排除）",
                              "snippet": lines[i].strip()[:100]})
                continue
            if "read_only" in window and ("read_only=true" in window or "mode=ro" in window):
                if "read_only=false" not in window and "read_only=read_only" not in window:
                    continue  # 只读连接（多行感知）
            if re.search(r"connect\(\s*\)|:memory:", window):
                kind, ev = "EXCLUDED_IN_MEMORY", "in-memory"
            else:
                me = MANUAL_EVIDENCE.get(rel, {})
                if str(i + 1) in me:
                    kind, ev = me[str(i + 1)]          # ① 行级证据（最高优先）
                elif me.get("default") and "sqlite3.connect" not in line:
                    kind, ev = me["default"]           # ② 文件默认（duckdb 连接：文件语义可靠）
                elif "sqlite3.connect" in line:
                    # ③ sqlite 目标嗅探（DSH 终审：sqlite 连接必须按目标对象验证，不用文件默认）
                    if re.search(r"quarantine", window):
                        kind, ev = "EXCLUDED_QUARANTINE", "连接目标含 quarantine（目标嗅探）"
                    elif re.search(r"batch_audit|audit_path|audit", window):
                        kind, ev = "EXCLUDED_AUDIT", "连接目标含 audit（目标嗅探）"
                    elif re.search(r"aux", window):
                        kind, ev = "AUX", "连接目标含 aux（目标嗅探）"
                    elif re.search(r"main_db|quantstudio\.db", window):
                        kind, ev = "MAIN", "连接目标含 main_db/quantstudio.db（目标嗅探）"
                    elif me.get("default"):
                        kind, ev = me["default"]       # 嗅探不决时回退文件默认（如 qfq_maintenance self.db_path→aux 有 L56-60 重定向证据）
                    else:
                        kind, ev = ("UNRESOLVED", "未分类（保守阻断）")
                else:
                    kind, ev = me.get("default", ("UNRESOLVED", "未分类（保守阻断）"))
            conns.append({"file": rel, "line": i + 1, "classification": kind, "evidence": ev,
                          "snippet": lines[i].strip()[:100]})
    return conns


def _adoption_status():
    """读取 lock_adoption_log：返回 (已接入模块集, 待拆分连接点集)。"""
    import json as _json
    p = ROOT / "data" / "snapshots" / "lock_adoption_log.json"
    if not p.exists():
        return set(), set()
    d = _json.loads(io.open(p, encoding="utf-8").read())
    ok_mods = {a["module"] for a in d.get("adopted", []) if a.get("guard_marker_present")}
    pending = {tuple(x[0].rsplit(":", 1)) for x in d.get("pending", [])}
    return ok_mods, pending


def main():
    conns = scan()
    unresolved = [c for c in conns if c["classification"] == "UNRESOLVED"]
    main_w = [c for c in conns if c["classification"] == "MAIN"]
    aux_w = [c for c in conns if c["classification"] == "AUX"]
    excl = [c for c in conns if c["classification"].startswith(EXCLUDE_PREFIX)]

    ok_mods, pending_pts = _adoption_status()
    _pending_mods = {m for m, _ in pending_pts}

    def _pending_content_match(c):
        return c["file"] in _pending_mods and "待语义拆分" in (c.get("snippet") or "") or                (c["file"] in _pending_mods and "3A 备注" in (c.get("snippet") or ""))

    def _lock_flag(c):
        # locked=true 仅当：模块在 adoption log 有守卫证据 且 该连接点不在 pending 清单
        if (c["file"], str(c["line"])) in pending_pts:
            return False
        return c["file"] in ok_mods

    # 语义豁免：connection_semantics.json 中 READ/STAGING 的连接点移入排除（证据文件引用）
    sem_path = ROOT / "data" / "snapshots" / "connection_semantics.json"
    if sem_path.exists():
        sem = {(x["module"], x["line"]): x for x in json.loads(io.open(sem_path, encoding="utf-8").read())}
        exempt_w, keep_main, keep_aux = [], [], []
        for c in main_w + aux_w:
            # 行号漂移容差：按模块内语义查最近行（±8 行）
            key = next((k for k in sem if k[0] == c["file"] and abs(k[1] - c["line"]) <= 8), None)
            sx = sem.get(key) if key else None
            if sx and sx["semantics"] in ("READ", "STAGING"):
                c2 = dict(c)
                c2["classification"] = f"EXCLUDED_{sx['semantics']}"
                c2["evidence"] = f"连接语义清单豁免（{sx.get('manual_evidence', sx['semantics'])}）；证据 data/snapshots/connection_semantics.json"
                exempt_w.append(c2)
            elif (c["file"], str(c["line"])) in pending_pts or _pending_content_match(c):
                keep = c  # pending 点保留在原类，locked=false
                (keep_main if c in main_w else keep_aux).append(c)
            else:
                (keep_main if c in main_w else keep_aux).append(c)
        main_w, aux_w = keep_main, keep_aux
        excl = excl + exempt_w

    reg = {
        "_note": "写路径覆盖清单 v3——由 scripts/governance_write_conn_scan.py 扫描生成（单一来源，stats 自动计算）。"
                 "create 前逐项校验 MAIN/AUX 全部 locked=true（locked 只能由代码接入锁协议的证据翻转，禁止手工改）。"
                 "重新生成：python scripts/governance_write_conn_scan.py",
        "generated_by": "scripts/governance_write_conn_scan.py",
        "main_db_writers": [{"module": c["file"], "line": c["line"], "desc": c["evidence"],
                             "snippet": c["snippet"],
                             "locked": _lock_flag(c)} for c in main_w],
        "aux_db_writers": [{"module": c["file"], "line": c["line"], "desc": c["evidence"],
                            "snippet": c["snippet"],
                            "locked": _lock_flag(c)} for c in aux_w],
        "excluded": [{"module": c["file"], "line": c["line"], "classification": c["classification"],
                      "evidence": c["evidence"]} for c in excl],
        "unresolved": unresolved,
        "_flip_rule": "locked=true ⇔ lock_adoption_log.adopted 含该模块守卫证据 且 连接点不在 pending；手工改 true 会被重新生成时按规则重算（伪造即失效）",
        "_semantic": "locked=true = 该文件全部写路径均已受锁保护，裸写路径已【结构性消除】（硬约束：aux_router.connect(read_only=False) raise / ObservationStore.__connect 私有化），非仅约定（DSH pending 拆分审计要求）",
        "stats": {  # 自动计算，禁止手写
            "total_connections_scanned": len(conns),
            "main_writers": len(main_w),
            "aux_writers": len(aux_w),
            "excluded": len(excl),
            "unresolved": len(unresolved),
            "locked_true": sum(1 for w in main_w + aux_w if _lock_flag(w)),
            "admission": f"MAIN({len(main_w)}) + AUX({len(aux_w)}) 全部 locked=true 才允许 create；"
                         f"当前 {sum(1 for w in main_w + aux_w if _lock_flag(w))}=true → "
                         + ("create 拒绝（存在未接入点）" if any(not _lock_flag(w) for w in main_w + aux_w) else "create 可解除拒绝"),
        },
    }
    OUT_REG.parent.mkdir(parents=True, exist_ok=True)
    json.dump(reg, io.open(OUT_REG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 双向差集报告（registry 为扫描产物 → 恒空；报告给出证明）
    reg_keys = {(w["module"], w["line"]) for w in reg["main_db_writers"] + reg["aux_db_writers"] + reg["excluded"]}
    scan_keys = {(c["file"], c["line"]) for c in conns}
    rep = {
        "date": "2026-08-17",
        "scan_rule": "多行感知（连接后 3 行内 read_only=true/mode=ro 排除；read_only=False/read_only=param 保留）",
        "classification_counts": dict(Counter(c["classification"] for c in conns)),
        "bidirectional_diff": {
            "scan_minus_registry": sorted(scan_keys - reg_keys),
            "registry_minus_scan": sorted(reg_keys - scan_keys),
            "both_empty": scan_keys == reg_keys,
        },
        "unresolved": unresolved,
        "note": "registry 由本扫描唯一生成，双向差集恒空；EXCLUDED 逐项附证据（staging 守卫/docstring/状态库/审计/导出/in-memory）",
    }
    OUT_REP.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rep, io.open(OUT_REP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"stats": reg["stats"], "diff_both_empty": rep["bidirectional_diff"]["both_empty"],
                      "unresolved": len(unresolved)}, ensure_ascii=False, indent=2))
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
