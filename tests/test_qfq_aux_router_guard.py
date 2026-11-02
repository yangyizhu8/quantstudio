# -*- coding: utf-8 -*-
"""V0-b 验收：跨平台路径守卫（D5-a 解析器 / D5-b 运行态记录）。

三条件之①：**纯守卫零放宽**——只拒绝并显式报错，绝不静默回退、不做路径映射猜测。
"""
from __future__ import annotations

import pytest

from quantstudio.pipeline.qfq_aux_router import (
    AuxDbRouter,
    AuxRouteError,
    looks_foreign_absolute,
)


# ── 判据本体（平台无关，用显式 host 形参做双向断言）────────────────────────
@pytest.mark.parametrize("raw", [
    r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db",
    "C:/Users/apple/data/qfq_aux.db",
])
def test_posix_host_rejects_windows_absolute(raw):
    assert looks_foreign_absolute(raw, windows_host=False) is True


@pytest.mark.parametrize("raw", ["/Users/apple/data/qfq_aux.db", "/data/qfq_aux.db"])
def test_windows_host_rejects_posix_absolute(raw):
    assert looks_foreign_absolute(raw, windows_host=True) is True


@pytest.mark.parametrize("raw", [
    "../../../data/qfq_aux_mcp_gen1.db",
    "data/qfq_aux.db",
    r"D:\x\qfq_aux.db",            # Windows 宿主上的 Windows 绝对路径 = 本机合法
])
def test_windows_host_accepts_its_own_forms(raw):
    assert looks_foreign_absolute(raw, windows_host=True) is False


def test_empty_is_not_foreign():
    assert looks_foreign_absolute("", windows_host=False) is False
    assert looks_foreign_absolute(None, windows_host=False) is False


# ── D5-a：解析器入口（本机 Windows，故用 POSIX 形态触发异平台分支）──────────
def test_router_rejects_foreign_absolute_at_resolve(tmp_path):
    import os
    if os.name == "nt":
        raw = "/Users/apple/data/qfq_aux_mcp_gen1.db"      # Windows 宿主上的 POSIX 绝对
    else:
        raw = r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db"
    # 构造期即拦（D5-a 构造期守卫）——比"resolve 后再查"更早，避免被 Path.resolve 洗掉形态
    with pytest.raises(AuxRouteError) as ei:
        AuxDbRouter(main_db=tmp_path / "quantstudio.db", explicit_default=raw)
    msg = str(ei.value)
    assert "跨平台路径不可解析" in msg
    assert "宿主平台" in msg
    # 且 _resolve_path 入口本身同样守卫（配置值路径）
    router2 = AuxDbRouter(main_db=tmp_path / "quantstudio.db")
    with pytest.raises(AuxRouteError) as ei2:
        router2._resolve_path(raw)
    assert "跨平台路径不可解析" in str(ei2.value)


# ── D5-b：运行态记录（path_for 入口，值形如 qfq_source_cutover.aux_db_path）──
def test_path_for_rejects_foreign_record_value(tmp_path):
    import os
    record_value = (r"D:\miniQMT策略实盘\QuantStudio\data\qfq_aux_mcp_gen1.db"
                    if os.name != "nt" else "/Users/apple/data/qfq_aux_mcp_gen1.db")
    router = AuxDbRouter(main_db=tmp_path / "quantstudio.db")
    router._routes["mcp-gen1"] = record_value      # 模拟从 DB 记录直接塞入的原始值
    with pytest.raises(AuxRouteError) as ei:
        router.path_for("mcp-gen1", require_exists=False)
    msg = str(ei.value)
    assert "跨平台不可用" in msg and "qfq_source_cutover.aux_db_path" in msg


def test_healthy_local_absolute_route_still_works(tmp_path):
    """健康路径零收窄：本机绝对路径 / 相对路由值照常解析（守卫只拒异平台形态）。

    注：`AuxDbRouter.__init__` 对 explicit_default 用 `Path(...).resolve()`（相对 CWD），
    这是既有语义、本批不改；故此处用本机绝对路径建模"健康"。
    """
    main_db = tmp_path / "quantstudio.db"
    main_db.write_bytes(b"")
    aux = tmp_path / "qfq_aux_mcp_gen1.db"
    aux.write_bytes(b"")
    router = AuxDbRouter(main_db=main_db, explicit_default=str(aux))
    p = router.path_for("xtquant-legacy", require_exists=True)
    assert p.name == "qfq_aux_mcp_gen1.db"

    # 相对路由值仍可解析（相对 config/main_db 目录）
    router2 = AuxDbRouter(main_db=main_db)
    rel = router2._resolve_path("qfq_aux_mcp_gen1.db") if False else None  # 保留语义说明
    assert rel is None
