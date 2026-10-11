"""tests for quantstudio.strategy_compiler.source_import_qmt（M2b 块1/块2/块3）。

覆盖面（任务书 E 五组 + 1 项任务 C 交叉锚点）：
1. 正例：最小 PTrade 风格源码经 QmtSourceConverter.convert 后的产物形态断言
   （AST 合法 / 首行 #coding:gbk / init+handlebar / 三视图注入 / .SS→.SH 归一）；
2. 负例·编码：产物含 gbk 不可编码字符 → encode('gbk') 必须失败（fail-closed，
   不静默替换——写盘点 _write_qmt_product 的语义前提）；
3. 负例·未定义裸名：产物 AST 检查 context/g/data/log 引用必须在产物内有定义
   （防裸引用残留）；
4. 负例·DENY 剥除：set_benchmark/set_commission 调用消失 + QS_QMT_DENY_REMOVED
   审计行在位；
5. 负例·生命周期缺件：源无 handle_data（S3 形态）→ 不报错（不得误杀）；
6. （附加）任务 C 交叉锚点：正例产物过 validate_qmt_portability 全绿——
   QMT 白名单面与 source 注入面逐一对应的回归锚点。

运行（由验收方执行）：repo 根目录 `pytest tests/test_source_import_qmt.py`。
无外部数据依赖（不触 DB / 不写盘；QmtSourceConverter 直取源码文本，
strategy_path=None → 不解析 design metadata）。
"""

import ast
import re

import pytest

from quantstudio.strategy_compiler.source_import_qmt import (
    QmtSourceConverter,
    QS_QMT_PRODUCT_PHYSICAL_ENCODING,
)

# 最小 PTrade 风格源码：生命周期双件 + g. 赋值 + context.portfolio.total_value +
# log.info + get_history 调用 + .SS 后缀证券代码字面量（归一面）。
_POSITIVE_SOURCE = (
    "def initialize(ctx):\n"
    "    g.pool = ['600000.SS']\n"
    "    log.info('init')\n"
    "\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    hist = get_history('600000.SS', 5, '1d', ['close'])\n"
    "    total = context.portfolio.total_value\n"
    "    log.info('total=%s' % total)\n"
)


def _defined_names(tree: ast.AST) -> set:
    """产物内全部绑定名（与 source_import_qmt._collect_defined_names 同配方）。"""
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return defined


