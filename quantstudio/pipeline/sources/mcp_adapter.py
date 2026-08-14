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
import time
import sqlite3
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from quantstudio.pipeline.sources.base import BaseSourceAdapter
from quantstudio.pipeline.mcp import MCPClient
from quantstudio.pipeline.mcp.client import load_mcp_api_key
from quantstudio.pipeline.mcp.errors import MCPClientError, MCPToolError
from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path

logger = logging.getLogger(__name__)

_QFQ_PROFILE = bool(os.environ.get("QFQ_PROFILE", ""))
_profile_logger = logging.getLogger("qfq_profile")

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
    # 【codex审计】stock_suspend_d 停更（语义错配：云端是每日停牌状态，canonical 是停牌区间表，
    #   且停牌判定走 volume==0→suspendFlag，不消费此表）。已移除。
    # === 类别A 全量扩展：新增映射表（含 namechange）===
    # stock_daily_valuation→stock_daily_basic / index_constituents→index_weight /
    # industry_classification→sw_classify / stock_namechange→stock_namechange(同名)
    # 【codex审计】sw_industry(←sw_daily 行业指数行情)、industry_membership(←sw_weight 权重快照)
    #   语义错配已停更；sw_daily/sw_weight 改 passthrough 本名直通（见类别B）。
    ("stock_daily_valuation", "daily"): "stock_daily_basic",
    ("index_constituents", "daily"): "index_weight",
    ("industry_classification", "daily"): "sw_classify",
    ("stock_namechange", "daily"): "stock_namechange",
    # === 类别B：67 张 passthrough 同名表（全部 daily，直接同名建 DuckDB 表）===
    ("ai_research_snapshot", "daily"): "ai_research_snapshot",
    ("block_trade", "daily"): "block_trade",
    ("broker_monthly", "daily"): "broker_monthly",
    ("broker_recommend", "daily"): "broker_recommend",
    ("cninfo_first_rating", "daily"): "cninfo_first_rating",
    ("cnthesims_events", "daily"): "cnthesims_events",
    ("cnthesims_factors", "daily"): "cnthesims_factors",
    ("cyq_chips", "daily"): "cyq_chips",
    ("cyq_perf", "daily"): "cyq_perf",
    ("daily_info", "daily"): "daily_info",
    ("em_first_cover_rating", "daily"): "em_first_cover_rating",
    ("etf_adj_factor", "daily"): "etf_adj_factor",
    ("etf_sentiment_daily", "daily"): "etf_sentiment_daily",
    ("etf_share_size", "daily"): "etf_share_size",
    ("factor_value", "daily"): "factor_value",
    ("forecast_vip", "daily"): "forecast_vip",
    ("gisisi_daily", "daily"): "gisisi_daily",
    ("hm_detail", "daily"): "hm_detail",
    ("hm_list", "daily"): "hm_list",
    ("idx_anns", "daily"): "idx_anns",
    ("idx_factor_pro", "daily"): "idx_factor_pro",
    ("index_classify", "daily"): "index_classify",
    ("inst_survey", "daily"): "inst_survey",
    ("limit_cpt_list", "daily"): "limit_cpt_list",
    ("limit_list_d", "daily"): "limit_list_d",
    ("limit_list_ths", "daily"): "limit_list_ths",
    ("limit_step", "daily"): "limit_step",
    ("llm_text_events", "daily"): "llm_text_events",
    ("llm_text_events_enriched", "daily"): "llm_text_events_enriched",
    ("llm_text_raw_feed", "daily"): "llm_text_raw_feed",
    ("margin", "daily"): "margin",
    ("margin_detail", "daily"): "margin_detail",
    ("margin_secs", "daily"): "margin_secs",
    ("market_breadth_daily", "daily"): "market_breadth_daily",
    ("moneyflow_cnt_ths", "daily"): "moneyflow_cnt_ths",
    ("moneyflow_ind_ths", "daily"): "moneyflow_ind_ths",
    ("moneyflow_ths", "daily"): "moneyflow_ths",
    ("news_sentiment", "daily"): "news_sentiment",
    ("report_rc", "daily"): "report_rc",
    ("rsshub_raw", "daily"): "rsshub_raw",
    ("sector_top300_daily", "daily"): "sector_top300_daily",
    ("sentiment_factor_daily", "daily"): "sentiment_factor_daily",
    ("slb_len", "daily"): "slb_len",
    ("stk_auction", "daily"): "stk_auction",
    ("stk_factor_pro", "daily"): "stk_factor_pro",
    ("stk_limit", "daily"): "stk_limit",
    ("style_cross_section_daily", "daily"): "style_cross_section_daily",
    ("survey_sentiment", "daily"): "survey_sentiment",
    ("sw_daily", "daily"): "sw_daily",         # codex审计：行业指数行情（原 sw_industry 错配，改 passthrough）
    ("sw_weight", "daily"): "sw_weight",       # codex审计：L1 指数权重快照（原 industry_membership 错配，改 passthrough）
    ("tdx_theme_etf_overlay", "daily"): "tdx_theme_etf_overlay",
    ("tdx_theme_llm_scores", "daily"): "tdx_theme_llm_scores",
    ("tdx_theme_news", "daily"): "tdx_theme_news",
    ("tdx_theme_sentiment_daily", "daily"): "tdx_theme_sentiment_daily",
    ("ths_daily", "daily"): "ths_daily",
    ("ths_hot", "daily"): "ths_hot",
    ("ths_index", "daily"): "ths_index",
    ("ths_member", "daily"): "ths_member",
    ("top_inst", "daily"): "top_inst",
    ("top_list", "daily"): "top_list",
    ("ws_asfund", "daily"): "ws_asfund",
    ("ws_blocktrade", "daily"): "ws_blocktrade",
    ("ws_etf_holders", "daily"): "ws_etf_holders",
    ("ws_etf_holdings", "daily"): "ws_etf_holdings",
    ("ws_ipo", "daily"): "ws_ipo",
    ("ws_lhb", "daily"): "ws_lhb",
    ("ws_margintrade", "daily"): "ws_margintrade",
    ("ws_reserve", "daily"): "ws_reserve",
    ("xueqiu_sentiment", "daily"): "xueqiu_sentiment",
}

# 大型映射表：走 export_dataset 落 Raw Landing（支持日期范围 + 分片）。
# 原因：stock_daily 约 1400 万行，query_snapshot(limit=N) 只返回「最新 N 行」无法覆盖
# 历史日期范围；export_dataset 导出全量分片后由 adapter 侧按日期+codes 过滤。
# 其余映射表与 passthrough 表走 fetch_page cursor 分页，禁止 query_snapshot 截断。
_EXPORT_TABLES = {
    ("stock_daily", "daily"), ("etf_daily", "daily"),
    ("stock_daily_valuation", "daily"),
    ("stock_minutes", "1min"), ("stock_minutes", "5min"),
    ("stock_minutes", "15min"), ("stock_minutes", "30min"),
    ("stock_minutes", "60min"), ("etf_minutes", "1min"),
    ("etf_minutes", "5min"), ("etf_minutes", "15min"),
    ("etf_minutes", "30min"), ("etf_minutes", "60min"),
    # === 快照大表改走 export（绕开 query_snapshot 1万行硬上限）===
    # 这些表 20万~25万行，query_snapshot 服务端硬上限 1 万行（limit>10000 无效），
    # 只能拉到 ~5% 数据。export_dataset 无 1 万限制（只有 200 万 row_limit，这些表
    # 远低于，单次 export 即可完整覆盖）。_export_batches 对它们返回单批（全历史一次）。
    ("index_constituents", "daily"),
    ("balance_statement", "daily"),
    ("cashflow_statement", "daily"),
    ("fin_indicator", "daily"),
    ("income_statement", "daily"),
    # trade_cal has no ts_code column, while the generic export path currently
    # orders by ts_code. It therefore uses cursor pagination like other
    # non-export mapped tables.
}

# QuantStudio canonical 表名 → QuestDB 源表名映射
# 大部分表名一致，以下是需要映射的例外
#
# 【codex 全量审计 2026-08-03 修订】：
#   - sw_industry(←sw_daily)、industry_membership(←sw_weight)、stock_suspend_d(←stock_suspend)
#     三张表语义错配/模型不匹配，已停更（collector_tasks.json enabled=false），
#     故从本映射移除；其 QuestDB 源表 sw_daily/sw_weight 改为 passthrough 同名直通。
#   - sw_daily / sw_weight 有独立价值（行业指数行情 / L1 指数权重快照），作 passthrough。
_CANONICAL_TO_QUESTDB = {
    "balance_statement": "stock_balancesheet",
    "income_statement": "stock_income",
    "cashflow_statement": "stock_cashflow",
    "fin_indicator": "stock_fina_indicator",
    "trade_calendar": "trade_cal",
    "stock_dividend": "ws_exdiv",
    # === 类别A 全量扩展：新增映射表（含 namechange 同名）===
    "stock_daily_valuation": "stock_daily_basic",   # 估值表无 OHLCV，UnitCheck 不拦
    "index_constituents": "index_weight",
    "industry_classification": "sw_classify",        # codex审计：补 column_map + adapter L1 过滤
    "stock_namechange": "stock_namechange",          # 同名（云端 ETL 新建表），DuckDB schema: code/change_date/status_after/name_before/name_after
}

# === 类别B：passthrough 同名表（不走 aligner / validator / source_watermark）===
# DuckDB 表名/列名 = QuestDB 原样（ts_code/trade_date 等保留）。
#
# 【codex 全量审计 2026-08-03 修订】：
#   - sw_daily（行业指数行情）、sw_weight（L1 指数权重快照）从原类别A 错配映射改为
#     passthrough 本名直通（有独立价值，但与 canonical sw_industry/industry_membership
#     语义不同，不再强行映射）。
_PASSTHROUGH_TABLES = frozenset({
    "ai_research_snapshot", "block_trade", "broker_monthly", "broker_recommend",
    "cninfo_first_rating", "cnthesims_events", "cnthesims_factors", "cyq_chips",
    "cyq_perf", "daily_info", "em_first_cover_rating", "etf_adj_factor",
    "etf_sentiment_daily", "etf_share_size", "factor_value", "forecast_vip",
    "gisisi_daily", "hm_detail", "hm_list", "idx_anns", "idx_factor_pro",
    "index_classify", "inst_survey",     "limit_cpt_list", "limit_list_d", "limit_list_ths",
    "limit_step", "llm_text_events", "llm_text_events_enriched", "llm_text_raw_feed",
    "margin", "margin_detail", "margin_secs", "market_breadth_daily",
    "moneyflow_cnt_ths", "moneyflow_ind_ths", "moneyflow_ths", "news_sentiment",
    "report_rc", "rsshub_raw", "sector_top300_daily", "sentiment_factor_daily",
    "slb_len", "stk_auction", "stk_factor_pro", "stk_limit",
    "style_cross_section_daily", "survey_sentiment", "sw_daily", "sw_weight",
    "tdx_theme_etf_overlay", "tdx_theme_llm_scores", "tdx_theme_news",
    "tdx_theme_sentiment_daily", "ths_daily", "ths_hot", "ths_index", "ths_member",
    "top_inst", "top_list", "ws_asfund", "ws_blocktrade", "ws_etf_holders",
    "ws_etf_holdings", "ws_ipo", "ws_lhb", "ws_margintrade", "ws_reserve",
    "xueqiu_sentiment",
})

