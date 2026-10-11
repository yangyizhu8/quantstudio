# -*- coding: utf-8 -*-
"""P6 交付段（对应设计文档 docs/ptrade-deploy-pipeline-design.md §5.2 P6 / §2 / §14）。

职责：
- 产物写入 output/deploy/<strategy_id>/<strategy_id>_trade.py；
- 转换报告 report.md（改写清单逐条 + 保真差异声明（按 execution.profile 生成对应
  声明文案）+ 门禁结果汇总）；
- runbook.md 模板（占位符化：模拟盘部署步骤 / 验收清单 / 实盘迁移清单 / 状态重建流程；
  券商/环境细节一律 {{占位符}}，公开仓不含实盘操作细节——批复修订①）；
- 敏感词扫描（可配置模式列表常量：常见券商名后缀 / 账号模式；命中 → 报告标注
  ROUTE-TO-TRADING，路由 trading 侧处置——§2.2 边界执行机制）。

确定性：产物文件内容禁止时间戳/绝对路径/随机数——报告与 runbook 同样不注入
时间戳，保证同一输入两次转换全部输出 SHA256 逐位一致（双端 parity 门禁基础，§7.3）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

#: 交付根目录（cwd 相对；与仓库 .gitignore 的 output/ 规则对齐）
OUTPUT_ROOT = Path("output") / "deploy"

#: 敏感词模式（§2.2：命中 → ROUTE-TO-TRADING 标注；列表常量便于扩展）
SENSITIVE_PATTERNS: Dict[str, str] = {
    "broker_names": (
        r"(国金|国泰君安|中信|华泰|招商|银河|广发|兴业|东方|光大|安信|方正|平安|"
        r"中泰|华鑫|恒泰|东兴|申万|海通|浙商|财通|长江|国盛|西南|东北|首创|渤海)"
        r"(证券|期货)"),
    "account_number": r"(资金账号|交易账号|股东账号|账号|账户)[:：=\s]*\d{6,}",
    "phone": r"\b1[3-9]\d{9}\b",
    "ip_addr": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}

#: 保真差异声明（§6.2 映射表 + §14 风险声明；按 execution.profile 取用）
FIDELITY_BY_PROFILE: Dict[str, List[str]] = {
    "daily_eod_1450": [
        "信号仍取 D-1（E1 铁律：get_history(..., include=False)），信号口径与回测一致",
        "成交价由回测收盘价变为尾盘 14:50 实时价（执行时点差，§14.2）",
        "日线回测盘后运行+收盘撮合若直接映射交易盘后会变隔夜单——本档位以 14:50 执行规避（F8）",
    ],
    "daily_open_0931": [
        "信号仍取 D-1（E1 铁律：get_history(..., include=False)），信号口径与回测一致",
        "成交价由回测收盘价变为次日 09:31 开盘价（执行时点差，§14.2）",
        "隔夜跳空风险显式声明（F8：15:00 后下单成隔夜单次日 9:15 报送）",
    ],
    "minute_keep": [
        "频率语义一致（分钟档保持原 handle_data 生命周期，§6.2）",
        "撮合差仍声明：模拟撮合 vs 真实柜台盘口（不消除，声明式管理）",
    ],
}

#: 通用保真声明（所有档位附加，§14.1）
COMMON_FIDELITY = [
    "撮合语义差（回测撮合模型 vs 真实柜台盘口）与滑点模型差不因转换消除，按声明式管理（§14.1）",
    "PBOX 版本差异：契约表版本列注记（如 check_limit ≥ PBOXQT1.0V202202.01.038，§14.3）",
    "模拟盘与实盘同引擎同代码仅撮合不同，两端分开部署、文件空间独立（F9）",
]

_ROUTE_FLAG = "**ROUTE-TO-TRADING**"


@dataclass
class DeliverResult:
    """P6 输出：交付目录 + 工件路径 + 报告文本 + 敏感词命中。"""

    out_dir: str
    artifacts: Dict[str, str] = field(default_factory=dict)
    report_text: str = ""
    runbook_text: str = ""
    sensitive_hits: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 敏感词扫描 --

def scan_sensitive(text: str) -> List[str]:
    """按 SENSITIVE_PATTERNS 扫描文本；返回命中标注清单（含 ROUTE-TO-TRADING 路由标记）。"""
    hits: List[str] = []
    for kind, pattern in SENSITIVE_PATTERNS.items():
        for m in re.finditer(pattern, text):
            line_no = text.count("\n", 0, m.start()) + 1
            excerpt = text[m.start():m.end()]
            hits.append(f"{_ROUTE_FLAG} [{kind}] 行 {line_no}: {excerpt}")
            break  # 每类首个命中即标注（避免刷屏；完整定位由人工复查）
    return hits


# ---------------------------------------------------------------- 报告生成 --

def _edits_table(edits: List[Dict[str, str]]) -> List[str]:
    lines = ["| # | 类型 | 位置 | 说明 |", "|---|---|---|---|"]
    for i, e in enumerate(edits, start=1):
        detail = str(e.get("detail", "")).replace("|", "\\|")
        lines.append(f"| {i} | {e.get('kind', '')} | {e.get('location', '')} | {detail} |")
    if len(lines) == 2:
        lines.append("| - | - | - | 无改写 |")
    return lines


def build_report(cfg, profile: Dict[str, object], edits: List[Dict[str, str]],
                 equiv_result, gate_result, sensitive_hits: List[str]) -> str:
    """转换报告（改写清单逐条 + 保真差异声明 + 门禁结果汇总）。"""
    lines: List[str] = []
    lines.append(f"# PTrade 部署转换报告 —— {cfg.strategy_id}")
    lines.append("")
    lines.append("## 一、转换概况")
    lines.append("")
    lines.append(f"- 源策略: `{cfg.source}`")
    lines.append(f"- 执行档位: `{cfg.execution.profile}`（设计文档 §6.2 唯一语义决策点）")
    lines.append(f"- 频率画像: `{profile.get('frequency', 'unknown')}`"
                 f"（证据: {profile.get('frequency_evidence') or '无'}）")
    lines.append(f"- 生命周期函数: {', '.join(profile.get('lifecycle', [])) or '无'}")
    lines.append(f"- 股票池来源: {profile.get('universe', {}).get('source', 'none')}"
                 f"（{profile.get('universe', {}).get('detail', '')}）")
    lines.append(f"- 订单 API 家族: {', '.join(profile.get('order_apis', [])) or '无'}")
    lines.append(f"- 持久化: enabled={cfg.persistence.enabled}, "
                 f"state_file=`{cfg.persistence.state_file}`, "
                 f"save_points={list(cfg.persistence.save_points)}")
    lines.append(f"- 护栏: limit_price_explicit={cfg.guards.limit_price_explicit}, "
                 f"limit_up_down_block={cfg.guards.limit_up_down_block}, "
                 f"suspension_block={cfg.guards.suspension_block}, "
                 f"order_idempotency={cfg.guards.order_idempotency}, "
                 f"fundamentals_retry={cfg.guards.fundamentals_retry}")
    lines.append("")
    lines.append("## 二、改写清单（逐条，全部带机器可识别标记，设计文档 §5.4）")
    lines.append("")
    lines.extend(_edits_table(edits))
    lines.append("")
    lines.append("## 三、保真差异声明（声明式管理，不消除）")
    lines.append("")
    for note in FIDELITY_BY_PROFILE.get(cfg.execution.profile, []) + COMMON_FIDELITY:
        lines.append(f"- {note}")
    for note in cfg.report.fidelity_notes:
        lines.append(f"- （用户附加）{note}")
    lines.append("")
    lines.append("## 四、门禁结果汇总（P5，设计文档 §5.2）")
    lines.append("")
    lines.append("| 门禁 | 结果 | 说明 | 违规明细 |")
    lines.append("|---|---|---|---|")
    for c in gate_result.checks:
        detail = c.detail.replace("|", "\\|")
        findings = "<br>".join(f.replace("|", "\\|") for f in c.findings) or "-"
        lines.append(f"| {c.name} | {c.status} | {detail} | {findings} |")
    if gate_result.version_notes:
        lines.append("")
        lines.append("PBOX 版本注记（§14.3）：")
        for v in gate_result.version_notes:
            lines.append(f"- {v}")
    if gate_result.unknown_apis:
        lines.append("")
        lines.append("契约表外未知 API（需人工确认）：")
        for u in gate_result.unknown_apis:
            lines.append(f"- {u}")
    lines.append("")
    lines.append(f"等价验证（P4）: {'PASS（白名单外 diff=0）' if equiv_result.passed else 'FAIL'}")
    lines.append("")
    lines.append("## 五、敏感词扫描（§2.2 边界执行机制）")
    lines.append("")
    if sensitive_hits:
        for h in sensitive_hits:
            lines.append(f"- {h}")
        lines.append("")
        lines.append("⚠ 命中敏感词：本报告与相关材料 **路由 QuantStudio-trading 侧处置**，"
                     "不得进入公开仓。")
    else:
        lines.append("- 未命中（券商名/账号/手机号/IP 模式均无）")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- runbook 模板 --

def build_runbook(cfg) -> str:
    """runbook 模板：策略级事实直填，环境级细节一律 {{占位符}}（批复修订①）。"""
    product_file = f"{cfg.strategy_id}_trade.py"
    return f"""# 部署 runbook —— {cfg.strategy_id}（占位符模板）