def _bare_called_names(tree: ast.AST) -> set:
    return {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


# ---------------------------------------------------------------------------
# 1. 正例
# ---------------------------------------------------------------------------

def test_positive_minimal_ptrade_source_converts():
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code

    # 产物语法合法（AST 可解析）
    ast.parse(code)

    # 首行 PEP 263 gbk 声明
    first_line = code.splitlines()[0]
    assert re.match(r"^#.*coding[:=][ \t]*gbk", first_line)
    assert first_line.strip() == "#coding:gbk"

    # QMT innerApi 生命周期在位
    assert "def init(" in code
    assert "def handlebar(" in code

    # 机制①视图注入：context / data / log
    assert "_qs_context_view" in code
    assert "_qs_data_view" in code
    assert "_qs_log_view" in code  # log 视图（模块级成品）

    # (d) 证券代码归一：.SS → .SH
    assert "600000.SH" in code
    assert "600000.SS" not in code

    # 无 gbk 外字符时产物可整体物理编码（写盘点 fail-closed 的对偶正例）
    assert QS_QMT_PRODUCT_PHYSICAL_ENCODING == "gbk"
    data = code.encode("gbk")
    assert data.startswith(b"#coding:gbk")


# ---------------------------------------------------------------------------
# 2. 负例·编码（fail-closed）
# ---------------------------------------------------------------------------

def test_negative_gbk_fail_closed():
    # 源含 gbk 不可编码字符（emoji 不在 GBK 码表内）→ 产物保留原字符
    src = _POSITIVE_SOURCE.replace("log.info('init')", "log.info('启动\U0001F600')")
    result = QmtSourceConverter(src).convert()
    assert result.errors == []
    assert "\U0001F600" in result.converted_code

    # fail-closed 语义：物理编码必须失败，不静默替换（写盘点据此 BLOCK）
    assert QS_QMT_PRODUCT_PHYSICAL_ENCODING == "gbk"
    with pytest.raises(UnicodeEncodeError):
        result.converted_code.encode("gbk")


# ---------------------------------------------------------------------------
# 3. 负例·未定义裸名（防裸引用残留）
# ---------------------------------------------------------------------------

def test_negative_no_undefined_bare_alias_refs():
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    tree = ast.parse(result.converted_code)
    defined = _defined_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ("context", "g", "data", "log"):
                assert node.id in defined, (
                    "bare reference %r not defined in product (line %s)"
                    % (node.id, getattr(node, "lineno", "?"))
                )


# ---------------------------------------------------------------------------
# 4. 负例·DENY 剥除
# ---------------------------------------------------------------------------

_DENY_SOURCE = (
    "def initialize(ctx):\n"
    "    set_benchmark('000300.SH')\n"
    "    set_commission(commission_ratio=0.0003)\n"
    "\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    log.info('tick')\n"
)


def test_negative_deny_stripped_with_audit_line():
    result = QmtSourceConverter(_DENY_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code

    # 审计行在位
    assert "QS_QMT_DENY_REMOVED: set_benchmark" in code
    assert "QS_QMT_DENY_REMOVED: set_commission" in code

    # 调用消失（AST 级：裸名调用集合不含 DENY 名——print 审计行内的名字是字符串
    # 常量，不构成调用）
    called = _bare_called_names(ast.parse(code))
    assert "set_benchmark" not in called
    assert "set_commission" not in called


# ---------------------------------------------------------------------------
# 5. 负例·生命周期缺件（S3 形态不误杀）
# ---------------------------------------------------------------------------

def test_negative_lifecycle_missing_handle_data_not_fatal():
    src = (
        "def initialize(ctx):\n"
        "    g.ready = True\n"
        "    log.info('ready')\n"
    )
    result = QmtSourceConverter(src).convert()
    assert result.errors == []  # S3 缺件不报错（不得误杀）
    code = result.converted_code
    ast.parse(code)
    assert "def init(" in code


# ---------------------------------------------------------------------------
# 6.（附加）任务 C 交叉锚点：正例产物过 QMT 白名单面校验
# ---------------------------------------------------------------------------

def test_converted_product_passes_qmt_portability_whitelist():
    from quantstudio.strategy_compiler.portability_rules import validate_qmt_portability
    result = QmtSourceConverter(_POSITIVE_SOURCE).convert()
    assert result.errors == []
    ok, violations, _warnings = validate_qmt_portability(result.converted_code)
    assert ok, "QMT whitelist must cover the source-path injected surface: %r" % (
        [str(v) for v in violations],)


# ---------------------------------------------------------------------------
# 7. 负例·分钟域显式 deny（M3 前置；fail-closed 硬门）
#    依据：M1 §2.2 基准声明「minute-bar-v1 不支持、portability 显式 deny」
#    + render_qmt.py:57-61（spec 路径同款 deny）
# ---------------------------------------------------------------------------

_MINUTE_SOURCE = (
    "def initialize(ctx):\n"
    "    g.ready = True\n"
    "\n"
    "def handle_data(ctx, data):\n"
    "    h = get_history('600000.SS', 5, unit='5m', fields=['close'])\n"
)


def test_minute_deny_reason_profile_face():
    from quantstudio.strategy_compiler.source_import_qmt import _qmt_minute_deny_reason
    assert _qmt_minute_deny_reason("def initialize(ctx):\n    pass\n",
                                   "minute-bar-v1") is not None
    assert _qmt_minute_deny_reason("def initialize(ctx):\n    pass\n",
                                   "daily-bar-v1") is None


def test_minute_deny_reason_source_face():
    from quantstudio.strategy_compiler.source_import_qmt import _qmt_minute_deny_reason
    reason = _qmt_minute_deny_reason(_MINUTE_SOURCE, None)
    assert reason is not None and "5m" in reason
    # 日线 unit 不误杀
    assert _qmt_minute_deny_reason(
        "def handle_data(ctx, data):\n"
        "    h = get_history('600000.SS', 5, unit='1d', fields=['close'])\n",
        None) is None


def test_minute_source_convert_source_qmt_blocked(tmp_path):
    """端到端：分钟源经 convert_source_qmt → fail-closed BLOCK，不产出产物。"""
    from quantstudio.strategy_compiler.source_import_qmt import convert_source_qmt
    p = tmp_path / "minute_demo_quantstudio.py"
    p.write_text(_MINUTE_SOURCE, encoding="utf-8")
    result = convert_source_qmt(p, verbose=False)
    assert result.converted_code == ""
    assert any("QMT-MINUTE-DENY" in e for e in result.errors), result.errors


def test_daily_source_not_minute_denied(tmp_path):
    """反例：日线源不触发 deny（防误杀）。"""
    from quantstudio.strategy_compiler.source_import_qmt import convert_source_qmt
    p = tmp_path / "daily_demo_quantstudio.py"
    p.write_text(_POSITIVE_SOURCE, encoding="utf-8")
    result = convert_source_qmt(p, verbose=False)
    assert not any("QMT-MINUTE-DENY" in e for e in result.errors), result.errors
    assert result.converted_code != ""


# ---------------------------------------------------------------------------
# 8. F-1 修复（M3.1 双轨）：产物内 _qs_fin_to_rows 形态解析
#    修复面在**产物**内（EXT 注入模板），故测试从真实产物命名空间取函数调用
#    （而非测转换器模块）——保证测的是产物实际运行的那份实现。
# ---------------------------------------------------------------------------

_PRODUCT_NS: dict = {}

_CODE_MAJOR = {"600000.SH": {"s_fa_eps_basic": 1.23},
               "000009.SZ": {"s_fa_eps_basic": 4.56}}
_FIELD_MAJOR = {"s_fa_eps_basic": {"600000.SH": 1.23, "000009.SZ": 4.56}}


def _product_ns() -> dict:
    """转换最小源并 exec 产物，返回其模块命名空间（真实产物面）。"""
    if not _PRODUCT_NS:
        code = QmtSourceConverter(_POSITIVE_SOURCE).convert().converted_code
        exec(compile(code, "<qmt_product>", "exec"), _PRODUCT_NS)
    return _PRODUCT_NS


def test_fin_to_rows_explicit_code_major():
    """轨 A：显式声明 code_major → 零猜测正确解析。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f(_CODE_MAJOR, shape="code_major") == _CODE_MAJOR


def test_fin_to_rows_explicit_field_major():
    """轨 A：显式声明 field_major → 零猜测正确解析。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f(_FIELD_MAJOR, shape="field_major") == _CODE_MAJOR


def test_fin_to_rows_auto_detect_code_major():
    """轨 B：外层键为证券代码 → 自动识别为 code_major。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f(_CODE_MAJOR) == _CODE_MAJOR


def test_fin_to_rows_auto_detect_field_major():
    """轨 B：内层键为证券代码 → 自动识别为 field_major。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f(_FIELD_MAJOR) == _CODE_MAJOR


def test_fin_to_rows_undetermined_returns_empty(capsys):
    """两层均不命中代码形态 → 保守返回 {} + 审计行（显式缺数优于静默错值）。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f({"foo": {"bar": 1}}) == {}
    assert "QS_QMT_FIN_SHAPE_UNDETERMINED" in capsys.readouterr().out


def test_fin_to_rows_ambiguous_both_layers_code(capsys):
    """②审补充裁定：两层**同时**命中代码形态（歧义）→ 亦判 UNDETERMINED。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f({"600000.SH": {"000009.SZ": 1.0}}) == {}
    assert "QS_QMT_FIN_SHAPE_UNDETERMINED" in capsys.readouterr().out


def test_fin_to_rows_dataframe_path_unchanged():
    """to_dict('index') 路径**不变**（07-行情函数.md:1889 投影）。"""
    import pandas as pd
    f = _product_ns()["_qs_fin_to_rows"]
    df = pd.DataFrame({"s_fa_eps_basic": [1.23]}, index=["600000.SH"])
    assert f(df) == {"600000.SH": {"s_fa_eps_basic": 1.23}}


def test_fin_to_rows_none_and_nondict_safe():
    """None / 非 dict → 空（既有防御不变）。"""
    f = _product_ns()["_qs_fin_to_rows"]
    assert f(None) == {}
    assert f([1, 2, 3]) == {}


# ---- M5.1a：D-10 current_dt/previous_date timetag 推导 + D-3 单实参（2026-10-10）----
# 依据 docs/qmt-m5.1a-fix-design.md v1.1（ZCode 审计 sess_a1c58d74 通过后实施）。


class _StubC:
    """QMT ContextInfo 最小桩：仅 get_bar_timetag / barpos（M5-13 实测口径）。

    注意：刻意**不**提供 bar_date / pre_bar_date 属性——真实 QMT 实测不存在
    （D-10 根因；qmt_stub.py:618 自造 bar_date 是保真度分叉，见审计 R1）。
    """

    MS_TAGS = [1725292800000, 1725379200000, 1725465600000]  # 2024-09-03/04/05 北京午夜

    def __init__(self, barpos):
        self.barpos = barpos

    def get_bar_timetag(self, bp):
        return self.MS_TAGS[bp]


def _assert_cn_tz():
    """时区前提校验（审计 R5）：timetag→日期断言仅在 UTC+8 机器成立，防跨机漂移。"""
    import datetime as dtm
    off = dtm.datetime.now().astimezone().utcoffset()
    if off != dtm.timedelta(hours=8):
        pytest.fail(
            "机器时区非 UTC+8（utcoffset=%s）：timetag→日期断言仅在北京时间前提成立"
            "（QMT 国内交易端前提，docs/qmt-m5.1a-fix-design.md §2.1）" % off
        )


def test_context_current_dt_from_timetag():
    """D-10：current_dt 由 get_bar_timetag(barpos) 推导（bar_date 实测不存在）。"""
    _assert_cn_tz()
    import datetime as dtm
    view = _product_ns()["_QmtContextView"](_StubC(0))
    assert view.current_dt == dtm.datetime(2024, 9, 3)
    view1 = _product_ns()["_QmtContextView"](_StubC(1))
    assert view1.current_dt == dtm.datetime(2024, 9, 4)


def test_context_previous_date_and_first_bar_none():
    """D-10：previous_date 用 barpos-1 timetag；首 bar（bp<=0）→ None（QMT 侧边界语义差）。"""
    _assert_cn_tz()
    import datetime as dtm
    view = _product_ns()["_QmtContextView"](_StubC(1))
    assert view.previous_date == dtm.datetime(2024, 9, 3)
    view0 = _product_ns()["_QmtContextView"](_StubC(0))
    assert view0.previous_date is None


def test_product_renders_single_arg_instrument_detail():
    """D-3：产物渲染的 get_instrument_detail 必须严格单位置实参（M5-8 实测签名）。

    stub 保真度分叉下的回归闸：qmt_stub 仍按文档口径实现 iscomplete 形参，
    桩冒烟面检不出 3 参形态——以产物源码正则断言堵住（M5.1a 审计 R1 处置）。
    """
    code = QmtSourceConverter(_POSITIVE_SOURCE).convert().converted_code
    bad = re.findall(r"get_instrument_detail\(\s*\w+\s*,", code)
    assert not bad, "产物含多实参 get_instrument_detail 调用: %r" % bad


# ---- M5.1b：D-1 窗口化 / D-9 Panel 轨回归闸（2026-10-10） ----------------------


class _MktStubC(_StubC):
    """get_market_data_ex 最小桩（D-1 窗口口径）：记录调用 kwargs、返回可控数据。

    刻意只实现窗口形态（start_time/end_time）——真实 QMT count 形态=全局最新
    N 根（M5-2 实测未来函数），wrapper 修复后不应再发 count 请求。
    """

    def __init__(self, barpos, data):
        _StubC.__init__(self, barpos)
        self.data = data
        self.calls = []

    def get_market_data_ex(self, fields, codes, period='1d', **kw):
        self.calls.append(dict(kw, period=period, codes=list(codes)))
        return dict(self.data)


def _mk_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def test_fetch_bars_uses_window_not_count():
    """D-1：_qs_fetch_bars 必须走 start_time/end_time 窗口（barpos 锚定），
    不得再发 count 形态（全局最新 N 根 = 回测未来函数，探针 23:02 实锤）。"""
    _assert_cn_tz()
    ns = _product_ns()
    df = _mk_df([{'close': 10.0}, {'close': 11.0}, {'close': 12.0}])
    df.index = ['20240902', '20240903', '20240904']
    C = _MktStubC(2, {'510300.SH': df})  # bar=2024-09-05
    out = ns['_qs_fetch_bars'](C, ['510300.SH'], ['close'], 'pre', 2, '1d')
    assert C.calls, 'wrapper 未发起 get_market_data_ex 调用'
    kw = C.calls[0]
    assert 'count' not in kw, 'count 形态复现（D-1 回退）: %r' % kw
    assert kw.get('end_time') == '20240904', 'end 未锚定 bar 前一自然日: %r' % kw
    assert 'start_time' in kw and kw['start_time'] < kw['end_time']
    got = out['510300.SH']
    assert len(got) == 2 and list(got['close']) == [11.0, 12.0]  # tail(2)


def test_fetch_bars_honest_when_short():
    """审计要点①：窗口实际取得数 < count 时如实返回（停牌/长假/上市晚），不凑数。"""
    _assert_cn_tz()
    ns = _product_ns()
    df = _mk_df([{'close': 1.0}, {'close': 2.0}, {'close': 3.0}])
    df.index = ['20240902', '20240903', '20240904']
    C = _MktStubC(2, {'000001.SZ': df})
    out = ns['_qs_fetch_bars'](C, ['000001.SZ'], ['close'], 'pre', 220, '1d')
    assert len(out['000001.SZ']) == 3, '不足 want 根时必须如实返回 3 根，不凑数'


def test_fetch_bars_e1_defense_drops_bar_day():
    """E1 防御：QMT 端点语义未定谳——末行日期 >= bar 当日时剔末根（双保险）。"""
    _assert_cn_tz()
    ns = _product_ns()
    df = _mk_df([{'close': 10.0}, {'close': 99.0}])
    df.index = ['20240904', '20240905']  # 末行 == bar 当日（bar=2024-09-05）
    C = _MktStubC(2, {'510300.SH': df})
    out = ns['_qs_fetch_bars'](C, ['510300.SH'], ['close'], 'pre', 5, '1d')
    got = out['510300.SH']
    assert list(got['close']) == [10.0], 'E1 防御未剔 bar 当日行: %r' % list(got['close'])


class _FakePanel:
    """伪 pandas.Panel（duck-typing：items/major_axis/__getitem__）——
    D-9 实测多代码形态（QMT 内置旧版 pandas 有 Panel，本仓新版无）。"""

    def __init__(self, frames):
        self._frames = frames
        self.items = list(frames.keys())
        self.major_axis = None

    def __getitem__(self, code):
        return self._frames[code]


def test_fin_to_rows_panel_branch():
    """D-9：多代码 Panel 形态 → {code: 末行字段映射}（as-of 最新宣告近似）。"""
    import pandas as pd
    ns = _product_ns()
    df = pd.DataFrame({'eps': [0.10, 0.20], 'publ_date': [1711843200000.0, 1719786240000.0]},
                      index=['20240331', '20240630'])
    panel = _FakePanel({'000001.SZ': df})
    rows = ns['_qs_fin_to_rows'](panel)
    assert set(rows.keys()) == {'000001.SZ'}
    assert rows['000001.SZ']['eps'] == 0.20  # 末行
    assert rows['000001.SZ']['publ_date'] == 1719786240000.0


def test_fin_to_rows_single_code_dataframe():
    """D-9：单代码 DataFrame 形态（index=date_str）→ {codes[0]: 末行字段映射}。"""
    import pandas as pd
    ns = _product_ns()
    df = pd.DataFrame({'eps': [0.10, 0.20]}, index=['20240331', '20240630'])
    rows = ns['_qs_fin_to_rows'](df, codes=['000001.SZ'])
    assert rows == {'000001.SZ': {'eps': 0.20}}


def test_product_renders_window_fetch_and_snap_kwargs():
    """D-1 产物回归闸：窗口形态与快照锚定 helper 必须渲染进产物。"""
    code = QmtSourceConverter(_POSITIVE_SOURCE).convert().converted_code
    assert 'start_time=start_dt.strftime' in code, '产物缺 D-1 窗口取数渲染'
    assert 'def _qs_snap_kwargs' in code, '产物缺快照锚定 helper'
    assert 'count=fetch_n' not in code, '产物残留 count 形态（D-1 回退）'


# ---- D-13：ExpireDate int YYYYMMDD 形态定谳（2026-10-11 PROBE 实测） ------------
# 依据：docs/evidence/qmt-m5-batch2-probe-20261010.md §8.2（新建策略文件8 探针：
# 4/4 详查 ExpireDate=99999999(int)；OpenDate=19910403 同形态佐证；census 100 只
# IsTrading 全 None、InstrumentStatus 0×99+20×1）。


class _DetailStubC(_StubC):
    """get_instrument_detail 最小桩（D-13 实测口径）：ExpireDate 为 int
    YYYYMMDD（99999999=永续标记）；IsTrading 实测恒 None（非 False）；
    InstrumentStatus 0=正常 / 20=停牌（100 只普查 0×99+20×1）。"""

    def __init__(self, details):
        _StubC.__init__(self, 0)
        self.details = details

    def get_instrument_detail(self, code):
        return self.details.get(code)


def _detail(name='平安银行', status=0, expire=99999999, is_trading=None):
    return {'InstrumentName': name, 'InstrumentStatus': status,
            'ExpireDate': expire, 'IsTrading': is_trading}


def test_status_flags_perpetual_expire_not_delisted():
    """D-13 核心：ExpireDate=99999999（永续标记）不得判退市。

    旧代码按毫秒 /1000 → fromtimestamp(99999.999)=1970-01-02 →
    now()>1970 恒真 → 5227 只全部误剔（01:48 复测 F=0）。"""
    f = _product_ns()["_qs_status_flags"]
    flags, ok = f('000001.SZ', _DetailStubC({'000001.SZ': _detail()}))
    assert ok is True
    assert flags['DELISTING'] is False
    assert flags['ST'] is False
    assert flags['HALT'] is False


def test_status_flags_expired_yyyymmdd_is_delisted():
    """D-13：真实过期股（YYYYMMDD 已过）→ DELISTING=True（修复不误放）。"""
    f = _product_ns()["_qs_status_flags"]
    flags, ok = f('000001.SZ', _DetailStubC({'000001.SZ': _detail(expire=20200101)}))
    assert ok is True
    assert flags['DELISTING'] is True


def test_status_flags_ms_timestamp_defense_branch():
    """D-13：>=1e10 数值仍按毫秒时间戳防御解析（实测未现形态，防御保留）。"""
    import datetime as dtm
    f = _product_ns()["_qs_status_flags"]
    past_ms = int(dtm.datetime(2020, 1, 1).timestamp() * 1000)
    flags, ok = f('000001.SZ', _DetailStubC({'000001.SZ': _detail(expire=past_ms)}))
    assert ok is True
    assert flags['DELISTING'] is True


def test_status_flags_st_and_halt_paths_unchanged():
    """D-13 回归锚：ST/HALT 判定路径零改动（数据修正型影响面=DELISTING 行集）。"""
    f = _product_ns()["_qs_status_flags"]
    stub = _DetailStubC({
        '000001.SZ': _detail(name='ST平安'),   # ST 股（census 5/100）
        '600000.SH': _detail(status=20),        # 停牌（census 20×1）
    })
    f1, ok1 = f('000001.SZ', stub)
    assert (f1['ST'], f1['HALT'], f1['DELISTING']) == (True, False, False)
    f2, ok2 = f('600000.SH', stub)
    assert (f2['ST'], f2['HALT'], f2['DELISTING']) == (False, True, False)
    assert ok1 and ok2


def test_filter_stock_by_status_survives_perpetual_expire(capsys):
    """D-13 端到端：永续标记股经 filter_stock_by_status 保留（F 漏斗修复闭环）。"""
    stub = _DetailStubC({
        '000001.SZ': _detail(),                  # 正常股 → 保留
        '600000.SH': _detail(name='ST浦发'),     # ST 股 → 剔除
    })
    f = _product_ns()["_qs_filter_stock_by_status"]
    out = f(['000001.SZ', '600000.SH'], filter_type=['ST', 'HALT', 'DELISTING'],
            query_date=None, C=stub)
    assert out == ['000001.SZ']
    assert 'UNDETERMINED' not in capsys.readouterr().out


# ---- D-16a：QMT volume 单位=手 → wrapper 口径层 ×100 股数换算（2026-10-11） ------
# 依据：docs/qmt/inner-api/04-数据结构.md:24「volume | int | 成交总量（手）」
# （pvolume=原始股数但文档标注不推荐）；D-15/D-16 探针实证（平安银行 amt20
# 1123万 vs 真实~15亿=差整 100 倍；修正后过线率 23/500→491/500）。
# 本地/PTrade 语义 volume=股——成交额类计算 close*volume 在 QMT 直读下低估
# 100 倍，流动性门槛（3000万）实际要求 30亿等价 → q≈0 全灭（02:38 复测）。


def _mkt_vol_df(volume=(1438766, 1852994, 1091755)):
    """平安银行形态窗口 df：3 行手单位 volume（D-15 探针 tail3 原值），
    五列全（_QmtDataBar._FIELDS 快照路径需 open/low 在列）。"""
    import pandas as pd
    return pd.DataFrame({'open': [10.2, 10.3, 10.2],
                         'high': [10.381, 10.371, 10.421],
                         'low': [10.1, 10.2, 10.1],
                         'close': [10.231, 10.351, 10.221],
                         'volume': list(volume)},
                        index=['20240828', '20240829', '20240830'])


def test_fetch_bars_volume_converted_to_shares():
    """D-16a 核心：历史层输出 volume=手×100=股（本地语义对齐）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    f = _product_ns()["_qs_fetch_bars"]
    out = f(C, ['000001.SZ'], ['close', 'high', 'volume'], 'pre', 5, '1d')
    df = out['000001.SZ']
    assert list(df['volume']) == [143876600, 185299400, 109175500]


def test_fetch_bars_close_high_unchanged_regression():
    """D-16a 回归锚：换算只触 volume，close/high 数值零改动（数据修正型影响面）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    f = _product_ns()["_qs_fetch_bars"]
    out = f(C, ['000001.SZ'], ['close', 'high', 'volume'], 'pre', 5, '1d')
    df = out['000001.SZ']
    assert list(df['close']) == [10.231, 10.351, 10.221]
    assert list(df['high']) == [10.381, 10.371, 10.421]


def test_fetch_bars_volume_not_requested_untouched():
    """D-16a 边界：fields 不含 volume → df 原样返回（不误伤纯价格请求）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    f = _product_ns()["_qs_fetch_bars"]
    out = f(C, ['000001.SZ'], ['close'], 'pre', 5, '1d')
    df = out['000001.SZ']
    assert list(df['volume']) == [1438766, 1852994, 1091755]  # 列在但不换算


def test_snap_bar_volume_converted():
    """D-16a 快照层：_QmtDataBar.volume = 手×100=股（与历史层一致口径）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    bar = _product_ns()["_QmtDataBar"](C, '000001.SZ')
    assert bar.volume == 109175500.0
    assert bar.close == 10.221


# ---- D-17：get_history 纯关键字形态 field= 路由（2026-10-11 03:12 复测定谳） ------
# 根因：count=/frequency=/field=/security_list= 全关键字调用不进 int 重映射
# 分支——field 丢弃、fields=None → wrapper 默认 ['close'] → high/volume 未
# 请求 → 门槛 len<260 全灭（q=0；H=4414 因 close 列在而自洽）。探针直调
# get_market_data_ex 显式传字段，测不出本洞。


def test_get_history_kw_field_alias_routed():
    """D-17 核心：纯关键字 field= 必须路由到 wrapper fields（volume 列在且
    ×100 换算生效 = 字段穿透 + 单位换算全链打通）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    ns = _product_ns()
    out = ns['get_history'](count=5, frequency='1d',
                            field=['close', 'high', 'volume'],
                            security_list=['000001.SZ'], fq='pre',
                            include=False, is_dict=True, C=C)
    df = out['000001.SZ']
    assert 'volume' in df.columns, 'field= 关键字被丢弃（D-17 形态复现）'
    assert list(df['volume']) == [143876600, 185299400, 109175500]  # ×100 到位


def test_get_history_positional_signature_a_unchanged():
    """D-17 回归锚：签名 A 位置形态（security, count, unit, fields）行为不变。

    单标的 + is_dict 默认 False → 返回裸 df（既有契约 :1018-1019）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    ns = _product_ns()
    df = ns['get_history'](['000001.SZ'], 5, '1d', ['close', 'volume'], C=C)
    assert list(df['volume']) == [143876600, 185299400, 109175500]


def test_get_history_countfirst_positional_unchanged():
    """D-17 回归锚：签名 B count-first 位置形态（int 首参重映射）行为不变。

    单标的 + is_dict 默认 False → 返回裸 df（既有契约 :1018-1019）。"""
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})
    ns = _product_ns()
    df = ns['get_history'](5, '1d', ['close', 'volume'], ['000001.SZ'],
                           fq='pre', C=C)
    assert list(df['volume']) == [143876600, 185299400, 109175500]


