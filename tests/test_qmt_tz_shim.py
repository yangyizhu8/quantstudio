"""tests for QMT wrapper 时区解析垫片 _qs_tz_shim（QD-2026-0022，2026-11-05）。

设计：docs/qmt-iface-defect-triage-and-tz-shim-design-20261105.md。
根因：QMT 内置 Python 缺时区数据 => pandas 解析 'Asia/Shanghai' 抛
pytz.UnknownTimeZoneError（KeyError 子类）=> 策略 _compute_eps_factors 内
.dt.tz_convert 抛错被宽 try 吞 => EPS 因子空表 => 选股偏离。垫片落在
_QS_QMT_MODULE_HEADER（策略源码零改动）。

测试模式（与 tests/test_qmt_instrument_snapshot.py 同款）：QmtSourceConverter
渲染产物（内嵌 MODULE_HEADER，含模块级 _qs_tz_shim() 调用）-> exec 进独立
命名空间 -> capsys 捕获探测行断言。每用例独立 exec（_QS_QMT_TZ_DONE 模块级
缓存隔离）。

用例（任务包五例）：
  T1 探测行格式：QS_QMT_TZ_PROBE / QS_QMT_TZ_SHIM failed 二者之一，
     含 pytz= zoneinfo= resolve= tzcache= 四字段
  T2 幂等：_QS_QMT_TZ_DONE 生效，二次调用零打印
  T3 真实走 tz_convert（非仅 patch 存在性）：force=True 后
     2026-06-01T01:30:00+00:00 -> 2026-06-01 09:30:00+08:00
  T4 等价性：2026-06-01..2026-09-30 每 5 日抽样 25 个 UTC 时刻（>=20），
     tz_convert('Asia/Shanghai') 与固定 +08:00 手工换算逐位一致
  T5 fail-loud：pytz/zoneinfo 导入失败 + pd.Timestamp 抛错 => 三层全失败，
     打印 QS_QMT_TZ_SHIM failed 且不抛异常（后续策略异常可见性由既有
     except 承担，垫片自身不得 raise）
"""

import sys
from datetime import datetime, timedelta, timezone as _dt_timezone

import pandas as pd

from quantstudio.strategy_compiler.source_import_qmt import QmtSourceConverter

# 与 tests/test_qmt_instrument_snapshot.py 最小正例同源：生命周期双件 +
# get_history 调用（渲染产物内嵌含垫片的完整 MODULE_HEADER）。
_MIN_SOURCE = (
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

# 探测行必备四字段（机检 grep 契约）
_TZ_FIELDS = ('pytz=', 'zoneinfo=', 'resolve=', 'tzcache=')


def _render_ns():
    """渲染产物 + exec 进独立命名空间（垫片随头部在模块级执行一次）。"""
    result = QmtSourceConverter(_MIN_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code
    # 产物须整体物理 GBK 可编码（含时区垫片段——写盘点前提）
    code.encode("gbk")
    ns = {}
    exec(compile(code, "<qmt-product>", "exec"), ns)
    return ns


def test_t1_probe_line_format(capsys):
    """T1：正常环境保持既有零输出契约；force=True 触发垫片时才打印探测行。

    契约依据（R318 实施轮修正）：wrapper 既有「absent 态零输出」被多处测试断言，
    故探测行改为**仅当需要垫片（初始 resolve != 'Y'）时**输出——正常环境静默，
    缺时区环境才打印 PROBE / SHIM failed（真机诊断信息不丢）。
    """
    ns = _render_ns()
    assert callable(ns.get('_qs_tz_shim'))
    out_normal = capsys.readouterr().out
    # 本机 pytz 完整 => 走原路径 => 必须零输出（守住既有契约）
    assert 'QS_QMT_TZ_' not in out_normal, "正常环境不得输出探测行：%r" % out_normal
    # force=True 强制重走分级注册 => 必须打印 PROBE/SHIM failed 二者之一 + 四字段
    ns['_qs_tz_shim'](force=True)
    out_forced = capsys.readouterr().out
    has_probe = 'QS_QMT_TZ_PROBE' in out_forced
    has_fail = 'QS_QMT_TZ_SHIM failed' in out_forced
    assert has_probe ^ has_fail, "force 后应答恰一种：%r" % out_forced
    for field in _TZ_FIELDS:
        assert field in out_forced


def test_t2_idempotent_no_second_print(capsys):
    """T2：_QS_QMT_TZ_DONE 生效——再次调用直接返回，零打印。"""
    ns = _render_ns()
    capsys.readouterr()  # 清掉 exec 期输出
    ret = ns['_qs_tz_shim']()
    assert 'QS_QMT_TZ_' not in capsys.readouterr().out
    assert ns['_QS_QMT_TZ_DONE'] is not None
    assert ret == ns['_QS_QMT_TZ_DONE']


def test_t3_real_tz_convert(capsys):
    """T3：真实走 tz_convert（force=True 强制重走分级注册后实测解析）。"""
    ns = _render_ns()
    capsys.readouterr()
    ns['_qs_tz_shim'](force=True)
    out = capsys.readouterr().out
    assert ('QS_QMT_TZ_PROBE' in out) ^ ('QS_QMT_TZ_SHIM failed' in out)
    idx = pd.DatetimeIndex(pd.to_datetime(['2026-06-01T01:30:00+00:00']))
    got = idx.tz_convert('Asia/Shanghai')
    assert str(got[0]) == '2026-06-01 09:30:00+08:00'


def test_t4_equivalence_fixed_offset_8h(capsys):
    """T4：2026-06-01..09-30 每 5 日抽样，Asia/Shanghai 与固定 +08:00 逐位一致。"""
    ns = _render_ns()
    capsys.readouterr()
    ns['_qs_tz_shim'](force=True)
    # 17:30 UTC 跨日界时刻（+8h => 次日 01:30），抽样才真正检验换算
    base = datetime(2026, 6, 1, 17, 30, tzinfo=_dt_timezone.utc)
    samples = [base + timedelta(days=5 * k) for k in range(25)]  # 25 >= 20
    for t in samples:
        via_shanghai = pd.DatetimeIndex([pd.Timestamp(t)]).tz_convert(
            'Asia/Shanghai').strftime('%Y%m%d')[0]
        manual = (t + timedelta(hours=8)).strftime('%Y%m%d')  # 固定 +08:00 手工换算
        assert via_shanghai == manual, (t, via_shanghai, manual)


def test_t5_fail_loud_three_layers_dead(monkeypatch, capsys):
    """T5：三层全失败 => 打印 QS_QMT_TZ_SHIM failed 且不抛异常。"""
    ns = _render_ns()
    capsys.readouterr()
    # mock pytz/zoneinfo 导入失败：sys.modules 置 None => import 抛 ImportError
    monkeypatch.setitem(sys.modules, 'pytz', None)
    monkeypatch.setitem(sys.modules, 'zoneinfo', None)

    def _boom(*a, **k):
        raise RuntimeError('mock: pd.Timestamp unavailable')

    class _PdStub:
        Timestamp = staticmethod(_boom)

    real_pd = ns['pd']
    ns['pd'] = _PdStub  # 探测/复探（P2、P3 探测均挂 pd.Timestamp）全抛错
    try:
        ns['_qs_tz_shim'](force=True)  # fail-loud：只打印不 raise
    finally:
        ns['pd'] = real_pd
    out = capsys.readouterr().out
    assert 'QS_QMT_TZ_SHIM failed' in out
    for field in _TZ_FIELDS:
        assert field in out
