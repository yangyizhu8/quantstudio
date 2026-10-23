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
import time
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
    """判断 daemon 是否在运行（status 存在 + psutil 身份校验）。

    V4（2026-09-14 客户 macOS 案）：'unknown'（内核查询瞬时失败，D2）**不得**收敛为
    False——否则调用侧会把修复完全吃掉。语义：无法确证死亡 → 按在运行对待
    （保持现状、继续观察，绝不清理 token/status）；仅确证 stale 才返回 False。
    """
    status = read_daemon_status()
    if status is None:
        return False
    return verify_daemon_identity(status) in ("alive", "unknown")


def get_daemon_status() -> Optional[dict]:
    """读取 daemon status（用于显示按钮状态、token 匹配等）。"""
    return read_daemon_status()


def _is_daemon_cmdline(cl: list) -> bool:
    """结构化判定「这是不是一个常驻 daemon 进程的命令行」。

    FP-3 实测修正（2026-10-23 03:1x）：初版用「cmdline 含
    `quantstudio.pipeline.daemon` 子串」，在真实环境里**误命中**了一个
    `node.exe ... powershell -Command "...quantstudio.pipeline.daemon..."` 的
    诊断脚本进程（其命令行**提及**该串但不是 daemon）⇒ 会误拦正常启动。
    故改为两条**结构化**形态 + 解释器白名单：
      ① `python -m quantstudio.pipeline.daemon ...`（GUI/CLI 标准启动）
      ② `python <...>/quantstudio/pipeline/daemon.py ...`（status 文件中记录形态）
    并要求 `cl[0]` 是 python/pythonw 可执行文件（含 venv 路径）。
    """
    if not cl:
        return False
    # 取 basename 判定：psutil 在全路径与裸名（"python.exe"）两种形态下都可能返回，
    # 只认 "/python.exe" 后缀会漏判裸名（FP-3 桩测暴露，2026-10-23 修正）。
    exe = str(cl[0]).replace("\\", "/").lower()
    base = exe.rsplit("/", 1)[-1]
    if base not in ("python.exe", "pythonw.exe", "python", "python3", "python3.exe"):
        return False
    # 形态①：-m quantstudio.pipeline.daemon（相邻参数严格配对）
    for i in range(len(cl) - 1):
        if cl[i] == "-m" and cl[i + 1] == "quantstudio.pipeline.daemon":
            return True
    # 形态②：脚本路径形态
    for a in cl[1:]:
        s = str(a).replace("\\", "/").lower()
        if s.endswith("quantstudio/pipeline/daemon.py"):
            return True
    return False


def diagnose_start_gate(*, others=None, note: str = "") -> None:
    """FP-5（2026-10-23）：把「启动前置判据为何未拦住」的**现场值**落一行诊断。

    背景：R-2——用户报错时点（01:56）前置闸判定为 False，但事后实测 verdict=alive，
    静态证据无法确定当时 `read_daemon_status()` / 身份校验返回了什么。故在判定为
    "未拦住"的分支留一次现场快照，供下次复现时定谳。

    落点说明（相对方案的实现级调整）：**不写 daemon.log**——该文件由 daemon 侧的
    `TimedRotatingFileHandler` 持有并轮转，GUI 进程直接追加有轮转冲突/占用风险；
    改落同目录专用文件 `data/logs/gui_gate_diag.log`（每行一条 JSON，便于 grep）。

    开关：env `QS_GUI_DAEMON_DIAG`（**默认开**；`0/false/off` 关闭）。
    R-2 定谳后按裁定转默认关。
    任何异常一律吞掉——诊断绝不改变启动路径行为。
    """
    if os.environ.get("QS_GUI_DAEMON_DIAG", "1").strip().lower() in ("0", "false", "off"):
        return
    try:
        path = daemon_status_path()
        try:
            raw = read_daemon_status()
            if raw is None:
                status_view = "<missing>" if not path.exists() else "<unparsable>"
            else:
                status_view = {k: raw.get(k) for k in
                               ("pid", "status", "instance_token", "started_at", "config_dir")}
        except Exception as e:  # 连读取都异常时也要留痕
            status_view = f"<read-error: {type(e).__name__}>"
        try:
            verdict = verify_daemon_identity(read_daemon_status() or {})
        except Exception as e:
            verdict = f"<verify-error: {type(e).__name__}>"
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "start_gate_not_blocked",
            "status_path": str(path),
            "status_raw": status_view,
            "verdict": verdict,
            "proc_pids": sorted(others or []),
            "note": note,
        }
        log_dir = _data_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "gui_gate_diag.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 诊断失败绝不影响启动路径


