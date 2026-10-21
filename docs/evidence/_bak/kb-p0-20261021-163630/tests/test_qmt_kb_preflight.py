# -*- coding: utf-8 -*-
"""test_qmt_kb_preflight —— D-33 件 D 预检读取器测试（批 2 验收四步）

判据（用户 2026-10-16 裁定）：
  ①B1–B3 各构造**应阻断样本** → BLOCK；
  ②**非恒红对照样本**（正常输入）→ PASS（证判据非恒红）；
  ③**豁免样本**（QS_KB_ACK）→ 对应 BLOCK 降 WARN；
  ④（零回归由批 2 的六策略重转门覆盖，不在本套件）
另覆盖：B4 降 WARN 待证（不阻断）／W1 快照三态提示／已知坑清单读取。

run: pytest tests/test_qmt_kb_preflight.py -q
"""
import importlib.machinery
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = SCRIPTS / "qmt_kb_preflight.py"


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pf():
    return _load(SRC, "qmt_kb_preflight_under_test")


def _w(tmp_path, name, text, encoding="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(encoding) if isinstance(text, str) else text)
    return p


# ---------- ① B1–B3 应阻断样本 ----------

def test_b1_non_gbk_product_blocks(pf, tmp_path):
    """B1：产物含非 GBK 字符（≪ U+226A）→ BLOCK。"""
    code = "#coding:gbk\n# 注释含 ≪ 符号\nprint(1)\n"
    p = _w(tmp_path, "bad_product.py", code)
    blocks, warns = pf.check_product(p)
    assert any(c == "B1" for c, _ in blocks), blocks


def test_b1_missing_header_and_bom_block(pf, tmp_path):
    """B1：首行非 #coding:gbk ／ 含 BOM → BLOCK。"""
    p1 = _w(tmp_path, "no_header.py", "# comment\nprint(1)\n")
    p2 = _w(tmp_path, "bom.py", b"\xef\xbb\xbf#coding:gbk\nprint(1)\n")
    b1, _ = pf.check_product(p1)
    b2, _ = pf.check_product(p2)
    assert any(c == "B1" for c, _ in b1) and any(c == "B1" for c, _ in b2)


def test_b2_empty_required_arg_blocks(pf, tmp_path):
    """B2：get_financial_data 实参含空串字面量 → BLOCK。"""
    src = "df = C.get_financial_data(fields, codes, '', '')\n"
    p = _w(tmp_path, "b2.py", src)
    blocks, _ = pf.check_source(p)
    assert any(c == "B2" for c, _ in blocks), blocks


def test_b3_realtime_arg_blocks(pf, tmp_path):
    """B3：get_stock_list_in_sector 传第二实参（realtime）→ BLOCK。"""
    src = "lst = C.get_stock_list_in_sector('沪深A股', 1786723200000)\n"
    p = _w(tmp_path, "b3.py", src)
    blocks, _ = pf.check_source(p)
    assert any(c == "B3" for c, _ in blocks), blocks


# ---------- ② 非恒红对照样本 ----------

def test_control_samples_pass(pf, tmp_path):
    """对照：合规产物 + 合规源码 → 无 BLOCK（证判据非恒红）。"""
    good_product = _w(tmp_path, "good_product.py", "#coding:gbk\nprint(1)\n")
    good_src = _w(tmp_path, "good_src.py",
                  "df = C.get_financial_data(fields, codes, '19900101', '20991231')\n"
                  "lst = C.get_stock_list_in_sector('沪深A股')\n")
    b1, _ = pf.check_product(good_product)
    b2, _ = pf.check_source(good_src)
    assert b1 == [], b1
    assert b2 == [], b2


# ---------- ③ 豁免样本（QS_KB_ACK） ----------

def test_ack_exemption_downgrades_block_to_warn(pf, tmp_path):
    """豁免：B2 命中 + QS_KB_ACK → 降 WARN，BLOCK 清空且留痕。"""
    src = ("# QS_KB_ACK: QD-2026-0002 历史兼容样本（登记理由）\n"
           "df = C.get_financial_data(fields, codes, '', '')\n")
    p = _w(tmp_path, "ack.py", src)
    blocks, warns = pf.check_source(p)
    assert blocks == [], blocks
    assert any("豁免" in m and c == "B2" for c, m in warns), warns


# ---------- ④ B4 降 WARN 待证（不阻断） ----------

def test_b4_is_warn_only(pf, tmp_path):
    """B4：hist 请求未见折叠键 → 仅 WARN（②审裁定降级，不阻断）。"""
    src = ("df = get_fundamentals(codes, table='eps', fields=['eps'])\n"
           "# 请求未含 publ_date/end_date（折叠键来源）\n")
    p = _w(tmp_path, "b4.py", src)
    blocks, warns = pf.check_source(p)
    assert blocks == [], blocks
    assert any(c == "B4" for c, _ in warns), warns


# ---------- ⑤ W1 快照三态提示 + 已知坑清单 ----------

def test_w1_snapshot_warn(pf, tmp_path, monkeypatch):
    """W1：快照缺失 → WARN 提示（不阻断）。"""
    monkeypatch.setenv("QS_QMT_INSTR_SNAP_PATH", str(tmp_path / "nonexistent.json"))
    good = _w(tmp_path, "p.py", "#coding:gbk\nprint(1)\n")
    blocks, warns = pf.check_product(good)
    assert blocks == []
    assert any(c == "W1" for c, _ in warns), warns


def test_kb_index_loads_six_entries(pf):
    """已知坑清单：读 knowledge/qmt/ 六条 QD（批 1 落位）。"""
    kb = pf.load_kb_index()
    assert len(kb) >= 6, sorted(kb)
    assert "QD-2026-0001" in kb and kb["QD-2026-0001"]["fix_layer"] == "portability_rule"


def test_cli_exit_codes(pf, tmp_path, capsys):
    """CLI：有 BLOCK → 退出码 1；合规 → 0。"""
    bad = _w(tmp_path, "bad.py", "df = C.get_financial_data(f, c, '', '')\n")
    good = _w(tmp_path, "ok.py", "df = C.get_financial_data(f, c, '19900101', '20991231')\n")
    assert pf.main(["--source", str(bad), "--json"]) == 1
    assert pf.main(["--source", str(good), "--json"]) == 0
