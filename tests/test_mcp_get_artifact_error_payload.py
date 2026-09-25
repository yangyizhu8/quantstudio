# -*- coding: utf-8 -*-
"""Q2 验收（铁律：纯恢复型 + 新增检测型）：`get_artifact` 载荷级 error 分类与留痕。

方案：docs/error-q2-get-artifact-error-payload-design.md（过审）

判据：
  V1 载荷级 error（含 artifact_id）→ 抛 MCPToolError（非 MCPProtocolError）+ 留痕字段齐全
  V2 瞬时类 error → 走既有重试（重试计数可见），最终仍失败时错误含 error 原文
  V3 确定性类（not found/expired/…）→ **不重试**（调用次数恰 1）且显式失败
  V4 成功路径零变化：正常载荷返回值/字段/sha256 校验行为与改前一致
  V5 真协议违例（无 error 载荷但缺 base64）→ 仍抛 MCPProtocolError
"""
from __future__ import annotations

import base64
import hashlib

import pytest

from quantstudio.pipeline.mcp import client as C
from quantstudio.pipeline.mcp.client import MCPClient, MCPProtocolError, MCPToolError


def _mk_client(monkeypatch, responses, *, retry_max=3):
    """构造一个不打网络的 client：_call_tool 依次返回 responses（可调用/可值）。"""
    c = object.__new__(MCPClient)             # 仅测这两个方法，跳过网络初始化
    c.retry_max = retry_max
    c.retry_budget_sec = 0                    # 0 = 不限（逐行等效旧行为）
    c.backoff_sec = [0.0, 0.0, 0.0, 0.0]
    c.call_timeout = 5.0
    calls = {"n": 0}

    def _call_tool(name, arguments=None):
        i = calls["n"]
        calls["n"] += 1
        r = responses[min(i, len(responses) - 1)]
        if callable(r):
            return r(name, arguments)
        return r

    monkeypatch.setattr(c, "_call_tool", _call_tool, raising=False)
    monkeypatch.setattr(c, "_acquire_rate", lambda: None, raising=False)
    return c, calls


def _ok_payload(raw: bytes):
    return {"content_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw)}


PARQUET_MAGIC = b"PAR1" + b"\x00" * 8 + b"PAR1"


def test_v1_payload_error_raises_tool_error_with_context(monkeypatch):
    """V1：载荷级 error + 确定性语义 → MCPToolError（非协议错误）+ 留痕。"""
    payload = {"error": "artifact not found: expired", "artifact_id": "job1/shard9"}
    c, calls = _mk_client(monkeypatch, [payload])
    with pytest.raises(MCPToolError) as ei:
        c.get_artifact("job1", "job1/shard9")
    msg = str(ei.value)
    assert isinstance(ei.value, MCPToolError) and not isinstance(ei.value, MCPProtocolError)
    assert "载荷级 error" in msg and "not found" in msg          # error 原文保留
    assert "artifact_id=job1/shard9" in msg and "job_id=job1" in msg   # 留痕字段
    assert ei.value.tool == "get_artifact" and ei.value.is_error is True
    assert calls["n"] == 1, "确定性类不得重试"


def test_v2_transient_error_is_retried(monkeypatch):
    """V2：瞬时类 error → 走既有重试（多次调用），最终失败仍带原文。"""
    transient = {"error": {"message": "artifact not ready yet, try again"}, "artifact_id": "job2/s1"}
    c, calls = _mk_client(monkeypatch, [transient], retry_max=3)
    with pytest.raises(MCPToolError) as ei:
        c.get_artifact("job2", "job2/s1")
    assert calls["n"] == 3, f"瞬时类应按 retry_max 重试，实际调用 {calls['n']} 次"
    assert "not ready" in str(ei.value)


def test_v2b_transient_then_success(monkeypatch):
    """V2 续：瞬时类先失败后成功 → 正常返回（返回值与成功路径一致）。"""
    transient = {"error": "busy", "artifact_id": "job3/s1"}
    c, calls = _mk_client(monkeypatch, [transient, _ok_payload(PARQUET_MAGIC)])
    art = c.get_artifact("job3", "job3/s1", verify_sha256=True)
    assert art.parquet_bytes == PARQUET_MAGIC and calls["n"] == 2


def test_v3_deterministic_never_retried(monkeypatch):
    """V3：确定性类（multiple 关键词）→ 调用恰 1 次。"""
    for msg in ("no such artifact", "artifact expired", "缺失字段", "非法 artifact_id"):
        c, calls = _mk_client(monkeypatch, [{"error": msg, "artifact_id": "j/s"}])
        with pytest.raises(MCPToolError):
            c.get_artifact("j", "j/s")
        assert calls["n"] == 1, f"{msg!r} 不应重试"


def test_v4_success_path_unchanged(monkeypatch):
    """V4：成功路径零变化（返回 bytes/sha256/size + 校验语义）。"""
    raw = PARQUET_MAGIC
    c, calls = _mk_client(monkeypatch, [_ok_payload(raw)])
    art = c.get_artifact("j", "j/s1")            # verify_sha256 默认 True
    assert art.parquet_bytes == raw
    assert art.sha256 == hashlib.sha256(raw).hexdigest()
    assert art.size_bytes == len(raw) and calls["n"] == 1

    # sha256 不匹配 → 仍抛 MCPChecksumError（原行为）
    bad = _ok_payload(raw)
    bad["sha256"] = "0" * 64
    c2, _ = _mk_client(monkeypatch, [bad])
    with pytest.raises(C.MCPChecksumError):
        c2.get_artifact("j", "j/s1")


def test_v5_missing_b64_without_error_is_still_protocol_error(monkeypatch):
    """V5：无 error 载荷却缺 base64 → 仍为真协议违例（MCPProtocolError）+ 上下文。"""
    c, calls = _mk_client(monkeypatch, [{"foo": 1}])
    with pytest.raises(MCPProtocolError) as ei:
        c.get_artifact("jobx", "jobx/s2")
    assert "缺少 Parquet base64 字段" in str(ei.value)
    assert "artifact_id=jobx/s2" in str(ei.value)