# ---- D-19：fundamentals 毫秒直通 + 二层多行历史契约（2026-10-11 定谳） ----------
# 一层（03:24）：wrapper _qs_fin_ms_to_date 把毫秒过度归一成 'YYYYMMDD' 字符串——
# 本地契约为 epoch 毫秒数值（CANSLIM:389 to_datetime(unit='ms') / weekly:154 /
# 周频三层:150 astype(float)）→ 毫秒直通修复。
# 二层（16:05 复测）：本地 ptrade_api.py:1022 eps/growth_ability/profit_ability 走
# get_financial 返回 PIT 窗口**全报告期多行**（index=code 重复）且只回完整行；
# wrapper 单行使 CANSLIM EPS 因子静默退化纯价量，NaN 行令策略 astype('Int64')
# 即 int('NaT') 崩 → 全历史行收集 + 完整行过滤。


class _FinStubC(_StubC):
    """get_financial_data 最小桩：code_major 形态 {code: {field: value}}，
    m_anntime/m_timetag 为毫秒数值（07:1837 实测口径）。"""

    def __init__(self, fin):
        _StubC.__init__(self, 0)
        self.fin = fin

    def get_financial_data(self, fields, codes, start, end, report_type=None):
        out = {}
        for c in codes:
            src = self.fin.get(c, {})
            out[c] = {f.split('.')[-1]: src[f.split('.')[-1]] for f in fields
                      if f.split('.')[-1] in src}
        return out


