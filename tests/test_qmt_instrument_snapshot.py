"""tests for D-30 PIT 冻结合约快照层（instrument snapshot，QMT source 注入面）。

设计合同：docs/qmt-instrument-snapshot-design.md v0.3 §三（合同）/ §五（验收
判据 8 组，本文件逐组覆盖，15 用例 >= 12）。

测试模式（与 tests/test_source_import_qmt.py 同款）：QmtSourceConverter 渲染
产物 -> exec 进命名空间 -> tmp_path 写快照文件 -> 改命名空间内
_QS_QMT_INSTR_SNAP_PATH -> mock C（get_instrument_detail 可计数/可抛错）断言。
每个用例独立 exec（产物模块级 _QS_QMT_SNAP_STATE 三态缓存隔离——设计 §三：
运行中新建/变更文件不生效）。

四消费点（设计 §一）：
  1 _qs_get_stock_info（live get_instrument_detail，except -> d={} 续字段映射）
  2 _qs_status_flags（live，except -> ({},False)；live 空结果亦 ({},False)）
  3 _qs_get_ashares（live get_stock_list_in_sector 单参，except -> []；
    date!=None 时 QS_QMT_SEMANTIC_DIFF 登记行保留）
  4 _QmtDataBar.__init__（live，except -> det={}，消费 PreClose/UpStopPrice/
    DownStopPrice 三字段）
"""

import hashlib
import json
import re

from quantstudio.strategy_compiler.source_import_qmt import QmtSourceConverter

# 与 tests/test_source_import_qmt.py 最小正例同源：生命周期双件 + get_history
# 调用（渲染产物含全部四消费点 wrapper）。
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

# mock 哨兵：details[code] -> raw dict / _RAISE（live 抛错形态）
_RAISE = object()

_DETAIL_A = {
    'InstrumentName': '浦发银行', 'OpenDate': 19991110, 'ExpireDate': 99999999,
    'UpStopPrice': 11.0, 'DownStopPrice': 9.0, 'PreClose': 10.0,
    'FloatVolume': 1750000, 'InstrumentStatus': 0, 'IsTrading': True,
}
_DETAIL_B = {
    'InstrumentName': 'ST_SAMPLE', 'OpenDate': 20000101, 'ExpireDate': 20200101,
    'UpStopPrice': 5.5, 'DownStopPrice': 4.5, 'PreClose': 5.0,
    'FloatVolume': 800, 'InstrumentStatus': 1, 'IsTrading': False,
}


def _render_ns():
    """渲染产物 + exec 进独立命名空间（每用例独立 exec = 三态缓存隔离）。"""
    result = QmtSourceConverter(_MIN_SOURCE).convert()
    assert result.errors == []
    code = result.converted_code
    # 产物须整体物理 GBK 可编码（含 D-30 MODULE_HEADER 新增段——写盘点前提）
    code.encode("gbk")
    ns = {}
    exec(compile(code, "<qmt-product>", "exec"), ns)
    return ns


def _bind(ns, path):
    """把产物命名空间内快照约定路径改绑到测试路径（函数 globals 即 ns）。"""
    ns["_QS_QMT_INSTR_SNAP_PATH"] = str(path)


def _write_snapshot(tmp_path, records, enabled=True):
    """按导出脚本约定写快照：UTF-8 / ensure_ascii=False / int 保持 int。

    返回 (path, data)——data 为文件字节（sha 断言用）。
    """
    path = tmp_path / "instrument_snapshot.json"
    doc = {
        "enabled": enabled,
        "meta": {"exported_at": "2026-10-13T00:00:00", "n": len(records)},
        "records": records,
    }
    data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    return path, data


class _MockC:
    """mock ContextInfo：detail 可计数/可抛错；sector 单参守卫（严禁 realtime）。"""

    def __init__(self, details=None, sector=None):
        self._details = dict(details or {})
        self._sector = list(sector or [])
        self.detail_calls = []
        self.sector_calls = 0

    def get_instrument_detail(self, code):
        self.detail_calls.append(code)
        v = self._details.get(code, _RAISE)
        if v is _RAISE:
            raise RuntimeError("get_instrument_detail boom: %s" % code)
        return v

    def get_stock_list_in_sector(self, sectorname, *args, **kwargs):
        self.sector_calls += 1
        assert not args and not kwargs, (
            "get_stock_list_in_sector 必须单参调用（realtime 严禁，D-31 定谳）")
        return list(self._sector)

    def get_market_data_ex(self, *args, **kwargs):
        return {}  # _QmtDataBar 行情路径取空（三字段断言不受行情影响）