# 大表（>100万行）：首拉 1亿行量级，默认 disabled，需客户手动启用
_PASSTHROUGH_BIG_TABLES = frozenset({
    "cyq_chips", "cyq_perf", "etf_adj_factor", "etf_share_size", "idx_factor_pro",
    "margin_detail", "margin_secs", "moneyflow_ths", "report_rc", "stk_auction",
    "stk_factor_pro", "stk_limit", "ths_daily", "ths_member",
})

# ===========================================================================
# 线1：is_qfq 还原 raw（adapter 侧还原）
# ---------------------------------------------------------------------------
# 云端 QuestDB 存的是**前复权价**（is_qfq=True，日线实测 100%），锚点为云端因子
# 系列的全局最新因子：
#     qfq_i = raw_i × adj_factor_i / adj_factor_latest_global
# 而本框架契约要求 adapter 只返回 raw（复权统一由 aligner._apply_qfq 负责）。
# 若把 qfq 直接当 raw 交给 aligner，会再算一次 front = qfq × adj_i/adj_latest
# → **双重复权**（实测 300750 4-22：正确 226.312 vs 错误 222.017）。
# 故在 adapter 侧反解还原：
#     raw_i = qfq_i × adj_factor_latest_global / adj_factor_i
#
# 【codex P0-①，最易错】adj_factor_latest_global 必须取自 qfq_aux.db 的**完整
# 因子历史**（全局最新），绝不能用本次 export 分片内最后一行的因子。
# 实测 300750 2024-06-03：用全局 latest(1.9495) 还原 = 202.5001；
# 误用 2024-06 分片末行(1.8660) = 193.8267，**差 8.67 元**。
#
# 还原只作用于价格列；vol/amount/pct_chg 等非价格列原样保留
# （amount 成交额与复权无关，pct_chg 为比率不变量）。
_RESTORE_PRICE_COLS = ("open", "high", "low", "close", "pre_close")
# 需要还原的四张行情表（与 _QFQ_ADJFACTOR_TABLES 同源，按 canonical 表名去重）
_RESTORE_TABLES = frozenset({"stock_daily", "etf_daily",
                             "stock_minutes", "etf_minutes"})
