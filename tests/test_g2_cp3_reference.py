"""tests/test_g2_cp3_reference.py — Strategy Compiler G2 CP3 Reference closure tests.

G2 CP3 = independent hand-written Reference Oracle producing frozen signal/order/NAV
reference artifacts + source/data/config/engine digests + run_card reference_closure.

核心断言（review 硬要求，不靠测试数量取 PASS）：
- Oracle 移植完整性（源 SHA-256 比对）；
- 4 artifacts 由 Oracle 真实运行生成（非手工拼接）+ schema 校验 + 确定性；
- reference_orders 三字段分离严格独立（trigger_reason 策略层 / order_status 引擎层 /
  order_reason 引擎层）；rejected 原因不得塞 trigger_reason；
- source_digest 字段齐全；input_data_digest 缺失时 data_digest_status=blocked；
- run_card reference_closure 写入 + 旧 run_card（无该段）仍校验通过；
- Oracle vs G1-I engine 对照（hermetic，不连真实 DB/live QMT）；
- 运行隔离：不连正式 DuckDB / live QMT。

全 hermetic：合成 history/context stubs。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ORACLE_PATH = ROOT / "tests" / "strategy_references" / "etf_rotation_ref.py"
SPEC_PATH = ROOT / "quantstudio" / "strategy_compiler" / "examples" / "etf_rotation_spec.json"
PROVENANCE_PATH = ROOT / "tests" / "strategy_references" / "reference_provenance.json"
SCHEMAS = ROOT / "quantstudio" / "strategy_compiler" / "schemas"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _load_oracle_constants():
    """Load Oracle module constants (ETF_POOL, DEFENSIVE_ASSET) without running callbacks."""
    import importlib.util
    import types
    spec = importlib.util.spec_from_file_location("oref_test", ORACLE_PATH)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__.update({
        "set_universe": lambda x: None, "set_limit_mode": lambda x: None,
        "set_commission": lambda **k: None, "set_benchmark": lambda x: None,
        "log": types.SimpleNamespace(info=lambda *a, **k: None),
        "g": types.SimpleNamespace(),
    })
    spec.loader.exec_module(mod)
    return mod


def _build_scenario(n_days=3):
    """Deterministic 3-day uptrend scenario (all ETFs trending up, normal volume)."""
    from quantstudio.strategy_compiler.reference.artifact_builder import Scenario, ScenarioDay
    mod = _load_oracle_constants()
    pool = mod.ETF_POOL
    base_days = ["2026-01-06", "2026-01-07", "2026-01-08"]
    days = []
    for d in base_days[:n_days]:
        closes, vols, raws, opens = {}, {}, {}, {}
        for qmt in pool:
            b = qmt.split(".")[0]
            # uptrend series (close > MA20 > MA60), deterministic per code
            series = [10.0 + 0.05 * i + (abs(hash(b)) % 100) * 0.001 for i in range(70)]
            closes[b] = series
            vols[b] = [1000.0] * 70
            raws[b] = series[-1]
            opens[b] = series[-1]
        days.append(ScenarioDay(trading_date=d, t1_open_prices=opens,
                                history_closes=closes, history_volumes=vols,
                                t1_raw_closes=raws))
    return Scenario(strategy_id="etf_regression_rotation_v1",
                    engine_semantics_version="0.4.0-next_open_basket",
                    etf_pool=pool, defensive_asset=mod.DEFENSIVE_ASSET, days=days)


def _build_source_digest():
    from quantstudio.strategy_compiler.reference.source_digest import compute_source_digest
    schema_paths = [
        SCHEMAS / "reference_signals.schema.json",
        SCHEMAS / "reference_orders.schema.json",
        SCHEMAS / "reference_nav.schema.json",
        SCHEMAS / "reference_source_digest.schema.json",
    ]
    spec = _load_json(SPEC_PATH)
    return compute_source_digest(
        ORACLE_PATH, SPEC_PATH, [str(p) for p in schema_paths], spec,
        engine_commit="b3da10b-g2-test",
        engine_semantics_version="0.4.0-next_open_basket",
        data_digest_status="blocked",
        data_digest_block_reason="hermetic synthetic scenario; no real market data digest in CP3 scope",
    )


def _build_artifacts():
    from quantstudio.strategy_compiler.reference.artifact_builder import build_reference_artifacts
    return build_reference_artifacts(_build_scenario(), ORACLE_PATH, _build_source_digest())


@pytest.fixture(scope="module")
def artifacts():
    return _build_artifacts()


# ── 1. Oracle port integrity (provenance + source SHA-256) ──────────────────

class TestOraclePortIntegrity:
    def test_provenance_records_source_hashes(self):
        """reference_provenance.json records the pr6b2a source SHA-256 for all 3 ported files."""
        prov = _load_json(PROVENANCE_PATH)
        assert prov["source_head_commit"] == "8931430"
        assert prov["engine_semantics_version"] == "0.4.0-next_open_basket"
        for f in prov["ported_files"]:
            assert len(f["source_sha256"]) == 64
        # the Oracle file's recorded hash matches the actual ported file
        oracle_entry = next(f for f in prov["ported_files"]
                            if f["path"] == "tests/strategy_references/etf_rotation_ref.py")
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        assert sha256_file(ORACLE_PATH) == oracle_entry["source_sha256"]

    def test_spec_engine_version_updated_to_basket(self):
        """Spec engine_semantics_version updated to 0.4.0-next_open_basket (G1-I in main)."""
        spec = _load_json(SPEC_PATH)
        assert spec["contract_versions"]["engine_semantics_version"] == "0.4.0-next_open_basket"

    def test_oracle_independence_preserved(self):
        """Oracle source still declares independence (not generated by Codegen/Renderer)."""
        src = ORACLE_PATH.read_text(encoding="utf-8")
        assert "NOT generated by" in src
        # does not import compiler/codegen
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "strategy_compiler" not in s
                assert "codegen" not in s.lower()


# ── 2. Artifact generation (real Oracle run, schema validation, determinism) ─

class TestArtifactGeneration:
    def test_signals_artifact_schema_valid(self, artifacts):
        schema = _load_json(SCHEMAS / "reference_signals.schema.json")
        jsonschema.validate(artifacts.signals, schema)

    def test_orders_artifact_schema_valid(self, artifacts):
        schema = _load_json(SCHEMAS / "reference_orders.schema.json")
        jsonschema.validate(artifacts.orders, schema)

    def test_nav_artifact_schema_valid(self, artifacts):
        schema = _load_json(SCHEMAS / "reference_nav.schema.json")
        jsonschema.validate(artifacts.nav, schema)

    def test_source_digest_schema_valid(self, artifacts):
        schema = _load_json(SCHEMAS / "reference_source_digest.schema.json")
        jsonschema.validate(artifacts.source_digest, schema)

    def test_artifacts_nonempty_and_generated(self, artifacts):
        """Artifacts are produced by a real Oracle run (non-empty, structured)."""
        assert len(artifacts.signals["signals"]) > 0
        assert len(artifacts.nav["nav_series"]) > 0
        # each artifact carries schema_version + strategy_id + engine version + timezone + digest ref
        for art in (artifacts.signals, artifacts.orders, artifacts.nav):
            assert art["schema_version"] == "1.0"
            assert art["strategy_id"] == "etf_regression_rotation_v1"
            assert art["engine_semantics_version"] == "0.4.0-next_open_basket"
            assert art["timezone"] == "Asia/Shanghai"
            assert len(art["source_digest_ref"]) == 64

    def test_artifacts_deterministic(self):
        """Two runs over the same scenario yield identical signal/order/NAV content
        (except generated_at timestamp)."""
        a1 = _build_artifacts()
        a2 = _build_artifacts()
        for key in ("signals", "orders", "nav"):
            d1 = {k: v for k, v in a1.__dict__[key].items() if k != "generated_at"}
            d2 = {k: v for k, v in a2.__dict__[key].items() if k != "generated_at"}
            assert d1 == d2, f"{key} not deterministic"


# ── 3. Three-field separation (trigger_reason / order_status / order_reason) ─

class TestThreeFieldSeparation:
    def test_trigger_reason_is_strategy_layer_only(self, artifacts):
        """trigger_reason must be a strategy label (or None), never an engine reject reason."""
        strategy_labels = {"stop_loss", "volume_surge", "rotation_exit", "rotation_buy",
                           "defensive_clear", "defensive_buy", "no_candidate_clear"}
        engine_reasons = {"insufficient_cash_after_sells", "mandatory_sell_failed",
                          "limit_down_blocked", "direction_changed_at_drain", "halted",
                          "limit_up_blocked", "insufficient_position"}
        for o in artifacts.orders["orders"]:
            if o["trigger_reason"] is not None:
                assert o["trigger_reason"] in strategy_labels, \
                    f"trigger_reason {o['trigger_reason']} is not a strategy label"
                assert o["trigger_reason"] not in engine_reasons, \
                    "engine reject reason leaked into trigger_reason"

    def test_order_reason_is_engine_layer_only(self, artifacts):
        """order_reason must be an engine reject/cancel reason (or None), never a strategy trigger."""
        strategy_labels = {"stop_loss", "volume_surge", "rotation_exit", "rotation_buy",
                           "defensive_clear", "defensive_buy", "no_candidate_clear"}
        for o in artifacts.orders["orders"]:
            if o["order_reason"] is not None:
                assert o["order_reason"] not in strategy_labels, \
                    f"strategy trigger {o['order_reason']} leaked into order_reason"

    def test_order_status_is_engine_terminal(self, artifacts):
        for o in artifacts.orders["orders"]:
            assert o["order_status"] in {"filled", "rejected", "pending", "noop", "cancelled"}

    def test_rejected_reason_not_in_trigger(self, artifacts):
        """If any order is rejected, its reject cause is in order_reason, NOT trigger_reason."""
        for o in artifacts.orders["orders"]:
            if o["order_status"] == "rejected":
                assert o["order_reason"] is not None, "rejected order missing order_reason"
                engine_reasons = {"insufficient_cash_after_sells", "mandatory_sell_failed",
                                  "limit_down_blocked", "direction_changed_at_drain", "halted"}
                assert o["order_reason"] in engine_reasons


# ── 4. source_digest completeness + honest data-digest blocking ──────────────

class TestSourceDigest:
    def test_digest_fields_complete(self, artifacts):
        sd = artifacts.source_digest
        for k in ("oracle_source_digest", "spec_digest", "artifact_schema_digest",
                  "config_digest", "engine_commit", "engine_semantics_version",
                  "data_digest_status"):
            assert k in sd and sd[k], f"missing digest field {k}"

    def test_data_digest_blocked_when_no_real_data(self, artifacts):
        """When no real input-data digest exists, data_digest_status=blocked (not faked)."""
        sd = artifacts.source_digest
        if sd["input_data_digest"] is None:
            assert sd["data_digest_status"] == "blocked"
            assert sd["data_digest_block_reason"] is not None

    def test_oracle_digest_matches_file(self, artifacts):
        from quantstudio.strategy_compiler.reference.source_digest import sha256_file
        assert artifacts.source_digest["oracle_source_digest"] == sha256_file(ORACLE_PATH)

    def test_config_digest_matches_spec(self, artifacts):
        from quantstudio.strategy_compiler.reference.source_digest import sha256_json
        spec = _load_json(SPEC_PATH)
        assert artifacts.source_digest["config_digest"] == sha256_json(spec)


# ── 5. run_card reference_closure + backward compatibility ──────────────────

class TestRunCardClosure:
    def _minimal_run_card(self):
        """A pre-G2 run_card (no reference_closure) — must still validate after schema update."""
        return {
            "run_card_version": "1.0", "run_id": "rc_test", "strategy_id": "etf_regression_rotation_v1",
            "build_id": "b1", "created_at": "2026-07-23T00:00:00+08:00", "stage": "STATIC_VALIDATED",
            "status": "PASS",
            "contract_versions": {"strategy_spec_version": "1.0.0", "engine_semantics_version": "0.4.0-next_open_basket",
                                  "provider_contract_version": "0.2.0-frequency-aware", "security_code_rules_version": "1.0.0",
                                  "ptrade_profile_version": "1.0.0-default", "renderer_version": "0.2.0-pr6b2a", "skill_version": "0.2.0-pr6b2a"},
            "profile": {"engine_profile_id": "daily-bar-v1", "ptrade_profile_id": "1.0.0-default", "execution_status": "READY"},
            "data_window": {"start": "2026-01-01", "end": "2026-01-31", "as_of": "2026-07-23T00:00:00+08:00"},
            "artifacts": [], "validation": {"schema": "PASS", "timing": "PASS", "hard_filters": "PASS",
                                            "api_portability": "PASS", "variant_consistency": "PASS"},
            "smoke_backtest": None, "fidelity": None, "approximations": [], "known_limitations": [],
            "reproducibility": {"python_version": "3.11.0", "quantstudio_version": "0.3.0-mvp",
                                "random_seed": None, "data_fingerprint": None},
        }

    def test_old_run_card_without_closure_still_valid(self):
        """Backward compat: a run_card lacking reference_closure validates post-schema-update."""
        schema = _load_json(SCHEMAS / "run_card.schema.json")
        rc = self._minimal_run_card()
        jsonschema.validate(rc, schema)  # must NOT raise

    def test_attach_reference_closure_valid(self, artifacts):
        """run_card with reference_closure attached validates against the updated schema."""
        from quantstudio.strategy_compiler.reference.run_card_writer import attach_reference_closure
        schema = _load_json(SCHEMAS / "run_card.schema.json")
        rc = self._minimal_run_card()
        sd = artifacts.source_digest
        rc2 = attach_reference_closure(
            rc,
            oracle_source_digest=sd["oracle_source_digest"],
            data_digest=sd["input_data_digest"],
            config_digest=sd["config_digest"],
            signals_path="reference_signals.json",
            orders_path="reference_orders.json",
            nav_path="reference_nav.json",
            source_digest_path="source_digest.json",
            trigger_reason_taxonomy=artifacts.orders["trigger_reason_taxonomy"],
        )
        jsonschema.validate(rc2, schema)
        assert rc2["reference_closure"]["schema_version"] == "1.0"
        # original run_card not mutated
        assert "reference_closure" not in rc


# ── 6. Oracle vs G1-I engine contrast ───────────────────────────────────────

class TestOracleVsEngineContrast:
    def test_engine_exposes_basket_metadata_for_contrast(self):
        """G1-I engine (in main) exposes _baskets / _today_orders / Order.basket_id,
        the runtime objects a reference-vs-engine contrast would capture.
        This confirms the contrast surface exists without running a full backtest."""
        from quantstudio.backtest.backtest_engine import BacktestEngine, Order
        import inspect
        # Order has basket_id field
        sig = inspect.signature(Order)
        assert "basket_id" in sig.parameters or hasattr(Order, "basket_id") or \
            "basket_id" in getattr(Order, "__dataclass_fields__", {}) or \
            "basket_id" in Order.__annotations__
        # BacktestEngine has _baskets and _today_orders attributes (set in __init__)
        src = inspect.getsource(BacktestEngine.__init__)
        assert "_baskets" in src and "_today_orders" in src

    def test_oracle_path_does_not_enter_basket_engine(self, artifacts):
        """Oracle orders run via the independent Oracle's order_target_value stub;
        basket_id stays None in the reference artifact (Oracle path is independent of G1-I basket)."""
        for o in artifacts.orders["orders"]:
            assert o["basket_id"] is None, \
                "reference order should not carry a G1-I basket_id (Oracle is independent)"


# ── 7. Runtime isolation (no real DB / live QMT) ─────────────────────────────

class TestRuntimeIsolation:
    def test_no_duckdb_connection_in_reference_module(self):
        """reference module must not import duckdb or connect to a real DB."""
        import quantstudio.strategy_compiler.reference.artifact_builder as ab
        import quantstudio.strategy_compiler.reference.source_digest as sd
        for mod in (ab, sd):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "import duckdb" not in src
            assert "connect(" not in src

    def test_no_xtquant_import_in_reference_module(self):
        import quantstudio.strategy_compiler.reference.artifact_builder as ab
        src = Path(ab.__file__).read_text(encoding="utf-8")
        assert "xtquant" not in src