# ---------------------------------------------------------------------------
# 组 1：无文件（默认零漂移）——四消费点与现版本逐位一致 + 零审计行
# ---------------------------------------------------------------------------

def test_no_file_stock_info_live_bitwise_and_zero_output(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, tmp_path / "instrument_snapshot.json")  # 不存在
    live = dict(_DETAIL_A, InstrumentName="NAME_A")
    c = _MockC(details={"600000.SH": live})
    info = ns["_qs_get_stock_info"]("600000.SH", C=c)
    assert c.detail_calls == ["600000.SH"]  # live 调用发生
    assert info == {
        "display_name": "NAME_A", "name": "NAME_A",
        "start_date": 19991110, "end_date": 99999999,
        "high_limit": 11.0, "low_limit": 9.0, "float_shares": 1750000,
        "instrument_status": 0, "is_trading": True,
        "listed_date": "1999-11-10", "de_listed_date": None,  # 99999999 -> None
    }
    assert capsys.readouterr().out == ""  # 零审计行（判据 C：无文件 = 零输出）
    assert ns["_QS_QMT_SNAP_STATE"]["state"] == "absent"


def test_no_file_status_flags_live_semantics(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, tmp_path / "instrument_snapshot.json")
    okd = {"InstrumentName": "X", "InstrumentStatus": 0, "IsTrading": True,
           "ExpireDate": 99999999}
    c = _MockC(details={"600000.SH": okd, "000001.SZ": _RAISE, "000002.SZ": {}})
    assert ns["_qs_status_flags"]("600000.SH", c) == (
        {"ST": False, "HALT": False, "DELISTING": False}, True)
    # live 抛错 -> ({}, False)（:1017 except 形态逐点保留）
    assert ns["_qs_status_flags"]("000001.SZ", c) == ({}, False)
    # live 空结果 -> ({}, False)（:1020 not d 形态逐点保留）
    assert ns["_qs_status_flags"]("000002.SZ", c) == ({}, False)
    assert capsys.readouterr().out == ""


def test_no_file_ashares_live_and_zero_output(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, tmp_path / "instrument_snapshot.json")
    c = _MockC(sector=["600000.SH", "000001.SZ"])
    assert ns["_qs_get_ashares"](C=c) == ["600000.SH", "000001.SZ"]
    assert c.sector_calls == 1
    # live 抛错 -> []（:887 except 形态保留）
    c3 = _raise_sector_mock()
    assert ns["_qs_get_ashares"](C=c3) == []
    assert capsys.readouterr().out == ""


class _RaiseSectorC(_MockC):
    def get_stock_list_in_sector(self, sectorname, *args, **kwargs):
        self.sector_calls += 1
        raise RuntimeError("sector boom")


def _raise_sector_mock():
    return _RaiseSectorC()


def test_no_file_databar_three_fields_current_behavior(tmp_path, capsys):
    ns = _render_ns()
    _bind(ns, tmp_path / "instrument_snapshot.json")
    live = {"PreClose": 10.5, "UpStopPrice": 11.55, "DownStopPrice": 9.45}
    c = _MockC(details={"600000.SH": live})
    bar = ns["_QmtDataBar"](c, "600000.SH")
    assert (bar.preclose, bar.high_limit, bar.low_limit) == (10.5, 11.55, 9.45)
    assert c.detail_calls == ["600000.SH"]
    # live 抛错 -> det={} -> 三字段 None（:503 except 形态逐点保留）
    c2 = _MockC(details={"600000.SH": _RAISE})
    bar2 = ns["_QmtDataBar"](c2, "600000.SH")
    assert (bar2.preclose, bar2.high_limit, bar2.low_limit) == (None, None, None)
    assert capsys.readouterr().out == ""


def test_absent_cached_mid_run_file_creation_ignored(tmp_path, capsys):
    """absent 态被缓存：运行中新建文件不生效（须重跑回测）——设计 §三①。"""
    ns = _render_ns()
    snap = tmp_path / "instrument_snapshot.json"
    _bind(ns, snap)
    c = _MockC(details={"600000.SH": {"InstrumentName": "LIVE"}})
    assert ns["_qs_get_stock_info"]("600000.SH", C=c)["name"] == "LIVE"
    assert capsys.readouterr().out == ""
    # 中途建文件
    _write_snapshot(tmp_path, {"600000.SH": {"InstrumentName": "FROZEN"}})
    assert ns["_qs_get_stock_info"]("600000.SH", C=c)["name"] == "LIVE"
    assert c.detail_calls == ["600000.SH", "600000.SH"]  # 两次都走 live
    assert capsys.readouterr().out == ""  # 仍零审计行
    assert ns["_QS_QMT_SNAP_STATE"]["state"] == "absent"


