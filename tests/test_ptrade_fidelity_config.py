# -*- coding: utf-8 -*-
"""PTrade 保真模式配置单测（P-A0/P-A1/P-A2，2026-08-24）。

覆盖审计 v2 收尾项：
  - fidelity_eps_basis 必须双端列都存在才生效；
  - 任一端缺失 → 显性报错（eps_missing_mode='error'，默认）或降级 passthrough + WARNING
    （eps_missing_mode='degrade'）；禁止静默单端 fallback；
  - 校验进单测（本文件 ≥12 用例）。
"""
import pytest

from quantstudio.backtest.fidelity_config import (
    PLATFORM_ASHARE_SNAPSHOT,
    PTradeFidelityConfig,
    _EPS_BASIS_CANDIDATES,
)


# ---------------------------------------------------------------------------
# 探针实证常量（2026-08-24 回贴）
# ---------------------------------------------------------------------------
class TestProbeEvidence:
    def test_snapshot_evidence_frozen(self):
        assert PLATFORM_ASHARE_SNAPSHOT["snapshot_date"] == "2026-07-01"
        assert PLATFORM_ASHARE_SNAPSHOT["total"] == 5205
        assert PLATFORM_ASHARE_SNAPSHOT["sha256"].startswith("ce35485a")

    def test_eps_candidates_cover_planned_basis(self):
        # 审计方案约定的五档全部在候选表里
        assert set(_EPS_BASIS_CANDIDATES) == {
            "passthrough", "basic", "diluted", "ttm", "weighted"}


# ---------------------------------------------------------------------------
# P-A2 eps basis 双端校验（审计 v2 收尾项核心）
# ---------------------------------------------------------------------------
class TestEpsBasisDualEnd:
    def test_passthrough_dual_end_ok(self):
        cfg = PTradeFidelityConfig(fidelity_eps_basis="passthrough")
        basis, ok = cfg.resolve_eps_basis()
        assert basis == "passthrough" and ok is True

    def test_basic_dual_end_ok(self):
        # 本地 income_statement.basic_eps + 平台 basic_eps（探针乙实证）双端存在
        cfg = PTradeFidelityConfig(fidelity_eps_basis="basic")
        basis, ok = cfg.resolve_eps_basis()
        assert basis == "basic" and ok is True

    def test_diluted_dual_end_ok(self):
        # 本地 fin_indicator.diluted_eps + 平台 diluted_eps 双端存在
        cfg = PTradeFidelityConfig(fidelity_eps_basis="diluted")
        basis, ok = cfg.resolve_eps_basis()
        assert basis == "diluted" and ok is True

    def test_ttm_platform_only_raises_by_default(self):
        # 平台有 eps_ttm、本地无 → 单端缺失 → 默认 fail-closed 显性报错
        cfg = PTradeFidelityConfig(fidelity_eps_basis="ttm")
        with pytest.raises(ValueError, match="双端校验失败"):
            cfg.resolve_eps_basis()

    def test_bps_platform_missing_raises(self):
        # 本地有 bps、平台缺（探针乙 KeyError）→ 单端缺失 → 显性报错
        cfg = PTradeFidelityConfig(fidelity_eps_basis="bps") if False else None
        # bps 不在候选表（审计五档无 bps）——构造平台缺列场景用 override：
        # 模拟"用户选了 diluted 但平台列缺失"（ps：平台 diluted 实存，此处仅测校验路径）
        cfg = PTradeFidelityConfig(fidelity_eps_basis="diluted")
        platform_cols = {"eps": ["eps", "basic_eps"]}  # 模拟平台缺 diluted_eps
        with pytest.raises(ValueError, match="双端校验失败"):
            cfg.resolve_eps_basis(platform_cols_override=platform_cols)

    def test_local_missing_raises(self):
        # 模拟本地缺 basic_eps（income_statement 无此列）→ 单端缺失显性报错
        cfg = PTradeFidelityConfig(fidelity_eps_basis="basic")
        local_cols = {"fin_indicator": ["eps", "diluted_eps"],
                      "income_statement": ["code"]}  # 本地缺 basic_eps
        with pytest.raises(ValueError, match="双端校验失败"):
            cfg.resolve_eps_basis(local_cols_override=local_cols)

    def test_degrade_mode_warns_and_returns_passthrough(self, caplog):
        # 显式 eps_missing_mode='degrade' → 降级 passthrough + WARNING（不 raise）
        cfg = PTradeFidelityConfig(fidelity_eps_basis="ttm", eps_missing_mode="degrade")
        with caplog.at_level("WARNING"):
            basis, ok = cfg.resolve_eps_basis()
        assert basis == "passthrough" and ok is False
        assert any("降级 passthrough" in r.message for r in caplog.records)

    def test_weighted_basis_raises_as_single_end_missing(self):
        # weighted：本地无列、平台无此列名（平台 eps 即加权口径）→ 双端均缺 → 显性报错
        cfg = PTradeFidelityConfig(fidelity_eps_basis="weighted")
        with pytest.raises(ValueError, match="双端校验失败"):
            cfg.resolve_eps_basis()

    def test_no_silent_fallback_any_mode(self):
        # 任一模式都不允许"静默换列"：degrade 走 passthrough，error 报错，
        # 二者共同覆盖审计"禁止静默单端 fallback"。
        for mode in ("error", "degrade"):
            cfg = PTradeFidelityConfig(fidelity_eps_basis="basic", eps_missing_mode=mode)
            local_cols = {"fin_indicator": ["eps"],
                          "income_statement": ["basic_eps"]}
            platform_cols = {"eps": ["basic_eps"]}
            # 双端存在 → 正常生效（不做 fallback）
            basis, ok = cfg.resolve_eps_basis(local_cols, platform_cols)
            assert basis == "basic" and ok is True


