#!/usr/bin/env python3
"""Agent-first runtime-shape fixture for get_history(is_dict=True) field extraction.

This is NOT a full backtest. It extracts the strategy's history-field
normalization helper via AST, compiles it in an isolated namespace, and runs it
against the real return shapes a PTrade broker may produce:

- pandas.DataFrame({"close": closes})
- pandas.Series(closes)                    (fail-soft: no exception)
- numpy.array(..., dtype=[("close", "f8")])
- numpy.rec.fromarrays([closes], names="close")
- None                                     (fail-soft empty)
- empty structured array                   (fail-soft empty)
- missing close field                      (fail-soft empty)
- NaN/inf values                           (structure preserved, no exception)

Requirements:
- DataFrame and recarray must return the same 1-D float ndarray values;
- empty/None input must not raise;
- missing fields follow the fail-soft contract (empty array).

Usage:
    python validate_runtime_shapes.py strategy.py [--helper _extract_history_field]
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

DEFAULT_HELPER_NAMES = ("_extract_history_field", "_history_field", "_field_array")


def requires_runtime_shape_fixture(design: dict[str, Any], source: str) -> bool:
    """True when the canonical source consumes get_history(is_dict=True) on a
    dual/PTrade target and therefore must pass the agent-first runtime-shape
    fixture before candidate generation or publication."""
    if "ptrade" not in design.get("targets", []):
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name != "get_history":
            continue
        for keyword in node.keywords:
            if keyword.arg == "is_dict" and isinstance(keyword.value, ast.Constant) \
                    and keyword.value.value is True:
                return True
    return False


def _load_helper_source(source: str, helper: str | None) -> tuple[str, str]:
    tree = ast.parse(source)
    candidates = (helper,) if helper else DEFAULT_HELPER_NAMES
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in candidates:
            return node.name, ast.get_source_segment(source, node) or ""
    raise ValueError(
        f"no history-field extraction helper found; expected one of {candidates}. "
        "Define e.g. _extract_history_field(history_item, field, dtype=float).")


def _compile_helper(helper_source: str) -> Any:
    import numpy as np
    import pandas as pd

    namespace: dict[str, Any] = {"np": np, "pd": pd, "numpy": np, "pandas": pd}
    exec(compile(helper_source, "<history-helper>", "exec"), namespace)
    return namespace


def run_fixtures(func: Any) -> list[dict[str, Any]]:
    import numpy as np
    import pandas as pd

    closes = [10.0, 10.5, 11.0, 10.8, 11.2]
    expected = np.asarray(closes, dtype=float)

    def call(item: Any) -> Any:
        return func(item, "close", float)

    def evaluate(name: str, item: Any) -> dict[str, Any]:
        check: dict[str, Any] = {"fixture": name}
        try:
            result = call(item)
        except Exception as exc:  # noqa: BLE001 - fixture records the failure
            check.update(status="FAIL", error=f"raised {type(exc).__name__}: {exc}")
            return check
        arr = np.asarray(result, dtype=float).reshape(-1)
        check["result_len"] = int(arr.size)
        check["result_kind"] = type(result).__name__
        check["_array"] = arr
        check["status"] = "PASS"
        return check

    checks = [
        evaluate("dataframe", pd.DataFrame({"close": closes})),
        evaluate("series_item_fail_soft", pd.Series(closes)),
        evaluate("structured_array", np.array(
            [(c,) for c in closes], dtype=[("close", "f8")])),
        evaluate("recarray", np.rec.fromarrays([closes], names="close")),
        evaluate("none", None),
        evaluate("empty_structured", np.array([], dtype=[("close", "f8")])),
        evaluate("missing_close_field", pd.DataFrame({"open": closes})),
        evaluate("nan_inf_values", pd.DataFrame({"close": [1.0, float("nan"), float("inf")]})),
    ]

    failures: list[str] = []
    by_name = {check["fixture"]: check for check in checks}
    for check in checks:
        if check["status"] != "PASS":
            failures.append(f"{check['fixture']}: {check.get('error')}")

    df_arr = by_name["dataframe"].get("_array")
    rec_arr = by_name["recarray"].get("_array")
    struct_arr = by_name["structured_array"].get("_array")
    if df_arr is None or rec_arr is None or struct_arr is None:
        failures.append("dataframe/structured/recarray fixtures must all produce arrays")
    else:
        if df_arr.shape != expected.shape or not np.allclose(df_arr, expected):
            failures.append("dataframe fixture values do not match the input closes")
        if not np.allclose(rec_arr, df_arr):
            failures.append("recarray result differs from the DataFrame result")
        if not np.allclose(struct_arr, df_arr):
            failures.append("structured-array result differs from the DataFrame result")

    for name in ("none", "empty_structured", "missing_close_field"):
        arr = by_name[name].get("_array")
        if arr is None:
            continue  # already recorded as failure above
        if arr.size != 0:
            failures.append(f"{name} must fail-soft to an empty array, got length {arr.size}")

    for check in checks:
        check.pop("_array", None)
    return checks, failures


def validate_runtime_shapes(strategy_path: str | Path, helper: str | None = None) -> dict[str, Any]:
    source = Path(strategy_path).read_text(encoding="utf-8-sig")
    report: dict[str, Any] = {
        "report_version": "1.0",
        "source": str(strategy_path),
        "fixture_kind": "agent_first_source_runtime_shape",
    }
    try:
        helper_name, helper_source = _load_helper_source(source, helper)
    except (SyntaxError, ValueError) as exc:
        report.update(status="FAIL", error=str(exc))
        return report
    report["helper"] = helper_name
    namespace = _compile_helper(helper_source)
    func = namespace[helper_name]
    checks, failures = run_fixtures(func)
    report["checks"] = checks
    report["failures"] = failures
    report["status"] = "PASS" if not failures else "FAIL"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run agent-first runtime-shape fixtures")
    parser.add_argument("strategy", help="Strategy Python source containing the helper")
    parser.add_argument("--helper", help="Explicit helper function name")
    parser.add_argument("--out", help="Write fixture report JSON")
    args = parser.parse_args(argv)
    report = validate_runtime_shapes(args.strategy, args.helper)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
