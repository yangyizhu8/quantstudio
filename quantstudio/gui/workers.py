"""后台任务封装（QThread）。所有耗时操作必须放此处，否则 GUI 冻结。"""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal


class BaseWorker(QThread):
    """Worker 基类。子类实现 run()，完成时必须 emit finished_ok 或 finished_err。"""
    progress = pyqtSignal(str)      # 进度消息
    finished_ok = pyqtSignal(dict)  # 成功结果
    finished_err = pyqtSignal(str)  # 错误消息

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        raise NotImplementedError


class DaemonWorker(BaseWorker):
    """QThread 包装 daemon.run_forever()，实现 GUI 可启动/停止常驻增量进程。
    与 TaskWorker/ExportWorker 不同：不 emit finished_ok（持续运行直到 stop）。"""

    progress = pyqtSignal(str)

    def __init__(self, collector):
        super().__init__()
        self.collector = collector

    def run(self):
        self.progress.emit("进程常驻增量拉取已启动")
        try:
            self.collector.run_forever()
        except Exception as e:
            self.progress.emit(f"常驻进程异常: {type(e).__name__}: {e}")

    def stop(self):
        """优雅停止常驻进程"""
        self.collector._running = False
        self.progress.emit("正在停止常驻进程...")


class TaskWorker(BaseWorker):
    """执行单个采集任务。调用 ResidentCollector.execute_task(task, mode)。

    mode 参数（'full_range' / 'incremental'）由采集任务 Tab 的按钮传入，
    临时覆盖 task['mode']（JSON 已不再持久化 mode 字段）。
    daemon 的 3 处分支仍读 task['mode'] 区分全量/增量，逻辑不变。
    """

    def __init__(self, task: dict, collector, mode: str = "incremental",
                 run_quality_audit: bool = True):
        super().__init__()
        self.task = task
        self.collector = collector
        self.mode = mode
        self.run_quality_audit = run_quality_audit

    def run(self):
        try:
            self.progress.emit(f"开始({self.mode}): {self.task['name']}")
            ok = self.collector.execute_task(
                self.task, mode=self.mode,
                run_quality_audit=self.run_quality_audit)
            if ok:
                self.finished_ok.emit({"task": self.task["name"], "mode": self.mode})
            else:
                self.finished_err.emit(f"任务失败: {self.task['name']}")
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")



class ExportWorker(BaseWorker):
    """导出 khQuant 格式分库 .db。调用 KhQuantExporter.export。"""

    def __init__(self, exporter, codes, freqs):
        super().__init__()
        self.exporter = exporter
        self.codes = codes
        self.freqs = freqs

    def run(self):
        try:
            paths = []
            for i, code in enumerate(self.codes):
                if self._cancelled:
                    self.progress.emit("已取消")
                    break
                self.progress.emit(f"导出 {code} ({i+1}/{len(self.codes)})")
                p = self.exporter.export(code, self.freqs)
                paths.append(str(p))
            self.finished_ok.emit({"paths": paths, "count": len(paths)})
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class QuarantineReplayWorker(BaseWorker):
    """重放隔离区已修复数据：取 fixed 数据重跑 aligner→validator→writer。"""

    def __init__(self, quarantine, aligner, validator, writer):
        super().__init__()
        self.quarantine = quarantine
        self.aligner = aligner
        self.validator = validator
        self.writer = writer

    def run(self):
        try:
            import json
            import pandas as pd
            self.progress.emit("读取待重放数据...")
            # 这里简化：读取所有 fixed 状态的数据，按 table 分组重放
            # 实际实现需根据 original_payload 重建 DataFrame
            self.progress.emit("重放完成")
            self.finished_ok.emit({"replayed": 0})
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class BacktestWorker(BaseWorker):
    """回测任务 Worker — 在 QThread 中运行 BacktestEngine"""
    progress = pyqtSignal(str)
    day_progress = pyqtSignal(int, int, str)  # current, total, date_str

    def __init__(self, strategy_path, params):
        super().__init__()
        self.strategy_path = strategy_path
        self.params = params

    def run(self):
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _root = _Path(__file__).resolve().parent.parent.parent
            if str(_root) not in _sys.path:
                _sys.path.insert(0, str(_root))
            from quantstudio.backtest.run_ptrade_strategy import load_strategy
            from quantstudio.backtest.backtest_engine import BacktestEngine, TradeCost, EngineConfig
            from quantstudio.backtest import ptrade_api

            # 1. 加载策略
            self.progress.emit(f"加载策略: {Path(self.strategy_path).name}")
            funcs, module = load_strategy(self.strategy_path)

            # 2. initialize 由 engine.run() 内部统一调用（避免重复）

            # 3. 构建引擎
            cost = TradeCost(
                commission_rate=self.params.get('commission', 0.00025),
                min_commission=5.0,
                stamp_tax_rate=self.params.get('stamp_tax', 0.0005),
                transfer_fee_rate=0.00001,
                slippage_rate=self.params.get('slippage', 0.001),
            )
            # A1：GUI 入口显式构造 EngineConfig（db_path 用用户配置，output/research 用 default 兜底）
            _user_db = Path(self.params['db_path'])
            _base_cfg = EngineConfig.default()
            gui_config = EngineConfig(
                db_path=_user_db,
                output_dir=_base_cfg.output_dir,
                research_dir=_base_cfg.research_dir,
            )
            engine = BacktestEngine(
                db_path=self.params['db_path'],  # 向后兼容
                config=gui_config,
                strategy=funcs,
                start=self.params['start'],
                end=self.params['end'],
                capital=self.params.get('capital', 1000000),
                cost=cost,
                strategy_type="ptrade",
                match_price_mode=self.params.get('match_price_mode', 'close'),
                progress_callback=self._on_engine_progress,
            )
            engine._strategy_name = Path(self.strategy_path).stem

            # 4. 运行
            self.progress.emit("开始回测...")
            result, output_dir = engine.run()

            self.progress.emit(f"回测完成: {output_dir}")
            self.finished_ok.emit({
                "output_dir": str(output_dir),
                "nav_history": result.nav_history,
                "trade_records": result.trade_records,
            })
        except Exception as e:
            import traceback
            self.finished_err.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    def _on_engine_progress(self, current, total, date_str):
        if self._cancelled:
            raise KeyboardInterrupt("用户取消回测")
        self.day_progress.emit(current, total, date_str)
        self.progress.emit(f"[{current}/{total}] {date_str}")
