"""Tab3: 数据浏览（SQL 查询 + 结果表格）"""
from __future__ import annotations

import logging

import pandas as pd
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView, QFileDialog, QSplitter,
    QAbstractItemView)
from qfluentwidgets import (
    ListWidget, PlainTextEdit, PushButton, PrimaryPushButton, TableView)

from ..skin import PageHeader

logger = logging.getLogger(__name__)

# 时间戳列（毫秒）显示时转可读
MS_TIME_COLS = {"time", "ann_date", "end_date", "last_date"}


class PandasTableModel(QAbstractTableModel):
    """DataFrame → QTableView 模型。time 列（ms 时间戳）显示为日期。"""

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self._df = df

    def rowCount(self, parent=QModelIndex()):
        return len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            val = self._df.iloc[index.row(), index.column()]
            col = self._df.columns[index.column()]
            if col in MS_TIME_COLS and pd.notna(val):
                try:
                    return pd.Timestamp(int(val), unit="ms").strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError, TypeError):
                    pass
            if pd.isna(val):
                return ""
            return str(val)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return str(self._df.columns[section])
            else:
                return str(section + 1)
        return None


class BrowserTab(QWidget):
    """数据浏览：左侧表列表 + 右侧 SQL 编辑器 + 结果表格"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._setup_ui()
        self._load_tables()

    def _setup_ui(self):
        layout = QHBoxLayout(self)

        # 左侧表列表
        self.table_list = ListWidget()
        self.table_list.setFixedWidth(200)
        self.table_list.currentTextChanged.connect(self._on_table_selected)
        layout.addWidget(self.table_list)

        # 右侧
        right = QVBoxLayout()

        # 页面头（参考效果图风格）
        right.addWidget(PageHeader(
            "DATA BROWSER", "数据浏览",
            "DuckDB SQL 查询 · 表结构浏览 · 结果导出 CSV"))

        # SQL 编辑器
        sql_label = QLabel("SQL 查询：")
        right.addWidget(sql_label)
        self.sql_edit = PlainTextEdit()
        self.sql_edit.setPlainText("SELECT * FROM stock_daily LIMIT 100")
        self.sql_edit.setMaximumHeight(80)
        self.sql_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 13px;")
        right.addWidget(self.sql_edit)

        # 按钮栏
        btn_bar = QHBoxLayout()
        self.run_btn = PrimaryPushButton("▶ 执行")
        self.run_btn.clicked.connect(self._run_query)
        self.export_btn = PushButton("📤 导出 CSV")
        self.export_btn.clicked.connect(self._export_csv)
        btn_bar.addWidget(self.run_btn)
        btn_bar.addWidget(self.export_btn)
        btn_bar.addStretch()
        self.info_label = QLabel("")
        btn_bar.addWidget(self.info_label)
        right.addLayout(btn_bar)

        # 结果表格
        self.result_table = TableView()
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self.result_table, 1)

        layout.addLayout(right, 1)

    def _load_tables(self):
        tables = self.mw.db_helper.list_tables()
        self.table_list.clear()
        self.table_list.addItems(tables)

    def _on_table_selected(self, table_name):
        self.sql_edit.setPlainText(f"SELECT * FROM {table_name} LIMIT 100")

    def _run_query(self):
        sql = self.sql_edit.toPlainText().strip().rstrip(";")
        if not sql:
            self.info_label.setText("请输入 SQL")
            return
        try:
            df = self.mw.db_helper.query_duckdb(sql)
            self._last_df = df
            model = PandasTableModel(df)
            self.result_table.setModel(model)
            self.result_table.resizeColumnsToContents()
            self.info_label.setText(f"{len(df)} 行 × {len(df.columns)} 列")
        except Exception as e:
            logger.error(f"查询失败: {e}")
            self.info_label.setText(f"❌ {e}")

    def _export_csv(self):
        if not hasattr(self, "_last_df") or self._last_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "", "CSV (*.csv)")
        if path:
            self._last_df.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"已导出 {len(self._last_df)} 行 → {path}")

    def refresh(self):
        self._load_tables()