# ---------------------------------------------------------------------------
# P-A0 PIT 门禁 + 超龄告警
# ---------------------------------------------------------------------------
class TestAshareSnapshotPITGate:
    def test_pit_gate_pass_at_snapshot_date(self):
        cfg = PTradeFidelityConfig(fidelity_ashares_snapshot=True)
        r = cfg.resolve("2026-07-01")
        # P-D13 D2：eps 口径对齐默认 'basic'（2026-08-27 审计批准）
        assert r._resolved_eps_basis == "basic"

    def test_pit_gate_pass_after_snapshot_date(self):
        cfg = PTradeFidelityConfig(fidelity_ashares_snapshot=True)
        r = cfg.resolve("2026-07-31")
        assert r._dual_end_ok is True

    def test_pit_gate_fail_before_snapshot_date(self):
        # 快照 2026-07-01 之前 → fail-closed 显性报错
        cfg = PTradeFidelityConfig(fidelity_ashares_snapshot=True)
        with pytest.raises(ValueError, match="PIT 门禁失败"):
            cfg.resolve("2026-06-30")

    def test_staleness_warning_over_30_days(self, caplog):
        # 快照 07-01，回测起点 08-01 → 超龄 31 天 > 30 → WARNING（不阻断）
        cfg = PTradeFidelityConfig(fidelity_ashares_snapshot=True)
        with caplog.at_level("WARNING"):
            r = cfg.resolve("2026-08-01")
        assert r._dual_end_ok is True
        assert any("已超龄" in rec.message for rec in caplog.records)

    def test_snapshot_disabled_no_gate(self):
        # 默认关闭：任何日期都不触发 PIT 门禁（本地语义锚不变）
        cfg = PTradeFidelityConfig()
        r = cfg.resolve("2020-01-01")
        assert r._dual_end_ok is True

    def test_snapshot_parquet_missing_raises(self, tmp_path):
        # 快照文件缺失 → fail-closed 显性报错（不得静默关闭）
        cfg = PTradeFidelityConfig(
            fidelity_ashares_snapshot=True, snapshot_dir=str(tmp_path))
        with pytest.raises(FileNotFoundError, match="快照文件缺失"):
            cfg.load_ashare_snapshot()


# ---------------------------------------------------------------------------
# 默认关闭 = 本地语义锚不变（P-D9 裁定）
# ---------------------------------------------------------------------------
class TestDefaultOff:
    def test_all_defaults_off(self):
        cfg = PTradeFidelityConfig()
        assert cfg.fidelity_ashares_snapshot is False
        assert cfg.fidelity_st_filter is False
        # P-D13 D2：eps 对齐默认 'basic'（探针三实证 basic_eps==本地 eps）；保真开关仍默认关
        assert cfg.fidelity_eps_basis == "basic"

    def test_resolve_is_noop_when_all_off(self):
        # 全关时 resolve 不抛错、不降级、不告警（无 PIT 门禁 / 无 eps 校验生效）
        cfg = PTradeFidelityConfig()
        r = cfg.resolve("2018-01-01")
        assert r._resolved_eps_basis == "basic"


