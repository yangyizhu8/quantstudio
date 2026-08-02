"""Canonical ETF-basic standardization shared by pipeline and bootstrap sync."""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

CLASSIFICATION_VERSION = "etf-basic-v1"
DATA_SOURCE = "tushare_fund_basic"
ETF_TYPES = {"equity", "bond", "money", "commodity", "gold", "qdii", "other"}
REQUIRED_RAW_COLUMNS = {"ts_code", "name", "list_date", "delist_date"}
VALID_EXCHANGES = {"SS", "SZ"}

_CROSS_BORDER_TOKENS = (
    "\u6e2f\u80a1", "\u9999\u6e2f", "\u6052\u751f", "\u6052\u6307", "H\u80a1",
    "\u6d77\u5916", "\u5168\u7403", "\u4e9a\u592a", "\u6b27\u6d32",
    "\u7eb3\u65af\u8fbe\u514b", "\u7eb3\u6307", "\u6807\u666e", "\u9053\u743c\u65af",
    "\u7f8e\u80a1", "\u7f8e\u56fd", "\u65e5\u7ecf", "\u65e5\u672c", "\u5fb7\u56fd",
    "\u6cd5\u56fd", "\u82f1\u56fd", "\u5370\u5ea6", "\u8d8a\u5357", "\u4e1c\u5357\u4e9a",
    "\u6c99\u7279", "\u65b0\u52a0\u5761", "\u97e9\u56fd", "\u4e2d\u97e9", "\u5df4\u897f",
    "\u58a8\u897f\u54e5", "\u52a0\u62ff\u5927", "\u6fb3\u6d32", "\u6fb3\u5927\u5229\u4e9a",
)
_GOLD_TOKENS = ("\u9ec4\u91d1", "\u91d1\u4ef7", "\u9ec4\u91d1\u73b0\u8d27")
_COMMODITY_TOKENS = (
    "\u539f\u6cb9", "\u8c46\u7c95", "\u767d\u94f6", "\u6709\u8272\u91d1\u5c5e\u671f\u8d27",
    "\u80fd\u6e90\u5316\u5de5", "\u5546\u54c1\u671f\u8d27", "\u5546\u54c1\u6307\u6570",
    "\u671f\u8d27\u578b",
)

BASELINE_COLUMNS = [
    "code", "ts_code", "name", "exchange", "list_date", "delist_date",
    "etf_type", "tracking_index", "is_cross_border", "status",
    "fund_type", "invest_type", "type", "classification_method",
    "classification_version", "update_time", "data_source",
]
SEMANTIC_COLUMNS = [c for c in BASELINE_COLUMNS if c != "update_time"]


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    upper = str(text or "").upper()
    return any(token.upper() in upper for token in tokens)


def _date_ms(value):
    if value is None or pd.isna(value) or not str(value).strip():
        return None
    stamp = pd.Timestamp(str(value), tz="Asia/Shanghai").normalize()
    return int(stamp.timestamp() * 1000)


def _exchange(ts_code: str) -> str:
    suffix = str(ts_code or "").upper().rsplit(".", 1)[-1]
    return {"SH": "SS", "SZ": "SZ"}.get(suffix, suffix)


def classify_etf(row: pd.Series) -> tuple[str, bool, str]:
    """Map Tushare fund metadata to the canonical QuantStudio ETF taxonomy."""
    name = str(row.get("name") or "")
    tracking = str(row.get("tracking_index") or row.get("benchmark") or "")
    fund_type = str(row.get("fund_type") or row.get("type") or "")
    invest_type = str(row.get("invest_type") or "")
    text = " ".join((name, tracking, fund_type, invest_type))
    code = str(row.get("ts_code") or row.get("code") or "").split(".", 1)[0]

    is_cross_border = code.startswith(("513", "520")) or _contains_any(
        f"{name} {tracking}", _CROSS_BORDER_TOKENS)
    if "\u8d27\u5e01" in text:
        return "money", False, "raw_type:money"
    if any(token in text for token in ("\u503a\u5238", "\u56fd\u503a", "\u653f\u91d1\u503a", "\u53ef\u8f6c\u503a")):
        return "bond", False, "raw_type:bond"
    if _contains_any(text, _GOLD_TOKENS):
        return "gold", False, "keyword:gold"
    if _contains_any(text, _COMMODITY_TOKENS) or "\u671f\u8d27" in invest_type:
        return "commodity", is_cross_border, "keyword:commodity"
    if is_cross_border:
        return "qdii", True, "keyword_or_code:cross_border"
    if "\u80a1\u7968" in fund_type or "\u80a1\u7968" in str(row.get("type") or ""):
        return "equity", False, "raw_type:equity"
    return "other", False, "fallback:other"


