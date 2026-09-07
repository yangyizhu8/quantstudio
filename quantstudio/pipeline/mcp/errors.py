"""MCP client 异常层级（P2-1）。

key 纪律：所有异常消息与日志均不得包含 X-MCP-Key / Authorization 值。
"""
from __future__ import annotations


class MCPClientError(Exception):
    """MCP client 基类异常。"""

    def __init__(self, message: str, *, raw: object = None):
        super().__init__(message)
        # raw 仅用于程序内排查，不得被日志/str 意外带出敏感头
        self.message = message
        self.raw = raw


class MCPAuthError(MCPClientError):
    """鉴权失败（401 / -32001 / key 缺失）。"""


class MCPTransportError(MCPClientError):
    """网络层失败（连接/超时/TLS/非 2xx）。"""


class MCPProtocolError(MCPClientError):
    """JSON-RPC 协议层失败（解析失败 / 缺 session / 非法响应）。"""


class MCPToolError(MCPClientError):
    """工具级失败（result.isError / result.error / 参数校验失败）。

    attributes:
        tool: 工具名
        is_error: 是否来自 result.isError 通道
        code: JSON-RPC error.code（若有）
    """

    def __init__(self, message: str, *, tool: str = "", is_error: bool = False,
                 code: object = None, raw: object = None):
        super().__init__(message, raw=raw)
        self.tool = tool
        self.is_error = is_error
        self.code = code


class MCPChecksumError(MCPClientError):
    """SHA256 对账失败（artifact vs manifest）。"""


class MCPExportBudgetError(MCPClientError):
    """服务端导出预算/结构化错误（F-4 修复，2026-09-08）。

    服务端 60s 软超时等返回 {"error": ..., "hint": ..., "suggested_shards": [...]}
    结构化错误时抛出。携带 hint 与分片建议，上层应缩小窗口重试而非原样重发。

    attributes:
        error_code: 服务端 error 字符串（如 export_exceeds_time_budget）
        hint: 服务端提示
        suggested_shards: 建议的分片窗口列表
    """

    def __init__(self, message: str, *, error_code: str = "",
                 hint: str = "", suggested_shards: object = None, raw: object = None):
        super().__init__(message, raw=raw)
        self.error_code = error_code
        self.hint = hint
        self.suggested_shards = suggested_shards
