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


# ==========================================================================
# A3/A4/A5 扩写用例（2026-10-21；以下全部为纯追加，上方既有 10 用例零改动）
# 覆盖：真实九条合法 / 旧 schema 兼容 / 域过滤计数 / 目录缺失 exit2 /
#       薄壳↔公共核等价 / hits 形状 / 会红核验 / kb_draft 正负向 / 薄壳再导出面
# ==========================================================================
import json  # noqa: E402  追加用例区集中导入（不扰动上方既有 10 例的 import 头）
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402

CORE_SRC = SCRIPTS / "kb_preflight.py"
DRAFT_SRC = SCRIPTS / "kb_draft.py"
REAL_KB = ROOT / "knowledge" / "qmt"

# 子进程统一强制 UTF-8 stdio（Windows 管道默认 locale 编码会破坏 ensure_ascii=False 的 JSON）
PYENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


@pytest.fixture(scope="module")
def core():
    return _load(CORE_SRC, "kb_preflight_under_test")


@pytest.fixture(scope="module")
def kd():
    return _load(DRAFT_SRC, "kb_draft_under_test")


def _run(args, cwd=None):
    return subprocess.run([sys.executable] + [str(a) for a in args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=PYENV, cwd=str(cwd) if cwd else None)


_VALID_FRONT = (
    "id: QD-2026-01{nn}\n"
    'symptom: "临时测试条目（扩写用例造件）"\n'
    'root_cause: "测试根因"\n'
    'fix_target: "scripts/none.py:1"\n'
    "fix_layer: wrapper\n"
    "{domain}"
    'trigger: "测试触发条件"\n'
    "recurrence: 0\n"
    'evidence: "docs/evidence/x.md"\n'
    'fixture: "tests/test_x.py"\n'
    "feedback:\n"
    '  contract_row: "registry TEST-01"\n'
    "  gate_rule: null\n"
    "  template_patch: null\n"
    "status: open\n"
    "closed_round: null\n"
)


def _mk_kb(tmp_path, entries):
    """entries: list[(文件名, frontmatter 文本)] → 临时知识库目录（复用 _w 同风格的临时件模式）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    for name, fm in entries:
        (kb / name).write_text("---\n" + fm + "---\n", encoding="utf-8")
    return kb


def _fm(nn, domain=""):
    return _VALID_FRONT.format(nn=nn, domain=domain)


# ---------- ⑥' ① 九条真实条目全字段齐全且合法 ----------

def test_a3_real_nine_entries_validate(core):
    """①：knowledge/qmt/QD-*.md（现存九条）逐条过 validate_entry 全通过。"""
    files = sorted(REAL_KB.glob("QD-*.md"))
    assert len(files) >= 9, files
    for p in files:
        errs = core.validate_entry(core.parse_entry(p))
        assert errs == [], "%s: %s" % (p.name, errs)


# ---------- ⑥' ② 旧 schema 兼容（无 domain 条目） ----------

def test_a3_old_schema_entry_without_domain(tmp_path):
    """②：无 domain 条目——不带 --domain 不报错；--domain 下不匹配且输出 DOM warn。"""
    kb = _mk_kb(tmp_path, [("QD-2026-0101.md", _fm("01"))])
    src = _w(tmp_path, "ok.py", "x = 1\n")
    r0 = _run([CORE_SRC, "--kb", kb, "--source", src, "--json"])
    assert r0.returncode == 0, r0.stderr
    j0 = json.loads(r0.stdout)
    assert j0["kb_entries"] == 1
    assert all(w["rule"] != "DOM" for w in j0["warn"]), j0["warn"]
    r1 = _run([CORE_SRC, "--kb", kb, "--source", src, "--domain", "D2", "--json"])
    assert r1.returncode == 0, r1.stderr
    j1 = json.loads(r1.stdout)
    assert j1["kb_entries"] == 0
    dom = [w for w in j1["warn"] if w["rule"] == "DOM"]
    assert len(dom) == 1, j1["warn"]
    assert dom[0]["qd"] == "-"
    assert "未标注 domain" in dom[0]["msg"] and "QD-2026-0101" in dom[0]["msg"]


# ---------- ⑥' ③ 域过滤计数 ----------

def test_a3_domain_filter_counts(tmp_path):
    """③：D2×2 + 无域×1 → --domain D2 ⇒ kb_entries==2、hits==2、DOM warn 记 1 条。"""
    kb = _mk_kb(tmp_path, [
        ("QD-2026-0201.md", _fm("01", "domain: D2\n")),
        ("QD-2026-0202.md", _fm("02", "domain: D2\n")),
        ("QD-2026-0203.md", _fm("03")),
    ])
    src = _w(tmp_path, "ok.py", "x = 1\n")
    r = _run([CORE_SRC, "--kb", kb, "--source", src, "--domain", "D2", "--json"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["kb_entries"] == 2, j
    assert len(j["kb_ids"]) == 2, j["kb_ids"]      # 域过滤后的条目全集
    assert j["hits"] == [], j["hits"]              # 无规则触发 ⇒ hits 为空（hits＝实际命中，A7 口径）
    dom = [w for w in j["warn"] if w["rule"] == "DOM"]
    assert len(dom) == 1 and "1 条" in dom[0]["msg"], j["warn"]


# ---------- ⑥' ④ 目录缺失负向（旧缺陷不得复制） ----------

def test_a3_missing_kb_dir_exit2(tmp_path):
    """④：--kb 指向不存在目录 → exit 2 且 stderr 非空（禁静默 0 条目 PASS）。"""
    src = _w(tmp_path, "ok.py", "x = 1\n")
    r = _run([CORE_SRC, "--kb", tmp_path / "__nonexistent__", "--source", src, "--json"])
    assert r.returncode == 2
    assert r.stderr.strip()


# ---------- ⑥' ⑤ 薄壳 CLI ↔ 公共核 CLI 等价性 ----------

def test_a3_shell_vs_core_equivalence():
    """⑤：同一 source（薄壳自身），旧 CLI vs 新 CLI（--kb knowledge/qmt）
    四键全等（kb_entries/verdict/block/warn）+ 退出码全等。"""
    r_old = _run([SRC, "--source", SRC, "--json"], cwd=ROOT)
    r_new = _run([CORE_SRC, "--kb", "knowledge/qmt", "--source", SRC, "--json"], cwd=ROOT)
    assert r_old.returncode == r_new.returncode == 0, (r_old.stderr, r_new.stderr)
    j_old, j_new = json.loads(r_old.stdout), json.loads(r_new.stdout)
    for k in ("kb_entries", "verdict", "block", "warn"):
        assert j_old[k] == j_new[k], k


# ---------- ⑥' ⑥ hits 元素形状 ----------

def test_a3_hits_element_shape(tmp_path):
    """⑥：hits 元素四键齐（qd/domain/rule/path），且仅含**实际命中**条目（A7 的 H 口径）。

    用触发 B4（hist 表请求缺折叠键）的源码作输入 ⇒ 命中 QD-2026-0003；未触发的条目不入 hits。
    """
    src = _w(tmp_path, "b4_src.py", "df = get_fundamentals('000001.SZ', table='eps', fields=['eps'])\n")
    r = _run([CORE_SRC, "--kb", "knowledge/qmt", "--source", src, "--json"], cwd=ROOT)
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    qds = [h["qd"] for h in j["hits"]]
    assert qds == ["QD-2026-0003"], qds
    assert "QD-2026-0001" not in qds, "未触发 B1 的条目不得进入 hits"
    for h in j["hits"]:
        assert set(h) == {"qd", "domain", "rule", "path"}, h
        assert re.match(r"^QD-\d{4}-\d{4}$", h["qd"]), h
        assert h["domain"] in ("D1", "D2", "D3", "D4", "D5"), h
        assert isinstance(h["rule"], str) and h["rule"], h
        if h["rule"] != "-":
            assert all(c in ("B1", "B2", "B3", "B4", "W1", "W4", "W5")
                       for c in h["rule"].split("/")), h
        assert h["path"].endswith(".md"), h


# ---------- ⑥' ⑦ 会红核验（判据非恒绿） ----------

def test_a3_bom_product_blocks(tmp_path):
    """⑦：含 BOM 产物 → verdict=BLOCK、exit 1。"""
    p = _w(tmp_path, "bom_product.py", b"\xef\xbb\xbf#coding:gbk\nprint(1)\n")
    r = _run([CORE_SRC, "--kb", "knowledge/qmt", "--product", p, "--json"], cwd=ROOT)
    assert r.returncode == 1
    j = json.loads(r.stdout)
    assert j["verdict"] == "BLOCK"
    assert any(b["rule"] == "B1" for b in j["block"]), j["block"]


# ---------- ⑥' ⑧⑨ kb_draft 正向 / 自动判层 / 负向 ----------

def test_a5_kb_draft_positive_writes_valid_entry(core, tmp_path):
    """⑧：kb_draft 正向——生成条目 UTF-8 无 BOM 且过 validate_entry。"""
    out = tmp_path / "QD-2026-0099.md"
    r = _run([DRAFT_SRC,
              "--symptom", "草稿器测试现象", "--evidence", "docs/evidence/x.md",
              "--fix-target", "quantstudio/backtest/x.py:1", "--domain", "D3",
              "--layer", "wrapper", "--root-cause", "测试根因", "--fixture", "tests/test_x.py",
              "--out", out, "--json"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["ok"] is True and j["fix_layer"] == "wrapper" and j["domain"] == "D3", j
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    errs = core.validate_entry(core.parse_entry(out))
    assert errs == [], errs
    txt = raw.decode("utf-8")
    assert "status: open" in txt and "recurrence: 0" in txt and "closed_round: null" in txt
    assert "## 复现步骤" in txt and "## 影响面" in txt and "## 讨论" in txt


def test_a5_kb_draft_auto_layer_basis_printed(tmp_path):
    """A5：--layer 缺省自动判定且判据可见（--json 进 notes，禁静默）。"""
    out = tmp_path / "draft_auto.md"
    r = _run([DRAFT_SRC,
              "--symptom", "s", "--evidence", "e",
              "--fix-target", "docs/evidence/matrix.yaml:1", "--domain", "D2",
              "--root-cause", "r", "--fixture", "f", "--out", out, "--json"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["fix_layer"] == "matrix", j
    assert any("matrix" in n for n in j["notes"]), j


def test_a5_kb_draft_missing_trigger_refuses_write(kd, monkeypatch, tmp_path):
    """⑨：注入缺 trigger（占位置空）→ exit 1 且不落盘。"""
    out = tmp_path / "QD-2026-0098.md"
    monkeypatch.setattr(kd, "TRIGGER_PLACEHOLDER", "", raising=False)
    rc = kd.main(["--symptom", "s", "--evidence", "e",
                  "--fix-target", "quantstudio/backtest/x.py:1", "--domain", "D3",
                  "--root-cause", "r", "--fixture", "f", "--out", str(out)])
    assert rc == 1
    assert not out.exists()


# ---------- ⑥' ⑩ 薄壳再导出面 ----------

def test_a3_shell_reexports(pf):
    """⑩：薄壳再导出六名齐、可调；load_kb_index() 无参可调且含 domain 字段。"""
    assert pf.BLOCK_RULES["B1"][0] == "QD-2026-0001"
    assert pf.WARN_RULES["B4"][0] == "QD-2026-0003"
    assert pf.ACK_RE.search("# QS_KB_ACK: QD-2026-0002 理由")
    assert callable(pf.check_product) and callable(pf.check_source)
    kb = pf.load_kb_index()
    assert isinstance(kb, dict) and len(kb) >= 9, sorted(kb)
    assert kb["QD-2026-0001"]["fix_layer"] == "portability_rule"
    assert kb["QD-2026-0009"]["domain"] == "D3"


# ---------- ⑥' ⑪ 用法错误退出码（退出码语义不变） ----------

def test_a3_core_usage_error_exit2(core):
    """⑪：--kb 给定但缺 --source/--product → 旧用法串 + return 2。"""
    assert core.main(["--kb", str(REAL_KB)]) == 2
