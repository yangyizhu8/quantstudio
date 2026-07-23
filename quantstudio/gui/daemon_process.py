"""GUI 侧常驻 daemon 进程管理（纯逻辑，无 Qt 依赖）。

封装 daemon 子进程的启停、状态查询、身份校验、强制停止。Qt 层（task_tab）
通过 QTimer 轮询这些函数实现异步状态同步，不在主线程阻塞。

与 quantstudio/pipeline/daemon_lifecycle.py 共享状态文件契约：
  - daemon_status.json     只读（GUI）/ 读写（daemon）
  - daemon_stop.request    只写（GUI）/ 只读消费（daemon）
  - daemon_run_state.json  只读（GUI，用于显示今日轮次状态）

核心原则（评审决议）：
  - GUI 的 Popen 不写正式 status 文件；只有 daemon 拿到 .daemon.lock 后才写。
  - 停止优先优雅（写 stop.request）；超时后弹窗，用户确认才走强制路径。
  - 强制路径必须 psutil 五验全过 + 进程消失，才清对应 token 的 status。
  - stale 清理仅限"进程已不存在"；AccessDenied 不清只提示。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple

import psutil

# 复用 daemon_lifecycle 的路径常量和校验函数（单一真源）
from quantstudio.pipeline.daemon_lifecycle import (
    daemon_status_path,
    daemon_stop_request_path,
    read_daemon_status,
    verify_daemon_identity,
    _data_root,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 状态读取（GUI 只读）
# ---------------------------------------------------------------------------

def is_daemon_running() -> bool:
    """判断 daemon 是否在运行（status 存在 + psutil 五验 alive）。"""
    status = read_daemon_status()
    if status is None:
        return False
    return verify_daemon_identity(status) == "alive"


def get_daemon_status() -> Optional[dict]:
    """读取 daemon status（用于显示按钮状态、token 匹配等）。"""
    return read_daemon_status()


# ---------------------------------------------------------------------------
# 启动（评审 1+8：Popen 不写 status，bootstrap 按 token 分文件）
# ---------------------------------------------------------------------------

def start_daemon_subprocess(config_dir: Path) -> Tuple[str, subprocess.Popen]:
    """启动 daemon 子进程（detached）。

    返回 (instance_token, proc)。GUI 拿到后启动 QTimer 轮询 status 文件，
    等 daemon 自己拿到 .daemon.lock 后写入匹配 token 的 status 才算启动成功。

    Popen 不写 status 文件——避免 daemon 启动失败时残留错误 status。
    stdout/stderr 重定向到 daemon_bootstrap_{token}.log（按 token 分文件）。
    """
    token = uuid.uuid4().hex
    _data_root().mkdir(parents=True, exist_ok=True)
    log_dir = _data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_path = log_dir / f"daemon_bootstrap_{token}.log"

    cmd = [
        sys.executable, "-m", "quantstudio.pipeline.daemon",
        "--mode", "forever",
        "--config-dir", str(config_dir),
        "--instance-token", token,
    ]

    # 父进程打开 bootstrap fd 传给 Popen，Popen 后立即关闭自己的 fd（防句柄泄漏）
    bootstrap_fd = os.open(str(bootstrap_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)

    # Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP（脱离 GUI 控制台）
    # POSIX: start_new_session=True
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=bootstrap_fd,
        stderr=subprocess.STDOUT,
        cwd=str(_project_root()),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        close_fds=True,
    )
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        popen_kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    finally:
        # 父进程立即关闭自己的 fd（子进程已继承）
        try:
            os.close(bootstrap_fd)
        except OSError:
            pass

    logger.info(f"[GUI] daemon 子进程已启动 pid={proc.pid} token={token[:8]}... "
                f"bootstrap={bootstrap_path.name}")
    return token, proc


def _project_root() -> Path:
    """项目根（用于 Popen 的 cwd，防快捷方式起 GUI 时 -m 找不到包）。"""
    from quantstudio._paths import _ROOT
    return _ROOT


def read_bootstrap_log_tail(token: str, lines: int = 200) -> str:
    """读取指定 token 的 bootstrap 日志尾部（启动失败时显示错误）。"""
    log_dir = _data_root() / "logs"
    path = log_dir / f"daemon_bootstrap_{token}.log"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except (FileNotFoundError, OSError):
        return f"（无法读取 bootstrap 日志: {path.name}）"


# ---------------------------------------------------------------------------
# 停止（评审 5：优雅优先）
# ---------------------------------------------------------------------------

def request_graceful_stop(timeout_check_token: Optional[str] = None) -> bool:
    """写 stop.request 文件，请求 daemon 优雅退出。

    timeout_check_token：若提供，仅当当前 status 的 token 匹配才写
    （防止 GUI 显示的是旧 daemon 时误发给新 daemon）。
    返回 True 表示已写入请求；False 表示 token 不匹配或 status 不存在。
    """
    status = read_daemon_status()
    if status is None:
        logger.warning("[GUI] 无 daemon status，无法发送停止请求")
        return False
    token = status.get("instance_token")
    if not token:
        logger.warning("[GUI] daemon status 无 instance_token，无法发送停止请求")
        return False
    if timeout_check_token and token != timeout_check_token:
        logger.warning(f"[GUI] daemon token 不匹配（期望 {timeout_check_token[:8]}..., "
                       f"实际 {token[:8]}...），拒绝发送停止请求")
        return False
    # 写 stop.request
    req = {"instance_token": token, "requested_at": _now_iso()}
    path = daemon_stop_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    logger.info(f"[GUI] 已发送停止请求 (token={token[:8]}...)")
    return True


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 强制停止（评审 9：五验 + 进程消失 + 清 token status）
# ---------------------------------------------------------------------------

def force_kill_daemon() -> Tuple[bool, str]:
    """强制终止 daemon（用户在超时弹窗确认后调用）。

    流程：
      1. psutil 五验（PID + create_time + exe + cmdline + token）；
      2. 任一不匹配 → 返回 (False, "stale/denied 提示")，不杀；
      3. psutil.Process.terminate()（Windows=TerminateProcess 强杀，POSIX=SIGTERM）；
      4. 等 3s；未消失 → psutil.kill()（POSIX SIGKILL）；
      5. 确认特定 pid 已消失；
      6. 仅清该 token 所有的 status 文件。

    返回 (success, message)。
    """
    status = read_daemon_status()
    if status is None:
        return False, "无 daemon status 文件，无需强制停止"

    identity = verify_daemon_identity(status)
    if identity == "denied":
        return False, "无权限确认进程身份（AccessDenied），请手动结束进程"
    if identity == "stale":
        # 进程已死或身份不匹配，清理 stale status
        try:
            daemon_status_path().unlink(missing_ok=True)
            return True, "进程已不存在，已清理陈旧 status 文件"
        except OSError as e:
            return False, f"清理陈旧 status 失败: {e}"
    # identity == "alive"，继续强制路径
    pid = status["pid"]
    token = status.get("instance_token", "")
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        # 刚好退出
        try:
            daemon_status_path().unlink(missing_ok=True)
        except OSError:
            pass
        return True, "进程已退出"

    # 再次校验 token（status 与 cmdline 一致才继续）
    stored_cmdline_has_token = token and token in status.get("cmdline", [])
    if stored_cmdline_has_token and token not in p.cmdline():
        return False, "进程 cmdline 不含 token（身份已变），拒绝强制停止"

    logger.warning(f"[GUI] 强制终止 daemon pid={pid} token={token[:8]}... "
                   f"（可能造成当前批次残留或数据库异常）")
    try:
        p.terminate()  # Windows: TerminateProcess; POSIX: SIGTERM
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        return False, "无权限终止进程（AccessDenied），请手动结束"

    # 等 3s
    gone = False
    for _ in range(30):
        try:
            if not p.is_running():
                gone = True
                break
        except psutil.NoSuchProcess:
            gone = True
            break
        import time as _t
        _t.sleep(0.1)

    if not gone:
        # POSIX 上 SIGTERM 可能被忽略，升级到 SIGKILL
        try:
            p.kill()
        except psutil.NoSuchProcess:
            gone = True
        except psutil.AccessDenied:
            return False, "无权限 kill 进程，请手动结束"
        # 再等 1s
        for _ in range(10):
            try:
                if not p.is_running():
                    gone = True
                    break
            except psutil.NoSuchProcess:
                gone = True
                break
            _t.sleep(0.1)

    if not gone:
        return False, f"进程 pid={pid} 强制终止后仍存活，请手动结束"

    # 确认特定 pid 已消失（防误判）
    try:
        psutil.Process(pid)
        return False, f"进程 pid={pid} 仍存在，请手动确认"
    except psutil.NoSuchProcess:
        pass

    # 仅清该 token 所有的 status 文件
    final_status = read_daemon_status()
    if final_status and final_status.get("instance_token") == token:
        try:
            daemon_status_path().unlink(missing_ok=True)
            logger.info(f"[GUI] 已清理 token={token[:8]}... 的 status 文件")
        except OSError:
            pass

    # 清理 stop.request（避免残留）
    try:
        daemon_stop_request_path().unlink(missing_ok=True)
    except OSError:
        pass

    return True, f"进程 pid={pid} 已强制终止"


# ---------------------------------------------------------------------------
# 下次启动前健康检查（强制停止后数据库完整性）
# ---------------------------------------------------------------------------

def check_db_openable() -> Tuple[bool, str]:
    """DuckDB 可打开性检查（强制停止后下次启动前调用）。

    返回 (ok, message)。ok=False 时应在 GUI 提示用户。
    """
    try:
        import duckdb
        from quantstudio._paths import db_path
        db = str(db_path())
        # 尝试 read_only 打开（不持有 RW，与可能残留的连接不冲突）
        conn = duckdb.connect(db, read_only=True)
        conn.close()
        return True, "DuckDB 可正常打开"
    except Exception as e:
        return False, f"DuckDB 打开失败: {e}"
