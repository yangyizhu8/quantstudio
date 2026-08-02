"""MCP 数据源适配器（P2-2：MCPSourceAdapter）

按任务书 §4 + P2-0 协议探针结论 + QFQ 设计 §7.2 实现。

职责边界（铁律）：
- **只取数 + 原样返回 raw**：fetch_table 返回 raw OHLC(+adj_factor)，不做任何复权计算
  （复权交给 aligner / P2-4 daemon 协同）。
- **不改 aligner / validator / writer 行为**，不改默认生产配置。
- **QFQ 数据注入（§7.2-A，最高优先级）**：取 stock_dividend / 含 adj_factor 的行情表时，
  把数据写入 DuckDB 主库 stock_dividend 与 qfq_aux.db 快照，以驱动 qfq_event_discovery
  生成 trigger。这是"MCP 能取数且能驱动 discovery"闭环的关键，缺失 = 失败。
- **code_format 声明**：MCP 返回 600063.SH（tushare 格式），声明 "tushare_to_raw"，
  由 aligner 按 source=metadata["source"] 查 alignment_rules 映射归一为裸码。
- **lineage**：metadata 带 upstream_authority=xtquant（server 侧真相源），供 daemon
  authority 校验。
- **内存约束**：分钟大表可能全量进内存。fetch_table 通过 export_dataset 落 Raw Landing
  （DATA_ROOT/mcp_landing/）后分片读取，避免单 DataFrame 全量驻留（P3-4 验证前形态不变，
  仍为 DataFrame 返回，符合基类公共 API；如需改为分片迭代形态属公共 API 变更，单独立项）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from quantstudio.pipeline.sources.base import BaseSourceAdapter
from quantstudio.pipeline.mcp import MCPClient
from quantstudio.pipeline.mcp.client import load_mcp_api_key
from quantstudio.pipeline.mcp.errors import MCPClientError, MCPToolError
from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 覆盖矩阵（基于 P2-0 探针 + 任务书 §4）
# MCP server 支持的全表矩阵。supports_freq / supports_task 据此声明。
# 注意：MCP server 当前支持股票/ETF 行情 + 除权除息；不支持财务/指数/行业等。
#
# 【扩展点】新增表流程（修复3）：先跑 scripts/mcp_probe_tables.py 探测云端真实
# QuestDB 源表名 → 回填下列两处 + mcp_only/collector_tasks.json + alignment_rules.json
# → ConfigLint 通过。未探测确认的表不激活，避免 ConfigLint 因云端无此表失败。
# ---------------------------------------------------------------------------
_MCP_SUPPORTED: Dict[Tuple[str, str], str] = {
    ("stock_daily", "daily"): "stock_daily",
    ("etf_daily", "daily"): "etf_daily",
    ("stock_minutes", "1min"): "stock_minutes_1min",
    ("stock_minutes", "5min"): "stock_minutes_5min",
    ("stock_minutes", "15min"): "stock_minutes_15min",
    ("stock_minutes", "30min"): "stock_minutes_30min",
    ("stock_minutes", "60min"): "stock_minutes_60min",
    ("etf_minutes", "1min"): "etf_minutes_1min",
    ("etf_minutes", "5min"): "etf_minutes_5min",
    ("etf_minutes", "15min"): "etf_minutes_15min",
    ("etf_minutes", "30min"): "etf_minutes_30min",
    ("etf_minutes", "60min"): "etf_minutes_60min",
    ("stock_dividend", "daily"): "stock_dividend",
    # 财务表（P3-5起支持）
    ("balance_statement", "daily"): "balance_statement",
    ("income_statement", "daily"): "income_statement",
    ("cashflow_statement", "daily"): "cashflow_statement",
    ("fin_indicator", "daily"): "fin_indicator",
    # 基础资料
    ("stock_basic", "daily"): "stock_basic",
    ("etf_basic", "daily"): "etf_basic",
    ("trade_calendar", "daily"): "trade_calendar",
    ("index_daily", "daily"): "index_daily",
}

# 行情大表（日线 + 分钟线）：走 export_dataset 落 Raw Landing（支持日期范围 + 分片）。
# 原因：stock_daily 约 1400 万行，query_snapshot(limit=N) 只返回「最新 N 行」无法覆盖
# 历史日期范围；export_dataset 导出全量分片后由 adapter 侧按日期+codes 过滤。
# 小表（stock_dividend 等千行级）仍走 query_snapshot。
_EXPORT_TABLES = {
    ("stock_daily", "daily"), ("etf_daily", "daily"),
    ("stock_minutes", "1min"), ("stock_minutes", "5min"),
    ("stock_minutes", "15min"), ("stock_minutes", "30min"),
    ("stock_minutes", "60min"), ("etf_minutes", "1min"),
    ("etf_minutes", "5min"), ("etf_minutes", "15min"),
    ("etf_minutes", "30min"), ("etf_minutes", "60min"),
}
# 小表（走 query_snapshot 内存路径）
_SNAPSHOT_TABLES = {("stock_dividend", "daily")}

# QuantStudio canonical 表名 → QuestDB 源表名映射
# 大部分表名一致，以下是需要映射的例外
_CANONICAL_TO_QUESTDB = {
    "balance_statement": "stock_balancesheet",
    "income_statement": "stock_income",
    "cashflow_statement": "stock_cashflow",
    "fin_indicator": "stock_fina_indicator",
    "trade_calendar": "trade_cal",
    "stock_dividend": "ws_exdiv",
}

_QFQ_DIVIDEND_TABLE = ("stock_dividend", "daily")
_QFQ_ADJFACTOR_TABLES = {("stock_daily", "daily"), ("etf_daily", "daily"),
                         ("stock_minutes", "1min"), ("stock_minutes", "5min"),
                         ("stock_minutes", "15min"), ("stock_minutes", "30min"),
                         ("stock_minutes", "60min"), ("etf_minutes", "1min"),
                         ("etf_minutes", "5min"), ("etf_minutes", "15min"),
                         ("etf_minutes", "30min"), ("etf_minutes", "60min")}


def _resolve_data_root() -> Path:
    """解析 Raw Landing 根目录（DATA_ROOT，回退项目 data/）。

    直接复用 quantstudio._paths.get_data_root()，确保与 daemon / writer
    使用的 DATA_ROOT 完全一致（避免 parents[N] 越级的 off-by-one）。
    """
    from quantstudio._paths import get_data_root
    return get_data_root()


class MCPAdapter(BaseSourceAdapter):
    """MCP 全数据源适配器。

    config 示例：
        {
            "name": "mcp",
            "base_url": "https://124.223.159.234/mcp",
            "tls_verify": false,          # 开发 IP 自签证书
            "main_db": "data/quantstudio.db",   # 用于推导 qfq_aux.db 路径 + 写 stock_dividend
            "enable_qfq_injection": true,  # §7.2-A 注入开关（默认开）
            "landing_subdir": "mcp_landing" # Raw Landing 子目录（相对 DATA_ROOT）
        }

    MCP_API_KEY 解析链：构造参数 → config/secrets.env（GUI 写入，不进 git）
    → 环境变量（见 MCPClient.load_mcp_api_key），缺失即 fail-fast。
    """

    def __init__(self, config: Dict):
        super().__init__(config)
        # 与 MCPClient 对齐：统一使用 endpoint（兼容旧 base_url 配置键，profile 不用改）
        self.endpoint = config.get("endpoint") or config.get("base_url", "https://124.223.159.234/mcp")
        self.tls_verify = bool(config.get("tls_verify", False))
        self.enable_qfq_injection = bool(config.get("enable_qfq_injection", True))
        self.main_db = config.get("main_db")
        self.landing_subdir = config.get("landing_subdir", "mcp_landing")
        self._client: Optional[MCPClient] = None
        self._landing_root = _resolve_data_root() / self.landing_subdir
        try:
            self._landing_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # pragma: no cover - 目录权限问题不阻断构造
            logger.warning(f"[MCPAdapter] 创建 Raw Landing 目录失败（取数时再试）: {e}")

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    @property
    def client(self) -> MCPClient:
        if self._client is None:
            # 修复2：构造时从 config/secrets.env（GUI 写入）读取 API Key 注入，
            # 不从环境变量隐式依赖，避免 key 泄露到 git 配置。
            self._client = MCPClient(
                endpoint=self.endpoint,
                tls_verify=self.tls_verify,
                api_key=load_mcp_api_key(),
            )
            self._client.handshake()  # 建立 session（initialize→mcp-session-id→notifications/initialized）
        return self._client

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:  # pragma: no cover
                logger.debug(f"[MCPAdapter] client.close 异常（忽略）: {e}")
            self._client = None

    # ------------------------------------------------------------------
    # 能力声明
    # ------------------------------------------------------------------
    def supports_freq(self, freq: str) -> bool:
        """声明 MCP 整体支持的频率（基类契约：单参数 freq）。"""
        return freq in {"daily", "1min", "5min", "15min", "30min", "60min"}

    def supports_task(self, table: str, freq: str) -> Tuple[bool, str]:
        """检查 (table, freq) 是否在 MCP 支持矩阵内（基类契约：双参数，返回 (ok, reason)）。

        注意：MCP server 支持任意日期窗口/代码范围（server 侧分页/分片），
        故 start/end/codes 不影响能力判断；大表全量受 Raw Landing 落盘 + 分片读取保护。
        """
        if not self.supports_freq(freq):
            return (False, f"mcp 不支持频率 '{freq}'（仅 daily/1min/5min/15min/30min/60min）")
        if (table, freq) in _MCP_SUPPORTED:
            return (True, "ok")
        return (False, f"mcp 不支持表/频率组合 ({table},{freq})，"
                       f"矩阵: {sorted(_MCP_SUPPORTED)}")

    # ------------------------------------------------------------------
    # 核心：fetch_table
    # ------------------------------------------------------------------
    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        """拉取单表数据，返回 (raw_df, metadata)。

        - 行情大表（stock_daily/etf_daily/分钟）：client.export_dataset 落 Raw Landing
          → 分片读取 → concat（server 不接日期参数，由 adapter 侧按日期+codes 过滤）
        - 小表（stock_dividend 等）：client.query_snapshot
        - 返回 raw OHLC（+adj_factor 列原样），不做复权
        - §7.2-A：取 stock_dividend / 含 adj_factor 表时写 DB 驱动 discovery
        """
        ok, reason = self.supports_task(table, freq)
        if not ok:
            raise ValueError(
                f"[MCPAdapter] 不支持的表/频率: ({table},{freq})，"
                f"MCP 支持矩阵: {sorted(_MCP_SUPPORTED)}")

        key = (table, freq)
        if key in _EXPORT_TABLES:
            raw_df, meta = self._fetch_export(table, freq, start, end, codes)
        else:
            raw_df, meta = self._fetch_small_table(table, freq, start, end, codes)

        # §7.2-A QFQ 数据注入
        if self.enable_qfq_injection:
            self._inject_qfq_inputs(table, freq, raw_df, meta)

        return raw_df, meta

    # ------------------------------------------------------------------
    # 小表：query_snapshot
    # ------------------------------------------------------------------
    def _fetch_small_table(self, table: str, freq: str, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        # 注意：server 的 query_snapshot 仅支持 dataset_id + limit（无日期/codes 过滤），
        # 全量行 JSON 已在 rows 中。日期/codes 过滤在 adapter 侧完成（保持与原 source 一致）。
        # canonical→questdb表名映射
        qdb_table = _CANONICAL_TO_QUESTDB.get(table, table)
        page = self.client.query_snapshot(dataset_id=qdb_table, limit=200_000)
        all_rows = page.rows
        df = pd.DataFrame(all_rows, columns=page.columns) if all_rows else pd.DataFrame()
        # 日期窗口过滤（daemon 传入 start/end 为 YYYY-MM-DD；
        # MCP 列名可能是 date/trade_date，值格式可能是 YYYYMMDD 或 YYYY-MM-DD）
        # 统一归一化为 YYYYMMDD 8位字符串再比较，避免格式不匹配导致全滤为 0
        def _norm_date(v):
            s = str(v).strip()
            if len(s) >= 10 and s[4] == "-":
                s = s[:10].replace("-", "")
            return s[:8]
        date_col = next((c for c in ("date", "trade_date") if c in df.columns), None)
        if len(df) and date_col:
            dcol = df[date_col].map(_norm_date)
            s8, e8 = _norm_date(start), _norm_date(end)
            mask = (dcol >= s8) & (dcol <= e8)
            df = df[mask].reset_index(drop=True)
        # codes 过滤（MCP 返回 ts_code 列，格式 600063.SH；部分源也可能是 code 列）
        if codes and len(df):
            want = {str(c) for c in codes}
            code_col = next((c for c in ("ts_code", "code", "stock_code")
                             if c in df.columns), None)
            if code_col:
                df = df[df[code_col].astype(str).isin(want)].reset_index(drop=True)
                logger.info(f"[MCPAdapter] codes 过滤({code_col})→ {len(df)} 行")
            else:
                logger.warning(f"[MCPAdapter] 无 code 类列可过滤 codes，返回全量 {len(df)} 行")
        has_adj = "adj_factor" in df.columns
        # ws_exdiv 适配（P3-7）：云端除权除息表 ws_exdiv 无 div_proc 列，
        # 仅 dividend_plan（如 "10派6.180元"）描述方案。event_discovery 要求
        # div_proc='实施' 才生成 trigger，故 dividend_plan 非空即视为已实施。
        if table == "stock_dividend":
            if "div_proc" not in df.columns and "dividend_plan" in df.columns:
                df["div_proc"] = df["dividend_plan"].apply(
                    lambda v: "实施" if (v is not None and str(v).strip() != "") else None)
                logger.info(f"[MCPAdapter] stock_dividend(ws_exdiv) 派生 div_proc='实施' "
                             f"→ {int(df['div_proc'].notna().sum())} 行")
        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "code_format": "tushare_to_raw",  # 600063.SH → 裸码（aligner 归一）
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "upstream_authority": "xtquant",   # server 侧真相源
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "server": self.endpoint,
            },
            "rows": len(df),
            "is_qfq_capable": has_adj,
        }
        logger.info(f"[MCPAdapter] {table}/{freq} 小表拉取 {len(df)} 行 "
                    f"(adj_factor={has_adj})")
        return df, meta

    # ------------------------------------------------------------------
    # 行情大表：export_dataset 落 Raw Landing + 分片读取 + 本地日期/codes 过滤
    # ------------------------------------------------------------------
    def _fetch_export(self, table: str, freq: str, start: str, end: str,
                      codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """行情大表（日线/分钟）：导出落 Raw Landing，分片读取避免全量驻留。

        export_dataset 已内部跑完 create_export_job → get_manifest → 逐 shard
        get_artifact（含 SHA256 对账），返回 Artifact 列表。这里落盘 + 分片读回。
        server 的 export 不接收日期/codes 参数（全量导出），故过滤在 adapter 侧完成。
        """
        # 服务端时间范围下推（ISO 格式 + 闭区间 end 加 1 天，避免漏当天）
        def _to_iso(d: str) -> str:
            s = str(d).strip()[:10]
            return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00")
        time_start_iso = _to_iso(start)
        time_end_iso = (datetime.strptime(str(end).strip()[:10], "%Y-%m-%d")
                        + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        # 分钟大表服务端强制要求 row_limit
        _is_big = "minutes" in table
        try:
            qdb_tbl = _CANONICAL_TO_QUESTDB.get(table, table)
            artifacts = self.client.export_dataset(
                dataset_id=qdb_tbl, page_size=50_000,
                time_start=time_start_iso, time_end=time_end_iso,
                row_limit=5_000_000 if _is_big else None)
        except Exception as _e:
            logger.error(f"[_fetch_export] export_dataset 异常: "
                         f"type={type(_e).__name__} msg={_e} "
                         f"endpoint={self.endpoint}")
            raise
        job_id = (artifacts[0].raw.get("job_id")
                  if artifacts and artifacts[0].raw.get("job_id")
                  else "export")
        logger.info(f"[MCPAdapter] {table}/{freq} export 分片数={len(artifacts)}")

        frames: List[pd.DataFrame] = []
        for art in artifacts:
            local_parquet = self._landing_path(job_id, art.artifact_id.replace("/", "_"))
            local_parquet.write_bytes(art.parquet_bytes)
            df_shard = pd.read_parquet(local_parquet)
            frames.append(df_shard)
            logger.debug(f"[MCPAdapter] 分片 {art.artifact_id} 读取 {len(df_shard)} 行 "
                         f"→ Raw Landing {local_parquet.name}")

        raw_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        # 日期窗口过滤（daemon 传入 YYYY-MM-DD；MCP 列可能是 date/trade_date，
        # 值格式可能是 YYYYMMDD 或 YYYY-MM-DD，统一归一化为 8 位比较）
        def _norm_date(v):
            s = str(v).strip()
            if len(s) >= 10 and s[4] == "-":
                s = s[:10].replace("-", "")
            return s[:8]

        date_col = next((c for c in ("date", "trade_date") if c in raw_df.columns), None)
        if len(raw_df) and date_col:
            dcol = raw_df[date_col].map(_norm_date)
            s8, e8 = _norm_date(start), _norm_date(end)
            raw_df = raw_df[(dcol >= s8) & (dcol <= e8)].reset_index(drop=True)
        # codes 过滤（MCP 返回 ts_code 列，格式 600063.SH）
        if codes and len(raw_df):
            want = {str(c) for c in codes}
            code_col = next((c for c in ("ts_code", "code", "stock_code")
                             if c in raw_df.columns), None)
            if code_col:
                raw_df = raw_df[raw_df[code_col].astype(str).isin(want)].reset_index(drop=True)
                logger.info(f"[MCPAdapter] codes 过滤({code_col})→ {len(raw_df)} 行")
            else:
                logger.warning(f"[MCPAdapter] 无 code 类列可过滤 codes，返回全量 {len(raw_df)} 行")

        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "code_format": "tushare_to_raw",
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "upstream_authority": "xtquant",
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "export_job": job_id,
                "shards": len(artifacts),
                "server": self.endpoint,
            },
            "rows": len(raw_df),
            "raw_landing_dir": str(self._landing_root / job_id),
            "is_qfq_capable": True,  # 分钟表含 adj_factor 列（原样返回）
        }
        logger.info(f"[MCPAdapter] {table}/{freq} 大表拉取 {len(raw_df)} 行 "
                    f"(Raw Landing: {meta['raw_landing_dir']})")
        return raw_df, meta

    def _landing_path(self, job_id: str, shard_name: str) -> Path:
        d = self._landing_root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{shard_name}.parquet"

    # ------------------------------------------------------------------
    # get_last_date（基类要求；daemon 实际用 writer.get_last_date 决定水位）
    # ------------------------------------------------------------------
    def get_last_date(self, table: str, freq: str = "daily",
                      codes: Optional[List[str]] = None) -> Optional[str]:
        """返回该表可获取的最新日期（YYYYMMDD 字符串）。

        非 daemon 水位权威（daemon 用 writer.get_last_date）。此处仅作能力探测：
        拉一次小窗口 query_snapshot 取 max(date)；分钟大表退化为返回 None（避免全量导出）。
        """
        if (table, freq) in _EXPORT_TABLES:
            # 行情大表走 export 全量，这里不触发导出（避免全量下载）；
            # get_last_date 仅作能力探测，退化为返回 None，由 daemon 用 writer 水位。
            return None
        try:
            snap = self.client.query_snapshot(dataset_id=table, limit=1)
            if snap.rows:
                return snap.rows[0].get("date") or snap.rows[0].get("trade_date")
        except MCPClientError as e:
            logger.warning(f"[MCPAdapter] get_last_date({table},{freq}) 失败: {e}")
        return None

    # ------------------------------------------------------------------
    # §7.2-A QFQ 数据注入：驱动 qfq_event_discovery
    # ------------------------------------------------------------------
    def _inject_qfq_inputs(self, table: str, freq: str,
                           raw_df: pd.DataFrame, meta: Dict) -> None:
        """把 dividend / adj_factor 数据写入 DB 以驱动 discovery 生成 trigger。

        - stock_dividend：upsert 进 DuckDB 主库（discovery.scan_stock_dividend 直接读）。
        - 含 adj_factor 的行情表：标准化后写 qfq_aux.db 的 adj_factor(股票)/fund_adj(ETF)
          表——这正是 qfq_event_discovery._observe_factors 读取的因子快照表（裸码口径，
          (code,time) PK），discovery 据此生成 factor_new trigger。

        code 列统一为裸码（MCP 返回 600063.SH → 去后缀），与 discovery 口径一致。
        """
        if raw_df is None or len(raw_df) == 0:
            return
        try:
            if (table, freq) == _QFQ_DIVIDEND_TABLE:
                self._inject_dividend(raw_df)
            if (table, freq) in _QFQ_ADJFACTOR_TABLES and "adj_factor" in raw_df.columns:
                self._inject_adjfactor(raw_df, freq, table)
        except Exception as e:
            # 注入失败不得阻断取数（取数已成功返回）；记 warning 待 P2-4 协同排查。
            logger.warning(f"[MCPAdapter] §7.2-A QFQ 注入失败（取数不受影响）: {e}")

    def _inject_dividend(self, df: pd.DataFrame) -> None:
        """upsert stock_dividend 进 DuckDB 主库（裸码 + div_proc 实施 才驱动 trigger）。"""
        if self.main_db is None:
            logger.debug("[MCPAdapter] main_db 未配置，跳过 stock_dividend 注入")
            return
        # 取 discovery 关心的列；MCP 返回的 dividend 列名按 server 实测对齐。
        needed = {"code", "ex_date", "record_date", "ann_date", "end_date",
                  "cash_div_before_tax", "cash_div_after_tax", "cash_div",
                  "stk_div", "stk_bo_rate", "stk_co_rate", "div_rat",
                  "div_proc", "update_time", "data_source"}
        cols = [c for c in needed if c in df.columns]
        if "code" not in cols or "ex_date" not in cols:
            logger.debug(f"[MCPAdapter] dividend 缺 code/ex_date 列，跳过注入: {df.columns.tolist()}")
            return
        out = df[cols].copy()
        out["code"] = out["code"].astype(str).str.split(".").str[0]  # 裸码
        out["data_source"] = out.get("data_source", "mcp")
        db_path = str(self.main_db)
        try:
            import duckdb
            con = duckdb.connect(db_path, read_only=False)
            try:
                # 幂等建表（与 writers.py 列集合一致，PRIMARY KEY 供 upsert 冲突定位）
                con.execute(
                    "CREATE TABLE IF NOT EXISTS stock_dividend ("
                    "code VARCHAR, ex_date BIGINT, record_date BIGINT, ann_date BIGINT, "
                    "end_date BIGINT, cash_div_before_tax DOUBLE, cash_div_after_tax DOUBLE, "
                    "cash_div DOUBLE, stk_div DOUBLE, stk_bo_rate DOUBLE, stk_co_rate DOUBLE, "
                    "div_rat DOUBLE, div_proc VARCHAR, update_time VARCHAR, data_source VARCHAR, "
                    "PRIMARY KEY (code, ex_date))")
                # upsert（按 code+ex_date 冲突）
                conflict = ", ".join(cols)
                placeholders = ", ".join(["?"] * len(cols))
                update_cols = [c for c in cols if c not in ("code", "ex_date")]
                if update_cols:
                    set_clause = ", ".join([f"{c}=EXCLUDED.{c}" for c in update_cols])
                    upsert_sql = (
                        f"INSERT INTO stock_dividend ({conflict}) VALUES ({placeholders}) "
                        f"ON CONFLICT (code, ex_date) DO UPDATE SET {set_clause}")
                else:
                    upsert_sql = (
                        f"INSERT INTO stock_dividend ({conflict}) VALUES ({placeholders}) "
                        f"ON CONFLICT (code, ex_date) DO NOTHING")
                try:
                    con.executemany(
                        upsert_sql,
                        [tuple(r) for r in out[cols].itertuples(index=False, name=None)])
                except Exception:
                    # 回退：表已存在但无 PK（历史/writers 预建）→ 纯插入（daemon 侧去重）
                    logger.debug("[MCPAdapter] stock_dividend 无 PK 约束，回退纯 INSERT")
                    con.executemany(
                        f"INSERT INTO stock_dividend ({conflict}) VALUES ({placeholders})",
                        [tuple(r) for r in out[cols].itertuples(index=False, name=None)])
                logger.info(f"[MCPAdapter] §7.2-A 注入 stock_dividend {len(out)} 行 → {db_path}")
            finally:
                con.close()
        except Exception as e:
            logger.warning(f"[MCPAdapter] stock_dividend 写入失败: {e}")

    def _inject_adjfactor(self, df: pd.DataFrame, freq: str, table: str) -> None:
        """标准化 MCP adj_factor 并写入 qfq_aux.db 的 adj_factor(股票)/fund_adj(ETF) 表。

        这是 qfq_event_discovery._observe_factors 读取的因子快照表（裸码口径，
        (code,time) PK），discovery 据此生成 factor_new trigger，驱动 QFQ 闭环。
        """
        if self.main_db is None:
            logger.debug("[MCPAdapter] main_db 未配置，跳过 adj_factor 注入")
            return
        asset_type = "ETF" if table.startswith("etf") else "STOCK"
        norm = normalize_mcp_adj_factor_df(df, freq, asset_type)
        if len(norm) == 0:
            logger.debug(f"[MCPAdapter] adj_factor 标准化为空（表={table}），跳过注入")
            return
        aux = aux_db_path(self.main_db)
        target = "fund_adj" if asset_type == "ETF" else "adj_factor"
        rows = [(str(r["code"]), int(r["time"]), float(r["adj_factor"]))
                for _, r in norm.iterrows()]
        try:
            aconn = sqlite3.connect(str(aux), timeout=30)
            try:
                aconn.execute("PRAGMA journal_mode=WAL")
                aconn.execute("PRAGMA busy_timeout=30000")
                aconn.execute(
                    f"CREATE TABLE IF NOT EXISTS {target} ("
                    f"code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY (code, time))")
                aconn.executemany(
                    f"INSERT OR REPLACE INTO {target} (code, time, adj_factor) "
                    f"VALUES (?, ?, ?)", rows)
                aconn.commit()
                logger.info(f"[MCPAdapter] §7.2-A 注入 {target} {len(rows)} 行 "
                            f"→ {aux} (asset_type={asset_type})")
            finally:
                aconn.close()
        except Exception as e:
            logger.warning(f"[MCPAdapter] {target} 写入失败: {e}")


# ----------------------------------------------------------------------
# P2-4 §7.2-B：MCP adj_factor 标准化（供 aligner tushare 计算路径消费）
# ----------------------------------------------------------------------
_CST = timezone(timedelta(hours=8))  # Asia/Shanghai 固定偏移（复权连接用，无需历史时区库）


def _mcp_time_to_utc_ms(val, freq: str) -> Optional[int]:
    """把 MCP 的时间字段归一为 UTC epoch 毫秒（aligner._apply_qfq 以 utc=True 解析）。

    - 日线 trade_date=YYYYMMDD（int/str）→ Asia/Shanghai 当日 00:00 的 UTC ms
    - 分钟 time=ms 大数 → 视为 Asia/Shanghai 本地墙钟 ms，转 UTC（减 8h）
    - 分钟 trade_time=YYYYMMDDHHMMSS → 解析为 Asia/Shanghai 后转 UTC ms
    统一以 Asia/Shanghai 墙钟为基准，保证 aligner 按自然日连接日频因子一致。
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        # MCP 返回的时间字段可能是 pd.Timestamp / datetime（datetime64[ns] 列），
        # 需先归一为 YYYYMMDD / YYYYMMDDHHMMSS 字符串，避免 int(Timestamp) 抛 TypeError
        if isinstance(val, (pd.Timestamp, datetime)):
            if freq == "daily":
                s = val.strftime("%Y%m%d")
            else:
                s = val.strftime("%Y%m%d%H%M%S")
                if len(s) < 14:
                    s = s.ljust(14, "0")
            dt = datetime.strptime(s[:14] if len(s) >= 14 else s[:8],
                                   "%Y%m%d%H%M%S" if len(s) >= 14 else "%Y%m%d").replace(tzinfo=_CST)
            return int(dt.timestamp() * 1000)
        if freq == "daily":
            # YYYYMMDD
            s = str(int(val))
            dt = datetime.strptime(s, "%Y%m%d").replace(tzinfo=_CST)
            return int(dt.timestamp() * 1000)
        # 分钟
        if isinstance(val, (int, float)) and val > 1e12:
            # 视为 Asia/Shanghai 本地墙钟 ms → UTC
            return int(val - 8 * 3600 * 1000)
        s = str(int(val))
        if len(s) >= 14:  # YYYYMMDDHHMMSS
            dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=_CST)
            return int(dt.timestamp() * 1000)
        if len(s) >= 8:  # YYYYMMDD
            dt = datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=_CST)
            return int(dt.timestamp() * 1000)
    except Exception:
        return None
    return None


