# -*- coding: utf-8 -*-
"""duckdb 版本闸（**薄封装** —— 判定规则唯一来源 = `duckdb_version_policy`）。

2026-10-29 变更（首跳 1.4.5 → 1.5.6，spec 002-duckdb-1.5-upgrade）：

- 旧：`REQUIRED_SERIES = "1.4"` + `ver.startswith("1.4.")`（单值前缀匹配）
- 新：放行集合 `{1.4.5 ≤ x < 1.5} ∪ {x == 1.5.6}`，规则集中于
  `quantstudio/pipeline/duckdb_version_policy.py`；**本模块不再持有任何版本规则**，
  只做「取版本 → 调策略 → 放行 / 拒启」。
- `REQUIRED_SERIES` 常量**已删除**（全仓外部引用点 = 0，核查表：
  `docs/evidence/duckdb-version-gate-compat-audit-20261029.md`）。

三入口覆盖不变：GUI / daemon（`-m` 与直启两形态）/ GUI 拉起子进程。
逃生阀不变：`QS_DUCKDB_VERSION_GATE=0` 显式放行并留醒目告警。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Tuple

from .duckdb_version_policy import (  # noqa: F401 —— ALLOWED_RANGES/parse_version 供外部引用
    ALLOWED_RANGES,
    describe_policy,
    is_allowed,
    parse_version,
    probe_version,
)
from .duckdb_version_policy import main as policy_main

logger = logging.getLogger(__name__)

GATE_ENV = "QS_DUCKDB_VERSION_GATE"

_POLICY_TEXT = describe_policy()

_REMEDY = (
    "修复方式（择一）：\n"
    '  ① 把当前解释器的 duckdb 升级到目标版本：pip install "duckdb==1.5.6"\n'
    "  ② 或改用已合规的解释器启动（放行集合："
    + _POLICY_TEXT
    + "）：\n"
    "     C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python311\\python.exe\n"
    "     C:\\python3.12.9\\python.exe\n"
    "  ③ 确需临时放行：设 "
    + GATE_ENV
    + "=0（风险自负，会在日志留醒目警告）。\n"
    "依据：依赖声明面 duckdb==1.5.6；放行集合 "
    + _POLICY_TEXT
    + "；判定源 quantstudio/pipeline/duckdb_version_policy.py。"
)


def check_duckdb_version() -> Tuple[bool, str]:
    """校验当前 duckdb 版本是否落在放行集合内。返回 `(ok, version_or_error)`。"""
    ok, detail = probe_version()
    if not ok:
        return False, detail
    return is_allowed(detail), detail


def require_duckdb_version(component: str) -> None:
    """不合规即拒启（`SystemExit(3)`）；合规则静默通过（debug 级留痕）。"""
    ok, detail = check_duckdb_version()
    if ok:
        logger.debug(f"[version-gate] {component}: duckdb {detail} 合规（放行集合：{_POLICY_TEXT}）")
        return

    if os.environ.get(GATE_ENV, "1").strip() == "0":
        logger.warning(
            f"[version-gate] {component}: duckdb {detail} **不合规**（放行集合：{_POLICY_TEXT}），"
            f"但 {GATE_ENV}=0 已显式放行 —— 风险自负")
        return

    msg = (
        f"[version-gate] 拒绝启动：{component} 当前 duckdb = **{detail}**，"
        f"不在放行集合（{_POLICY_TEXT}）。\n"
        "原因：放行集合外的版本不在本项目支持范围"
        "（1.5.x 早期小版本含 1.5.5 存在已知 ART index-delete 回归 issue duckdb#23645）。\n"
        + _REMEDY
    )
    logger.error(msg)
    print(msg, file=sys.stderr)
    raise SystemExit(3)


if __name__ == "__main__":
    # 契约：python -m quantstudio.pipeline.duckdb_version_gate --check [版本串] [--json]
    raise SystemExit(policy_main(sys.argv[1:]))
