
import json, os, datetime
ROOT = r"D:\miniQMT策略实盘\QuantStudio"
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0).isoformat()

# 1) design: robustness_gates.enabled=false + exemption reason
for rel in [r"output\generated_strategies\ou_reversal_csi300_10\agent_strategy_design.json",
            r"agent_workspace\ou_reversal_csi300_10\agent_strategy_design.json"]:
    p = os.path.join(ROOT, rel)
    j = json.load(open(p, encoding="utf-8"))
    j["validation_contract"] = {
        "robustness_gates": {
            "enabled": False,
            "exemption_reason": "客户风险接受型发布：客户知悉 R5 绩效为负（总收益 -29.29% / 超额 -46.45 pp / 最大回撤 -30.75%），"
                                "明确裁定「策略本身盈亏不重要」，本策略用途为验证本地回测功能与 PTrade 转换管线的双端对齐能力。",
            "customer_text": "发布，我的意图旨在测试回测功能和转换ptrade管线的双端对齐功能，策略本身盈亏不重要。",
            "confirmed_at": now,
            "source": "customer_reply",
            "note": "R5.5 若后续执行，结果原样留痕，不豁免为 PASS。"
        }
    }
    open(p, "w", encoding="utf-8").write(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    print(rel, "-> robustness_gates.enabled=False")

# 2) workspace ledger: BACKTEST_PASS + robustness exempted
lp = os.path.join(ROOT, "agent_workspace", "ou_reversal_csi300_10", "workspace_state.json")
led = json.load(open(lp, encoding="utf-8"))
led["stage"] = "BACKTEST_PASS"
led["backtest_status"] = "PASS"
led["backtest_execution_owner"] = "agent_managed"
led["robustness"] = {
    "stage": "EXEMPTED",
    "exempted": True,
    "reason": "客户风险接受型发布（verbatim 确认见 design.validation_contract.robustness_gates）",
    "customer_text": "发布，我的意图旨在测试回测功能和转换ptrade管线的双端对齐功能，策略本身盈亏不重要。",
    "exempted_at": now
}
led["r5_evidence"] = {
    "run1_output_dir": "output/backtest_results/20260923_101807_strategy",
    "run1_provenance": "agent_workspace/ou_reversal_csi300_10/r5_provenance_run1.json",
    "backtest_db_path": "D:\\miniQMT策略实盘\\QuantStudio\\data\\quantstudio.old_20260920.db",
    "external_db_override_confirmed": True,
    "window": "2025-07-01..2026-09-01",
    "trading_days": 287,
    "metrics": {"total_return_pct": -29.29, "benchmark_pct": 17.16, "excess_pp": -46.45, "max_dd_pct": -30.75, "trades": 695},
    "deployment_invariants_fails": 0,
    "runtime_errors": 0
}
led["quantstudio_output"] = "quantstudio/backtest/strategies/沪深300均值回归超跌反弹.py"
led["quantstudio_output_status"] = "GENERATED"
led["canonical_sha256"] = None
open(lp, "w", encoding="utf-8").write(json.dumps(led, ensure_ascii=False, indent=2) + "\n")
print("ledger -> stage=%s backtest_status=%s robustness=%s" % (led["stage"], led["backtest_status"], led["robustness"]["stage"]))
print("ledger keys:", list(led.keys()))
