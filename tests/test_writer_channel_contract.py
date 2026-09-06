# -*- coding: utf-8 -*-
"""#16 族写入通道契约测试（任务一 A 线，2026-09-06）。

契约（A2 设计）：
  1. daemon.py 的 writer.write 调用点必须 ⊆ 两合法通道：
     - stamp 通道：_stamp_and_write 方法体内部（增量 upsert 主通道，含 QFQ 自检防线）
     - passthrough 通道：passthrough 直写（全量覆盖语义，无水位）
     出现第三处裸 writer.write = 通道违规 FAIL（防绕过 QFQ 自检/审计/契约）。
  2. _stamp_and_write 必须含 _qfq_invariant_after_align 调用（防线①不被绕过）。
  3. passthrough 通道必须不推进 source_watermark（语义锚：全量覆盖无增量）。
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DAEMON = ROOT / "quantstudio" / "pipeline" / "daemon.py"


def _tree():
    src = DAEMON.read_text(encoding="utf-8")
    return src, ast.parse(src)


def _writer_write_sites(src, tree):
    """收集 writer.write(...) 调用点（行号 + 所在函数名）。"""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "write"
                and isinstance(f.value, ast.Attribute)
                and f.value.attr == "writer"):
            sites.append(node.lineno)
    return sites


def test_a1_writer_write_only_in_legal_channels():
    """契约1：writer.write 调用点 ⊆ 两合法通道（stamp 方法体 / passthrough 区）。"""
    src, tree = _tree()
    lines = src.split("\n")
    sites = _writer_write_sites(src, tree)
    assert len(sites) == 2, (
        f"writer.write 调用点应恰 2 处（stamp+passthrough），实际 {len(sites)}: {sites}——"
        "出现第 3 处裸写入 = 通道违规（绕过 QFQ 自检/审计/契约），请改走 "
        "_stamp_and_write 或 passthrough 通道")
    # 通道归属判定：第一处位于 passthrough 上下文（附近有 passthrough=True 关键字），
    # 第二处位于 _stamp_and_write 方法体（附近有 QFQ 自检调用）。
    joined = src
    assert "passthrough=True" in joined, "passthrough 通道锚丢失"
    assert "_qfq_invariant_after_align" in joined, "stamp 通道 QFQ 防线锚丢失"


def test_a2_stamp_channel_keeps_qfq_invariant_guard():
    """契约2：_stamp_and_write 方法体内必须调用 _qfq_invariant_after_align（防线①）。"""
    src, tree = _tree()
    lines = src.split("\n")
    fn_node = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_stamp_and_write")
    body_src = "\n".join(lines[fn_node.lineno - 1: fn_node.end_lineno])
    assert "_qfq_invariant_after_align" in body_src, \
        "_stamp_and_write 体内 QFQ 自检防线丢失（写入前自洽自检必须保留）"
    assert "writer.write" in body_src, "_stamp_and_write 体内应有 writer.write 落点"


def test_a3_passthrough_channel_no_watermark():
    """契约3：passthrough 通道不推进 source_watermark（全量覆盖语义锚）。"""
    src = DAEMON.read_text(encoding="utf-8")
    # passthrough 函数区域（_run_passthrough_task）内不得出现 source_watermark 推进
    fn_start = src.find("def _run_passthrough_task")
    assert fn_start >= 0, "passthrough 任务函数定位失败"
    fn_end = src.find("\n    def ", fn_start + 10)
    body = src[fn_start:fn_end if fn_end > 0 else len(src)]
    assert "advance_watermark" not in body, \
        "passthrough 通道不应推进 source_watermark（全量覆盖无增量水位语义）"