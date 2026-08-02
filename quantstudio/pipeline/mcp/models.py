"""MCP client 数据模型（P2-1）。

对应实测 12 工具返回结构（非 P1B 草案）。所有字段名以探针实测为准。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ServerInfo:
    name: str = ""
    version: str = ""


@dataclass
class ServerCapabilities:
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DatasetSchema:
    """describe_dataset / get_schema 返回的列定义。"""
    dataset_id: str = ""
    columns: List[Dict[str, str]] = field(default_factory=list)  # {name, type, designated?}
    designated_ts: Optional[str] = None
    adj_factor_desc: str = ""  # describe_dataset 中 adj_factor 列的描述（若有）
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CoverageInfo:
    dataset_id: str = ""
    row_count: int = 0
    ts_min: Optional[str] = None
    ts_max: Optional[str] = None
    distinct_code: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Shard:
    """get_manifest 中单个 Parquet 分片。"""
    shard_id: str = ""
    row_start: int = 0
    row_end: int = 0
    parquet_uri: str = ""          # artifact://<job_id>/<shard_id>
    sha256: str = ""
    rows: int = 0
    size_bytes: int = 0

    @property
    def artifact_id(self) -> str:
        """get_artifact 所需 artifact_id 格式：job_id/shard_id。

        实测：artifact_id 必须形如 '<job_id>/<shard_id>'，不是裸 shard_id。
        """
        uri = self.parquet_uri
        if uri.startswith("artifact://"):
            return uri[len("artifact://"):]
        return self.shard_id


@dataclass
class ExportManifest:
    job_id: str = ""
    dataset_id: str = ""
    table: str = ""
    generated_at: Optional[str] = None
    total_rows: int = 0
    shard_count: int = 0
    shards: List[Shard] = field(default_factory=list)
    concat_sha256: Optional[str] = None  # create_export_job 返回的全局拼接 sha256
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Artifact:
    artifact_id: str = ""
    sha256: str = ""
    size_bytes: int = 0
    parquet_bytes: bytes = b""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotPage:
    """query_snapshot 返回的小表页（行 JSON）。"""
    rows: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)


@dataclass
class HealthStatus:
    status: str = ""
    questdb: Dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessInfo:
    """validate_access 返回。"""
    scope: List[str] = field(default_factory=list)
    allowed_datasets: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
