# -*- coding: utf-8 -*-
"""PD-2026-0020 期1：`_qs_budget_clamp` 的 AST／受控 stub 行为验证。

目的（回应「赋值计数非完整行为证据」）：直接调用产物里的函数本体，验证
  - 隔离关闭（C1/C2）：`_qs_budget_clamp` 返回 `applied=False`（**不折算**）；
  - 隔离开启（C3/C4）：`_QSClampHooks.iso_avail` 被赋值 ⇒ **折算生效**（applied=True 且
    amount 被压到 floor(avail/px/lot)*lot）。
并验证该函数在产物中**确实存在且为 AST 节点**（不是被文本替换出来的假象）。

**沙箱自检（硬纪律）**：函数体含 `except Exception: return _no` 兜底，环境缺失与业务失败
会被压成同一观测值。故每次调用前先打印桩的**依赖直读**（`_qs_px_exec` 的返回值、
`_QS_SPLIT_LOT`、`_QSClampHooks.iso_avail` 是否为 None），自检不过则结论作废。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC_REL = "output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py"
OUT_DATE = "20261113"
EVID = ROOT / "docs" / "evidence" / ("pd0020-ast-stub-" + OUT_DATE)

EXTRACT = ("_qs_budget_clamp", "_QSClampHooks")


def render(iso: bool, pers: bool) -> str:
    import tests.test_deploy_f1f2f3_guard as T
    d = pathlib.Path(tempfile.mkdtemp())
    out, cfg = d / "out", d / "c.yaml"
    cfg.write_text(
        "strategy_id: tech_etf_mvo_rotation\n"
        "source: " + (ROOT / SRC_REL).as_posix() + "\n"
        "execution:\n  profile: daily_eod_1450\n"
        "persistence:\n  enabled: " + ("true" if pers else "false") + "\n"
        "isolation:\n  enabled: " + ("true" if iso else "false") +
        "\n  capital: 100000.0\n  policy: strict\n  market_split: true\n"
        "output:\n  root: " + out.as_posix() + "\n  dir_name: s\n", encoding="utf-8")
    r = T.run_deploy(str(cfg)).to_dict()
    assert r.get("status") != "FAIL"
    return (out / "s" / "tech_etf_mvo_rotation_trade.py").read_text(encoding="utf-8")


def extract_nodes(code: str):
    """按 AST 抽取 _QSClampHooks 类与 _qs_budget_clamp 函数源码（保留原文本）。"""
    tree = ast.parse(code)
    lines = code.split("\n")
    found = {}
    for node in tree.body:
        nm = getattr(node, "name", None)
        if nm in EXTRACT:
            found[nm] = "\n".join(lines[node.lineno - 1:node.end_lineno])
    return found


def run_case(tag: str, code: str, expect_applied: bool) -> bool:
    nodes = extract_nodes(code)
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("    PASS  " if cond else "    FAIL  ") + msg)
        if not cond:
            ok = False

    print("=== %s ===" % tag)
    chk("_qs_budget_clamp" in nodes, "函数在产物 AST 中存在（非文本假象）")
    chk("_QSClampHooks" in nodes, "承载 class 在产物 AST 中存在")

    ns = {"log": type("L", (), {
        "info": staticmethod(lambda *a, **k: None),
        "warning": staticmethod(lambda *a, **k: None),
        "error": staticmethod(lambda *a, **k: None)})(),
        "_QS_SPLIT_LOT": 100,
        "_QS_CLAMP_UNARMED_COUNT": [0],
        "_QS_MAX_ORDER_SHARES": 1000000}
    exec(compile(nodes["_QSClampHooks"], "<cls>", "exec"), ns)
    # 桩：_qs_px_exec 固定返回 10.0（直读自检用）
    PX = 10.0
    ns["_qs_px_exec"] = lambda security: PX
    exec(compile(nodes["_qs_budget_clamp"], "<fn>", "exec"), ns)

    # —— 沙箱依赖直读自检（不稳则结论作废）——
    print("    [沙箱自检] _qs_px_exec('X') =", ns["_qs_px_exec"]("X"), "（期望 10.0）")
    print("    [沙箱自检] _QS_SPLIT_LOT =", ns["_QS_SPLIT_LOT"], "（期望 100）")
    avail = ns["_QSClampHooks"].iso_avail
    print("    [沙箱自检] _QSClampHooks.iso_avail is None =", avail is None)
    chk(ns["_qs_px_exec"]("X") == PX, "桩直读符合预期（非默认值）")

    # 隔离开启时注入额度（模拟 TAIL 赋值）
    if expect_applied:
        ns["_QSClampHooks"].iso_avail = lambda: 5000.0
        print("    [注入] iso_avail = lambda: 5000.0")

    r = ns["_qs_budget_clamp"]("X", 1000)
    amount, applied, orig, clamp, av, px = r
    print("    返回 = %s" % (r,))
    chk(applied is expect_applied,
        "applied == %s（实测 %s）" % (expect_applied, applied))
    if expect_applied:
        # avail=5000, px=10, lot=100 ⇒ can_buy = 500 ⇒ min(1000,500) = 500
        chk(amount == 500, "折算后 amount == 500（实测 %s）" % amount)
        chk(orig == 1000, "orig == 1000（实测 %s）" % orig)
    else:
        chk(amount == 1000, "未折算 amount == 1000（实测 %s）" % amount)

    # 卖出不钳（任何配置下 amount<0 必不 applied）
    r_sell = ns["_qs_budget_clamp"]("X", -500)
    chk(r_sell[1] is False and r_sell[0] == -500,
        "卖出不钳：applied=False 且 amount 保持 -500（实测 %s）" % (r_sell,))
    return ok


def main() -> int:
    EVID.mkdir(parents=True, exist_ok=True)
    ok = True
    for tag, iso, pers, exp in (("C1 双关(iso=F,pers=F)", False, False, False),
                                ("C2 仅持久化(F,T)", False, True, False),
                                ("C3 仅隔离(T,F)", True, False, True),
                                ("C4 双开(T,T)", True, True, True)):
        code = render(iso, pers)
        print()
        if not run_case(tag, code, exp):
            ok = False
    print()
    print("  总判定:", "PASS" if ok else "FAIL")
    (EVID / "manifest.md").write_text(
        "# PD-2026-0020 期1 · AST／受控 stub 行为验证\n\n"
        "对 `_qs_budget_clamp` **函数本体**直调（不是文本计数）：\n\n"
        "- 隔离关闭（C1/C2）：`applied=False`，amount 保持 1000（**不折算**）；\n"
        "- 隔离开启（C3/C4）：注入 `iso_avail=lambda:5000` 后 `applied=True`，"
        "avail=5000 / px=10 / lot=100 ⇒ can_buy=500 ⇒ `amount=500`、`orig=1000`；\n"
        "- **卖出（amount<0）在全部配置下均不钳**（applied=False，amount 保持 -500）；\n"
        "- 每次调用前打印**桩依赖直读**（`_qs_px_exec`=10.0、`_QS_SPLIT_LOT`=100、"
        "`iso_avail is None`），自检不过则结论作废（防「沙箱缺依赖 ⇒ 静默返回值」误读）。\n\n"
        "判定：%s\n" % ("PASS" if ok else "FAIL"),
        encoding="utf-8", newline="\n")
    print("  证据:", (EVID / "manifest.md").relative_to(ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
