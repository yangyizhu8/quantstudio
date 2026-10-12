# -*- coding: utf-8 -*-
"""test_qmt_progress_audit —— D-24 进度审计行验收单测（判型＝新增检测型）。

判据映射（docs/qmt-progress-audit-design.md v1.2 §6）：
  a  契约：tick 步长触发 / 非步长不触发 / total<500 全静默 / 收尾恒打印 /
           哨兵七字段 / begin-end 配对（含异常路径）
  b  黄金对照：与改动前模块（_bak/*.bak-20261012-d24）返回对象逐位一致
  h  elapsed 有效性：t0 有效时 elapsed > 0；t0=None 时回落 0.0 不抛
  i  无 time 依赖泄漏：AST-only 机检（模块顶层无 import time；模板内 time
     仅出现于两个 helper 函数体）

run: pytest tests/test_qmt_progress_audit.py -q
"""
import ast
import importlib.machinery
import importlib.util
import pathlib
import re
import sys
import time

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import_qmt.py"
BAK = ROOT / "docs" / "evidence" / "_bak" / "source_import_qmt.py.bak-20261012-d24"
EVERY = 500

_MODULE_NAMES = ("_QS_QMT_FUND_FIELD_MAP", "_qs_snap_kwargs", "_qs_norm_ymd")
_TEMPLATES = ("_QS_QMT_LOG_EXT", "_QS_QMT_CONTEXT_VIEW_EXT", "_QS_QMT_DATA_VIEW_EXT",
              "_QS_QMT_HISTORY_EXT", "_QS_QMT_POSITION_EXT", "_QS_QMT_ORDER_EXT",
              "_QS_QMT_ASHARES_EXT", "_QS_QMT_TRADE_DAYS_EXT",
              "_QS_QMT_STOCK_INFO_EXT", "_QS_QMT_FUNDAMENTALS_EXT",
              "_QS_QMT_WRAPPER_ZONE")
_MOD_CACHE = {}


