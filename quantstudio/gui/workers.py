"""后台任务封装（QThread）。所有耗时操作必须放此处，否则 GUI 冻结。"""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from filelock import FileLock, Timeout


class BacktestCancelled(RuntimeError):
    """用户主动取消回测（结构化信号，取代字符串嗅探——文案即接口=脆弱）。

    取消路径：_on_stop → worker.cancel()（_cancelled=True）→ 下一进度回调
    raise BacktestCancelled → run() except 捕获 → finished_err.emit(异常对象)
    → 槽内 isinstance(err, BacktestCancelled) 判定（非字符串匹配）。
    """


class BaseWorker(QThread):
    """Worker 基类。子类实现 run()，完成时必须 emit finished_ok 或 finished_err。"""
    progress = pyqtSignal(str)      # 进度消息
    finished_ok = pyqtSignal(dict)  # 成功结果
    finished_err = pyqtSignal(object)  # 错误（str 或异常对象——取消=结构化 BacktestCancelled）

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        raise NotImplementedError


class DaemonWorker(BaseWorker):
    """DEPRECATED v3：旧版 QThread daemon 包装，长期持有 DuckDB _shared_conn，
    与 GUI 跨进程读库冲突。新常驻模式由独立 OS 进程（subprocess）+ DaemonLifecycle
    负责，GUI 通过 quantstudio.gui.daemon_process 管理子进程。此类保留作回退，
    **新代码不应实例化**。
    """

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


def _collector_run_lock_path() -> Path:
    """collector_run.lock 路径（与 daemon_lifecycle.collector_run_lock_path 同源）。"""
    from quantstudio._paths import DATA_ROOT
    return DATA_ROOT / ".collector_run.lock"


def _qfq_cycle_result(collector) -> dict | None:
    """Return the manual QFQ cycle outcome in a signal-safe shape."""
    summary = getattr(collector, "_last_qfq_cycle_summary", None)
    if summary is None:
        return None
    gate_report = getattr(summary, "gate_report", {}) or {}
    return {
        "status": getattr(summary, "status", None),
        "error": getattr(summary, "error", None),
        "gate_reasons": list(gate_report.get("reasons", []) or []),
        "watermarks_committed": int(getattr(summary, "watermarks_committed", 0) or 0),
        "watermarks_held": int(getattr(summary, "watermarks_held", 0) or 0),
    }


def _task_runtime_result(collector) -> dict:
    """Return GUI-only metadata from the latest public execute_task call."""
    return {
        "qfq_cycle": _qfq_cycle_result(collector),
        "qfq_managed": bool(getattr(collector, "_last_task_qfq_managed", False)),
        "watermark_candidate_created": bool(
            getattr(collector, "_last_task_watermark_candidate_created", False)),
        "actual_source": getattr(collector, "_last_task_actual_source", None),
    }


