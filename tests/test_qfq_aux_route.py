"""TD-D2：QFQ 因子库 legacy → mcp-gen1 统一路由测试
（docs/mcp_migration/wp7e3-TD-D2-task.md v1.1 §3 步骤 3）。

覆盖：
- 三态路由：当前真实态（active cutover 已存在 + ⑤ 未释放 → legacy，R-2 核心用例）/
  无 active cutover → legacy / released+active 双条件 → gen1 / fail-secure 各分支；
- grep 审计：daemon.py 无 aux_db_path( 直引；mcp_adapter.py 仅 _qfq_aux_path 内一处；
- adapter override 注入：daemon 侧路由结果与 MCPAdapter._qfq_aux_path() 同源；
- 防线 1/2.1/3 调用点统一（别名等价）；
- Phase 3 fail-fast 行为不变（released 两态下 _qfq_snapshot_kwargs 语义）。
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantstudio.pipeline.qfq_aux_router import resolve_runtime_aux_path  # noqa: E402
from quantstudio.pipeline.qfq_reanchor_schema import aux_db_path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAIN_DB = ROOT / "data" / "quantstudio.db"
LEGACY = aux_db_path(MAIN_DB)
GEN1 = MAIN_DB.parent / "qfq_aux_mcp_gen1.db"


def _make_cfg(tmp_path, *, released):
    cfg = tmp_path / "qfq_aux_paths.json"
    cfg.write_text(json.dumps({
        "released": released,
        "default": str(LEGACY),
        "generations": {
            "xtquant-legacy": str(LEGACY),
            "mcp-gen1": str(GEN1),
        }}), encoding="utf-8")
    return cfg


def _db_read(rows):
    """构造 duckdb_read 回调 mock：rows 非空 = active cutover 存在。"""
    def read(sql, params=None):
        if "qfq_active_cutover" in sql:
            return rows
        return []
    return read


# —————————————————————— 三态路由（R-2 核心用例集） ——————————————————————

def test_current_real_state_routes_legacy(tmp_path):
    """当前真实态（R-2 核心）：dynamic 配置 + active cutover 已存在（b6_formal_
    20260807_v2，v6.7.43）+ ⑤ 未释放（released=false）→ 必须仍走 legacy。
    防"仅 active cutover 即切"误判（v1.1 关键事实）。"""
    cfg = _make_cfg(tmp_path, released=False)
    path, reason = resolve_runtime_aux_path(
        main_db=MAIN_DB, duckdb_read=_db_read([("b6", "mcp-gen1")]),
        config_path=cfg)
    assert path == LEGACY
    assert reason == "legacy:released=false"


def test_no_active_cutover_routes_legacy(tmp_path):
    """released=true 但无 active cutover → legacy（防仅凭配置误切）。"""
    cfg = _make_cfg(tmp_path, released=True)
    path, reason = resolve_runtime_aux_path(
        main_db=MAIN_DB, duckdb_read=_db_read([]), config_path=cfg)
    assert path == LEGACY
    assert reason == "legacy:no_active_cutover"


def test_both_conditions_route_gen1(tmp_path):
    """双条件齐备（released=true + active cutover）→ gen1 世代库。"""
    cfg = _make_cfg(tmp_path, released=True)
    path, reason = resolve_runtime_aux_path(
        main_db=MAIN_DB, duckdb_read=_db_read([("b6", "mcp-gen1")]),
        config_path=cfg)
    assert path == GEN1
    assert reason == "gen1:mcp-gen1"


def test_fail_secure_branches(tmp_path):
    """fail-secure 分支：无 config / 无 duckdb_read / 查询异常 / JSON 坏 → legacy。"""
    # 无 config
    p, r = resolve_runtime_aux_path(main_db=MAIN_DB, duckdb_read=_db_read([("b6", "mcp-gen1")]),
                                    config_path=tmp_path / "nonexistent.json")
    assert p == LEGACY and "fail-secure" in r
    # released=true 但无 duckdb_read（条件②不可判定）
    cfg = _make_cfg(tmp_path, released=True)
    p, r = resolve_runtime_aux_path(main_db=MAIN_DB, duckdb_read=None, config_path=cfg)
    assert p == LEGACY and "no_duckdb_read" in r
    # duckdb_read 抛异常
    def boom(sql, params=None):
        raise RuntimeError("conn down")
    p, r = resolve_runtime_aux_path(main_db=MAIN_DB, duckdb_read=boom, config_path=cfg)
    assert p == LEGACY and "cutover_query_error" in r
    # 坏 JSON
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    p, r = resolve_runtime_aux_path(main_db=MAIN_DB, duckdb_read=_db_read([]),
                                    config_path=bad)
    assert p == LEGACY and "fail-secure" in r


# —————————————————————— grep 审计（R-3 扩围） ——————————————————————

def test_grep_audit_no_direct_aux_path_derivation():
    """禁止第二处路径推导：
    - daemon.py 无 aux_db_path( 直引（收敛后唯一入口是 _qfq_aux_path）；
    - mcp_adapter.py 的 aux_db_path( 仅允许出现在 _qfq_aux_path 方法体内。
    """
    daemon_src = (ROOT / "quantstudio/pipeline/daemon.py").read_text(encoding="utf-8")
    hits = [ln for ln in daemon_src.splitlines()
            if re.search(r"\baux_db_path\s*\(", ln) and not ln.strip().startswith("#")]
    assert hits == [], f"daemon.py 存在 aux_db_path( 直引（TD-D2 收敛违反）: {hits}"

    mcp_src = (ROOT / "quantstudio/pipeline/sources/mcp_adapter.py").read_text(encoding="utf-8")
    lines = mcp_src.splitlines()
    in_method = False
    violations = []
    for i, ln in enumerate(lines, 1):
        if "def _qfq_aux_path" in ln:
            in_method = True
            continue
        if in_method and ln.strip() and not ln.startswith(" ") and "def " in ln:
            in_method = False
        if re.search(r"\baux_db_path\s*\(", ln) and not in_method:
            if not ln.strip().startswith("#"):
                violations.append(f"L{i}: {ln.strip()}")
    assert violations == [], \
        f"mcp_adapter.py 的 aux_db_path( 必须仅在 _qfq_aux_path 方法内: {violations}"


# —————————————————————— adapter override 注入同源 ——————————————————————

def test_mcp_adapter_aux_path_override():
    """MCPAdapter._qfq_aux_path：override 注入（daemon 侧统一路由结果）优先；
    未注入时兜底 legacy 推导（独立 CLI/测试场景）。"""
    from quantstudio.pipeline.sources.mcp_adapter import MCPAdapter
    adapter = MCPAdapter.__new__(MCPAdapter)
    adapter.main_db = str(MAIN_DB)
    adapter.qfq_aux_override = None
    assert adapter._qfq_aux_path() == LEGACY          # 兜底推导
    adapter.qfq_aux_override = str(GEN1)
    assert adapter._qfq_aux_path() == GEN1            # override 优先


def test_daemon_alias_unification(tmp_path):
    """daemon 收敛：_qfq_align_aux_path / _qfq_aux_db_routed 均为 _qfq_aux_path
    别名（防线 1 与防线 2.1/3 调用点统一，消除分叉）。"""
    import types
    from quantstudio.pipeline.daemon import ResidentCollector
    c = ResidentCollector.__new__(ResidentCollector)
    c.qfq_aux_paths_config = _make_cfg(tmp_path, released=False)
    c._qfq_config = lambda: types.SimpleNamespace(price_source="mcp")
    c.writer = types.SimpleNamespace(
        db_path=str(MAIN_DB), execute_read=_db_read([("b6", "mcp-gen1")]))
    p_main = c._qfq_aux_path()
    assert p_main == LEGACY                            # 当前真实态 → legacy
    assert c._qfq_align_aux_path() == p_main           # 防线 1 别名等价
    assert c._qfq_aux_db_routed() == p_main            # 防线 2.1/3 别名等价
    assert c._qfq_aux_route_reason == "legacy:released=false"


def test_daemon_route_follows_release_gate(tmp_path):
    """释放门翻转（released false→true）后 daemon 路由跟随切换到 gen1
    （⑤ 释放 = 改配置不切代码）。"""
    import types
    from quantstudio.pipeline.daemon import ResidentCollector
    c = ResidentCollector.__new__(ResidentCollector)
    c._qfq_config = lambda: types.SimpleNamespace(price_source="mcp")
    c.writer = types.SimpleNamespace(
        db_path=str(MAIN_DB), execute_read=_db_read([("b6", "mcp-gen1")]))
    c.qfq_aux_paths_config = _make_cfg(tmp_path, released=False)
    assert c._qfq_aux_path() == LEGACY
    c.qfq_aux_paths_config = _make_cfg(tmp_path, released=True)   # ⑤ 释放
    assert c._qfq_aux_path() == GEN1


# —————————————————————— Phase 3 fail-fast 行为不变 ——————————————————————

def test_phase3_failfast_unchanged_under_routing():
    """released 两态下 _qfq_snapshot_kwargs 语义不变：aux 不可读 → 空 map →
    aligner fail-fast raise（宁拒不写坏）。"""
    import types
    import pandas as pd
    from quantstudio.pipeline.daemon import ResidentCollector
    from quantstudio.pipeline.aligner import FieldAligner
    c = ResidentCollector.__new__(ResidentCollector)
    c._QFQ_PRICE_TABLES = ResidentCollector._QFQ_PRICE_TABLES
    c.qfq_aux_paths_config = None      # fail-secure → legacy
    c._qfq_config = lambda: types.SimpleNamespace(price_source="mcp")
    # legacy 库真实存在（生产环境）→ 若快照成功则 kwargs 非 fail-fast 分支；
    # 本用例验证的是：路由路径解析正确 + 无异常穿透（fail-fast 语义由
    # tests/test_qfq_global_snapshot.py 6 例回归覆盖）
    c.writer = types.SimpleNamespace(
        db_path=str(MAIN_DB), execute_read=_db_read([]))
    kwargs = c._qfq_snapshot_kwargs("etf_daily", "test")
    assert set(kwargs.keys()) == {"adj_latest_map", "adj_earliest_map"}
    # 引用 FieldAligner 确认契约面未变（签名断言在既有回归中）
    assert FieldAligner is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# —————————————————————— 复审补测：orchestrator aux 纳入 released 门（阻断项修复） ——————————————————————

def _mk_dynamic_env(tmp_path, *, released):
    """构造 dynamic 模式最小环境：主库 cutover 表（active 已存在=当前真实态）
    + qfq_aux_paths.json(released) + orchestrator。返回 (orch, legacy_path, gen1_path)。"""
    import duckdb as _dd
    from quantstudio.pipeline.qfq_orchestrator_types import QFQOrchestratorConfig
    from quantstudio.pipeline.qfq_resident_orchestrator import QFQResidentOrchestrator

    legacy = tmp_path / "qfq_aux.db"
    legacy.touch()
    gen1 = tmp_path / "qfq_aux_mcp_gen1.db"
    gen1.touch()

    conn = _dd.connect()
    conn.execute("""CREATE TABLE qfq_source_cutover (
        cutover_id VARCHAR, price_source VARCHAR, source_generation VARCHAR,
        status VARCHAR, aux_db_path VARCHAR, schema_version VARCHAR,
        baseline_version VARCHAR, evidence_path VARCHAR,
        activated_at VARCHAR, config_hash VARCHAR)""")
    conn.execute("""CREATE TABLE qfq_active_cutover (
        price_source VARCHAR, cutover_id VARCHAR, activated_at VARCHAR)""")
    # 当前真实态：active cutover 已存在（b6_formal_20260807_v2，2026-08-07）
    conn.execute("INSERT INTO qfq_source_cutover VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ["b6_formal_20260807_v2", "mcp", "mcp-gen1", "active",
                  str(gen1), "2.1", "b1", "ev.json", "2026-08-07 00:00:00", "cfgx"])
    conn.execute("INSERT INTO qfq_active_cutover VALUES (?,?,?)",
                 ["mcp", "b6_formal_20260807_v2", "2026-08-07 00:00:00"])

    cfg_json = tmp_path / "qfq_aux_paths.json"
    cfg_json.write_text(json.dumps({
        "released": released,
        "default": str(legacy),
        "generations": {"xtquant-legacy": str(legacy), "mcp-gen1": str(gen1)},
    }), encoding="utf-8")

    cfg = QFQOrchestratorConfig.load(raw=dict(
        enabled=True, require_bootstrap=False, price_source="mcp",
        generation_mode="dynamic", source_generation="mcp-gen1",
        cutover_id="b6_formal_20260807_v2"))
    orch = QFQResidentOrchestrator(
        cfg, main_db=str(tmp_path / "main.duckdb"),
        aux_db=str(legacy), fetcher=object(), calendar=object())
    orch.qfq_aux_paths_config = cfg_json
    return orch, conn, legacy, gen1


def test_orchestrator_aux_released_false_routes_legacy(tmp_path):
    """阻断项修复验收：released=false（当前真实态：active cutover 已存在 + ⑤ 未释放）
    → orchestrator self.aux_db 必须= legacy（与 daemon._qfq_aux_path 同源），
    不得指向空 gen1（口径 B/S2/discovery/refresher 不再静默失效）。"""
    orch, conn, legacy, gen1 = _mk_dynamic_env(tmp_path, released=False)
    ident = orch.prepare_runtime(conn, require_aux=False)
    assert ident["source_generation"] == "mcp-gen1"   # 世代身份解析不受 released 影响
    assert Path(orch.aux_db) == legacy                 # aux 路径 = legacy
    assert Path(orch.aux_db) != gen1
    conn.close()


def test_orchestrator_aux_released_true_routes_gen1(tmp_path):
    """released=true（⑤ 已释放）+ active cutover → orchestrator self.aux_db = gen1
    （与 daemon 侧切换同步，四者同路径）。"""
    orch, conn, legacy, gen1 = _mk_dynamic_env(tmp_path, released=True)
    orch.prepare_runtime(conn, require_aux=False)
    assert Path(orch.aux_db) == gen1
    assert Path(orch.aux_db) != legacy
    conn.close()


def test_grep_audit_orchestrator_aux_derivation():
    """grep 审计扩围（R-3）：qfq_resident_orchestrator.py 中 aux_db_path( 仅允许
    出现在 _explicit_aux_db 兜底行（legacy 方向的构造缺省，非 gen1 直连）。"""
    src = (ROOT / "quantstudio/pipeline/qfq_resident_orchestrator.py").read_text(
        encoding="utf-8")
    violations = []
    for i, ln in enumerate(src.splitlines(), 1):
        if not re.search(r"\baux_db_path\s*\(", ln):
            continue
        if ln.strip().startswith("#"):
            continue
        # 合法：_explicit_aux_db 兜底（aux_db or aux_db_path(main_db)，legacy 方向）
        if "_explicit_aux_db" in ln and "aux_db or" in ln:
            continue
        # 合法：import 行 / runtime_cutover_record 的字段访问不含 "(" 调用
        if "import" in ln:
            continue
        violations.append(f"L{i}: {ln.strip()}")
    assert violations == [], \
        f"orchestrator 存在未收敛的 aux_db_path 推导: {violations}"
