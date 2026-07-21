"""Tab1: 采集任务（核心 Tab，80% 使用场景）"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem,
    QHeaderView, QLabel, QMessageBox, QAbstractItemView)
from qfluentwidgets import (
    PushButton, TableWidget, GroupHeaderCardWidget)

from ..workers import TaskWorker, DaemonWorker
from quantstudio._paths import db_path
from quantstudio.pipeline.source_capabilities import capability_matrix

logger = logging.getLogger(__name__)

# 数据源 × 表 能力矩阵（基于 adapter 代码实测确认，不依赖运行时实例化）
# ✅=支持（fetch_table 有对应分支 + supports_task 不拒绝）
# 行情类表：5 源全支持（stock_daily/etf_daily/index_daily）
# 估值/财务/报表/行业类表：仅部分源支持（tushare 主，baostock/akshare 备，mootdx/xtquant 无）
SOURCE_CAPABILITY = capability_matrix()


class TaskTab(QWidget):
    """采集任务管理：加载任务列表 + 执行单任务 + 全部执行 + 刷新 + 重置水位
    + 常驻增量拉取进程开关（状态切换按钮）"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.collector = None
        self.tasks = []
        self.daemon_worker = None  # 常驻进程 worker（非 None=运行中）
        # 运行态集合：{任务名: 模式('full_range'/'incremental')}
        # 作用：标记正在执行的任务，使 refresh() 重建表格后按钮仍保持
        # 禁用 + "执行中..." 状态，防止导航切走再切回导致按钮"复活"被反复点击。
        self._running_tasks = {}
        # 任务状态集合：{任务名: 状态文案}，用于状态列展示“就绪/执行中/成功/失败”
        self._task_status = {}
        self._setup_ui()
        self._load_tasks()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        # ★ 常驻增量拉取开关（状态切换按钮）
        self.daemon_btn = PushButton("🔴 进程常驻增量拉取：打开")
        self.daemon_btn.clicked.connect(self._toggle_daemon)
        toolbar.addWidget(self.daemon_btn)

        toolbar.addStretch()
        self.refresh_btn = PushButton("🔄 刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        self.run_all_btn = PushButton("▶ 全部执行")
        self.run_all_btn.clicked.connect(self._run_all)
        self.reset_wm_btn = PushButton("⏮ 重置水位")
        self.reset_wm_btn.clicked.connect(self._reset_watermark)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.run_all_btn)
        toolbar.addWidget(self.reset_wm_btn)
        self.status_label = QLabel("")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        # 任务列表表格
        task_group = GroupHeaderCardWidget()
        task_group.setTitle("采集任务")
        task_layout = task_group.layout()  # reuse the card's existing QVBoxLayout
        self.task_table = TableWidget()
        self.task_table.setColumnCount(7)
        self.task_table.setHorizontalHeaderLabels(
            ["任务名", "数据源", "目标表", "频率", "水位线", "状态", "操作"])
        header = self.task_table.horizontalHeader()
        # 操作列固定宽（容纳"全量拉取"+"增量拉取"两按钮），其余列自适应拉伸
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)  # 操作列固定
        self.task_table.setColumnWidth(6, 240)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        task_layout.addWidget(self.task_table)
        layout.addWidget(task_group, 3)

        # 批次审计表格
        audit_group = GroupHeaderCardWidget()
        audit_group.setTitle("最近批次审计")
        audit_layout = audit_group.layout()  # reuse the card's existing QVBoxLayout
        self.audit_table = TableWidget()
        self.audit_table.setColumnCount(7)
        self.audit_table.setHorizontalHeaderLabels(
            ["批次ID", "任务", "源", "raw", "passed", "written", "状态"])
        self.audit_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        audit_layout.addWidget(self.audit_table)
        layout.addWidget(audit_group, 2)

    def _load_tasks(self):
        """读 collector_tasks.json 的 tasks 数组"""
        tasks_path = self.mw.config_dir / "collector_tasks.json"
        try:
            with tasks_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 只加载 enabled=true 且非 hidden 的任务（hidden 任务如图可见但不在采集面板中）
            all_tasks = cfg.get("tasks", [])
            self.tasks = [t for t in all_tasks if t.get("enabled", True) and not t.get("hidden", False)]
            visible_task_names = {t.get("name", "") for t in self.tasks if t.get("name", "")}
            self._running_tasks = {k: v for k, v in self._running_tasks.items() if k in visible_task_names}
            self._task_status = {k: v for k, v in self._task_status.items() if k in visible_task_names}
            for task_name in visible_task_names:
                self._task_status.setdefault(task_name, "就绪")
        except Exception as e:
            logger.error(f"加载任务配置失败: {e}")
            self.tasks = []
        self._render_tasks()

    def _render_tasks(self):
        # 查一次水位缓存（P1-3：不再每任务查一次 DuckDB）
        wms = self.mw.db_helper.get_watermarks()
        self.task_table.setRowCount(len(self.tasks))
        for i, task in enumerate(self.tasks):
            self.task_table.setItem(i, 0, QTableWidgetItem(task.get("name", "")))
            # 数据源列：数据源由框架自动选择，用户无需配置
            # hidden 任务已在上层过滤不显示，这里只显示用户可见任务
            pri = task.get("source_priority") or []
            effective_source = (pri[0] if pri else None) or task.get("source") or "tushare"
            source_item = QTableWidgetItem(effective_source)
            source_item.setToolTip("数据源由 collector_tasks.json 的 source / source_priority 决定（框架实际生效）。\n切换权威源请直接编辑该配置文件后保存。")
            self.task_table.setItem(i, 1, source_item)
            table_name = task.get("table", "")
            self.task_table.setItem(i, 2, QTableWidgetItem(table_name))
            self.task_table.setItem(i, 3, QTableWidgetItem(task.get("freq", "daily")))
            # 水位线（从缓存的 wms 查）
            wm = self._get_watermark_cached(wms, effective_source, table_name, task.get("freq","daily"))
            self.task_table.setItem(i, 4, QTableWidgetItem(wm))
            task_name = task.get("name", "")
            running_mode = self._running_tasks.get(task_name)
            status_text = self._get_status_text(task_name, running_mode)
            self.task_table.setItem(i, 5, QTableWidgetItem(status_text))
            # 操作栏：全量拉取 + 增量拉取 两个按钮（替代原单个"▶ 执行"）
            op_widget = QWidget()
            op_layout = QHBoxLayout(op_widget)
            op_layout.setContentsMargins(2, 2, 2, 2)
            op_layout.setSpacing(4)
            full_btn = PushButton("全量拉取")
            full_btn.setToolTip(f"全量拉取：读取配置的 start_date~end_date")
            inc_btn = PushButton("增量拉取")
            inc_btn.setToolTip("增量拉取：水位线 → 今天")
            # 运行态：若该任务正在执行，则两按钮禁用，并把执行中的那个标"执行中..."
            self._apply_task_button_state(full_btn, inc_btn, running_mode)
            full_btn.clicked.connect(
                lambda _, t=task, fb=full_btn, ib=inc_btn: self._run_single(t, "full_range", fb, ib)
            )
            inc_btn.clicked.connect(
                lambda _, t=task, fb=full_btn, ib=inc_btn: self._run_single(t, "incremental", fb, ib)
            )
            op_layout.addWidget(full_btn)
            op_layout.addWidget(inc_btn)
            self.task_table.setCellWidget(i, 6, op_widget)

    def _apply_task_button_state(self, full_btn: PushButton, inc_btn: PushButton, running_mode: str | None):
        """统一维护单任务两颗按钮的文案/可点击状态。"""
        full_btn.setText("执行中..." if running_mode == "full_range" else "全量拉取")
        inc_btn.setText("执行中..." if running_mode == "incremental" else "增量拉取")
        is_idle = running_mode is None
        full_btn.setEnabled(is_idle)
        inc_btn.setEnabled(is_idle)

    def _get_status_text(self, task_name: str, running_mode: str | None = None) -> str:
        """返回状态列文案；运行中的任务优先展示执行中状态。"""
        if running_mode == "full_range":
            return "全量执行中"
        if running_mode == "incremental":
            return "增量执行中"
        return self._task_status.get(task_name, "就绪")

    def _set_task_status(self, task_name: str, status_text: str):
        """更新任务状态缓存，并同步刷新表格中的状态列。"""
        if task_name:
            self._task_status[task_name] = status_text
        row = self._find_task_row(task_name)
        if row is None:
            return
        item = self.task_table.item(row, 5)
        if item is None:
            self.task_table.setItem(row, 5, QTableWidgetItem(status_text))
        else:
            item.setText(status_text)

    def _get_watermark_cached(self, wms, source, table, freq) -> str:
        """从已查的水位 DataFrame 取值（不再每行查 DuckDB）"""
        try:
            if len(wms) == 0:
                return "无"
            row = wms[(wms["source"]==source) & (wms["table_name"]==table) & (wms["freq"]==freq)]
            if len(row) == 0:
                return "无"
            import datetime
            ts = int(row.iloc[0]["last_date"])
            return datetime.datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d")
        except Exception:
            return "?"

    def _get_collector(self):
        """延迟初始化 ResidentCollector（首次执行时创建）"""
        if self.collector is None:
            from quantstudio.pipeline.daemon import ResidentCollector
            ROOT = self.mw.root_path
            self.collector = ResidentCollector.from_configs(
                ROOT / "config" / "data_config.json",
                ROOT / "config" / "sources_config.json",
                ROOT / "config" / "collector_tasks.json",
                ROOT / "config" / "alignment_rules.json")
            logger.info("ResidentCollector 初始化完成")
        return self.collector

    # ---------------- 常驻增量拉取开关 ----------------
    def _toggle_daemon(self):
        """切换常驻增量拉取进程的 开/停"""
        if self.daemon_worker is not None:
            # 当前运行中 → 停止
            self._stop_daemon()
        else:
            # 当前停止 → 启动
            self._start_daemon()

    def _start_daemon(self):
        """启动常驻增量拉取进程"""
        collector = self._get_collector()
        collector._running = True
        self.daemon_worker = DaemonWorker(collector)
        self.daemon_worker.progress.connect(lambda msg: self.status_label.setText(msg))
        # 注意：daemon_worker 持续运行不 emit finished，不放 hold_worker（不会被自动释放）
        self.daemon_worker.start()
        self._update_daemon_btn(running=True)

    def _stop_daemon(self):
        """停止常驻增量拉取进程"""
        if self.daemon_worker is not None:
            self.daemon_worker.stop()
            self.daemon_worker.wait(5000)  # 等最多5秒优雅退出
            self.daemon_worker = None
        self._update_daemon_btn(running=False)

    def _update_daemon_btn(self, running: bool):
        """更新开关按钮的显示状态"""
        if running:
            self.daemon_btn.setText("🟢 进程常驻增量拉取：运行中（点击停止）")
        else:
            self.daemon_btn.setText("🔴 进程常驻增量拉取：已停止（点击启动）")

    def _run_single(self, task: dict, mode: str, full_btn: PushButton, inc_btn: PushButton):
        """执行单个采集任务（后台 TaskWorker）。
        mode: 'full_range'（全量，读配置日期）/ 'incremental'（增量，水位线→今天）
        full_btn/inc_btn: 两个按钮引用，执行时互禁。"""
        table = task.get("table", "")
        freq = task.get("freq", "daily")

        try:
            chain = self._get_collector().resolve_source_chain(task)
        except Exception as e:
            QMessageBox.warning(self, "数据源检查失败", str(e))
            return
        if not chain:
            QMessageBox.warning(
                self, "无可用数据源",
                f"任务 {table}/{freq} 没有已启用且实现该接口的数据源?\n\n"
                "请检查 source_priority、数据源启用状态和依赖安装。")
            return

        if mode == "full_range":
            start = task.get("start_date", "").strip()
            if not start:
                QMessageBox.warning(self, "缺少开始日期",
                    f"全量拉取需要配置 start_date。\n\n"
                    f"请在「配置编辑」标签页为任务 '{task.get('name','?')}' 设置开始日期。")
                return

        # 互禁：登记运行态（refresh 重建表格后仍保持禁用）+ 即时禁用两按钮
        task_name = task.get("name", "")
        self._running_tasks[task_name] = mode
        self._set_task_status(task_name, self._get_status_text(task_name, mode))
        self._apply_task_button_state(full_btn, inc_btn, mode)

        mode_label = "全量" if mode == "full_range" else "增量"
        self.status_label.setText(f"执行中({mode_label}): {task['name']}...")
        try:
            collector = self._get_collector()
            worker = TaskWorker(task, collector, mode=mode)
            worker.progress.connect(lambda msg: self.status_label.setText(msg))
            worker.finished_ok.connect(lambda res: self._on_task_done(task, True, res))
            worker.finished_err.connect(lambda err: self._on_task_done(task, False, err))
            self.mw.hold_worker(worker)
            worker.start()
        except Exception as e:
            logger.exception("启动采集任务失败: %s", task_name)
            self._running_tasks.pop(task_name, None)
            self._set_task_status(task_name, "失败")
            self._apply_task_button_state(full_btn, inc_btn, None)
            self.status_label.setText(f"❌ 启动失败: {task_name}")
            QMessageBox.critical(self, "启动失败", f"任务 '{task_name}' 启动失败：\n\n{type(e).__name__}: {e}")

    def _get_source_config(self, source: str) -> dict:
        """从 sources_config.json 读取指定数据源的配置（含 enabled 状态）"""
        try:
            path = self.mw.config_dir / "sources_config.json"
            with path.open("r", encoding="utf-8") as f:
                sc = json.load(f)
            return sc.get("sources", {}).get(source)
        except Exception:
            return None

    def _check_source_supports(self, source: str, table: str, freq: str) -> str:
        """检查数据源是否支持该任务。返回错误消息（空串=支持）。"""
        try:
            adapter = self._get_collector()._get_adapter(source)
            # 1. 检查表+频率支持
            ok, reason = adapter.supports_task(table, freq)
            if not ok:
                return f"当前配置的数据源 '{source}' 不支持该任务：\n\n{reason}\n\n请在配置编辑中修改 source 字段，更换为支持的数据源。"
            # 2. 检查复权支持（stock_daily/stock_minutes 需要 8 个复权字段）
            if table in ("stock_daily", "stock_minutes") and not adapter.supports_qfq():
                reply = QMessageBox.question(
                    self, "数据源不支持复权",
                    f"数据源 '{source}' 不支持复权价格计算：\n\n"
                    f"  · 该源返回不复权原始价\n"
                    f"  · 8 个复权字段（open_front/close_front 等）将为 NULL\n\n"
                    f"是否仍要继续执行？（复权字段留 NULL）\n"
                    f"如需复权数据，请改用 baostock / tushare / xtquant。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return f"用户取消：{source} 不支持复权，请更换数据源。"
            return ""
        except Exception as e:
            err_str = str(e)
            # 区分常见的初始化失败原因，给出精准指引
            if "No module named" in err_str or "未安装" in err_str:
                mod_hint = {
                    "xtquant": "xtquant 需安装 miniQMT 客户端，并将 xtquant 库路径加入 PYTHONPATH",
                    "baostock": "baostock 库未安装，请运行 pip install baostock",
                    "akshare": "akshare 库未安装，请运行 pip install akshare",
                    "mootdx": "mootdx 库未安装，请运行 pip install mootdx",
                    "efinance": "efinance 库未安装，请运行 pip install efinance",
                }
                hint = ""
                for mod, h in mod_hint.items():
                    if mod in err_str:
                        hint = h
                        break
                return f"数据源 '{source}' 所需的 Python 库未安装。\n\n{hint}\n\n错误详情：{err_str}"
            elif "login" in err_str.lower() or "登录" in err_str:
                return f"数据源 '{source}' 登录失败：\n\n{e}\n\n请检查凭证配置（token/账号密码）是否正确。"
            else:
                return f"数据源 '{source}' 初始化失败：\n\n{e}\n\n请在配置编辑器中检查该数据源的配置。"

    def _find_task_row(self, task_name: str):
        """根据任务名找表格行号"""
        for i in range(self.task_table.rowCount()):
            item = self.task_table.item(i, 0)
            if item and item.text() == task_name:
                return i
        return None

    def _run_all(self):
        """全部执行（串行，防限流）。默认增量模式。"""
        if not self.tasks:
            return
        self.run_all_btn.setEnabled(False)
        self.run_all_btn.setText("⏳ 全部执行中...")
        self._run_queue = list(self.tasks)
        self._run_next_in_queue()

    def _run_next_in_queue(self):
        if not getattr(self, "_run_queue", None):
            quality_ok = self._get_collector()._run_full_quality_audit()
            self.run_all_btn.setEnabled(True)
            self.run_all_btn.setText("▶ 全部执行")
            self.status_label.setText("全部执行完成" if quality_ok else "❌ 全部执行完成，但质量检查未通过")
            self.refresh()
            return
        task = self._run_queue.pop(0)
        # Use the same fallback chain as the collector. Do not reject a task
        # merely because its preferred source is disabled when a fallback works.
        if not self._get_collector().resolve_source_chain(task):
            logger.warning(f"跳过 {task['name']}: 无已启用且支持该任务的数据源")
            self.status_label.setText(f"⏭ 跳过 {task['name']}（无可用数据源）")
            self._run_next_in_queue()
            return
        task_name = task.get("name", "")
        self._running_tasks[task_name] = "incremental"
        self._set_task_status(task_name, self._get_status_text(task_name, "incremental"))
        self.refresh()
        try:
            collector = self._get_collector()
            worker = TaskWorker(task, collector, mode="incremental",
                                run_quality_audit=False)  # 队列结束后统一审计
            worker.progress.connect(lambda msg: self.status_label.setText(msg))
            worker.finished_ok.connect(lambda res, t=task: self._on_run_all_task_done(t, True, res))
            worker.finished_err.connect(lambda err, t=task: self._on_run_all_task_done(t, False, err))
            self.mw.hold_worker(worker)
            worker.start()
        except Exception as e:
            logger.exception("全部执行启动失败: %s", task_name)
            self._running_tasks.pop(task_name, None)
            self._set_task_status(task_name, "失败")
            self.status_label.setText(f"❌ 启动失败: {task_name}")
            self.refresh()
            self._run_next_in_queue()

    def _on_task_done(self, task: dict, ok: bool, result):
        task_name = task.get("name", "")
        if ok:
            logger.info(f"任务 {task_name} 完成: {result}")
        else:
            logger.error(f"任务 {task_name} 失败: {result}")
        self.status_label.setText(f"{'✅' if ok else '❌'} {task_name}")
        # 清除运行态：refresh() 重建按钮时该任务恢复可点击
        self._running_tasks.pop(task_name, None)
        self._set_task_status(task_name, "成功" if ok else "失败")
        self.refresh()

    def _on_run_all_task_done(self, task: dict, ok: bool, result):
        """全部执行模式下的单任务收尾：更新状态后继续队列。"""
        self._on_task_done(task, ok, result)
        self._run_next_in_queue()

    def _reset_watermark(self):
        """重置水位线（下次全量拉取）"""
        reply = QMessageBox.question(
            self, "确认重置水位",
            "重置后下次采集将从 2020-01-01 全量拉取，确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import duckdb
            db_path_str = str(db_path())
            with duckdb.connect(db_path_str) as conn:
                conn.execute("DELETE FROM source_watermark")
            logger.info("水位线已重置（下次全量拉取）")
            self.refresh()
        except Exception as e:
            logger.error(f"重置水位失败: {e}")

    def refresh(self):
        """刷新任务列表（重新从 collector_tasks.json 加载配置 + 水位线 + 批次审计 + 按钮状态）"""
        self._load_tasks()  # 重新从配置文件加载（修复：配置编辑器改 source 后刷新可生效）
        self._update_daemon_btn(running=(self.daemon_worker is not None))
        self._render_tasks()
        # 批次审计
        try:
            audits = self.mw.db_helper.query_batch_audit(20)
            self.audit_table.setRowCount(len(audits))
            for i, (_, r) in enumerate(audits.iterrows()):
                self.audit_table.setItem(i, 0, QTableWidgetItem(str(r.get("batch_id",""))))
                self.audit_table.setItem(i, 1, QTableWidgetItem(str(r.get("task_name",""))))
                self.audit_table.setItem(i, 2, QTableWidgetItem(str(r.get("source",""))))
                self.audit_table.setItem(i, 3, QTableWidgetItem(str(r.get("rows_raw",""))))
                self.audit_table.setItem(i, 4, QTableWidgetItem(str(r.get("rows_passed",""))))
                self.audit_table.setItem(i, 5, QTableWidgetItem(str(r.get("rows_written",""))))
                self.audit_table.setItem(i, 6, QTableWidgetItem(str(r.get("status",""))))
        except Exception:
            self.audit_table.setRowCount(0)