def _load(path, name):
    """加载任意后缀的 .py 副本（.bak-* 不被 spec_from_file_location 识别）。

    须注册进 sys.modules：本模块用 @dataclass 且注解为字符串，dataclasses
    的 _is_type 依赖 sys.modules 查找，未注册即 AttributeError。
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _mod(key, path, name):
    if key not in _MOD_CACHE:
        _MOD_CACHE[key] = _load(path, name)
    return _MOD_CACHE[key]


def _ns(mod):
    """按 _assemble 序 exec 全部模板 → 单一命名空间（注入 _qs_pick_ctx 直通替身）。"""
    ns = {"_qs_pick_ctx": lambda C=None: C, "pd": pd}
    for n in _MODULE_NAMES:
        if hasattr(mod, n):
            ns[n] = getattr(mod, n)
    for t in _TEMPLATES:
        if hasattr(mod, t):
            exec(getattr(mod, t), ns)
    return ns


@pytest.fixture(scope="module")
def cur_mod():
    return _mod("cur", SRC, "qmt_cur")


@pytest.fixture(scope="module")
def cur(cur_mod):
    return _ns(cur_mod)


@pytest.fixture(scope="module")
def old():
    return _ns(_mod("old", BAK, "qmt_old"))


class _FakeCtx(object):
    def __init__(self, table, boom=None):
        self._t = table
        self._boom = boom or set()

    def get_instrument_detail(self, code):
        return self._t.get(code)

    def get_market_data_ex(self, *a, **kw):
        return {}

    def get_financial_data(self, *a, **kw):
        if "fin" in self._boom:
            raise RuntimeError("boom")
        return {}


def _lines(capsys):
    return capsys.readouterr().out.splitlines()


# ------------------------------------------------------------------ 判据 a

def test_a_tick_step_and_silence(cur, capsys):
    p = cur["_qs_progress"]
    p("f", EVERY, 5227, 0.0)
    assert len(_lines(capsys)) == 1, "步长命中应打印"

    p("f", EVERY + 1, 5227, 0.0)
    assert _lines(capsys) == [], "非步长不应打印"

    p("f", 1, EVERY - 1, 0.0)
    assert _lines(capsys) == [], "total<500 应全静默"

    p("f", 5227, 5227, 0.0)
    assert len(_lines(capsys)) == 1, "收尾 done==total 恒打印"


def test_a_sentinel_seven_fields(cur, capsys):
    cur["_qs_progress"]("get_financial_data", 0, 4503, time.time(), "begin", "table=valuation")
    line = _lines(capsys)[0]
    # 契约：done=<n>/<total> 为复合字段（total 为分母），无独立 total= 键
    for k in ("func=", "done=", "pct=", "elapsed=", "phase="):
        assert k in line, "缺字段 %s：%s" % (k, line)
    assert "done=0/4503" in line, "done/total 复合字段缺失：%s" % line
    assert "extra=table=valuation" in line
    assert re.match(r"^QS_QMT_PROGRESS func=get_financial_data done=0/4503 "
                    r"pct=0\.0 elapsed=\d+\.\ds phase=begin extra=table=valuation$", line)


def test_a_end_ignores_step(cur, capsys):
    """end 不受 tick 步长约束（done 可为任意值）。"""
    cur["_qs_progress"]("get_financial_data", 4503, 4503, 0.0, "end", "table=eps err=0")
    assert len(_lines(capsys)) == 1


def test_a_begin_end_paired_on_exception(cur, capsys):
    """异常路径亦须成对（finally 保证）——否则孤儿 begin 与死锁不可区分。"""
    fake = _FakeCtx({}, boom={"fin"})
    out = cur["_qs_get_fundamentals"]("valuation", ["000001.SZ"] * 600,
                                      fields=["float_value"], C=fake)
    assert isinstance(out, pd.DataFrame)
    txt = "\n".join(_lines(capsys))
    assert "phase=begin" in txt and "phase=end" in txt, "begin/end 未配对：\n" + txt
    assert "err=1" in txt, "异常语境应标 err=1"


# ------------------------------------------------------------------ 判据 b

@pytest.mark.parametrize("codes", [
    ["000001.SZ"], ["000001.SZ", "600000.SH", "301999.SZ"],
])
def test_b_stock_info_golden(cur, old, capsys, codes):
    tbl = {"000001.SZ": {"InstrumentName": "平安银行", "OpenDate": 19910403,
                         "ExpireDate": 99999999, "InstrumentStatus": 0,
                         "IsTrading": None, "UpStopPrice": 11.0,
                         "DownStopPrice": 9.0, "FloatVolume": 1.0e9},
           "600000.SH": {"InstrumentName": "浦发银行", "OpenDate": 19991110,
                         "ExpireDate": 0, "InstrumentStatus": 0,
                         "IsTrading": None},
           "301999.SZ": {}}
    new = cur["get_stock_info"](codes, field=["listed_date", "start_date"], C=_FakeCtx(tbl))
    capsys.readouterr()
    ref = old["get_stock_info"](codes, field=["listed_date", "start_date"], C=_FakeCtx(tbl))
    assert new == ref, "返回对象未逐位一致（黄金对照失败）"


def test_b_filter_status_golden(cur, old, capsys):
    tbl = {"a.SZ": {"InstrumentName": "ST某某"}, "b.SZ": {"InstrumentName": "正常"},
           "c.SZ": {"InstrumentName": "正常", "InstrumentStatus": 1}}
    codes = ["a.SZ", "b.SZ", "c.SZ"]
    new = cur["_qs_filter_stock_by_status"](codes, ["ST", "HALT", "DELISTING"], None, _FakeCtx(tbl))
    capsys.readouterr()
    ref = old["_qs_filter_stock_by_status"](codes, ["ST", "HALT", "DELISTING"], None, _FakeCtx(tbl))
    assert new == ref, "filter_stock_by_status 返回未逐位一致"


def test_b_get_fundamentals_golden(cur, old, capsys):
    codes = ["000001.SZ", "600000.SH"]
    new = cur["_qs_get_fundamentals"]("valuation", codes, fields=["float_value"], C=_FakeCtx({}))
    capsys.readouterr()
    ref = old["_qs_get_fundamentals"]("valuation", codes, fields=["float_value"], C=_FakeCtx({}))
    assert new.equals(ref), "get_fundamentals 返回未逐位一致"
    assert list(new.index) == list(ref.index) and list(new.columns) == list(ref.columns)


def test_b_unknown_table_early_return(cur, capsys):
    """未入映射表 → 空 NaN 契约行（不受进度代码影响）。"""
    out = cur["_qs_get_fundamentals"]("not_a_table", ["000001.SZ"], fields=["x"], C=_FakeCtx({}))
    assert isinstance(out, pd.DataFrame) and len(out) == 1
    assert _lines(capsys) == [], "早返回路径不应打印任何进度行"


# ------------------------------------------------------------------ 判据 h

def test_h_elapsed_positive_when_t0_valid(cur, capsys):
    t0 = time.time() - 5.0
    cur["_qs_progress"]("f", 1000, 2000, t0)
    line = _lines(capsys)[0]
    val = float(re.search(r"elapsed=([\d.]+)s", line).group(1))
    assert val > 0.0, "t0 有效时 elapsed 必须 > 0（否则审计行关键字段静默失效）"


def test_h_elapsed_falls_back_to_zero(cur, capsys):
    cur["_qs_progress"]("f", 1000, 2000, None)
    assert "elapsed=0.0s" in _lines(capsys)[0]


def test_h_t0_zero_treated_as_invalid(cur, capsys):
    """t0=0.0（非 _qs_progress_start 返回值）→ elapsed 记 0.0；
    不得出现 epoch 天文数字（实施自测发现并加固的契约边界）。"""
    cur["_qs_progress"]("f", 1000, 2000, 0.0)
    val = float(re.search(r"elapsed=([\d.]+)s", _lines(capsys)[0]).group(1))
    assert val == 0.0


def test_h_progress_start_returns_numeric_or_none(cur):
    v = cur["_qs_progress_start"]()
    assert v is None or isinstance(v, float)


# ------------------------------------------------------------------ 判据 i

def test_i_no_module_level_time_import():
    """AST-only：模块顶层不得 import time（产物头部仅 import pandas）。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(a.name != "time" for a in node.names), "模块级 import time"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "time", "模块级 from time import"


def test_i_time_import_only_local_in_log_ext(cur_mod):
    """模板内 import time 仅出现于两个 helper 函数体（局部 import），
    且其余模板不得出现——不依赖产物头部 import 面。"""
    tree = ast.parse(cur_mod._QS_QMT_LOG_EXT)
    holders = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Import) and any(a.name == "time" for a in sub.names):
                holders.append(fn.name)
    assert set(holders) <= {"_qs_progress_start", "_qs_progress"}, holders
    assert "_qs_progress" in holders, "helper 内未见局部 import time"
    for tname in _TEMPLATES:
        if tname == "_QS_QMT_LOG_EXT":
            continue
        assert "import time" not in getattr(cur_mod, tname, ""), tname
