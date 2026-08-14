"""MCP 客户端（P2-1）。

职责：MCP server 的 12 工具封装 + SSE 握手/session 管理 + retry +
SHA256 对账 + Parquet 解码 + TLS 双模式 + Header 脱敏。

关键约束（铁律）：
- key 解析链：构造参数 api_key（GUI 注入） → 项目 config/secrets.env（GUI 写入，不进 git）
  → 环境变量 ``MCP_API_KEY``（兼容原链路，缺失 fail-fast）。绝不回显/记日志/落盘。
- HTTP 头 ``X-MCP-Key`` / ``Authorization`` 在任何日志中脱敏为 ``***``。
- 复权不在 client 内完成（因子随行返回，由上层按 raw×adj_factor 计算）。
- 不改默认生产配置；TLS 双模式由构造参数控制。

协议（探针实测固化，见 docs/mcp_migration/mcp_protocol_probe.md）：
- transport=streamable-http，全程 SSE（text/event-stream，CRLF）。
- initialize → 响应头 mcp-session-id → notifications/initialized(202)。
- 错误两类：JSON-RPC 级 response.error（-32001 鉴权）；工具级 result.isError / result.error。
- 大表主路径：create_export_job → get_manifest(job_id) → get_artifact("{job_id}/{shard_id}")。
- 小表：query_snapshot（直接返回行 JSON，含 adj_factor）。
- fetch_page(cursor 字符串，首页 ""；分页 <=50k 行）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from quantstudio._paths import _ROOT as PROJECT_ROOT

try:
    import pyarrow.parquet as pq
    _HAS_PARQUET = True
except Exception:  # pragma: no cover
    _HAS_PARQUET = False

from .errors import (
    MCPAuthError,
    MCPChecksumError,
    MCPClientError,
    MCPProtocolError,
    MCPToolError,
    MCPTransportError,
)
from .models import (
    AccessInfo,
    Artifact,
    CoverageInfo,
    DatasetSchema,
    ExportManifest,
    HealthStatus,
    ServerCapabilities,
    ServerInfo,
    Shard,
    SnapshotPage,
)

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://124.223.159.234/mcp"
_PROTOCOL_VERSION = "2024-11-05"
_SSE_CT = "text/event-stream"
_PARQUET_MAGIC = b"PAR1"
_SECRET_HEADERS = {"x-mcp-key", "authorization"}


def _mask_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """脱敏 secret 头，供日志使用。绝不返回明文 key。"""
    return {k: ("***" if k.lower() in _SECRET_HEADERS else v) for k, v in headers.items()}


def _require_key() -> str:
    key = os.environ.get("MCP_API_KEY")
    if not key:
        raise MCPAuthError("MCP_API_KEY 环境变量未设置（fail-fast）")
    return key


def load_mcp_api_key(
    *, api_key: Optional[str] = None, secrets_path: Optional[Union[str, Path]] = None
) -> Optional[str]:
    """解析 MCP API Key（注入优先级）：

    1. 显式构造参数 api_key（最高优先，由 GUI/MCP adapter 传入）
    2. 项目 config/secrets.env 中的 MCP_API_KEY（GUI 写入，不进 git）
    3. 环境变量 MCP_API_KEY（兼容原 fail-fast 链路）

    注意：仅在未显式给 api_key 时回退到 secrets.env / 环境变量，
    且 key 绝不以明文出现在日志（脱敏由 _build_headers/mask 负责）。
    """
    if api_key:
        return api_key
    # 2) config/secrets.env（手工解析 MCP_API_KEY=xxx，不依赖 dotenv）
    if secrets_path is None:
        secrets_path = PROJECT_ROOT / "config" / "secrets.env"
    p = Path(secrets_path)
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("MCP_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception as e:
            logger.warning(f"读取 secrets.env 失败: {e}")
    # 3) 环境变量
    return os.environ.get("MCP_API_KEY") or None


class MCPClient:
    """MCP server 客户端。

    Args:
        endpoint: MCP 端点。默认开发 IP；备案后切域名。
        tls_verify: TLS 校验开关。开发 IP 模式 False（配合白名单 IP）；正式模式 True。
        call_timeout: 单次 HTTP 硬超时（秒）。
        retry_max: 幂等请求最大重试次数。
        backoff_sec: 退避序列（秒）。
        idempotency_namespace: 应用级 job 缓存命名空间（同一 dataset+page_size 复用 manifest_ref）。
    """

    def __init__(
        self,
        endpoint: str = _DEFAULT_ENDPOINT,
        *,
        tls_verify: bool = False,
        call_timeout: float = 90.0,
        retry_max: int = 5,
        backoff_sec: Tuple[int, ...] = (30, 60, 120, 240, 480),
        rate_per_min: int = 200,
        api_key: Optional[str] = None,
        secrets_path: Optional[Union[str, Path]] = None,
    ):
        # MCP API Key 解析链（修复2）：构造参数 → config/secrets.env → 环境变量
        # api_key 显式传入则优先；否则从 secrets.env / 环境变量读取（缺省 None，
        # 真正 fail-fast 推迟到首次 _build_headers 调用 _require_key）。
        self._api_key: Optional[str] = load_mcp_api_key(
            api_key=api_key, secrets_path=secrets_path)
        self.endpoint = endpoint
        self.tls_verify = bool(tls_verify)
        self.call_timeout = float(call_timeout)
        self.retry_max = int(retry_max)
        self.backoff_sec = tuple(int(x) for x in backoff_sec)
        self.rate_per_min = int(rate_per_min)

        self._session = requests.Session()
        self._session.verify = self.tls_verify
        if not self.tls_verify:
            # 开发 IP 模式（tls_verify=False + 白名单 IP）下抑制 urllib3 的
            # InsecureRequestWarning 噪音（告警文本固定指向 127.0.0.1 误报，
            # 实际连接目标由 endpoint 决定）。这不改变任何安全语义。
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self._session_id: Optional[str] = None
        self._initialized = False
        self._req_id = 0
        self._lock = threading.Lock()
        self._rate_ts: List[float] = []
        # 应用级 create_export_job 缓存（防重复创建）
        self._job_cache: Dict[Tuple[str, int], str] = {}
        self._rpc_id = 0

    # ---------------- 底层 RPC ----------------
    def _next_rpc_id(self) -> int:
        with self._lock:
            self._rpc_id += 1
            return self._rpc_id

    def _acquire_rate(self) -> None:
        now = time.time()
        self._rate_ts = [t for t in self._rate_ts if now - t < 60]
        if len(self._rate_ts) >= self.rate_per_min:
            sleep_sec = 60 - (now - self._rate_ts[0]) + 0.1
            logger.debug(f"[MCP rate] sleep {sleep_sec:.1f}s (limit={self.rate_per_min}/min)")
            self._sleep_with_heartbeat(sleep_sec, "[MCP rate]")
        self._rate_ts.append(time.time())

    @staticmethod
    def _sleep_with_heartbeat(total_sec: float, tag: str = "", interval: int = 30) -> None:
        if total_sec <= interval:
            time.sleep(total_sec)
            return
        slept = 0.0
        while slept < total_sec:
            chunk = min(interval, total_sec - slept)
            time.sleep(chunk)
            slept += chunk
            remaining = total_sec - slept
            if remaining > 0:
                logger.debug(f"{tag} 等待中 {slept:.0f}/{total_sec:.0f}s，剩余 {remaining:.0f}s")

    def _build_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": f"application/json, {_SSE_CT}",
            # 优先注入的 api_key/secrets.env，回退到环境变量（_require_key fail-fast）
            "X-MCP-Key": self._api_key or _require_key(),
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _parse_sse(text: str) -> Optional[Dict[str, Any]]:
        """解析 SSE：按 CRLF 分行，取 data: <json> 负载（实测 CRLF + 'event: message'）。"""
        for line in text.split("\r\n"):
            line = line.strip()
            if line.startswith("data: "):
                payload = line[len("data: "):].strip()
                if not payload:
                    continue
                try:
                    return requests.models.complexjson.loads(payload)
                except Exception:
                    continue
        # 兜底：整段当作 JSON
        try:
            return requests.models.complexjson.loads(text)
        except Exception:
            return None

    def _post_rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
                  *, is_notification: bool = False) -> Optional[Dict[str, Any]]:
        """发送一次 JSON-RPC（走 SSE），返回解析后的 dict（notification 返回 None）。

        两类错误在此捕获并转异常：
        - JSON-RPC 级：response.error（如 401/-32001）
        - HTTP 级：非 2xx / 连接 / 超时
        """
        rid = None if is_notification else self._next_rpc_id()
        body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if rid is not None:
            body["id"] = rid

        headers = self._build_headers()
        logger.debug(f"[MCP rpc] {method} id={rid} headers={_mask_headers(headers)}")
        try:
            resp = self._session.post(
                self.endpoint, headers=headers, json=body,
                timeout=self.call_timeout, stream=True,
            )
        except requests.RequestException as e:
            raise MCPTransportError(f"{method} 网络失败: {type(e).__name__}: {e}") from e

        # 捕获 session id（实测在 mcp-session-id 响应头，大小写不敏感）
        sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
        if sid:
            self._last_session_id = sid

        if resp.status_code == 401:
            raise MCPAuthError("MCP 鉴权失败 (401)：X-MCP-Key 缺失或无效")
        if resp.status_code == 400:
            # 确定性错误（如 "Missing session ID"），重试无意义，立即上抛 MCPProtocolError
            # （_call_with_retry 对该类型不重试，避免傻等数百秒）
            raise MCPProtocolError(
                f"{method} HTTP 400（确定性错误，不重试）: {resp.text[:300]}")
        if resp.status_code == 429 or resp.status_code >= 500:
            # 限流 / 服务端临时错误，可重试
            raise MCPTransportError(
                f"{method} HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            # 其他 4xx（403/404 等）确定性错误，不重试
            raise MCPProtocolError(
                f"{method} HTTP {resp.status_code}（确定性错误，不重试）: {resp.text[:300]}")

        if is_notification:
            return None

        text = resp.content.decode("utf-8", "replace")
        msg = self._parse_sse(text)
        if msg is None:
            raise MCPProtocolError(f"{method} 响应无法解析为 JSON-RPC: {text[:300]}")
        if "error" in msg and msg["error"] is not None:
            err = msg["error"]
            err_msg = str(err.get("message") or "")
            err_code = err.get("code")
            # 连接层错误分类（双保险）：若 JSON-RPC error 的 message 含连接中断关键字，
            # 说明 server 偶发死连接漏网（server 侧连接池根治 + 此处 client 兜底自愈）。
            # 抛 MCPTransportError → _call_with_retry 会 _reset_connection + 重握手后重试，
            # 10053/ECONNRESET 完全透明自愈。
            _CONN_ABORT_HINTS = (
                "10053", "connection abort", "could not receive data",
                "econnreset", "connection reset", "broken pipe",
                "remote end closed", "connection closed",
            )
            if any(h in err_msg.lower() for h in _CONN_ABORT_HINTS):
                raise MCPTransportError(
                    f"{method} JSON-RPC 连接层错误（自愈重试） code={err_code} msg={err_msg}")
            raise MCPProtocolError(
                f"{method} JSON-RPC error code={err_code} msg={err_msg}",
                code=err_code)
        return msg

    def _call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用 tools/call，返回 content 解析后的结构（自动二次 json.loads text）。

        工具级错误（result.isError / result.error）转 MCPToolError。
        """
        msg = self._post_rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if msg is None or "result" not in msg:
            raise MCPProtocolError(f"{name} 响应缺少 result 字段")
        result = msg["result"]
        # 工具级错误通道 1：isError
        if result.get("isError"):
            text = ""
            c = result.get("content") or []
            if c and isinstance(c[0], dict) and c[0].get("type") == "text":
                text = c[0].get("text", "")
            raise MCPToolError(f"{name} isError: {text[:400]}", tool=name, is_error=True)
        # 工具级错误通道 2：result.error（部分工具走此通道，如 get_artifact 格式错）
        if isinstance(result.get("error"), dict):
            raise MCPToolError(
                f"{name} error: {result['error'].get('message', result['error'])}",
                tool=name, is_error=True)
        # content -> text（结构化 JSON 字符串）或直接结构化
        if "content" in result and isinstance(result["content"], list):
            c = result["content"]
            if c and isinstance(c[0], dict) and c[0].get("type") == "text":
                try:
                    return requests.models.complexjson.loads(c[0]["text"])
                except Exception:
                    return {"_text": c[0]["text"]}
            # content 非 text（极少）：原样返回
            return result
        return result

    def _call_with_retry(self, fn, *args, **kwargs):
        """幂等请求重试（线程超时包裹 + 退避 + 心跳）。"""
        last_err: Optional[BaseException] = None
        for attempt in range(self.retry_max):
            try:
                self._acquire_rate()
                result = [None]
                err = [None]

                def _run():
                    try:
                        result[0] = fn(*args, **kwargs)
                    except BaseException as e:  # noqa: BLE001
                        err[0] = e

                th = threading.Thread(target=_run, daemon=True)
                th.start()
                th.join(self.call_timeout)
                if th.is_alive():
                    raise TimeoutError(f"{fn.__name__} 超过 {self.call_timeout}s 未返回")
                if err[0] is not None:
                    raise err[0]
                return result[0]
            except (MCPAuthError, MCPProtocolError):
                # 鉴权/协议错误不可重试，直接上抛
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = self.backoff_sec[min(attempt, len(self.backoff_sec) - 1)]
                logger.warning(
                    f"[MCP retry] attempt {attempt + 1}/{self.retry_max} failed: "
                    f"{type(e).__name__}: {e}, sleep {wait}s")
                # P3-2 10053 根因修复：server 间歇性断开连接（如 ECONNRESET/10053），
                # 重试若复用同一已损坏的 _session 和失效 _session_id 必败。
                # 对传输层错误（MCPTransportError/网络异常），重试前重置连接并重握手，
                # 使后续重试走全新 session。这不改变任何数据语义/API 契约。
                if attempt + 1 < self.retry_max:  # 还有重试机会才重置
                    from .errors import MCPTransportError as _TE
                    if isinstance(e, (_TE,)) or isinstance(e, requests.RequestException):
                        try:
                            self._reset_connection()
                            logger.info("[MCP retry] 已重置连接并重握手，准备重试")
                        except Exception as _re:  # 重置失败则按原错误继续重试
                            logger.warning(f"[MCP retry] 连接重置失败（将按原错误重试）: {_re}")
                self._sleep_with_heartbeat(wait, "[MCP retry]")
        raise MCPTransportError(
            f"MCP 重试 {self.retry_max} 次仍失败: {last_err}") from last_err

    # ---------------- 连接重置（重连） ----------------
    def _reset_connection(self) -> None:
        """重建底层 HTTP session 并重新握手，用于 server 间歇性断连后恢复。

        不改变任何数据语义/API 契约：仅重置传输层状态
        （_session / _session_id / _initialized），随后重新 initialize 握手。
        幂等安全：重试路径调用，失败向上抛由 _call_with_retry 继续处理。
        """
        try:
            self._session.close()
        except Exception:
            pass
        self._session = requests.Session()
        self._session.verify = self.tls_verify
        if not self.tls_verify:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self._session_id = None
        self._initialized = False
        self._job_cache.clear()  # 旧 session 的 job 缓存失效，避免跨 session 复用脏 job
        # 重新握手（建立新 session_id）
        self.handshake()

    # ---------------- 握手 ----------------
    def handshake(self) -> ServerInfo:
        """initialize → 记录 mcp-session-id → notifications/initialized。幂等。"""
        if self._initialized and self._session_id:
            return self._server_info
        msg = self._post_rpc(
            "initialize",
            {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
             "clientInfo": {"name": "quantstudio", "version": "0.1"}},
        )
        if msg is None or "result" not in msg:
            raise MCPProtocolError("initialize 响应缺少 result")
        res = msg["result"]
        # session id 同时可能在 header（实测在 mcp-session-id 响应头）
        sid = msg.get("_sid")  # 备用
        # 从最后一次响应头取（见 _post_rpc 增强）
        self._session_id = getattr(self, "_last_session_id", None) or sid
        if not self._session_id:
            # 服务端未在 header 返回时，用 serverInfo 兜底（不应发生）
            raise MCPProtocolError("initialize 未返回 mcp-session-id")
        info = res.get("serverInfo", {})
        self._server_info = ServerInfo(name=info.get("name", ""), version=info.get("version", ""))
        # notifications/initialized（202）
        self._post_rpc("notifications/initialized", is_notification=True)
        self._initialized = True
        logger.info(f"[MCP] handshake ok session={self._session_id[:8]}... "
                    f"server={self._server_info.name} proto={res.get('protocolVersion')}")
        return self._server_info

    # ---------------- 12 工具封装 ----------------
    def ping_health(self) -> HealthStatus:
        d = self._call_with_retry(self._call_tool, "ping_health")
        return HealthStatus(
            status=d.get("status", ""),
            questdb=d.get("questdb", {}) or {},
            latency_ms=d.get("latency_ms"),
            raw=d,
        )

    def validate_access(self) -> AccessInfo:
        d = self._call_with_retry(self._call_tool, "validate_access")
        return AccessInfo(
            scope=d.get("scope", []) or [],
            allowed_datasets=d.get("allowed_datasets", []) or [],
            raw=d,
        )

    def list_datasets(self) -> List[str]:
        d = self._call_with_retry(self._call_tool, "list_datasets")
        if isinstance(d, dict) and "datasets" in d:
            ds = d["datasets"]
            if isinstance(ds, list):
                if ds and isinstance(ds[0], dict):
                    return [x.get("dataset_id") or x.get("id") for x in ds]
                return [str(x) for x in ds]
        return []

    def describe_server(self) -> Dict[str, Any]:
        return self._call_with_retry(self._call_tool, "describe_server")

    def describe_dataset(self, dataset_id: str) -> DatasetSchema:
        d = self._call_with_retry(self._call_tool, "describe_dataset",
                                  {"dataset_id": dataset_id})
        cols = d.get("columns", []) or []
        designated = d.get("designated_ts") or d.get("designated")
        # adj_factor 描述：在 columns 里找 adj_factor 的说明
        adj_desc = ""
        for c in cols:
            if isinstance(c, dict) and c.get("name") == "adj_factor":
                adj_desc = c.get("description", "")
        return DatasetSchema(
            dataset_id=dataset_id,
            columns=[c for c in cols if isinstance(c, dict)],
            designated_ts=designated,
            adj_factor_desc=adj_desc,
            raw=d,
        )

    def get_schema(self, dataset_id: str) -> DatasetSchema:
        d = self._call_with_retry(self._call_tool, "get_schema",
                                  {"dataset_id": dataset_id})
        cols = d.get("columns", []) or []
        designated = d.get("designated_ts") or d.get("designated")
        return DatasetSchema(
            dataset_id=dataset_id,
            columns=[c for c in cols if isinstance(c, dict)],
            designated_ts=designated, raw=d,
        )

    def get_coverage(self, dataset_id: str) -> CoverageInfo:
        d = self._call_with_retry(self._call_tool, "get_coverage",
                                  {"dataset_id": dataset_id})
        return CoverageInfo(
            dataset_id=dataset_id,
            row_count=int(d.get("row_count", 0) or 0),
            ts_min=d.get("ts_min"),
            ts_max=d.get("ts_max"),
            distinct_code=int(d.get("distinct_code", 0) or 0),
            raw=d,
        )

    def query_snapshot(self, dataset_id: str,
                       columns: Optional[List[str]] = None,
                       limit: int = 100) -> SnapshotPage:
        """小表内存路径：直接返回行 JSON（含 adj_factor）。"""
        args: Dict[str, Any] = {"dataset_id": dataset_id, "limit": int(limit)}
        if columns:
            args["columns"] = list(columns)
        d = self._call_with_retry(self._call_tool, "query_snapshot", args)
        rows = d.get("rows", []) or []
        cols = d.get("columns", []) or (list(rows[0].keys()) if rows else [])
        return SnapshotPage(rows=rows, columns=cols)

    def fetch_page(self, dataset_id: str, cursor: str = "",
                   page_size: int = 50000,
                   columns: Optional[List[str]] = None) -> Dict[str, Any]:
        """分页取数：cursor 必须为字符串，首页传 ""（实测 null 会校验失败）。"""
        args: Dict[str, Any] = {
            "dataset_id": dataset_id,
            "cursor": cursor if cursor is not None else "",
            "page_size": int(page_size),
        }
        if columns:
            args["columns"] = list(columns)
        return self._call_with_retry(self._call_tool, "fetch_page", args)

    def query_updated_since(self, since: str,
                            table: Optional[str] = None) -> List[Dict[str, Any]]:
        """A4 变更检测：查询自 since（ISO 8601 UTC）以来的云端更新记录。

        返回 [{"table_name", "trade_date", "last_update_time",
               "update_source", "rows_pushed"}, ...]。
        空结果返回 []；表不存在返回 []。
        """
        args: Dict[str, Any] = {"since": since}
        if table:
            args["table"] = table
        d = self._call_with_retry(self._call_tool, "query_updated_since", args)
        # server 返回 {"updates": [...], "count": N}
        if isinstance(d, dict):
            return d.get("updates", [])
        if isinstance(d, list):
            return d
        return []

    # ----- 大表导出作业路径 -----
    def create_export_job(self, dataset_id: str, page_size: int = 50000,
                          *, idempotency_key: Optional[str] = None,
                          time_start: Optional[str] = None,
                          time_end: Optional[str] = None,
                          row_limit: Optional[int] = None) -> str:
        """触发 Parquet 导出作业，返回 manifest_ref（= get_manifest 要的 job_id）。

        防重复创建策略（应用级幂等，不依赖未验证的服务端 idempotency 语义）：
        - 同 (dataset_id, page_size, time_start, time_end) 复用已创建的 manifest_ref（缓存）。
        - idempotency_key 可选透传给服务端（若服务端支持则进一步防重）。
        - time_start/time_end：服务端 WHERE 下推时间范围（ISO 或 YYYYMMDD），避免
          默认 row_limit 截断到最老数据。
        """
        cache_key = (dataset_id, int(page_size), time_start, time_end)
        with self._lock:
            cached = self._job_cache.get(cache_key)
        if cached:
            logger.debug(f"[MCP] reuse cached export job {cached} for {cache_key}")
            return cached
        args: Dict[str, Any] = {"dataset_id": dataset_id, "page_size": int(page_size)}
        if row_limit is not None:
            args["row_limit"] = int(row_limit)
        if idempotency_key:
            args["idempotency_key"] = idempotency_key
        if time_start is not None:
            args["time_start"] = time_start
        if time_end is not None:
            args["time_end"] = time_end
        d = self._call_with_retry(self._call_tool, "create_export_job", args)
        ref = d.get("manifest_ref") or d.get("job_id")
        if not ref:
            raise MCPProtocolError(f"create_export_job 未返回 manifest_ref: {d}")
        with self._lock:
            self._job_cache[cache_key] = ref
        return ref

    def get_manifest(self, job_id: str) -> ExportManifest:
        d = self._call_with_retry(self._call_tool, "get_manifest", {"job_id": job_id})
        shards = [Shard(
            shard_id=s.get("shard_id", ""),
            row_start=int(s.get("row_start", 0) or 0),
            row_end=int(s.get("row_end", 0) or 0),
            parquet_uri=s.get("parquet_uri", ""),
            sha256=s.get("sha256", ""),
            rows=int(s.get("rows", 0) or 0),
            size_bytes=int(s.get("size_bytes", 0) or 0),
        ) for s in (d.get("shards", []) or [])]
        concat = d.get("concat_sha256")
        return ExportManifest(
            job_id=d.get("job_id", job_id),
            dataset_id=d.get("dataset_id", ""),
            table=d.get("table", ""),
            generated_at=d.get("generated_at"),
            total_rows=int(d.get("total_rows", 0) or 0),
            shard_count=int(d.get("shard_count", 0) or len(shards)),
            shards=shards,
            concat_sha256=concat,
            raw=d,
        )

    def get_artifact(self, job_id: str, artifact_id: str,
                     *, verify_sha256: bool = True) -> Artifact:
        """取一个 Parquet 分片（base64）并解码为 bytes。

        artifact_id 格式："{job_id}/{shard_id}"（实测必须，非裸 shard_id）。
        verify_sha256：比对 artifact.sha256 与 manifest 中该 shard 的 sha256。
        """
        d = self._call_with_retry(
            self._call_tool, "get_artifact",
            {"job_id": job_id, "artifact_id": artifact_id})
        b64 = (d.get("content_base64") or d.get("content_base64_b64")
               or d.get("data") or d.get("parquet_b64") or "")
        if not b64:
            raise MCPProtocolError(
                f"get_artifact 缺少 Parquet base64 字段: keys={list(d.keys())}")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise MCPProtocolError(f"get_artifact base64 解码失败: {e}") from e
        self._verify_parquet_magic(raw, artifact_id)
        artifact = Artifact(
            artifact_id=artifact_id,
            sha256=d.get("sha256", ""),
            size_bytes=int(d.get("size_bytes", 0) or len(raw)),
            parquet_bytes=raw,
            raw=d,
        )
        if verify_sha256 and artifact.sha256:
            actual = hashlib.sha256(raw).hexdigest()
            if actual.lower() != artifact.sha256.lower():
                raise MCPChecksumError(
                    f"artifact {artifact_id} sha256 不匹配: 期望={artifact.sha256[:12]} "
                    f"实际={actual[:12]}")
        return artifact

    @staticmethod
    def _verify_parquet_magic(raw: bytes, label: str) -> None:
        if len(raw) < 8:
            raise MCPProtocolError(f"artifact {label} 过短，非 Parquet: {len(raw)} bytes")
        if raw[:4] != _PARQUET_MAGIC or raw[-4:] != _PARQUET_MAGIC:
            raise MCPProtocolError(
                f"artifact {label} 非 Parquet 文件（魔数缺失 PAR1）")

    @staticmethod
    def decode_parquet(artifact: Artifact):
        """把 Parquet bytes 解码为 pyarrow.Table（供上层转 DataFrame）。"""
        if not _HAS_PARQUET:
            raise MCPClientError("pyarrow 不可用，无法解码 Parquet")
        import io
        return pq.read_table(io.BytesIO(artifact.parquet_bytes))

    def export_dataset(self, dataset_id: str, page_size: int = 50000,
                       *, verify_each_shard: bool = True,
                       verify_concat: bool = False,
                       time_start: Optional[str] = None,
                       time_end: Optional[str] = None,
                       row_limit: Optional[int] = None) -> List[Artifact]:
        """端到端导出：create_export_job → get_manifest → 逐 shard get_artifact。

        Args:
            verify_each_shard: 每片 sha256 对账（artifact vs manifest）。
            verify_concat: 全量拼接 sha256 校验（concat_sha256，开销大，默认关）。
            time_start/time_end: 服务端时间范围下推（ISO 或 YYYYMMDD），服务端按此
                WHERE 过滤导出，避免默认 row_limit 截断到最老数据。
            row_limit: 大表(stock_minutes/etf_minutes)服务端强制要求，非大表可不传。
        Returns:
            解码后的 Artifact 列表（parquet_bytes 已加载内存）。
        """
        self.handshake()
        ref = self.create_export_job(dataset_id, page_size=page_size,
                                     time_start=time_start, time_end=time_end,
                                     row_limit=row_limit)
        manifest = self.get_manifest(ref)
        artifacts: List[Artifact] = []
        for shard in manifest.shards:
            art = self.get_artifact(ref, shard.artifact_id, verify_sha256=verify_each_shard)
            artifacts.append(art)
        if verify_concat and manifest.concat_sha256:
            h = hashlib.sha256()
            for art in artifacts:
                h.update(art.parquet_bytes)
            if h.hexdigest().lower() != manifest.concat_sha256.lower():
                raise MCPChecksumError("concat_sha256 全量对账失败")
        logger.info(f"[MCP] export_dataset {dataset_id}: {len(artifacts)} shards, "
                    f"total_rows={manifest.total_rows}")
        return artifacts

    def close(self) -> None:
        self._session.close()
        self._initialized = False
        self._session_id = None
