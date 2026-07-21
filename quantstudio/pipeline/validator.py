"""
PreIngestValidator — 入库前置校验 [E-2]

强制原则（基线 §1.2 第2条 + §1.2 第5条）：
- 对齐后的标准数据入库前必过校验
- 失败数据不丢弃，进 Quarantine 隔离区可重放 [E-2]
- 三级动作：REJECT（拦截）/ FIX（自动修复）/ WARN（告警放行）

10 条规则（基线 §三模块③ + D10 PIT 强化）：
    RequiredColumns / CodeFormat / DateValid / PricePositive / OHLCLogic
    UnitCheck / TypeCheck / DuplicateKey / PctChgRange / AnnDateLogic(PIT)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .quarantine import Quarantine

logger = logging.getLogger(__name__)
from quantstudio._paths import quarantine_db_path


@dataclass
class ValidationResult:
    """校验结果"""
    passed_df: pd.DataFrame              # 通过的行
    rejected_rows: List[Dict]            # 被拒的原始行（→ Quarantine）
    rejected_rules: List[str]            # 每行命中的规则
    error_values: List[Dict]             # 每行的错误值
    fixed_count: int = 0                 # FIX 自动修复的行数
    warned_count: int = 0                # WARN 告警放行的行数


class PreIngestValidator:
    """入库前置校验器

    使用：
        v = PreIngestValidator.from_config("config/alignment_rules.json", quarantine)
        result = v.validate(std_df, table="stock_daily", batch_id="batch_xxx", source="baostock")
        writer.write(result.passed_df, ...)   # 仅通过的入库
    """

    def __init__(self, schemas: Dict, quarantine: Optional[Quarantine] = None,
                 pctchg_tolerance_pct: float = 1.1):
        self.schemas = schemas
        self.quarantine = quarantine
        self.pctchg_tolerance_pct = pctchg_tolerance_pct

    @classmethod
    def from_config(cls, config_path: str | Path,
                    quarantine: Optional[Quarantine] = None) -> "PreIngestValidator":
        with Path(config_path).open("r", encoding="utf-8") as f:
            rules = json.load(f)
        return cls(rules["schemas"], quarantine)

    def validate(self, df: pd.DataFrame, table: str, batch_id: str,
                 source: str, expected_freq: Optional[str] = None) -> ValidationResult:
        """对标准化后的 df 执行全部规则，分离 passed/rejected。
        失败数据写入 Quarantine（不丢弃）。"""
        schema = self.schemas[table]
        reject_mask = pd.Series([False] * len(df), index=df.index)
        hit_rules = pd.Series([[] for _ in range(len(df))], index=df.index)
        error_vals = pd.Series([{} for _ in range(len(df))], index=df.index)
        fixed_count = warned_count = 0

        def reject(idx: int, rule: str, field_: str, val: Any):
            nonlocal reject_mask
            reject_mask.iloc[idx] = True
            hit_rules.iloc[idx].append(rule)
            error_vals.iloc[idx][field_] = str(val)

        # ---- 1. RequiredColumns：必填字段存在且非空 ----
        required = [c for c, spec in schema["columns"].items() if spec.get("required")]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            # 整表缺列：全部拒绝
            logger.error(f"[Validator] {table}: missing required columns {missing_cols}, reject all")
            for i in range(len(df)):
                reject(i, "RequiredColumns", "_columns", missing_cols)
        # 列存在不代表逐行有效；必填字段 NULL/NaN 同样必须拦截。
        for col in required:
            if col in df.columns:
                for pos, value in enumerate(df[col]):
                    if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                        reject(pos, "RequiredValueNull", col, value)

        # ---- 2. CodeFormat：代码格式正则（khQuant 裸码 ^\d{6}$）----
        pk = schema.get("primary_key", ["code"])
        code_col = pk[0] if pk else "code"
        if code_col in df.columns:
            code_re = schema["columns"].get(code_col, {}).get("regex", r"^\d{6}$")
            pat = re.compile(code_re)
            for i, v in df[code_col].items():
                if not (isinstance(v, str) and pat.match(v)):
                    reject(i, "CodeFormat", code_col, v)

        # ---- 3. DateValid：时间字段（ms 时间戳 INTEGER）合法 ----
        time_field = schema.get("time_key", "time")
        if time_field in df.columns:
            for i, v in df[time_field].items():
                if v is None or pd.isna(v):
                    reject(i, "DateValid", time_field, v)
                else:
                    try:
                        iv = int(v)
                        # 拒绝 0/负数/异常大值（>2099年 ≈ 4e12）
                        if iv <= 0 or iv > 4e12:
                            reject(i, "DateValid", time_field, v)
                    except (ValueError, TypeError):
                        reject(i, "DateValid", time_field, v)

        # ---- 4. PricePositive：价格 > 0 ----
        for col in ["open", "high", "low", "close", "preClose"]:
            spec = schema["columns"].get(col, {})
            if col in df.columns and spec.get("gt", 0):
                for i, v in df[col].items():
                    try:
                        if pd.notna(v) and float(v) <= 0:
                            reject(i, "PricePositive", col, v)
                    except (ValueError, TypeError):
                        reject(i, "PricePositive", col, v)

        # ---- 4.1 isST 必须有值（不允许 NULL）----
        # 各 adapter 应在 fetch_table 返回前填充 isST，这里校验确保不漏
        # 配置化：读 schema.columns.isST.required，覆盖所有声明了 isST 必填的表
        isst_spec = schema["columns"].get("isST", {})
        if "isST" in schema["columns"] and "isST" in df.columns:
            for i, v in df["isST"].items():
                if pd.isna(v):
                    reject(i, "IsSTNull", "isST", v)
        elif isst_spec.get("required") and "isST" not in df.columns:
            # schema 声明 isST 必填但 df 缺列（覆盖 stock_daily / etf_daily 等）
            for i in range(len(df)):
                reject(i, "IsSTNull", "isST", "MISSING_COLUMN")

        # ---- 5. OHLCLogic：high >= max(o,l,c), low <= min(o,h,c) ----
        if all(c in df.columns for c in ["open", "high", "low", "close"]):
            for i in range(len(df)):
                o, h, l, c = (df[c].iloc[i] for c in ["open", "high", "low", "close"])
                if all(pd.notna(x) for x in (o, h, l, c)):
                    if h < max(o, l, c) - 1e-9:
                        reject(i, "OHLCLogic", "high", h)
                    if l > min(o, h, c) + 1e-9:
                        reject(i, "OHLCLogic", "low", l)

        # ---- 6. UnitCheck：amount/(close*volume) 比值在合理范围（防单位错配）----
        # khQuant 口径统一：volume=股, amount=元, close=元 → ratio ≈ 1.0
        # 配置化：仅当 schema 声明 close.unit=="元" 时执行（指数=点、ETF=元仍校验、未来期货自动跳过）
        close_unit = schema["columns"].get("close", {}).get("unit", "")
        ratio_lo, ratio_hi = 0.5, 2.0
        if close_unit != "元":
            if all(c in df.columns for c in ["amount", "close", "volume"]):
                logger.debug(f"[Validator] {table}: 跳过 UnitCheck（close.unit='{close_unit}'，非'元'口径）")
        elif all(c in df.columns for c in ["amount", "close", "volume"]):
            for i in range(len(df)):
                amt, cls, vol = (df[c].iloc[i] for c in ["amount", "close", "volume"])
                if all(pd.notna(x) and float(x) > 0 for x in (amt, cls, vol)):
                    ratio = float(amt) / (float(cls) * float(vol))
                    if not (ratio_lo <= ratio <= ratio_hi):
                        reject(i, "UnitCheck", "amount/(close*volume)_ratio", round(ratio, 4))

        # ---- 7. TypeCheck：字段类型与 schema 一致（float / int）----
        for col, spec in schema["columns"].items():
            if col not in df.columns:
                continue
            t = spec["type"]
            if t in ("float", "int"):
                coerced = pd.to_numeric(df[col], errors="coerce")
                n_bad = coerced.isna().sum() - df[col].isna().sum()
                if n_bad > 0:
                    for i in df[col].index:
                        if pd.notna(df[col].iloc[i]):
                            try:
                                float(df[col].iloc[i])
                            except (ValueError, TypeError):
                                reject(i, "TypeCheck", col, df[col].iloc[i])

        # ---- 7.1 SchemaConstraint：enum / ge 通用约束 ----
        for col, spec in schema["columns"].items():
            if col not in df.columns:
                continue
            if "enum" in spec:
                allowed = set(spec["enum"])
                for pos, value in enumerate(df[col]):
                    if pd.notna(value) and value not in allowed:
                        reject(pos, "EnumCheck", col, value)
            if "ge" in spec:
                for pos, value in enumerate(df[col]):
                    if pd.notna(value):
                        try:
                            if float(value) < float(spec["ge"]):
                                reject(pos, "RangeCheck", col, value)
                        except (TypeError, ValueError):
                            pass

        # ---- 7.2 FrequencyAlignment：分钟频率标签与时间网格 ----
        if table in ("stock_minutes", "etf_minutes"):
            valid_freqs = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
            if "freq" not in df.columns:
                for pos in range(len(df)):
                    reject(pos, "FrequencyMissing", "freq", "MISSING_COLUMN")
            else:
                for pos, value in enumerate(df["freq"]):
                    if value not in valid_freqs:
                        reject(pos, "FrequencyInvalid", "freq", value)
                    elif expected_freq and value != expected_freq:
                        reject(pos, "FrequencyMismatch", "freq", value)
                if time_field in df.columns:
                    for pos in range(len(df)):
                        f = df["freq"].iloc[pos]
                        value = df[time_field].iloc[pos]
                        if f in valid_freqs and pd.notna(value):
                            minute_ms = valid_freqs[f] * 60_000
                            try:
                                if int(value) % minute_ms != 0:
                                    reject(pos, "FrequencyGrid", time_field, value)
                            except (TypeError, ValueError):
                                pass

        # ---- 7.3 AdjustmentIntegrity：复权列完整性/价格逻辑/倍率一致性 ----
        if table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
            raw_ohlc = ["open", "high", "low", "close"]
            for side in ("front", "back"):
                cols = [f"{name}_{side}" for name in raw_ohlc]
                present = [col for col in cols if col in df.columns]
                if not present:
                    continue
                required_adjustment = source == "tushare"
                for pos in range(len(df)):
                    values = [df[col].iloc[pos] if col in df.columns else np.nan for col in cols]
                    non_null = sum(pd.notna(v) for v in values)
                    if 0 < non_null < 4 or (required_adjustment and non_null != 4):
                        reject(pos, "AdjustmentCompleteness", side, values)
                        continue
                    if non_null == 0:
                        continue
                    try:
                        o, h, l, c = map(float, values)
                        if min(o, h, l, c) <= 0:
                            reject(pos, "AdjustmentPositive", side, values)
                            continue
                        if h < max(o, l, c) - 1e-9 or l > min(o, h, c) + 1e-9:
                            reject(pos, "AdjustmentOHLC", side, values)
                        if all(col in df.columns and pd.notna(df[col].iloc[pos]) and
                               float(df[col].iloc[pos]) > 0 for col in raw_ohlc):
                            factors = [float(values[i]) / float(df[raw_ohlc[i]].iloc[pos])
                                       for i in range(4)]
                            base = sum(factors) / len(factors)
                            if base <= 0 or max(abs(x / base - 1) for x in factors) > 0.02:
                                reject(pos, "AdjustmentFactorConsistency", side, factors)
                    except (TypeError, ValueError, ZeroDivisionError):
                        reject(pos, "AdjustmentType", side, values)

        # ---- 8. DuplicateKey：主键去重（FIX 自动保留最后一条）----
        pk_cols = [c for c in schema.get("primary_key", []) if c in df.columns]
        if pk_cols:
            before = len(df)
            # 财务报表特殊处理：主键含 ann_date 时，同一报告期(code,end_date)可能有多条
            # 不同公告日的记录（初版 + 重述/更正版）。保留 ann_date 最新的一条，
            # 即最终修正版，避免财务因子用到过时初版（如 000159 重述差7倍案例）。
            # ann_date 为空时（未公告）保留原序最后一条，不参与 ann_date 比较。
            has_fin_ann_date = "ann_date" in pk_cols and "ann_date" in df.columns
            if has_fin_ann_date:
                fin_pk = [c for c in pk_cols if c != "ann_date"]  # [code, end_date]
                if fin_pk:
                    df["_ann_for_rank"] = pd.to_numeric(df["ann_date"], errors="coerce")
                    # 同 fin_pk 组内，ann_date 最大(含空)的排第一；空 ann_date 退回保留原最后一条
                    df = (
                        df.sort_values("_ann_for_rank", ascending=False, na_position="last")
                          .drop_duplicates(subset=fin_pk, keep="first")
                          .drop(columns="_ann_for_rank")
                    )
            # 主键去重（同 ann_date 的 flag=0/1 重复对在此被合并）
            df = df.drop_duplicates(subset=pk_cols, keep="last")
            fixed_count += before - len(df)
            # 重建 mask（去重后索引已变）
            reject_mask = reject_mask.loc[df.index]
            hit_rules = hit_rules.loc[df.index]
            error_vals = error_vals.loc[df.index]

        # ---- 9. PctChgRange：|pctChg| ≤ 涨跌停×tolerance（WARN 放行）----
        # 配置化：阈值 = 涨跌停基准(20%, 覆盖创业板/科创板/ETF) × schema.pctchg_tolerance_pct(默认1.1)
        # 指数（close.unit=="点"）无涨跌停限制，直接跳过（如北证50 924行情单日+24%属正常）
        if "pctChg" in df.columns:
            close_unit = schema["columns"].get("close", {}).get("unit", "")
            if close_unit == "点":
                logger.debug(f"[Validator] {table}: 跳过 PctChgRange（指数无涨跌停，close.unit='点'）")
            else:
                tol = schema.get("pctchg_tolerance_pct", self.pctchg_tolerance_pct)
                limit = 20.0 * tol  # 基准20% × 容忍倍数（默认 20×1.1=22%）
                abs_pct = pd.to_numeric(df["pctChg"], errors="coerce").abs()
                out_of_range = abs_pct > limit
                n_warn = int(out_of_range.sum())
                warned_count += n_warn
                if n_warn:
                    logger.warning(f"[Validator] {table}: {n_warn} rows |pctChg|>{limit:g}% (WARN)")
                else:
                    logger.debug(f"[Validator] {table}: PctChgRange OK (limit={limit:g}%)")

        # ---- 10. AnnDateLogic：PIT 正确性校验（D10 强化）----
        # 对含 ann_date + end_date 的表（财务报表/股本表）校验"公告日逻辑合法"：
        #   - 规则 10a：ann_date 不能远在未来（超过当前日期 7 天 → REJECT，异常数据）
        #   - 规则 10b：ann_date < end_date（公告日早于报告期末日/生效日）
        #     * 三大报表（balance/income/cashflow）：REJECT（财报未结束就公告=数据错误）
        #     * stock_float_share：WARN 放行（xtquant Capital 的 m_timetag 是"股本变更生效日"，
        #       可能早于公告日 m_anntime，如新股登记/股本变动，是正常业务语义）
        #     * fin_indicator：REJECT（与三大报表同理）
        # 这是入库层能做的 PIT 保证。回测取数层另有"按回测日过滤 ann_date"兜底。
        if "ann_date" in df.columns and "end_date" in df.columns:
            try:
                ad = pd.to_numeric(df["ann_date"], errors="coerce")
                ed = pd.to_numeric(df["end_date"], errors="coerce")
                # 规则 10a：ann_date 不能远在未来（所有财务表统一 REJECT）
                import time as _time
                now_ms = int(_time.time() * 1000)
                future_bad = (ad > now_ms + 7 * 86400_000) & ad.notna()
                # 规则 10b：ann_date < end_date（按表区分严宽）
                ann_lt_end = (ad < ed) & ad.notna() & ed.notna()
                # stock_float_share 放宽为 WARN（股本生效日早于公告日是正常业务）
                strict_tables = ("balance_statement", "income_statement", "cashflow_statement", "fin_indicator")
                is_strict = table in strict_tables
                # reject 用位置索引（与 reject_mask.iloc 对齐）
                n_future_reject = 0
                n_lt_end_reject = 0
                n_lt_end_warn = 0
                for pos, idx in enumerate(df.index):
                    if bool(future_bad.loc[idx]):
                        reject(pos, "AnnDateLogic", "ann_date_in_far_future",
                               f"ann={ad.loc[idx]:.0f},now≈{now_ms}")
                        n_future_reject += 1
                    elif is_strict and bool(ann_lt_end.loc[idx]):
                        reject(pos, "AnnDateLogic", "ann_date<end_date",
                               f"ann={ad.loc[idx]:.0f},end={ed.loc[idx]:.0f}")
                        n_lt_end_reject += 1
                # stock_float_share 的 ann<end 只 WARN 不拒
                if not is_strict:
                    n_lt_end_warn = int(ann_lt_end.sum())
                    if n_lt_end_warn > 0:
                        logger.info(f"[Validator] {table}: {n_lt_end_warn} rows ann_date<end_date "
                                    f"(WARN 放行，股本生效日早于公告日是正常业务)")
                n_reject_total = n_future_reject + n_lt_end_reject
                if n_reject_total:
                    logger.warning(f"[Validator] {table}: {n_reject_total} rows 违反 AnnDateLogic "
                                   f"(future={n_future_reject}, ann<end={n_lt_end_reject})，已 REJECT")
                else:
                    logger.debug(f"[Validator] {table}: AnnDateLogic PIT 校验通过")
            except Exception as e:
                logger.warning(f"[Validator] {table}: AnnDateLogic 校验异常（跳过）: {e}")

        # ---- 11. PositiveNumeric：股本/市值/量为正（防负值/0 脏数据）----
        # 配置化：schema.columns 里声明 "gt": 0 的字段必须 > 0（流通股本/成交量/市值等）
        # 注意：用 "gt" in spec 判断（不能用 spec.get("gt",0) 因为 0 是 falsy 会误判）
        for col, spec in schema["columns"].items():
            if col not in df.columns or "gt" not in spec:
                continue
            gt_threshold = spec["gt"]
            for pos, idx in enumerate(df.index):
                v = df[col].loc[idx]
                if pd.notna(v):
                    try:
                        if float(v) <= gt_threshold:
                            reject(pos, "PositiveNumeric", col, v)
                    except (ValueError, TypeError):
                        pass  # TypeCheck 已处理

        # ---- 12. InfCheck：数值字段不能是 inf/-inf（防下游计算污染）----
        # TypeCheck 只查类型，inf 是合法 float 但会污染 pandas/numpy 计算
        for col, spec in schema["columns"].items():
            if col not in df.columns or spec.get("type") not in ("float", "int"):
                continue
            if col in df.columns:
                inf_mask = df[col].apply(lambda x: isinstance(x, (int, float)) and not pd.isna(x) and (x == float('inf') or x == float('-inf')))
                for pos, idx in enumerate(df.index):
                    if bool(inf_mask.loc[idx]):
                        reject(pos, "InfCheck", col, df[col].loc[idx])

        # ---- 13. ExtremeValue：已知易极端的字段范围检查（WARN 放行，进 hit_rules 不拒绝）----
        # PE/PB 等比率字段可能因极小分母变得巨大（如 265 万），属可疑但非必然错误。
        # 仅 WARN 不 REJECT（亏损股 PE 为负/极大是正常的，由策略层判断）。
        extreme_thresholds = {"peTTM": 1e6, "pe_ttm": 1e6, "pbMRQ": 1e4, "pb_ratio": 1e4}
        for col, thr in extreme_thresholds.items():
            if col in df.columns:
                vals = pd.to_numeric(df[col], errors="coerce")
                n_extreme = int((vals.abs() > thr).sum())
                if n_extreme:
                    warned_count += n_extreme
                    logger.debug(f"[Validator] {table}: {n_extreme} rows {col} 绝对值>{thr:g} (WARN)")

        # ---- 分离 passed / rejected ----
        passed_df = df[~reject_mask].copy()
        rejected_idx = df[reject_mask].index

        rejected_rows = []
        rejected_rules_list = []
        error_values_list = []
        for i in rejected_idx:
            row = df.loc[i].to_dict()
            # pandas Timestamp 转 str
            rejected_rows.append({k: (str(v) if hasattr(v, "isoformat") else v)
                                  for k, v in row.items()})
            rejected_rules_list.append(list(hit_rules.loc[i]))
            error_values_list.append(dict(error_vals.loc[i]))

        # ---- 写入 Quarantine [E-2] ----
        if rejected_rows and self.quarantine is not None:
            # 按规则分组写（同一批可能命中多规则，合并写）
            self.quarantine.write(
                batch_id=batch_id, table=table, source=source,
                rows=rejected_rows,
                failed_rules=list(set(r for rules in rejected_rules_list for r in rules)),
                error_values=error_values_list[0] if error_values_list else None,
                contract_version=schema.get("schema_version", "1.0"))

        result = ValidationResult(
            passed_df=passed_df, rejected_rows=rejected_rows,
            rejected_rules=rejected_rules_list, error_values=error_values_list,
            fixed_count=fixed_count, warned_count=warned_count)

        logger.info(f"[Validator] {table} batch={batch_id}: "
                    f"passed={len(passed_df)} rejected={len(rejected_rows)} "
                    f"fixed={fixed_count} warned={warned_count}")
        return result


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    here = Path(__file__).resolve().parent.parent.parent

    q = Quarantine(quarantine_db_path())
    v = PreIngestValidator.from_config(here / "config" / "alignment_rules.json", q)

    # 模拟对齐后的数据（含脏数据）
    df = pd.DataFrame({
        "ts_code":     ["600000.SH", "600000.SH", "600000.SH", "600000.SH", "BAD_CODE"],
        "trade_date":  ["2026-07-10", "2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13"],
        "open":        [10.0, 10.0, 10.5, 11.0, 10.0],     # 第4行 OHLC 违规(high<close)
        "high":        [10.2, 10.2, 10.8, 10.5, 10.2],     # 第4行 high=10.5 < close=11.0
        "low":         [9.9, 9.9, 10.3, 10.8, 9.9],
        "close":       [10.1, 10.1, 10.6, 11.0, 10.1],
        "pct_chg":     [1.0, 1.0, 4.95, 3.77, 1.0],
        "vol":         [1000.0, 1000.0, 1200.0, 1100.0, 1000.0],   # 手
        "amount":      [1010.0, 1010.0, 1272.0, 1210.0, 1010.0],   # 千元
    })
    res = v.validate(df, table="stock_daily", batch_id="smoke_test_001", source="test")

    print(f"\n=== 验证结果 ===")
    print(f"通过: {len(res.passed_df)} 行")
    print(f"拒绝: {len(res.rejected_rows)} 行 → Quarantine")
    print(f"修复(去重): {res.fixed_count} 行")
    print(f"告警: {res.warned_count} 行")

    print(f"\n=== 通过的数据 ===")
    print(res.passed_df[["ts_code", "trade_date", "close"]])

    print(f"\n=== Quarantine 内容 ===")
    print(q.list_pending()[["batch_id", "table_name", "failed_rules"]])
    print(f"stats: {q.stats()}")

    assert len(res.passed_df) == 2, f"应通过 2 行（主键去重后 1 行 + 另 2 日 2 行 - 重复）"
    assert len(res.rejected_rows) >= 2, "应至少拒绝 2 行（OHLC违规 + 代码格式错）"
    print("\n✅ PreIngestValidator 验证通过")
