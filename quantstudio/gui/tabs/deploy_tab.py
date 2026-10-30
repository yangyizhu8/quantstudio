"""Tab10: 转模拟盘/实盘（qs-deploy M5 薄壳 + PD-0008 快捷模式）

设计文档 docs/ptrade-deploy-pipeline-design.md §7.2 与
docs/ptrade-deploy-quickmode-design.md（PD-0008 D5）：
- **快捷模式**：选本地 PTrade 存量产物（`output/ptrade_export/**/*_ptrade.py`）→
  选执行档位 → 一键转换（配置由内核纯函数 `quickmode` 生成，落
  `output/.qs-deploy-quick/<strategy_id>.yaml`）；
- **高级模式**：沿用 deploy.yaml 手写路径（不删不改，向后兼容）。

薄壳纪律（§7.3 审计检查点）：本文件零转换逻辑，唯一内核调用点 = run_deploy_kernel
（CLI 壳 cmd_deploy 与 parity 测试共用同一函数 → 哈希门禁可机检对等）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QProgressBar, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, ComboBox, GroupHeaderCardWidget, InfoBar, InfoBarPosition,
    LineEdit, PrimaryPushButton, PushButton,
)

from ..skin import PageHeader

logger = logging.getLogger(__name__)

#: 快捷页执行档位（与 config.PROFILES 同序；默认日线尾盘）
QUICK_PROFILES = ("daily_eod_1450", "daily_open_0931", "minute_keep")


def run_deploy_kernel(config_path: str) -> dict:
    """唯一内核调用点（审计检查点：壳内零转换逻辑）。

    CLI（quantstudio.strategy_compiler.cli.cmd_deploy）与本 tab 的 DeployWorker
    共用本函数直调内核 run_deploy——对等判据 = 双端产物 SHA256 逐位一致（§7.3）。
    """
    from quantstudio.ptrade_deploy import run_deploy
    return run_deploy(config_path).to_dict()


class DeployWorker(QThread):
    """转换 worker（仿 PtradeExportWorker：QThread 防止 GUI 冻结）。"""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_err = pyqtSignal(str)

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path

    def run(self):
        try:
            self.progress.emit(10, "P0-P6 流水线运行中...")
            d = run_deploy_kernel(self.config_path)
            self.progress.emit(100, f"完成（{d['status']}）")
            self.finished_ok.emit(d)
        except Exception as e:  # 内核契约「绝不抛异常」，此处仅防御性兜底
            logger.exception("qs-deploy 内核异常（防御性兜底触发）")
            self.finished_err.emit(str(e))


class DeployTab(QWidget):
    """转模拟盘/实盘 tab：快捷模式（选策略）+ 高级模式（deploy.yaml）→ 运行 → 报告。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._strategies = []
        #: 最近一次成功转换的产物目录（PD-0014：「打开输出目录」按钮的定位依据）
        self._last_out_dir = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(PageHeader(
            "QS-DEPLOY", "转模拟盘/实盘",
            "PTrade 回测策略 → 模拟盘/实盘部署代码 · P0-P6 内核流水线 · "
            "与 qs-compile deploy 同内核（产物 SHA256 对等）"))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_quick_page(), "快捷模式")
        self.tabs.addTab(self._build_advanced_page(), "高级模式（deploy.yaml）")
        layout.addWidget(self.tabs)

        action_row = QHBoxLayout()
        self.status_label = BodyLabel("就绪")
        action_row.addWidget(self.status_label, 1)
        layout.addLayout(action_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        layout.addWidget(QLabel("转换报告（逐段状态 / 注记 / 产物）"))
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        layout.addWidget(self.report_view, 1)

        self._refresh_strategies()

    def _build_quick_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        grp = GroupHeaderCardWidget()
        grp.setTitle("本地存量策略（output/ptrade_export/**/*_ptrade.py）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        grp.layout().addWidget(inner)

        self.strategy_combo = ComboBox()
        self.strategy_combo.setMinimumWidth(480)
        row.addWidget(self.strategy_combo, 1)
        refresh_btn = PushButton("刷新扫描")
        refresh_btn.clicked.connect(self._refresh_strategies)
        row.addWidget(refresh_btn)
        outer.addWidget(grp)

        grp2 = GroupHeaderCardWidget()
        grp2.setTitle("执行档位与输出")
        inner2 = QWidget()
        row2 = QHBoxLayout(inner2)
        grp2.layout().addWidget(inner2)
        self.profile_combo = ComboBox()
        self.profile_combo.addItems(list(QUICK_PROFILES))
        self.profile_combo.setCurrentIndex(0)
        row2.addWidget(BodyLabel("执行档位"))
        row2.addWidget(self.profile_combo)
        self.out_hint = BodyLabel("输出：Demo_account_Real_account/<策略名>/")
        row2.addWidget(self.out_hint, 1)
        outer.addWidget(grp2)

        row3 = QHBoxLayout()
        self.quick_btn = PrimaryPushButton("一键转换")
        self.quick_btn.clicked.connect(self._on_quick_clicked)
        row3.addWidget(self.quick_btn)
        self.quick_open_btn = PushButton("打开输出目录")
        self.quick_open_btn.clicked.connect(self._open_out_dir)
        row3.addWidget(self.quick_open_btn)
        row3.addStretch(1)
        outer.addLayout(row3)
        outer.addStretch(1)
        return page

    def _build_advanced_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        grp_cfg = GroupHeaderCardWidget()
        grp_cfg.setTitle("部署配置（deploy.yaml）")
        inner = QWidget()
        row = QHBoxLayout(inner)
        grp_cfg.layout().addWidget(inner)
        self.config_edit = LineEdit()
        self.config_edit.setMinimumWidth(480)
        self.config_edit.setPlaceholderText(
            "deploy.yaml 路径（strategy_id / source / execution.profile 等七段配置）")
        row.addWidget(self.config_edit, 1)
        browse_btn = PushButton("浏览...")
        browse_btn.clicked.connect(self._browse_config)
        row.addWidget(browse_btn)
        outer.addWidget(grp_cfg)

        row2 = QHBoxLayout()
        self.run_btn = PrimaryPushButton("开始转换")
        self.run_btn.clicked.connect(self._on_start_clicked)
        row2.addWidget(self.run_btn)
        self.adv_open_btn = PushButton("打开输出目录")
        self.adv_open_btn.clicked.connect(self._open_out_dir)
        row2.addWidget(self.adv_open_btn)
        row2.addStretch(1)
        outer.addLayout(row2)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # 快捷模式
    # ------------------------------------------------------------------
    def _refresh_strategies(self):
        """扫描本地存量产物（失败降级为空列表，不阻塞 UI）。"""
        from quantstudio.ptrade_deploy.quickmode import discover_strategies
        try:
            self._strategies = discover_strategies()
        except Exception:
            logger.exception("策略扫描失败（降级为空列表）")
            self._strategies = []
        self.strategy_combo.clear()
        for item in self._strategies:
            self.strategy_combo.addItem(
                f"{item['name']}（{item['strategy_id']}）", userData=item)
        if not hasattr(self, "status_label"):
            return
        if self._strategies:
            self.status_label.setText(f"发现 {len(self._strategies)} 个可部署产物")
        else:
            self.status_label.setText("未发现 *_ptrade.py 产物（先做「转 PTrade」）")

    def _on_quick_clicked(self):
        item = self.strategy_combo.currentData()
        if not item:
            self.status_label.setText("请先选择本地策略")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("转换进行中，请等待完成")
            return
        from quantstudio.ptrade_deploy.quickmode import write_quick_config
        try:
            profile = (self.profile_combo.currentText() or QUICK_PROFILES[0]).strip()
            config_path = write_quick_config(
                item["name"], item["product_path"], profile=profile)
        except Exception as exc:  # 配置生成失败不进入 worker
            logger.exception("快捷配置生成失败")
            self.status_label.setText(f"配置生成失败: {exc}")
            return
        self.status_label.setText(f"配置已生成：{config_path}")
        self._start(str(config_path))

    def _start(self, config_path: str):
        self.quick_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.status_label.setText("运行中...")
        self._worker = DeployWorker(config_path)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.finished_err.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------
    # 高级模式（原行为不变）
    # ------------------------------------------------------------------
    def _browse_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 deploy.yaml", "", "YAML (*.yaml *.yml)")
        if path:
            self.config_edit.setText(path)

    def _on_start_clicked(self):
        config_path = self.config_edit.text().strip()
        if not config_path:
            self.status_label.setText("请先选择 deploy.yaml")
            return
        if not Path(config_path).exists():
            self.status_label.setText(f"文件不存在: {config_path}")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("转换进行中，请等待完成")
            return
        self._start(config_path)

    # ------------------------------------------------------------------
    # 输出目录定位（PD-0014）
    # ------------------------------------------------------------------

    def _open_out_dir(self):
        """打开输出目录：无目录/不存在/打开失败都给用户反馈（对齐导出 PTrade 页实现）。

        参考实现：`quantstudio/gui/tabs/ptrade_export_tab.py::_open_out_dir`。
        目录来源＝最近一次成功转换的产物目录（`_on_finished` 由 artifacts 派生）。
        """
        if not self._last_out_dir:
            InfoBar.info("无输出目录", "请先执行一次转换", parent=self,
                         position=InfoBarPosition.TOP)
            return
        d = Path(self._last_out_dir).resolve()
        if not d.exists():
            InfoBar.error("输出目录不存在", str(d), parent=self,
                          position=InfoBarPosition.TOP)
            return
        try:
            os.startfile(str(d))  # noqa: S606 Windows only
        except OSError as e:
            InfoBar.error("打开失败", f"{d}: {e}", parent=self,
                          position=InfoBarPosition.TOP)

    @staticmethod
    def _out_dir_from_artifacts(artifacts) -> object:
        """由内核返回的 artifacts 派生产物目录（取任一产物所在目录）。

        PD-0014：产物路径由内核给出（绝对路径），此处只取其父目录——
        内核（preflight/transformer/equiv/gates/runner/deliver）零改动。
        """
        if not isinstance(artifacts, dict):
            return None
        for key in ("product", "report", "runbook"):
            p = artifacts.get(key)
            if p:
                try:
                    return Path(p).parent
                except (TypeError, ValueError):
                    continue
        return None

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------
    def _on_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_finished(self, d: dict):
        self.quick_btn.setEnabled(True)
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"完成（{d['status']}）")
        # PD-0014：缓存本次产物目录，供「打开输出目录」按钮定位（P6 SKIPPED 时不更新）
        out_dir = self._out_dir_from_artifacts(d.get("artifacts"))
        if out_dir is not None:
            self._last_out_dir = out_dir
        lines = []
        for stage, payload in d.get("stages", {}).items():
            status = payload.get("status") if isinstance(payload, dict) else payload
            line = f"[{stage}] {status}"
            if isinstance(payload, dict) and payload.get("reason"):
                line += f" — {payload['reason']}"
            lines.append(line)
        for note in d.get("notes") or []:
            lines.append(f"[note] {note}")
        for key, p in (d.get("artifacts") or {}).items():
            lines.append(f"[artifact:{key}] {p}")
        self.report_view.setPlainText("\n".join(lines) or "(无报告内容)")

    def _on_error(self, msg: str):
        self.quick_btn.setEnabled(True)
        self.run_btn.setEnabled(True)
        self.status_label.setText(f"失败: {msg}")
        self.report_view.setPlainText(f"ERROR: {msg}")
