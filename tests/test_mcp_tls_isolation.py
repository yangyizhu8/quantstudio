# -*- coding: utf-8 -*-
"""P1-4 验收：TLS 显式隔离 + 逃生通道 + 生效策略可见化。

方案：docs/jabberwock-four-findings-fix-design.md（P1-4，过审 7c6f36a）
问题：`requests` 在 `verify=True` 时仍读 env `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE`
      ⇒ 客户机错误 CA 导致即使开启校验也握手失败；原因只在 DEBUG 级不可见。

判据：
  C1 **隔离**：tls_verify=True 且 env REQUESTS_CA_BUNDLE 指向不存在路径 → session.verify
     **不使用该 env 值**（改为显式 CA 路径）
  C2 显式 CA 优先：MCP_CA_BUNDLE=… → session.verify 取该路径
  C3 逃生通道关：MCP_TLS_VERIFY=0 + tls_verify=True → verify=False
  C4 逃生通道开：MCP_TLS_VERIFY=1 + tls_verify=False → verify 为 CA 路径（非 False）
  C5 默认：无 env + tls_verify=True → 来源描述含 certifi（或系统默认），verify 非 False
  C6 生效策略 INFO 日志存在（源码静态断言，防回归删除）
"""
from __future__ import annotations

import inspect

import pytest

from quantstudio.pipeline.mcp import client as C


class _Sess:
    """最小 session 桩：只接受 verify 赋值。"""

    def __init__(self):
        self.verify = None


def test_c1_env_ca_bundle_invalid_is_ignored(monkeypatch):
    """失效 env CA 路径（客户机故障形态）→ 忽略并回落默认 CA。"""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/no/such/bundle.pem")
    monkeypatch.delenv(C.MCP_CA_BUNDLE_ENV, raising=False)
    monkeypatch.delenv(C.MCP_TLS_VERIFY_ENV, raising=False)
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)
    assert verify != "/no/such/bundle.pem", "失效路径不得被采用（客户机故障形态消除）"
    assert s.verify == verify and verify is not False
    assert "certifi" in src or "系统默认" in src


def test_c1b_env_ca_bundle_valid_is_honored(monkeypatch, tmp_path):
    """**纯增益补强**：env CA 路径**文件确实存在**时沿用（不误伤企业/内网 CA 部署）。"""
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca))
    monkeypatch.delenv(C.MCP_CA_BUNDLE_ENV, raising=False)
    monkeypatch.delenv(C.MCP_TLS_VERIFY_ENV, raising=False)
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)
    assert verify == str(ca) and s.verify == str(ca)
    assert "沿用" in src and "既有部署" in src


def test_c2_explicit_ca_wins(monkeypatch, tmp_path):
    ca = tmp_path / "my-ca.pem"
    ca.write_text("dummy", encoding="utf-8")
    monkeypatch.setenv(C.MCP_CA_BUNDLE_ENV, str(ca))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/no/such/bundle.pem")
    monkeypatch.delenv(C.MCP_TLS_VERIFY_ENV, raising=False)
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)
    assert verify == str(ca) and "显式 CA" in src


def test_c2b_explicit_ca_missing_is_ignored(monkeypatch):
    """显式路径不存在 → 不采用（避免静默 TLS 失败），回落默认并 WARNING。"""
    monkeypatch.setenv(C.MCP_CA_BUNDLE_ENV, "/no/such/explicit-ca.pem")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv(C.MCP_TLS_VERIFY_ENV, raising=False)
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)
    assert verify != "/no/such/explicit-ca.pem" and verify is not False
    assert "certifi" in src or "系统默认" in src


def test_c3_escape_hatch_off(monkeypatch):
    monkeypatch.setenv(C.MCP_TLS_VERIFY_ENV, "0")
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)          # 显式覆盖：即使 tls_verify=True
    assert verify is False and s.verify is False and "关闭" in src


def test_c4_escape_hatch_on(monkeypatch):
    monkeypatch.setenv(C.MCP_TLS_VERIFY_ENV, "1")
    monkeypatch.delenv(C.MCP_CA_BUNDLE_ENV, raising=False)
    s = _Sess()
    verify, _src = C._apply_tls_policy(s, False)        # 显式覆盖：即使 tls_verify=False
    assert verify is not False, "MCP_TLS_VERIFY=1 应强制开启校验"


def test_c5_default_true_keeps_verification(monkeypatch):
    for k in (C.MCP_TLS_VERIFY_ENV, C.MCP_CA_BUNDLE_ENV, "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(k, raising=False)
    s = _Sess()
    verify, src = C._apply_tls_policy(s, True)
    assert verify is not False and s.verify == verify
    assert "certifi" in src or "系统默认" in src


def test_c6_policy_info_log_present_in_source():
    src = inspect.getsource(C)
    assert "[MCP tls] 生效策略" in src, "生效策略 INFO 日志不得被删除（P1-4 可见化要求）"
    assert "_apply_tls_policy" in src and "self._session.verify = self.tls_verify" not in src, \
        "构造/重连均应走 _apply_tls_policy（不得残留裸赋值）"