> 策略级事实直填；`{{{{占位符}}}}` 为环境细节，由部署人在 trading 侧填写，
> 公开仓不含实盘操作细节（设计文档 §2）。

## 一、模拟盘部署步骤

1. 将产物 `{product_file}` 上传至 PTrade 模拟盘策略区（{{{{券商客户端入口}}}}）。
2. 确认运行环境 PBOX 版本满足契约表注记（{{{{实际版本}}}}）。
3. 首启前确认状态文件路径可写：`{cfg.persistence.state_file}`
   （首启无状态文件将自动回退默认状态并告警，属预期行为）。
4. 选择执行档位对应的运行时段核对（档位 `{cfg.execution.profile}`）。
5. 启动策略并观察首日日志（护栏拦截/持久化告警均在 log 输出）。

## 二、模拟盘验收清单

- [ ] initialize 正常完成（无异常日志）
- [ ] 定时任务按档位触发（{cfg.execution.profile}）
- [ ] 下单前护栏检查生效（停牌/涨跌停/幂等日志可见）
- [ ] after_trading_end 后状态文件存在且可 pickle.load
- [ ] 重启策略：g 状态经框架自动持久化 + 显式落盘双层恢复（版本校验日志）
- [ ] 连续 {{{{验收交易日数}}}} 个交易日无未解释的差异

