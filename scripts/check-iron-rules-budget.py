#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check-iron-rules-budget.py —— AGENTS.md 瘦身治理·防复发门禁（方案 B §三步骤 4，四查）

工单：docs/handoff/AGENTS-md-瘦身治理方案-20261025.md（2026-10-25 ②审通过 + 用户 5 条补强）
盘点预演（关键词集预登记面）：docs/handoff/AGENTS-md-瘦身-盘点预演-20261025.md

四查（全绿才准推送；挂 .githooks/pre-push，对一切 remote 生效，含 export/main 出库白名单通道）：
  ① 预算查        本仓 AGENTS.md ≤ 40,000 B；链合计（全局+本仓+CLAUDE）≤ 60,000 B；
                   单列全局 ~/.dsh/AGENTS.md > 20,000 B 告警（WARN 不拦截，防一头瘦身一头膨胀）。
  ② 覆盖查        RULES 登记的每条铁律 ≥3 个独有关键词，在「knowledge/iron-rules/*.md ∪ AGENTS.md」
                   合并语料中至少命中一处（防归档被删/被掏空/精简版被误删）。
  ③ 指针查        AGENTS.md 中出现的每个 knowledge/iron-rules/… 指针，目标文件必须存在。
  ④ 反向指针查    knowledge/iron-rules/*.md 每个文件必须被 AGENTS.md（索引表）引用——孤儿归档 = FAIL。

退出码：0 = 全绿；1 = 任一 FAIL。成功时只打一行摘要（推送噪声最小化），失败时打全明细。
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / "AGENTS.md"
CLAUDE = REPO / "CLAUDE.md"
IRON_DIR = REPO / "knowledge" / "iron-rules"
GLOBAL = Path.home() / ".dsh" / "AGENTS.md"

LIMIT_AGENTS = 40_000   # 本仓 AGENTS.md 上限（方案 §二）
LIMIT_CHAIN = 60_000    # 链合计 FAIL 线（方案 §四判据 1）
WARN_GLOBAL = 20_000    # 全局告警线（2026-10-25 用户补强条件 2；WARN 不拦截）
FRAMEWORK_OVERHEAD = 500  # 框架自身注入开销（方案 §二账目），仅作展示，不并入 FAIL 判定

# 关键词登记表：与 docs/handoff/AGENTS-md-瘦身-盘点预演-20261025.md §一 逐格一致（每条 ≥3）
RULES = {
    "01-六步流水线":          ["六步流水线", "双仓库推送", "用户确认", "docs/*-design.md"],
    "02-性能优化纯等价":      ["纯性能优化", "黄金结果", "语义完全等价", "撮合价格"],
    "03-写前快照":            ["写前快照", "回退点", "stash create", "stash store"],
    "04-策略生成默认skill":   ["quantstudio-strategy-compiler", "get_etf_list_local", "ETF_POOL_STATIC"],
    "05-MCP进度报告":         ["实时进度报告.md", "唯一权威进度档案", "待需完成"],
    "06-Rust先查教程":        ["rust教程", "104 个编号", "校准语法"],
    "07-全链路修复限框架层":  ["零改动", "修一送一", "重转", "CANSLIM"],
    "08-框架问题立即解决":    ["登记挂账", "BLOCKED(外部依赖", "关单标准", "立即解决"],
    "09-总调度三件套":        ["三件套", "总日历", "彻底闭环清单", "技术债清单"],
    "10-共享核心文件提交":    ["source_import.py", "check_fund_matrix", "wrapper_hash", "qmt_wrapper_hash"],
    "11-客户密钥对":          ["ssh-keygen", "id_ed25519", "客户密钥对", "Read-only"],
    "12-客户长期运维":        ["修复完成通知", "归因分析", "案例归档", "派单"],
    "13-日历双向同步":        ["日历同步段", "回执", "对账点"],
    "14-主仓同步trading":     ["同步门", "check-drift", "origin/main"],
    "15-审核回复分序":        ["裁定事项", "按会话分块", "回复分序"],
    "16-平台代码先查文档":    ["dict_thinktrader_net", "joinquant_help_api", "ptradeapi"],
    "17-E1日线includeFalse":  ["include=False", "NO-LOOKAHEAD-INCLUDE", "match_price_mode"],
    "18-纯增益审计":          ["三型判定", "纯恢复型", "数据修正型", "新增检测型"],
    "19-daemon占用纪律":      ["受控窗", "声明占用", "daemon", "误杀"],
    "20-三端框架层对齐":      ["QuantStudioHybrid", "逐位一致", "保温"],
    "21-知识反哺留痕":        ["知识反哺检索留痕", "grep-able", "例外亦须留痕", "Plan-Mode"],
    "22-知识反哺R1R4":        ["kb_draft.py", "validate_record", "本轮沉淀", "kb-info.yaml"],
    "23-WAL残留处置":         ["check_wal", "CHECKPOINT", "256MB", "duckdb"],
    "24-schema指纹加列":      ["partial_or_mixed", "verify_fingerprint", "SCHEMA_VERSION", "detect_schema_status"],
    "25-上游可变因子判据":    ["结构化版本差", "fund_adj", "fail-closed", "数据损坏"],
}

fails = []
warns = []


def main() -> int:
    # ---------- ① 预算查 ----------
    agents_b = AGENTS.read_bytes()
    agents_len = len(agents_b)
    if agents_len > LIMIT_AGENTS:
        fails.append(f"① 预算查 FAIL：AGENTS.md = {agents_len:,} B > {LIMIT_AGENTS:,} B")
    claude_len = len(CLAUDE.read_bytes()) if CLAUDE.exists() else 0
    chain_parts = [f"本仓 {agents_len:,}"]
    if GLOBAL.exists():
        g_len = len(GLOBAL.read_bytes())
        chain = g_len + agents_len + claude_len
        chain_parts.insert(0, f"全局 {g_len:,}")
        if g_len > WARN_GLOBAL:
            warns.append(f"① 预算查 WARN：全局 ~/.dsh/AGENTS.md = {g_len:,} B > 告警线 {WARN_GLOBAL:,} B"
                         f"（补强条件 2：防一头瘦身一头膨胀；本 WARN 不拦截，但须当周期处置）")
    else:
        g_len = None
        chain = agents_len + claude_len
        warns.append("① 预算查 NOTE：全局 ~/.dsh/AGENTS.md 不存在（客户机/他机），链合计按 本仓+CLAUDE 计")
    if claude_len:
        chain_parts.append(f"CLAUDE {claude_len:,}")
    if chain > LIMIT_CHAIN:
        fails.append(f"① 预算查 FAIL：链合计({'+'.join(chain_parts)}) = {chain:,} B > {LIMIT_CHAIN:,} B")

    # ---------- ② 覆盖查 ----------
    agents_text = agents_b.decode("utf-8", errors="replace")
    if not IRON_DIR.is_dir():
        fails.append("② 覆盖查 FAIL：knowledge/iron-rules/ 目录不存在")
        corpus = agents_text
        archive_files = []
    else:
        archive_files = sorted(p for p in IRON_DIR.glob("*.md"))
        corpus = agents_text + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in archive_files)
    missing_kw = []
    for rid, kws in RULES.items():
        for kw in kws:
            if kw not in corpus:
                missing_kw.append(f"{rid}: 「{kw}」")
    if missing_kw:
        fails.append("② 覆盖查 FAIL（关键词未命中）：" + "；".join(missing_kw))
    # 登记表 ↔ 归档目录 双向对账：登记 id 无归档文件 = FAIL；归档文件未登记 = FAIL（孤儿另见④）
    arc_names = {p.stem for p in archive_files}
    for rid in RULES:
        if rid not in arc_names:
            fails.append(f"② 覆盖查 FAIL：登记规则 {rid} 在 knowledge/iron-rules/ 无归档文件")
    for name in sorted(arc_names - set(RULES)):
        fails.append(f"② 覆盖查 FAIL：归档文件 {name}.md 未在 RULES 登记表登记（先补盘点表关键词再提交）")

    # ---------- ③ 指针查 ----------
    import re
    pointers = sorted(set(re.findall(r"knowledge/iron-rules/[^\s`|)）\]]+?\.md", agents_text)))
    bad_ptr = [p for p in pointers if not (REPO / p).is_file()]
    if bad_ptr:
        fails.append("③ 指针查 FAIL（指针目标缺失）：" + "；".join(bad_ptr))
    if not pointers:
        fails.append("③ 指针查 FAIL：AGENTS.md 中未发现任何 knowledge/iron-rules/ 指针（索引表被删？）")

    # ---------- ④ 反向指针查（2026-10-25 补强条件 1）----------
    orphans = [p.name for p in archive_files if p.name not in agents_text]
    if orphans:
        fails.append("④ 反向指针查 FAIL（孤儿归档，未被 AGENTS.md 索引表引用）：" + "；".join(orphans))

    # ---------- 汇总 ----------
    kw_total = sum(len(v) for v in RULES.values())
    detail = [
        f"[iron-rules-budget] ① 预算查：本仓 AGENTS.md {agents_len:,} B（≤{LIMIT_AGENTS:,}）；"
        f"链合计 {chain:,} B（≤{LIMIT_CHAIN:,}，构成：{'+'.join(chain_parts)}"
        f"{f'，全局 {g_len:,} B' if g_len is not None else ''}；含框架开销 ~{FRAMEWORK_OVERHEAD} B 仅示）",
        f"[iron-rules-budget] ② 覆盖查：{len(RULES)} 条规则 × {kw_total} 个关键词 全部命中"
        f"；归档 {len(archive_files)} 件与登记表双向对账一致",
        f"[iron-rules-budget] ③ 指针查：AGENTS.md 内 {len(pointers)} 个指针目标全部存在",
        f"[iron-rules-budget] ④ 反向指针查：{len(archive_files)} 件归档全部被 AGENTS.md 引用（孤儿 0）",
    ]
    for w in warns:
        print(f"[iron-rules-budget] {w}")
    if fails:
        print("[iron-rules-budget] ==== FAIL 明细 ====")
        for d in detail:
            print(d)
        for f_ in fails:
            print(f"[iron-rules-budget] {f_}")
        print("[iron-rules-budget] RESULT: FAIL（修复后再推送；工单 docs/handoff/AGENTS-md-瘦身治理方案-20261025.md）")
        return 1
    # 成功：一行摘要（明细可用 --verbose 查看）
    if "--verbose" in sys.argv or "-v" in sys.argv:
        for d in detail:
            print(d)
    chain_desc = f"{chain:,} B"
    print(f"[iron-rules-budget] PASS 四查全绿：AGENTS.md {agents_len:,} B / 链 {chain_desc} / "
          f"{len(RULES)} 条×{kw_total} 词命中 / 指针 {len(pointers)} 双向闭合 / 孤儿 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
