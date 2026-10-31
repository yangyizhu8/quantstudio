"""数据交付 Tab：整库交付包导出 / 只读校验 / 导入（GUI 薄壳）。

薄壳纪律：本文件只做参数收集与结果展示，业务全部在 workers →
quantstudio.delivery 内核；此处禁止出现任何交付包写盘/删盘逻辑。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFileDialog, QMessageBox, QPlainTextEdit)
from qfluentwidgets import (
    LineEdit, PushButton, PrimaryPushButton, CheckBox, GroupHeaderCardWidget)

from ..workers import (
    DeliveryExportWorker, DeliveryVerifyWorker, DeliveryImportWorker)
from ..skin import PageHeader

logger = logging.getLogger(__name__)

_LOG_MAX_LINES = 2000  # 日志区最大保留行数（超出自动丢弃最旧行）


def _fmt_bytes(size) -> str:
    """字节数 → 人类可读（如 1.5 GB），完成读数展示用。"""
    n = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


class DeliveryTab(QWidget):
    """数据交付单页：导出 / 只读校验 / 导入三卡片 + 进度行 + 日志区。"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._worker = None  # 强引用运行中的 QThread，防被 Python 侧 GC
        self._setup_ui()

    # ── UI 构建 ─────────────────────────────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(PageHeader(
            "DATA DELIVERY", "数据交付",
            "整库交付包：一键导出 / 只读校验 / 导入（含备份与回滚）"))

        # ① 导出：内核会在所选目录下新建时间戳子目录，永不覆盖旧包
        export_card = GroupHeaderCardWidget()
        export_card.setTitle("① 导出（源库 → 交付包）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        export_card.layout().addWidget(inner)
        self.export_edit = LineEdit()
        self.export_edit.setReadOnly(True)
        self.export_edit.setPlaceholderText("先选择导出目录（要求：非同步盘、空间充足、与数据目录不相交）")
        row.addWidget(self.export_edit)
        self.export_browse_btn = PushButton("选择目录")
        self.export_browse_btn.clicked.connect(
            lambda: self._browse_dir(self.export_edit, "选择导出目录"))
        row.addWidget(self.export_browse_btn)
        self.export_btn = PrimaryPushButton("开始导出")
        self.export_btn.clicked.connect(self._do_export)
        row.addWidget(self.export_btn)
        layout.addWidget(export_card)

        # ② 只读校验：manifest 四要素 + 逐文件哈希 + 表结构，全程不写盘
        verify_card = GroupHeaderCardWidget()
        verify_card.setTitle("② 只读校验（不写盘）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        verify_card.layout().addWidget(inner)
        self.import_edit = LineEdit()
        self.import_edit.setReadOnly(True)
        self.import_edit.setPlaceholderText("选择交付包所在目录")
        row.addWidget(self.import_edit)
        self.import_browse_btn = PushButton("选择目录")
        self.import_browse_btn.clicked.connect(
            lambda: self._browse_dir(self.import_edit, "选择交付包目录"))
        row.addWidget(self.import_browse_btn)
        self.verify_btn = PushButton("只读校验")
        self.verify_btn.clicked.connect(self._do_verify)
        row.addWidget(self.verify_btn)
        layout.addWidget(verify_card)

        # ③ 导入：与②共用同一交付包目录；勾选演练则内核仅前四步（零写入）
        import_card = GroupHeaderCardWidget()
        import_card.setTitle("③ 导入（自动备份旧库，失败自动回滚）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        import_card.layout().addWidget(inner)
        self.import_mirror = LineEdit()
        self.import_mirror.setReadOnly(True)
        self.import_mirror.setPlaceholderText("（同②的交付包目录）")
        self.import_edit.textChanged.connect(self.import_mirror.setText)
        row.addWidget(self.import_mirror)
        self.dry_run_cb = CheckBox("仅演练（dry-run，不写盘）")
        row.addWidget(self.dry_run_cb)
        self.import_btn = PushButton("开始导入")
        self.import_btn.clicked.connect(self._do_import)
        row.addWidget(self.import_btn)
        layout.addWidget(import_card)

        # 进度行 + 日志区（块数上限 2000，自动丢最旧）
        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(_LOG_MAX_LINES)
        layout.addWidget(self.log_edit, 1)  # 拉伸吃掉剩余高度

        # SC-010：未选目录时动作按钮呈禁用态（选好即启用；运行期由 _set_busy 统一禁用）
        self.export_edit.textChanged.connect(self._refresh_action_state)
        self.import_edit.textChanged.connect(self._refresh_action_state)
        self._refresh_action_state()

    def _refresh_action_state(self, *_args):
        """按"是否已选目录"刷新三个动作按钮的可用态（空目录即禁用 + 状态行提示）。"""
        has_export = bool(self.export_edit.text().strip())
        has_import = bool(self.import_edit.text().strip())
        self.export_btn.setEnabled(has_export)
        self.verify_btn.setEnabled(has_import)
        self.import_btn.setEnabled(has_import)
        if not (has_export or has_import):
            self.status_label.setText("请先选择目录")

    # ── 通用小件 ────────────────────────────────────────────────────────────
    def _browse_dir(self, edit: LineEdit, title: str):
        d = QFileDialog.getExistingDirectory(self, title)
        if d:
            edit.setText(d)

    def _log(self, msg: str):
        """追加一行日志。"""
        self.log_edit.appendPlainText(msg)

    def _set_busy(self, busy: bool):
        """任一交付操作运行期间禁用全部动作按钮，结束（成功/失败）后恢复。"""
        for btn in (self.export_browse_btn, self.export_btn,
                    self.import_browse_btn, self.verify_btn, self.import_btn):
            btn.setEnabled(not busy)

    def _require_dir(self, text: str, what: str) -> bool:
        """目录未选时弹警告并返回 False。"""
        if not text:
            QMessageBox.warning(self, "提示", f"请先选择{what}目录")
            return False
        return True

    def _wire(self, worker, on_ok):
        """三 worker 通用接线：进度→进度行+日志；成功→on_ok；失败→错误日志。"""
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(on_ok)
        worker.finished_err.connect(self._on_err)
        worker.start()

    # ── 动作入口 ────────────────────────────────────────────────────────────
    def _do_export(self):
        export_dir = self.export_edit.text().strip()
        if not self._require_dir(export_dir, "导出"):
            return
        self._set_busy(True)
        self.status_label.setText("导出进行中…")
        self._log(f"── 开始导出：{export_dir}")
        self._worker = DeliveryExportWorker(export_dir)
        self._wire(self._worker, self._on_export_ok)

    def _do_verify(self):
        import_dir = self.import_edit.text().strip()
        if not self._require_dir(import_dir, "交付包"):
            return
        self._set_busy(True)
        self.status_label.setText("只读校验进行中…")
        self._log(f"── 只读校验：{import_dir}")
        self._worker = DeliveryVerifyWorker(import_dir)
        self._wire(self._worker, self._on_verify_ok)

    def _do_import(self):
        import_dir = self.import_edit.text().strip()
        if not self._require_dir(import_dir, "交付包"):
            return
        dry_run = self.dry_run_cb.isChecked()
        self._set_busy(True)
        self.status_label.setText("导入演练进行中…" if dry_run else "导入进行中…")
        self._log(f"── 开始导入（dry-run 演练）：{import_dir}" if dry_run
                  else f"── 开始导入：{import_dir}")
        self._worker = DeliveryImportWorker(import_dir, dry_run=dry_run)
        self._wire(self._worker, self._on_import_ok)

    # ── 结果槽 ──────────────────────────────────────────────────────────────
    def _on_progress(self, msg: str):
        self.status_label.setText(msg)
        self._log(msg)

    def _on_err(self, err):
        self._set_busy(False)
        self.status_label.setText("❌ 失败（详见日志）")
        self._log(f"❌ {err}")

    def _on_export_ok(self, result: dict):
        self._set_busy(False)
        qualified = result.get("qualified")
        self._log(f"✅ 导出完成：{result.get('bundle_dir', '?')}")
        self._log(f"   体积 {_fmt_bytes(result.get('total_bytes'))}"
                  f"｜耗时 {result.get('duration_sec')}s｜合格 qualified={qualified}")
        self.status_label.setText(f"✅ 导出完成（qualified={qualified}）")

    def _on_verify_ok(self, r: dict):
        self._set_busy(False)
        ok = bool(r.get("ok"))
        tables = r.get("tables") or []
        self._log(f"{'✅' if ok else '❌'} 校验结束：ok={ok}"
                  f"｜files_checked={r.get('files_checked')}｜表数={len(tables)}")
        # 四态汇总（与 CLI 同源：内核 VerifyReport.state_counts，壳层不另算）
        counts = r.get("state_counts") or {}
        if counts:
            order = ("OK", "BEHIND", "AHEAD", "MISSING", "UNKNOWN-SOURCE",
                     "UNKNOWN-BASIS", "NO-WATERMARK-SYSTEM", "EMPTY")
            parts = [f"{k}={counts[k]}" for k in order if counts.get(k)]
            self._log("   状态汇总：" + "  ".join(parts))
        attention = r.get("attention") or []
        if attention:
            self._log("   需关注（水位 vs 数据）：")
            for row in attention[:8]:
                self._log(f"     - {row.get('table')}: {row.get('state')}"
                          f"（水位 {row.get('watermark_last_date') or '-'}"
                          f" / 数据 {row.get('max_date') or '-'}"
                          f" / 基准列 {row.get('date_basis_col')}）")
            if len(attention) > 8:
                self._log(f"     …另有 {len(attention) - 8} 张，详见 --json 的 attention")
        if not ok:
            self._log(f"   failure_kind={r.get('failure_kind')}")
            for reason in (r.get("reasons") or []):
                self._log(f"   - {reason}")
        # 告警只在这里打印一次（worker 不再经 progress 重复播报）
        for warn in (r.get("warnings") or []):
            self._log(f"   ⚠ {warn}")
        self.status_label.setText("✅ 校验通过" if ok else "❌ 校验未通过（详见日志）")

    def _on_import_ok(self, r: dict):
        self._set_busy(False)
        ok = bool(r.get("ok"))
        backup = r.get("backup") or {}
        reverify = r.get("reverify") or {}
        self._log(f"{'✅' if ok else '❌'} 导入结束：mode={r.get('mode') or '?'}"
                  f"｜exit_code={r.get('exit_code')}")
        if backup.get("dir"):
            self._log(f"   备份目录（确认新库正常前勿删）：{backup.get('dir')}")
        if "can_incremental" in reverify:
            self._log(f"   复验：{reverify.get('summary', '')}"
                      f"｜can_incremental={reverify.get('can_incremental')}")
        if not ok:
            self._log(f"   failure_kind={r.get('failure_kind')}")
            for reason in (r.get("reasons") or []):
                self._log(f"   - {reason}")
        for warn in (r.get("warnings") or []):
            self._log(f"   ⚠ {warn}")
        self.status_label.setText("✅ 导入完成" if ok else "❌ 导入未完成（详见日志）")

    def refresh(self):
        pass
