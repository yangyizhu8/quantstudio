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


class MCPRetryBudgetExhausted(MCPTransportError):
    """重试/重握手总预算耗尽（六步③，2026-09-18，client.py:362 重试有界）。

    继承 MCPTransportError：上层既有 except 语义零变更（仍按传输层失败处理），
    仅新增可辨识类型，供任务失败标记与审计行区分「预算耗尽」与「单次失败」。

    attributes:
        budget_sec: 生效预算（秒）
        elapsed_sec: 实际耗时（秒）
        attempts: 已尝试次数
        last_err: 最后一次底层异常（可能为 None）
    """

    def __init__(self, message: str, *, budget_sec: float = 0.0,
                 elapsed_sec: float = 0.0, attempts: int = 0,
                 last_err: object = None, raw: object = None):
        super().__init__(message, raw=raw)
        self.budget_sec = float(budget_sec)
        self.elapsed_sec = float(elapsed_sec)
        self.attempts = int(attempts)
        self.last_err = last_err


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