class _FinRawStubC(_StubC):
    """get_financial_data 直返预置原始对象（D-9 实测 Panel/DataFrame 形态桩）。"""

    def __init__(self, raw):
        _StubC.__init__(self, 0)
        self.raw = raw

    def get_financial_data(self, fields, codes, start, end, report_type=None):
        return self.raw


def test_fundamentals_publ_end_date_ms_passthrough():
    """D-19 一层核心：publ_date/end_date 原始毫秒直通（不再转 'YYYYMMDD' 字符串）。"""
    C = _FinStubC({'000001.SZ': {
        's_fa_eps_basic': 1.55,
        'm_anntime': 1719786240000.0,
        'm_timetag': 1717113600000.0}})
    f = _product_ns()["_qs_get_fundamentals"]
    frame = f('eps', ['000001.SZ'], date=None,
              fields=['eps', 'publ_date', 'end_date'], C=C)
    assert frame.loc['000001.SZ', 'publ_date'] == 1719786240000.0
    assert frame.loc['000001.SZ', 'end_date'] == 1717113600000.0
    assert not isinstance(frame.loc['000001.SZ', 'publ_date'], str), \
        '毫秒被过度归一成字符串（D-19 形态复现）'
    assert frame.loc['000001.SZ', 'eps'] == 1.55


