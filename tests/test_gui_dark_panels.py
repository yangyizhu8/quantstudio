from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import ScrollArea

from quantstudio.gui.tabs.source_tab import SourceTab


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def source_config(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources_config.json").write_text(
        json.dumps({"sources": {}}), encoding="utf-8"
    )
    return config_dir


class DummyMainWindow:
    def __init__(self, config_dir):
        self.config_dir = config_dir


def test_source_tab_uses_transparent_fluent_scroll_area(app, source_config):
    tab = SourceTab(DummyMainWindow(source_config))

    assert isinstance(tab.scroll_area, ScrollArea)
    assert "background: transparent" in tab.scroll_area.styleSheet()
