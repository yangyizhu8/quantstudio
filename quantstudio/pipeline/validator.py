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
        失败数据写入 Quarantine（不丢弃）。

        实现说明（2026-07-21 向量化重构）：
        原 implementation 逐行 for i in range(len(df)) + df[col].iloc[i]，
        对分钟表 31571 行/只 × 5202 只全市场拉取单只耗时 ~50s（profile 显示
        OHLCLogic/UnitCheck 的 <genexpr> + reject() 的 .iloc 赋值占 90%）。
        改为 boolean mask 矢量化 + 稀疏 dict 累积 hit_rules/error_vals，
        单只耗时降到 < 1s。所有规则的语义（哪些行被拒、命中哪条规则、错误值）
        与原 implementation 完全一致（由 tests/test_validator_behavior.py 锁定）。
        """
        schema = self.schemas[table]
        # 入口防御：确保 DataFrame 使用连续 RangeIndex，消除外部非连续 index 导致的
        # .iloc[pos] 越界问题（如 index [5,9] 在 len=2 时 .iloc[9] 触发 IndexError）。
        df = df.reset_index(drop=True)
        n = len(df)
        # 用 numpy bool 数组（比 pandas Series 标量赋值快得多）
        reject_mask = np.zeros(n, dtype=bool)
        # 稀疏累积：只记录被拒行的规则名和错误值，避免 N 个空 list/dict 的开销
        # key = 位置索引(0..n-1)，value = 规则名列表 / {field: val}
        hit_rules_map: Dict[int, List[str]] = {}
        error_vals_map: Dict[int, Dict[str, str]] = {}

        def mark_rule(pos: int, rule: str):
            """记录某位置命中某规则（去重：同一规则在同一位置只记一次，匹配旧行为）。"""
            lst = hit_rules_map.get(pos)
            if lst is None:
                hit_rules_map[pos] = [rule]
            elif rule not in lst:
                lst.append(rule)

        def reject_pos(pos: int, rule: str, field_: str, val: Any):
            reject_mask[pos] = True
            mark_rule(pos, rule)
            error_vals_map.setdefault(pos, {})[field_] = str(val)

        def reject_mask_batch(rule: str, field_: str, mask: np.ndarray, col_for_val: Optional[pd.Series] = None):
            """批量 reject：mask 是 bool 数组（长度 = len(df)），True 的位置全部标记。
            col_for_val 非空时，错误值取该列在该位置的值；否则用 field_ 占位。"""
            if not mask.any():
                return
            for pos in np.nonzero(mask)[0]:
                reject_mask[pos] = True
                mark_rule(int(pos), rule)
                if col_for_val is not None:
                    error_vals_map.setdefault(int(pos), {})[field_] = str(col_for_val.iloc[int(pos)])
                else:
                    error_vals_map.setdefault(int(pos), {})[field_] = field_

        fixed_count = warned_count = 0

        # ---- 1. RequiredColumns：必填字段存在且非空 ----
        required = [c for c, spec in schema["columns"].items() if spec.get("required")]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            # 整表缺列：全部拒绝
            logger.error(f"[Validator] {table}: missing required columns {missing_cols}, reject all")
            reject_mask[:] = True
            for pos in range(n):
                mark_rule(pos, "RequiredColumns")
                error_vals_map.setdefault(pos, {})["_columns"] = str(missing_cols)
        # 列存在不代表逐行有效；必填字段 NULL/NaN 同样必须拦截。
        for col in required:
            if col in df.columns:
                s = df[col]
                # 空/NaN
                is_null = s.isna().to_numpy()
                # 字符串类型（object 或 pandas string dtype）额外检查空字符串
                is_string_like = s.dtype == object or pd.api.types.is_string_dtype(s)
                if is_string_like:
                    is_blank = s.astype("string").fillna("").str.strip().eq("").to_numpy()
                    bad = is_null | is_blank
                else:
                    bad = is_null
                reject_mask_batch("RequiredValueNull", col, bad, s)

        # ---- 2. CodeFormat：代码格式正则（统一裸码 ^\d{6}$）----
        # code_field 可由 schema 显式声明（如 industry_classification 主键首列是
        # classification_system 而非证券代码）；未声明时按主键首列推断（既有行为）。
        pk = schema.get("primary_key", ["code"])
        code_col = (schema["code_field"] if "code_field" in schema
                    else (pk[0] if pk else "code"))
        if code_col is not None and code_col in df.columns:
            code_re = schema["columns"].get(code_col, {}).get("regex", r"^\d{6}$")
            pat = re.compile(code_re)
            s = df[code_col]
            # 统一转 Python str（兼容 pandas str dtype / object dtype）
            s_str = s.astype("string").fillna("")
            matched = s_str.map(lambda v: bool(pat.match(v)) if isinstance(v, str) else False)
            bad = (~matched).to_numpy()
            reject_mask_batch("CodeFormat", code_col, bad, s)

        # ---- 3. DateValid：时间字段（ms 时间戳 INTEGER）合法 ----
        time_field = schema.get("time_key", "time")
        if time_field in df.columns:
            s = pd.to_numeric(df[time_field], errors="coerce")
            is_null = s.isna().to_numpy()
            iv = s.to_numpy()
            bad_val = (iv <= 0) | (iv > 4e12) | np.isnan(iv)
            bad = is_null | bad_val
            reject_mask_batch("DateValid", time_field, bad, df[time_field])

        # ---- 4. PricePositive：价格 > 0 ----
        # 注意：schema 声明 gt:0 的字段，统一在规则 11 PositiveNumeric 处理（语义等价）。
        # 此处仅处理 schema 未声明 gt 但列名在价目表里的兜底（保留原行为）。
        for col in ["open", "high", "low", "close", "preClose"]:
            spec = schema["columns"].get(col, {})
            if col in df.columns and spec.get("gt", 0):
                # 已被规则 11 覆盖（gt 在 spec 里），跳过避免重复
                pass

        # ---- 4.1 isST 必须有值（不允许 NULL）----
        isst_spec = schema["columns"].get("isST", {})
        if "isST" in schema["columns"] and "isST" in df.columns:
            bad = df["isST"].isna().to_numpy()
            reject_mask_batch("IsSTNull", "isST", bad, df["isST"])
        elif isst_spec.get("required") and "isST" not in df.columns:
            reject_mask[:] = True
            for pos in range(n):
                mark_rule(pos, "IsSTNull")
                error_vals_map.setdefault(pos, {})["isST"] = "MISSING_COLUMN"

        # ---- 5. OHLCLogic：high >= max(o,l,c), low <= min(o,h,c) ----
        if all(c in df.columns for c in ["open", "high", "low", "close"]):
            o = pd.to_numeric(df["open"], errors="coerce")
            h = pd.to_numeric(df["high"], errors="coerce")
            l = pd.to_numeric(df["low"], errors="coerce")
            c = pd.to_numeric(df["close"], errors="coerce")
            valid = o.notna() & h.notna() & l.notna() & c.notna()
            ol_max = pd.concat([o, l, c], axis=1).max(axis=1)
            ol_min = pd.concat([o, h, c], axis=1).min(axis=1)
            bad_high = valid & (h < ol_max - 1e-9)
            bad_low = valid & (l > ol_min + 1e-9)
            reject_mask_batch("OHLCLogic", "high", bad_high.to_numpy(), df["high"])
            reject_mask_batch("OHLCLogic", "low", bad_low.to_numpy(), df["low"])

        # ---- 6. UnitCheck：amount/(close*volume) 比值在合理范围（防单位错配）----
        close_unit = schema["columns"].get("close", {}).get("unit", "")
        ratio_lo, ratio_hi = 0.5, 2.0
        if close_unit != "元":
            if all(c in df.columns for c in ["amount", "close", "volume"]):
                logger.debug(f"[Validator] {table}: 跳过 UnitCheck（close.unit='{close_unit}'，非'元'口径）")
        elif all(c in df.columns for c in ["amount", "close", "volume"]):
            amt = pd.to_numeric(df["amount"], errors="coerce")
            cls = pd.to_numeric(df["close"], errors="coerce")
            vol = pd.to_numeric(df["volume"], errors="coerce")
            valid = (amt > 0) & (cls > 0) & (vol > 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = (amt / (cls * vol)).where(valid, other=np.nan)
            bad = valid & ~ratio.between(ratio_lo, ratio_hi)
            # 用 round 后的 ratio 值作为错误值（匹配旧行为 round(ratio, 4)）
            rounded = ratio.round(4)
            reject_mask_batch("UnitCheck", "amount/(close*volume)_ratio", bad.to_numpy(), rounded)

        # ---- 7. TypeCheck：字段类型与 schema 一致（float / int）----
        for col, spec in schema["columns"].items():
            if col not in df.columns:
                continue
            t = spec["type"]
            if t in ("float", "int"):
                coerced = pd.to_numeric(df[col], errors="coerce")
                # 原: 非 NaN 但 coerce 后变 NaN = 类型错
                orig_notna = df[col].notna()
                bad = orig_notna & coerced.isna()
                reject_mask_batch("TypeCheck", col, bad.to_numpy(), df[col])

        # ---- 7.1 SchemaConstraint：enum / ge 通用约束 ----
        for col, spec in schema["columns"].items():
            if col not in df.columns:
                continue
            s = df[col]
            notna = s.notna().to_numpy()
            if "enum" in spec:
                allowed = set(spec["enum"])
                # isin 矢量化
                if s.dtype == object:
                    in_allowed = s.isin(allowed).to_numpy()
                else:
                    in_allowed = s.isin(allowed).to_numpy()
                bad = notna & ~in_allowed
                reject_mask_batch("EnumCheck", col, bad, s)
            if "ge" in spec:
                ge_thr = float(spec["ge"])
                num = pd.to_numeric(s, errors="coerce")
                bad = notna & (num < ge_thr) & num.notna()
                reject_mask_batch("RangeCheck", col, bad.to_numpy(), s)

        # ---- 7.2 FrequencyAlignment：分钟频率标签与时间网格 ----
        if table in ("stock_minutes", "etf_minutes"):
            valid_freqs = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60}
            if "freq" not in df.columns:
                reject_mask[:] = True
                for pos in range(n):
                    mark_rule(pos, "FrequencyMissing")
                    error_vals_map.setdefault(pos, {})["freq"] = "MISSING_COLUMN"
            else:
                s = df["freq"]
                # 统一转 Python str（兼容 object / string dtype）
                freq_vals = s.astype("string").fillna("").astype(object).to_numpy()
                # 构造每个 freq 对应的分钟数数组（无效 freq → -1）
                minute_map = {k: v for k, v in valid_freqs.items()}
                minutes_arr = np.array([minute_map.get(v, -1) if isinstance(v, str) else -1
                                        for v in freq_vals])
                invalid_mask = minutes_arr < 0
                # 不匹配（且有效）→ Mismatch
                mismatch_mask = (~invalid_mask) & (expected_freq is not None) & \
                                np.array([v != expected_freq for v in freq_vals])
                reject_mask_batch("FrequencyInvalid", "freq", invalid_mask, s)
                reject_mask_batch("FrequencyMismatch", "freq", mismatch_mask, s)
                # FrequencyGrid：时间戳必须是 freq 分钟数的整数倍
                if time_field in df.columns:
                    tv = pd.to_numeric(df[time_field], errors="coerce")
                    tv_valid = tv.notna().to_numpy()
                    tv_arr = tv.to_numpy()
                    # 仅对有效 freq 的位置算网格
                    grid_bad = np.zeros(n, dtype=bool)
                    valid_freq_mask = minutes_arr > 0
                    calc_mask = valid_freq_mask & tv_valid
                    if calc_mask.any():
                        minute_ms = minutes_arr[calc_mask] * 60_000
                        tv_part = tv_arr[calc_mask]
                        # NaN 已被 tv_valid 排除
                        rem = np.mod(tv_part.astype(np.int64), minute_ms.astype(np.int64))
                        grid_bad_calc = rem != 0
                        # 写回对应位置
                        calc_idx = np.nonzero(calc_mask)[0]
                        grid_bad[calc_idx[grid_bad_calc]] = True
                    reject_mask_batch("FrequencyGrid", time_field, grid_bad, df[time_field])

        # ---- 7.3 AdjustmentIntegrity：复权列完整性/价格逻辑/倍率一致性 ----
        if table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
            raw_ohlc = ["open", "high", "low", "close"]
            for side in ("front", "back"):
                cols = [f"{name}_{side}" for name in raw_ohlc]
                present = [col for col in cols if col in df.columns]
                if not present:
                    continue
                required_adjustment = source == "tushare"
                # 拼接 4 列数值（缺列用 NaN）
                parts = []
                for col in cols:
                    if col in df.columns:
                        parts.append(pd.to_numeric(df[col], errors="coerce").to_numpy())
                    else:
                        parts.append(np.full(n, np.nan))
                arr = np.column_stack(parts)  # shape (n, 4): o,h,l,c
                non_null = np.sum(~np.isnan(arr), axis=1)  # 每行非空数
                # Completeness: 0<non_null<4 或 (required 且 non_null!=4)
                bad_complete = (non_null > 0) & (non_null < 4)
                if required_adjustment:
                    bad_complete = bad_complete | ((non_null != 4) & (non_null > 0))
                reject_mask_batch("AdjustmentCompleteness", side, bad_complete)
                # 后续检查仅对 non_null==4 的行
                full = non_null == 4
                o_, h_, l_, c_ = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
                # Positive: min > 0
                with np.errstate(invalid="ignore"):
                    min4 = np.nanmin(np.where(np.isnan(arr), np.inf, arr), axis=1)
                bad_pos = full & (min4 <= 0)
                reject_mask_batch("AdjustmentPositive", side, bad_pos)
                # OHLC: h < max(o,l,c) - 1e-9 or l > min(o,h,c) + 1e-9
                checkable = full & ~bad_pos
                ol_max = np.maximum(np.maximum(o_, l_), c_)
                ol_min = np.minimum(np.minimum(o_, h_), c_)
                bad_ohlc_h = checkable & (h_ < ol_max - 1e-9)
                bad_ohlc_l = checkable & (l_ > ol_min + 1e-9)
                # 合并成一条规则（旧行为对 h 和 l 分别 reject，但规则名都是 AdjustmentOHLC）
                reject_mask_batch("AdjustmentOHLC", side, bad_ohlc_h | bad_ohlc_l)
                # FactorConsistency: 与 raw ohlc 的比值一致性
                # 仅当 raw 4 列都在且 >0
                # 阈值源感知（2026-07-22，与 quality_audit 一致）：
                # - xtquant back（分钟+日线）：逐 tick 累积复权，同根 K 线 OHLC 因子有 2-4% 微差
                #   （算法固有，非数据错误），用 5%；实测偏差全部 <4%，>5% 才是真错配
                # - xtquant front / 其他源：日级单一因子，OHLC 共用，严格 2%
                consistency_threshold = 0.05 if (source == "xtquant" and side == "back") else 0.02
                raw_all_present = all(c in df.columns for c in raw_ohlc)
                if raw_all_present:
                    raw_arr = np.column_stack([
                        pd.to_numeric(df[c], errors="coerce").to_numpy() for c in raw_ohlc
                    ])
                    raw_pos = np.all(raw_arr > 0, axis=1) & np.all(~np.isnan(raw_arr), axis=1)
                    factor_ok = checkable & raw_pos & ~bad_ohlc_h & ~bad_ohlc_l
                    bad_factor = np.zeros(n, dtype=bool)
                    if factor_ok.any():
                        idx_calc = np.nonzero(factor_ok)[0]
                        with np.errstate(divide="ignore", invalid="ignore"):
                            arr_sub = arr[idx_calc]
                            raw_sub = raw_arr[idx_calc]
                            factors = np.where(raw_sub > 0, arr_sub / raw_sub, np.nan)
                            base = np.nanmean(factors, axis=1)
                            rel = np.abs(factors / base[:, None] - 1)
                            max_rel = np.nanmax(rel, axis=1)
                            bad_factor_calc = (base <= 0) | (max_rel > consistency_threshold)
                        bad_factor[idx_calc[bad_factor_calc]] = True
                    reject_mask_batch("AdjustmentFactorConsistency", side, bad_factor)

        # ---- 8. DuplicateKey：主键去重（FIX 自动保留最后一条）----
        pk_cols = [c for c in schema.get("primary_key", []) if c in df.columns]
        if pk_cols:
            before = len(df)
            # 主键去重（PIT 语义，2026-07-27 用户批示）：
            # - 不同 ann_date 的报告版本**全部保留**（财务表完整主键已含
            #   ann_date：(code,end_date,ann_date)）——下游 as-of 查询在两次
            #   公告之间见初版、之后见重述版；
            # - 仅对**完全相同完整主键**的重复行去重：
            #   * 有 update_flag 列 → 同主键优先保留 update_flag=1（最终修正
            #     版）；平局用稳定排序 + 原行序（确定性）；
            #   * 无 update_flag 列 → 确定性规则：保留原始输入顺序的最后一条。
            # 禁止回退到 max(ann_date) 只保留最新版（丢失 PIT 历史）。
            if "update_flag" in df.columns:
                upd_rank = pd.to_numeric(df["update_flag"], errors="coerce").fillna(-1)
                order = upd_rank.sort_values(ascending=False, kind="stable").index
                df = df.loc[order].drop_duplicates(subset=pk_cols, keep="first")
                df = df.loc[df.index.sort_values()]   # 恢复原行序（确定性输出）
            else:
                df = df.drop_duplicates(subset=pk_cols, keep="last")
            fixed_count += before - len(df)
            # 重建 mask（去重后行数变化）：用 df.index 反查原位置
            # drop_duplicates 默认 keep='last'，保留的行 index 来自原 df
            # reject_mask 是按原位置 0..n-1 索引的，需按保留下来的 index 重排
            kept_pos = df.index.to_numpy()  # 原位置索引
            reject_mask = reject_mask[kept_pos]
            # hit_rules_map / error_vals_map 按 kept_pos 重索引
            hit_rules_map = {new_pos: hit_rules_map[old_pos]
                             for new_pos, old_pos in enumerate(kept_pos)
                             if old_pos in hit_rules_map}
            error_vals_map = {new_pos: error_vals_map[old_pos]
                              for new_pos, old_pos in enumerate(kept_pos)
                              if old_pos in error_vals_map}
            df = df.reset_index(drop=True)

        # ---- 9. PctChgRange：|pctChg| ≤ 涨跌停×tolerance（WARN 放行）----
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
        if "ann_date" in df.columns and "end_date" in df.columns:
            try:
                ad = pd.to_numeric(df["ann_date"], errors="coerce")
                ed = pd.to_numeric(df["end_date"], errors="coerce")
                import time as _time
                now_ms = int(_time.time() * 1000)
                future_bad = (ad > now_ms + 7 * 86400_000) & ad.notna()
                ann_lt_end = (ad < ed) & ad.notna() & ed.notna()
                strict_tables = ("balance_statement", "income_statement", "cashflow_statement", "fin_indicator")
                is_strict = table in strict_tables
                n_future_reject = int(future_bad.sum())
                # reject future_bad
                if n_future_reject:
                    reject_mask_batch("AnnDateLogic", "ann_date_in_far_future",
                                      future_bad.to_numpy())
                    # 错误值：匹配旧行为 f"ann={ad.loc[idx]:.0f},now≈{now_ms}"
                    for pos in np.nonzero(future_bad.to_numpy())[0]:
                        error_vals_map.setdefault(int(pos), {})["ann_date_in_far_future"] = \
                            f"ann={ad.iloc[int(pos)]:.0f},now≈{now_ms}"
                n_lt_end_reject = 0
                if is_strict:
                    strict_bad = ann_lt_end.to_numpy()
                    n_lt_end_reject = int(strict_bad.sum())
                    if n_lt_end_reject:
                        reject_mask_batch("AnnDateLogic", "ann_date<end_date", strict_bad)
                        for pos in np.nonzero(strict_bad)[0]:
                            error_vals_map.setdefault(int(pos), {})["ann_date<end_date"] = \
                                f"ann={ad.iloc[int(pos)]:.0f},end={ed.iloc[int(pos)]:.0f}"
                else:
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
        # 注意：用 "gt" in spec 判断（不能用 spec.get("gt",0) 因为 0 是 falsy 会误判）
        for col, spec in schema["columns"].items():
            if col not in df.columns or "gt" not in spec:
                continue
            gt_threshold = spec["gt"]
            s = df[col]
            num = pd.to_numeric(s, errors="coerce")
            bad = num.notna() & (num <= gt_threshold)
            reject_mask_batch("PositiveNumeric", col, bad.to_numpy(), s)

        # ---- 12. InfCheck：数值字段不能是 inf/-inf（防下游计算污染）----
        for col, spec in schema["columns"].items():
            if col not in df.columns or spec.get("type") not in ("float", "int"):
                continue
            s = df[col]
            num = pd.to_numeric(s, errors="coerce")
            inf_bad = np.isinf(num.to_numpy())
            reject_mask_batch("InfCheck", col, inf_bad, s)

        # ---- 13. ExtremeValue：已知易极端的字段范围检查（WARN 放行，进 hit_rules 不拒绝）----
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
        rejected_positions = np.nonzero(reject_mask)[0]

        rejected_rows = []
        rejected_rules_list = []
        error_values_list = []
        for pos in rejected_positions:
            pos_i = int(pos)
            row = df.iloc[pos_i].to_dict()
            # pandas Timestamp 转 str
            rejected_rows.append({k: (str(v) if hasattr(v, "isoformat") else v)
                                  for k, v in row.items()})
            rejected_rules_list.append(list(hit_rules_map.get(pos_i, [])))
            error_values_list.append(dict(error_vals_map.get(pos_i, {})))

        # ---- 写入 Quarantine [E-2] ----
        if rejected_rows and self.quarantine is not None:
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
