"""Tab4: 隔离区管理（统计 + 筛选 + 标记修复 + 归档）"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem,
    QHeaderView, QMessageBox, QAbstractItemView)
from qfluentwidgets import (
    PushButton, TableWidget, ComboBox, PlainTextEdit, GroupHeaderCardWidget)

from ..skin import PageHeader

logger = logging.getLogger(__name__)
from quantstudio._paths import quarantine_db_path


class QuarantineTab(QWidget):
    """隔离区管理：统计 + 筛选 + 查看原始数据 + 标记修复 + 归档"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 页面头（参考效果图风格）
        layout.addWidget(PageHeader(
            "QUARANTINE", "隔离区",
            "脏数据隔离 · 修复标记 · 归档管理"))

        # 统计标签
        self.stats_label = QLabel("统计: 加载中...")
        layout.addWidget(self.stats_label)

        # 筛选栏
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("表:"))
        self.table_filter = ComboBox()
        self.table_filter.addItem("全部", userData="")
        self.table_filter.currentIndexChanged.connect(self.refresh)
        filter_bar.addWidget(self.table_filter)
        filter_bar.addWidget(QLabel("状态:"))
        self.status_filter = ComboBox()
        self.status_filter.addItems(["全部", "pending_repair", "fixed", "replayed", "archived"])
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filter_bar.addWidget(self.status_filter)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # 隔离区数据表格
        group = GroupHeaderCardWidget()
        group.setTitle("隔离区数据")
        glayout = group.layout()  # reuse the card's existing QVBoxLayout
        self.table = TableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "批次ID", "表", "源", "失败规则", "状态", "入隔时间"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)
        self.table.itemSelectionChanged.connect(self._on_select)
        glayout.addWidget(self.table)

        # 原始数据展示
        self.payload_text = PlainTextEdit()
        self.payload_text.setReadOnly(True)
        self.payload_text.setMaximumHeight(120)
        glayout.addWidget(QLabel("原始数据 (original_payload):"))
        glayout.addWidget(self.payload_text)
        layout.addWidget(group, 2)

        # 操作按钮
        btn_bar = QHBoxLayout()
        self.fix_btn = PushButton("✅ 标记已修复")
        self.fix_btn.clicked.connect(self._mark_fixed)
        self.archive_btn = PushButton("📦 归档过期(>30天)")
        self.archive_btn.clicked.connect(self._archive_expired)
        self.refresh_btn = PushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_bar.addWidget(self.fix_btn)
        btn_bar.addWidget(self.archive_btn)
        btn_bar.addWidget(self.refresh_btn)
        btn_bar.addStretch()
        layout.addLayout(btn_bar)

    def _get_quarantine(self):
        from quantstudio.pipeline.quarantine import Quarantine
        return Quarantine(quarantine_db_path())

    def refresh(self):
        try:
            q = self._get_quarantine()
            # 统计
            stats = q.stats()
            stats_text = " | ".join(f"{k}: {v}" for k, v in stats.items()) or "无数据"
            self.stats_label.setText(f"统计: {stats_text}")

            # 筛选（用 query_quarantine_all 支持全部状态）
            table_filter = self.table_filter.currentData()
            status = self.status_filter.currentText()
            df = self.mw.db_helper.query_quarantine_all(
                status=status, table=table_filter if table_filter else None)

            # 更新表名筛选下拉（首次）
            if self.table_filter.count() <= 1 and "table_name" in df.columns:
                for t in df["table_name"].unique():
                    self.table_filter.addItem(t, userData=t)

            self._cached_df = df  # 缓存供 _on_select 使用（P1-4）
            self.table.setRowCount(len(df))
            for i, (_, r) in enumerate(df.iterrows()):
                self.table.setItem(i, 0, QTableWidgetItem(str(r.get("quarantine_id", ""))))
                self.table.setItem(i, 1, QTableWidgetItem(str(r.get("batch_id", ""))))
                self.table.setItem(i, 2, QTableWidgetItem(str(r.get("table_name", ""))))
                self.table.setItem(i, 3, QTableWidgetItem(str(r.get("source", ""))))
                self.table.setItem(i, 4, QTableWidgetItem(str(r.get("failed_rules", ""))))
                self.table.setItem(i, 5, QTableWidgetItem(str(r.get("status", ""))))
                self.table.setItem(i, 6, QTableWidgetItem(str(r.get("ingested_at", ""))[:19]))
        except Exception as e:
            logger.error(f"加载隔离区失败: {e}")
            self.stats_label.setText(f"统计: 加载失败 ({e})")

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        # 用缓存数据（P1-4：不再每次全查）
        if not hasattr(self, "_cached_df") or self._cached_df is None:
            return
        qid_item = self.table.item(row, 0)
        if not qid_item:
            return
        try:
            qid = int(qid_item.text())
            row_data = self._cached_df[self._cached_df["quarantine_id"] == qid]
            if len(row_data) > 0:
                self.payload_text.setPlainText(str(row_data.iloc[0].get("original_payload", "")))
        except (ValueError, KeyError):
            pass

    def _mark_fixed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选择行")
            return
        qids = [int(self.table.item(r.row(), 0).text()) for r in rows]
        reply = QMessageBox.question(
            self, "确认", f"标记 {len(qids)} 条为已修复？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            q = self._get_quarantine()
            q.mark_fixed(qids)
            logger.info(f"已标记 {len(qids)} 条为 fixed")
            self.refresh()

    def _archive_expired(self):
        q = self._get_quarantine()
        n = q.archive_expired(30)
        QMessageBox.information(self, "归档完成", f"已归档 {n} 条过期数据")
        self.refresh()
