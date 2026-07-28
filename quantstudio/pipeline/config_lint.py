"""ConfigLint — 配置启动校验（Pipeline 防御层）

在 daemon.from_configs() 加载配置后立即执行，fail-fast：
发现 ERROR 级问题直接抛 ConfigLintError，阻止错误配置启动；
WARN 级问题打日志但不阻断（如 schema 与 writer pk 字典漂移）。

校验项：
    1. codes 合法性：["ALL"] 或合法代码（tushare 需 .SH/.SZ/.BJ 后缀）
    2. table 存在性：task.table 必须在 alignment_rules.schemas 里有定义
    3. enabled task 的 source 已启用（sources_config.sources.X.enabled=true）
    4. 财务表 PIT 门禁：primary_key 含 ann_date 的表必须有 available_at_field
    5. schema 主键一致性：与 writer.py 硬编码 pk 字典比对（WARN）

设计原则：
    - 只读校验，不修改任何配置
    - 错误信息明确（指出哪个 task/表/字段错、期望是什么）
    - 可被测试单独调用（lint_configs(data_cfg, sources_cfg, tasks_cfg, align_rules)）
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

from .source_capabilities import supports_task

logger = logging.getLogger(__name__)


class ConfigLintError(Exception):
    """配置校验失败（ERROR 级，阻止启动）"""


# writer.py 里硬编码的主键字典（与 DDL PRIMARY KEY 对齐）
# 用于第 5 项一致性校验。漂移时 WARN（非 ERROR，因为 upsert 仍能兜底）。
_WRITER_PK_REFERENCE = {
    "stock_daily": ["code", "time"],
    "stock_minutes": ["code", "time", "freq"],
    "etf_minutes": ["code", "time", "freq"],
    "tick": ["code", "time"],
    "fin_indicator": ["code", "end_date", "ann_date"],
    "index_daily": ["code", "time"],
    "etf_daily": ["code", "time"],
    "etf_basic": ["code"],
    "stock_float_share": ["code", "end_date", "ann_date"],
    "stock_daily_valuation": ["code", "time"],
    "index_constituents": ["index_code", "code", "time"],
    "balance_statement": ["code", "end_date", "ann_date"],
    "income_statement": ["code", "end_date", "ann_date"],
    "cashflow_statement": ["code", "end_date", "ann_date"],
    "stock_dividend": ["code", "ex_date"],
    "sw_industry": ["code", "industry_code"],
    "industry_classification": ["classification_system", "classification_version",
                                "industry_level", "industry_code", "effective_from"],
    "industry_membership": ["classification_system", "classification_version",
                            "industry_level", "industry_code", "code",
                            "effective_from"],
}

# 各源的代码格式（用于第 1 项 codes 校验）
_CODE_PATTERNS = {
    # tushare：6位数字 + 市场后缀
    "tushare": re.compile(r"^\d{6}\.(SH|SZ|BJ|CSI)$", re.IGNORECASE),
    # baostock：sh.600000 / sz.000001
    "baostock": re.compile(r"^(sh|sz|bj)\.\d{6}$", re.IGNORECASE),
    # 其余源用裸 6 位码
    "_default": re.compile(r"^\d{6}$"),
}

_REGISTERED_ADAPTERS = {"tushare", "baostock", "akshare", "xtquant", "a_stock_data"}


def lint_configs(data_cfg: Dict, sources_cfg: Dict,
                 tasks_cfg: Dict, align_rules: Dict) -> Tuple[List[str], List[str]]:
    """校验四层配置。返回 (errors, warnings)。

    errors 非空时调用方应 raise ConfigLintError 阻止启动。
    """
    errors: List[str] = []
    warnings: List[str] = []

    schemas = align_rules.get("schemas", {})
    sources = sources_cfg.get("sources", {})
    tasks = tasks_cfg.get("tasks", [])

    if not tasks:
        errors.append("collector_tasks.json: tasks 列表为空，无采集任务")
        return errors, warnings

    enabled_sources = {name for name, cfg in sources.items() if cfg.get("enabled", False)}
    for source in sorted(enabled_sources - _REGISTERED_ADAPTERS):
        errors.append(
            f"sources_config: 数据源 '{source}' enabled=true，但没有注册 Adapter 实现")

    for task in tasks:
        name = task.get("name", "<unnamed>")
        _lint_one_task(task, name, schemas, sources, errors, warnings)

    # 第 4、5 项：遍历所有 schema（不只 task 引用的，因为 schema 可能被多源共用）
    for table, schema in schemas.items():
        _lint_schema_pit_gate(table, schema, errors)
        _lint_schema_pk_consistency(table, schema, warnings)

    return errors, warnings


def _lint_one_task(task: Dict, name: str, schemas: Dict, sources: Dict,
                   errors: List[str], warnings: List[str]):
    """校验单个 task：codes 合法性 / table 存在 / 回退链中至少一项已启用且注册

    与 daemon._resolve_source_chain 保持一致的链优先级：
        task.source_priority > task.source > 全局 default_source_priority
    此处仅校验 task 级显式声明（source_priority / source），全局默认由 daemon 运行时兜底。
    """
    source = task.get("source", "")
    source_priority = task.get("source_priority") or []
    table = task.get("table", "")
    codes = task.get("codes")
    enabled = task.get("enabled", True)

    # 构建候选源链（仅 task 级显式部分）
    chain = list(source_priority)
    if source and source not in chain:
        chain.append(source)
    if not chain:
        errors.append(
            f"task '{name}': 未配置 source 或 source_priority，无法确定数据源")

    # 链中每一项必须是已注册源
    for s in chain:
        if s not in sources:
            errors.append(
                f"task '{name}': 候选源 '{s}' 未在 sources_config.sources 注册")

    # 至少一项已启用（否则该 task 永远无法执行）
    freq = task.get("freq", "daily")
    usable = []
    for candidate in chain:
        ok, reason = supports_task(candidate, table, freq)
        if not ok:
            warnings.append(
                f"task '{name}': 候选源 '{candidate}' 不支持 {table}/{freq}: {reason}")
        elif sources.get(candidate, {}).get("enabled", False):
            usable.append(candidate)
    if enabled and chain and not usable:
        errors.append(
            f"task '{name}': 候选链中无已启用且支持 {table}/{freq} 的数据源 ({chain})")

    # 第 2 项：table 必须在 schemas 定义
    if table and table not in schemas:
        errors.append(
            f"task '{name}': table='{table}' 在 alignment_rules.schemas 中未定义")

    # 第 1 项：codes 合法性（按链首位源判断格式）
    _lint_codes(name, chain[0] if chain else "", codes, errors, warnings)

    # 权威源校验：authoritative_source 必须已启用且支持该 task 的 table/freq
    authoritative = task.get("authoritative_source")
    if authoritative:
        ok, reason = supports_task(authoritative, table, freq)
        if not ok:
            errors.append(
                f"task '{name}': authoritative_source='{authoritative}' "
                f"不支持 {table}/{freq}: {reason}")
        elif not sources.get(authoritative, {}).get("enabled", False):
            errors.append(
                f"task '{name}': authoritative_source='{authoritative}' "
                f"未在 sources_config 中启用")
        if task.get("allow_fallback") is False:
            if not (ok and sources.get(authoritative, {}).get("enabled", False)):
                errors.append(
                    f"task '{name}': allow_fallback=false 但 "
                    f"authoritative_source='{authoritative}' 不可用（未启用或不支持该表）")


def _lint_codes(task_name: str, source: str, codes, errors: List[str], warnings: List[str]):
    """校验 codes 字段：["ALL"] 或合法代码列表"""
    if codes is None:
        # 无 codes（部分表如 index_constituents 用默认）不算错
        return
    if not isinstance(codes, list):
        errors.append(f"task '{task_name}': codes 必须是列表，实际类型 {type(codes).__name__}")
        return

    # 特殊值 ALL
    if codes == ["ALL"] or codes == "ALL":
        return

    # 具体代码列表：校验每个代码格式
    pat = _CODE_PATTERNS.get(source, _CODE_PATTERNS["_default"])
    bad = [c for c in codes if not (isinstance(c, str) and pat.match(c))]
    if bad:
        sample = bad[:3]
        if source == "tushare":
            expect = "格式 600000.SH / 000001.SZ / 830001.BJ"
        elif source == "baostock":
            expect = "格式 sh.600000 / sz.000001"
        else:
            expect = "裸 6 位码 600000"
        errors.append(
            f"task '{task_name}': codes 含非法代码 {sample}（source={source} 期望 {expect}）")

    # WARN：具体代码列表可能是调试残留（同类任务通常用 ALL）
    # 仅在代码数很少（<5）且非特殊用途表时提醒
    if 0 < len(codes) < 5:
        warnings.append(
            f"task '{task_name}': codes 只有 {len(codes)} 只 ({codes[:3]})，"
            f"疑似调试残留，全市场任务建议用 [\"ALL\"]")


def _lint_schema_pit_gate(table: str, schema: Dict, errors: List[str]):
    """第 4 项：财务表 PIT 门禁——主键含 ann_date 必须有 available_at_field"""
    pk = schema.get("primary_key", [])
    if "ann_date" in pk:
        aaf = schema.get("available_at_field")
        if aaf != "ann_date":
            errors.append(
                f"schema '{table}': primary_key 含 ann_date 但 available_at_field='{aaf}'，"
                f"财务表必须配 available_at_field='ann_date' 做 PIT 门禁（防重述用错版本）")


def _lint_schema_pk_consistency(table: str, schema: Dict, warnings: List[str]):
    """第 5 项：schema 主键与 writer 硬编码 pk 字典一致性（WARN）"""
    pk = schema.get("primary_key", [])
    ref = _WRITER_PK_REFERENCE.get(table)
    if ref is None:
        return  # writer 字典里没有的表（新增表），跳过
    if list(pk) != list(ref):
        warnings.append(
            f"schema '{table}': primary_key={pk} 与 writer.pk_for_dedup={ref} 不一致，"
            f"可能导致 upsert 去重逻辑与 schema 声明漂移")


def assert_configs_ok(data_cfg: Dict, sources_cfg: Dict,
                      tasks_cfg: Dict, align_rules: Dict):
    """校验配置，errors 非空则 raise ConfigLintError（fail-fast 入口）。

    供 daemon.from_configs() 调用。warnings 仅打日志不阻断。
    """
    errors, warnings = lint_configs(data_cfg, sources_cfg, tasks_cfg, align_rules)
    for w in warnings:
        logger.warning(f"[ConfigLint] {w}")
    if errors:
        for e in errors:
            logger.error(f"[ConfigLint] {e}")
        raise ConfigLintError(
            f"配置校验失败（{len(errors)} 个错误），请修复后重启。错误清单见上方日志。")
    if warnings:
        logger.info(f"[ConfigLint] 校验通过（0 错误，{len(warnings)} 个警告）")
    else:
        logger.info(f"[ConfigLint] 校验通过（0 错误，0 警告）")


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = Path(__file__).resolve().parent.parent.parent

    data_cfg = json.loads((root / "config" / "data_config.json").read_text(encoding="utf-8"))
    sources_cfg = json.loads((root / "config" / "sources_config.json").read_text(encoding="utf-8"))
    tasks_cfg = json.loads((root / "config" / "collector_tasks.json").read_text(encoding="utf-8"))
    align_rules = json.loads((root / "config" / "alignment_rules.json").read_text(encoding="utf-8"))

    errors, warnings = lint_configs(data_cfg, sources_cfg, tasks_cfg, align_rules)
    print(f"\n=== 校验结果: {len(errors)} 错误, {len(warnings)} 警告 ===")
    for e in errors:
        print(f"  [ERR] {e}")
    for w in warnings:
        print(f"  [WARN] {w}")
    if not errors:
        print("  [OK] 配置校验通过")
    sys.exit(1 if errors else 0)
