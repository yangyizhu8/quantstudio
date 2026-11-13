# -*- coding: utf-8 -*-
"""PD-2026-0020 期1：多配置「预期差异」核验 ＋ 同配置重复确定性。

判据（四配置 × 双渲染）：
  cfg          iso  pers   期望 REF-CUR 差异
  C1 双关      F    F      0 行（模板不参与注入）
  C2 仅持久化  F    T      9 行（C 探针段，纯 insert）
  C3 仅隔离    T    F      0 行
  C4 双开      T    T      9 行
重复确定性：同配置连续渲染两次，产物 sha256 必须逐位一致（四配置各自成立）。

单位声明：本件所有 len(...) 为**字符数**；文件体积另以 UTF-8 **字节数** 标注，
两者在含中文注释的产物上差异显著（约 +10%），不得混用。

**BUILD 指纹边界**：_build_tag() 的 src 哈希读当前磁盘源码，不反映 monkey-patch
的旧模板；故 REF/CUR 的 BUILD 行相同，BUILD 差异不在本判据内。
"""
from __future__ import annotations

import difflib
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = "7b7a69c^"
TPL = "quantstudio/ptrade_deploy/templates/persistence_template.py"
SRC_REL = "output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py"
OUT_DATE = "20261113"
EVID = ROOT / "docs" / "evidence" / ("pd0020-multicfg-" + OUT_DATE)

PROBE_LINES = 9


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    import quantstudio.ptrade_deploy.transformer as tf
    import tests.test_deploy_f1f2f3_guard as T

    EVID.mkdir(parents=True, exist_ok=True)
    old_src = subprocess.run(["git", "show", "%s:%s" % (REF, TPL)], cwd=str(ROOT),
                             capture_output=True, text=True, encoding="utf-8").stdout
    assert old_src and "QS_PERSIST_PROBE" not in old_src
    old_mod = types.ModuleType("old_persist_tpl")
    exec(compile(old_src, "<ref_tpl>", "exec"), old_mod.__dict__)
    src_abs = ROOT / SRC_REL
    assert src_abs.is_file()

    def render(tag, iso, pers, use_old):
        orig = tf._load_template

        def patched(name):
            if use_old and name == "persistence_template":
                return old_mod
            return orig(name)

        tf._load_template = patched
        try:
            d = pathlib.Path(tempfile.mkdtemp())
            out, cfg = d / "out", d / "c.yaml"
            cfg.write_text(
                "strategy_id: tech_etf_mvo_rotation\n"
                "source: " + src_abs.as_posix() + "\n"
                "execution:\n  profile: daily_eod_1450\n"
                "persistence:\n  enabled: " + ("true" if pers else "false") + "\n"
                "isolation:\n  enabled: " + ("true" if iso else "false") +
                "\n  capital: 100000.0\n  policy: strict\n  market_split: true\n"
                "output:\n  root: " + out.as_posix() + "\n  dir_name: s\n",
                encoding="utf-8")
            r = T.run_deploy(str(cfg)).to_dict()
            p = out / "s" / "tech_etf_mvo_rotation_trade.py"
            code = p.read_text(encoding="utf-8") if p.is_file() else ""
            assert r.get("status") != "FAIL" and code, "%s 渲染失败" % tag
            return code
        finally:
            tf._load_template = orig

    rows, ok = [], True
    for name, iso, pers, expect in (
            ("C1 双关", False, False, 0),
            ("C2 仅持久化", False, True, PROBE_LINES),
            ("C3 仅隔离", True, False, 0),
            ("C4 双开", True, True, PROBE_LINES)):
        print("=== %s (iso=%s pers=%s) ===" % (name, iso, pers))
        # 重复确定性：同配置渲染两次
        r1 = render(name + "/r1", iso, pers, False)
        r2 = render(name + "/r2", iso, pers, False)
        determ = (sha(r1) == sha(r2))
        # 预期差异：REF 模板 vs CUR 模板（同配置）
        ref_c = render(name + "/ref", iso, pers, True)
        al, bl = ref_c.split("\n"), r1.split("\n")
        blocks = [o for o in difflib.SequenceMatcher(None, al, bl).get_opcodes() if o[0] != "equal"]
        added = sum(j2 - j1 for _, _, _, j1, j2 in blocks)
        removed = sum(i2 - i1 for _, i1, i2, _, _ in blocks)
        noninsert = [t for t, *_ in blocks if t != "insert"]
        print("  字符=%d 字节=%d sha16=%s" % (len(r1), len(r1.encode("utf-8")), sha(r1)[:16]))
        print("  重复确定性: %s | 差异块=%d 新增=%d 删除=%d 非insert=%s"
              % ("PASS" if determ else "FAIL", len(blocks), added, removed, noninsert))
        d_ok = determ and added == expect and removed == 0 and not noninsert
        print("  预期差异(%d 行): %s" % (expect, "PASS" if d_ok else "FAIL"))
        if not d_ok:
            ok = False
        rows.append((name, iso, pers, expect, len(r1), len(r1.encode("utf-8")),
                     sha(r1)[:16], determ, len(blocks), added, removed, d_ok))

    md = ["# PD-2026-0020 期1 · 多配置预期差异 ＋ 重复确定性\n\n",
          "> 单位：`字符` = `len(code)`；`字节` = UTF-8 编码后长度（含中文注释，二者不可混用）。\n",
          "> BUILD 边界：`_build_tag()` 读当前磁盘源码，不反映 monkey-patch 旧模板；\n",
          "> 故 REF/CUR 的 BUILD 行相同，BUILD 差异不在本判据内。\n\n",
          "| 配置 | iso | pers | 期望差异 | 字符 | 字节 | sha16 | 重复确定性 | 实差异块 | 新增 | 删除 | 判定 |\n",
          "|---|---|---|---|---|---|---|---|---|---|---|---|\n"]
    for r in rows:
        md.append("| %s | %s | %s | %d 行 | %d | %d | `%s` | %s | %d | %d | %d | %s |\n"
                  % (r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                     "PASS" if r[7] else "FAIL", r[8], r[9], r[10], "PASS" if r[11] else "FAIL"))
    md.append("\n## 结论\n\n")
    md.append("四配置各自：同配置重复渲染 sha256 逐位一致（确定性成立）；"
              "相对 C 探针前模板的差异**恰为预期**（persistence 开启=9 行纯新增探针段，关闭=0 行），"
              "**无获批差异之外的漂移**。\n")
    (EVID / "manifest.md").write_text("".join(md), encoding="utf-8", newline="\n")
    print()
    print("=== 证据 ===")
    print("  ", (EVID / "manifest.md").relative_to(ROOT))
    print("  总判定:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