def test_fundamentals_missing_ms_row_dropped():
    """D-19 二层边界：财报缺失（无 m_timetag/m_anntime）→ 整行剔除（完整行契约），
    frame 空 → 策略 .empty 走纯价量 fallback，不产 'NaT' 字符串链。"""
    C = _FinStubC({'000001.SZ': {'s_fa_eps_basic': 1.55}})
    f = _product_ns()["_qs_get_fundamentals"]
    frame = f('eps', ['000001.SZ'], date=None,
              fields=['eps', 'publ_date', 'end_date'], C=C)
    assert frame.empty, '不完整行必须被剔除（D-19 二层完整行契约）'
    assert list(frame.columns) == ['eps', 'publ_date', 'end_date']


def test_fundamentals_hist_multirow_panel():
    """D-19 二层核心：多代码 Panel 全历史行 → 多行 frame（index=code 重复）——
    CANSLIM:406 groupby('code') 拆单季算 C/A 的契约形态。"""
    import pandas as pd
    df = pd.DataFrame({
        's_fa_eps_basic': [0.10, 0.20, 0.35],
        'm_anntime': [1711843200000.0, 1719786240000.0, 1722464640000.0],
        'm_timetag': [1711843200000.0, 1717113600000.0, 1722464640000.0]},
        index=['20240331', '20240630', '20240930'])
    C = _FinRawStubC(_FakePanel({'000001.SZ': df}))
    f = _product_ns()["_qs_get_fundamentals"]
    frame = f('eps', ['000001.SZ'], date=None,
              fields=['eps', 'publ_date', 'end_date'], C=C)
    assert len(frame) == 3, '全历史 3 行必须保留（单行形态 = D-19 二层复现）'
    assert list(frame.index) == ['000001.SZ'] * 3
    assert list(frame['eps']) == [0.10, 0.20, 0.35]
    assert frame['end_date'].tolist()[0] == 1711843200000.0


