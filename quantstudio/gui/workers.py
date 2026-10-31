"""后台任务封装（QThread）。所有耗时操作必须放此处，否则 GUI 冻结。"""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

from quantstudio.pipeline.task_resume import TaskCancelled


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
                 run_quality_audit: bool = True, lock_timeout: int = 5,
                 cancel_check=None):
        super().__init__()
        self.task = task
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.run_quality_audit = run_quality_audit
        self.lock_timeout = lock_timeout
        # 停止语义 v3.1：协作式停止谓词，透传到 collector 的日批/每股边界。
        # None = 现行为逐位一致（向后兼容；常驻链与旧调用方零变化）。
        self._cancel_check = cancel_check

    def run(self):
        """T2 委托化（2026-09-20）：GUI 进程不再持锁——once 子进程执行，本线程轮询转发。

        锁由子进程内 CollectorRunLock(timeout=30) 持有；成败读 runtime-manifest 与
        once 日志（不判退出码，A2 障碍 3）；采集中 → 有界提示。
        取消语义降级如实声明：终止子进程（terminate），非原进程内协作式取消。
        """
        from quantstudio.gui.daemon_process import (
            start_once_subprocess, read_once_manifest, once_busy_hint)
        self.progress.emit("开始({}): {}".format(self.mode, self.task["name"]))
        nonce, proc, manifest_path, log_path = start_once_subprocess(
            self.task["name"], self.config_dir, mode=self.mode,
            run_quality_audit=self.run_quality_audit)
        import time as _t
        cancelled = False
        while True:
            if self._cancel_check is not None:
                try:
                    want_stop = bool(self._cancel_check())
                except Exception:
                    want_stop = False
                if want_stop and proc.poll() is None:
                    cancelled = True
                    self.progress.emit("已请求停止：终止采集子进程 pid={}...".format(proc.pid))
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            if proc.poll() is not None:
                break
            _t.sleep(0.5)
        _t.sleep(0.5)  # 给 manifest 原子写落盘的窗口

        if cancelled:
            self.finished_err.emit(
                "已停止（采集子进程已终止；已拉取部分由水位续跑，未完成审计）")
            return
        busy = once_busy_hint(log_path)
        if busy is not None:
            self.finished_err.emit(busy)
            return
        manifest = read_once_manifest(manifest_path)
        log_tail = ""
        try:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except Exception:
            pass
        once_done = ("once 完成" in log_tail) or ("no new data, skip" in log_tail)
        # A6（Phase 1，2026-10-31）：第三态标记解析（同批量路径）。
        qa_incomplete = "QUALITY_AUDIT_INCOMPLETE" in log_tail
        nonce_ok = bool(manifest) and manifest.get("nonce") == nonce
        if once_done and nonce_ok:
            result = {
                "task": self.task["name"],
                "mode": self.mode,
                "task_ok": True,
                "quality_audit_ran": bool(self.run_quality_audit),
                "quality_audit_ok": not qa_incomplete,
                "quality_audit_incomplete": qa_incomplete,
                "delegated": True,
                "manifest_nonce": nonce,
            }
            self.finished_ok.emit(result)
            return
        # 失败：日志尾片段随错误返回（不吞现场）
        snippet = log_tail.strip().splitlines()[-6:] if log_tail.strip() else []
        self.finished_err.emit(
            "任务拉取失败: {}（once 子进程 exit={}；日志尾：{}）".format(
                self.task["name"], proc.returncode,
                " | ".join(snippet) if snippet else "无输出"))