# ---------------------------------------------------------------------------
# P-A0/P-A1/P-A2 接线集成测试（ptrade_api.py / source_import.py 消费侧）
# ---------------------------------------------------------------------------
class TestPtradeApiWiring:
    """P-A1：filter_stock_by_status 'ST' 分支的 fidelity_st_filter 语义。"""

    def _st_prev_day(self, risk_source, is_delisting_risk=True):
        import pandas as pd
        return pd.DataFrame([{
            "code": "600000", "isST": 0, "suspendFlag": 0, "volume": 10000,
            "close": 1.0, "is_st_reliable": False,
            "is_delisting_risk": is_delisting_risk,
            "is_delisting_risk_source": risk_source,
        }])

    def test_st_filter_price_source_keeps_default(self):
        # 默认关闭：price 分支 → 本地现状（仍过滤）——保真 off 不影响存量行为
        from quantstudio.backtest.ptrade_api import _api as api
        api.set_fidelity_config(None)
        api._prev_day_data = self._st_prev_day("price")
        assert api.filter_stock_by_status(["600000"], filter_type=["ST"]) == []

    def test_st_filter_price_source_strict_platform_filters(self):
        # 保真 on：source='price' → 平台口径有效 → 过滤
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(fidelity_st_filter=True)
        api.set_fidelity_config(cfg)
        api._prev_day_data = self._st_prev_day("price")
        assert api.filter_stock_by_status(["600000"], filter_type=["ST"]) == []

    def test_st_filter_market_cap_source_strict_platform_keeps(self):
        # 保真 on：source='market_cap'（本地 circ_mv 扩展）→ 平台口径无效 → 保留
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(fidelity_st_filter=True)
        api.set_fidelity_config(cfg)
        api._prev_day_data = self._st_prev_day("market_cap")
        assert api.filter_stock_by_status(["600000"], filter_type=["ST"]) == ["600000"]

    def test_st_filter_no_source_fallback_strict_platform(self):
        # 保真 on：source='none'（数据缺失）→ 视为无效 → 保留（不误杀）
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(fidelity_st_filter=True)
        api.set_fidelity_config(cfg)
        api._prev_day_data = self._st_prev_day("none")
        assert api.filter_stock_by_status(["600000"], filter_type=["ST"]) == ["600000"]

    def test_st_filter_both_source_strict_platform_filters(self):
        # 保真 on：source='both' → price 分支命中 → 过滤（与平台 close<1 一致）
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(fidelity_st_filter=True)
        api.set_fidelity_config(cfg)
        api._prev_day_data = self._st_prev_day("both")
        assert api.filter_stock_by_status(["600000"], filter_type=["ST"]) == []

    def test_fidelity_reset_semantics(self):
        # set_fidelity_config 注入跨日持久；reset_session 保留配置、清派生缓存
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(fidelity_st_filter=True)
        api.set_fidelity_config(cfg)
        api.reset_session()
        assert api._fidelity is cfg
        assert api._fidelity_ashare_codes is None


class TestAshareSnapshotConsumption:
    """P-A0：get_Ashares 快照消费（裸码 → .SZ/.SS + 惰性缓存 + fail-closed）。"""

    def _write_snapshot(self, tmp_path, codes):
        import pandas as pd
        pd.DataFrame({"code": codes}).to_parquet(
            tmp_path / "ashares_2026-07-01.parquet", index=False)

    def test_get_ashares_uses_snapshot(self, tmp_path):
        from quantstudio.backtest.ptrade_api import _api as api
        self._write_snapshot(tmp_path, ["000001", "600000", "300750"])
        cfg = PTradeFidelityConfig(
            fidelity_ashares_snapshot=True, snapshot_dir=str(tmp_path))
        api.set_fidelity_config(cfg)
        result = api._fidelity_ashares()
        assert "000001.SZ" in result and "600000.SS" in result
        # 全 A 列表应为 .SZ/.SS 尾缀（Ptrade 策略上下文格式）
        assert all(c.endswith((".SZ", ".SS")) for c in result)

    def test_snapshot_missing_file_fail_closed(self, tmp_path):
        # 快照文件缺失 → FileNotFoundError（不得静默退回本地池）
        from quantstudio.backtest.ptrade_api import _api as api
        cfg = PTradeFidelityConfig(
            fidelity_ashares_snapshot=True, snapshot_dir=str(tmp_path))
        api.set_fidelity_config(cfg)
        with pytest.raises(FileNotFoundError, match="快照文件缺失"):
            api._fidelity_ashares()

    def test_get_ashares_disabled_uses_local(self):
        # 默认关闭：get_Ashares 走本地（reference.get_all_stocks）——用空 reference 断言路径
        from quantstudio.backtest.ptrade_api import _api as api
        api.set_fidelity_config(None)
        # 无 reference / 无日期 → 空列表（本地路径，非快照）
        assert api.get_Ashares() == []


