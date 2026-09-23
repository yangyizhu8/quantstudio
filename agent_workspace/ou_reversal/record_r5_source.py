
import json, os, datetime
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0).isoformat()
TEXT = "用 data/quantstudio.old_20260920.db 跑 R5"
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    j["confirmation_evidence"]["external_db_override"] = {
        "confirmed": True,
        "customer_text": TEXT,
        "confirmed_at": now,
        "source": "customer_reply",
        "confirmed_scope": "R5 数据源 P2：以 data/quantstudio.old_20260920.db 作为外部库覆盖（external_db_override_confirmed=true）",
        "note": "项目本地库 data/quantstudio.db 存在但被生产守护进程占用；客户明确批准外部库覆盖。"
    }
    j["r5_data_source"] = {
        "declared_project_db": "data/quantstudio.db",
        "r5_backtest_db_path": "D:\\\\miniQMT策略实盘\\\\QuantStudio\\\\data\\\\quantstudio.old_20260920.db",
        "external_db_override_confirmed": True,
        "customer_text": TEXT,
        "equivalence_evidence": {
            "tables": 101,
            "window_open_days": 287,
            "window_index_days_000300": 286,
            "window_complete_snapshots": 15,
            "front_adjust_spot_check": "600519 @2025-01-02 close=1488.0 / close_front=1401.7967454286804（与主库 R1 实测逐位一致）",
            "note": "该库为 2026-09-20 迁移前旧库，数据冻结于 2026-09-18；窗口内覆盖与主库一致（含同一 2026-08-03 缺口）。"
        },
        "rejected_alternative": {
            "path": "agent_workspace/backtest_readonly/quantstudio.db（= data/quantstudio_backup_20260912.db）",
            "reason": "000300 指数日线止于 2026-07-31，窗口内仅 265/287 指数日 → 2026-08-03..2026-09-01 共 22 个交易日择时门会 fail-closed"
        }
    }
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "-> r5_data_source recorded")

# workspace ledger
lp = os.path.join(ROOT, "agent_workspace", "ou_reversal", "workspace_state.json")
led = json.load(open(lp, encoding="utf-8"))
led["pipeline"]["R5"] = {
    "status": "RUNNING",
    "data_source": "D:\\miniQMT策略实盘\\QuantStudio\\data\\quantstudio.old_20260920.db",
    "external_db_override": {"approved": True, "customer_text": TEXT, "approved_at": now},
    "window": "2025-07-01..2026-09-01",
    "capital": 100000,
    "match_price_mode": "open",
    "engine_profile": "daily-bar-v1",
    "runner": "agent_workspace/ou_reversal_csi300_10/r5_run.py",
    "note": "两跑独立进程（G3.5 复现性门禁）；三件套 SHA-256 逐位比对。"
}
led["pipeline"]["R2_5"] = {"status": "COMPLETED", "closed_at": "2026-09-23T00:05:22+08:00",
                           "customer_text": "确认，无异议，继续推进",
                           "approximations_confirmed": 14}
open(lp, "w", encoding="utf-8").write(json.dumps(led, ensure_ascii=False, indent=2) + "\n")
print("ledger updated")