def build_payload(raw: pd.DataFrame, daily_bounds: Optional[pd.DataFrame] = None,
                  update_time: Optional[str] = None) -> pd.DataFrame:
    """Normalize Tushare ``fund_basic(market='E')`` output to the DB baseline."""
    if raw is None or raw.empty:
        raise RuntimeError("Tushare fund_basic returned no rows")
    missing = sorted(REQUIRED_RAW_COLUMNS - set(raw.columns))
    if missing:
        raise ValueError(f"Tushare fund_basic missing required fields: {missing}")
    data = raw.copy()
    names = data["name"].fillna("").astype(str).str.upper()
    data = data[names.str.contains("ETF", regex=False)]
    data = data[~data["name"].fillna("").astype(str).str.contains("\u8054\u63a5", regex=False)]
    data = data.drop_duplicates("ts_code", keep="first").copy()
    if data.empty:
        raise RuntimeError("Tushare fund_basic ETF filter produced no rows")

    if "code" not in data.columns:
        data["code"] = data["ts_code"]
    data["code"] = data["code"].astype(str).str.split(".").str[0].str.zfill(6)
    data["exchange"] = data["ts_code"].map(_exchange)
    if "tracking_index" not in data.columns:
        data["tracking_index"] = data.get("benchmark")

    classified = data.apply(classify_etf, axis=1, result_type="expand")
    classified.columns = ["etf_type", "is_cross_border", "classification_method"]
    data = pd.concat([data, classified], axis=1)
    data["list_date"] = data["list_date"].map(_date_ms)
    data["delist_date"] = data["delist_date"].map(_date_ms)

    if daily_bounds is not None and not daily_bounds.empty:
        bounds = daily_bounds.copy()
        data = data.merge(bounds[["code", "first_bar_ms", "last_bar_ms"]], on="code", how="left")
        data["list_date"] = data["list_date"].where(
            data["list_date"].notna(), data["first_bar_ms"])
        missing_delist = (
            data["status"].eq("D") & data["delist_date"].isna()
            & data["last_bar_ms"].notna()
        )
        data.loc[missing_delist, "delist_date"] = (
            data.loc[missing_delist, "last_bar_ms"] + 86_400_000)

    now = update_time or dt.datetime.now(
        dt.timezone(dt.timedelta(hours=8))).isoformat()
    data["update_time"] = now
    data["data_source"] = DATA_SOURCE
    data["classification_version"] = CLASSIFICATION_VERSION
    for col in ("status", "fund_type", "invest_type", "type", "tracking_index"):
        if col not in data.columns:
            data[col] = None
    payload = data[BASELINE_COLUMNS].sort_values("code").reset_index(drop=True)
    invalid_types = sorted(set(payload["etf_type"]) - ETF_TYPES)
    invalid_exchange = sorted(set(payload["exchange"]) - VALID_EXCHANGES)
    invalid_ts_code = ~payload["ts_code"].astype(str).str.match(r"^\d{6}\.(SH|SZ)$")
    if invalid_types or invalid_exchange or invalid_ts_code.any() or payload["code"].duplicated().any():
        raise ValueError(
            "etf_basic canonical quality gate failed: "
            f"invalid_types={invalid_types}, invalid_exchange={invalid_exchange}, "
            f"invalid_ts_code={int(invalid_ts_code.sum())}, "
            f"duplicate_codes={int(payload['code'].duplicated().sum())}"
        )
    return payload


