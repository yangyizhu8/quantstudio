"""防线包 #8 测试矩阵（U1-U10，设计 docs/governance-guard-pattern-precision-design.md v1.1）。

三事故回归：U1（bash 裸提及误拒复刻）/ U2（守护脚本文本复刻）/ U6（pid3 幻影）；
数值级断言：U9（逐 pattern 正/负样例核数）；一致性断言：U10（接口复用同源）。
"""
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import governance_snapshot as gs  # noqa: E402


def _scan_one(pid, name, cmdline, create_time=None):
    """构造单进程快照驱动 _data_side_tasks_running 的 mock 扫描。"""
    class P:
        def __init__(self):
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

        def create_time(self):
            if create_time is None:
                raise OSError("access denied")
            return create_time

    with mock.patch.object(gs.psutil, "process_iter", return_value=[P()]) if False else \
         mock.patch("psutil.process_iter", return_value=[P()]):
        return gs._data_side_tasks_running()


def test_u1_bash_inline_mention_no_hit():
    """U1（事故1复刻）：bash 持久 shell 历史命令文本含 pat 裸提及（无扩展名）→ 不命中。

    残留面登记（design v1.1 §3.1 边界）：bash -c 文本若含完整 `pat.ps1/.py` 字样，
    锚定无法与真实包装器调用区分 → 保守命中（误报方向=延迟快照零损失，可接受）；
    事故实况为裸提及，本用例忠实复刻。"""
    hits = _scan_one(25100, "bash.exe",
                     ["bash", "-c", ". /c/Users/Administrator/.zcode/cli/exec/shell-snapshots/snapshot-bash-178738014579 "
                                    "&& 恢复两个wrapper run_cloud_sync 与 repair_minutes 重命名操作历史 ..."])
    assert hits == [], hits


def test_u2_guard_script_text_no_hit():
    """U2（事故2复刻）：wait_until.sh 描述文本内嵌 pat → 不命中。"""
    hits = _scan_one(46048, "bash.exe",
                     ["bash", "/tmp/wait_until.sh", "2026-08-25 10:00",
                      "08-25 周二10:00 恢复两个wrapper（正确日期版）：run_cloud_sync 与 repair"])
    assert hits == [], hits


def test_u3_real_sh_wrapper_hit():
    """U3：真实 sh 包装器（pat+.sh 锚）→ 命中（防矫枉过正）。"""
    hits = _scan_one(100, "bash.exe", ["bash", "/app/scripts/run_cloud_sync.sh"])
    assert len(hits) == 1 and hits[0]["matched_pattern"] == "run_cloud_sync", hits


def test_u4_legacy_anchors_regression():
    """U4：ps1/py 锚与 python 词边界本体命中（回归不变）。"""
    hits = _scan_one(101, "powershell.exe",
                     ["powershell", "-File", "D:/x/scripts/run_cloud_sync.ps1"])
    assert hits and hits[0]["matched_pattern"] == "run_cloud_sync"
    hits = _scan_one(102, "python.exe",
                     [r"C:\Python\python.exe", r"D:\proj\data\sync_to_cloud\run_sync_now.py", "--all"])
    assert hits and hits[0]["matched_pattern"] == "run_sync_now"


def test_u9_word_boundary_numeric_assertions():
    """U9：逐 pattern 数值级断言——整词命中=1；前后缀拼接/内嵌标识符=0（P-D14b 教训）。"""
    res = gs._pat_word_res()
    for pat in gs.DATA_SIDE_PATTERNS:
        pos = [pat, f"xdir/{pat}.ps1", f"{pat} --flag", f"pre {pat} post"]
        neg = [f"x{pat}x", f"{pat}_notes.txt", f"my_{pat}", f"{pat}suffix_v2"]
        for s in pos:
            assert res[pat].search(s), f"{pat} 正例未命中: {s}"
        for s in neg:
            assert not res[pat].search(s), f"{pat} 负例误命中: {s}"


def test_u6_phantom_pid_filtered():
    """U6：pid=3 幻影（不可读 cmdline + create_time 拒绝）→ 不计入 fail_closed。"""
    assert _scan_one(3, "python.exe", None) == []
    # 真实 SYSTEM python（pid 正常 + create_time 可得）→ 仍 fail_closed（红线）
    hits = _scan_one(20000, "python.exe", None, create_time=1700000000.0)
    assert len(hits) == 1 and hits[0]["matched_pattern"] == "fail_closed", hits


def test_u10_query_uses_guard_enum():
    """U10：query 接口复用 guard 枚举（同源）；同快照输出逐字段相等。"""
    hits = [{"pid": 7, "cmd": "python run_sync_now.py", "matched_pattern": "run_sync_now"}]
    with mock.patch.object(gs, "_data_side_tasks_running", return_value=hits):
        import data_side_process_query as q
        assert q.query() is hits or q.query() == hits
    # 独立实现漂移防线：接口模块不得含独立 process_iter 扫描
    src = io.open(Path(q.__file__), encoding="utf-8").read()
    assert "process_iter" not in src, "query 接口必须复用 guard 枚举，禁止独立实现"


def test_u7_query_interface_output_contract():
    """U7：接口输出契约——count/hits/锚类型说明，JSON 可解析。"""
    r = subprocess.run([sys.executable, str(Path(gs.__file__).parent / "data_side_process_query.py")],
                       capture_output=True, text=True, encoding="utf-8")
    out = json.loads(r.stdout)
    assert out["count"] == len(out["hits"]) and isinstance(out["hits"], list)