# 因子缺失时的兜底策略：fail-fast。静默放行 = 把 qfq 当 raw 写进主库 = 数据污染，
# 比取数失败严重得多，故宁可让本次任务失败。
_RESTORE_MISSING_FACTOR_FAIL_FAST = True

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
        # 线1：is_qfq 还原 raw 开关（默认开；关闭仅用于对照实验，生产不得关）
        self.enable_qfq_restore = bool(config.get("enable_qfq_restore", True))
        # 线1：因子冷启动（qfq_aux.db 未覆盖的 code → 全历史导出注入）
        self.enable_adj_coldstart = bool(config.get("enable_adj_coldstart", True))
        # WP7-E3 阶段 2A：export 缓存开关（仅 bootstrap 链路开启，daemon 默认 false）。
        # 开启时 _export_batches 走网格化切分 + 全市场 export parquet 落盘级复用，
        # 跨证券共享同一组缓存键 (table, grid_bs|grid_be)，export 次数从 2181×N 降至 ~N。
        # daemon 日常采集路径不开启 → 每次 fetch 走真实 export_dataset，行为与修复前一致。
        self.export_cache = bool(config.get("export_cache", False))
        # 全局最新因子缓存：{asset_type: {裸码: adj_latest}}；每进程每资产类型只查一次
        self._adj_latest_cache: Dict[str, Dict[str, float]] = {}
        # 已执行过冷启动的资产类型（避免同一进程内重复全历史导出）
        self._coldstart_done: set = set()
        # 优化 A：shard pandas DataFrame LRU 缓存（仅 export_cache=True 的 bootstrap 链路生效）。
        # 同一批次 50 个证券共享 shard 读取：首个证券读后缓存 DataFrame，后续直接取 + isin 过滤。
        # 缓存键 = (文件路径, mtime, size)——防止文件被覆盖后误用旧缓存。
        # LRU 容量 30：覆盖一个批次全部 shard（实测每证券 ~17 个 shard）。
        # 缓存 pandas DataFrame（非 Arrow Table）——缓存命中时零转换（to_pandas 开销 ~0.4s）。
        from collections import OrderedDict
        # 优化 A：ckey 级别 DataFrame LRU 缓存（每 ckey 的全 shard concat 后缓存为一个 DataFrame）。
        # 缓存键 = (table, ckey, mtime指纹)——同 ckey 跨证券共享（grid_aligned 保证全证券 ckey 一致）。
        # LRU 容量 30：覆盖一个批次全部 ckey（ETF ~13 + STOCK ~17 = ~30）。
        self._shard_table_cache: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
        self._SHARD_CACHE_MAX = 30
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

    @staticmethod
    def is_passthrough(table: str) -> bool:
        """类别B 同名 passthrough 表判定（不走 aligner/validator/watermark）。"""
        return table in _PASSTHROUGH_TABLES

    # ------------------------------------------------------------------
    # 核心：fetch_table
    # ------------------------------------------------------------------
    def fetch_table(self, table: str, start: str, end: str,
                    freq: str = "daily",
                    codes: Optional[List[str]] = None) -> Tuple[pd.DataFrame, Dict]:
        """拉取单表数据，返回 (raw_df, metadata)。

        - 大型映射表（股票/ETF K 线、估值、财务、指数成分）：
          client.export_dataset 落 Raw Landing → 分片读取 → concat
        - 其余映射表与 passthrough 表：client.fetch_page cursor 完整分页；
          禁止 query_snapshot 的 10,000 行静默截断进入生产采集路径
        - 返回 raw OHLC（+adj_factor 列原样），不做复权
        - §7.2-A：取 stock_dividend / 含 adj_factor 表时写 DB 驱动 discovery
        """
        ok, reason = self.supports_task(table, freq)
        if not ok:
            raise ValueError(
                f"[MCPAdapter] 不支持的表/频率: ({table},{freq})，"
                f"MCP 支持矩阵: {sorted(_MCP_SUPPORTED)}")

        # === 类别B passthrough：通过 fetch_page 完整分页取 raw，不做 column_map/
        #     不 normalize_adj_factor / 不走 aligner / 不注入 QFQ ===
        if table in _PASSTHROUGH_TABLES:
            raw_df, meta = self._fetch_passthrough(table, freq, start, end, codes)
            meta["passthrough"] = True
            return raw_df, meta

        key = (table, freq)
        if key in _EXPORT_TABLES:
            raw_df, meta = self._fetch_export(table, freq, start, end, codes)
        else:
            raw_df, meta = self._fetch_small_table(table, freq, start, end, codes)

        # §7.2-A QFQ 数据注入
        if self.enable_qfq_injection:
            _t_inj = time.perf_counter() if _QFQ_PROFILE else 0
            self._inject_qfq_inputs(table, freq, raw_df, meta)
            if _QFQ_PROFILE:
                _profile_logger.info(
                    f"PROFILE_2b inject_qfq {table}/{freq} rows={len(raw_df)} "
                    f"time={time.perf_counter() - _t_inj:.3f}s")

        return raw_df, meta

    def _fetch_all_pages(self, dataset_id: str,
                         page_size: int = 50_000) -> Tuple[pd.DataFrame, int]:
        """通过已验证的 cursor API 完整拉取一个 MCP 数据集。

        服务端 `query_snapshot` 存在 10,000 行硬上限，即使请求更大的 limit
        也会静默截断；因此所有非 export 管线表都必须通过 fetch_page 分页。
        """
        rows: List[Dict] = []
        cursor = ""
        seen_cursors = set()
        pages = 0
        while True:
            payload = self.client.fetch_page(
                dataset_id=dataset_id, cursor=cursor, page_size=page_size)
            page_rows = payload.get("rows", []) or []
            rows.extend(page_rows)
            pages += 1
            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                break
            next_cursor = str(next_cursor)
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise ValueError(
                    f"[MCPAdapter] fetch_page cursor did not advance for "
                    f"{dataset_id}: {next_cursor!r}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        logger.info(f"[MCPAdapter] fetch_page complete: {dataset_id} "
                    f"pages={pages} rows={len(rows)}")
        return pd.DataFrame(rows), pages

    # ------------------------------------------------------------------
    # 类别B passthrough：分页全量取 raw（原样返回，不做任何映射/归一）
    # ------------------------------------------------------------------
    def _fetch_passthrough(self, table: str, freq: str, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """passthrough 同名表：fetch_page 分页全量返回原始 DataFrame。

        - 列名/表名 = QuestDB 原样（ts_code/trade_date 等保留），不做 column_map
        - 不做 normalize_adj_factor / code_format 归一（无 aligner 消费）
        - 仅做（可选）日期/codes 过滤以便增量范围裁剪，不改列
        - 不触发 §7.2-A QFQ 注入
        """
        qdb_table = _CANONICAL_TO_QUESTDB.get(table, table)
        df, page_count = self._fetch_all_pages(qdb_table)
        # 仅做日期窗口裁剪（不改变列），MCP 列名可能是 date/trade_date
        def _norm_date(v):
            s = str(v).strip()
            if len(s) >= 10 and s[4] == "-":
                s = s[:10].replace("-", "")
            return s[:8]
        date_col = next((c for c in ("date", "trade_date", "cal_date") if c in df.columns), None)
        if len(df) and date_col:
            dcol = df[date_col].map(_norm_date)
            s8, e8 = _norm_date(start), _norm_date(end)
            df = df[(dcol >= s8) & (dcol <= e8)].reset_index(drop=True)
        # codes 过滤（识别 daemon 的 'ALL' 全市场标记，跳过过滤）
        _is_all = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
        if codes and not _is_all and len(df):
            want = {str(c) for c in codes}
            code_col = next((c for c in ("ts_code", "code", "stock_code")
                             if c in df.columns), None)
            if code_col:
                df = df[df[code_col].astype(str).isin(want)].reset_index(drop=True)
        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "fetch_mode": "fetch_page",
            "passthrough": True,
            "upstream_authority": "xtquant",
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "server": self.endpoint,
                "passthrough": True,
                "pages": page_count,
            },
            "rows": len(df),
        }
        logger.info(f"[MCPAdapter] {table}/{freq} passthrough 拉取 {len(df)} 行 "
                    f"(列原样: {list(df.columns)[:8]}{'...' if len(df.columns) > 8 else ''})")
        return df, meta

    # ------------------------------------------------------------------
    # 非 export 映射表：fetch_page cursor 分页
    # ------------------------------------------------------------------
    def _fetch_small_table(self, table: str, freq: str, start: str, end: str,
                           codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        # fetch_page 只负责全量分页；日期/codes 过滤仍在 adapter 侧完成，
        # 保持与其他 source adapter 的 fetch_table 契约一致。
        qdb_table = _CANONICAL_TO_QUESTDB.get(table, table)
        df, page_count = self._fetch_all_pages(qdb_table)
        # 日期窗口过滤（daemon 传入 start/end 为 YYYY-MM-DD；
        # MCP 列名可能是 date/trade_date，值格式可能是 YYYYMMDD 或 YYYY-MM-DD）
        # 统一归一化为 YYYYMMDD 8位字符串再比较，避免格式不匹配导致全滤为 0
        def _norm_date(v):
            s = str(v).strip()
            if len(s) >= 10 and s[4] == "-":
                s = s[:10].replace("-", "")
            return s[:8]
        date_col = next((c for c in ("date", "trade_date", "cal_date") if c in df.columns), None)
        if len(df) and date_col:
            dcol = df[date_col].map(_norm_date)
            s8, e8 = _norm_date(start), _norm_date(end)
            mask = (dcol >= s8) & (dcol <= e8)
            df = df[mask].reset_index(drop=True)
        # codes 过滤（MCP 返回 ts_code 列，格式 600063.SH；部分源也可能是 code 列）
        # 识别 daemon 的 'ALL' 全市场标记，跳过过滤
        _is_all = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
        if codes and not _is_all and len(df):
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

        # === codex 审计 TD-15：industry_classification 常量注入 + L1 过滤 + 去重 ===
        # 云端 sw_classify 含 L1/L2/L3，canonical 只需 L1（对齐 tushare
        # SW2021_L1_EXPECTED_COUNT=31 门控）。常量列 classification_system='SW'/
        # effective_from=0/effective_to=None 由 adapter 补（column_map 无法注入常量）。
        # 实测云端 L1 有重复行（同 index_code 多次 ingest， QuestDB 累积），
        # 按 industry_code 去重保留首行（canonical PK 含 industry_code）。
        # Complete reference-table shape without discarding source fields.
        if table == "stock_basic" and len(df) > 0:
            if "symbol" in df.columns and "code" not in df.columns:
                df["code"] = df["symbol"]
        if table == "trade_calendar" and len(df) > 0:
            # Do not synthesize updated_at here: the host clock can cross the
            # authoritative reporting date. Source lineage is sufficient.
            df["source"] = "mcp"

        if table == "industry_classification" and len(df) > 0:
            if "level" in df.columns:
                before = len(df)
                df = df[df["level"].astype(str).str.upper() == "L1"].reset_index(drop=True)
                if len(df) < before:
                    logger.info(f"[MCPAdapter] industry_classification L1 过滤 "
                                f"{before}→{len(df)} 行")
            if "industry_code" in df.columns:
                before = len(df)
                df = df.drop_duplicates(subset=["industry_code"], keep="first").reset_index(drop=True)
                if len(df) < before:
                    logger.info(f"[MCPAdapter] industry_classification 去重 "
                                f"{before}→{len(df)} 行（按 industry_code）")
            df["classification_system"] = "SW"
            df["effective_from"] = 0
            df["effective_to"] = None
            logger.info(f"[MCPAdapter] industry_classification 注入常量列 "
                        f"(classification_system=SW/effective_from=0) → {len(df)} 行")

        # === 线1：is_qfq 还原 raw（与 _fetch_export 对称接入）===
        # 当前四张行情表都走 export 路径；此处接入是防御性对称——若将来某张
        # 行情表降级为 snapshot 路径，还原逻辑不会被绕过。非行情表由
        # _restore_to_raw 内部按 _RESTORE_TABLES 白名单自动跳过。
        # === 线1：is_qfq 还原 raw（与 _fetch_export 对称接入）===
        # 若将来 QFQ K 线降级为 snapshot，仍执行同一显式白名单与同步门禁；
        # 非 QFQ 表只记录跳过原因，不接触因子库。
        df, restore_meta = self._restore_qfq_if_required(df, table, freq)

        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "fetch_mode": "fetch_page",
            "code_format": "tushare_to_raw",  # 600063.SH → 裸码（aligner 归一）
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "upstream_authority": "xtquant",   # server 侧真相源
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "server": self.endpoint,
                "pages": page_count,
            },
            "rows": len(df),
            "is_qfq_capable": has_adj,
            **restore_meta,          # 线1 追溯字段（codex P1-⑥）
        }
        logger.info(f"[MCPAdapter] {table}/{freq} 小表拉取 {len(df)} 行 "
                    f"(adj_factor={has_adj})")
        return df, meta

    # ------------------------------------------------------------------
    # 行情大表：export_dataset 落 Raw Landing + 分片读取 + 本地日期/codes 过滤
    # ------------------------------------------------------------------
    # 服务端 export_dataset 默认 row_limit=200万行截断（取最老数据）。
    # 全历史行情表（stock_daily 1400万行 / stock_minutes 4.8亿行）必须分批拉取，
    # 每批控制在安全阈值内（日线 ~12个月/批，分钟 ~10天/批），避免截断丢数据。
    _EXPORT_SAFE_ROWS = 1_500_000   # 安全阈值（200万服务端上限留 25% 余量）
    _EXPORT_DAILY_WINDOW_DAYS = 365  # 日线表每批最大窗口（~120万行/年）
    _EXPORT_MINUTE_WINDOW_DAYS = 10  # 分钟表每批最大窗口（~6000万行/年，10天~160万行）
    _EXPORT_ROW_LIMIT_BIG = 5_000_000  # 分钟表 export 的 row_limit（_fetch_export 传参）

    @staticmethod
    def _parse_flexible_date(s: str) -> datetime:
        """Parse a date string that may be ``%Y-%m-%d`` (daemon collector) or
        ``%Y%m%d`` (McpFreshFetcher bootstrap path).  Raises ValueError if
        neither format matches."""
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise ValueError(f"unrecognized date format: {s!r} (expected %Y-%m-%d or %Y%m%d)")

    def _export_batches(self, start: str, end: str, is_minute: bool,
                        est_rows: int = None,
                        grid_aligned: bool = False) -> List[Tuple[str, str]]:
        """按时间窗口切分 export 批次，避免服务端 200 万行截断。

        行数估算驱动：est_rows < _EXPORT_SAFE_ROWS（<200万）时返回单批（全历史一次，
        不切碎）；否则按窗口切分。
        日线行情（~120万行/年）：365天/批；分钟表（~6000万行/年）：10天/批。
        快照大表（财务/指数成分等，<200万行全历史）：单批。

        grid_aligned=True（仅 bootstrap 链路开启）：窗口边界对齐到固定 epoch 网格
        （日线 365 天 / 分钟 10 天），且**尾部批次不截断到 end**（nxt = cur + window）。
        所有证券共享同一组批次边界 → export 缓存键 (table, bs|be) 全证券一致、命中率 100%。
        语义等价：网格扩出的边界数据由 _fetch_export 的 _norm_date 日期裁剪兜底（已有逻辑），
        最终 raw_df 与非网格路径逐值一致。默认 False（daemon 增量采集零影响）。
        """
        s = self._parse_flexible_date(str(start).strip()[:10])
        e = self._parse_flexible_date(str(end).strip()[:10])
        # 估算行数 < 安全阈值 → 单批（不切碎，减少 job 开销）
        if est_rows is not None and est_rows < self._EXPORT_SAFE_ROWS:
            return [(s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"))]
        window = self._EXPORT_MINUTE_WINDOW_DAYS if is_minute else self._EXPORT_DAILY_WINDOW_DAYS
        if grid_aligned:
            # Bug 2 修复：网格化窗口需适配 row_limit，避免服务端截断。
            # 全市场分钟数据 ~1200 万行/10天 > row_limit=5M → 窗口缩到安全值。
            if is_minute:
                # 按表行数估算每日行数，反算安全窗口
                est_total = est_rows or self._EXPORT_ROW_ESTIMATE.get("stock_minutes", 480_000_000)
                # 近 1 年交易日约 243 天，估算每日行数
                daily_rows = est_total / 243
                safe_window = max(1, int(self._EXPORT_ROW_LIMIT_BIG / (daily_rows * 1.2)))
                window = min(window, safe_window)
                logger.info(f"[MCPAdapter] grid_aligned 分钟窗口: {window}天 "
                            f"(daily_rows≈{daily_rows:.0f}, row_limit={self._EXPORT_ROW_LIMIT_BIG})")
            # epoch 天数网格对齐：起点向前对齐到网格边界（只扩不缩）
            epoch = date(1970, 1, 1)
            s_date = epoch + timedelta(days=((s.date() - epoch).days // window) * window)
            e_date = e.date()
            batches = []
            cur = s_date
            while cur <= e_date:
                # 尾部不截断到 e：nxt = cur + window（完整网格边界），
                # 服务端多导的几天由客户端 _norm_date 裁剪兜底。
                nxt = cur + timedelta(days=window)
                batches.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
                cur = nxt + timedelta(days=1)
            return batches
        batches = []
        cur = s
        while cur <= e:
            nxt = min(cur + timedelta(days=window), e)
            batches.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
            cur = nxt + timedelta(days=1)
        return batches

    # 各表数据规模估算（行数，用于 _export_batches 决定是否分批）
    # 来源：docs/mcp_migration/full_table_inventory.json 实测云端行数
    _EXPORT_ROW_ESTIMATE = {
        "stock_daily": 14_000_000, "etf_daily": 2_400_000,
        "stock_minutes": 480_000_000, "etf_minutes": 120_000_000,
        "stock_daily_valuation": 14_000_000,
        # 快照大表（<200万，单批）
        "index_constituents": 250_000, "balance_statement": 210_000,
        "cashflow_statement": 210_000, "fin_indicator": 230_000,
        "income_statement": 210_000, "trade_calendar": 15_000,
    }



    def _fetch_export(self, table: str, freq: str, start: str, end: str,
                      codes: Optional[List[str]]) -> Tuple[pd.DataFrame, Dict]:
        """行情大表（日线/分钟）：导出落 Raw Landing，分片读取避免全量驻留。

        export_dataset 已内部跑完 create_export_job → get_manifest → 逐 shard
        get_artifact（含 SHA256 对账），返回 Artifact 列表。这里落盘 + 分片读回。
        server 的 export 不接收 codes 参数（全量导出），codes 过滤在 adapter 侧完成。
        时间范围下推到服务端（time_start/time_end），但服务端有 200 万行截断，
        故对超长范围自动分批（_export_batches 切分时间窗口）。
        """
        _is_big = "minutes" in table
        qdb_tbl = _CANONICAL_TO_QUESTDB.get(table, table)

        # === 自动分批：按时间窗口切分，避免服务端 200 万行截断 ===
        # grid_aligned 仅在 export_cache=True（bootstrap 链路）时启用：全证券共享网格边界。
        _export_cache = getattr(self, "export_cache", False)
        batches = self._export_batches(start, end, _is_big,
                                       est_rows=self._EXPORT_ROW_ESTIMATE.get(table),
                                       grid_aligned=_export_cache)

        frames: List[pd.DataFrame] = []
        job_id = "export"
        if _export_cache:
            frames, job_id = self._fetch_export_cached(table, freq, batches, qdb_tbl, _is_big, codes=codes)
        else:
            frames, job_id = self._fetch_export_direct(table, freq, batches, qdb_tbl, _is_big)
        logger.info(f"[MCPAdapter] {table}/{freq} export 分批={len(batches)} 总分片={len(frames)}")

        raw_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        # index_weight contains both six-digit exchange indices and Hxxxxx.CSI indices.
        # The current QuantStudio canonical contract supports six-digit index_code values;
        # filter out-of-contract source rows before alignment instead of converting them
        # to NULL and sending them to the quality quarantine. Member ts_code is normalized by aligner.
        filtered_out_of_contract = 0
        filtered_index_codes: List[str] = []
        if table == "index_constituents" and len(raw_df) and "index_code" in raw_df.columns:
            import re
            index_mask = raw_df["index_code"].astype(str).map(
                lambda value: bool(re.match(r"^\d{6}\.(SH|SZ|BJ|SI)$", value)))
            filtered_out_of_contract = int((~index_mask).sum())
            if filtered_out_of_contract:
                filtered_index_codes = sorted(
                    raw_df.loc[~index_mask, "index_code"].astype(str).unique().tolist())
                logger.warning(
                    f"[MCPAdapter] index_constituents filtered out-of-contract indices "
                    f"rows={filtered_out_of_contract} codes={len(filtered_index_codes)} "
                    f"sample={filtered_index_codes[:5]}")
                raw_df = raw_df[index_mask].reset_index(drop=True)

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
        elif len(raw_df):
            # 分钟表：trade_time/time（取日期部分）——与 _filter_date_window 同修复
            minute_col = next((c for c in ("trade_time", "time") if c in raw_df.columns), None)
            if minute_col:
                dcol = pd.to_datetime(raw_df[minute_col]).dt.strftime("%Y%m%d")
                s8, e8 = _norm_date(start), _norm_date(end)
                raw_df = raw_df[(dcol >= s8) & (dcol <= e8)].reset_index(drop=True)
        # codes 过滤（MCP 返回 ts_code 列，格式 600063.SH）
        # 修复：daemon 全量任务传 codes=['ALL']（全市场标记），需识别并跳过过滤
        # （'ALL' 不是具体代码，isin 会把数据全滤为 0 行）。
        _is_all = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
        if codes and not _is_all and len(raw_df):
            want = {str(c) for c in codes}
            code_col = next((c for c in ("ts_code", "code", "stock_code")
                             if c in raw_df.columns), None)
            if code_col:
                raw_df = raw_df[raw_df[code_col].astype(str).isin(want)].reset_index(drop=True)
                logger.info(f"[MCPAdapter] codes 过滤({code_col})→ {len(raw_df)} 行")
            else:
                logger.warning(f"[MCPAdapter] 无 code 类列可过滤 codes，返回全量 {len(raw_df)} 行")

        # === 测试代码过滤（云端数据卫生）===
        # 云端 stock_daily/etf_daily 等含 TEST* 测试代码（如 TEST999.SH），
        # adj_factor=NaN（无因子），导致线1还原缺因子 fail-fast。
        # 过滤掉非标准代码（不是 6位数字.SH/.SZ/.BJ 格式），保留真实证券。
        code_col = next((c for c in ("ts_code", "code") if c in raw_df.columns), None)
        if code_col and len(raw_df):
            import re
            std_re = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
            mask = raw_df[code_col].astype(str).map(lambda x: bool(std_re.match(x)))
            n_filtered = (~mask).sum()
            if n_filtered > 0:
                test_codes = raw_df[~mask][code_col].unique().tolist()
                logger.info(f"[MCPAdapter] 过滤非标准代码 {n_filtered} 行（示例={test_codes[:5]}）")
                raw_df = raw_df[mask].reset_index(drop=True)

        # === 线1：is_qfq 还原 raw ===
        # 云端存 qfq，而 adapter 契约是"只返回 raw"（复权由 aligner 统一负责）。
        # 必须在返回前还原，否则 aligner 会二次复权。注意：必须放在日期/codes
        # 过滤之后，但还原用的 adj_latest 来自 qfq_aux.db 全量因子历史，
        # **与本次窗口无关**（codex P0-①）。
        #
        # 【修复（2026-08-03 因子锚过期）】：export 数据的 adj_factor 列是云端
        # 最新状态（含新除权重锚），但 qfq_aux.db 快照可能滞后（静态灌库）。
        # 在还原前先用本次 export 的 adj_factor 增量注入 qfq_aux.db，保持快照
        # 跟上云端动态重锚——这不是"取分片末行"（codex P0-①），而是增量同步
        # 快照到云端最新状态。还原检查用的仍是 qfq_aux.db 全局最新因子。
        # === 线1：is_qfq 还原 raw ===
        # 只有显式登记在 _QFQ_ADJFACTOR_TABLES 的股票/ETF K 线才允许进入
        # 因子同步与 qfq→raw 还原。export 只是传输方式：指数成分、财务、估值、
        # 指数行情等表即使也走 export，也绝不能据此被当作可复权行情。
        # 真正的 QFQ 表仍保持严格 fail-fast：同步失败时禁止把 qfq 当 raw 放行。
        raw_df, restore_meta = self._restore_qfq_if_required(raw_df, table, freq)

        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "fetch_mode": "export",
            "code_format": "tushare_to_raw",
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "upstream_authority": "xtquant",
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "export_job": job_id,
                "shards": len(frames),
                "server": self.endpoint,
            },
            "rows": len(raw_df),
            "raw_landing_dir": str(self._landing_root / job_id),
            "is_qfq_capable": (table, freq) in _QFQ_ADJFACTOR_TABLES,
            "filtered_out_of_contract_rows": filtered_out_of_contract,
            "filtered_out_of_contract_codes": filtered_index_codes[:50],
            **restore_meta,          # 线1 追溯字段（codex P1-⑥）
        }
        logger.info(f"[MCPAdapter] {table}/{freq} 大表拉取 {len(raw_df)} 行 "
                    f"(Raw Landing: {meta['raw_landing_dir']})")
        return raw_df, meta

    def _landing_path(self, job_id: str, shard_name: str) -> Path:
        d = self._landing_root / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{shard_name}.parquet"

    # ==================================================================
    # WP7-E3 阶段 2A：export 缓存（bootstrap 链路专用，daemon 默认不开启）
    # ==================================================================
    _EXPORT_CACHE_MANIFEST = "_export_cache_manifest.json"

    def _cache_manifest_path(self) -> Path:
        return self._landing_root / self._EXPORT_CACHE_MANIFEST

    def _load_cache_manifest(self) -> Dict:
        """读取 export 缓存 manifest；损坏/缺失返回 {} （调用方据此走未命中路径）。"""
        p = self._cache_manifest_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[MCPAdapter] export_cache manifest 损坏，回退空 manifest: {e}")
            return {}

    def _save_cache_manifest(self, manifest: Dict) -> None:
        """原子写 manifest（写临时文件后 replace，防半写）。"""
        p = self._cache_manifest_path()
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError as e:  # pragma: no cover - 磁盘问题不阻断取数
            logger.warning(f"[MCPAdapter] export_cache manifest 写入失败（不影响取数）: {e}")

    @staticmethod
    def _cache_key(table: str, bs: str, be: str) -> str:
        """缓存键：table + 网格化批次边界。全证券共享（grid_aligned 保证边界一致）。"""
        return f"{table}|{bs}|{be}"

    def _fetch_export_direct(self, table: str, freq: str,
                             batches: List[Tuple[str, str]],
                             qdb_tbl: str,
                             _is_big: bool) -> Tuple[List[pd.DataFrame], str]:
        """直连路径（daemon 默认）：逐批 export_dataset → 落盘 → 分片读回。
        与修复前 _fetch_export 行为逐行一致。"""
        all_artifacts = []
        job_ids = []
        for i, (bs, be) in enumerate(batches):
            def _to_iso(d: str) -> str:
                s = str(d).strip()[:10]
                return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00")
            ts_iso = _to_iso(bs)
            te_iso = (datetime.strptime(str(be).strip()[:10], "%Y-%m-%d")
                      + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            try:
                if len(batches) > 1:
                    logger.info(f"[MCPAdapter] {table}/{freq} export 批次 {i+1}/{len(batches)}: "
                                f"{bs} → {be}")
                arts = self.client.export_dataset(
                    dataset_id=qdb_tbl, page_size=50_000,
                    time_start=ts_iso, time_end=te_iso,
                    row_limit=5_000_000 if _is_big else None)
                all_artifacts.extend(arts)
                jid = (arts[0].raw.get("job_id") if arts and arts[0].raw.get("job_id") else f"export_{i}")
                job_ids.append(jid)
            except Exception as _e:
                logger.error(f"[_fetch_export_direct] export_dataset 异常(批次{i+1}): "
                             f"type={type(_e).__name__} msg={_e}")
                raise
        job_id = job_ids[0] if job_ids else "export"
        frames: List[pd.DataFrame] = []
        for art in all_artifacts:
            local_parquet = self._landing_path(job_id, art.artifact_id.replace("/", "_"))
            local_parquet.write_bytes(art.parquet_bytes)
            df_shard = pd.read_parquet(local_parquet)
            frames.append(df_shard)
            logger.debug(f"[MCPAdapter] 分片 {art.artifact_id} 读取 {len(df_shard)} 行 "
                         f"→ Raw Landing {local_parquet.name}")
        return frames, job_id

    def _read_ckey_cached(self, ckey: str, shard_paths: list) -> pd.DataFrame:
        """优化 A：ckey 级别 DataFrame LRU 缓存。

        一个 ckey 的所有 shard concat 后缓存为一个 DataFrame（全市场未过滤）。
        后续证券同 ckey 命中缓存 → 内存 isin 过滤（零 I/O）。

        缓存键 = ckey + 文件指纹（首 shard 的 mtime+size，防文件覆盖后误用）。
        LRU 容量 30：覆盖一个批次全部 ckey。
        """
        # 文件指纹：用第一个 shard 的 mtime+size 做 quick check
        first_sp = shard_paths[0]
        first_stat = first_sp.stat()
        cache_key = f"{ckey}|{int(first_stat.st_mtime)}|{first_stat.st_size}"
        cached = self._shard_table_cache.get(cache_key)
        if cached is not None:
            self._shard_table_cache.move_to_end(cache_key)
            if _QFQ_PROFILE:
                self._shard_cache_hits = getattr(self, '_shard_cache_hits', 0) + 1
            return cached
        # 未命中：逐 shard read_parquet + concat
        parts = []
        for sp in shard_paths:
            sdf = pd.read_parquet(sp)
            if len(sdf):
                parts.append(sdf)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        self._shard_table_cache[cache_key] = df
        while len(self._shard_table_cache) > self._SHARD_CACHE_MAX:
            self._shard_table_cache.popitem(last=False)
        if _QFQ_PROFILE:
            self._shard_cache_misses = getattr(self, '_shard_cache_misses', 0) + 1
            _profile_logger.info(
                f"PROFILE_2a_cache MISS ckey={ckey} shards={len(shard_paths)} "
                f"rows={len(df)} cache_size={len(self._shard_table_cache)} "
                f"hits={getattr(self, '_shard_cache_hits', 0)} "
                f"misses={getattr(self, '_shard_cache_misses', 0)}")
        return df

    def _fetch_export_cached(self, table: str, freq: str,
                             batches: List[Tuple[str, str]],
                             qdb_tbl: str,
                             _is_big: bool,
                             codes: Optional[List[str]] = None) -> Tuple[List[pd.DataFrame], str]:
        """缓存路径（仅 bootstrap 链路）：命中读本地 parquet 跳过 export_dataset；
        未命中走 export → 落盘 → 写 manifest。命中失败自动回退直连。
        缓存只存全市场 export 的 parquet（与 codes 无关）。
        WP7-E3 优化：命中时传入 codes，逐片读→codes 过滤→只保留过滤结果（避免全量 concat OOM）。
        未命中时落盘全市场 parquet（不过滤，供后续证券复用）。"""
        manifest = self._load_cache_manifest()
        table_cache: Dict = manifest.get(table, {})
        frames: List[pd.DataFrame] = []
        job_id = "export"
        manifest_dirty = False
        for bs, be in batches:
            ckey = self._cache_key(table, bs, be)
            entry = table_cache.get(ckey)
            hit = False
            if entry is not None:
                # 逐文件 size 校验（防半写文件）；任一文件缺失/size 不符 → 回退直连
                shards_info = entry.get("shards", [])
                shard_sizes = entry.get("shard_sizes", {})
                all_ok = True
                shard_paths = []
                for shard_name in shards_info:
                    sp = self._landing_root / entry.get("job_id", "export") / f"{shard_name}.parquet"
                    shard_paths.append(sp)
                    if not sp.exists() or sp.stat().st_size != shard_sizes.get(shard_name, -1):
                        all_ok = False
                        break
                if all_ok and shard_paths:
                    # 命中：ckey 级别缓存读取 → codes 过滤
                    _is_all = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
                    want_codes = None if _is_all else ({str(c) for c in codes} if codes else None)
                    _t_shard_total = time.perf_counter() if _QFQ_PROFILE else 0
                    # 优化 A：ckey 级别缓存（全 shard concat 后缓存，跨证券共享）
                    full_df = self._read_ckey_cached(ckey, shard_paths)
                    if _QFQ_PROFILE: _t_shard_total = time.perf_counter() - _t_shard_total
                    if len(full_df) > 0 and want_codes is not None:
                        code_col = next((c for c in ("ts_code", "code", "stock_code")
                                         if c in full_df.columns), None)
                        if code_col:
                            sdf = full_df[full_df[code_col].astype(str).isin(want_codes)]
                        else:
                            sdf = full_df
                    else:
                        sdf = full_df
                    if len(sdf):
                        frames.append(sdf)
                    hit = True
                    if _QFQ_PROFILE and _t_shard_total is not None:
                        _profile_logger.info(
                            f"PROFILE_2a shard_read {ckey} shards={len(shard_paths)} "
                            f"time={_t_shard_total:.3f}s")
                    logger.info(f"[MCPAdapter] export_cache 命中 {ckey} "
                                f"分片={len(shard_paths)} codes过滤={'ALL' if _is_all else len(want_codes or [])}→{sum(len(f) for f in frames)}行")
                else:
                    logger.info(f"[MCPAdapter] export_cache 校验失败 {ckey}，回退直连")
            if not hit:
                # 未命中/校验失败 → export 落盘（逐批落盘不累积 artifact bytes）
                # 然后只读回目标 codes 的行（避免全量 concat OOM）
                miss_paths, one_job_id = self._resolve_shard_paths(
                    table, freq, [(bs, be)], qdb_tbl, _is_big)
                # 落盘后，用 ckey 级别缓存（与命中路径一致）
                _is_all_miss = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
                want_codes_miss = None if _is_all_miss else ({str(c) for c in codes} if codes else None)
                full_df_miss = self._read_ckey_cached(ckey, miss_paths)
                if len(full_df_miss) > 0 and want_codes_miss is not None:
                    code_col_miss = next((c for c in ("ts_code", "code", "stock_code")
                                          if c in full_df_miss.columns), None)
                    if code_col_miss:
                        sdf = full_df_miss[full_df_miss[code_col_miss].astype(str).isin(want_codes_miss)]
                    else:
                        sdf = full_df_miss
                else:
                    sdf = full_df_miss
                if len(sdf):
                    frames.append(sdf)
                # 记录本批实际写入的 parquet 文件到 manifest（不用 glob 全目录，避免旧文件混入）
                shards_info = [p.stem for p in miss_paths]  # 去掉 .parquet 后缀
                shard_sizes = {p.stem: p.stat().st_size for p in miss_paths}
                table_cache[ckey] = {
                    "job_id": one_job_id,
                    "shards": shards_info,
                    "shard_sizes": shard_sizes,
                    "bytes": sum(shard_sizes.values()),
                    "rows": sum(len(f) for f in frames),
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                }
                manifest_dirty = True
                if job_id == "export":
                    job_id = one_job_id
        if manifest_dirty:
            manifest[table] = table_cache
            self._save_cache_manifest(manifest)
        return frames, job_id

    # ==================================================================
    # WP7-E3 阶段 2B：流式分片处理（解决大表内存峰值）
    # ==================================================================
    # 行情大表可安全分片：aligner 复权逐行公式（传 adj_latest_map 快照）、
    # validator 聚合计数、watermark max 聚合，均无跨行全局依赖。
    # 因子注入时序（修正 2）：两遍流程——第一遍聚合全部分片 adj_factor 完成注入
    # （等价直连 _sync_factor_snapshot 全量语义）→ 预取 adj_latest_map → 第二遍
    # 逐片用同一快照还原 → yield。保证跨分片基准一致（铁律：与直连路径逐值等价）。

    # 5 类行情大表（任务书 §2.2 已核对立即可分片）
    _STREAMING_TABLES = frozenset({
        "stock_daily", "etf_daily", "stock_daily_valuation",
        "stock_minutes", "etf_minutes",
    })

    def fetch_table_streaming(self, table: str, start: str, end: str,
                              freq: str = "daily",
                              codes: Optional[List[str]] = None
                              ) -> Tuple[Dict, "object"]:
        """流式拉取单表数据，返回 (metadata, shard_iter)。

        逐 shard yield DataFrame（不 concat），内存峰值 = 单分片量级（~200-300MB）
        而非全量驻留（4.6 亿行 40GB+）。

        - 行情大表（_STREAMING_TABLES）：两遍流程（因子注入→快照→逐片还原→yield）。
        - 其余表：透传 fetch_table → yield 单片（无内存收益，API 一致性）。

        metadata 与 fetch_table 对齐（source/freq/table/lineage 等），供 daemon 使用。
        shard_iter yield 的每个 DataFrame 已完成 codes/日期过滤 + qfq 还原（行情表），
        与 fetch_table 的 raw_df 语义一致——调用方（daemon aligner）零感知。
        """
        if table not in self._STREAMING_TABLES:
            # 非行情大表：透传单 df（yield 一片）
            raw_df, meta = self.fetch_table(table, start, end, freq=freq, codes=codes)
            return meta, iter([raw_df])
        # 行情大表：走流式两遍流程
        return self._streaming_export(table, freq, start, end, codes)

    def _streaming_export(self, table: str, freq: str, start: str, end: str,
                          codes: Optional[List[str]]
                          ) -> Tuple[Dict, "object"]:
        """行情大表流式两遍流程。"""
        _is_big = "minutes" in table
        qdb_tbl = _CANONICAL_TO_QUESTDB.get(table, table)
        _export_cache = getattr(self, "export_cache", False)
        batches = self._export_batches(start, end, _is_big,
                                       est_rows=self._EXPORT_ROW_ESTIMATE.get(table),
                                       grid_aligned=_export_cache)
        # 获取分片 parquet 路径列表（命中缓存或 export 落盘）
        shard_paths, job_id = self._resolve_shard_paths(table, freq, batches,
                                                        qdb_tbl, _is_big)
        # codes 过滤辅助
        _is_all = codes and (len(codes) == 1 and str(codes[0]).upper() == "ALL")
        want_codes = None if _is_all else ({str(c) for c in codes} if codes else None)

        # === 两遍流程（修正 2）===
        # 第一遍：逐片只读因子列 → 逐片注入（不 concat 全量，避免内存峰值）。
        # _inject_adjfactor 是幂等 INSERT OR REPLACE，逐片调用语义等价于全量注入。
        # 同时收集全部 code 用于预取 adj_latest_map 快照。
        needs_qfq = self._requires_qfq_restore(table, freq) and self.enable_qfq_restore
        adj_latest_map: Optional[Dict[str, float]] = None
        if needs_qfq:
            asset_type = self._asset_type_of(table)
            total_injected = 0
            all_codes = set()
            # 优化 1（连接复用）：循环外单连接 + CREATE TABLE 一次，逐片 executemany，
            # 全部片写完后统一 commit。N 次连接/事务 → 1 次。
            _aux_conn = None
            if self.main_db is not None:
                aux_p = aux_db_path(self.main_db)
                _target_tbl = "fund_adj" if asset_type == "ETF" else "adj_factor"
                _aux_conn = sqlite3.connect(str(aux_p), timeout=30)
                _aux_conn.execute("PRAGMA journal_mode=WAL")
                _aux_conn.execute("PRAGMA busy_timeout=30000")
                _aux_conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {_target_tbl} ("
                    f"code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY (code, time))")
            for sp in shard_paths:
                # 列投影：adj_factor + ts_code(→code) + trade_date/trade_time(→time)
                proj = []
                for c in ("adj_factor", "ts_code", "trade_date", "trade_time"):
                    try:
                        if self._parquet_has_column(sp, c):
                            proj.append(c)
                    except Exception:
                        pass
                if "adj_factor" not in proj:
                    continue
                sdf = pd.read_parquet(sp, columns=proj)
                if len(sdf) == 0:
                    continue
                # 逐片注入（连接复用 + 按日去重，等价全量注入）
                written = self._inject_adjfactor(sdf, freq, table, conn=_aux_conn)
                total_injected += written
                # 收集 code（用于预取快照）
                cc = next((c for c in ("ts_code", "code", "stock_code") if c in sdf.columns), None)
                if cc:
                    all_codes.update(sdf[cc].map(self._bare_code).unique().tolist())
                del sdf  # 立即释放
            if _aux_conn is not None:
                _aux_conn.commit()
                _aux_conn.close()
            # 清缓存：逐片注入后 _adj_latest_cache 可能含中间状态，清空确保预取最新
            self._adj_latest_cache.pop(asset_type, None)
            if total_injected <= 0 and len(shard_paths) > 0:
                raise ValueError(
                    f"[MCPAdapter] streaming {table}/{freq} 第一遍因子同步失败（注入 0 行）")
            logger.info(f"[MCPAdapter] streaming {table}/{freq} 第一遍逐片注入 {total_injected} 行因子")
            # 预取全局最新因子快照（全量注入完成后 → 跨分片基准一致）
            adj_latest_map = self._get_adj_latest_global(
                sorted(all_codes), asset_type=asset_type) if all_codes else {}

        def _shard_iter():
            for sp in shard_paths:
                df_shard = pd.read_parquet(sp)
                if len(df_shard) == 0:
                    continue
                # 日期裁剪（与 _fetch_export 的 _norm_date 一致）
                df_shard = self._filter_date_window(df_shard, start, end)
                # codes 过滤
                if want_codes is not None:
                    df_shard = self._filter_codes(df_shard, want_codes)
                # 非标准代码过滤（测试代码）
                df_shard = self._filter_nonstandard_codes(df_shard)
                # qfq 还原（第二遍：用预取快照，skip_sync=True）
                if needs_qfq:
                    df_shard, _rm = self._restore_qfq_if_required(
                        df_shard, table, freq,
                        adj_latest_map=adj_latest_map, skip_sync=True)
                yield df_shard

        meta = {
            "source": "mcp",
            "freq": freq,
            "table": table,
            "fetch_mode": "export_streaming",
            "code_format": "tushare_to_raw",
            "date_format": "YYYYMMDD",
            "units": {"vol": "股", "amount": "元", "pct_chg": "%"},
            "upstream_authority": "xtquant",
            "lineage": {
                "upstream_authority": "xtquant",
                "transport": "mcp_streamable_http",
                "export_job": job_id,
                "shards": len(shard_paths),
                "server": self.endpoint,
                "streaming": True,
            },
            "rows": None,  # 流式未知总行数，daemon 累加
            "raw_landing_dir": str(self._landing_root / job_id),
            "is_qfq_capable": (table, freq) in _QFQ_ADJFACTOR_TABLES,
        }
        return meta, _shard_iter()

    def _resolve_shard_paths(self, table: str, freq: str,
                             batches: List[Tuple[str, str]],
                             qdb_tbl: str,
                             _is_big: bool) -> Tuple[List[Path], str]:
        """获取分片 parquet 路径列表：逐批 export 落盘、不累积 artifact bytes 到内存。

        与 _fetch_export_direct 的区别：后者收集所有 artifact 的 parquet_bytes 到
        all_artifacts 列表（内存峰值=全量），此处逐批 export → 逐 shard write_bytes
        → 立即释放 artifact 引用 → 只保留落盘路径。内存峰值=单批 artifact 量级。
        """
        # 始终走直连逐批落盘（不查缓存，避免与 _fetch_export_cached 循环调用）。
        # 缓存命中/未命中由 _fetch_export_cached 自身管理；此处只负责落盘 + 返回路径。
        paths: List[Path] = []
        # 唯一 job_id 前缀（table+freq+时间戳），避免不同 export 混入同一目录
        import uuid
        job_id = f"exp_{table}_{uuid.uuid4().hex[:8]}"
        for i, (bs, be) in enumerate(batches):
            def _to_iso(d: str) -> str:
                s = str(d).strip()[:10]
                return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00")
            ts_iso = _to_iso(bs)
            te_iso = (datetime.strptime(str(be).strip()[:10], "%Y-%m-%d")
                      + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            if len(batches) > 1:
                logger.info(f"[MCPAdapter] {table}/{freq} export 批次 {i+1}/{len(batches)}: "
                            f"{bs} → {be}")
            arts = self.client.export_dataset(
                dataset_id=qdb_tbl, page_size=50_000,
                time_start=ts_iso, time_end=te_iso,
                row_limit=5_000_000 if _is_big else None)
            if arts:
                jid = (arts[0].raw.get("job_id") if arts[0].raw.get("job_id") else f"export_{i}")
                if job_id == "export":
                    job_id = jid
                for art in arts:
                    local_parquet = self._landing_path(job_id, art.artifact_id.replace("/", "_"))
                    local_parquet.write_bytes(art.parquet_bytes)
                    paths.append(local_parquet)
                    # art 引用在此循环迭代结束后释放，不累积
        logger.info(f"[MCPAdapter] {table}/{freq} 流式 export 分批={len(batches)} 落盘分片={len(paths)}")
        return paths, job_id

    @staticmethod
    def _parquet_has_column(path: Path, col: str) -> bool:
        """检查 parquet 文件是否含指定列（用 pyarrow schema，不全量读）。"""
        try:
            import pyarrow.parquet as pq
            schema = pq.read_schema(str(path))
            return col in schema.names
        except Exception:
            return False

    def _filter_date_window(self, df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        """日期窗口过滤（与 _fetch_export 的 _norm_date 逻辑一致）。

        Bug 修复：分钟表的时间列是 trade_time/time（非 trade_date/date），
        之前只认 date/trade_date → 分钟表跳过裁剪（extra 行根因）。
        现在：优先 trade_date/date（日线），其次 trade_time/time（分钟，取日期部分）。
        """
        if len(df) == 0:
            return df
        def _norm_date(v):
            s = str(v).strip()
            if len(s) >= 10 and s[4] == "-":
                s = s[:10].replace("-", "")
            return s[:8]
        # 日线列优先，分钟列其次（取前 10 字符 YYYY-MM-DD 或前 8 字符 YYYYMMDD）
        date_col = next((c for c in ("date", "trade_date") if c in df.columns), None)
        if date_col is None:
            # 分钟表：trade_time/time 是 datetime，取日期部分
            minute_col = next((c for c in ("trade_time", "time") if c in df.columns), None)
            if minute_col:
                dcol = pd.to_datetime(df[minute_col]).dt.strftime("%Y%m%d")
                s8, e8 = _norm_date(start), _norm_date(end)
                df = df[(dcol >= s8) & (dcol <= e8)].reset_index(drop=True)
        else:
            dcol = df[date_col].map(_norm_date)
            s8, e8 = _norm_date(start), _norm_date(end)
            df = df[(dcol >= s8) & (dcol <= e8)].reset_index(drop=True)
        return df

    def _filter_codes(self, df: pd.DataFrame, want: set) -> pd.DataFrame:
        """codes 过滤（与 _fetch_export 逻辑一致）。"""
        if len(df) == 0:
            return df
        code_col = next((c for c in ("ts_code", "code", "stock_code")
                         if c in df.columns), None)
        if code_col:
            return df[df[code_col].astype(str).isin(want)].reset_index(drop=True)
        return df

    def _filter_nonstandard_codes(self, df: pd.DataFrame) -> pd.DataFrame:
        """非标准代码过滤（剔除 TEST* 测试代码，与 _fetch_export 一致）。"""
        if len(df) == 0:
            return df
        code_col = next((c for c in ("ts_code", "code") if c in df.columns), None)
        if code_col is None:
            return df
        import re
        std_re = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
        mask = df[code_col].astype(str).map(lambda x: bool(std_re.match(x)))
        n_filtered = (~mask).sum()
        if n_filtered > 0:
            df = df[mask].reset_index(drop=True)
        return df

    # ==================================================================
    # 线1：is_qfq 还原 raw —— raw_i = qfq_i × adj_latest_global / adj_i
    # ==================================================================
    @staticmethod
    def _bare_code(v) -> str:
        """600063.SH / 300750.SZ → 裸码（与 qfq_aux.db、discovery 口径一致）。"""
        return str(v).split(".")[0].strip()

    @staticmethod
    def _asset_type_of(table: str) -> str:
        return "ETF" if str(table).startswith("etf") else "STOCK"

    @staticmethod
    def _requires_qfq_restore(table: str, freq: str) -> bool:
        """Return whether this exact canonical table/frequency carries cloud qfq."""
        return (str(table), str(freq)) in _QFQ_ADJFACTOR_TABLES

    def _restore_qfq_if_required(self, df: pd.DataFrame, table: str,
                                 freq: str,
                                 adj_latest_map: Optional[Dict[str, float]] = None,
                                 skip_sync: bool = False) -> Tuple[pd.DataFrame, Dict]:
        """Synchronize factors and restore raw only for explicit QFQ price tables.

        Export routing is intentionally irrelevant: non-price datasets can use
        export_dataset for completeness without touching the factor snapshot.

        WP7-E3 阶段 2B 流式参数：
        - adj_latest_map：预取的全局最新因子快照（两遍流程第二遍传入，跳过内部查询）。
        - skip_sync：跳过 _sync_factor_snapshot（两遍流程第一遍已统一同步）。
        """
        if not self.enable_qfq_restore:
            return df, {
                "is_qfq_restored": False,
                "restored_rows": 0,
                "restore_skip_reason": "qfq_restore_disabled",
            }
        if not self._requires_qfq_restore(table, freq):
            return df, {
                "is_qfq_restored": False,
                "restored_rows": 0,
                "restore_skip_reason": f"table_freq_not_in_qfq_scope:{table}/{freq}",
            }
        if df is None or len(df) == 0:
            return df, {
                "is_qfq_restored": False,
                "restored_rows": 0,
                "restore_skip_reason": "empty_df",
            }
        if not skip_sync:
            if not self._sync_factor_snapshot(df, table, freq):
                raise ValueError(
                    f"[MCPAdapter] {table}/{freq} factor snapshot synchronization failed; "
                    f"refusing qfq restore with a potentially stale anchor")
        return self._restore_to_raw(df, table, freq, adj_latest_map=adj_latest_map)

    def _get_adj_latest_global(self, codes, asset_type: str = "STOCK") -> Dict[str, float]:
        """取每个 code 的**全局最新** adj_factor（codex P0-① 的关键实现）。

        真相源：qfq_aux.db 的 adj_factor(STOCK) / fund_adj(ETF) 表，
        它保存的是该证券的**完整因子历史**，因此 `ORDER BY time DESC LIMIT 1`
        得到的就是全局最新因子（1.9495 for 300750），与本次 export 的
        日期窗口无关。

        **绝不可**改成从传入的分片 DataFrame 里取 max(time) 那行的因子——
        历史窗口拉取时分片末行远早于今天，会算出严重错误的 raw
        （300750 2024-06：202.5001 → 193.8267，差 8.67 元）。

        Args:
            codes: 代码可迭代对象，可带 .SH/.SZ 后缀（内部转裸码）
            asset_type: "STOCK" → adj_factor 表；"ETF" → fund_adj 表
        Returns:
            {裸码: adj_latest_global}；查不到的 code 不出现在返回值中
            （由调用方按 _RESTORE_MISSING_FACTOR_FAIL_FAST 决定处理方式）
        """
        want = {self._bare_code(c) for c in codes if str(c).strip() != ""}
        if not want:
            return {}
        cache = self._adj_latest_cache.setdefault(asset_type, {})
        missing = sorted(want - set(cache.keys()))
        if missing:
            found = self._query_adj_latest(missing, asset_type)
            cache.update(found)
            still = sorted(set(missing) - set(found.keys()))
            # 冷启动：qfq_aux.db 未覆盖的新 code → 全历史因子导出注入后重查
            if still and self.enable_adj_coldstart and asset_type not in self._coldstart_done:
                logger.warning(
                    f"[MCPAdapter] 线1 因子冷启动触发（asset_type={asset_type}）："
                    f"{len(still)} 个 code 在 qfq_aux.db 无因子历史，"
                    f"示例={still[:5]}")
                try:
                    self._coldstart_adj_factors(asset_type)
                finally:
                    self._coldstart_done.add(asset_type)
                found2 = self._query_adj_latest(still, asset_type)
                cache.update(found2)
        return {c: cache[c] for c in want if c in cache}

    def _query_adj_latest(self, codes: List[str], asset_type: str) -> Dict[str, float]:
        """从 qfq_aux.db 批量读取全局最新因子（按 (code, MAX(time)) 取值）。"""
        if self.main_db is None:
            logger.warning("[MCPAdapter] main_db 未配置，无法读取 qfq_aux.db 因子快照")
            return {}
        aux = aux_db_path(self.main_db)
        if not Path(str(aux)).exists():
            logger.warning(f"[MCPAdapter] qfq_aux.db 不存在: {aux}")
            return {}
        table = "fund_adj" if asset_type == "ETF" else "adj_factor"
        out: Dict[str, float] = {}
        try:
            conn = sqlite3.connect(f"file:{aux}?mode=ro", uri=True, timeout=30)
        except Exception as e:
            logger.warning(f"[MCPAdapter] 打开 qfq_aux.db 失败: {e}")
            return {}
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            # SQLite 变量上限（旧版 999）→ 分批 IN 查询
            batch = 900
            for i in range(0, len(codes), batch):
                chunk = codes[i:i + batch]
                marks = ",".join(["?"] * len(chunk))
                # The restore anchor is the factor at the latest time, not the historical maximum.
                # Adjustment factors can decrease after ETF share split/consolidation (for example,
                # 510500 latest=0.3401 while historical max=1.0). MAX(adj_factor) would inflate
                # current raw prices by about 2.94x and cause mass UnitCheck rejection.
                sql = (
                    f"SELECT a.code, a.adj_factor FROM {table} a "
                    f"JOIN (SELECT code, MAX(time) AS mt FROM {table} "
                    f"      WHERE code IN ({marks}) GROUP BY code) m "
                    f"  ON a.code = m.code AND a.time = m.mt")
                for code, adj in conn.execute(sql, chunk).fetchall():
                    if adj is None:
                        continue
                    try:
                        val = float(adj)
                    except (TypeError, ValueError):
                        continue
                    if val > 0:
                        out[str(code)] = val
        except sqlite3.Error as e:
            logger.warning(f"[MCPAdapter] 查询 {table} 全局最新因子失败: {e}")
        finally:
            conn.close()
        return out

    def _sync_factor_snapshot(self, df: pd.DataFrame, table: str, freq: str) -> bool:
        """增量同步因子快照：用本次 export 数据的 adj_factor 更新 qfq_aux.db。

        解决因子锚过期问题（2026-08-03）：qfq_aux.db 快照是静态灌库，跟不上云端
        stock_daily 的动态重锚（新除权时历史行因子更新）。每次 export 后，用本次
        数据的 adj_factor 增量注入 qfq_aux.db，保持快照跟上云端最新状态。
        这不是"取分片末行"（codex P0-①），而是增量同步快照到云端最新状态——
        还原检查用的仍是 qfq_aux.db 全局最新因子（MAX(time) 对应值）。
        """
        if self.main_db is None or "adj_factor" not in df.columns:
            return False
        asset_type = "ETF" if table.startswith("etf") else "STOCK"
        try:
            written = self._inject_adjfactor(df, freq, table)
            if len(df) > 0 and written <= 0:
                logger.error(f"[MCPAdapter] factor snapshot sync wrote no rows "
                             f"(asset_type={asset_type})")
                return False
            self._adj_latest_cache.pop(asset_type, None)
            logger.debug(f"[MCPAdapter] factor snapshot synced "
                         f"(asset_type={asset_type}, rows={written})")
            return True
        except Exception as e:
            logger.error(f"[MCPAdapter] factor snapshot sync failed: {e}")
            return False

    def _coldstart_adj_factors(self, asset_type: str) -> None:
        """冷启动：全历史导出行情表的 adj_factor 并注入 qfq_aux.db。

        实现约束（ZCode 实现提示，已核对 client.py）：
        - `export_dataset` **不支持列选择**（client.py:656 签名无 columns 参数），
          只能全列导出后由 `normalize_mcp_adj_factor_df` 提取因子三元组。
        - `fetch_page`(client.py:531) 虽有 columns 参数可做列裁剪，但该方法在
          当前生产代码中**从未被调用/验证**（返回结构、cursor 语义均未探测），
          按项目铁律不得把生产路径建立在未验证 API 上，故此处不采用；
          待 P2-0 类探针验证后可作为性能优化单独立项。
        - qfq_aux.db 已有 2.3GB 因子快照（STOCK 5793 码），冷启动实际只对未覆盖
          的资产类型触发（当前 fund_adj 为空 → ETF 必触发一次）。
        """
        table = "etf_daily" if asset_type == "ETF" else "stock_daily"
        qdb_tbl = _CANONICAL_TO_QUESTDB.get(table, table)
        # 全历史窗口（起点取足够早的日期，终点取明天以含当日）
        t0 = "1990-01-01T00:00:00"
        t1 = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        logger.warning(
            f"[MCPAdapter] 线1 冷启动开始：全历史导出 {qdb_tbl} 提取 adj_factor "
            f"（asset_type={asset_type}，此操作较重，每进程每类型仅执行一次）")
        try:
            artifacts = self.client.export_dataset(
                dataset_id=qdb_tbl, page_size=50_000,
                time_start=t0, time_end=t1, row_limit=None)
        except Exception as e:
            logger.error(f"[MCPAdapter] 线1 冷启动导出失败({qdb_tbl}): "
                         f"{type(e).__name__}: {e}")
            return
        total = 0
        for art in artifacts:
            try:
                local_parquet = self._landing_path(
                    f"coldstart_adj_{asset_type.lower()}",
                    art.artifact_id.replace("/", "_"))
                local_parquet.write_bytes(art.parquet_bytes)
                df_shard = pd.read_parquet(local_parquet)
                if "adj_factor" not in df_shard.columns:
                    continue
                # 复用既有注入逻辑（内部做 normalize + INSERT OR REPLACE）
                self._inject_adjfactor(df_shard, "daily", table)
                total += len(df_shard)
            except Exception as e:
                logger.warning(f"[MCPAdapter] 线1 冷启动分片处理失败 "
                               f"{art.artifact_id}: {e}")
        logger.warning(f"[MCPAdapter] 线1 冷启动完成：{qdb_tbl} 处理 {total} 行 "
                       f"→ qfq_aux.db({'fund_adj' if asset_type == 'ETF' else 'adj_factor'})")

    def _restore_to_raw(self, df: pd.DataFrame, table: str,
                        freq: str,
                        adj_latest_map: Optional[Dict[str, float]] = None) -> Tuple[pd.DataFrame, Dict]:
        """把云端 qfq 价格列还原为 raw：raw_i = qfq_i × adj_latest_global / adj_i。

        - 只作用于 _RESTORE_TABLES 四张行情表的价格列（OHLC + pre_close）；
          vol / amount / pct_chg 等非价格列原样保留。
        - **全部行走还原公式，不按 is_qfq 分流**（P1-③ 2026-08-03 实测定论，
          commit f7df29c）：is_qfq=False 行并非真 raw，而是「写入时当时最新
          adj_factor」算出的旧基准前复权，直通会把旧基准 qfq 与新数据（全局最新
          基准）混在一起产生跨批次尺度断层（如 300182.SZ 实测 108x）。
          故一律 raw_i = qfq_i × adj_latest_global / adj_i 归一化到全局最新基准：
            · 真 raw 行（adj_i == adj_latest_global）ratio=1 → raw=qfq，数学等价安全；
            · 旧基准前复权行重新归一化到全局最新基准，消除尺度断层。
          is_qfq 列只进 metadata（original_is_qfq_ratio = True 行占比）作追溯，
          不参与「是否还原」的决策。
          无 is_qfq 列时按云端语义默认整批为前复权（日线实测 100% True），
          metadata 标记 is_qfq_col_present=False 供追溯。
        - adj_latest 一律走 `_get_adj_latest_global`（全局最新，见 P0-①）。

        Returns:
            (还原后的 df, restore_meta)。restore_meta 含 is_qfq_restored /
            restored_rows / adj_latest_source 等追溯字段（codex P1-⑥）。
        """
        meta: Dict = {
            "is_qfq_restored": False,
            "restored_rows": 0,
            "restored_codes": 0,
            "adj_latest_source": "qfq_aux.db:adj_factor(global_latest)",
            "restore_formula": "raw = qfq * adj_latest_global / adj_factor_i",
            "is_qfq_col_present": False,
            "original_is_qfq_ratio": None,
            "skipped_rows_no_factor": 0,
            "missing_factor_codes": [],
            "restored_price_cols": [],
        }
        if df is None or len(df) == 0:
            meta["restore_skip_reason"] = "empty_df"
            return df, meta
        if str(table) not in _RESTORE_TABLES:
            meta["restore_skip_reason"] = f"table_not_in_restore_scope:{table}"
            return df, meta
        if "adj_factor" not in df.columns:
            # 无因子列 = 无法还原。若该表本应带因子，属云端契约变更，必须显性失败。
            meta["restore_skip_reason"] = "no_adj_factor_column"
            logger.error(f"[MCPAdapter] 线1 还原失败：{table}/{freq} 缺 adj_factor 列，"
                         f"列={list(df.columns)[:12]}")
            if _RESTORE_MISSING_FACTOR_FAIL_FAST:
                raise ValueError(
                    f"[MCPAdapter] 线1 还原required：{table}/{freq} 云端返回缺少 "
                    f"adj_factor 列，无法把 qfq 还原为 raw。直接放行会导致双重复权，"
                    f"故 fail-fast。")
            return df, meta

        asset_type = self._asset_type_of(table)
        meta["adj_latest_source"] = (
            f"qfq_aux.db:{'fund_adj' if asset_type == 'ETF' else 'adj_factor'}"
            f"(global_latest)")

        code_col = next((c for c in ("ts_code", "code", "stock_code")
                         if c in df.columns), None)
        if code_col is None:
            meta["restore_skip_reason"] = "no_code_column"
            logger.error(f"[MCPAdapter] 线1 还原失败：{table}/{freq} 无 code 类列")
            if _RESTORE_MISSING_FACTOR_FAIL_FAST:
                raise ValueError(
                    f"[MCPAdapter] 线1 还原 required：{table}/{freq} 缺 code 列，"
                    f"无法定位每只证券的全局最新因子。")
            return df, meta

        price_cols = [c for c in _RESTORE_PRICE_COLS if c in df.columns]
        if not price_cols:
            meta["restore_skip_reason"] = "no_price_column"
            return df, meta

        out = df  # 原地改列（调用方已持有本次取数的独立 DataFrame）
        bare = out[code_col].map(self._bare_code)

        # === P1-③（2026-08-03 实测定论）：不分流，全部行走还原公式 =========
        # is_qfq 列只作追溯，不参与「是否还原」的决策。
        if "is_qfq" in out.columns:
            meta["is_qfq_col_present"] = True
            is_qfq_bool = out["is_qfq"].map(_as_bool_qfq)
            qfq_true = int(is_qfq_bool.fillna(False).astype(bool).sum())
            meta["original_is_qfq_ratio"] = (qfq_true / len(out)) if len(out) else 0.0
        else:
            logger.warning(
                f"[MCPAdapter] 线1 {table}/{freq} 云端返回无 is_qfq 列，"
                f"按云端语义默认整批为前复权（日线实测 is_qfq=True 占比 100%）")
            meta["original_is_qfq_ratio"] = 1.0

        adj_i = pd.to_numeric(out["adj_factor"], errors="coerce")
        # 全部 code（含 is_qfq=False）都要查全局最新因子：旧基准前复权行必须用
        # 全局最新基准还原，否则直通会和新数据产生尺度断层。
        # 流式两遍流程：第二遍传入预取快照（全量注入完成后），保证跨分片基准一致。
        if adj_latest_map is not None:
            latest_map = adj_latest_map
        else:
            latest_map = self._get_adj_latest_global(
                bare.unique().tolist(), asset_type=asset_type)
        adj_latest = bare.map(latest_map)

        valid = adj_i.notna() & (adj_i > 0) & adj_latest.notna() & (adj_latest > 0)
        bad = ~valid
        if bool(bad.any()):
            bad_codes = sorted(bare[bad].unique().tolist())
            meta["skipped_rows_no_factor"] = int(bad.sum())
            meta["missing_factor_codes"] = bad_codes[:50]
            msg = (f"[MCPAdapter] 线1 还原缺因子：{table}/{freq} "
                   f"{int(bad.sum())} 行无法还原，涉及 {len(bad_codes)} 个 code，"
                   f"示例={bad_codes[:5]}")
            if _RESTORE_MISSING_FACTOR_FAIL_FAST:
                logger.error(msg)
                raise ValueError(
                    msg + "。放行会把 qfq 当 raw 写入主库造成双重复权，故 fail-fast。")
            logger.warning(msg)

        # Adjustment-factor histories are not guaranteed to be monotonic. ETF share
        # splits/consolidations and some capital restructurings can leave historical adj_i above
        # the factor at the latest time. Therefore factor magnitude is not a valid stale-anchor
        # test. The production export path synchronizes the batch factor snapshot before restore
        # and fails fast if that synchronization cannot write the snapshot.

        ratio = (adj_latest / adj_i).where(valid, 1.0)
        for col in price_cols:
            out[col] = pd.to_numeric(out[col], errors="coerce") * ratio

        meta.update({
            "is_qfq_restored": True,
            "restored_rows": int(valid.sum()),
            "restored_codes": int(bare[valid].nunique()),
            "restored_price_cols": price_cols,
        })
        logger.info(
            f"[MCPAdapter] 线1 还原 {table}/{freq}: {meta['restored_rows']} 行 / "
            f"{meta['restored_codes']} 码 → raw（列={price_cols}，"
            f"锚={meta['adj_latest_source']}）")
        return out, meta

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
            dataset_id = _CANONICAL_TO_QUESTDB.get(table, table)
            snap = self.client.query_snapshot(dataset_id=dataset_id, limit=1)
            if snap.rows:
                return snap.rows[0].get("date") or snap.rows[0].get("trade_date")
        except MCPClientError as e:
            logger.warning(f"[MCPAdapter] get_last_date({table},{freq}) 失败: {e}")
        return None

    # ------------------------------------------------------------------
    # 全市场代码列表（daemon per_stock 路径要求）
    # ------------------------------------------------------------------
    def get_all_stock_codes(self) -> List[str]:
        """获取全市场 A 股股票代码（tushare 格式 600000.SH），对齐 tushare adapter。

        从云端 stock_basic 表通过 cursor 分页完整拉取，过滤沪深主板/创业板/科创板/北交所。
        daemon 的 _execute_task_per_stock 路径要求此方法（codes=ALL 时获取代码列表逐只拉取）。
        """
        try:
            df, _ = self._fetch_all_pages("stock_basic")
            rows = df.to_dict("records")
            if not rows:
                logger.warning("[MCPAdapter] get_all_stock_codes: stock_basic 无数据")
                return []
            import re
            a_share_re = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
            codes = [str(r.get("ts_code", "")) for r in rows
                     if a_share_re.match(str(r.get("ts_code", "")))]
            logger.info(f"[MCPAdapter] 全市场 A 股: {len(codes)} 只")
            return codes
        except Exception as e:
            logger.error(f"[MCPAdapter] get_all_stock_codes 失败: {e}")
            return []

    def get_etf_codes(self) -> List[str]:
        """获取全市场 ETF 基金代码（tushare 格式 510050.SH / 159919.SZ），对齐 tushare adapter。

        从云端 etf_basic 表通过 cursor 分页完整拉取，过滤上市状态。
        """
        try:
            df, _ = self._fetch_all_pages("etf_basic")
            rows = df.to_dict("records")
            if not rows:
                logger.warning("[MCPAdapter] get_etf_codes: etf_basic 无数据")
                return []
            import re
            etf_re = re.compile(r"^\d{6}\.(SH|SZ)$")
            codes = [str(r.get("ts_code", "")) for r in rows
                     if etf_re.match(str(r.get("ts_code", "")))]
            logger.info(f"[MCPAdapter] 全市场 ETF: {len(codes)} 只")
            return codes
        except Exception as e:
            logger.error(f"[MCPAdapter] get_etf_codes 失败: {e}")
            return []

    def get_index_codes(self) -> List[str]:
        """获取指数代码列表（用于 index_daily 任务的 per_stock 路径）。

        从云端 index_daily 表通过 cursor 分页完整拉取并去重取 ts_code。
        """
        try:
            df, _ = self._fetch_all_pages("index_daily")
            rows = df.to_dict("records")
            if not rows:
                logger.warning("[MCPAdapter] get_index_codes: index_daily 无数据")
                return []
            seen = set()
            codes = []
            for r in rows:
                tc = str(r.get("ts_code", ""))
                if tc and tc not in seen:
                    seen.add(tc)
                    codes.append(tc)
            logger.info(f"[MCPAdapter] 指数: {len(codes)} 个")
            return codes
        except Exception as e:
            logger.error(f"[MCPAdapter] get_index_codes 失败: {e}")
            return []


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

    def _inject_adjfactor(self, df: pd.DataFrame, freq: str, table: str,
                          conn=None) -> int:
        """标准化 MCP adj_factor 并写入 qfq_aux.db 的 adj_factor(股票)/fund_adj(ETF) 表。

        这是 qfq_event_discovery._observe_factors 读取的因子快照表（裸码口径，
        (code,time) PK），discovery 据此生成 factor_new trigger，驱动 QFQ 闭环。

        WP7-E3 阶段 2B 优化：
        - conn 参数：传入已打开的 sqlite 连接时只 executemany 不 commit/close（连接复用），
          不传时保持原行为（打开+PRAGMA+CREATE+executemany+commit+close）。
        - 分钟线按日去重：因子值每天相同，每根 bar 注入一行是 240x 冗余。
          按 (code, 自然日) 去重保留首 bar 的 time → 行数从 4.6 亿降到 ~190 万。
          语义等价：_observe_factors 的 factor_new 检测是相邻 factor_time 值变化（日频粒度），
          每天一行因子与每天 240 行（同值）的检测结果完全一致。
        """
        if self.main_db is None:
            logger.debug("[MCPAdapter] main_db 未配置，跳过 adj_factor 注入")
            return 0
        asset_type = "ETF" if table.startswith("etf") else "STOCK"
        norm = normalize_mcp_adj_factor_df(df, freq, asset_type)
        if len(norm) == 0:
            logger.debug(f"[MCPAdapter] adj_factor 标准化为空（表={table}），跳过注入")
            return 0
        aux = aux_db_path(self.main_db)
        target = "fund_adj" if asset_type == "ETF" else "adj_factor"

        # 优化 2：分钟线按 (code, 自然日) 去重，保留首 bar 的 time。
        # normalize 输出的 time 是 UTC ms epoch；自然日 = time // 86400000。
        # 因子值每天相同（normalize 注释 1860 行），240 bar → 1 行/天，240x 收益。
        if "minutes" in freq:
            norm = norm.sort_values(["code", "time"])
            norm["_day"] = norm["time"] // 86_400_000
            norm = norm.drop_duplicates(subset=["code", "_day"], keep="first")
            norm = norm.drop(columns=["_day"])

        rows = [(str(r["code"]), int(r["time"]), float(r["adj_factor"]))
                for _, r in norm.iterrows()]

        own_conn = conn is None
        try:
            if own_conn:
                conn = sqlite3.connect(str(aux), timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {target} ("
                    f"code TEXT, time INTEGER, adj_factor REAL, PRIMARY KEY (code, time))")
            conn.executemany(
                f"INSERT OR REPLACE INTO {target} (code, time, adj_factor) "
                f"VALUES (?, ?, ?)", rows)
            if own_conn:
                conn.commit()
            logger.info(f"[MCPAdapter] §7.2-A 注入 {target} {len(rows)} 行 "
                        f"→ {aux} (asset_type={asset_type})")
            return len(rows)
        except Exception as e:
            logger.warning(f"[MCPAdapter] {target} 写入失败: {e}")
            return 0
        finally:
            if own_conn and conn is not None:
                conn.close()


# ----------------------------------------------------------------------
# P2-4 §7.2-B：MCP adj_factor 标准化（供 aligner tushare 计算路径消费）
# ----------------------------------------------------------------------
_CST = timezone(timedelta(hours=8))  # Asia/Shanghai 固定偏移（复权连接用，无需历史时区库）


def _as_bool_qfq(v) -> Optional[bool]:
    """把云端 is_qfq 字段归一为 bool。

    QuestDB/Parquet 往返后该列可能是 bool / 0-1 / 'true' / 'True' / 't'，
    统一归一；无法判定返回 None（调用方按"默认视为 qfq"兜底，见 _restore_to_raw）。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return bool(int(v))
    s = str(v).strip().lower()
    if s in ("true", "t", "1", "yes", "y"):
        return True
    if s in ("false", "f", "0", "no", "n"):
        return False
    return None


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