def find_running_daemon_pids() -> list:
    """枚举在跑的常驻 daemon 进程（只读、零锁副作用）。

    FP-3（2026-10-23 方案 docs/daemon-gui-start-failure-design-20261023.md）：
    `daemon_status.json` 是**单源判据**——一旦它缺失/损坏（`read_daemon_status()`
    返回 None），`is_daemon_running()` 即判 False 而放行启动，随后子进程撞
    `.daemon.lock` 失败退出（GUI 侧只看到「启动后立即退出」）。此函数以**进程表**
    作第二源交叉校验。

    **刻意不试探 `.daemon.lock`**：试探会短暂持有/释放锁，可能干扰正在启动的实例
    （方案 §4 已判定为高风险并规避）。

    过滤（见 `_is_daemon_cmdline`）：python 解释器 + 结构化匹配 -m/脚本形态；
    **排除 `--mode once`**（单次执行走 collector_run.lock，不是常驻实例）；
    排除本进程。返回 [{"pid": int, "started_at": "YYYY-MM-DD HH:MM:SS"}, ...]，按 pid 升序。
    """
    me = os.getpid()
    hits = []
    try:
        for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
            try:
                pid = p.info.get("pid")
                if pid == me:
                    continue
                cl = p.info.get("cmdline") or []
                if not _is_daemon_cmdline(cl):
                    continue
                # 排除 once（非常驻）；缺省 mode 视为 forever
                if "--mode" in cl and "once" in cl:
                    continue
                ct = p.info.get("create_time") or 0
                hits.append({
                    "pid": int(pid),
                    "started_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(float(ct))) if ct else "unknown",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue  # 枚举期进程消失/无权限：跳过，不影响其余
    except Exception:
        return []  # 枚举失败不得影响启动路径（与既有 fail-soft 一致）
    return sorted(hits, key=lambda d: d["pid"])


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
    # 三轮裁定 §3.3 #2：hold 门二次校验（GUI 路径唯一入口）。
    # 时点：任何副作用（建 bootstrap 文件 / Popen）之前；命中 ⇒ 抛 HoldActiveError，
    # GUI 据此拒绝启动并提示用户（不静默、不 Popen）。
    from quantstudio.pipeline.daemon_hold_gate import ensure_not_held
    ensure_not_held("gui.daemon_process.start_daemon_subprocess")

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


# ---------------------------------------------------------------------------
# T2（2026-09-20）：GUI 委托通道——once 子进程跑采集，GUI 不再自建采集锁
# ---------------------------------------------------------------------------

def start_once_subprocess(task_name: str, config_dir: Path, mode: str = "incremental",
                          run_quality_audit: bool = True,
                          pull_mode: Optional[str] = None
                          ) -> Tuple[str, subprocess.Popen, Path, Path]:
    """以 once 子进程执行单个采集任务（T2 委托通道，复用 L68 Popen 模式）。

    与旧 Worker 的本质差别：GUI 进程不再构造 FileLock——采集锁由子进程内的
    CollectorRunLock(timeout=30)（daemon.py once 路径既有）持有，从根源消除
    GUI×daemon 的跨进程写锁冲突面。

    成败判定契约（A2 障碍 3）：调方读 runtime-manifest（原子写 JSON：task/pid/
    nonce/created_at/DATA_ROOT/三锁路径），不得以退出码判定（pwsh 包装 exit 1
    为假阳性，实证见 handoff-data-line-20260917）。

    采集期排队为有界：子进程 30s 等锁超时即退出并落日志——GUI 侧据此提示
    「采集中，请稍后重试」，不做无限等待。
    """
    nonce = uuid.uuid4().hex[:8]
    _data_root().mkdir(parents=True, exist_ok=True)
    log_dir = _data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stem = "once_{}_{}_{}_{}".format(task_name, mode, time.strftime("%Y%m%d_%H%M%S"), nonce)
    log_path = log_dir / (stem + ".log")
    manifest_path = log_dir / (stem + ".manifest.json")

    cmd = [
        sys.executable, "-m", "quantstudio.pipeline.daemon",
        "--mode", "once",
        "--task", task_name,
        "--config-dir", str(config_dir),
        "--quality-audit", "full" if run_quality_audit else "none",
        "--runtime-manifest", str(manifest_path),
        "--runtime-nonce", nonce,
    ]
    if pull_mode:
        cmd += ["--pull-mode", pull_mode]

    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=log_fd,
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
        try:
            os.close(log_fd)
        except OSError:
            pass

    logger.info("[GUI] once 委托已启动 task={} pid={} nonce={} manifest={}".format(
                task_name, proc.pid, nonce, manifest_path.name))
    return nonce, proc, manifest_path, log_path


def read_once_manifest(manifest_path: Path) -> Optional[dict]:
    """读取 once 委托的 runtime-manifest（不存在/损坏返回 None，不抛）。"""
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception:
        return None


def once_busy_hint(log_path: Path) -> Optional[str]:
    """有界排队提示：从 once 日志探测「等锁超时」形态，返回用户可读文案。"""
    try:
        tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-4000:]
    except Exception:
        return None
    if "collector_run.lock" in tail and ("获取失败" in tail or "正在采集" in tail):
        return "数据库采集中（守护进程正在写入），请稍后重试"
    return None


def _project_root() -> Path:
    """项目根（用于 Popen 的 cwd，防快捷方式起 GUI 时 -m 找不到包）。"""
    from quantstudio._paths import _ROOT
    return _ROOT


def read_bootstrap_log_tail(token: str, lines: int = 200) -> str:
    """读取指定 token 的 bootstrap 日志尾部（启动失败时显示错误）。

    FP-1（2026-10-23 方案 docs/daemon-gui-start-failure-design-20261023.md）：
    GUI 场景下 daemon 在 logging 初始化后**移除 StreamHandler**（daemon.py FIX-5，
    防 bootstrap 无轮转地承载全量日志），因此**可预期失败**（如「另一个 daemon
    已在运行」）只写进 daemon.log ⇒ bootstrap 为 0B ⇒ 弹窗空白、用户无从诊断。
    故增加回退链：
      ① bootstrap 有内容 ⇒ 原样返回（语义不变）；
      ② 缺失或去空白后为空 ⇒ 回退读 daemon.log 尾部，并**显式标注**来源
         （该文件可能含其他实例日志，不得误认为本实例日志）；
      ③ 两者皆不可用 ⇒ 返回含两个文件名的提示（不抛异常）。
    只读尾部窗口（bootstrap 全读；daemon.log 尾 64KB），避免大日志全量载入。
    """
    log_dir = _data_root() / "logs"
    path = log_dir / f"daemon_bootstrap_{token}.log"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        if "".join(all_lines).strip():
            return "".join(all_lines[-lines:])
    except (FileNotFoundError, OSError):
        pass  # 缺失/不可读 → 走回退链

    daemon_log = log_dir / "daemon.log"
    try:
        with daemon_log.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            blob = f.read().decode("utf-8", errors="replace")
        tail_lines = blob.splitlines()[-lines:]
        if tail_lines and "".join(tail_lines).strip():
            return ("[bootstrap 为空 ⇒ 以下为 daemon.log 尾部"
                    "（可能含其他实例日志）]\n" + "\n".join(tail_lines) + "\n")
    except (FileNotFoundError, OSError):
        pass  # 走兜底文案

    return (f"（bootstrap 日志为空或不可读: {path.name}；"
            f"daemon.log 亦不可读: {daemon_log.name}）")


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
    if identity == "unknown":
        # D2 三态：无法确认 ≠ 死亡 → 不清理 status（清了就永久脱钩），不误杀
        return False, "无法确认 daemon 身份（内核查询瞬时失败），不清不杀，请稍后重试"
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