# ---------------------------------------------------------------------------
# 组 2：enabled 全命中——四消费点零 live 调用
# ---------------------------------------------------------------------------

def test_enabled_all_four_consumers_hit_zero_live(tmp_path, capsys):
    ns = _render_ns()
    path, _ = _write_snapshot(
        tmp_path, {"600000.SH": dict(_DETAIL_A), "000001.SZ": dict(_DETAIL_B)})
    _bind(ns, path)
    c = _MockC()  # 任何 live 调用都会抛错（mock 默认 _RAISE）
    # 消费点 1：字段级冻结
    info = ns["_qs_get_stock_info"]("600000.SH", C=c)
    assert info["name"] == "浦发银行"
    assert info["listed_date"] == "1999-11-10"
    assert info["high_limit"] == 11.0
    # 消费点 2：三判冻结
    assert ns["_qs_status_flags"]("000001.SZ", c) == (
        {"ST": True, "HALT": True, "DELISTING": True}, True)
    # 消费点 3：sorted 稳定序冻结名单
    assert ns["_qs_get_ashares"](C=c) == ["000001.SZ", "600000.SH"]
    # 消费点 4：执行层三字段冻结
    bar = ns["_QmtDataBar"](c, "600000.SH")
    assert (bar.preclose, bar.high_limit, bar.low_limit) == (10.0, 11.0, 9.0)
    # 零 live（detail 与 sector 均未被触达）
    assert c.detail_calls == []
    assert c.sector_calls == 0
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT mode=on") == 1
    assert "first_miss" not in out
    assert ns["_QS_QMT_SNAP_STATE"]["state"] == "loaded"


# ---------------------------------------------------------------------------
# 组 3：缺码回落——三形态异常语义逐点保持 + first_miss 恰一次
# ---------------------------------------------------------------------------

def test_missing_code_stock_info_fallback_first_miss_once(tmp_path, capsys):
    ns = _render_ns()
    path, _ = _write_snapshot(tmp_path, {"600000.SH": dict(_DETAIL_A)})
    _bind(ns, path)
    c = _MockC(details={"000001.SZ": _RAISE})
    # 命中码：冻结值，零 live
    assert ns["_qs_get_stock_info"]("600000.SH", C=c)["name"] == "浦发银行"
    # 缺码：live 抛错 -> d={} -> 全键 None 映射（:982 except 形态保持）
    miss = ns["_qs_get_stock_info"]("000001.SZ", C=c)
    assert set(miss.values()) == {None}
    assert len(miss) == 11
    # 二次缺码：first_miss 仍恰一次
    ns["_qs_get_stock_info"]("000001.SZ", C=c)
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT first_miss=") == 1
    assert "first_miss=000001.SZ" in out
    assert out.count("mode=on") == 1
    assert c.detail_calls == ["000001.SZ", "000001.SZ"]  # 命中码零 live


def test_missing_code_status_flags_two_fallback_forms(tmp_path, capsys):
    ns = _render_ns()
    path, _ = _write_snapshot(tmp_path, {"600000.SH": dict(_DETAIL_A)})
    _bind(ns, path)
    # 缺码 A：live 抛错 -> ({}, False)（:1017 except 形态保持）
    c_raise = _MockC(details={"000001.SZ": _RAISE})
    assert ns["_qs_status_flags"]("000001.SZ", c_raise) == ({}, False)
    # 缺码 B：live 返回 {} -> ({}, False)（:1020 not d 形态保持）
    c_empty = _MockC(details={"000002.SZ": {}})
    assert ns["_qs_status_flags"]("000002.SZ", c_empty) == ({}, False)
    # 命中码：正常判定，零 live
    c_ok = _MockC()
    assert ns["_qs_status_flags"]("600000.SH", c_ok) == (
        {"ST": False, "HALT": False, "DELISTING": False}, True)
    assert c_ok.detail_calls == []
    out = capsys.readouterr().out
    # first_miss 整次运行恰一次（首个缺码；第二个缺码不再打印）
    assert out.count("QS_QMT_SNAPSHOT first_miss=") == 1
    assert "first_miss=000001.SZ" in out