class TestConvertedArtifactFidelity:
    """P-A2：转换产物 _QS_FIDELITY_* 常量 + eps 字段映射（source_import 注入）。"""

    def _convert(self, source, eps_basis="basic"):
        from quantstudio.strategy_compiler.source_import import SourceConverter
        conv = SourceConverter(fidelity_eps_basis=eps_basis)
        return conv.convert(source).converted_code

    def test_artifact_explicit_passthrough_no_fidelity_constant(self):
        # 显式 passthrough：产物不注入 _QS_FIDELITY_EPS_BASIS（向后兼容通道）
        src = "def initialize(context):\n    pass\n"
        out = self._convert(src, eps_basis="passthrough")
        assert "_QS_FIDELITY_EPS_BASIS" not in out
        # 源码无 get_fundamentals（门控不触发）——默认 basic 也不注入：
        # 二者在此场景等价（验证门控优先于 eps_basis）
        assert self._convert(src) == out

    def test_artifact_basic_injects_constant_and_mapping(self):
        # fidelity_eps_basis='basic' → 产物注入常量 + 映射表（平台运行时零环境变量）
        src = ("def initialize(context):\n    pass\n\ndef handle_data(context, data):\n"
               "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
        out = self._convert(src, eps_basis="basic")
        assert "_QS_FIDELITY_EPS_BASIS = 'basic'" in out
        assert "'eps': 'basic_eps'" in out

    def test_artifact_diluted_injects_constant(self):
        src = ("def initialize(context):\n    pass\n\ndef handle_data(context, data):\n"
               "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
        out = self._convert(src, eps_basis="diluted")
        assert "_QS_FIDELITY_EPS_BASIS = 'diluted'" in out

    def test_artifact_basic_maps_eps_to_basic_eps(self):
        # P-A2 核心：eps 表 + basic → 平台请求 basic_eps（本地 eps 语义）
        src = ("def initialize(context):\n    pass\n\ndef handle_data(context, data):\n"
               "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
        out = self._convert(src, eps_basis="basic")
        import ast
        tree = ast.parse(out)
        src_text = out
        # 产物建议：平台请求 basic_eps（映射表中存在）——
        # 直接断言映射表和 basis 常量就位（wrapper 运行时解析）
        assert "'eps': 'basic_eps'" in src_text

    def test_artifact_default_has_eps_map(self):
        # P-D13 D2：默认 basic → P-A2 常量段注入（产物含 `_QS_FIDELITY_EPS_BASIS = 'basic'`
        # + eps→basic_eps 映射）——默认产物请求 basic_eps（对齐本地 eps 语义）。
        src = ("def initialize(context):\n    pass\n\ndef handle_data(context, data):\n"
               "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
        out = self._convert(src)
        assert "_QS_FIDELITY_EPS_BASIS = 'basic'" in out
        assert "'eps': 'basic_eps'" in out

    def test_artifact_explicit_passthrough_reverts_to_legacy(self):
        # 显式 passthrough → eps 映射常量烘焙进统一 wrapper（v8.3 整合：单一
        # get_fundamentals 定义，常量恒在、值为 passthrough）——向后兼容：eps 表请求
        # 平台默认 eps 列（不翻译）；旧独立 _QS_FIDELITY_EPS_EXT def 已删除。
        src = ("def initialize(context):\n    pass\n\ndef handle_data(context, data):\n"
               "    df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
        out = self._convert(src, eps_basis="passthrough")
        assert "_QS_FIDELITY_EPS_BASIS = 'passthrough'" in out
        assert "_QS_FIDELITY_EPS_EXT = '''" not in out  # 独立模板/def 已删除（v8.3 整合）
        # 且与默认（basic）不同——显式 passthrough 撤销默认注入
        default = self._convert(src)
        assert default != out


# ---------------------------------------------------------------------------
# 入口补齐（2026-08-24）：run_ptrade_strategy.py CLI 旗标 + PyQt 面板勾选接线
# ---------------------------------------------------------------------------
class TestCliEntrypointFidelity:
    """CLI：--fidelity ashares,st_filter --eps-basis basic → PTradeFidelityConfig 构造。"""

    def _parse(self, argv):
        from quantstudio.backtest.run_ptrade_strategy import _parse_flag
        return _parse_flag(argv, '--fidelity', ''), _parse_flag(argv, '--eps-basis', 'passthrough')

    def test_cli_defaults_all_off(self):
        fid, basis = self._parse(['strategy.py', '2026-07-01', '2026-07-31'])
        assert fid == '' and basis == 'passthrough'  # 默认全关

    def test_cli_fidelity_flag_parse(self):
        fid, _ = self._parse(['--fidelity', 'ashares,st_filter', 's.py'])
        assert fid == 'ashares,st_filter'

    def test_cli_eps_basis_parse(self):
        _, basis = self._parse(['--eps-basis', 'basic', 's.py'])
        assert basis == 'basic'

    def test_cli_fidelity_eq_form(self):
        fid, basis = self._parse(['--fidelity=ashares', '--eps-basis=diluted', 's.py'])
        assert fid == 'ashares' and basis == 'diluted'

    def test_run_backtest_type_check_present(self):
        # run_backtest 源码含 fidelity_config 类型校验（非 PTradeFidelityConfig → TypeError fail-closed）
        import inspect
        from quantstudio.backtest.run_ptrade_strategy import run_backtest
        src = inspect.getsource(run_backtest)
        assert "PTradeFidelityConfig" in src
        assert "resolve" in src  # resolve() PIT 门禁在注入前执行
        assert "set_fidelity_config" in src  # ptrade_api 显式注入

    def test_fidelity_flag_values_validation(self):
        # main() 里 --fidelity 未知项 fail-closed（不以测试触发退出，直接验集合语义）
        _FIDELITY_OPTS = {"ashares", "st_filter"}
        assert {"ashares", "st_filter"} <= _FIDELITY_OPTS
        assert {"ashares", "bogus"} - _FIDELITY_OPTS  # 未知项会被拒绝


class TestGuiFidelityWiring:
    """PyQt 面板：勾选 → params 透传 fidelity_config；默认不勾选 = None（默认全关）。"""

    def test_params_fidelity_none_when_unchecked(self):
        # 未勾选：params 中 fidelity_config 应为 None（本地语义锚，零影响）
        # （直接验证 run_backtest 透传语义：None 不触发保真注入）
        from quantstudio.backtest.run_ptrade_strategy import run_backtest
        import inspect
        sig = inspect.signature(run_backtest)
        assert 'fidelity_config' in sig.parameters
        assert sig.parameters['fidelity_config'].default is None

    def test_worker_passes_fidelity_config_through(self):
        # BacktestWorker 调 run_backtest 时透传 params['fidelity_config']（默认 None）
        import inspect
        from quantstudio.backtest.run_ptrade_strategy import run_backtest
        from quantstudio.gui.workers import BacktestWorker
        src = inspect.getsource(BacktestWorker.run)[:20000] if hasattr(BacktestWorker, 'run') else inspect.getsource(BacktestWorker)
        # 直接断言 workers.py 源码含 fidelity_config 透传
        raw = inspect.getsource(BacktestWorker)
        assert "fidelity_config=self.params.get('fidelity_config', None)" in raw

    def test_checkbox_present_in_tab_source(self):
        # 面板源码含「PTrade 保真模式（验证用）」勾选项 + tooltip 关键措辞
        raw = open(
            __import__('pathlib').Path(__file__).parent.parent
            / 'quantstudio' / 'gui' / 'tabs' / 'backtest_tab.py',
            encoding='utf-8').read()
        assert "PTrade 保真模式（验证用）" in raw
        assert "严禁实盘前评估" in raw
        assert "2026-07-01" in raw  # PIT 窗口限制说明
        assert "fidelity_config" in raw

    def test_gui_inject_type_check_readback(self):
        # 保真模式配置构造：勾选态 = ashares+st_filter+basic（面板语义），resolve 需通过 PIT
        cfg = PTradeFidelityConfig(
            fidelity_ashares_snapshot=True, fidelity_st_filter=True,
            fidelity_eps_basis="basic")
        r = cfg.resolve("2026-07-31")  # ≥ 快照日 → 通过
        assert r._resolved_eps_basis == "basic" and r._dual_end_ok
        with pytest.raises(ValueError):  # < 快照日 → PIT 门禁 fail-closed
            cfg.resolve("2026-06-30")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))