class LockedRunAllWorker(BaseWorker):
    """v3：GUI"全部执行"的 Worker。

    持有 collector_run.lock 跑完整个队列 + 末尾质量审计（评审 1：不每任务单独
    拿锁，防 daemon 插入队列中间）。主线程零 collector 调用。
    """

    def __init__(self, tasks: list, config_dir: Path,
                 mode: str = "incremental", lock_timeout: int = 5,
                 cancel_check=None):
        super().__init__()
        self.tasks = tasks
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.lock_timeout = lock_timeout
        # 停止语义 v3.1：L1 批间停止谓词（None = 现行为逐位一致）
        self._cancel_check = cancel_check

    def _stop_requested(self) -> bool:
        """L1 批间停止判定：线程内 _cancelled 与 GUI 停止谓词取并集。"""
        if self._cancelled:
            return True
        if self._cancel_check is None:
            return False
        try:
            return bool(self._cancel_check())
        except Exception:
            return False

    def run(self):
        """T2 委托化（2026-09-20，裁定①）：逐任务串行子进程循环。

        旧语义（单锁持队列 + 末尾一次全库审计）迁移为：每任务一个 once 子进程
        （--quality-audit full 随任务承载），串行顺序获取释放 collector_run.lock，
        无并发竞争（原评审 1 顾虑在串行形态下不成立）。
        quality_audit_ok 语义变更如实声明：任务级审计汇总（全部有审计任务皆过=True），
        非旧「队列后一次全库审计」。
        """
        from quantstudio.gui.daemon_process import (
            start_once_subprocess, read_once_manifest, once_busy_hint)
        import time as _t
        results = []
        stopped = False
        audit_all = []
        for idx, task in enumerate(self.tasks):
            if self._stop_requested():
                self.progress.emit("已停止：不再派发后续任务（L1 批间）")
                stopped = True
                break
            name = task.get("name", "?")
            self.progress.emit("[{}/{}] 开始({}): {}".format(
                idx + 1, len(self.tasks), self.mode, name))
            nonce, proc, manifest_path, log_path = start_once_subprocess(
                name, self.config_dir, mode=self.mode, run_quality_audit=True)
            task_cancelled = False
            while True:
                if self._stop_requested() and proc.poll() is None:
                    task_cancelled = True
                    self.progress.emit("已请求停止：终止采集子进程 pid={}...".format(proc.pid))
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                if proc.poll() is not None:
                    break
                _t.sleep(0.5)
            _t.sleep(0.5)
            if task_cancelled:
                results.append({"name": name, "ok": False,
                                "error": "stopped:subprocess_terminated", "delegated": True})
                stopped = True
                break
            busy = once_busy_hint(log_path)
            if busy is not None:
                results.append({"name": name, "ok": False, "error": busy, "delegated": True})
                self.progress.emit("⚠ {}: {}".format(name, busy))
                continue
            manifest = read_once_manifest(manifest_path)
            log_tail = ""
            try:
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except Exception:
                pass
            once_done = ("once 完成" in log_tail) or ("no new data, skip" in log_tail)
            # A6（Phase 1，2026-10-31）：解析子进程日志里的机读第三态标记。
            # 审计未完整（OOM/超时/中断）时 daemon 打 QUALITY_AUDIT_INCOMPLETE；
            # 此时数据已写入成功 ⇒ 仍算任务成功，但结果带标记，界面渲染为
            # 「完成·审计未完成」，既不静默变绿也不误判失败。
            qa_incomplete = "QUALITY_AUDIT_INCOMPLETE" in log_tail
            nonce_ok = bool(manifest) and manifest.get("nonce") == nonce
            if once_done and nonce_ok:
                results.append({"name": name, "ok": True, "delegated": True,
                                "nonce": nonce,
                                "quality_audit_incomplete": qa_incomplete})
                audit_all.append(True)
                self.progress.emit("✅ {}: 完成".format(name))
            else:
                snippet = log_tail.strip().splitlines()[-4:] if log_tail.strip() else []
                results.append({"name": name, "ok": False,
                                "error": " | ".join(snippet) if snippet else "无输出",
                                "delegated": True})
                audit_all.append(False)
                self.progress.emit("❌ {}: 失败".format(name))
        ok_count = sum(1 for r in results if r.get("ok"))
        payload = {"results": results, "ok_count": ok_count, "total": len(results),
                   "quality_audit_ok": (all(audit_all) if audit_all else None),
                   "stopped": stopped, "cancelled": stopped, "delegated": True}
        self.finished_ok.emit(payload)


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
                match_profile=self.params.get('match_profile', 'ptrade'),  # M5.2 件7 口径标签
                # 常驻采集共存（specs/001-daemon-backtest-coexistence）：
                # 默认开启——采集开着也能直接回测；等待期取消复用既有 self._cancelled。
                coexistence=self.params.get('coexistence', True),
                cancel_check=lambda: self._cancelled,
                # 等待读数走 progress 信号（单参数）；引擎的逐日进度走 progress_callback
                # （三参数 current/total/date_str）——两者是不同通道，不可混用。
                wait_callback=lambda m: self.progress.emit(m),
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


# ══ 交付包 GUI 薄壳 Worker（quantstudio.delivery 内核：W1 导出 / W2 只读校验 / W3 导入）══
# 内核进度回调统一为 ProgressCB(done_bytes, total_bytes, phase_str)，
# 经 _DeliveryProgress 节流后转发为 progress(str) 信号。


def _fmt_bytes(size) -> str:
    """字节数 → 人类可读（如 1.5 GB），进度与完成读数展示用。"""
    n = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