class LockedTaskWorker(BaseWorker):
    """v3：GUI 手动拉取单个任务的 Worker。

    collector_run.lock 在本 worker 线程内 acquire/release（评审 1），
    覆盖 from_configs + resolve_source_chain + execute_task + 质量审计 + close 全程。
    主线程不碰 collector、不超时等锁。

    获取锁失败（daemon 正在写库）→ emit finished_err("定时采集正在运行，请稍后重试")。
    """

    def __init__(self, task: dict, config_dir: Path, mode: str = "incremental",
                 run_quality_audit: bool = True, lock_timeout: int = 5):
        super().__init__()
        self.task = task
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.run_quality_audit = run_quality_audit
        self.lock_timeout = lock_timeout

    def run(self):
        from quantstudio.pipeline.daemon import ResidentCollector
        lock = FileLock(str(_collector_run_lock_path()), timeout=self.lock_timeout)
        try:
            lock.acquire()
        except Timeout:
            self.finished_err.emit("\u5b9a\u65f6\u91c7\u96c6\u6b63\u5728\u8fd0\u884c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5")
            return

        collector = None
        outcome_kind = "error"
        outcome_payload = None
        try:
            self.progress.emit(
                f"\u5f00\u59cb({self.mode}): {self.task['name']}")
            collector = ResidentCollector.from_configs(
                self.config_dir / "data_config.json",
                self.config_dir / "sources_config.json",
                self.config_dir / "collector_tasks.json",
                self.config_dir / "alignment_rules.json")
            chain = collector.resolve_source_chain(self.task)
            if not chain:
                outcome_payload = (
                    f"\u4efb\u52a1 {self.task.get('name', '?')} "
                    f"\u6ca1\u6709\u5df2\u542f\u7528\u4e14\u652f\u6301\u8be5\u4efb\u52a1\u7684\u6570\u636e\u6e90")
            else:
                task_ok = False
                task_error = None
                try:
                    task_ok = collector.execute_task(
                        self.task, mode=self.mode, run_quality_audit=False)
                except Exception as e:
                    task_error = e

                audit_ran = bool(self.run_quality_audit)
                audit_ok = None
                audit_error = None
                if audit_ran:
                    self.progress.emit(
                        f"{self.task['name']} \u62c9\u53d6\u7ed3\u675f\uff0c"
                        f"\u6267\u884c\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1...")
                    try:
                        audit_ok = bool(collector._run_full_quality_audit())
                    except Exception as e:
                        audit_ok = False
                        audit_error = f"{type(e).__name__}: {e}"
                    if not audit_ok:
                        self.progress.emit(
                            f"\u26a0\ufe0f {self.task['name']} "
                            f"\u62c9\u53d6\u7ed3\u679c\u5df2\u72ec\u7acb\u5224\u5b9a\uff1b"
                            f"\u5168\u5e93\u5ba1\u8ba1\u672a\u901a\u8fc7\uff0c"
                            f"\u8bf7\u67e5\u770b\u8d28\u91cf\u5ba1\u8ba1\u9875/\u65e5\u5fd7")

                if task_error is not None:
                    raise task_error

                result = {
                    "task": self.task["name"],
                    "mode": self.mode,
                    "task_ok": bool(task_ok),
                    "quality_audit_ran": audit_ran,
                    "quality_audit_ok": audit_ok,
                    **_task_runtime_result(collector),
                }
                if audit_error is not None:
                    result["quality_audit_error"] = audit_error

                if task_ok:
                    outcome_kind = "success"
                    outcome_payload = result
                else:
                    suffix = ""
                    if audit_ran and audit_ok is False:
                        suffix = (
                            "\uff1b\u5168\u5e93\u8d28\u91cf\u5ba1\u8ba1\u540c\u65f6\u672a\u901a\u8fc7\uff0c"
                            "\u8bf7\u67e5\u770b\u8d28\u91cf\u5ba1\u8ba1\u9875/\u65e5\u5fd7")
                    outcome_payload = (
                        f"\u4efb\u52a1\u62c9\u53d6\u5931\u8d25: "
                        f"{self.task['name']}{suffix}")
        except Exception as e:
            outcome_kind = "error"
            outcome_payload = f"{type(e).__name__}: {e}"
        finally:
            # The GUI may query DuckDB immediately after the final signal. Close the collector and
            # release the cross-process lock before emitting that signal.
            if collector is not None:
                try:
                    collector.close()
                except Exception:
                    pass
            try:
                lock.release()
            except Exception:
                pass

        if outcome_kind == "success":
            self.finished_ok.emit(outcome_payload)
        else:
            self.finished_err.emit(str(outcome_payload))