def test_missing_code_databar_fallback(tmp_path, capsys):
    ns = _render_ns()
    path, _ = _write_snapshot(tmp_path, {"600000.SH": dict(_DETAIL_A)})
    _bind(ns, path)
    c = _MockC(details={"000001.SZ": _RAISE})
    # 缺码：live 抛错 -> det={} -> 三字段 None（:503 except 形态保持）
    bar = ns["_QmtDataBar"](c, "000001.SZ")
    assert (bar.preclose, bar.high_limit, bar.low_limit) == (None, None, None)
    # 命中码：冻结三字段
    bar_hit = ns["_QmtDataBar"](c, "600000.SH")
    assert (bar_hit.preclose, bar_hit.high_limit, bar_hit.low_limit) == (10.0, 11.0, 9.0)
    assert c.detail_calls == ["000001.SZ"]
    assert capsys.readouterr().out.count("QS_QMT_SNAPSHOT first_miss=") == 1


# ---------------------------------------------------------------------------
# 组 4：broken（JSON 损坏 / schema 不符 / 读失败）——全回落 + off(broken) 首印
# ---------------------------------------------------------------------------

def test_broken_json_all_fallback_and_disclosure_once(tmp_path, capsys):
    ns = _render_ns()
    path = tmp_path / "instrument_snapshot.json"
    path.write_bytes(b'{"enabled": true, "records": {"600000.SH": {"a": 1')  # 截断
    _bind(ns, path)
    c = _MockC(details={"600000.SH": {"InstrumentName": "LIVE"}}, sector=["600000.SH"])
    assert ns["_qs_get_stock_info"]("600000.SH", C=c)["name"] == "LIVE"
    assert ns["_qs_status_flags"]("600000.SH", c)[1] is True
    assert ns["_qs_get_ashares"](C=c) == ["600000.SH"]
    bar = ns["_QmtDataBar"](c, "600000.SH")
    assert bar.preclose is None  # live 无 PreClose 字段 -> None
    assert len(c.detail_calls) == 3  # 三处 detail 消费点全回落
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT mode=off(broken)") == 1
    assert "n=-1" in out  # broken 态 n=-1（第二轮审计 P2-2）
    assert "first_miss" not in out  # 非 loaded 态无 first_miss


def test_broken_schema_records_not_dict(tmp_path, capsys):
    ns = _render_ns()
    path = tmp_path / "instrument_snapshot.json"
    doc = {"enabled": True, "meta": {}, "records": ["600000.SH"]}
    path.write_bytes(json.dumps(doc).encode("utf-8"))
    _bind(ns, path)
    c = _MockC(sector=["600000.SH"])
    assert ns["_qs_get_ashares"](C=c) == ["600000.SH"]  # 回落 live
    assert c.sector_calls == 1
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT mode=off(broken)") == 1
    assert "n=-1" in out


def test_broken_read_failure_directory_path(tmp_path, capsys):
    """读失败形态：路径存在但字节不可得（指向目录）-> broken（sha=- 占位）。"""
    ns = _render_ns()
    _bind(ns, tmp_path)  # 目录：exists=True，open/read 抛错
    c = _MockC(sector=["600000.SH"])
    assert ns["_qs_get_ashares"](C=c) == ["600000.SH"]
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT mode=off(broken)") == 1
    assert "sha=-" in out and "n=-1" in out


# ---------------------------------------------------------------------------
# 组 5（含 disabled）：首印静态行字段完整且整次运行恰一次
# ---------------------------------------------------------------------------

def test_first_print_fields_complete_and_once(tmp_path, capsys):
    ns = _render_ns()
    path, data = _write_snapshot(
        tmp_path, {"600000.SH": dict(_DETAIL_A), "000001.SZ": dict(_DETAIL_B)})
    _bind(ns, path)
    c = _MockC()
    ns["_qs_get_stock_info"]("600000.SH", C=c)
    ns["_qs_status_flags"]("600000.SH", c)
    ns["_qs_get_ashares"](C=c)
    ns["_QmtDataBar"](c, "600000.SH")
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines()
             if ln.startswith("QS_QMT_SNAPSHOT mode=")]
    assert len(lines) == 1  # 整次运行恰一次
    assert re.fullmatch(
        r"QS_QMT_SNAPSHOT mode=on file=instrument_snapshot\.json"
        r" sha=[0-9a-f]{16} n=2", lines[0])
    # sha 与产物字节逐位对齐（导出摘要行同源指纹）
    assert ("sha=%s" % hashlib.sha256(data).hexdigest()[:16]) in lines[0]


