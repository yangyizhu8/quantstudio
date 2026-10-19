# -*- coding: utf-8 -*-
"""P0 vault 回哺读取测试（PD-2026-0009：BOM 致静默断链）。

背景（2026-10-20 实测）：vault 8 条目中 5 条被带 BOM 写出，`_parse_frontmatter`
以 `lines[0].strip() == "---"` 起判 → 首行为 "\\ufeff---" → 条目被静默跳过，
P0 只读到 3 条。修复：`read_text(encoding="utf-8-sig")` + PD-* 解析失败告警。
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
    text = FM.format(id=eid, symptom=symptom, status=status)
    path.write_bytes(text.encode("utf-8-sig" if bom else "utf-8"))


def test_bom_entry_is_read(tmp_path):
    """BOM 前缀条目必须被读取（原缺陷：静默跳过）。"""
    _entry(tmp_path / "PD-2026-9001.md", "PD-2026-9001", bom=True)
    entries, degraded, warnings = read_vault(tmp_path)
    assert degraded is False
    assert [e["id"] for e in entries] == ["PD-2026-9001"]
    assert warnings == []


def test_plain_utf8_entry_is_read(tmp_path):
    _entry(tmp_path / "PD-2026-9002.md", "PD-2026-9002")
    entries, _, _ = read_vault(tmp_path)
    assert [e["id"] for e in entries] == ["PD-2026-9002"]


def test_readme_without_frontmatter_skipped_silently(tmp_path):
    (tmp_path / "README.md").write_text("# 说明\n\n非条目。\n", encoding="utf-8")
    entries, _, warnings = read_vault(tmp_path)
    assert entries == []
    assert warnings == []


def test_pd_entry_without_frontmatter_warns(tmp_path):
    """PD-* 但解析不出 frontmatter → 必须告警（断链可见），不得静默。"""
    (tmp_path / "PD-2026-9003.md").write_text("没有 frontmatter 的坏条目\n",
                                              encoding="utf-8")
    entries, _, warnings = read_vault(tmp_path)
    assert entries == []
    assert any("frontmatter 解析失败" in w and "PD-2026-9003" in w for w in warnings)


def test_missing_dir_degrades_not_raises(tmp_path):
    entries, degraded, warnings = read_vault(tmp_path / "nope")
    assert entries == [] and degraded is True
    assert any("不可达" in w for w in warnings)


def test_open_status_is_surfaced(tmp_path):
    _entry(tmp_path / "PD-2026-9004.md", "PD-2026-9004", status="open")
    entries, _, _ = read_vault(tmp_path)
    assert entries[0]["status"] == "open"
