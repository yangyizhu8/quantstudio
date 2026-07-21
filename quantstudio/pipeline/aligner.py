"""
FieldAligner — 字段对齐引擎（Phase 1.1 核心）

6 步流水线：列名映射 → 代码统一 → 日期统一 → 单位换算 → QFQ前复权 → pct_chg透传
配置驱动（读 config/alignment_rules.json），所有外部数据源入库前强制走此模块。

强制原则（AGENTS 基线 §1.2）：
- 全库统一到 tushare 格式（代码 600001.SH / 日期 datetime / 字段名 close/vol/amount）
- 真实涨跌幅一律用 pct_chg 字段 [E-3]，禁用跨批次 QFQ close.pct_change()
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 代码格式转换器（基线 §五 code_format_converters）
# ---------------------------------------------------------------------------
def normalize_code(code: str, fmt: str = "identity") -> Optional[str]:
    """统一股票代码到 khQuant 裸码格式 600000 / 000001 / 830001（无市场后缀）

    支持的输入格式：
        identity:        600000（已是裸码）
        tushare_to_raw:  600000.SH / 000001.SZ / 830001.BJ → 600000
        baostock_to_raw: sh.600000 / sz.000001 → 600000
    """
    if code is None or (isinstance(code, float) and np.isnan(code)):
        return None
    code = str(code).strip()

    if fmt == "identity":
        return code if re.match(r"^\d{6}$", code) else None

    if fmt == "tushare_to_raw":
        # 600000.SH → 600000
        m = re.match(r"^(\d{6})\.(SH|SZ|BJ)$", code, re.IGNORECASE)
        if m:
            return m.group(1)
        # 已是裸码
        if re.match(r"^\d{6}$", code):
            return code
        return None

    if fmt == "baostock_to_raw":
        # sh.600000 → 600000
        m = re.match(r"^(sh|sz|bj)\.(\d{6})$", code.lower())
        if m:
            return m.group(2)
        # 已是裸码
        if re.match(r"^\d{6}$", code):
            return code
        return None

    # 兜底：尝试提取 6 位数字
    m = re.search(r"(\d{6})", code)
    return m.group(1) if m else None


def market_of_code(code: str) -> str:
    """裸码 → 市场标识（Exporter 按股票分库时用：6→SH, 0/3→SZ, 8/4→BJ）"""
    code = str(code).strip()
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    return "BJ"


# ---------------------------------------------------------------------------
# 日期/时间统一到毫秒时间戳（khQuant time 字段，INTEGER 毫秒 epoch）
# ---------------------------------------------------------------------------
def to_ms_timestamp(val: Any) -> Optional[int]:
    """统一任意日期/时间格式到毫秒时间戳 INTEGER（khQuant 口径）
    兼容：20260713 / 2026-07-13 / 2026-07-13 09:30:00 / datetime / Timestamp / 秒/毫秒数字
    """
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, bool):
        return None
    # int / numpy integer
    if isinstance(val, (int, np.integer)):
        v = int(val)
        # 已是毫秒时间戳（>1e12）
        if v > 1e12:
            return v
        # 秒级时间戳
        if v > 1e9:
            return v * 1000
        # YYYYMMDD 数字（如 20260713）→ 直接解析，不回调自身
        if 19000101 <= v <= 99991231:
            s = str(v)
            try:
                ts = pd.Timestamp(datetime.strptime(s, "%Y%m%d")).tz_localize("Asia/Shanghai")
                return int(ts.timestamp() * 1000)
            except ValueError:
                return None
        return v
    # float（非 NaN）→ 尝试当时间戳或 YYYYMMDD.0
    if isinstance(val, float):
        v = int(val)
        return to_ms_timestamp(v)
    if isinstance(val, (pd.Timestamp, datetime)):
        ts = pd.Timestamp(val)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Shanghai")
        return int(ts.timestamp() * 1000)
    # 字符串
    s = str(val).strip()
    if re.match(r"^\d+$", s):
        # 纯数字字符串 → 转 int 处理（int 分支已涵盖所有情况，不会回调字符串）
        return to_ms_timestamp(int(s))
    # YYYY-MM-DD[ HH:MM:SS]
    try:
        ts = pd.Timestamp(s)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Shanghai")
        return int(ts.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# 兼容旧调用（保留 datetime 模式，内部转 ms）
def normalize_datetime(val: Any) -> Optional[int]:
    """[兼容] 返回毫秒时间戳（khQuant time 字段）"""
    return to_ms_timestamp(val)


# ---------------------------------------------------------------------------
# FieldAligner 主类
# ---------------------------------------------------------------------------
class FieldAligner:
    """字段对齐引擎：读取 alignment_rules.json，把任意源数据转为 tushare 标准格式。

    使用：
        aligner = FieldAligner.from_config("config/alignment_rules.json")
        std_df, meta = aligner.align(raw_df, table="stock_daily", source="baostock")
    """

    def __init__(self, rules: Dict, db_path: Optional[str | Path] = None):
        self.schemas = rules["schemas"]
        self.source_mappings = rules["source_mappings"]
        self.schema_version = rules.get("schema_version", "1.0")
        # db_path：Canonical 库路径（DuckDB），用于 pctChg 垃圾值 DB 兜底推导。
        # 不传则跳过 DB 兜底（仅批次内 close_front 推导），不影响主流程。
        self.db_path = Path(db_path) if db_path else None
        # shared_conn_provider：可选回调，返回持久 read_write 连接（采集流程内由 daemon
        # 注入 writer.shared_conn）。用于 pctChg DB 兜底，避免开 read_only 与 write 并发冲突。
        # 不传则降级为自开 read_only（CLI/回测场景，无并发 write 不冲突）。
        self.shared_conn_provider = None

    @classmethod
    def from_config(cls, config_path: str | Path,
                    db_path: Optional[str | Path] = None) -> "FieldAligner":
        config_path = Path(config_path)
        with config_path.open("r", encoding="utf-8") as f:
            rules = json.load(f)
        return cls(rules, db_path=db_path)

    def align(self, raw_df: pd.DataFrame, table: str, source: str,
              adj_factor_df: Optional[pd.DataFrame] = None,
              adj_latest_map: dict = None, adj_earliest_map: dict = None,
              close_df: Optional[pd.DataFrame] = None,
              namechange_df: Optional[pd.DataFrame] = None,
              valuation_df: Optional[pd.DataFrame] = None,
              freq: Optional[str] = None) -> Tuple[pd.DataFrame, Dict]:
        """执行对齐流水线，返回 (标准化DataFrame, metadata)。

        Args:
            raw_df: 原始数据
            table: 目标表名（stock_daily / stock_minutes / tick / fin_indicator / index_daily）
            source: 数据源名（tushare / baostock / akshare / xtquant）
            adj_factor_df: 复权因子 DataFrame（列 code/time/adj_factor，可选）。
                           若提供则计算 8 种复权价填入 open_front/close_front 等。
            close_df: 收盘价 DataFrame（列 code/time/close，可选）。
                      用于 stock_float_share 补算 circ_mv/total_mv（= free_share × end_date 最近交易日 close）。
            namechange_df: 简称变更历史 DataFrame（列 code/change_date/name_after/status_after，可选）。
                           用于 stock_daily 推导 is_st_reliable（PIT：找 ≤time 的最近变更记录）。
            valuation_df: 每日估值 DataFrame（列 code/time/circ_mv，可选）。
                          用于 stock_daily 推导 is_delisting_risk（近 20 日 circ_mv<5 亿）。

        Returns:
            (std_df, metadata) —— metadata 含 batch/source/applied_steps 等
        """
        mapping = self._get_mapping(table, source, freq=freq)
        schema = self.schemas[table]
        applied_steps = []
        time_to_ms = mapping.get("time_to_ms", False)

        # ---- Step 1: 列名映射 ----
        df = self._map_columns(raw_df, mapping)
        applied_steps.append("column_map")

        # ---- Step 2: 代码格式统一（裸 6 位码）----
        code_col = self._find_code_col(df, schema)
        if code_col is not None:
            fmt = mapping.get("code_format", "identity")
            df[code_col] = df[code_col].apply(lambda c: normalize_code(c, fmt))
        applied_steps.append("code_normalize")

        # ---- Step 3: 时间统一到毫秒时间戳（khQuant time 字段，INTEGER ms）----
        if time_to_ms:
            # schema 中 type=int 且是时间相关的列（time/ann_date/end_date）转 ms
            time_cols = [c for c, spec in schema["columns"].items()
                         if spec.get("type") == "int"
                         and c in df.columns
                         and ("time" in c or "date" in c)]
            for col in time_cols:
                df[col] = df[col].apply(to_ms_timestamp)
        applied_steps.append("time_to_ms" if time_to_ms else "time_skip")

        # ---- Step 4: 单位换算 ----
        unit_conversions = mapping.get("unit_conversions", {})
        for col, conv in unit_conversions.items():
            if col in df.columns and "factor" in conv:
                df[col] = pd.to_numeric(df[col], errors="coerce") * conv["factor"]
        applied_steps.append("unit_convert")

        # ---- Step 4.3: derive_fields 补算（市值 = 股本 × 收盘价）----
        # 对 stock_float_share：circ_mv/total_mv 缺失时用 free_share/total_share × end_date 最近交易日 close 补算
        # 修正1：end_date 可能是非交易日，用 merge_asof(direction="backward") 取 ≤ end_date 的最近交易日 close
        # 修正2：源已提供 circ_mv 时，补算后比对，差异 >5% 则 warning（不拒绝，记录归因）
        if table == "stock_float_share" and close_df is not None:
            applied_steps.append(self._derive_market_value(df, close_df))
        elif table == "stock_float_share":
            applied_steps.append("derive_mv_skip_no_close")

        # ---- Step 4.4: derive_st_status（stock_daily 推导 is_st_reliable + is_delisting_risk）----
        # 这一步在数据层解决 isST 不可靠问题，避免回测层做临时处理。
        # is_st_reliable：PIT 查 stock_namechange 找 ≤time 最近变更，status_after ∈ {ST, *ST} → True
        # is_delisting_risk：兜底，close<1 元 OR 近 20 日 circ_mv<5 亿（来自 valuation_df）
        if table == "stock_daily":
            applied_steps.append(self._derive_st_status(df, namechange_df, valuation_df))

        # ---- Step 4.45: derive_valuation_fields（xtquant 日线补估值字段）----
        # xtquant 不提供 peTTM/pbMRQ/psTTM/turn，tushare 时代这些来自 daily_basic。
        # 切权威源到 xtquant 后，由 stock_daily_valuation 表（仍走 tushare daily_basic 作前置依赖）
        # PIT JOIN 补全 stock_daily 的 peTTM/pbMRQ/turn 列，保证数据适配层
        #（duckdb_data_access.py:99 SELECT peTTM/pbMRQ/turn）拿到非 NULL 值，避免回测层歧义。
        # valuation_df 为 None（依赖表失败/ETF 无依赖）时留 NULL，不阻断（与 is_delisting_risk 同款容错）。
        # 字段映射：valuation.pe_ttm→peTTM, valuation.pb→pbMRQ, valuation.turnover_rate→turn。
        # psTTM 无对应源（valuation 表无），留 NULL（存量 tushare 段保留有值）。
        if table in ("stock_daily", "etf_daily"):
            applied_steps.append(self._derive_valuation_fields(df, valuation_df))

        # ---- Step 4.6: 价格/成交量/金额 舍入 ----
        # A 股原始成交价：2 位小数（报价单位 0.01 元）
        # 成交量：整数（股）
        # 成交额：2 位小数
        # 涨跌幅：4 位小数（0.01% 精度）
        # 复权价/估值指标：不处理（保留计算精度）
        self._round_fields(df, table=table)
        applied_steps.append("round_fields")

        # ---- Step 4.5 [补丁3]: suspendFlag 推导（volume==0 → 1，否则 0）----
        if "suspendFlag" not in df.columns and "volume" in df.columns:
            df["suspendFlag"] = (pd.to_numeric(df["volume"], errors="coerce") == 0).astype(int)
            applied_steps.append("derive_suspendFlag")
        else:
            applied_steps.append("suspendFlag_skip")

        # ---- Step 5: 复权价填充（front 4 + back 4，ratio 8 填 NULL）[补丁2]----
        # baostock: adapter 已提供 front/back 列（3 次 adjustflag 调用），直通
        # tushare: 用 adj_factor 计算 front/back
        qfq_applied = False
        native_price_cols = {f"{price}_{side}" for price in ("open", "high", "low", "close")
                             for side in ("front", "back")}
        has_front_back = bool(native_price_cols.intersection(raw_df.columns) or
                              native_price_cols.intersection(df.columns))
        native_adjustment_sources = {"baostock", "akshare", "xtquant"}
        if table in ("stock_daily", "stock_minutes", "etf_daily", "etf_minutes"):
            if has_front_back and source in native_adjustment_sources:
                # 原生复权源直通；不再叠加 adj_factor，避免重复复权。
                df = self._fill_ratio_null(df)
                qfq_applied = True
                applied_steps.append(f"qfq_native_passthrough:{source}")
            elif adj_factor_df is not None:
                df = self._apply_qfq(df, adj_factor_df, table,
                                     adj_latest_map=adj_latest_map,
                                     adj_earliest_map=adj_earliest_map)
                qfq_applied = True
        if not qfq_applied:
            applied_steps.append("qfq_skip")

        # ---- Step 6 [E-3]: pctChg 透传/计算 ----
        pctchg_source = mapping.get("pctchg_source", "compute_from_raw")
        df, pctchg_actual = self._preserve_pctchg(df, table, pctchg_source)
        applied_steps.append(f"pctChg:{pctchg_actual}")

        # ---- 附加：freq 标记（分钟表）----
        if table in ("stock_minutes", "etf_minutes"):
            freq_out = freq or mapping.get("freq_out", "1min")
            df["freq"] = freq_out

        # ---- 附加：dividend_type / update_time 默认值 [补丁2]----
        if "dividend_type" in schema["columns"] and "dividend_type" not in df.columns:
            df["dividend_type"] = "all"
        if "update_time" in schema["columns"] and "update_time" not in df.columns:
            df["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        metadata = {
            "schema_version": schema.get("schema_version", self.schema_version),
            "table": table,
            "source": source,
            "applied_steps": applied_steps,
            "pctchg_source": pctchg_actual,
            "qfq_applied": qfq_applied,
            "rows_in": len(raw_df),
            "rows_out": len(df),
            "aligned_at": datetime.now().isoformat(),
        }
        logger.info(f"[FieldAligner] {source}/{table} aligned: {len(raw_df)}→{len(df)} rows, steps={applied_steps}")
        return df, metadata

    # ---------------- 私有方法 ----------------

    def _get_mapping(self, table: str, source: str, freq: Optional[str] = None) -> Dict:
        """取该 source 对该 table 的映射规则；identity=true 则返回空映射。

        lookup 顺序：精确 key 优先，fallback 次（如 stock_minutes_5min → stock_minutes）。
        命中后若 identity=true，返回仅含 time_to_ms 的空映射（字段名不变，仅时间转 ms）。
        """
        src_maps = self.source_mappings.get(source, {})
        lookup_keys = []
        if freq:
            lookup_keys.append(f"{table}_{freq}")
        lookup_keys.append(table)
        # 优先取精确 key，再取 fallback；全不命中才报 KeyError
        m = None
        for k in lookup_keys:
            if k in src_maps:
                m = src_maps[k]
                break
        if m is None:
            raise KeyError(f"无 {source}/{table} 的映射规则，请检查 alignment_rules.json")
        if m.get("identity"):
            # identity 映射：字段名不变（adapter 已预重命名），但保留 time_to_ms
            # （xtquant 财务表 end_date/ann_date 是 YYYYMMDD 字符串/整数，需转 ms）
            return {"column_map": {}, "code_format": "identity",
                    "unit_conversions": {}, "pctchg_source": "official",
                    "time_to_ms": m.get("time_to_ms", False)}
        return m

    def _map_columns(self, df: pd.DataFrame, mapping: Dict) -> pd.DataFrame:
        column_map = mapping.get("column_map", {})
        if not column_map:
            return df.copy()
        # 仅映射存在的列
        rename = {k: v for k, v in column_map.items() if k in df.columns}
        return df.rename(columns=rename).copy()

    def _find_code_col(self, df: pd.DataFrame, schema: Dict) -> Optional[str]:
        # 主键第一个通常是 code
        pk = schema.get("primary_key", ["code"])
        code_col = pk[0] if pk else "code"
        return code_col if code_col in df.columns else None

    def _round_fields(self, df: pd.DataFrame, table: str = "stock_daily"):
        """Round raw quote fields according to the instrument tick size.

        Stock bars keep two decimals; exchange-traded funds keep three decimals.
        Reducing ETF prices to two decimals distorts signals, valuation, and order sizing.
        """
        price_decimals = 3 if table in ("etf_daily", "etf_minutes") else 2
        for col in ["open", "high", "low", "close", "preClose"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").round(price_decimals)
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").round(0).astype("Float64")
        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").round(2)
        if "pctChg" in df.columns:
            df["pctChg"] = pd.to_numeric(df["pctChg"], errors="coerce").round(4)

    def _derive_market_value(self, df: pd.DataFrame, close_df: pd.DataFrame) -> str:
        """补算 circ_mv/total_mv（= 股本 × 报告期末日收盘价）。

        修正1：end_date 可能是非交易日（周末/假期），取 ≤ end_date 的最近交易日 close
              （向历史方向找最近成交日）。
        修正2：源已提供 circ_mv 时，补算后比对，差异 >5% 则 warning（不拒绝）。

        性能：close_df 行数 > 5 万时走 DuckDB ASOF JOIN（1.2s/950 万行），
              否则 fallback 到 pandas 逐组 merge_asof（兼容小数据/测试）。
              实测全市场（5201 codes × 8.5 年 close）pandas 路径要 348s，DuckDB 1.2s。

        close_df 列：code/time(ms 时间戳)/close
        df 的 end_date 此时已是 ms 时间戳（Step 3 已 time_to_ms）。
        返回 applied_step 标签。
        """
        if "free_share" not in df.columns or len(df) == 0:
            return "derive_mv_skip_no_share"
        try:
            close_aligned = self._align_close_to_end_date(df, close_df)
            free = pd.to_numeric(df["free_share"], errors="coerce")
            calc_mv = free * close_aligned  # index 与原 df 对齐
            # 修正2：源已有 circ_mv 时比对告警
            if "circ_mv" in df.columns:
                src_mv = pd.to_numeric(df["circ_mv"], errors="coerce")
                # 只对两者都有值的行比对
                valid = calc_mv.notna() & src_mv.notna() & (src_mv > 0)
                if valid.any():
                    dev = (calc_mv[valid] - src_mv[valid]).abs() / src_mv[valid]
                    big_dev = dev[dev > 0.05]
                    if len(big_dev) > 0:
                        logger.warning(
                            f"[Aligner] stock_float_share circ_mv 源值 vs 补算差异 >5%: "
                            f"{len(big_dev)} 行（可能源口径不一致）")
                # 源已有值则保留源值（不覆盖），仅补缺失
                miss = df["circ_mv"].isna() | (pd.to_numeric(df["circ_mv"], errors="coerce") <= 0)
                df.loc[miss & calc_mv.notna(), "circ_mv"] = calc_mv[miss & calc_mv.notna()]
            else:
                df["circ_mv"] = calc_mv
            # total_mv 同理（total_share × close）
            if "total_share" in df.columns:
                total = pd.to_numeric(df["total_share"], errors="coerce")
                calc_tmv = total * close_aligned
                if "total_mv" in df.columns:
                    miss = df["total_mv"].isna() | (pd.to_numeric(df["total_mv"], errors="coerce") <= 0)
                    df.loc[miss & calc_tmv.notna(), "total_mv"] = calc_tmv[miss & calc_tmv.notna()]
                else:
                    df["total_mv"] = calc_tmv
            n_filled = df["circ_mv"].notna().sum()
            logger.debug(f"[Aligner] derive_market_value: circ_mv 补算后 {n_filled}/{len(df)} 行有值")
            return "derive_mv_done"
        except Exception as e:
            logger.warning(f"[Aligner] derive_market_value 失败（跳过）: {e}")
            return "derive_mv_error"

    def _align_close_to_end_date(self, df: pd.DataFrame, close_df: pd.DataFrame) -> pd.Series:
        """对 df 每行 (code, end_date) 找 close_df 中 ≤ end_date 的最近交易日 close。

        返回 Series（index 对齐 df.index，列名 'close'）。
        路由：close_df 行数 > 5 万 → DuckDB ASOF JOIN（快）；
              否则 → pandas 逐组 merge_asof（兼容测试/小数据）。
        """
        import pandas as pd
        # 数值类型规范化
        df_end = pd.to_numeric(df["end_date"], errors="coerce")
        # 阈值：实测 5 万行以下 pandas 路径 <1s，超过走 DuckDB
        if len(close_df) > 50_000:
            return self._align_close_duckdb(df, df_end, close_df)
        return self._align_close_pandas(df, df_end, close_df)

    def _align_close_duckdb(self, df: pd.DataFrame, df_end: pd.Series,
                             close_df: pd.DataFrame) -> pd.Series:
        """DuckDB ASOF JOIN 路径（大数据快路径）。

        把 df + close_df 注册为临时视图，ASOF LEFT JOIN 取 time<=end_date 最近 close。
        不依赖具体表名（数据全从入参 pandas 来），保持 aligner 解耦。
        """
        import pandas as pd
        import duckdb
        # 准备临时视图（仅含必要列，避免类型冲突）
        left = pd.DataFrame({
            "_row_id": range(len(df)),   # 用于结果回填到 df 原顺序
            "code": df["code"].values,
            "end_date": df_end.values,
        })
        right = pd.DataFrame({
            "code": close_df["code"].values,
            "time": pd.to_numeric(close_df["time"], errors="coerce").values,
            "close": pd.to_numeric(close_df["close"], errors="coerce").values,
        })
        # DuckDB 要求 ASOF JOIN 的右表按 join key 排序
        right = right.sort_values(["code", "time"])
        conn = duckdb.connect()
        try:
            conn.register("_fs_left", left)
            conn.register("_sd_right", right)
            joined = conn.execute("""
                SELECT l._row_id, r.close
                FROM _fs_left l
                ASOF LEFT JOIN _sd_right r
                    ON l.code = r.code
                   AND l.end_date >= r.time
            """).fetchdf()
        finally:
            conn.close()
        # 按 _row_id 排序回到 df 原顺序
        joined = joined.sort_values("_row_id")
        close_series = pd.Series(pd.to_numeric(joined["close"], errors="coerce").values,
                                  index=df.index, name="close")
        return close_series

    def _align_close_pandas(self, df: pd.DataFrame, df_end: pd.Series,
                             close_df: pd.DataFrame) -> pd.Series:
        """pandas 逐组 merge_asof 路径（小数据/测试兼容路径）。

        关键陷阱：pandas merge_asof(by=code) 仍要求整列 _t 全局单调（不只组内），
        多 code 时按 (code, _t) 排序后 _t 跨 code 不单调 → 报 "left keys must be sorted"。
        解决：按 code 分组，每组单独做 merge_asof（单组内 _t 已升序），结果按原 index 拼回。
        """
        import pandas as pd
        df_work = df.copy()
        df_work["_t"] = df_end
        close_work = close_df.copy().rename(columns={"time": "_t"})
        close_work["_t"] = pd.to_numeric(close_work["_t"], errors="coerce")
        close_work["close"] = pd.to_numeric(close_work["close"], errors="coerce")
        close_work = close_work.sort_values(["code", "_t"])

        pieces = []
        for c, g in df_work.groupby("code", sort=False):
            g_sorted = g.sort_values("_t")
            cd = close_work[close_work["code"] == c][["_t", "close"]]
            if len(cd) == 0:
                g_sorted["close"] = pd.NA
            else:
                merged = pd.merge_asof(g_sorted, cd, on="_t", direction="backward")
                g_sorted["close"] = merged["close"].values
            pieces.append(g_sorted)
        df_with_close = pd.concat(pieces).reindex(df.index)
        return pd.to_numeric(df_with_close["close"], errors="coerce")

    def _derive_st_status(self, df: pd.DataFrame,
                          namechange_df: Optional[pd.DataFrame],
                          valuation_df: Optional[pd.DataFrame]) -> str:
        """为 stock_daily 推导 is_st_reliable + is_delisting_risk（4 个新字段）。

        设计目标：在数据层解决 isST 不可靠问题（xtquant isST 失真，002231 实为 *ST 但标 0），
        让回测层 filter_stock_by_status 只需读字段，不做临时处理。

        is_st_reliable：PIT 查 stock_namechange 找 ≤df.time 最近变更记录，
                       status_after ∈ {ST, *ST} → True，source='namechange'
                       无变更记录（深市缺/沪市/正常股）→ False，source='none'
        is_delisting_risk：兜底，三条规则任一触发：
                       1. close < 1.0 元（面值退市线）→ source 含 'price'
                       2. 近 20 日 circ_mv < 5e8 元（市值退市线）→ source 含 'market_cap'
                       两者都触发 → source='both'；都不触发 → False, source='none'

        职责分离：is_st_reliable 只放官方 namechange 状态，不混兜底结果；
                  is_delisting_risk 独立判定，两者可同时为 True。

        返回 applied_step 标签。
        """
        import pandas as pd
        if len(df) == 0:
            return "derive_st_skip_empty"

        # 字段初始化（防止 KeyError，让 writer 能正常入库）
        df["is_st_reliable"] = False
        df["is_st_reliable_source"] = "none"
        df["is_delisting_risk"] = False
        df["is_delisting_risk_source"] = "none"

        # ---- is_st_reliable：namechange PIT 查询 ----
        if namechange_df is not None and len(namechange_df) > 0 and "code" in df.columns:
            try:
                # DuckDB ASOF JOIN 批量算（性能模式，大数据量时走这条）
                st_flags = self._pit_st_flags_duckdb(df, namechange_df)
                if st_flags is not None:
                    df["is_st_reliable"] = st_flags["is_st"].values
                    df.loc[df["is_st_reliable"], "is_st_reliable_source"] = "namechange"
            except Exception as e:
                logger.warning(f"[Aligner] derive_st_status namechange PIT 失败（is_st_reliable 留 False）: {e}")

        # ---- is_delisting_risk：close<1 + 近 20 日 circ_mv<5 亿 ----
        try:
            # 规则 1：close < 1.0 元（来自 df 本身，已在 Step 2 数值化）
            if "close" in df.columns:
                close_val = pd.to_numeric(df["close"], errors="coerce")
                price_risk = (close_val < 1.0) & close_val.notna()
            else:
                price_risk = pd.Series([False] * len(df), index=df.index)

            # 规则 2：近 20 日 circ_mv < 5e8 元（需要 valuation_df + df.time PIT 对齐）
            market_cap_risk = pd.Series([False] * len(df), index=df.index)
            if valuation_df is not None and len(valuation_df) > 0 and "time" in df.columns:
                mc_risk_flags = self._pit_market_cap_risk(df, valuation_df)
                if mc_risk_flags is not None:
                    market_cap_risk = mc_risk_flags

            # 合并：source 字段编码 price/market_cap/both/none
            both = price_risk & market_cap_risk
            only_price = price_risk & ~market_cap_risk
            only_mc = market_cap_risk & ~price_risk

            df["is_delisting_risk"] = price_risk | market_cap_risk
            df.loc[only_price, "is_delisting_risk_source"] = "price"
            df.loc[only_mc, "is_delisting_risk_source"] = "market_cap"
            df.loc[both, "is_delisting_risk_source"] = "both"
        except Exception as e:
            logger.warning(f"[Aligner] derive_st_status delisting_risk 失败（留 False）: {e}")

        n_st = int(df["is_st_reliable"].sum())
        n_dr = int(df["is_delisting_risk"].sum())
        logger.debug(f"[Aligner] derive_st_status: is_st_reliable={n_st}, is_delisting_risk={n_dr} / {len(df)}")
        return f"derive_st_done(st={n_st},dr={n_dr})"

    def _derive_valuation_fields(self, df: pd.DataFrame,
                                  valuation_df: Optional["pd.DataFrame"]) -> str:
        """为 stock_daily/etf_daily 补 peTTM/pbMRQ/turn 列（xtquant 切源后的估值字段补全）。

        背景：xtquant 不提供 peTTM/pbMRQ/psTTM/turn（tushare 时代来自 daily_basic）。
        stock_daily_valuation 表（tushare daily_basic 前置依赖）含 pe_ttm/pb/turnover_rate，
        用 PIT ASOF JOIN（≤time 最近一条，与 namechange 同语义）补到 stock_daily 对应列：
          valuation.pe_ttm       → df.peTTM
          valuation.pb           → df.pbMRQ
          valuation.turnover_rate→ df.turn
        psTTM 无对应源（valuation 表无该列），留 NULL。

        valuation_df 为 None 或缺字段时留 NULL，不阻断（容错，与 _derive_st_status 同款）。
        幂等：若 df 已有 peTTM 列且非空（如 tushare 直传），不覆盖（保留源原值）。
        """
        import pandas as pd
        # 初始化目标列（缺失时建空列，保证 writer 不 KeyError）
        for col in ("peTTM", "pbMRQ", "turn"):
            if col not in df.columns:
                df[col] = pd.NA

        if valuation_df is None or len(valuation_df) == 0:
            return "derive_valuation_skip_no_data"
        if "code" not in df.columns or "time" not in df.columns:
            return "derive_valuation_skip_no_key"
        # valuation_df 必须含 pe_ttm/pb/turnover_rate 至少一个才有意义
        val_fields = [f for f in ("pe_ttm", "pb", "turnover_rate") if f in valuation_df.columns]
        if not val_fields:
            return "derive_valuation_skip_no_fields"

        try:
            import duckdb
            left = pd.DataFrame({
                "_row_id": range(len(df)),
                "code": df["code"].values,
                "time": pd.to_numeric(df["time"], errors="coerce").values,
            })
            # ASOF JOIN 要求右表按 (code, time) 排序
            right_cols = ["code", "time"] + val_fields
            right = valuation_df[right_cols].copy()
            right["time"] = pd.to_numeric(right["time"], errors="coerce")
            for f in val_fields:
                right[f] = pd.to_numeric(right[f], errors="coerce")
            right = right.dropna(subset=["time"]).sort_values(["code", "time"])

            conn = duckdb.connect()
            try:
                conn.register("_sd_left", left)
                conn.register("_val_right", right)
                # ASOF JOIN：每行找同 code 且 time ≤ df.time 的最近一条估值
                joined = conn.execute("""
                    SELECT l._row_id, r.pe_ttm, r.pb, r.turnover_rate
                    FROM _sd_left l
                    ASOF JOIN _val_right r
                        ON l.code = r.code
                       AND l.time >= r.time
                """).fetchdf()
            finally:
                conn.close()

            if len(joined) == 0:
                return "derive_valuation_skip_join_empty"
            joined = joined.sort_values("_row_id").reset_index(drop=True)

            # 仅在原值为空时补（幂等，不覆盖源已有值）
            if "pe_ttm" in val_fields:
                mask = df["peTTM"].isna() & joined["pe_ttm"].notna()
                df.loc[mask, "peTTM"] = joined.loc[mask, "pe_ttm"].values
            if "pb" in val_fields:
                mask = df["pbMRQ"].isna() & joined["pb"].notna()
                df.loc[mask, "pbMRQ"] = joined.loc[mask, "pb"].values
            if "turnover_rate" in val_fields:
                mask = df["turn"].isna() & joined["turnover_rate"].notna()
                df.loc[mask, "turn"] = joined.loc[mask, "turnover_rate"].values

            n_pe = int(df["peTTM"].notna().sum())
            n_pb = int(df["pbMRQ"].notna().sum())
            n_turn = int(df["turn"].notna().sum())
            logger.debug(f"[Aligner] derive_valuation_fields: peTTM={n_pe}, pbMRQ={n_pb}, "
                         f"turn={n_turn} / {len(df)} (PIT ASOF JOIN valuation)")
            return f"derive_valuation_done(pe={n_pe},pb={n_pb},turn={n_turn})"
        except Exception as e:
            logger.warning(f"[Aligner] derive_valuation_fields PIT JOIN 失败（peTTM/pbMRQ/turn 留 NULL）: {e}")
            return "derive_valuation_failed"

    def _pit_st_flags_duckdb(self, df: pd.DataFrame, namechange_df: pd.DataFrame) -> Optional["pd.DataFrame"]:
        """用 DuckDB ASOF JOIN 批量算每行 stock_daily 的 is_st_reliable。

        ASOF JOIN 语义：对每行 (code, time) 找 stock_namechange 中 ≤time 的最近一条变更，
        status_after ∈ {ST, *ST} → True。
        """
        import pandas as pd
        import duckdb
        left = pd.DataFrame({
            "_row_id": range(len(df)),
            "code": df["code"].values,
            "time": pd.to_numeric(df["time"], errors="coerce").values,
        })
        right = namechange_df[["code", "change_date", "status_after"]].copy()
        right = right.rename(columns={"change_date": "_t"})
        right["_t"] = pd.to_numeric(right["_t"], errors="coerce")
        right = right.dropna(subset=["_t"]).sort_values(["code", "_t"])

        conn = duckdb.connect()
        try:
            conn.register("_sd_left", left)
            conn.register("_nc_right", right)
            joined = conn.execute("""
                SELECT l._row_id,
                       CASE WHEN r.status_after IN ('ST', '*ST') THEN TRUE ELSE FALSE END AS is_st
                FROM _sd_left l
                ASOF LEFT JOIN _nc_right r
                    ON l.code = r.code
                   AND l.time >= r._t
            """).fetchdf()
        finally:
            conn.close()
        joined = joined.sort_values("_row_id").reset_index(drop=True)
        return joined[["is_st"]]

    def _pit_market_cap_risk(self, df: pd.DataFrame, valuation_df: pd.DataFrame) -> Optional["pd.Series"]:
        """判断每行 stock_daily 是否触发市值退市风险（近 20 日 circ_mv 最小值 < 5e8）。

        用 DuckDB 一次性算：对每行 (code, time) 查 stock_daily_valuation 中
        time ∈ [time-20d, time] 的 MIN(circ_mv)，若 < 5e8 则 True。
        """
        import pandas as pd
        import duckdb
        left = pd.DataFrame({
            "_row_id": range(len(df)),
            "code": df["code"].values,
            "time": pd.to_numeric(df["time"], errors="coerce").values,
        })
        right = valuation_df[["code", "time", "circ_mv"]].copy()
        right["time"] = pd.to_numeric(right["time"], errors="coerce")
        right["circ_mv"] = pd.to_numeric(right["circ_mv"], errors="coerce")
        right = right.dropna(subset=["time", "circ_mv"])

        conn = duckdb.connect()
        try:
            conn.register("_sd_left", left)
            conn.register("_val_right", right)
            # 关联 + 聚合：每行 stock_daily 找近 20 日（20*86400000 ms）的 MIN(circ_mv)
            joined = conn.execute("""
                SELECT l._row_id,
                       CASE WHEN MIN(r.circ_mv) < 5e8 AND COUNT(r.circ_mv) > 0
                            THEN TRUE ELSE FALSE END AS is_risk
                FROM _sd_left l
                LEFT JOIN _val_right r
                    ON l.code = r.code
                   AND r.time BETWEEN l.time - 20*86400000 AND l.time
                GROUP BY l._row_id
            """).fetchdf()
        finally:
            conn.close()
        joined = joined.sort_values("_row_id").reset_index(drop=True)
        return pd.Series(joined["is_risk"].values, index=df.index)

    def _fill_ratio_null(self, df: pd.DataFrame) -> pd.DataFrame:
        """等比复权 8 列填 NULL（Phase 2 不实现等比复权）"""
        ratio_cols = [f"{p}_{s}_ratio" for p in ["open", "high", "low", "close"]
                      for s in ["front", "back"]]
        for c in ratio_cols:
            if c not in df.columns:
                df[c] = None
        return df

    def _apply_qfq(self, df: pd.DataFrame, adj_df: pd.DataFrame, table: str,
                   adj_latest_map: dict = None, adj_earliest_map: dict = None) -> pd.DataFrame:
        """计算 8 种复权价填入对应列（补丁2）：
           front 4 列: price_front = price_raw × adj_i / adj_latest
           back 4 列:  price_back  = price_raw × adj_i / adj_earliest
           ratio 8 列: 填 NULL（等比复权延后到 Phase 4）

        adj_latest_map/adj_earliest_map: 全量快照（从 qfq_aux.db 读），
        存在时替代批次内 groupby max/min（修复 per_trade_date 复权失效）。
        """
        ohlc = ["open", "high", "low", "close"]
        available = [c for c in ohlc if c in df.columns]
        if not available:
            return df
        time_field = self.schemas[table].get("time_key", "time")
        code_col = "code"

        # 规整 adj_df 列名
        adj = adj_df.copy()
        if code_col not in adj.columns:
            adj = adj.rename(columns={adj.columns[0]: code_col})
        if "adj_factor" not in adj.columns:
            logger.warning("[QFQ] adj_factor_df 缺 adj_factor 列，跳过 QFQ")
            return df

        # 复权因子是日频。分钟 bar 的 time 含盘中时刻，必须按交易日连接，
        # 不能与午夜时间戳直接等值连接。
        bar_day = pd.to_datetime(df[time_field], unit="ms", utc=True).dt.tz_convert(
            "Asia/Shanghai").dt.strftime("%Y-%m-%d")
        adj_day = pd.to_datetime(adj[time_field], unit="ms", utc=True).dt.tz_convert(
            "Asia/Shanghai").dt.strftime("%Y-%m-%d")
        left = df.copy()
        right = adj[[code_col, time_field, "adj_factor"]].copy()
        left["_adj_day"] = bar_day
        right["_adj_day"] = adj_day
        right = right.sort_values(time_field).drop_duplicates([code_col, "_adj_day"], keep="last")
        merged = left.merge(right[[code_col, "_adj_day", "adj_factor"]],
                            on=[code_col, "_adj_day"], how="left")
        if merged["adj_factor"].isna().any():
            n_miss = merged["adj_factor"].isna().sum()
            logger.warning(f"[QFQ] {n_miss} rows missing adj_factor, 复权价留 NULL")

        # 按 code 分组求 latest/earliest adj_factor
        # 如果传入了快照 map（全量模式），用快照替代批次内 groupby（修复 per_trade_date 复权失效）
        if adj_latest_map and adj_earliest_map:
            merged["_adj_latest"] = merged[code_col].map(adj_latest_map)
            merged["_adj_earliest"] = merged[code_col].map(adj_earliest_map)
            # 快照里没有的 code，回退到批次内 max/min
            anchors = (adj.sort_values(time_field).groupby(code_col)["adj_factor"]
                       .agg(adj_earliest="first", adj_latest="last"))
            merged["_adj_latest"] = merged["_adj_latest"].fillna(
                merged[code_col].map(anchors["adj_latest"]))
            merged["_adj_earliest"] = merged["_adj_earliest"].fillna(
                merged[code_col].map(anchors["adj_earliest"]))
            adj_latest = merged["_adj_latest"]
            adj_earliest = merged["_adj_earliest"]
        else:
            anchors = (adj.sort_values(time_field).groupby(code_col)["adj_factor"]
                       .agg(adj_earliest="first", adj_latest="last"))
            adj_latest = merged[code_col].map(anchors["adj_latest"])
            adj_earliest = merged[code_col].map(anchors["adj_earliest"])

        # front: price × adj_i / adj_latest（前复权，基准=最新）
        for c in available:
            front_col = f"{c}_front"
            merged[front_col] = merged[c] * merged["adj_factor"] / adj_latest
        # back: price × adj_i / adj_earliest（后复权，基准=最早）
        for c in available:
            back_col = f"{c}_back"
            merged[back_col] = merged[c] * merged["adj_factor"] / adj_earliest
        # ratio 8 列填 NULL（Phase 4 实现）
        for c in available:
            merged[f"{c}_front_ratio"] = None
            merged[f"{c}_back_ratio"] = None

        result = merged.drop(columns=["adj_factor"]) if "adj_factor" not in df.columns else merged
        # 清理临时列
        for tmp_col in ["_adj_latest", "_adj_earliest", "_adj_day"]:
            if tmp_col in result.columns:
                result = result.drop(columns=[tmp_col])
        # ratio 8 列填 NULL（Phase 2 不实现等比复权）
        return self._fill_ratio_null(result)

    def _preserve_pctchg(self, df: pd.DataFrame, table: str, declared: str) -> Tuple[pd.DataFrame, str]:
        """[E-3] 真实涨跌幅统一用 pctChg 字段（khQuant 命名）

        优先级（严禁用不复权 close/preClose 直接除算，除权日会产生 ±1000% 垃圾值）：
          1. official    : 源已提供 pctChg（如 tushare/akshare/baostock/xtquant 官方涨跌幅）→ 直接保留
          2. derived_front: 源缺官方 pctChg，但已有 QFQ 前复权价 close_front
                           → 用 close_front 按 code 分组 pct_change 推导真实收益率
                             （close_front 已含复权，推导值即真实日收益，正确）
          3. computed_preClose: 既无官方 pctChg 也无 close_front，但 close/preClose 同基准
                           → 仅作最后兜底（同源同基准时才正确，混合基准会失真，故降为末选）
          4. missing     : 都无法推导 → 留 NULL，不制造垃圾值
        """
        schema_cols = self.schemas[table].get("columns", {})
        if "pctChg" not in schema_cols:
            return df, "not_required"

        if "pctChg" in df.columns:
            # 已有官方值，清理（可能含 % 字符）
            official = pd.to_numeric(
                df["pctChg"].astype(str).str.replace("%", "", regex=False),
                errors="coerce")
            df["pctChg"] = official

            # ---- 垃圾值检测（补丁 2026-07-19）：某些源（tushare）的官方 pct_chg
            #     对个别股票存在 ±1000%+ 的离群值（如 603395=+1917%），而 close_front
            #     推导的真实日收益仅 ~3%。若 close_front 可用，交叉校验并修复。
            #     阈值：|official|>200% 且 |derived|<30% → 判定为垃圾，改用 derived。
            if "close_front" in df.columns:
                basis = df
                if "code" in df.columns and "time" in df.columns:
                    basis = df.sort_values(["code", "time"])
                derived = basis.groupby("code")["close_front"].pct_change() * 100
                derived.index = basis.index
                derived = derived.reindex(df.index)

                # 判定垃圾：官方极端值 + derived 正常值 → 官方不可信，用 derived 修复
                fixed_mask = (official.abs() > 200) & (derived.abs() < 30) & derived.notna()
                n_garbage = int(fixed_mask.sum())
                if n_garbage:
                    df.loc[fixed_mask, "pctChg"] = derived[fixed_mask]

                # [补漏 2026-07-19] 首行 / per_date 单日批次：批次内无前一日，
                # close_front.pct_change() 全为 NaN，原 gate(derived.notna()) 直接跳过，
                # 导致官方垃圾值漏杀（如 603395=+1917%）。此处查 DB 前一日 close_front 兜底。
                nan_extreme = (official.abs() > 200) & derived.isna()
                n_db_fixed = 0
                n_nulled = 0
                if int(nan_extreme.sum()):
                    if self.db_path is not None:
                        n_db_fixed = self._fix_pctchg_from_db(df, nan_extreme)
                        n_garbage += n_db_fixed
                        # DB 仍无前日（=首行，如 603395/920227 的 IPO 首日垃圾值）→
                        # 官方垃圾值无法推导，按「missing → NULL」原则置空，避免污染因子。
                        remain = nan_extreme & (df["pctChg"].abs() > 200)
                        n_nulled = int(remain.sum())
                        if n_nulled:
                            df.loc[remain, "pctChg"] = None
                    else:
                        # 无 DB 兜底，无法判定首行/单日批次，保守置 NULL 防垃圾值入库
                        n_nulled = int(nan_extreme.sum())
                        df.loc[nan_extreme, "pctChg"] = None

                if n_garbage or n_nulled:
                    logger.info(
                        f"[E-3] {table}: 官方 pctChg 检测到 {n_garbage + n_nulled} 行垃圾值(>|200|%), "
                        f"已修复（批次内 {int(fixed_mask.sum())} + DB兜底 {n_db_fixed}）+ 置NULL {n_nulled}。"
                    )
                    return df, "official_garbage_fixed"
            return df, "official"

        # 2. 用 QFQ 前复权价推导真实收益率（正确，优先于不复权 close/preClose）
        if "close_front" in df.columns:
            basis = df
            if "code" in df.columns and "time" in df.columns:
                basis = df.sort_values(["code", "time"])
            derived = basis.groupby("code")["close_front"].pct_change() * 100
            derived.index = basis.index
            df["pctChg"] = derived.reindex(df.index)
            return df, "derived_from_front"

        # 3. 最后兜底：close/preClose 同基准（仅同源同口径时正确）
        if "close" in df.columns and "preClose" in df.columns:
            df["pctChg"] = (df["close"] / df["preClose"] - 1) * 100
            return df, "computed_from_preClose"

        if declared == "compute_from_raw" and "close" in df.columns:
            logger.warning(
                f"[E-3 WARNING] {table}: 无 pctChg/close_front，退化用 close.pct_change()。"
                f"存在复权跳变风险，建议源提供官方 pctChg。")
            df["pctChg"] = df.groupby("code")["close"].pct_change() * 100
            return df, "fallback_close_pct_change"

        return df, "missing"

    def _fix_pctchg_from_db(self, df: pd.DataFrame, mask: pd.Series) -> int:
        """pctChg 垃圾值 DB 兜底推导 [补漏 2026-07-19]

        对 derived 为 NaN（批次内无前一日，如首行 / per_date 单日批次）但官方 pctChg
        仍极端的行，查 Canonical 库 stock_daily 中该 code 前一交易日 close_front，
        用 (cur - prev)/prev 推导真实日收益并修复。

        返回修复行数。查库失败（并发锁/连接异常）则优雅降级返回 0，不阻断主流程。
        """
        import duckdb

        cand = df[mask]
        need = ("code", "time", "close_front")
        if not all(c in cand.columns for c in need):
            return 0
        cand = cand.dropna(subset=list(need))
        if len(cand) == 0:
            return 0
        try:
            # 优先用注入的 shared_conn（采集流程内，避免 read_only 与 write 并发冲突）；
            # 无 provider 则降级自开 read_only（CLI/回测，无并发 write 不冲突）。
            own_conn = None
            if self.shared_conn_provider is not None:
                con = self.shared_conn_provider()
            else:
                con = duckdb.connect(str(self.db_path), read_only=True)
                own_conn = con
            n_fixed = 0
            for rid in cand.index:
                code = str(df.loc[rid, "code"])
                t = int(pd.to_numeric(df.loc[rid, "time"], errors="coerce"))
                cur = float(pd.to_numeric(df.loc[rid, "close_front"], errors="coerce"))
                row = con.execute(
                    "SELECT close_front FROM stock_daily WHERE code=? AND time < ? "
                    "ORDER BY time DESC LIMIT 1",
                    (code, t)).fetchone()
                if row is None or row[0] is None:
                    continue
                prev = float(row[0])
                if prev == 0:
                    continue
                derived = (cur - prev) / prev * 100.0
                if abs(derived) < 30:
                    df.loc[rid, "pctChg"] = derived
                    n_fixed += 1
            if own_conn is not None:
                own_conn.close()
            if n_fixed:
                logger.info(f"[E-3] pctChg DB 兜底修复 {n_fixed} 行（close_front 前一日推导）")
            return n_fixed
        except Exception as e:
            logger.warning(f"[E-3] pctChg DB 兜底失败（跳过，不阻断）: {e}")
            return 0


# ---------------------------------------------------------------------------
# 自检入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 简单冒烟测试
    logging.basicConfig(level=logging.INFO)
    here = Path(__file__).resolve().parent.parent.parent  # 项目根目录
    aligner = FieldAligner.from_config(here / "config" / "alignment_rules.json")

    # 模拟 baostock 日线
    bs_df = pd.DataFrame({
        "code": ["sh.600000", "sh.600000"], "date": ["20260710", "20260711"],
        "open": [10.0, 10.5], "high": [10.2, 10.8], "low": [9.9, 10.3],
        "close": [10.1, 10.6], "pctChg": [1.0, 4.95],
        "volume": [100000, 120000], "amount": [1010000, 1272000],
        "turn": [0.5, 0.6], "peTTM": [12.0, 11.8],
    })
    std, meta = aligner.align(bs_df, "stock_daily", "baostock")
    print("=== baostock aligned ===")
    print(std[["ts_code", "trade_date", "close", "pct_chg", "vol", "amount"]])
    print("meta:", meta)

    # 模拟 akshare 日线（中文字段）
    ak_df = pd.DataFrame({
        "股票代码": ["600000", "600000"], "日期": ["2026-07-10", "2026-07-11"],
        "开盘": [10.0, 10.5], "最高": [10.2, 10.8], "最低": [9.9, 10.3],
        "收盘": [10.1, 10.6], "涨跌幅": [1.0, 4.95],
        "成交量": [100000, 120000], "成交额": [1010000, 1272000],
    })
    std2, meta2 = aligner.align(ak_df, "stock_daily", "akshare")
    print("=== akshare aligned ===")
    print(std2[["ts_code", "trade_date", "close", "pct_chg", "vol", "amount"]])
    print("meta:", meta2)

    # 验证两源对齐后一致
    assert std["ts_code"].tolist() == std2["ts_code"].tolist(), "代码不一致"
    assert std["close"].tolist() == std2["close"].tolist(), "close 不一致"
    assert std["vol"].tolist() == std2["vol"].tolist(), "vol 不一致"
    print("\n✅ 双源对齐验证通过：字段/单位/代码一致")