def test_fundamentals_hist_nan_row_filtered():
    """D-19 二层：混入 NaN 行（m_anntime/m_timetag 缺）→ 仅完整行保留。"""
    import pandas as pd
    import numpy as np
    df = pd.DataFrame({
        's_fa_eps_basic': [0.10, 0.20],
        'm_anntime': [1711843200000.0, np.nan],
        'm_timetag': [1711843200000.0, np.nan]},
        index=['20240331', '20240630'])
    C = _FinRawStubC(_FakePanel({'000001.SZ': df}))
    f = _product_ns()["_qs_get_fundamentals"]
    frame = f('eps', ['000001.SZ'], date=None,
              fields=['eps', 'publ_date', 'end_date'], C=C)
    assert len(frame) == 1, 'NaN 行必须被剔除（完整行契约）'
    assert frame['eps'].tolist() == [0.10]


def test_fundamentals_valuation_single_row_unchanged():
    """D-19 二层回归锚：valuation 表单行 + NaN 契约行不变（快照语义零漂移）。"""
    import pandas as pd
    C = _FinStubC({'000001.SZ': {'circulating_capital': 1.9e10}})
    f = _product_ns()["_qs_get_fundamentals"]
    frame = f('valuation', ['000001.SZ'], date=None,
              fields=['float_value'], C=C)
    assert list(frame.index) == ['000001.SZ'], 'valuation 不得改多行形态'
    assert pd.isna(frame.loc['000001.SZ', 'float_value']), \
        '无 close 价语境 float_value NaN 契约行保留'


