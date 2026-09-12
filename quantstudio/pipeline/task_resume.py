"""GUI 一次性采集任务续传游标（停止语义 v3.1 §9.2，2026-09-12）。

停止 = 真断点续传：日批（MCP 流式分片 / per_trade_date）或每股（per_stock）
在 validator PASS + 写入提交之后推进游标；再次运行从 last_completed+1 起拉。

红线（v2 定案不变，本模块零触碰）：
- 官方水位提交路径（_advance_or_defer_watermark / _advance_actual_watermark）；
- QFQ cycle 状态机（qfq_begin_cycle / qfq_run_post_ingest）；
- daemon 常驻执行链。

四规则：R1 推进 / R2 续传 / R3 清除 / R4 降级（见各方法 docstring）。
游标是本地运行态：data/task_resume/，不入 git、不入客户包。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from quantstudio._paths import DATA_ROOT

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RESUME_SUBDIR = "task_resume"

UNIT_TRADE_DATE = "trade_date"   # 日批路径游标粒度（流式分片 / per_trade_date）
UNIT_STOCK = "stock"             # per_stock 路径游标粒度（每股）

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TaskCancelled(RuntimeError):
    """协作式停止命中（结构化信号；复用 BacktestCancelled 模式，不做字符串嗅探）。

    取消路径：GUI 置旗标 → collector 日批/每股边界 cancel_check 命中 →
    raise TaskCancelled → execute_task 捕获（水位不推进、游标保留）→
    worker finished_err(TaskCancelled) → 槽内 isinstance 判定 → 界面文案「已停止」。
    """


def resume_dir() -> Path:
    """游标目录（本地运行态，已入 .gitignore）。

    可用环境变量 QUANTSTUDIO_RESUME_DIR 重定向（测试用临时目录，避免污染
    真实运行态；与 QUANTSTUDIO_DATA_ROOT 同族的部署重定向手段）。
    """
    override = os.environ.get("QUANTSTUDIO_RESUME_DIR")
    if override:
        return Path(override)
    return DATA_ROOT / RESUME_SUBDIR


def _safe_token(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(name))[:48]


def window_fingerprint(task_name: str, mode: str, window_start: str) -> str:
    """窗口指纹 = 任务 + 模式 + 窗口起点（水位基线）的哈希。

    窗口终点（今天）逐日漂移，不参与指纹——否则停止次日再跑恒判废，与
    「停止即续传」语义冲突（实施裁定，已记入证据文档）。
    """
    raw = str(task_name) + "|" + str(mode) + "|" + str(window_start)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_task_id(task_name: str, mode: str, window_start: str) -> str:
    """task_id = 任务名 + mode + 窗口指纹哈希（§9.2）。"""
    return (f"{_safe_token(task_name)}__{_safe_token(mode)}__"
            f"{window_fingerprint(task_name, mode, window_start)}")


def _looks_like_date(value) -> bool:
    return bool(value) and bool(_DATE_RE.match(str(value)))


class TaskResumeCursor:
    """单任务运行的续传游标会话（每个任务运行持有一份）。"""

    def __init__(self, task_name: str, mode: str, window_start: str,
                 window_end: str, watermark_at_start=None):
        self.task_name = str(task_name)
        self.mode = str(mode)
        self.window_start = str(window_start)
        self.window_end = str(window_end)
        self.watermark_at_start = (
            None if watermark_at_start in (None, "") else str(watermark_at_start))
        self.task_id = make_task_id(self.task_name, self.mode, self.window_start)
        # R4：写失败 → 静默降级 v2（全窗重拉；向安全侧失败，绝不过度跳拉）
        self.degraded = False
        self.resumed_from = None

    @property
    def path(self) -> Path:
        return resume_dir() / (self.task_id + ".json")

    # ------------------------------------------------------------------ 读
    def _read(self) -> Optional[dict]:
        try:
            if not self.path.exists():
                return None
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning("[resume] 游标读取失败（按无游标处理）: %s", e)
            return None

    def _invalid_reason(self, data: dict, current_watermark) -> Optional[str]:
        """R2 判废条件；返回 None 表示游标有效。"""
        if int(data.get("schema_version") or 0) != SCHEMA_VERSION:
            return "schema_version_mismatch"
        if data.get("task") != self.task_name or data.get("mode") != self.mode:
            return "task_or_mode_mismatch"
        win = data.get("window") or {}
        if str(win.get("start") or "") != self.window_start:
            return "window_fingerprint_mismatch"
        stored_wm = data.get("watermark_at_start")
        if stored_wm not in (None, "") and current_watermark not in (None, "") \
                and str(stored_wm) != str(current_watermark):
            return "official_watermark_moved"
        last = str((data.get("last_completed") or {}).get("value") or "")
        if _looks_like_date(last) and _looks_like_date(current_watermark) \
                and str(current_watermark) >= last:
            return "official_watermark_past_cursor"
        return None

    def try_resume(self, current_watermark=None) -> Optional[dict]:
        """R2 续传规则：返回游标数据（已置 resumed_from）或 None（不存在/判废）。

        判废即删除游标并按 v2 语义全窗执行（安全侧）。
        """
        data = self._read()
        if not data:
            # 本窗口无游标：顺手判废同任务+模式、不同窗口指纹的陈旧游标
            # （§9.2「窗口指纹不一致判废」——防陈旧游标累积与误用）
            self._invalidate_stale_siblings()
            return None
        reason = self._invalid_reason(data, current_watermark)
        if reason:
            logger.info("[resume] 游标判废(%s)，按 v2 语义全窗执行: %s",
                        reason, self.task_id)
            self.invalidate()
            return None
        last = data.get("last_completed") or {}
        if not last.get("value"):
            return None
        self.resumed_from = last
        logger.info("[resume] 命中续传游标: task_id=%s last_completed=%s",
                    self.task_id, last)
        return data

    # ------------------------------------------------------------------ 写
    def _payload(self, unit_type, value, cycle_id) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "task": self.task_name,
            "mode": self.mode,
            "window": {"start": self.window_start, "end": self.window_end},
            "watermark_at_start": self.watermark_at_start,
            "last_completed": {"type": unit_type, "value": str(value)},
            "cycle_id_at_stop": cycle_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _write(self, payload: dict) -> bool:
        try:
            d = resume_dir()
            d.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            logger.warning("[resume] 游标写入失败 → 静默降级 v2（全窗重拉）: %s", e)
            return False

    def advance(self, unit_type: str, value, cycle_id=None) -> None:
        """R1 推进规则：validator PASS + 写入提交之后调用（与取消检查同一钩子）。

        游标语义 = 已完成单元的**连续前缀**末端（乱序完成路径由此保证：
        绝不跳过未完成单元）。
        """
        if self.degraded or value in (None, ""):
            return
        if not self._write(self._payload(unit_type, value, cycle_id)):
            self.degraded = True

    # ---- A4 修复段进度（独立记账，§9.2 扩展）----
    def _update(self, **fields) -> bool:
        """在现有游标文件上打补丁（保留 last_completed 等既有字段）。"""
        data = self._read()
        if not data:
            data = {
                "schema_version": SCHEMA_VERSION,
                "task": self.task_name,
                "mode": self.mode,
                "window": {"start": self.window_start, "end": self.window_end},
                "watermark_at_start": self.watermark_at_start,
                "last_completed": None,
                "cycle_id_at_stop": None,
            }
        data.update(fields)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        return self._write(data)

    def a4_completed(self) -> set:
        """已完成的 A4 修复窗口集合（停止后不丢失；续跑跳过已修复窗口）。"""
        data = self._read() or {}
        a4 = data.get("a4") or {}
        return {str(x) for x in (a4.get("completed_windows") or [])}

    def advance_a4(self, trade_date, total=None) -> None:
        """A4 修复段进度记账（每窗口完成后调用）。

        **独立于 last_completed**：A4 窗口是「云端 repair/full 声明的修复日期集合」，
        不是主窗口的连续前缀——写进 last_completed 会让续跑从修复日起拉，
        从而跳过从未拉取的主窗口区间（制造数据缺口）。故 A4 进度单独记账。
        """
        if self.degraded or not trade_date:
            return
        done = self.a4_completed()
        done.add(str(trade_date))
        payload = {"completed_windows": sorted(done)}
        if total is not None:
            payload["total"] = int(total)
        if not self._update(a4=payload):
            self.degraded = True

    def mark_stopped(self, cycle_id=None) -> None:
        """停止时补记 cycle_id_at_stop（非功能性，供事后审计追溯；§9.2/审计 S5）。"""
        data = self._read()
        if not data:
            return
        data["cycle_id_at_stop"] = cycle_id
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._write(data)

    def clear(self, reason: str = "completed") -> None:
        """R3 清除规则：任务自然成功完成 → 清除（官方水位已接管）。"""
        try:
            if self.path.exists():
                self.path.unlink()
                logger.info("[resume] 游标清除(%s): %s", reason, self.task_id)
        except Exception as e:
            logger.warning("[resume] 游标清除失败（忽略）: %s", e)

    def invalidate(self) -> None:
        self.clear(reason="invalidated")

    def _invalidate_stale_siblings(self) -> None:
        """判废同任务+模式、不同窗口指纹的陈旧游标文件（窗口指纹不一致判废）。"""
        prefix = _safe_token(self.task_name) + "__" + _safe_token(self.mode) + "__"
        try:
            d = resume_dir()
            if not d.exists():
                return
            for p in d.glob(prefix + "*.json"):
                if p.name != self.path.name:
                    p.unlink()
                    logger.info("[resume] 陈旧游标判废(窗口指纹不一致): %s", p.name)
        except Exception as e:
            logger.warning("[resume] 陈旧游标清理失败（忽略）: %s", e)


def next_trade_date_after(date_str: str) -> Optional[str]:
    """游标续传起点：last_completed + 1 自然日（与 _bump_date 语义同族的本地实现）。

    只做自然日推进——交易日过滤交给各执行路径既有的日历/水位逻辑，
    避免在游标层复刻交易日历语义。
    """
    if not _looks_like_date(date_str):
        return None
    from datetime import date, timedelta
    try:
        y, m, d = (int(x) for x in str(date_str).split("-"))
        return (date(y, m, d) + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return None
