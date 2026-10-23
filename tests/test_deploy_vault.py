# -*- coding: utf-8 -*-
"""P0 回哺读取测试（PD-2026-0009 BOM 断链 + PD-0010 多候选探测）。

PD-2026-0009：vault 条目被带 BOM 写出 → `_parse_frontmatter` 静默跳过 → 回哺「面上存在、
实质失效」；修复=`utf-8-sig` 读取 + PD-* 解析失败告警。

PD-0010：PD 族拓扑两次纠偏后旧单点指针失效 → 改**多候选探测**（trading 归口 → 主仓公开副本
→ 旧址兜底）+ 命中来源 provenance + 旧址命中提示。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy.preflight import read_vault  # noqa: E402

FM = """---
id: {id}
symptom: "{symptom}"
status: {status}
---

正文。
"""


def _entry(path: Path, eid: str, symptom: str = "现象", status: str = "closed",
           bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = FM.format(id=eid, symptom=symptom, status=status)
    path.write_bytes(text.encode("utf-8-sig" if bom else "utf-8"))


# ---------------------------------------------- PD-2026-0009：BOM / 解析面 --

def test_bom_entry_is_read(tmp_path):
    """BOM 前缀条目必须被读取（原缺陷：静默跳过）。"""
    _entry(tmp_path / "PD-2026-9001.md", "PD-2026-9001", bom=True)
    read = read_vault(tmp_path)
    assert read.degraded is False
    assert [e["id"] for e in read.entries] == ["PD-2026-9001"]
    assert read.warnings == []


def test_plain_utf8_entry_is_read(tmp_path):
    _entry(tmp_path / "PD-2026-9002.md", "PD-2026-9002")
    assert [e["id"] for e in read_vault(tmp_path).entries] == ["PD-2026-9002"]


def test_readme_without_frontmatter_skipped_silently(tmp_path):
    (tmp_path / "README.md").write_text("# 说明\n\n非条目。\n", encoding="utf-8")
    read = read_vault(tmp_path)
    assert read.entries == []
    assert read.warnings == []


def test_pd_entry_without_frontmatter_warns(tmp_path):
    """PD-* 但解析不出 frontmatter → 必须告警（断链可见），不得静默。"""
    (tmp_path / "PD-2026-9003.md").write_text("没有 frontmatter 的坏条目\n",
                                              encoding="utf-8")
    read = read_vault(tmp_path)
    assert read.entries == []
    assert any("frontmatter 解析失败" in w and "PD-2026-9003" in w
               for w in read.warnings)


def test_missing_dir_degrades_not_raises(tmp_path):
    read = read_vault(tmp_path / "nope")
    assert read.entries == [] and read.degraded is True
    assert any("不可达" in w for w in read.warnings)


def test_open_status_is_surfaced(tmp_path):
    _entry(tmp_path / "PD-2026-9004.md", "PD-2026-9004", status="open")
    assert read_vault(tmp_path).entries[0]["status"] == "open"


# ------------------------------------------------ PD-0010：多候选探测 --

def test_candidate_priority_picks_first_existing(tmp_path):
    """候选序：第一个存在者胜出，source 记录命中来源。"""
    first = tmp_path / "c1"
    second = tmp_path / "c2"
    _entry(first / "PD-2026-9101.md", "PD-2026-9101")
    _entry(second / "PD-2026-9102.md", "PD-2026-9102")
    read = read_vault(candidates=(("trading-knowledge", first),
                                  ("main-knowledge", second)))
    assert read.source == "trading-knowledge"
    assert [e["id"] for e in read.entries] == ["PD-2026-9101"]


def test_candidate_fallback_when_first_missing(tmp_path):
    """首个候选不可达 → 回退次候选（拓扑漂移免疫的核心）。"""
    second = tmp_path / "c2"
    _entry(second / "PD-2026-9103.md", "PD-2026-9103")
    read = read_vault(candidates=(("trading-knowledge", tmp_path / "gone"),
                                  ("main-knowledge", second)))
    assert read.source == "main-knowledge"
    assert read.degraded is False
    assert [e["id"] for e in read.entries] == ["PD-2026-9103"]


def test_all_candidates_missing_degrades_with_listed_paths(tmp_path):
    """全部候选不可达 → degraded + 警告列出候选（不抛异常）。"""
    read = read_vault(candidates=(("a", tmp_path / "x"), ("b", tmp_path / "y")))
    assert read.degraded is True and read.entries == [] and read.source == ""
    assert any("全部不可达" in w for w in read.warnings)


def test_legacy_candidate_hit_warns_compat(tmp_path):
    """命中旧址 → 提示性 warning（兼容期，内容可能过期）——不得静默。"""
    legacy = tmp_path / "legacy"
    _entry(legacy / "PD-2026-9104.md", "PD-2026-9104")
    read = read_vault(candidates=(("trading-knowledge", tmp_path / "none"),
                                  ("legacy-vault", legacy)))
    assert read.source == "legacy-vault"
    assert read.degraded is False
    assert any("旧址" in w for w in read.warnings)


def test_default_candidates_are_relative_resolved():
    """默认候选不得硬编码绝对盘符（换机/换盘即断是旧硬伤）。

    旧实现为 `Path(r"D:\\miniQMT策略实盘\\...")` 绝对常量；PD-0010 起候选一律由
    仓库根 + 同级兄弟仓目录名**运行时派生**，故源码内不应再出现盘符字面量。
    """
    import quantstudio.ptrade_deploy.preflight as pf
    from quantstudio.ptrade_deploy.preflight import vault_candidates
    cands = vault_candidates()
    assert len(cands) == 3
    assert cands[0][0] == "trading-knowledge"
    assert cands[-1][0] == "legacy-vault"
    src = Path(pf.__file__).read_text(encoding="utf-8")
    assert "D:\\" not in src, "源码不得硬编码盘符路径（应运行时派生）"