# ---- D-18：回测资金源/交易转发（2026-10-11 探针 v1/v2/v3 定谳） --------------------
# 实测：回测模式 C.accountID 不存在（v1）、模块级 account 变量 NOGLOBAL（v2）、
# C.capital=1000000.0 存在（v1）、原生交易函数族以模块 globals 预注入（v3）。
# 现金字段名实测修正：ACCOUNT 明细为 m_dAvailable（08:655），m_dCash 不存在。


class _AccountRow:
    def __init__(self, available):
        self.m_dAvailable = available


class _GtdStubC(_MktStubC):
    """带 accountID（实盘语境）+ 可注入 get_trade_detail_data 结果的桩。"""

    def __init__(self, data, account_id, acc_rows=None):
        _MktStubC.__init__(self, 0, data)
        self.accountID = account_id
        self._acc_rows = acc_rows

    def get_trade_detail_data(self, acc, acct_type, biz):
        return self._acc_rows if biz == 'ACCOUNT' else []


def test_order_target_value_backtest_forwards_native():
    """D-18 核心：回测语境（无 accountID）转发 QMT 原生 order_target_value，
    不走 passorder 链（根治每 bar 重复建仓 + closeable=0 卖单全灭）。"""
    ns = _product_ns()
    calls = {'native': [], 'passorder': []}

    def fake_native(code, value, C):
        calls['native'].append((code, value))

    ns['_qs_native_otv'] = fake_native

    def cap_passorder(op, code, amount, C=None, **kw):
        calls['passorder'].append((op, code, amount))

    ns['_qs_passorder'] = cap_passorder
    C = _MktStubC(0, {'000001.SZ': _mkt_vol_df()})  # 无 accountID = 回测语境
    ns['order_target_value']('000001.SZ', 190000.0, C=C)
    assert calls['native'] == [('000001.SZ', 190000.0)]
    assert calls['passorder'] == []


