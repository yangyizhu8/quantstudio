# -*- coding: utf-8 -*-
"""A6 验收轮 · 影子化环境与探测原语（只读；生产零接触安全闸）。

所有验收场景必须经本模块构造环境：任何关键路径落在生产根即抛异常中止。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\miniQMT策略实盘\QuantStudio")
SHADOW_ROOT = ROOT / "agent_workspace" / "shadow_lockprobe"
SHADOW_DB = SHADOW_ROOT / "quantstudio.db"
SHADOW_CONFIG = SHADOW_ROOT / "config"
PROD_DB = ROOT / "data" / "quantstudio.db"
EVIDENCE_DIR = ROOT / "docs" / "evidence"
ARTIFACT_DIR = ROOT / "agent_workspace" / "acceptance" / "artifacts"
PY = sys.executable


def shadow_env(extra=None):
    """返回注入影子根的进程环境（QUANTSTUDIO_DATA_ROOT 须进程启动前生效）。"""
    env = dict(os.environ)
    env["QUANTSTUDIO_DATA_ROOT"] = str(SHADOW_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def assert_shadow_paths():
    """安全闸：六条关键路径必须全部落在影子根，否则中止（生产零接触铁律）。"""
    code = (
        "import sys, json\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "from quantstudio._paths import DATA_ROOT, db_path, quarantine_db_path\n"
        "from quantstudio.pipeline.daemon_lifecycle import (\n"
        "    collector_run_lock_path, daemon_lock_path, daemon_status_path)\n"
        "print(json.dumps({'DATA_ROOT': str(DATA_ROOT), 'db_path': str(db_path()),\n"
        "  'quarantine': str(quarantine_db_path()),\n"
        "  'collector_run.lock': str(collector_run_lock_path()),\n"
        "  'daemon.lock': str(daemon_lock_path()),\n"
        "  'daemon_status': str(daemon_status_path())}, ensure_ascii=False))\n"
    )
    p = subprocess.run([PY, "-c", code], cwd=ROOT, env=shadow_env(),
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError("影子路径解析失败: " + (p.stderr or "")[-300:])
    paths = json.loads(p.stdout.strip().splitlines()[-1])
    shadow = str(SHADOW_ROOT).lower()
    bad = {k: v for k, v in paths.items()
           if shadow not in str(v).lower() or str(PROD_DB).lower() in str(v).lower()}
    if bad:
        raise RuntimeError("安全闸中止：路径未落影子根 -> " + json.dumps(bad, ensure_ascii=False))
    return paths


def prod_db_untouched():
    """生产库零接触核对（mtime 应早于本轮操作；无 .wal）。"""
    st = PROD_DB.stat()
    return {"prod_db": str(PROD_DB), "size": st.st_size,
            "last_write": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "wal_exists": (PROD_DB.parent / (PROD_DB.name + ".wal")).exists()}


def probe_rw(db, duration_s, interval_s, label):
    """RW 打开探测（打开即关，不写数据）。"""
    code = (
        "import sys, time, json\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "import duckdb\n"
        "db, dur, iv, label = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]\n"
        "ok = fail = 0; errs = []\n"
        "t0 = time.time()\n"
        "while time.time() - t0 < dur:\n"
        "    try:\n"
        "        c = duckdb.connect(db); c.close(); ok += 1\n"
        "    except Exception as e:\n"
        "        fail += 1\n"
        "        if len(errs) < 3: errs.append(str(e).replace(chr(10), ' ')[:180])\n"
        "    time.sleep(iv)\n"
        "total = ok + fail\n"
        "print(json.dumps({'label': label, 'ok': ok, 'fail': fail, 'total': total,\n"
        "  'fail_rate_pct': round(100.0*fail/total, 1) if total else 0.0,\n"
        "  'sample_errors': errs}, ensure_ascii=False))\n"
    )
    p = subprocess.run([PY, "-c", code, str(db), str(duration_s), str(interval_s), label],
                       cwd=ROOT, env=shadow_env(), capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        return {"label": label, "ok": 0, "fail": 0, "total": 0, "fail_rate_pct": None,
                "sample_errors": [(p.stderr or "")[-200:]]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def start_bg(args, label):
    """后台启动影子化子进程（stdout/stderr 落 artifacts）。"""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    log = ARTIFACT_DIR / ("bg_%s_%s.log" % (label, time.strftime("%H%M%S")))
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(args, cwd=ROOT, env=shadow_env(), stdout=fh, stderr=subprocess.STDOUT)
    proc.dsh_log = str(log)
    return proc


def wait_pid_exit(proc, timeout_s):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if proc.poll() is not None:
            return proc.returncode
        time.sleep(0.3)
    return None


def lock_held():
    """collector_run.lock 是否被占（影子根）。"""
    code = ("import sys\n"
            "from quantstudio.pipeline.daemon_lifecycle import CollectorRunLock\n"
            "lk = CollectorRunLock(timeout=0); got = lk.try_acquire()\n"
            "print('HELD' if not got else 'FREE')\n")
    p = subprocess.run([PY, "-c", code], cwd=ROOT, env=shadow_env(),
                       capture_output=True, text=True, encoding="utf-8")
    return "HELD" in (p.stdout or "")
