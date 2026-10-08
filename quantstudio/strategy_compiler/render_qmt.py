"""QMT renderer (M2a): render StrategyIR to a QMT innerApi daily strategy .py.

架构依据：docs/qmt-pipeline-architecture-m1.md（M1-rev2）——spec 路径（IR 渲染）
M2a 首策略打通（etf_hot_theme_rotation）；source 路径（orchestrate_source /
convert_source 的 QMT 变体）为 M2b 主体，本模块不触碰。

产物编码约定（M1 §2.1 render_qmt 行）：本函数返回 unicode str；gbk 转码在
orchestrator 写盘点执行（fail-closed：转码失败 raise StrategyPipelineError，
不静默替换——M1 风险清单 3）。

生命周期映射（M1 §3 定稿表）：
  initialize(ctx)      -> def init(C):      （06-系统函数.md init 节：仅开始运行一次）
  handle_data(ctx, d)  -> def handlebar(C): （06-系统函数.md:111-117：每根 K 线一次；
                                            日线=每 bar 即一交易日，daily-bar-v1 对齐）
  before_trading_start -> handlebar 前段（日周期，M1 1.1(1) 主路径）
  after_trading_end    -> M2b+（_qs_should_run_after 同构 wrapper，M1 1.2；弃 is_last_bar）

minute-bar-v1 显式 deny（M1 2.2 基准声明/§3）：qmt_minute.py.j2 缺席 = 有意
fail-closed（deny 硬门前置），分钟域待后续立项再落模板。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ir_nodes import StrategyIR

# 账号占位（M1 §3：'testS'；08-交易函数.md:57/623 惯例——策略交易界面运行时由
# 策略配置账号覆盖，编辑器/回测界面运行需手动改）。经模板上下文 qmt_account 注入。
QMT_ACCOUNT_PLACEHOLDER = "testS"

# minute deny 面（render.py _select_template_name 同款频率集合）
_MINUTE_FREQUENCIES = ("1m", "5m", "15m", "30m", "60m")


def enrich_qmt_context(ctx: dict[str, Any], ir: StrategyIR) -> None:
    """QMT 专属上下文装配（在通用 IR 上下文之上叠加，就地修改；M2a）。

    静态池直灌与 _qs_* wrapper 注入由 qmt_daily.py.j2 模板承载（双目录：包内
    templates/ + skills 回退目录，render.py:41-46 机制）；本函数只补 QMT 专属键。
    """
    ctx["qmt_account"] = QMT_ACCOUNT_PLACEHOLDER


def render_qmt(
    ir: StrategyIR, config_path: str | Path | None = None
) -> str:
    """Render IR to a QMT innerApi .py source (unicode str; gbk at write point).

    Raises:
        ValueError: minute-bar-v1 spec（显式 deny 域，M1 2.2——qmt_minute.py.j2
            有意缺席，fail-closed 不静默降级为日线模板）。
        GoldenProtectionError: 经 render_strategy 传播（golden 保护双源清单）。
    """
    freq = ir.engine_profile.get("bar_frequency", "1d") or "1d"
    if freq in _MINUTE_FREQUENCIES:
        raise ValueError(
            f"QMT rendering requires a daily profile (bar_frequency=1d); got "
            f"{freq!r}. minute-bar-v1 is an explicit deny domain in M2a "
            f"(qmt_minute.py.j2 deliberately absent; docs/qmt-pipeline-"
            f"architecture-m1.md M1-rev2 2.2/3)."
        )
    # 函数内 import：render_strategy 的 qmt 分支 lazy-import 本模块（防环），
    # 此处反向依赖在调用期解析，模块加载顺序无关。
    from .render import render_strategy

    return render_strategy(ir, "qmt", config_path)
