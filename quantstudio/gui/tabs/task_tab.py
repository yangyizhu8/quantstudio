"""Tab1: 采集任务（核心 Tab，80% 使用场景）"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidgetItem,
    QHeaderView, QLabel, QMessageBox, QAbstractItemView)
from qfluentwidgets import (
    PushButton, TableWidget, GroupHeaderCardWidget, StateToolTip, ComboBox)

from ..workers import LockedTaskWorker, LockedRunAllWorker
from ..daemon_process import (
    is_daemon_running, get_daemon_status, start_daemon_subprocess,
    request_graceful_stop, force_kill_daemon, read_bootstrap_log_tail,
    check_db_openable,
)
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
        self.collector = None  # DEPRECATED v3：手动拉取改用 LockedTaskWorker 内部 from_configs
        self.tasks = []
        # v3 daemon 子进程状态（取代旧 DaemonWorker/QThread）
        self._daemon_token = None           # GUI 启动时生成的 token（握手用）
        self._daemon_proc = None            # subprocess.Popen 句柄
        self._daemon_state = "stopped"      # stopped|starting|running|stop_requested|force_stopping
        self._start_handshake_elapsed = 0   # 启动握手计时（500ms × 次数）
        self._stop_wait_elapsed = 0         # 停止等待计时（2s × 次数）
        # 运行态集合：{任务名: 模式('full_range'/'incremental')}
        # 作用：标记正在执行的任务，使 refresh() 重建表格后按钮仍保持
        # 禁用 + "执行中..." 状态，防止导航切走再切回导致按钮"复活"被反复点击。
        self._running_tasks = {}
        # 任务状态集合：{任务名: 状态文案}，用于状态列展示“就绪/执行中/成功/失败”
        self._task_status = {}
        self._setup_ui()
        self._load_tasks()
        # v3：低频状态同步 QTimer（3s 轮询 daemon status）
        self._daemon_poll_timer = QTimer(self)
        self._daemon_poll_timer.setInterval(3000)
        self._daemon_poll_timer.timeout.connect(self._on_daemon_poll)
        # 启动握手/停止等待专用 QTimer（500ms / 2s）
        self._handshake_timer = QTimer(self)
        self._handshake_timer.setInterval(500)
        self._handshake_timer.timeout.connect(self._on_handshake_tick)
        self._stop_wait_timer = QTimer(self)
        self._stop_wait_timer.setInterval(2000)
        self._stop_wait_timer.timeout.connect(self._on_stop_wait_tick)
        # GUI 启动时从 status 文件恢复按钮状态（关 GUI 后重开能显示"运行中"）
        self._sync_daemon_state()
        if self._daemon_state == "running":
            self._daemon_poll_timer.start()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ---- 数据源模式（profile）切换行 ----
        # 全应用唯一真源入口，切换 config_dir（MCP默认 ↔ 传统多源）。
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("数据源模式："))
        self.data_source_combo = ComboBox()
        for value, label in self.mw.profile_options():
            self.data_source_combo.addItem(label, userData=value)
        # 设置默认选中为当前模式
        cur = self.mw.current_profile
        idx = self.data_source_combo.findData(cur)
        if idx >= 0:
            self.data_source_combo.setCurrentIndex(idx)
        self.data_source_combo.currentIndexChanged.connect(self._on_data_source_changed)
        mode_row.addWidget(self.data_source_combo)
        self.mode_hint_label = QLabel("")
        self.mode_hint_label.setText(
            "统一正式库：data/quantstudio.db（采集与QFQ同库）")
        mode_row.addWidget(self.mode_hint_label)
        mode_row.addStretch()
        layout.addLayout(mode_row)

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

    def _on_data_source_changed(self, index: int):
        """数据源模式下拉变化 → 委托 MainWindow 执行切换（含守卫+确认+刷新）。"""
        value = self.data_source_combo.itemData(index)
        self.mw.apply_data_source_mode(value, self.data_source_combo)

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
            full_btn.setObjectName("fullBtn")  # 供 _apply_all_task_buttons_state 定位
            full_btn.setToolTip(f"全量拉取：读取配置的 start_date~end_date")
            inc_btn = PushButton("增量拉取")
            inc_btn.setObjectName("incBtn")  # 供 _apply_all_task_buttons_state 定位
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
        """统一维护单任务两颗按钮的文案/可点击状态。

        可点击态取决于“本任务是否在执行”：本任务 idle 才允许点击。
        若当前有其它任务正在执行（_running_tasks 非空），本任务即便 idle
        也要禁用，避免用户一次点击多个采集任务并行拉取。
        """
        full_btn.setText("执行中..." if running_mode == "full_range" else "全量拉取")
        inc_btn.setText("执行中..." if running_mode == "incremental" else "增量拉取")
        is_idle = running_mode is None
        # 本任务 idle 且 全局无任何任务在执行 时才允许点击
        can_click = is_idle and not self._running_tasks
        full_btn.setEnabled(can_click)
        inc_btn.setEnabled(can_click)

    def _apply_all_task_buttons_state(self):
        """就地更新所有任务行的按钮可点击态（不重查水位线、不重建表格）。

        触发时机：
          - 任一任务启动时：全局禁用所有按钮（执行中的那颗显示"执行中..."）
          - 任务完成/失败时：清空 _running_tasks 后恢复所有按钮可点击
        依赖 _render_tasks 给按钮 setObjectName('fullBtn'/'incBtn')。
        """
        for row in range(self.task_table.rowCount()):
            op_widget = self.task_table.cellWidget(row, 6)
            if op_widget is None:
                continue
            full_btn = op_widget.findChild(PushButton, "fullBtn")
            inc_btn = op_widget.findChild(PushButton, "incBtn")
            if full_btn is None or inc_btn is None:
                continue
            task_name_item = self.task_table.item(row, 0)
            task_name = task_name_item.text() if task_name_item else ""
            running_mode = self._running_tasks.get(task_name)
            self._apply_task_button_state(full_btn, inc_btn, running_mode)

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

    # ---------------- 常驻增量拉取开关（v3 subprocess 版）----------------
    def _toggle_daemon(self):
        """切换常驻进程 开/停（基于实际 status 而非内存变量）。"""
        if self._daemon_state in ("starting", "stop_requested", "force_stopping"):
            return  # 过渡态，忽略点击
        # 守卫：重置水印进行中禁止启停（与 MainWindow.toggle_daemon 一致）
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），"
                                "请等待重置完成后再操作守护进程。")
            return
        if self._daemon_state == "running":
            self._stop_daemon()
        else:
            self._start_daemon()

    def _start_daemon(self):
        """启动 daemon 子进程（detached），随后 QTimer 握手轮询 status。"""
        # 已有 daemon 在跑（如用户手动 CLI 启动）→ 不重复启动
        if is_daemon_running():
            QMessageBox.information(
                self, "已在运行",
                "常驻采集进程已在运行（可能是手动 CLI 启动）。如需重启请先停止。")
            self._sync_daemon_state()
            return
        try:
            token, proc = start_daemon_subprocess(self.mw.config_dir)
        except Exception as e:
            QMessageBox.critical(self, "启动失败",
                f"启动常驻进程失败：\n\n{type(e).__name__}: {e}")
            return
        self._daemon_token = token
        self._daemon_proc = proc
        self._daemon_state = "starting"
        self._start_handshake_elapsed = 0
        self._update_daemon_btn()
        self._handshake_timer.start()

    def _on_handshake_tick(self):
        """启动握手 QTimer：每 500ms 检查 status 文件，最长 30s（60 次）。"""
        self._start_handshake_elapsed += 1
        # 子进程已死？
        if self._daemon_proc is not None and self._daemon_proc.poll() is not None:
            self._handshake_timer.stop()
            tail = read_bootstrap_log_tail(self._daemon_token)
            self._daemon_state = "stopped"
            self._daemon_token = None
            self._daemon_proc = None
            self._update_daemon_btn()
            QMessageBox.critical(
                self, "启动失败",
                f"常驻进程启动后立即退出。bootstrap 日志尾部：\n\n{tail[-1500:]}")
            return
        # status 文件出现且 token 匹配？
        status = get_daemon_status()
        if status and status.get("instance_token") == self._daemon_token \
                and status.get("status") == "running":
            self._handshake_timer.stop()
            self._daemon_state = "running"
            self._update_daemon_btn()
            self.status_label.setText("🟢 常驻采集进程已启动")
            self._daemon_poll_timer.start()
            return
        # 超时 30s
        if self._start_handshake_elapsed >= 60:
            self._handshake_timer.stop()
            tail = read_bootstrap_log_tail(self._daemon_token)
            self._daemon_state = "stopped"
            self._daemon_token = None
            self._daemon_proc = None
            self._update_daemon_btn()
            QMessageBox.critical(
                self, "启动超时",
                f"30 秒内未收到 daemon 启动握手。bootstrap 日志尾部：\n\n{tail[-1500:]}")

    def _stop_daemon(self):
        """优雅停止：写 stop.request，QTimer 等待 status 消失，60s 超时弹窗。"""
        if not request_graceful_stop(timeout_check_token=self._daemon_token):
            # token 不匹配或 status 不存在 → 可能是手动 CLI 启动的 daemon
            status = get_daemon_status()
            if status is None:
                self._sync_daemon_state()
                return
            # 用 status 里的 token 重新尝试（CLI 启动场景）
            if not request_graceful_stop(timeout_check_token=None):
                QMessageBox.warning(self, "无法停止",
                    "无法发送停止请求（status 文件异常）。可尝试强制停止。")
                return
            self._daemon_token = status.get("instance_token")
        self._daemon_state = "stop_requested"
        self._stop_wait_elapsed = 0
        self._update_daemon_btn()
        self._stop_wait_timer.start()

    def _on_stop_wait_tick(self):
        """停止等待 QTimer：每 2s 检查 status，最长 60s（30 次）。"""
        self._stop_wait_elapsed += 1
        status = get_daemon_status()
        if status is None or not is_daemon_running():
            # daemon 已退出
            self._stop_wait_timer.stop()
            self._daemon_state = "stopped"
            self._daemon_token = None
            self._daemon_proc = None
            self._update_daemon_btn()
            self.status_label.setText("🔴 常驻采集进程已停止")
            self._daemon_poll_timer.stop()
            return
        if self._stop_wait_elapsed >= 30:  # 60s 超时
            self._stop_wait_timer.stop()
            self._prompt_force_kill()

    def _prompt_force_kill(self):
        """60s 超时后弹窗询问是否强制终止。"""
        reply = QMessageBox.warning(
            self, "优雅停止超时",
            "常驻采集进程尚未到达安全退出点，可能正在拉取或写入数据库。\n\n"
            "强制终止可能造成当前批次残留或数据库异常，是否强制终止？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            # 不撤销 stop.request，继续等待
            self._stop_wait_elapsed = 0
            self._stop_wait_timer.start()
            return
        # 强制路径
        self._daemon_state = "force_stopping"
        self._update_daemon_btn()
        ok, msg = force_kill_daemon()
        if ok:
            self._daemon_state = "stopped"
            self._daemon_token = None
            self._daemon_proc = None
            self._update_daemon_btn()
            self.status_label.setText("⚠ 常驻进程已被强制终止")
            self._daemon_poll_timer.stop()
            # 强制停止后检查 DB 可打开性
            db_ok, db_msg = check_db_openable()
            if not db_ok:
                QMessageBox.warning(self, "数据库检查", db_msg)
        else:
            QMessageBox.warning(self, "强制终止失败", msg)
            self._daemon_state = "stop_requested"
            self._update_daemon_btn()

    def _on_daemon_poll(self):
        """低频状态同步（3s）：daemon 异常退出时按钮自动恢复 stopped。"""
        if self._daemon_state == "running":
            if not is_daemon_running():
                logger.warning("[TaskTab] daemon 进程消失（异常退出），按钮恢复 stopped")
                self._daemon_state = "stopped"
                self._daemon_token = None
                self._daemon_proc = None
                self._update_daemon_btn()
                self._daemon_poll_timer.stop()
                self.status_label.setText("⚠ 常驻进程异常退出")

    def _sync_daemon_state(self):
        """从 status 文件同步 daemon 状态（GUI 启动时 + refresh 时）。

        daemon 子进程独立于 GUI，重启 GUI 后通过此函数恢复按钮显示。
        """
        if is_daemon_running():
            self._daemon_state = "running"
            status = get_daemon_status()
            self._daemon_token = status.get("instance_token") if status else None
        else:
            self._daemon_state = "stopped"
            self._daemon_token = None
        self._daemon_proc = None  # GUI 重启后无法持有旧 Popen 句柄
        self._update_daemon_btn()

    def _update_daemon_btn(self):
        """更新开关按钮的显示状态（v3 五态）。"""
        state = self._daemon_state
        if state == "running":
            self.daemon_btn.setText("🟢 进程常驻增量拉取：运行中（点击停止）")
            self.daemon_btn.setEnabled(True)
        elif state == "starting":
            self.daemon_btn.setText("⏳ 正在启动常驻进程...")
            self.daemon_btn.setEnabled(False)
        elif state == "stop_requested":
            self.daemon_btn.setText("⏳ 等待常驻进程安全退出...")
            self.daemon_btn.setEnabled(False)
        elif state == "force_stopping":
            self.daemon_btn.setText("⚠ 正在强制终止...")
            self.daemon_btn.setEnabled(False)
        else:  # stopped
            self.daemon_btn.setText("🔴 进程常驻增量拉取：已停止（点击启动）")
            self.daemon_btn.setEnabled(True)

    def _run_single(self, task: dict, mode: str, full_btn: PushButton, inc_btn: PushButton):
        """执行单个采集任务（后台 LockedTaskWorker）。
        mode: 'full_range'（全量，读配置日期）/ 'incremental'（增量，水位线→今天）
        full_btn/inc_btn: 两个按钮引用，执行时全局互禁。

        v3 评审 1+2：collector_run.lock 在 worker 线程内 acquire/release，
        from_configs/resolve_source_chain/execute_task 全在 worker 内，
        主线程零 collector 调用（不冻结 GUI、不隐式打开 DuckDB）。
        """
        table = task.get("table", "")
        freq = task.get("freq", "daily")

        # 主线程仅做纯静态检查（不创建 collector、不打开 DB，评审 2）
        # 数据源链可用性检查延后到 worker 内（resolve_source_chain 在锁内）

        if mode == "full_range":
            start = task.get("start_date", "").strip()
            if not start:
                QMessageBox.warning(self, "缺少开始日期",
                    f"全量拉取需要配置 start_date。\n\n"
                    f"请在「配置编辑」标签页为任务 '{task.get('name','?')}' 设置开始日期。")
                return

        # 全局互禁：登记运行态后立即禁用所有任务的按钮（防止用户一次点击多个任务并行拉取）
        task_name = task.get("name", "")
        self._running_tasks[task_name] = mode
        self._set_task_status(task_name, self._get_status_text(task_name, mode))
        self._apply_all_task_buttons_state()

        mode_label = "全量" if mode == "full_range" else "增量"
        self.status_label.setText(f"执行中({mode_label}): {task['name']}...")
        # 加载提示弹出（数据采集后台运行中）
        if not hasattr(self, '_collect_tooltip') or self._collect_tooltip is None:
            self._collect_tooltip = StateToolTip(
                f"数据采集进行中({mode_label})", f"正在拉取 {task['name']}...", self)
            self._collect_tooltip.show()
        try:
            # v3：LockedTaskWorker 在线程内持锁、from_configs、execute、close
            worker = LockedTaskWorker(
                task=task, config_dir=self.mw.config_dir, mode=mode,
                run_quality_audit=True)
            worker.progress.connect(self._on_collect_progress)
            worker.finished_ok.connect(lambda res: self._on_task_done(task, True, res))
            worker.finished_err.connect(lambda err: self._on_task_done(task, False, err))
            self.mw.hold_worker(worker)
            worker.start()
        except Exception as e:
            logger.exception("启动采集任务失败: %s", task_name)
            self._running_tasks.pop(task_name, None)
            self._set_task_status(task_name, "失败")
            self._apply_all_task_buttons_state()
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
        """DEPRECATED v3：此方法在主线程调用 _get_collector()，违反评审 2
        （主线程不得隐式 from_configs 打开 DuckDB）。当前无调用方（预检查已移入
        LockedTaskWorker 内部）。保留作回退参考，新代码不应调用。
        """
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
        """全部执行（v3：单 LockedRunAllWorker 持锁跑完整个队列 + 末尾审计）。

        评审 1：不每任务单独拿锁，防 daemon 插入队列中间。
        默认增量模式，主线程零 collector 调用。
        """
        if not self.tasks:
            return
        self.run_all_btn.setEnabled(False)
        self.run_all_btn.setText("⏳ 全部执行中...")
        # 登记所有任务为运行态（按钮互禁）
        for t in self.tasks:
            name = t.get("name", "")
            if name:
                self._running_tasks[name] = "incremental"
                self._set_task_status(name, "增量执行中")
        self._apply_all_task_buttons_state()
        # 加载提示
        if not hasattr(self, '_collect_tooltip') or self._collect_tooltip is None:
            self._collect_tooltip = StateToolTip(
                "全部执行进行中", f"正在执行 {len(self.tasks)} 个任务...", self)
            self._collect_tooltip.show()
        try:
            worker = LockedRunAllWorker(
                tasks=list(self.tasks), config_dir=self.mw.config_dir,
                mode="incremental")
            worker.progress.connect(self._on_collect_progress)
            worker.finished_ok.connect(self._on_run_all_done)
            worker.finished_err.connect(lambda err: self._on_run_all_done(
                {"results": [], "ok_count": 0, "total": 0, "error": err}))
            self.mw.hold_worker(worker)
            worker.start()
        except Exception as e:
            logger.exception("全部执行启动失败: %s", e)
            for t in self.tasks:
                self._running_tasks.pop(t.get("name", ""), None)
            self._apply_all_task_buttons_state()
            self.run_all_btn.setEnabled(True)
            self.run_all_btn.setText("▶ 全部执行")
            self.status_label.setText(f"❌ 启动失败: {e}")
            QMessageBox.critical(self, "启动失败", f"全部执行启动失败：\n\n{type(e).__name__}: {e}")

    def _on_run_all_done(self, result: dict):
        """全部执行完成的统一回调。"""
        for t in self.tasks:
            self._running_tasks.pop(t.get("name", ""), None)
        ok_count = result.get("ok_count", 0)
        total = result.get("total", 0)
        err = result.get("error")
        self.run_all_btn.setEnabled(True)
        self.run_all_btn.setText("▶ 全部执行")
        if err:
            self.status_label.setText(f"❌ 全部执行失败: {err}")
            QMessageBox.warning(self, "全部执行", f"执行失败：\n\n{err}")
        elif ok_count == total:
            self.status_label.setText(f"✅ 全部执行完成（{ok_count}/{total}）")
        else:
            self.status_label.setText(f"⚠ 全部执行完成（{ok_count}/{total} 成功，{total-ok_count} 失败）")
        # 关闭加载提示
        if hasattr(self, '_collect_tooltip') and self._collect_tooltip:
            self._collect_tooltip.setContent(f"完成 {ok_count}/{total}")
            self._collect_tooltip = None
        self.refresh()

    def _on_collect_progress(self, msg):
        """采集进度更新（同时更新 status_label + StateToolTip）。"""
        self.status_label.setText(msg)
        if hasattr(self, '_collect_tooltip') and self._collect_tooltip:
            self._collect_tooltip.setContent(msg)

    def _on_task_done(self, task: dict, ok: bool, result):
        task_name = task.get("name", "")
        audit_warning = (
            ok and isinstance(result, dict)
            and result.get("quality_audit_ran") is True
            and result.get("quality_audit_ok") is False
        )
        if ok:
            if audit_warning:
                logger.warning(
                    f"\u4efb\u52a1 {task_name} \u62c9\u53d6/\u5199\u5e93\u6210\u529f\uff0c"
                    f"\u4f46\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1\u672a\u901a\u8fc7: {result}")
            else:
                logger.info(f"\u4efb\u52a1 {task_name} \u5b8c\u6210: {result}")
        else:
            logger.error(f"\u4efb\u52a1 {task_name} \u5931\u8d25: {result}")

        if audit_warning:
            final_text = (
                f"\u26a0\ufe0f {task_name} \u62c9\u53d6\u6210\u529f\uff1b"
                f"\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1\u672a\u901a\u8fc7")
            task_status = "\u6210\u529f\uff08\u5ba1\u8ba1\u544a\u8b66\uff09"
        elif ok:
            final_text = f"\u2705 {task_name}"
            task_status = "\u6210\u529f"
        else:
            final_text = f"\u274c {task_name}"
            task_status = "\u5931\u8d25"
        self.status_label.setText(final_text)

        self._running_tasks.pop(task_name, None)
        self._set_task_status(task_name, task_status)
        self.refresh()
        if not self._running_tasks and not getattr(self, '_task_queue', None):
            if hasattr(self, '_collect_tooltip') and self._collect_tooltip:
                self._collect_tooltip.setContent(final_text)
                self._collect_tooltip = None

    def _reset_watermark(self):
        """重置水位线（下次全量拉取）。

        P0 约束：
        1. 按 source 过滤 DELETE（动态收集当前可见任务的 source 集合），
           绝对不能全表 DELETE（避免误清其它数据源水位）。
        2. 统一正式库：路径取自当前 config_dir 的 data_config.json（data/quantstudio.db），
           锚定项目根，不随 config_dir 变。
        3. 空库兜底：表不存在 / 库不可开 → 友好提示，不崩。
        4. 守卫：重置进行中或 daemon 运行中禁止（防配置与运行实例冲突）。
        """
        # ---- 守卫 ----
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），请稍候。")
            return
        if self.mw._daemon_running_in_config(self.mw.config_dir):
            QMessageBox.warning(self, "禁止操作",
                                "采集守护进程正在运行，请先「停止采集」后再重置水位，"
                                "避免造脏数据。")
            return

        reply = QMessageBox.question(
            self, "确认重置水位",
            "将按当前数据源模式可见任务的【源集合】重置水位线，"
            "下次采集从该源全量拉取。确认？\n\n"
            "（仅清除当前模式涉及的源，不影响其它模式水位。）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ---- 解析统一正式库路径（当前 config_dir 的 data_config.json）----
        try:
            dc = json.loads((self.mw.config_dir / "data_config.json")
                            .read_text(encoding="utf-8"))
            rel = dc.get("path", "data/quantstudio.db")
            db_file = (self.mw.app_root / rel).resolve() if not Path(rel).is_absolute() \
                else Path(rel).resolve()
        except Exception as e:
            QMessageBox.critical(self, "配置读取失败",
                                 f"无法读取 {self.mw.config_dir}/data_config.json：{e}")
            return

        if not db_file.exists():
            QMessageBox.information(self, "空库",
                                    f"数据库文件尚不存在：{db_file}\n"
                                    "首次启动无需重置水位（采集将从全量开始）。")
            return

        # ---- 动态收集 source 集合（仅当前可见任务涉及的源）----
        sources = sorted({t.get("source") for t in self.tasks
                          if t.get("source")})
        if not sources:
            QMessageBox.information(self, "无源", "当前模式无可见采集任务，无需重置。")
            return

        # ---- 执行：按 source 过滤 DELETE（绝不全表）----
        try:
            import duckdb
            self.mw._reset_in_progress = True
            self.mw._reset_mode = self.mw.current_profile
            self.mw._reset_sources = sources
            self.mw.show_reset_progress("重置水印中", f"正在清除源：{', '.join(sources)}")
            with duckdb.connect(str(db_file), read_only=False) as conn:
                # 空库兜底：表不存在则视为已重置（无需操作）
                tbl = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='main' AND table_name='source_watermark'"
                ).fetchall()
                if not tbl:
                    logger.info("source_watermark 表不存在（空库），无需重置")
                else:
                    for src in sources:
                        conn.execute(
                            "DELETE FROM source_watermark WHERE source = ?", [src])
                    logger.info(f"水位线已重置（按源过滤）: {sources}")
            self.mw._reset_in_progress = False
            self.mw._reset_mode = None
            self.mw._reset_sources = []
            QMessageBox.information(
                self, "重置完成",
                f"已按源重置水位线：{', '.join(sources)}\n下次采集将从该源全量拉取。")
            self.refresh()
        except Exception as e:
            self.mw._reset_in_progress = False
            self.mw._reset_mode = None
            self.mw._reset_sources = []
            logger.error(f"重置水位失败: {e}")
            QMessageBox.critical(self, "重置失败", f"重置水位失败：{e}")

    def refresh(self):
        """刷新任务列表（重新从 collector_tasks.json 加载配置 + 水位线 + 批次审计 + 按钮状态）"""
        self._load_tasks()  # 重新从配置文件加载（修复：配置编辑器改 source 后刷新可生效）
        # v3：从 daemon status 文件同步按钮状态（不再依赖内存 daemon_worker）
        if self._daemon_state not in ("starting", "stop_requested", "force_stopping"):
            self._sync_daemon_state()
        else:
            self._update_daemon_btn()
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