## 三、实盘迁移清单

- [ ] 模拟盘验收全部通过
- [ ] 实盘端重新部署同一份产物代码（F9：同引擎同代码，仅撮合不同；两端文件空间独立）
- [ ] 实盘账户/费率核对（{{{{实盘账户信息}}}}——不入公开仓）
- [ ] 首日小资金验证成交回报与预期一致

## 四、状态重建流程（§14.4）

> 禁止直接搬用模拟盘状态文件（F9：两端文件空间独立）。

1. 实盘首启以柜台持仓对账重建状态：`get_position`/`get_positions` 与
   `get_orders` 核对，不一致以柜台为准（F8：账户数据 6 秒同步，柜台为真相源）。
2. `g.state` 持久化版本校验失败时自动回退默认状态并 log.warning——按需人工修正。
3. 重建后手动触发一次落盘（等待 after_trading_end 或按 save_points 配置）。
"""


# ---------------------------------------------------------------- 主入口 --

def deliver(product_text: str, edits: List[Dict[str, str]], equiv_result,
            gate_result, cfg, profile: Dict[str, object],
            out_root: Optional[Path] = None) -> DeliverResult:
    """P6 交付：产物/报告/runbook 落盘 + 敏感词扫描路由标注。"""
    out_dir = (Path(out_root) if out_root else OUTPUT_ROOT) / cfg.strategy_id
    out_dir.mkdir(parents=True, exist_ok=True)

    product_path = out_dir / f"{cfg.strategy_id}_trade.py"
    report_path = out_dir / "report.md"
    runbook_path = out_dir / "runbook.md"

    sensitive_hits = scan_sensitive(product_text)

    report_text = build_report(cfg, profile, edits, equiv_result, gate_result,
                               sensitive_hits)
    runbook_text = build_runbook(cfg)

    product_path.write_text(product_text, encoding="utf-8", newline="\n")
    report_path.write_text(report_text, encoding="utf-8", newline="\n")
    runbook_path.write_text(runbook_text, encoding="utf-8", newline="\n")

    return DeliverResult(
        out_dir=str(out_dir),
        artifacts={
            "product": str(product_path),
            "report": str(report_path),
            "runbook": str(runbook_path),
        },
        report_text=report_text,
        runbook_text=runbook_text,
        sensitive_hits=sensitive_hits,
    )
