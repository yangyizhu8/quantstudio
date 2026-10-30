from quantstudio.pipeline.daemon import ResidentCollector


def _collector(tasks):
    collector = object.__new__(ResidentCollector)
    collector.tasks_cfg = {"tasks": tasks}
    collector._running = True
    return collector


def test_incremental_cycle_always_runs_quality_audit_after_task_failure():
    collector = _collector([
        {"name": "bad", "enabled": True, "mode": "incremental"},
        {"name": "good", "enabled": True, "mode": "incremental"},
        {"name": "full", "enabled": True, "mode": "full_range"},
    ])
    executed = []
    audits = []

    def execute(task):
        executed.append(task["name"])
        if task["name"] == "bad":
            raise RuntimeError("boom")

    collector._execute_task = execute
    collector._run_full_quality_audit = lambda **kw: audits.append("audit")
    collector._run_incremental_cycle()

    assert executed == ["bad", "good"]
    assert audits == ["audit"]


def test_run_once_runs_quality_audit_even_when_exception_escapes():
    collector = _collector([{"name": "bad", "enabled": True}])
    audits = []
    collector._execute_task = lambda task: (_ for _ in ()).throw(RuntimeError("boom"))
    # Phase 1 A5（2026-10-31）：_run_full_quality_audit 新增 tables= 关键字参数，
    # 测试替身签名随之兼容（判据未放宽：仍断言异常逃逸后审计照跑）。
    collector._run_full_quality_audit = lambda **kw: audits.append("audit")
    try:
        collector.run_once()
    except RuntimeError:
        pass
    assert audits == ["audit"]
