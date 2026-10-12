# -*- coding: utf-8 -*-
"""test_qmt_hist_vectorize —— D-26 向量化修复的黄金对照（判型＝纯性能型）。

判据（docs Plan ⑧）：修复前后 `_qs_fin_to_rows_hist` 输出**逐位一致**；
两条路径（path=batch / path=percode）均须一致。

基准：docs/evidence/_bak/source_import_qmt.py.bak-20261012-d26（修复前版本，
Panel 分支为 per-code `raw[_code]` + `iterrows()`）。

run: pytest tests/test_qmt_hist_vectorize.py -q
"""
import importlib.machinery
import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"
BAK = ROOT / "docs" / "evidence" / "_bak" / "source_import_qmt.py.bak-20261012-d26"
_TEMPLATES = ("_QS_QMT_LOG_EXT", "_QS_QMT_CONTEXT_VIEW_EXT", "_QS_QMT_DATA_VIEW_EXT",
              "_QS_QMT_HISTORY_EXT", "_QS_QMT_POSITION_EXT", "_QS_QMT_ORDER_EXT",
              "_QS_QMT_ASHARES_EXT", "_QS_QMT_TRADE_DAYS_EXT",
              "_QS_QMT_STOCK_INFO_EXT", "_QS_QMT_FUNDAMENTALS_EXT",
              "_QS_QMT_WRAPPER_ZONE")


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # @dataclass 字符串注解需 sys.modules
    loader.exec_module(mod)
    return mod


def _ns(mod):
    ns = {"_qs_pick_ctx": lambda C=None: C, "pd": pd}
    for n in ("_QS_QMT_FUND_FIELD_MAP", "_qs_snap_kwargs"):
        if hasattr(mod, n):
            ns[n] = getattr(mod, n)
    for t in _TEMPLATES:
        exec(getattr(mod, t), ns)
    return ns


class _FakePanel(object):
    """实现 Panel 的 duck-typing 面：major_axis / items / __getitem__ / to_frame。"""

    def __init__(self, data, with_to_frame=True):
        self._d = data
        self.items = list(data.keys())
        self.major_axis = [0, 1, 2]
        self._with = with_to_frame
        self.to_frame_called = False

    def __getitem__(self, k):
        return self._d[k]

    def to_frame(self):
        self.to_frame_called = True
        if not self._with:
            raise AttributeError("no to_frame")
        parts = []
        for c, df in self._d.items():
            d = df.copy()
            d.index = pd.MultiIndex.from_product([[c], list(d.index)])
            parts.append(d)
        return pd.concat(parts)


def _mk(seed):
    return {"%06d.SZ" % (seed + i): pd.DataFrame(
        {"s_fa_eps_basic": [0.1 * i, 0.2 * i], "m_anntime": [1.6e12, 1.7e12],
         "m_timetag": [1.6e12, 1.7e12]},
        index=[0, 1]) for i in range(3)}


@pytest.fixture(scope="module")
def cur():
    return _ns(_load(SRC, "qmt_d26_cur"))


@pytest.fixture(scope="module")
def old():
    return _ns(_load(BAK, "qmt_d26_old"))


@pytest.mark.parametrize("with_to_frame", [True, False])
def test_golden_identical(cur, old, capsys, with_to_frame):
    """批量路径与回落路径的输出均须与修复前逐位一致。"""
    panel = _FakePanel(_mk(1), with_to_frame=with_to_frame)
    new = cur["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    capsys.readouterr()
    ref = old["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    capsys.readouterr()
    assert new == ref, "输出未逐位一致（纯性能型判据违背）"


def test_batch_path_uses_to_frame(cur):
    """batch 路径须真正调用 to_frame（不得静默回落）。
    注：3 只 < 阈值 500 → 进度行按设计静默，故以调用记录断言（不依赖日志）。"""
    panel = _FakePanel(_mk(9), with_to_frame=True)
    out = cur["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    assert panel.to_frame_called is True, "未走 batch 路径"
    assert len(out) == 3


def test_percode_fallback_when_to_frame_raises(cur):
    """to_frame 抛异常 → 回落 per-code；异常被吞、输出仍正确。"""
    panel = _FakePanel(_mk(9), with_to_frame=False)
    out = cur["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    assert panel.to_frame_called is True, "未尝试 to_frame"
    assert len(out) == 3


def test_return_shape_contract(cur):
    """返回结构契约：{str(code): [ {str(k): v}, ... ]}。"""
    panel = _FakePanel(_mk(5))
    out = cur["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    assert all(isinstance(k, str) for k in out)
    for rows in out.values():
        assert isinstance(rows, list) and rows and isinstance(rows[0], dict)
        assert all(isinstance(k, str) for k in rows[0])
        assert len(rows) == 2


def test_fallback_when_loc_miss(cur):
    """D-26b 回归闸：真实 Panel 的 to_frame 结构与假设不符（.loc 取不到）时，
    batch 必须回落 raw[_code]，输出**不得为空**。

    背景：22:19 端测 `QSFUNDHIST kept=0 cc=0` → `L3v_complete=0` → 零交易——
    即 .loc 取不到被静默吞成 None 的事故。本用例锁死该路径。
    """
    panel = _FakePanel(_mk(3))
    panel.to_frame = lambda: pd.DataFrame(          # 结构不符：index 非以 code 为首层
        {"x": [1, 2]}, index=pd.Index([0, 1], name="nope"))
    out = cur["_qs_fin_to_rows_hist"](panel, shape=None, codes=list(panel.items))
    assert len(out) == 3, "回落未生效：输出为空（kept=0 事故未修复）"
    assert all(len(v) == 2 for v in out.values())