def test_disabled_enabled_false_off_mode_all_live(tmp_path, capsys):
    ns = _render_ns()
    path, data = _write_snapshot(
        tmp_path, {"600000.SH": dict(_DETAIL_A)}, enabled=False)
    _bind(ns, path)
    c = _MockC(details={"600000.SH": {"InstrumentName": "LIVE"}},
               sector=["600000.SH"])
    assert ns["_qs_get_stock_info"]("600000.SH", C=c)["name"] == "LIVE"
    assert ns["_qs_get_ashares"](C=c) == ["600000.SH"]
    out = capsys.readouterr().out
    assert out.count("QS_QMT_SNAPSHOT mode=off ") == 1  # 注意 off 非 off(broken)
    assert ("sha=%s" % hashlib.sha256(data).hexdigest()[:16]) in out
    assert "n=1" in out  # disabled 态 records 已解析 -> n=len(records)
    assert "first_miss" not in out
    assert ns["_QS_QMT_SNAP_STATE"]["state"] == "disabled"


# ---------------------------------------------------------------------------
# 组 6：round-trip raw dict 原样 + ExpireDate 三形态同判
# ---------------------------------------------------------------------------

def test_round_trip_raw_dict_and_expiredate_three_forms(tmp_path, capsys):
    raw = dict(_DETAIL_A)
    # round-trip：导出约定序列化 -> loader -> 访问器 raw dict 原样（int 保持 int）
    path, _ = _write_snapshot(tmp_path, {"600000.SH": raw})
    ns = _render_ns()
    _bind(ns, path)
    got = ns["_qs_instr_detail"]("600000.SH")
    assert got == raw
    assert isinstance(got["OpenDate"], int)
    assert isinstance(got["ExpireDate"], int)
    assert isinstance(got["FloatVolume"], int)
    capsys.readouterr()

    # ExpireDate 三形态（int 20261015 / int 99999999 永续 / str '2026-10-15'）：
    # 快照判定 == live 判定（_qs_norm_ymd 与 _qs_status_flags 双面同判）。
    ymd_known = {20261015: "2026-10-15", 99999999: None, "2026-10-15": "2026-10-15"}
    for exp in (20261015, 99999999, "2026-10-15"):
        # 快照侧（命中）
        ns_snap = _render_ns()
        p, _ = _write_snapshot(tmp_path, {"600000.SH": dict(raw, ExpireDate=exp)})
        _bind(ns_snap, p)
        c_snap = _MockC()
        flags_snap, ok_snap = ns_snap["_qs_status_flags"]("600000.SH", c_snap)
        assert c_snap.detail_calls == []
        ymd_snap = ns_snap["_qs_norm_ymd"](exp)
        # live 侧（无文件回落）
        ns_live = _render_ns()
        _bind(ns_live, tmp_path / "nonexistent.json")
        c_live = _MockC(details={"600000.SH": dict(raw, ExpireDate=exp)})
        flags_live, ok_live = ns_live["_qs_status_flags"]("600000.SH", c_live)
        ymd_live = ns_live["_qs_norm_ymd"](exp)
        assert (flags_snap, ok_snap) == (flags_live, ok_live)  # 同判
        assert ymd_snap == ymd_live == ymd_known[exp]
        capsys.readouterr()


# ---------------------------------------------------------------------------
# 组 7：消费点 4 专项（冻结值已由组 2 覆盖；此处无文件现行为已由组 1 覆盖）
# 组 8：get_Ashares 快照路径 SEMANTIC_DIFF 登记行保留
# ---------------------------------------------------------------------------

def test_ashares_snapshot_keeps_semantic_diff_line(tmp_path, capsys):
    ns = _render_ns()
    path, _ = _write_snapshot(tmp_path, {"600000.SH": {}, "000001.SZ": {}})
    _bind(ns, path)
    c = _MockC(sector=["600000.SH", "000001.SZ"])
    res = ns["_qs_get_ashares"](date="20260101", C=c)
    assert res == ["000001.SZ", "600000.SH"]  # sorted 稳定序冻结名单
    out = capsys.readouterr().out
    # 登记行保留（冻结名单 != 历史 PIT，登记语义仍真——设计 §三⑤）
    assert "QS_QMT_SEMANTIC_DIFF: get_Ashares date='20260101' 不消费" in out
    assert c.sector_calls == 0  # 快照路径零 live
    assert out.count("QS_QMT_SNAPSHOT mode=on") == 1