# ---------------------------------------------------------------------------
# MCP 输入分支（codex 审计 TD-15 etf_basic 专项，2026-08-03）
# ---------------------------------------------------------------------------
# 背景：tushare build_payload 要求 REQUIRED_RAW_COLUMNS 含 delist_date（L12），
# 而云端 QuestDB etf_basic 无 delist_date（仅 list_status）+ 列名差异
# （list_status≠status / index_code≠tracking_index）。本函数做形状适配后
# 复用 classify_etf + daily_bounds 兜底 + quality gate，**不新写第二套分类逻辑**
# （codex 约束：fund_type/is_cross_border 派生规则只有一份）。
#
# 边界（codex 三约束）：本函数 + aligner.py L288 放宽 + alignment_rules.json
# column_map 三个文件均不在线1改动范围（mcp_adapter/daemon/writers/orchestrator/
# config_lint），零冲突并行。
MCP_DATA_SOURCE = "mcp_questdb_etf_basic"
MCP_REQUIRED_RAW_COLUMNS = {"ts_code", "name", "list_date"}  # delist_date 缺失，用 daily_bounds 兜底


def build_payload_mcp(raw: pd.DataFrame, daily_bounds: Optional[pd.DataFrame] = None,
                      update_time: Optional[str] = None) -> pd.DataFrame:
    """Normalize MCP (QuestDB etf_basic) output to the DB baseline.

    与 build_payload 的差异（云端 QuestDB etf_basic 实测列）：
      - delist_date 缺失 → daily_bounds 兜底（status=='D' 用 last_bar_ms+1d）
      - list_status → status（列名适配）
      - index_code → tracking_index（列名适配）
      - etf_type → type（classify_etf 读 type 做 fallback 分类）
    其余（classify_etf 派生 / ETF 过滤 / 去重 / quality gate）完全复用 tushare 路径。
    """
    if raw is None or raw.empty:
        raise RuntimeError("MCP etf_basic returned no rows")
    missing = sorted(MCP_REQUIRED_RAW_COLUMNS - set(raw.columns))
    if missing:
        raise ValueError(f"MCP etf_basic missing required fields: {missing}")
    data = raw.copy()

    # ---- 形状适配：云端列名 → standardizer 期望列名 ----
    if "list_status" in data.columns and "status" not in data.columns:
        data["status"] = data["list_status"]
    if "index_code" in data.columns and "tracking_index" not in data.columns:
        data["tracking_index"] = data["index_code"]
    # 注意：云端 etf_type 列值是 '纯境内'/'QDII'（跨市场分类），不是 canonical
    # ETF 类型（equity/bond/money/...）。故不映射 etf_type→type，让 classify_etf
    # 从 name/tracking_index 独立推导（classify_etf 本就支持无 type 输入的 fallback）。
    # delist_date 缺失：先置 None，后续 daily_bounds 兜底（与 tushare 路径对称）
    if "delist_date" not in data.columns:
        data["delist_date"] = None

    # ---- 以下复用 build_payload 的核心逻辑（ETF 过滤/去重/code派生/classify/daily_bounds/quality gate）----
    names = data["name"].fillna("").astype(str).str.upper()
    data = data[names.str.contains("ETF", regex=False)]
    data = data[~data["name"].fillna("").astype(str).str.contains("\u8054\u63a5", regex=False)]
    data = data.drop_duplicates("ts_code", keep="first").copy()
    if data.empty:
        raise RuntimeError("MCP etf_basic ETF filter produced no rows")

    if "code" not in data.columns:
        data["code"] = data["ts_code"]
    data["code"] = data["code"].astype(str).str.split(".").str[0].str.zfill(6)
    # exchange：云端已有 exchange 列（SS/SZ），但统一用 _exchange 从 ts_code 派生保证一致
    data["exchange"] = data["ts_code"].map(_exchange)

    classified = data.apply(classify_etf, axis=1, result_type="expand")
    classified.columns = ["etf_type", "is_cross_border", "classification_method"]
    # 云端 etf_basic 有原生 etf_type 列（值'纯境内'/'QDII'，跨市场分类非 canonical 类型），
    # 与 classified 输出的 etf_type（equity/bond/...）同名。concat 前必须 drop 云端原列，
    # 否则产生两个同名列 → quality gate 取到 DataFrame 而非 Series → set() 含字面列名。
    if "etf_type" in data.columns:
        data = data.drop(columns=["etf_type"])
    data = pd.concat([data, classified], axis=1)

    # 【codex 审计 P2：equity 分类兜底启发式】
    # classify_etf 的 'equity' 只在 fund_type/type 含"股票"时返回（tushare fund_basic
    # 提供"股票型"值），云端 etf_basic 无此列 → MCP 路径所有股票型 ETF 落入 'other'。
    # 实测 1300 个 'other' 全部有真实 tracking_index（指数代码格式如 932315.CSI），
    # 排除货币/债券/黄金/商品/QDII 后，有真实跟踪指数的 ETF 几乎全是股票型。
    # 此处仅在 MCP 分支补一行兜底（不动 classify_etf 本体），消除双模式行为差异：
    # 回测/策略按 etf_type='equity' 筛选股票型 ETF 时，MCP 模式不再拿到空集。
    # 条件：tracking_index 此时仍是原始 index_code 值（真实指数代码，尚未被 name 兜底）。
    ti_real = data["tracking_index"].notna() & (
        data["tracking_index"].astype(str).str.contains(".", regex=False, na=False))
    equity_mask = data["etf_type"].eq("other") & ti_real
    data.loc[equity_mask, "etf_type"] = "equity"
    data.loc[equity_mask, "classification_method"] = "mcp_heuristic:tracking_index_present"

    data["list_date"] = data["list_date"].map(_date_ms)
    data["delist_date"] = data["delist_date"].map(_date_ms)

    if daily_bounds is not None and not daily_bounds.empty:
        bounds = daily_bounds.copy()
        data = data.merge(bounds[["code", "first_bar_ms", "last_bar_ms"]], on="code", how="left")
        data["list_date"] = data["list_date"].where(
            data["list_date"].notna(), data["first_bar_ms"])
        missing_delist = (
            data["status"].eq("D") & data["delist_date"].isna()
            & data["last_bar_ms"].notna()
        )
        data.loc[missing_delist, "delist_date"] = (
            data.loc[missing_delist, "last_bar_ms"] + 86_400_000)

    now = update_time or dt.datetime.now(
        dt.timezone(dt.timedelta(hours=8))).isoformat()
    data["update_time"] = now
    data["data_source"] = MCP_DATA_SOURCE
    data["classification_version"] = CLASSIFICATION_VERSION
    for col in ("status", "invest_type", "type"):
        if col not in data.columns:
            data[col] = None
    # fund_type：云端 etf_basic 无此列（tushare 由 fund_basic API 提供）。
    # classify_etf 推导的 etf_type（equity/bond/money/...）在 ETF 场景与 fund_type
    # 语义一致，用它填充以满足 canonical schema required 约束（validator 逐行非空检查）。
    data["fund_type"] = data.get("fund_type", data["etf_type"])
    # tracking_index：部分 ETF 无跟踪指数（如货币基金），用 name 兜底满足 required。
    if "tracking_index" not in data.columns:
        data["tracking_index"] = None
    data["tracking_index"] = data["tracking_index"].fillna(data["name"])
    payload = data[BASELINE_COLUMNS].sort_values("code").reset_index(drop=True)
    invalid_types = sorted(set(payload["etf_type"]) - ETF_TYPES)
    invalid_exchange = sorted(set(payload["exchange"]) - VALID_EXCHANGES)
    invalid_ts_code = ~payload["ts_code"].astype(str).str.match(r"^\d{6}\.(SH|SZ)$")
    if invalid_types or invalid_exchange or invalid_ts_code.any() or payload["code"].duplicated().any():
        raise ValueError(
            "MCP etf_basic canonical quality gate failed: "
            f"invalid_types={invalid_types}, invalid_exchange={invalid_exchange}, "
            f"invalid_ts_code={int(invalid_ts_code.sum())}, "
            f"duplicate_codes={int(payload['code'].duplicated().sum())}"
        )
    return payload
