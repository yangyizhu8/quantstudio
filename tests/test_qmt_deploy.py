# -*- coding: utf-8 -*-
"""test_qmt_deploy —— M5.2 件4/件5：QMT 部署内核单元测试。

覆盖：
  1. config 三级解析（CLI 显式 > 配置 > None）与 save_deploy_dir 持久化；
  2. slugify/target_filename（中文 tag → QMT_<英文标识>.py 规范）；
  3. deploy_file：哈希双端核对 + manifest 登记 + 幂等覆盖；
  4. CANONICAL_SIX 名单与 scripts/qmt_reconvert_six.py 名单一致性（单一事实源守护）；
  5. find_product 匹配序（canonical > 历史后缀 > slug）。

不触真实 QMT 目录——全部 tmp_path 沙箱。
run: pytest tests/test_qmt_deploy.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler import qmt_deploy as kd  # noqa: E402


def test_slug_and_filename():
    # CJK 保留（QMT 客户端实测支持中文文件名——CANSLIM「QMT转换测试.py」四轮复测先例），
    # 仅折叠文件系统危险字符；防不可辨识折叠（周频三层→strategy）。
    assert kd._slugify("CANSLIM突破成长选股策略") == "CANSLIM突破成长选股策略"
    assert kd.target_filename("CANSLIM突破成长选股策略") == "QMT_CANSLIM突破成长选股策略.py"
    assert kd.target_filename("fall_reversal") == "QMT_fall_reversal.py"
    assert kd.target_filename("周频三层") == "QMT_周频三层.py"
    assert kd.target_filename("a/b: c*?") == "QMT_a_b_c.py"


def test_config_roundtrip_and_resolve(tmp_path, monkeypatch):
    cfg = tmp_path / "qmt_deploy.json"
    monkeypatch.setattr(kd, "DEPLOY_CONFIG", cfg)
    assert kd.load_deploy_config()["deploy_dir"] is None
    assert kd.resolve_deploy_dir() is None  # 未配置态
    d = tmp_path / "qmtclient" / "python"
    d.mkdir(parents=True)
    saved = kd.save_deploy_dir(str(d))
    assert saved == d.resolve()
    assert kd.load_deploy_config()["deploy_dir"] == str(d.resolve())
    # CLI 显式覆盖优先
    other = tmp_path / "other"
    other.mkdir()
    assert kd.resolve_deploy_dir(str(other)) == other.resolve()
    # 配置路径失效 → None（fail-safe 不炸）
    cfg.write_text(json.dumps({"deploy_dir": str(tmp_path / "gone")}), encoding="utf-8")
    assert kd.resolve_deploy_dir() is None


def test_deploy_file_hash_verify_and_manifest(tmp_path, monkeypatch):
    src = tmp_path / "src.py"
    src.write_bytes("# QMT product test\nprint(1)\n".encode("gbk"))
    manifest = tmp_path / "deploy-manifest.json"
    monkeypatch.setattr(kd, "MANIFEST_PATH", manifest)
    tgt = tmp_path / "qmt_py"
    e = kd.deploy_file(src, tgt, strategy_id="demo_strat", origin="test")
    dst = tgt / "QMT_demo_strat.py"
    assert dst.is_file() and e["verified"] is True
    assert dst.read_bytes() == src.read_bytes()
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["deploys"][0]["sha256"] == e["sha256"] and m["deploys"][0]["origin"] == "test"
    # 幂等覆盖
    e2 = kd.deploy_file(src, tgt, strategy_id="demo_strat", origin="test2")
    assert e2["verified"] is True and len(json.loads(manifest.read_text(encoding="utf-8"))["deploys"]) == 2
    # 源缺失 fail-closed
    with pytest.raises(FileNotFoundError):
        kd.deploy_file(tmp_path / "nope.py", tgt)


def test_canonical_six_matches_reconvert_gate():
    """名单镜像一致性：内核 CANONICAL_SIX == scripts/qmt_reconvert_six.py SIX_TAGS。"""
    gate = Path(__file__).resolve().parents[1] / "scripts" / "qmt_reconvert_six.py"
    text = gate.read_text(encoding="utf-8")
    import re
    m = re.search(r"SIX_TAGS\s*=\s*\[(.*?)\]", text, re.S) or re.search(r"^SIX\s*=\s*\[(.*?)\]", text, re.S | re.M)
    assert m, "qmt_reconvert_six.py 未找到六策略名单（SIX_TAGS/SIX）"
    names = re.findall(r'"([^"]+)"', m.group(1))
    assert sorted(names) == sorted(kd.CANONICAL_SIX), (
        f"canonical 名单漂移：内核={kd.CANONICAL_SIX} vs 重转门={names}"
    )


def test_find_product_match_order(tmp_path, monkeypatch):
    monkeypatch.setattr(kd, "QMT_IMPORT_DIR", tmp_path)
    assert kd.find_product("nothing") is None
    (tmp_path / "fall_reversal.py").write_bytes(b"x")
    assert kd.find_product("fall_reversal").name == "fall_reversal.py"
    (tmp_path / "tech_etf_mvo_qmt.py").write_bytes(b"x")
    assert kd.find_product("tech_etf_mvo").name == "tech_etf_mvo_qmt.py"  # 历史后缀兜底
    assert "fall_reversal" in kd.list_products(canonical_only=True)
    assert "tech_etf_mvo" not in kd.list_products(canonical_only=True)  # 仅历史命名在盘→canonical 列表正确排除
    assert kd.list_products(canonical_only=False) == sorted(
        p.stem for p in tmp_path.glob("*.py"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