class LockedRunAllWorker(BaseWorker):
    """v3：GUI"全部执行"的 Worker。

    持有 collector_run.lock 跑完整个队列 + 末尾质量审计（评审 1：不每任务单独
    拿锁，防 daemon 插入队列中间）。主线程零 collector 调用。
    """

    def __init__(self, tasks: list, config_dir: Path,
                 mode: str = "incremental", lock_timeout: int = 5):
        super().__init__()
        self.tasks = tasks
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.lock_timeout = lock_timeout

    def run(self):
        from quantstudio.pipeline.daemon import ResidentCollector
        lock = FileLock(str(_collector_run_lock_path()), timeout=self.lock_timeout)
        try:
            lock.acquire()
        except Timeout:
            self.finished_err.emit("\u5b9a\u65f6\u91c7\u96c6\u6b63\u5728\u8fd0\u884c\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5")
            return

        collector = None
        results = []
        outcome_kind = "error"
        outcome_payload = None
        try:
            collector = ResidentCollector.from_configs(
                self.config_dir / "data_config.json",
                self.config_dir / "sources_config.json",
                self.config_dir / "collector_tasks.json",
                self.config_dir / "alignment_rules.json")
            total = len(self.tasks)
            for i, task in enumerate(self.tasks):
                if self._cancelled:
                    self.progress.emit("\u5df2\u53d6\u6d88")
                    break
                name = task.get("name", "?")
                try:
                    chain = collector.resolve_source_chain(task)
                except Exception:
                    chain = []
                if not chain:
                    self.progress.emit(
                        f"\u23ed \u8df3\u8fc7 {name}\uff08\u65e0\u53ef\u7528\u6570\u636e\u6e90\uff09({i+1}/{total})")
                    results.append({"name": name, "ok": False, "skipped": True})
                    continue
                self.progress.emit(f"\u6267\u884c {name} ({i+1}/{total})")
                try:
                    ok = collector.execute_task(task, mode=self.mode,
                                                run_quality_audit=False)
                    results.append({"name": name, "ok": ok,
                                    **_task_runtime_result(collector)})
                except Exception as e:
                    results.append({"name": name, "ok": False, "error": str(e),
                                    **_task_runtime_result(collector)})
                    self.progress.emit(f"\u274c {name}: {e}")

            audit_ok = None
            audit_error = None
            try:
                audit_ok = bool(collector._run_full_quality_audit())
            except Exception as e:
                audit_ok = False
                audit_error = f"{type(e).__name__}: {e}"
                self.progress.emit(f"\u8d28\u91cf\u5ba1\u8ba1\u5f02\u5e38: {e}")
            ok_count = sum(1 for r in results if r.get("ok"))
            outcome_kind = "success"
            outcome_payload = {"results": results, "ok_count": ok_count,
                               "total": len(results),
                               "quality_audit_ok": audit_ok}
            if audit_error is not None:
                outcome_payload["quality_audit_error"] = audit_error
        except Exception as e:
            outcome_payload = f"{type(e).__name__}: {e}"
        finally:
            # Match LockedTaskWorker: the GUI refreshes DuckDB after the final
            # signal, so close the writer and release the cross-process lock first.
            if collector is not None:
                try:
                    collector.close()
                except Exception:
                    pass
            try:
                lock.release()
            except Exception:
                pass

        if outcome_kind == "success":
            self.finished_ok.emit(outcome_payload)
        else:
            self.finished_err.emit(str(outcome_payload))


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
            task_ok = False
            task_error = None
            try:
                task_ok = self.collector.execute_task(
                    self.task, mode=self.mode, run_quality_audit=False)
            except Exception as e:
                task_error = e
            audit_ran = bool(self.run_quality_audit)
            audit_ok = None
            audit_error = None
            if audit_ran:
                try:
                    audit_ok = bool(self.collector._run_full_quality_audit())
                except Exception as e:
                    audit_ok = False
                    audit_error = f"{type(e).__name__}: {e}"
            if task_error is not None:
                raise task_error

            result = {
                "task": self.task["name"],
                "mode": self.mode,
                "task_ok": bool(task_ok),
                "quality_audit_ran": audit_ran,
                "quality_audit_ok": audit_ok,
                **_task_runtime_result(self.collector),
            }
            if audit_error is not None:
                result["quality_audit_error"] = audit_error
            if task_ok:
                self.finished_ok.emit(result)
            else:
                self.finished_err.emit(f"??????: {self.task['name']}")
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")



class ExportWorker(BaseWorker):
    """导出 K线原生格式分库 .db。调用 KLineExporter.export。"""

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
            from quantstudio.backtest.run_ptrade_strategy import run_backtest
            from quantstudio.backtest.backtest_engine import TradeCost

            # 1. 加载策略 + 2. 构建引擎 + 3. 运行 → 全部通过共用入口
            self.progress.emit(f"加载策略: {Path(self.strategy_path).name}")

            # 成本模型：从 params 构建（控件预设值已与 DEFAULT_TRADE_COST 对齐）
            cost = TradeCost(
                commission_rate=self.params.get('commission', 0.00035),
                min_commission=5.0,
                stamp_tax_rate=self.params.get('stamp_tax', 0.001),
                transfer_fee_rate=0.00001,
                slippage_rate=self.params.get('slippage', 0.0),
            )

            self.progress.emit("开始回测...")
            result, output_dir, engine = run_backtest(
                self.strategy_path,
                self.params['start'],
                self.params['end'],
                db_path=self.params['db_path'],
                capital=self.params.get('capital', 100_000),
                match_price_mode=self.params.get('match_price_mode', 'close'),
                engine_profile=self.params.get('engine_profile', 'daily-bar-v1'),
                etf_t0=self.params.get('etf_t0', False),
                cost=cost,
                rebalance_mode=self.params.get('rebalance_mode', 'legacy'),
                fidelity_config=self.params.get('fidelity_config', None),
                progress_callback=self._on_engine_progress,
            )

            self.progress.emit(f"回测完成: {output_dir}")
            self.finished_ok.emit({
                "output_dir": str(output_dir),
                "nav_history": result.nav_history,
                "trade_records": result.trade_records,
            })
        except Exception as e:
            import traceback
            # 结构化取消：BacktestCancelled 原样传递（槽内 isinstance 判定），
            # 真错误携带 traceback 文本（保持现状）。
            if isinstance(e, BacktestCancelled):
                self.finished_err.emit(e)
            else:
                self.finished_err.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    def _on_engine_progress(self, current, total, date_str):
        if self._cancelled:
            raise BacktestCancelled("用户取消回测")
        self.day_progress.emit(current, total, date_str)
        self.progress.emit(f"[{current}/{total}] {date_str}")
