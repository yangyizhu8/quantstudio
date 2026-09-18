# -*- coding: utf-8 -*-
"""MCP client 重试有界（六步③，2026-09-18）单元测试。

零网络：全部用桩函数 / 桩 _post_rpc；不连 MCP server、不读 secrets.env（显式 api_key）。
覆盖：预算耗尽、预算=0 等效旧行为、末次不空等退避、重握手受预算约束（真锚点）、
      首握手有界入口、成功路径不变、环境变量解析优先级、审计锚点。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantstudio.pipeline.mcp.client import (  # noqa: E402
    DEFAULT_RETRY_BUDGET_SEC,
    MCPClient,
    RETRY_BUDGET_ENV,
    resolve_retry_budget,
)
from quantstudio.pipeline.mcp.errors import (  # noqa: E402
    MCPAuthError,
    MCPRetryBudgetExhausted,
    MCPTransportError,
)


def _client(**kw) -> MCPClient:
    kw.setdefault("endpoint", "http://127.0.0.1:9/mcp")   # 不连（测试不触发真实请求）
    kw.setdefault("api_key", "test-key-not-real")          # 显式注入，跳过 secrets.env
    kw.setdefault("call_timeout", 0.3)
    kw.setdefault("retry_max", 3)
    kw.setdefault("backoff_sec", (0, 0, 0))
    return MCPClient(**kw)


# ── 预算解析 ─────────────────────────────────────────────────────────────
def test_resolve_retry_budget_precedence(monkeypatch):
    monkeypatch.delenv(RETRY_BUDGET_ENV, raising=False)
    assert resolve_retry_budget() == DEFAULT_RETRY_BUDGET_SEC == 900.0
    monkeypatch.setenv(RETRY_BUDGET_ENV, "12")
    assert resolve_retry_budget() == 12.0                  # env
    assert resolve_retry_budget(3) == 3.0                  # 构造参数优先
    monkeypatch.setenv(RETRY_BUDGET_ENV, "not-a-number")
    assert resolve_retry_budget() == DEFAULT_RETRY_BUDGET_SEC   # 无法解析 → 默认（不抛错）
    assert resolve_retry_budget("also-bad") == DEFAULT_RETRY_BUDGET_SEC


def test_client_reads_budget_from_env(monkeypatch):
    monkeypatch.setenv(RETRY_BUDGET_ENV, "0")
    assert _client().retry_budget_sec == 0.0
    monkeypatch.setenv(RETRY_BUDGET_ENV, "42")
    assert _client().retry_budget_sec == 42.0
    assert _client(retry_budget_sec=7).retry_budget_sec == 7.0


# ── 要素① 有界总时长 + 要素② 超时退出 ────────────────────────────────────
def test_budget_exhausted_raises_and_is_bounded():
    """预算耗尽：抛专用异常、耗时受界、继承 MCPTransportError（上层 except 语义不变）。"""
    c = _client(call_timeout=5.0, retry_max=5, backoff_sec=(1, 1, 1, 1, 1), retry_budget_sec=1.0)

    def never_returns():
        time.sleep(5.0)

    t0 = time.monotonic()
    with pytest.raises(MCPRetryBudgetExhausted) as ei:
        c._call_with_retry(never_returns)
    elapsed = time.monotonic() - t0
    assert elapsed <= 2.0, f"应有界（预算 1.0s），实际 {elapsed:.2f}s"
    e = ei.value
    assert e.budget_sec == 1.0 and e.attempts >= 1 and e.elapsed_sec > 0
    assert isinstance(e, MCPTransportError), "必须继承 MCPTransportError 以保上层语义"


def test_budget_exhausted_emits_audit_anchor(caplog):
    """要素④ 审计行：固定锚点 BUDGET_EXHAUSTED（含 budget/attempts/elapsed/last_err）。"""
    c = _client(call_timeout=5.0, retry_max=3, backoff_sec=(1, 1, 1), retry_budget_sec=1.0)

    def never_returns():
        time.sleep(5.0)

    with caplog.at_level("WARNING"):
        with pytest.raises(MCPRetryBudgetExhausted):
            c._call_with_retry(never_returns)
    txt = caplog.text
    assert "BUDGET_EXHAUSTED" in txt
    assert "budget=1s" in txt and "attempts=" in txt and "elapsed=" in txt


def test_budget_zero_is_exactly_legacy_behavior(monkeypatch):
    """要素④ 回退：预算=0 → 逐行等效旧行为（重试满 retry_max、普通错误类型、无预算异常）。"""
    c = _client(call_timeout=0.2, retry_max=3, backoff_sec=(0, 0, 0), retry_budget_sec=0)
    calls, resets, sleeps = [], [], []
    monkeypatch.setattr(c, "_reset_connection", lambda timeout=None: resets.append(timeout))
    monkeypatch.setattr(c, "_sleep_with_heartbeat", lambda total, tag="": sleeps.append(total))

    def boom():
        calls.append(1)
        raise MCPTransportError("boom")

    with pytest.raises(MCPTransportError) as ei:
        c._call_with_retry(boom)
    assert not isinstance(ei.value, MCPRetryBudgetExhausted)
    assert len(calls) == 3, "旧行为：尝试次数 = retry_max"
    assert "重试 3 次仍失败" in str(ei.value), "旧行为：原有错误文案"
    assert resets and all(t is None for t in resets), "预算=0 → 重握手不设上限（旧行为）"
    # 旧行为：末次失败后仍睡退避 → 退避次数 = retry_max - 1（每次都还有下一次）
    assert len(sleeps) == 2 and all(s == 0 for s in sleeps)


def test_last_attempt_does_not_sleep_backoff(monkeypatch):
    """裁定2：最后一次失败后不再空等退避（旧行为会白等 480s 量级）。"""
    c = _client(call_timeout=0.2, retry_max=2, backoff_sec=(1, 1), retry_budget_sec=0)
    sleeps = []
    monkeypatch.setattr(c, "_reset_connection", lambda timeout=None: None)
    monkeypatch.setattr(c, "_sleep_with_heartbeat", lambda total, tag="": sleeps.append(total))

    def boom():
        raise MCPTransportError("boom")

    with pytest.raises(MCPTransportError):
        c._call_with_retry(boom)
    assert sleeps == [1], f"两次尝试只应睡一次（末次不睡），实际 {sleeps}"


# ── 要素① 真锚点：重握手受预算约束 ───────────────────────────────────────
def test_reset_receives_finite_remaining_budget(monkeypatch):
    """真锚点：_reset_connection 现在收到**有限**剩余预算（旧行为：无参 = 无界）。"""
    c = _client(call_timeout=0.2, retry_max=3, backoff_sec=(0, 0, 0), retry_budget_sec=5.0)
    seen = []

    def spy_reset(timeout=None):
        seen.append(timeout)

    monkeypatch.setattr(c, "_reset_connection", spy_reset)

    def boom():
        raise MCPTransportError("boom")

    # 注：预算 5s 远大于瞬时失败总耗时 ⇒ 本用例**不**期望耗尽，只验「重置带有限上限」
    with pytest.raises(MCPTransportError):
        c._call_with_retry(boom)
    assert seen, "应发生连接重置"
    assert all(t is not None for t in seen), "重置必须带有限上限"
    assert all(0 < t <= 5.0 for t in seen), f"上限应为剩余预算，实际 {seen}"


def test_reset_handshake_is_bounded_when_post_rpc_hangs(monkeypatch):
    """**核心回归**：_post_rpc 无限期阻塞时，_reset_connection 必须有界返回（3h12m 真锚点）。"""
    c = _client()

    def hangs_forever(*a, **kw):
        time.sleep(5.0)

    monkeypatch.setattr(c, "_post_rpc", hangs_forever)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        c._reset_connection(timeout=0.5)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"重握手应有界（0.5s），实际 {elapsed:.2f}s"


def test_handshake_bounded_uses_budget_and_zero_is_unbounded(monkeypatch):
    """首握手入口：预算>0 → 有界；预算=0 → 直接走实现体（不限 = 旧行为）。"""
    c_bounded = _client(retry_budget_sec=0.5)
    monkeypatch.setattr(c_bounded, "_post_rpc", lambda *a, **k: time.sleep(5.0))
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        c_bounded.handshake_bounded()
    assert time.monotonic() - t0 < 2.0

    c_legacy = _client(retry_budget_sec=0)
    used = []

    def fake_impl():
        used.append(1)
        raise MCPTransportError("legacy-direct")

    monkeypatch.setattr(c_legacy, "_handshake_impl", fake_impl)
    monkeypatch.setattr(c_legacy, "_run_bounded",
                        lambda *a, **k: pytest.fail("预算=0 时不得走有界包裹"))
    with pytest.raises(MCPTransportError):
        c_legacy.handshake_bounded()
    assert used == [1]


# ── 成功路径不变 ─────────────────────────────────────────────────────────
def test_success_path_unchanged_with_and_without_budget():
    """成功路径零变更：返回值原样透传、无额外调用、两种预算态一致。"""
    sentinel = {"content": [{"type": "text", "text": "ok"}]}
    calls = []

    def fn():
        calls.append(1)
        return sentinel

    c_on = _client(retry_budget_sec=5.0)
    assert c_on._call_with_retry(fn) is sentinel
    c_off = _client(retry_budget_sec=0)
    assert c_off._call_with_retry(fn) is sentinel
    assert len(calls) == 2, "每次成功调用应恰走一次 fn"


def test_auth_error_still_not_retried():
    """不可重试错误语义不变：MCPAuthError 立刻上抛（不进退避/重置）。"""
    c = _client(retry_budget_sec=5.0)
    calls = []

    def boom():
        calls.append(1)
        raise MCPAuthError("401")

    with pytest.raises(MCPAuthError):
        c._call_with_retry(boom)
    assert len(calls) == 1