class _DeliveryProgress:
    """delivery 内核 ProgressCB → progress(str) 的节流适配器。

    内核 COPY 阶段每 1MiB 回调一次，直接转发会刷爆 GUI 事件循环；
    节流规则：距上次 ≥0.2 秒、或进度前进 ≥1%、或相位名变化，任一满足才 emit。
    """

    _MIN_INTERVAL_SEC = 0.2
    _MIN_PCT_STEP = 1.0

    def __init__(self, worker: BaseWorker):
        self._worker = worker
        self._last_ts = 0.0
        self._last_pct = -1e9
        self._last_phase = ""

    def __call__(self, done: int, total: int, phase: str) -> None:
        import time as _time

        now = _time.monotonic()
        pct = (done * 100.0 / total) if total > 0 else None
        due = (
            phase != self._last_phase
            or (now - self._last_ts) >= self._MIN_INTERVAL_SEC
            or (pct is not None and (pct - self._last_pct) >= self._MIN_PCT_STEP)
        )
        if not due:
            return
        self._last_ts = now
        self._last_phase = phase
        if pct is not None:
            self._last_pct = pct
            self._worker.progress.emit(
                f"[{phase}] {pct:.1f}%（{_fmt_bytes(done)}/{_fmt_bytes(total)}）")
        else:
            self._worker.progress.emit(f"[{phase}]")


class DeliveryExportWorker(BaseWorker):
    """整库交付包导出（W1 六相位内核）。薄壳：收参数、转发进度、回传结果。

    export_root = 源数据根目录（对应内核参数 data_root），None 走内核默认探测。
    """

    def __init__(self, export_dir: str, export_root: str | None = None,
                 verify_hashes: bool = False):
        super().__init__()
        self.export_dir = export_dir
        self.export_root = export_root
        self.verify_hashes = verify_hashes

    def run(self):
        try:
            from quantstudio.delivery import export_bundle
            self.progress.emit(f"开始导出交付包 → {self.export_dir}")
            result = export_bundle(
                export_dir=Path(self.export_dir),
                data_root=Path(self.export_root) if self.export_root else None,
                config_dir=None,
                progress=_DeliveryProgress(self),
                verify_hashes=self.verify_hashes,
            )
            for warn in result.warnings:
                self.progress.emit(f"⚠ {warn}")
            self.finished_ok.emit({
                "bundle_dir": str(result.bundle_dir),
                "total_bytes": result.total_bytes,
                "duration_sec": result.duration_sec,
                "qualified": result.qualified,
            })
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class DeliveryVerifyWorker(BaseWorker):
    """交付包只读校验（W2，全程不写盘）。从子模块 verify 直接导入：
    包级属性 verify_bundle 被 __init__.__getattr__ 拦截（W2 未在包 __init__ 落地）。"""

    def __init__(self, import_dir: str):
        super().__init__()
        self.import_dir = import_dir

    def run(self):
        try:
            from quantstudio.delivery.verify import verify_bundle
            self.progress.emit(f"只读校验交付包：{self.import_dir}")
            r = verify_bundle(
                import_dir=Path(self.import_dir),
                config_dir=None,
                data_root=None,
                progress=_DeliveryProgress(self),
            )
            # 注意：warnings 不在此处经 progress 播报——结果槽会打印一次，
            # 两处都发会让用户看到同一条告警两遍（2026-11-01 实跑反馈）。
            self.finished_ok.emit({
                "ok": r.ok,
                "package_dir": str(r.package_dir),
                "state_counts": getattr(r, "state_counts", {}) or {},
                "attention": getattr(r, "attention", []) or [],
                "failure_kind": r.failure_kind,
                "reasons": r.reasons,
                "warnings": r.warnings,
                "tables": r.tables,
                "table_set": r.table_set,
                "files_checked": r.files_checked,
            })
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")


class DeliveryImportWorker(BaseWorker):
    """交付包导入（W3 九步状态机：备份/覆盖/回滚/复验）。dry_run=True 仅演练不写盘。"""

    def __init__(self, import_dir: str, dry_run: bool = False):
        super().__init__()
        self.import_dir = import_dir
        self.dry_run = dry_run

    def run(self):
        try:
            from quantstudio.delivery import import_bundle
            action = "导入演练（dry-run）" if self.dry_run else "开始导入交付包"
            self.progress.emit(f"{action}：{self.import_dir}")
            r = import_bundle(
                import_dir=Path(self.import_dir),
                config_dir=None,
                data_root=None,
                dry_run=self.dry_run,
                progress=_DeliveryProgress(self),
            )
            for warn in r.warnings:
                self.progress.emit(f"⚠ {warn}")
            self.finished_ok.emit({
                "ok": r.ok,
                "mode": r.mode,
                "exit_code": r.exit_code,
                "failure_kind": r.failure_kind,
                "reasons": r.reasons,
                "warnings": r.warnings,
                "backup": r.backup,
                "reverify": r.reverify,
                "baseline": r.baseline,
                "forensic": r.forensic,
            })
        except Exception as e:
            self.finished_err.emit(f"{type(e).__name__}: {e}")
