"""MCP client 包（P2-1）。

公开 MCPClient（12 工具封装 + SSE 握手/session + retry + SHA256 + Parquet）。
key 仅从环境变量 MCP_API_KEY 读取，绝不回显/记日志/落盘。
"""
from .client import MCPClient
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

__all__ = [
    "MCPClient",
    "MCPAuthError",
    "MCPChecksumError",
    "MCPClientError",
    "MCPProtocolError",
    "MCPToolError",
    "MCPTransportError",
    "AccessInfo",
    "Artifact",
    "CoverageInfo",
    "DatasetSchema",
    "ExportManifest",
    "HealthStatus",
    "ServerCapabilities",
    "ServerInfo",
    "Shard",
    "SnapshotPage",
]
