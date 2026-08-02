"""Tab7: 配置编辑器（结构化表单，替代原始 JSON 编辑）
左侧 4 个区域导航 → 右侧对应表单：数据库配置 / 数据源凭证 / 采集任务 / 字段对齐规则
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidgetItem,
    QLabel, QFrame, QGridLayout, QFileDialog, QMessageBox,
    QTableWidgetItem, QHeaderView)
from qfluentwidgets import (
    PushButton, ComboBox, LineEdit, SpinBox,
    ScrollArea, GroupHeaderCardWidget, ListWidget, TableWidget)
from PyQt6.QtCore import Qt
from quantstudio._paths import db_path
from quantstudio.pipeline.source_capabilities import capability_matrix


def _disable_combo_wheel(combo):
    """禁用 ComboBox 的滚轮切换（避免滚动页面时误改下拉选项）"""
    combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    combo.wheelEvent = lambda e: e.ignore()

logger = logging.getLogger(__name__)

# 数据库类型选项
DB_TYPES = ["duckdb", "sqlite", "questdb", "mysql"]

# 采集任务能力矩阵（从 task_tab 复制，避免循环导入）
SOURCE_CAPABILITY = capability_matrix()

_DARK_CONFIG_STYLE = """
QWidget#sourcesScrollContent,
QWidget#tasksScrollContent,
QWidget#alignmentScrollContent,
QFrame#taskCard {
    background-color: #202020;
    color: #ffffff;
}
QWidget#sourcesScrollContent QLabel,
QWidget#tasksScrollContent QLabel,
QWidget#alignmentScrollContent QLabel,
QFrame#taskCard QLabel {
    color: #ffffff;
}
"""

# 各表的默认数据源（GUI 取消数据源下拉框后，框架按此映射自动决定源）。
# xtquant 为骨架权威源；tushare 为独立补充表（xtquant 不提供的数据维度）；
# akshare 用于 ST 历史（hidden 联动）和退市名单（用户可见）。
# 用户在 GUI 不再选数据源，框架内部按需混合多源打补丁。
# 分钟表（stock_minutes/etf_minutes）权威源=xtquant，单源锁定（复权一致性决策 2026-07-21 用户批准）：
# xtquant 三段式复权原生直通 aligner passthrough，禁止 tushare 兜底避免跨源复权基准不一致。
# daemon 分钟表权威源守卫会拒绝非 xtquant 源写入，故 GUI 默认源必须与此一致。
DEFAULT_SOURCE_MAP = {
    "stock_daily":          "xtquant",   # xtquant 骨架权威源
    "stock_minutes":        "xtquant",   # 复权一致性单源锁定（2026-07-21）
    "etf_daily":            "xtquant",   # 复权一致性单源锁定（2026-07-21，与 stock_daily 同款）
    "etf_minutes":          "xtquant",   # 复权一致性单源锁定（2026-07-21）
    "index_daily":          "tushare",
    "index_constituents":   "akshare",
    "stock_float_share":    "xtquant",   # 报告期股本，xtquant Capital 为权威源
    "stock_daily_valuation":"tushare",   # 每日估值，xtquant 不提供，独立补充表
    "fin_indicator":        "tushare",
    "balance_statement":    "xtquant",
    "income_statement":     "xtquant",
    "cashflow_statement":   "xtquant",
    "stock_dividend":       "tushare",
    "sw_industry":          "tushare",
    "industry_classification": "tushare",  # F4 正式分类定义（SW2021 L1）
    "industry_membership":  "tushare",     # F4 正式成员历史 PIT
    "tick":                 "xtquant",
    "stock_namechange":     "akshare",   # hidden 联动任务（stock_daily 前置依赖）
    "stock_delist":         "akshare",   # 沪深退市名单（用户可见）
}

# codes 字段的 ALL 含义提示
CODES_ALL_HINT = {
    "stock_daily": "ALL = 全部 A 股",
    "stock_minutes": "ALL = 全部 A 股",
    "etf_daily": "ALL = 全部 ETF",
    "etf_minutes": "ALL = 全部 ETF",
    "index_daily": "ALL = 全部指数",
    "index_constituents": "ALL = 全部指数成分股",
    "stock_float_share": "ALL = 全部 A 股",
    "stock_daily_valuation": "ALL = 全部 A 股",
    "fin_indicator": "ALL = 全部 A 股",
    "balance_statement": "ALL = 全部 A 股",
    "income_statement": "ALL = 全部 A 股",
    "cashflow_statement": "ALL = 全部 A 股",
    "stock_dividend": "ALL = 全部 A 股",
    "sw_industry": "ALL = 全部股票行业",
    "industry_classification": "ALL = 全部申万一级行业定义",
    "industry_membership": "ALL = 全部申万一级行业成员历史",
}

# 表名 → 中文简述（任务卡片右侧显示）
TABLE_DESCRIPTION = {
    "stock_daily": "A股日线行情（OHLCV+复权+估值指标）",
    "stock_minutes": "A股1分钟K线（OHLCV+复权）",
    "etf_daily": "ETF基金日线行情（OHLCV+复权）",
    "etf_minutes": "ETF基金1分钟K线（OHLCV+复权）",
    "index_daily": "指数日线行情",
    "index_constituents": "指数成分股列表",
    "stock_float_share": "流通股本与市值数据",
    "stock_daily_valuation": "每日估值（流通/总市值、PE/PB）",
    "fin_indicator": "财务指标（EPS/ROE/PE等）",
    "balance_statement": "资产负债表",
    "income_statement": "利润表",
    "cashflow_statement": "现金流量表",
    "stock_dividend": "除权除息记录",
    "sw_industry": "申万行业分类（legacy 快照，仅审计）",
    "industry_classification": "行业分类定义（SW2021 L1 正式）",
    "industry_membership": "行业成员历史（PIT 正式）",
}

# 按表类型分组（用于采集任务分区域显示）
TABLE_CATEGORIES = {
    "行情日线": ["stock_daily", "etf_daily", "index_daily"],
    "行情分钟": ["stock_minutes", "etf_minutes"],
    "估值股本": ["stock_float_share", "stock_daily_valuation", "fin_indicator"],
    "指数成分": ["index_constituents"],
    "三大报表": ["balance_statement", "income_statement", "cashflow_statement"],
    "除权行业": ["stock_dividend", "sw_industry"],
    "行业分类": ["industry_classification", "industry_membership"],
    "其他": [],  # 未归类的表放这里
}


class ConfigEditorTab(QWidget):
    """配置编辑器：左侧区域导航 + 右侧结构化表单"""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)

        # 左侧导航
        self.nav_list = ListWidget()
        self.nav_list.setFixedWidth(180)
        items = [
            ("db", "🗄️ 数据库配置"),
            ("sources", "🔌 数据源凭证"),
            ("tasks", "📋 采集任务"),
            ("alignment", "📐 字段对齐规则"),
        ]
        for key, label in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav_list)

        # 右侧内容区（用 QStackedWidget 替代）
        from PyQt6.QtWidgets import QStackedWidget
        self.stack = QStackedWidget()

        # 构建 4 个页面
        self.db_page = self._build_db_page()
        self.sources_page = self._build_sources_page()
        self.tasks_page = self._build_tasks_page()
        self.alignment_page = self._build_alignment_page()

        self.stack.addWidget(self.db_page)
        self.stack.addWidget(self.sources_page)
        self.stack.addWidget(self.tasks_page)
        self.stack.addWidget(self.alignment_page)

        layout.addWidget(self.stack, 1)

        # 默认选第一个
        self.nav_list.setCurrentRow(0)

    # ==================== 数据库配置页面 ====================

    def _build_db_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # 标题
        layout.addWidget(QLabel("🗄️ 数据库配置 (data_config.json)"))

        form = QGridLayout()

        # type 下拉
        form.addWidget(QLabel("数据库类型:"), 0, 0)
        self.db_type_combo = ComboBox()
        _disable_combo_wheel(self.db_type_combo)
        self.db_type_combo.addItems(DB_TYPES)
        form.addWidget(self.db_type_combo, 0, 1)

        # path 文件选择
        form.addWidget(QLabel("数据库路径:"), 1, 0)
        path_layout = QHBoxLayout()
        self.db_path_edit = LineEdit()
        self.db_path_btn = PushButton("📁 浏览...")
        self.db_path_btn.clicked.connect(self._browse_db_file)
        path_layout.addWidget(self.db_path_edit)
        path_layout.addWidget(self.db_path_btn)
        form.addLayout(path_layout, 1, 1)

        # quarantine retention_days
        form.addWidget(QLabel("隔离保留天数:"), 2, 0)
        self.db_retention_spin = SpinBox()
        self.db_retention_spin.setRange(1, 365)
        self.db_retention_spin.setSuffix(" 天")
        form.addWidget(self.db_retention_spin, 2, 1)

        # quarantine auto_archive
        form.addWidget(QLabel("自动归档:"), 3, 0)
        self.db_archive_combo = ComboBox()
        _disable_combo_wheel(self.db_archive_combo)
        self.db_archive_combo.addItems(["true", "false"])
        form.addWidget(self.db_archive_combo, 3, 1)

        # 统一正式库（验收 #13：采集与 QFQ 同库共存）显式展示
        form.addWidget(QLabel("统一正式库(QFQ同库):"), 4, 0)
        self.db_unified_label = QLabel("—")
        self.db_unified_label.setStyleSheet("color:#7fd1ff;")
        self.db_unified_label.setWordWrap(True)
        form.addWidget(self.db_unified_label, 4, 1)

        layout.addLayout(form)
        layout.addStretch()

        # 保存按钮
        save_btn = PushButton("💾 保存数据库配置")
        save_btn.clicked.connect(lambda: self._save_db_config())
        layout.addWidget(save_btn)

        self._load_db_config()
        return page

    def _load_db_config(self):
        try:
            with (self.mw.config_dir / "data_config.json").open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.db_type_combo.setCurrentText(cfg.get("type", "duckdb"))
            self.db_path_edit.setText(cfg.get("path", str(db_path())))
            q = cfg.get("quarantine", {})
            self.db_retention_spin.setValue(q.get("retention_days", 30))
            self.db_archive_combo.setCurrentText(str(q.get("auto_archive", True)).lower())
            # 展示统一正式库绝对路径（采集与 QFQ 同库，验收 #13）
            rel = cfg.get("path", "data/quantstudio.db")
            unified = (self.mw.app_root / rel).resolve() if not Path(rel).is_absolute() \
                else Path(rel).resolve()
            self.db_unified_label.setText(str(unified))
        except Exception as e:
            logger.warning(f"加载 data_config.json 失败: {e}")
            self.db_unified_label.setText("（加载失败）")

    def _browse_db_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择数据库文件", "", "数据库文件 (*.db *.duckdb);;所有文件 (*)")
        if path:
            self.db_path_edit.setText(path)

    def _browse_dir(self, edit_widget):
        """目录选择器（用于 qmt_path 等）"""
        path = QFileDialog.getExistingDirectory(self, "选择目录", edit_widget.text() or "C:/")
        if path:
            edit_widget.setText(path)

    def _save_db_config(self):
        # 守卫：重置水印进行中 / 守护进程运行 → 禁止写配置（防配置与实例冲突）
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），"
                                "请等待完成后再保存。")
            return
        if self.mw._daemon_running_in_config(self.mw.config_dir):
            QMessageBox.warning(self, "禁止保存",
                                "采集守护进程正在运行，请先「停止采集」后再保存数据库配置。")
            return
        cfg = {
            "type": self.db_type_combo.currentText(),
            "path": self.db_path_edit.text().strip(),
            "quarantine": {
                "path": "data/quarantine.db",
                "retention_days": self.db_retention_spin.value(),
                "auto_archive": self.db_archive_combo.currentText() == "true",
            },
        }
        self._write_json("data_config.json", cfg)

    # ==================== 数据源凭证页面 ====================

    def _build_sources_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("🔌 数据源凭证 (sources_config.json)"))

        # 滚动区域
        self.sources_scroll = ScrollArea()
        self.sources_scroll.setWidgetResizable(True)
        self.sources_scroll.enableTransparentBackground()
        self.sources_scroll_content = QWidget()
        self.sources_scroll_content.setObjectName("sourcesScrollContent")
        self.sources_form = QGridLayout(self.sources_scroll_content)

        self.source_widgets = {}  # {source_name: {field: widget}}
        try:
            with (self.mw.config_dir / "sources_config.json").open("r", encoding="utf-8") as f:
                self._sources_cfg = json.load(f)
        except Exception:
            self._sources_cfg = {"sources": {}}

        row = 0
        for name, cfg in self._sources_cfg.get("sources", {}).items():
            # 分组框
            group = GroupHeaderCardWidget()
            group.setTitle(name)
            inner = QWidget()
            gl = QGridLayout(inner)
            group.layout().addWidget(inner)
            widgets = {}

            # enabled
            gl.addWidget(QLabel("启用:"), 0, 0)
            enabled_combo = ComboBox()
            _disable_combo_wheel(enabled_combo)
            enabled_combo.addItems(["true", "false"])
            enabled_combo.setCurrentText(str(cfg.get("enabled", False)).lower())
            gl.addWidget(enabled_combo, 0, 1)
            widgets["enabled"] = enabled_combo

            # 凭证字段（token / user / api_key / qmt_path / base_url）
            field_row = 1
            for key in ["token", "user", "api_key", "qmt_path", "base_url"]:
                if key in cfg:
                    gl.addWidget(QLabel(f"{key}:"), field_row, 0)
                    if key == "qmt_path":
                        # QMT 路径用目录选择器
                        path_h = QHBoxLayout()
                        edit = LineEdit()
                        edit.setText(str(cfg[key]))
                        browse_btn = PushButton("📁")
                        browse_btn.setFixedWidth(30)
                        browse_btn.clicked.connect(lambda checked, e=edit: self._browse_dir(e))
                        path_h.addWidget(edit)
                        path_h.addWidget(browse_btn)
                        gl.addLayout(path_h, field_row, 1)
                    else:
                        edit = LineEdit()
                        edit.setText(str(cfg[key]))
                        if key in ("token", "api_key"):
                            edit.setEchoMode(LineEdit.EchoMode.Password)
                            edit.setPlaceholderText("从环境变量 ${VAR} 读取或直接输入")
                        gl.addWidget(edit, field_row, 1)
                    widgets[key] = edit
                    field_row += 1

            self.sources_form.addWidget(group, row, 0)
            self.source_widgets[name] = widgets
            row += 1

        self.sources_scroll.setWidget(self.sources_scroll_content)
        layout.addWidget(self.sources_scroll)
        page.setStyleSheet(_DARK_CONFIG_STYLE)

        save_btn = PushButton("💾 保存数据源凭证")
        save_btn.clicked.connect(self._save_sources_config)
        layout.addWidget(save_btn)
        return page

    def _save_sources_config(self):
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），"
                                "请等待完成后再保存。")
            return
        if self.mw._daemon_running_in_config(self.mw.config_dir):
            QMessageBox.warning(self, "禁止保存",
                                "采集守护进程正在运行，请先「停止采集」后再保存数据源凭证。")
            return
        for name, widgets in self.source_widgets.items():
            cfg = self._sources_cfg["sources"].get(name, {})
            cfg["enabled"] = widgets["enabled"].currentText() == "true"
            for key, widget in widgets.items():
                if key == "enabled":
                    continue
                cfg[key] = widget.text().strip()
            self._sources_cfg["sources"][name] = cfg
        self._write_json("sources_config.json", self._sources_cfg)

    # ==================== 采集任务页面 ====================

    def _build_tasks_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # 全局配置区
        global_group = GroupHeaderCardWidget()
        global_group.setTitle("全局调度配置")
        inner = QWidget()
        gl = QGridLayout(inner)
        global_group.layout().addWidget(inner)
        gl.addWidget(QLabel("每日执行时间:"), 0, 0)
        self.tasks_daily_time = LineEdit()
        gl.addWidget(self.tasks_daily_time, 0, 1)
        gl.addWidget(QLabel("检查间隔(秒):"), 1, 0)
        self.tasks_check_interval = SpinBox()
        self.tasks_check_interval.setRange(10, 3600)
        gl.addWidget(self.tasks_check_interval, 1, 1)
        layout.addWidget(global_group)

        # 加载全局配置
        try:
            with (self.mw.config_dir / "collector_tasks.json").open("r", encoding="utf-8") as f:
                self._tasks_cfg = json.load(f)
            ds = self._tasks_cfg.get("daemon_schedule", {})
            self.tasks_daily_time.setText(ds.get("daily_time", "17:00"))
            self.tasks_check_interval.setValue(ds.get("check_interval_sec", 300))
        except Exception:
            self._tasks_cfg = {"tasks": []}

        # 任务列表（按类别分组，可滚动）
        self.tasks_scroll = ScrollArea()
        self.tasks_scroll.setWidgetResizable(True)
        self.tasks_scroll.enableTransparentBackground()
        self.tasks_scroll_content = QWidget()
        self.tasks_scroll_content.setObjectName("tasksScrollContent")
        scroll_layout = QVBoxLayout(self.tasks_scroll_content)

        self.task_widgets = []  # [(task_dict_ref, widgets_dict, task_frame)]

        # 按 table 分类（跳过 hidden 任务：如 stock_daily_valuation 是 stock_daily 的前置依赖表，
        # 由框架自动联动拉取，用户无需也不应手动配置，显示出来是冗余）
        tasks_by_table = {}
        for t in self._tasks_cfg.get("tasks", []):
            if t.get("hidden", False):
                continue
            tbl = t.get("table", "unknown")
            tasks_by_table.setdefault(tbl, []).append(t)

        # 按类别显示
        categorized_tables = set()
        for cat_name, cat_tables in TABLE_CATEGORIES.items():
            cat_tasks = []
            for tbl in cat_tables:
                if tbl in tasks_by_table:
                    cat_tasks.extend(tasks_by_table[tbl])
                    categorized_tables.add(tbl)
            if not cat_tasks:
                continue

            cat_group = GroupHeaderCardWidget()
            cat_group.setTitle(f"{cat_name} ({len(cat_tasks)} 个任务)")
            cat_layout = cat_group.layout()  # reuse the card's existing QVBoxLayout

            for task in cat_tasks:
                task_frame = self._build_task_card(task)
                cat_layout.addWidget(task_frame)

            scroll_layout.addWidget(cat_group)

        # 未归类的表
        other_tasks = []
        for tbl, tasks in tasks_by_table.items():
            if tbl not in categorized_tables:
                other_tasks.extend(tasks)
        if other_tasks:
            cat_group = GroupHeaderCardWidget()
            cat_group.setTitle(f"其他 ({len(other_tasks)} 个任务)")
            cat_layout = cat_group.layout()  # reuse the card's existing QVBoxLayout
            for task in other_tasks:
                cat_layout.addWidget(self._build_task_card(task))
            scroll_layout.addWidget(cat_group)

        scroll_layout.addStretch()
        self.tasks_scroll.setWidget(self.tasks_scroll_content)
        layout.addWidget(self.tasks_scroll, 1)
        page.setStyleSheet(_DARK_CONFIG_STYLE)

        save_btn = PushButton("💾 保存采集任务")
        save_btn.clicked.connect(self._save_tasks_config)
        layout.addWidget(save_btn)
        return page

    def _build_task_card(self, task: dict) -> QFrame:
        """构建单个任务卡片"""
        frame = QFrame()
        frame.setObjectName("taskCard")
        frame.setFrameShape(QFrame.Shape.Box)
        fl = QGridLayout(frame)
        fl.setContentsMargins(8, 4, 8, 4)

        table_name = task.get("table", "")
        supported_sources = SOURCE_CAPABILITY.get(table_name, [])
        all_hint = CODES_ALL_HINT.get(table_name, "")
        desc = TABLE_DESCRIPTION.get(table_name, "")

        row = 0

        # name（只读标签）+ 中文说明
        fl.addWidget(QLabel(f"<b>{task.get('name', '')}</b>"), row, 0)
        # table + freq（只读）
        table_label = QLabel(f"[{table_name} / {task.get('freq', 'daily')}]")
        fl.addWidget(table_label, row, 1)
        # 右侧中文说明
        if desc:
            desc_label = QLabel(desc)
            desc_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            fl.addWidget(desc_label, row, 2)
        row += 1

        # enabled
        fl.addWidget(QLabel("启用:"), row, 0)
        enabled_combo = ComboBox()
        _disable_combo_wheel(enabled_combo)
        enabled_combo.addItems(["true", "false"])
        enabled_combo.setCurrentText(str(task.get("enabled", True)).lower())
        fl.addWidget(enabled_combo, row, 1)
        row += 1

        # 数据源：展示 collector_tasks.json 中真实生效配置（框架实际按 source_priority > source 决定权威源）。
        # 不再使用硬编码 DEFAULT_SOURCE_MAP 显示，避免与磁盘配置不一致、误导用户以为权威源是 xtquant。
        pri = task.get("source_priority") or []
        effective_source = (pri[0] if pri else None) or task.get("source") or DEFAULT_SOURCE_MAP.get(table_name, "tushare")
        fl.addWidget(QLabel("数据源:"), row, 0)
        source_text = f"{effective_source}（配置生效）"
        if pri:
            source_text += f"  ｜ 回退链: {' > '.join(pri)}"
        source_label = QLabel(source_text)
        source_label.setToolTip(
            "数据源由 collector_tasks.json 的 source / source_priority 决定（框架实际生效）。\n"
            "切换权威源请直接编辑该文件的 source 与 source_priority 字段后保存，GUI 不在此处修改。"
        )
        fl.addWidget(source_label, row, 1)
        # 数据源为只读展示，不在 GUI 编辑；widgets 字典在末尾统一构建
        row += 1

        # start_date / end_date（mode 已移除：全量/增量改由采集任务 Tab 的按钮决定）
        fl.addWidget(QLabel("开始日期 *:"), row, 0)
        start_edit = LineEdit()
        start_edit.setText(task.get("start_date", "2018-01-01"))
        start_edit.setPlaceholderText("必填，全量拉取起始日（如 2018-01-01）")
        fl.addWidget(start_edit, row, 1)
        row += 1

        fl.addWidget(QLabel("结束日期:"), row, 0)
        end_edit = LineEdit()
        end_edit.setText(task.get("end_date", ""))
        end_edit.setPlaceholderText("空=今天（全量拉取截止日）")
        fl.addWidget(end_edit, row, 1)
        row += 1

        # codes
        fl.addWidget(QLabel("代码列表:"), row, 0)
        codes_edit = LineEdit()
        codes_edit.setText(",".join(task.get("codes", ["ALL"])))
        if all_hint:
            codes_edit.setPlaceholderText(f"ALL（{all_hint}）或 600000,000001")
        fl.addWidget(codes_edit, row, 1)
        row += 1

        # max_workers
        fl.addWidget(QLabel("并发线程:"), row, 0)
        workers_spin = SpinBox()
        workers_spin.setRange(1, 32)
        workers_spin.setValue(task.get("max_workers", 4))
        fl.addWidget(workers_spin, row, 1)
        row += 1

        # rate_limit.calls_per_min
        fl.addWidget(QLabel("限流(次/分):"), row, 0)
        rate_spin = SpinBox()
        rate_spin.setRange(1, 600)
        rl = task.get("rate_limit", {})
        rate_spin.setValue(rl.get("calls_per_min", 60))
        fl.addWidget(rate_spin, row, 1)
        row += 1

        # rate_limit.wait_on_429
        fl.addWidget(QLabel("429等待:"), row, 0)
        wait429_combo = ComboBox()
        _disable_combo_wheel(wait429_combo)
        wait429_combo.addItems(["true", "false"])
        wait429_combo.setCurrentText(str(rl.get("wait_on_429", True)).lower())
        fl.addWidget(wait429_combo, row, 1)

        widgets = {
            "enabled": enabled_combo,
            "source": effective_source,  # 只读展示值（来自真实配置），保存时不再覆盖 task["source"]
            "start_date": start_edit, "end_date": end_edit, "codes": codes_edit,
            "max_workers": workers_spin, "calls_per_min": rate_spin, "wait_on_429": wait429_combo,
        }
        self.task_widgets.append((task, widgets, frame))
        return frame

    def _save_tasks_config(self):
        # 守卫：重置中 / 守护进程运行 → 禁止写配置
        if self.mw._reset_in_progress:
            QMessageBox.warning(self, "操作进行中",
                                f"正在执行重置水印（模式：{self.mw._reset_mode}），"
                                "请等待完成后再保存。")
            return
        if self.mw._daemon_running_in_config(self.mw.config_dir):
            QMessageBox.warning(self, "禁止保存",
                                "采集守护进程正在运行，请先「停止采集」后再保存采集任务配置。")
            return
        # 全局配置
        self._tasks_cfg["daemon_schedule"] = {
            "daily_time": self.tasks_daily_time.text().strip() or "17:00",
            "check_interval_sec": self.tasks_check_interval.value(),
        }

        # 更新每个任务
        for task, widgets, _frame in self.task_widgets:
            task["enabled"] = widgets["enabled"].currentText() == "true"
            # source / source_priority 由 collector_tasks.json 真实配置决定，不在 GUI 编辑，保留原值。
            # 注意：旧逻辑会在此用 DEFAULT_SOURCE_MAP 硬编码覆盖 task["source"]，
            # 导致在 GUI 保存后权威源被错误改回 xtquant（与磁盘配置冲突）。现已移除该覆盖。
            # mode 字段不再保存（全量/增量改由采集任务 Tab 按钮决定，运行时传入）
            task.pop("mode", None)
            # start_date 必填校验
            start_val = widgets["start_date"].text().strip()
            if not start_val:
                QMessageBox.warning(self, "保存失败",
                    f"任务 '{task.get('name','?')}' 的开始日期为必填项，不能为空。")
                return
            task["start_date"] = start_val
            end_val = widgets["end_date"].text().strip()
            if end_val:
                task["end_date"] = end_val
            elif "end_date" in task:
                task["end_date"] = datetime.now().strftime("%Y-%m-%d")
            codes_text = widgets["codes"].text().strip()
            if codes_text.upper() == "ALL" or not codes_text:
                task["codes"] = ["ALL"]
            else:
                task["codes"] = [c.strip() for c in codes_text.split(",") if c.strip()]
            task["max_workers"] = widgets["max_workers"].value()
            task["rate_limit"] = {
                "calls_per_min": widgets["calls_per_min"].value(),
                "wait_on_429": widgets["wait_on_429"].currentText() == "true",
            }

        self._write_json("collector_tasks.json", self._tasks_cfg)
        # 自动刷新各 Tab（source 等变更立即生效）
        # 注意：MainWindow 没有自定义 _tabs 字典，标签页由 FluentWindow 的
        # stackedWidget 管理，需通过 stackedWidget.widget(i) 遍历。
        stack = self.mw.stackedWidget
        for i in range(stack.count()):
            tab = stack.widget(i)
            if tab is None:
                continue
            # 跳过当前编辑器自身，避免在保存后立即重建自身 UI
            if tab is self:
                continue
            if hasattr(tab, "refresh"):
                try:
                    tab.refresh()
                except Exception as e:
                    logger.warning(f"Tab {i} refresh 失败: {e}")

    # ==================== 字段对齐规则页面 ====================

    def _build_alignment_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("📐 字段对齐规则 (alignment_rules.json) — 只读"))

        self.alignment_scroll = ScrollArea()
        self.alignment_scroll.setWidgetResizable(True)
        self.alignment_scroll.enableTransparentBackground()
        self.alignment_scroll_content = QWidget()
        self.alignment_scroll_content.setObjectName("alignmentScrollContent")
        scroll_layout = QVBoxLayout(self.alignment_scroll_content)

        try:
            with (self.mw.config_dir / "alignment_rules.json").open("r", encoding="utf-8") as f:
                rules = json.load(f)
        except Exception:
            rules = {"schemas": {}, "source_mappings": {}}

        # Schemas 展示
        schemas_group = GroupHeaderCardWidget()
        schemas_group.setTitle("Schema 定义（表结构）")
        sl = schemas_group.layout()  # reuse the card's existing QVBoxLayout
        schema_table = TableWidget()
        schema_table.setColumnCount(3)
        schema_table.setHorizontalHeaderLabels(["表名", "主键", "列数"])
        schema_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        schemas = rules.get("schemas", {})
        schema_table.setRowCount(len(schemas))
        for i, (name, schema) in enumerate(sorted(schemas.items())):
            pk = ", ".join(schema.get("primary_key", []))
            cols = len(schema.get("columns", {}))
            schema_table.setItem(i, 0, QTableWidgetItem(name))
            schema_table.setItem(i, 1, QTableWidgetItem(pk))
            schema_table.setItem(i, 2, QTableWidgetItem(str(cols)))
        sl.addWidget(schema_table)
        scroll_layout.addWidget(schemas_group)

        # Source mappings 展示
        mappings_group = GroupHeaderCardWidget()
        mappings_group.setTitle("数据源映射（字段映射 + 单位换算）")
        ml = mappings_group.layout()  # reuse the card's existing QVBoxLayout
        map_table = TableWidget()
        map_table.setColumnCount(3)
        map_table.setHorizontalHeaderLabels(["数据源", "表名", "映射字段数"])
        map_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        all_mappings = []
        for src, tables in rules.get("source_mappings", {}).items():
            if not isinstance(tables, dict):
                continue
            for tbl, m in tables.items():
                if not isinstance(m, dict):
                    continue
                all_mappings.append((src, tbl, len(m.get("column_map", {}))))
        all_mappings.sort()
        map_table.setRowCount(len(all_mappings))
        for i, (src, tbl, cnt) in enumerate(all_mappings):
            map_table.setItem(i, 0, QTableWidgetItem(src))
            map_table.setItem(i, 1, QTableWidgetItem(tbl))
            map_table.setItem(i, 2, QTableWidgetItem(str(cnt)))
        ml.addWidget(map_table)
        scroll_layout.addWidget(mappings_group)

        scroll_layout.addStretch()
        self.alignment_scroll.setWidget(self.alignment_scroll_content)
        layout.addWidget(self.alignment_scroll, 1)
        page.setStyleSheet(_DARK_CONFIG_STYLE)
        return page

    # ==================== 通用工具 ====================

    def _write_json(self, fname: str, data: dict):
        """保存 JSON 配置文件"""
        path = self.mw.config_dir / fname
        try:
            with path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "保存成功", f"✅ {fname} 已保存")
            logger.info(f"配置已保存: {fname}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"❌ {fname}: {e}")

    def _on_nav_changed(self, row):
        self.stack.setCurrentIndex(row)

    def refresh(self):
        """刷新所有页面数据（从配置文件重新加载）"""
        self._load_db_config()
        # sources 和 tasks 页面在构建时已加载，重新构建较重，暂不实现实时刷新
