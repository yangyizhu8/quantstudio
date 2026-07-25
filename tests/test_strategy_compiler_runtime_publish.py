"""Regression tests for broker-PTrade runtime compatibility and stable publishing."""
from __future__ import annotations

import ast
import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from quantstudio.strategy_compiler.build_strategy_ir import build_strategy_ir
from quantstudio.strategy_compiler.package.builder import build_strategy_package
from quantstudio.strategy_compiler.publish import (
    StrategyPublishError,
    publish_strategy_entry_points,
)
from quantstudio.strategy_compiler.render import render_ptrade, render_quantstudio

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "quantstudio" / "strategy_compiler" / "examples" / "manual_pool_top2_spec.json"


def _spec():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _rendered():
    ir = build_strategy_ir(_spec())
    return render_quantstudio(ir), render_ptrade(ir)




def test_rendered_history_uses_one_explicit_front_adjustment_literal():
    quantstudio_code, ptrade_code = _rendered()
    assert "fq='dypre'" not in quantstudio_code
    assert "fq='dypre'" not in ptrade_code
    assert "fq='pre'" in quantstudio_code
    assert "fq='pre'" in ptrade_code
    assert "data[code].close" not in quantstudio_code

def test_ptrade_manual_list_uses_broker_safe_runtime_shape():
    _, code = _rendered()
    tree = ast.parse(code)
    assert "run_daily(context, rebalance, time='9:31')" in code
    assert "_extract_history_field" in code
    assert "600570.SS" in code
    assert "920001.BJ" in code
    # Daily ranking must not depend on broker BarDict support for each symbol.
    assert not any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "data"
        for node in ast.walk(tree)
    )
    # The historical DataFrame-only bug must not be emitted as a direct chain.
    assert "h[code]['close'].values" not in code


def test_ptrade_render_executes_with_structured_arrays_and_empty_bse_history():
    _, code = _rendered()
    namespace = {"np": np}
    exec(compile(code, "generated_ptrade.py", "exec"), namespace)

    g = SimpleNamespace()
    logs = []
    scheduled = []
    orders = []
    namespace["g"] = g
    namespace["log"] = SimpleNamespace(info=lambda message: logs.append(str(message)))
    namespace["run_daily"] = lambda context, func, time="9:31": scheduled.append((func, time))

    values = {
        "600570.SS": [26.0, 26.5, 27.0],
        "000001.SZ": [11.5, 11.4, 11.3],
        "920001.BJ": [],
    }

    def get_history(count, unit, field, security_list, fq, include, is_dict):
        rows = np.array(
            [(20260501 + index, value) for index, value in enumerate(values[security_list])],
            dtype=[("datetime", "i8"), ("close", "f8")],
        )
        return OrderedDict([(security_list, rows)])

    namespace["get_history"] = get_history
    namespace["filter_stock_by_status"] = lambda candidates, filter_type, query_date: candidates
    namespace["order_target_value"] = lambda code, value: orders.append((code, value))
    context = SimpleNamespace(
        portfolio=SimpleNamespace(positions={}, cash=100000.0, total_value=100000.0)
    )

    namespace["initialize"](context)
    namespace["before_trading_start"](context, object())
    assert scheduled[0][1] == "9:31"
    assert g.selected == ["600570.SS", "000001.SZ"]
    scheduled[0][0](context)
    assert orders == [("600570.SS", 50000.0), ("000001.SZ", 50000.0)]
    assert any("920001.BJ" in message and "fewer than" in message for message in logs)


def test_quantstudio_manual_list_uses_alias_safe_history_and_target_weights():
    code, _ = _rendered()
    ast.parse(code)
    assert "g.history = {_bare_code(code):" in code
    assert "g.history.get(_bare_code(code), [])" in code
    assert "order_target_value(code, target_value)" in code


def test_publish_entry_points_to_gui_and_project_ptrade_directories(tmp_path):
    spec = _spec()
    package_dir = build_strategy_package(spec, tmp_path / "packages", package_version="0.3.2-mvp")
    project_root = tmp_path / "project"
    (project_root / "quantstudio" / "backtest" / "strategies").mkdir(parents=True)

    published = publish_strategy_entry_points(spec, package_dir, project_root)
    qs = project_root / "quantstudio" / "backtest" / "strategies" / "manual_pool_top2_runtime_compat_quantstudio.py"
    pt = project_root / "ptrade" / "manual_pool_top2_runtime_compat_ptrade.py"
    assert published["quantstudio"] == qs
    assert published["ptrade-default"] == pt
    assert qs.read_bytes() == (package_dir / qs.name).read_bytes()
    assert pt.read_bytes() == (package_dir / pt.name).read_bytes()
    # Identical rebuilds are idempotent even when overwrite=false.
    assert publish_strategy_entry_points(spec, package_dir, project_root)["quantstudio"] == qs


def test_publish_refuses_different_existing_file_without_overwrite(tmp_path):
    spec = _spec()
    package_dir = build_strategy_package(spec, tmp_path / "packages", package_version="0.3.2-mvp")
    project_root = tmp_path / "project"
    qs_dir = project_root / "quantstudio" / "backtest" / "strategies"
    qs_dir.mkdir(parents=True)
    target = qs_dir / "manual_pool_top2_runtime_compat_quantstudio.py"
    target.write_text("different", encoding="utf-8")
    with pytest.raises(StrategyPublishError, match="output.overwrite=true"):
        publish_strategy_entry_points(spec, package_dir, project_root)