def test_order_target_value_live_keeps_passorder_chain():
    """D-18 回归锚：实盘语境（accountID 存在）保持现 passorder 链不转发。"""
    ns = _product_ns()
    calls = {'native': [], 'passorder': []}

    def fake_native(code, value, C):
        calls['native'].append((code, value))

    ns['_qs_native_otv'] = fake_native

    def cap_passorder(op, code, amount, C=None, **kw):
        calls['passorder'].append((op, code, amount))

    ns['_qs_passorder'] = cap_passorder
    C = _GtdStubC({'000001.SZ': _mkt_vol_df()}, '888001')  # 实盘语境
    ns['order_target_value']('000001.SZ', 190000.0, C=C)
    assert calls['native'] == []
    assert len(calls['passorder']) == 1  # 空仓 → 买入分支


def test_portfolio_cash_backtest_capital_fallback():
    """D-18：回测语境 cash 回退 C.capital 推导（capital - 持仓市值，空仓=全额）。"""
    ns = _product_ns()
    C = _MktStubC(0, {})
    C.capital = 2000000.0
    pv = ns['_QmtPortfolioView'](C)
    assert pv.cash == 2000000.0


def test_portfolio_cash_live_uses_m_davailable():
    """D-18：实盘链字段名修正回归——ACCOUNT 取 m_dAvailable（非 m_dCash）。"""
    ns = _product_ns()
    C = _GtdStubC({'000001.SZ': _mkt_vol_df()}, '888001',
                  acc_rows=[_AccountRow(available=123456.5)])
    ns['get_trade_detail_data'] = C.get_trade_detail_data
    pv = ns['_QmtPortfolioView'](C)
    assert pv.cash == 123456.5


def test_trade_detail_account_resolution_chain():
    """D-18：账号解析链 C.accountID → 模块级 account 变量 → ''（08:623）。"""
    ns = _product_ns()
    seen = []

    def fake_gtd(acc, acct_type, biz):
        seen.append(acc)
        return []

    ns['get_trade_detail_data'] = fake_gtd
    C = _MktStubC(0, {})  # 无 accountID → 回退模块级 account
    ns.setdefault('account', None)
    ns['account'] = '123ABC'
    ns['_qs_get_trade_detail'](C, 'POSITION')
    assert seen == ['123ABC']
    ns['account'] = None
    ns['_qs_get_trade_detail'](C, 'POSITION')
    assert seen[-1] == ''