def normalize_mcp_adj_factor_df(raw_df: pd.DataFrame, freq: str,
                                 asset_type: str = "STOCK") -> pd.DataFrame:
    """从 MCP raw_df 提取 adj_factor 列，标准化为 aligner 期望格式。

    返回列：code(裸码) / time(UTC ms epoch) / adj_factor。
    标准化项（任务书 P2-4 §2）：
    - SH/SZ 后缀处理：600063.SH → 600063
    - UTC→Asia/Shanghai 时区：分钟 bar 时间按 Asia/Shanghai 墙钟归一
    - 分钟 bar 按交易日连接日频 factor：保留每个 bar 的 time（aligner 按自然日连接）
    - 同证券同交易日重复 factor 去重：保留末值
    - 缺失/非数字/adj_factor<=0 处理：drop
    """
    if raw_df is None or len(raw_df) == 0 or "adj_factor" not in raw_df.columns:
        return pd.DataFrame(columns=["code", "time", "adj_factor"])
    df = raw_df.copy()
    # 0) 列名归一：MCP 原始返回 ts_code/trade_date/trade_time，aligner 映射后才是 code/time。
    #    本函数接收 MCP 原始 raw（daemon 在 align 前调用），故先归一为裸码/时间列。
    if "code" not in df.columns and "ts_code" in df.columns:
        df["code"] = df["ts_code"]
    if "time" not in df.columns and "trade_time" in df.columns:
        df["time"] = df["trade_time"]
    if "time" not in df.columns and "trade_date" in df.columns:
        df["time"] = df["trade_date"]
    # 1) code 去后缀
    df["code"] = df["code"].astype(str).str.split(".").str[0]
    # 2) 时间列选择
    time_col = None
    for cand in ("time", "trade_time", "trade_date"):
        if cand in df.columns:
            time_col = cand
            break
    if time_col is None:
        logger.warning(f"[normalize_mcp_adj_factor_df] 无时间列(time/trade_time/trade_date)，"
                       f"跳过标准化: {df.columns.tolist()}")
        return pd.DataFrame(columns=["code", "time", "adj_factor"])
    df["time"] = df[time_col].apply(lambda v: _mcp_time_to_utc_ms(v, freq))
    # 3) 清洗 adj_factor
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["code", "time", "adj_factor"])
    df = df[df["adj_factor"] > 0]
    if len(df) < before:
        logger.debug(f"[normalize_mcp_adj_factor_df] 清洗丢弃 {before - len(df)} 行"
                     f"（缺失/非数字/adj_factor<=0）")
    if len(df) == 0:
        return pd.DataFrame(columns=["code", "time", "adj_factor"])
    # 4) 同证券同交易日去重（按自然日，保留末值）
    # 自然日 = UTC ms 转 Asia/Shanghai 日期
    df["_day"] = (df["time"] // 1000 + 8 * 3600) // 86400  # Asia/Shanghai 日期序号
    df = df.sort_values(["code", "time"])
    df = df.drop_duplicates(subset=["code", "_day"], keep="last").drop(columns=["_day"])
    out = df[["code", "time", "adj_factor"]].reset_index(drop=True)
    logger.info(f"[normalize_mcp_adj_factor_df] {asset_type} {freq} 标准化 "
                f"{len(out)} 行（去重后）")
    return out


if __name__ == "__main__":
    # 最小冒烟（需 MCP_API_KEY 环境变量）
    logging.basicConfig(level=logging.INFO)
    adapter = MCPAdapter({
        "name": "mcp",
        "base_url": "https://124.223.159.234/mcp",
        "tls_verify": False,
        "main_db": "data/quantstudio.db",
    })
    try:
        df, meta = adapter.fetch_table("stock_daily", "2026-07-07", "2026-07-10",
                                       freq="daily", codes=["600000.SH"])
        print("rows:", len(df), "| meta keys:", list(meta.keys()))
        print(df.head() if len(df) else "（无数据）")
    finally:
        adapter.close()
