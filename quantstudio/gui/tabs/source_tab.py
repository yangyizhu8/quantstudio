"""Tab2: 数据源配置（表单式，读/写 sources_config.json）"""
from __future__ import annotations

import json
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QMessageBox)
from qfluentwidgets import (
    GroupHeaderCardWidget, CheckBox, LineEdit, PushButton, ScrollArea)

logger = logging.getLogger(__name__)

_SOURCE_SCROLL_STYLE = """
QWidget#sourceScrollContent {
    background-color: #202020;
    color: #ffffff;
}
QWidget#sourceScrollContent QLabel {
    color: #ffffff;
}
"""


class SourceTab(QWidget):
    """数据源配置：每个源一个 GroupBox（CheckBox + 凭证 LineEdit）"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.config_path = self.mw.config_dir / "sources_config.json"
        self._widgets = {}  # source_name → {enabled: QCheckBox, ...}
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        self.scroll_area = ScrollArea()
        self.scroll_area.enableTransparentBackground()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("sourceScrollContent")
        self.inner_layout = QVBoxLayout(self.scroll_content)

        # 当前数据源模式提示（MCP默认 ↔ 传统多源）
        mode_label = QLabel(
            f"当前数据源模式：{self.mw._current_label()}　"
            f"配置目录：{self.mw.config_dir}\n"
            f"统一正式库：data/quantstudio.db（采集与QFQ同库）"
        )
        mode_label.setStyleSheet("color:#7fd1ff; font-weight:bold;")
        self.inner_layout.addWidget(mode_label)

        # 各源的配置（label, credential_field）
        # MCP 为统一权威源（默认模式），无需 API key，走 MCP server 端点。
        self.source_defs = [
            ("mcp", "MCP 权威源（统一入口，默认模式）", None),
            ("tushare", "Tushare Pro（付费，标准源）", "token"),
            ("baostock", "Baostock（免费，无QMT首选）", "user"),
            ("akshare", "Akshare（免费，东方财富）", None),
            ("xtquant", "xtquant（需 miniQMT 客户端运行）", "qmt_path"),
            ("a_stock_data", "a-stock-data（免费，基于mootdx，无需注册）", None),
            ("efinance", "efinance（免费备选）", None),
            ("joinquant", "聚宽 JoinQuant", "token"),
            ("custom_api", "自定义 API", "api_key"),
        ]

        for source, label, cred_field in self.source_defs:
            group = GroupHeaderCardWidget()
            group.setTitle(label)
            card_inner = QWidget()
            layout = QFormLayout(card_inner)
            group.layout().addWidget(card_inner)
            enabled_cb = CheckBox("启用")
            layout.addRow("启用", enabled_cb)
            cred_edit = None
            if cred_field:
                cred_edit = LineEdit()
                cred_edit.setPlaceholderText(f"${cred_field.upper()} 或实际值")
                if cred_field == "token":
                    cred_edit.setEchoMode(LineEdit.EchoMode.Password)
                layout.addRow(cred_field, cred_edit)
            self.inner_layout.addWidget(group)
            self._widgets[source] = {"enabled": enabled_cb, "cred": cred_edit}

        # 说明
        note = QLabel(
            "注：${ENV_VAR} 占位符表示从环境变量读取，不写入明文。\n"
            "MCP 权威源（默认）无需 API key，凭证由 MCP server 统一提供；"
            "切换数据源模式请用「采集任务」Tab 顶部下拉。"
        )
        self.inner_layout.addWidget(note)

        # 保存按钮
        self.save_btn = PushButton("💾 保存配置")
        self.save_btn.clicked.connect(self._save_config)
        self.inner_layout.addWidget(self.save_btn)
        self.inner_layout.addStretch()

        self.scroll_area.setWidget(self.scroll_content)
        outer = QVBoxLayout(self)
        outer.addWidget(self.scroll_area)
        self.setStyleSheet(_SOURCE_SCROLL_STYLE)

    def _load_config(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            logger.error(f"读取 sources_config 失败: {e}")
            return
        sources = cfg.get("sources", {})
        for source, w in self._widgets.items():
            sc = sources.get(source, {})
            w["enabled"].setChecked(sc.get("enabled", False))
            if w["cred"]:
                cred_field_map = {"tushare": "token", "baostock": "user",
                                  "xtquant": "qmt_path", "joinquant": "token",
                                  "custom_api": "api_key"}
                cf = cred_field_map.get(source)
                if cf and cf in sc:
                    w["cred"].setText(str(sc[cf]))

    def _save_config(self):
        # 守卫：重置水印进行中 / 守护进程运行 → 禁止写配置（防配置与实例冲突）
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），"
                                "请等待完成后再保存。")
            return
        if self.mw._daemon_running_in_config(self.mw.config_dir):
            QMessageBox.warning(self, "禁止保存",
                                "采集守护进程正在运行，请先「停止采集」后再保存配置，"
                                "避免运行实例读到半写配置。")
            return
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {"sources": {}}
        sources = cfg.setdefault("sources", {})
        cred_field_map = {"tushare": "token", "baostock": "user",
                          "xtquant": "qmt_path", "joinquant": "token",
                          "custom_api": "api_key"}
        for source, w in self._widgets.items():
            sc = sources.setdefault(source, {})
            sc["enabled"] = w["enabled"].isChecked()
            if w["cred"]:
                cf = cred_field_map.get(source)
                if cf:
                    sc[cf] = w["cred"].text()
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        logger.info(f"数据源配置已保存: {self.config_path}")
        QMessageBox.information(self, "保存成功", "数据源配置已保存")

    def refresh(self):
        self._load_config()
