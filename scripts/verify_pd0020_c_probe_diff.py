# -*- coding: utf-8 -*-
"""PD-2026-0020 期1 C 探针差异核验（ⓒ）——可复跑验证器。

目的：证明「持久化开启时，相对 C 探针加入前的模板，产物**唯一差异**是 C 探针段」。

方法（**不改共享工作树模板、不改正式策略**）：
  1. git show <REF>:<模板> 取出 C 探针前模板 → types.ModuleType 动态加载；
  2. monkey-patch transformer._load_template（仅 persistence_template 返回旧模块，finally 还原）；
  3. 在同一冻结输入下渲染两次（REF/CUR）到**临时目录**；
  4. difflib 逐行比对，断言差异块**恰为探针段**；
  5. 证据落 docs/evidence/pd0020-c-probe-diff-<date>/ ：REF/CUR 产物、旧模板、diff、sha256；
  6. 退出码：断言全过 = 0，否则 = 1。

**BUILD 指纹边界（必读）**：
  _build_tag() 的 src 内容哈希读取的是**当前磁盘源码**，**不反映 monkey-patch 加载的旧模板**。
  因此本核验中两次渲染的 BUILD 行必然相同——**BUILD 差异不在本核验判据内**，
  跨源码状态的 BUILD 差异属「冻结/跨版本」环节，须另立判据。
"""
from __future__ import annotations

import difflib
import hashlib
import itertools
import pathlib
import subprocess
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = "7b7a69c^"                                  # C 探针加入前
TPL = "quantstudio/ptrade_deploy/templates/persistence_template.py"
SRC_REL = "output/ptrade_export/tech_etf_mvo_rotation/tech_etf_mvo_rotation_ptrade.py"
OUT_DATE = "20261113"
EVID = ROOT / "docs" / "evidence" / ("pd0020-c-probe-diff-" + OUT_DATE)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    import quantstudio.ptrade_deploy.transformer as tf
    import tests.test_deploy_f1f2f3_guard as T

    EVID.mkdir(parents=True, exist_ok=True)

    old_src = subprocess.run(["git", "show", "%s:%s" % (REF, TPL)], cwd=str(ROOT),
                             capture_output=True, text=True, encoding="utf-8").stdout
    assert old_src, "未取到参照模板"
    assert "QS_PERSIST_PROBE" not in old_src, "参照模板已含 C 探针，REF 选错"
    (EVID / "REF_persistence_template.py").write_text(old_src, encoding="utf-8", newline="\n")

    old_mod = types.ModuleType("old_persist_tpl")
    exec(compile(old_src, "<ref_tpl>", "exec"), old_mod.__dict__)

    src_abs = ROOT / SRC_REL
    assert src_abs.is_file(), "冻结输入缺失：%s" % SRC_REL

    def render(tag: str, use_old: bool) -> str:
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
                "persistence:\n  enabled: true\n"
                "isolation:\n  enabled: false\n"
                "output:\n  root: " + out.as_posix() + "\n  dir_name: s\n",
                encoding="utf-8")
            r = T.run_deploy(str(cfg)).to_dict()
            st = r.get("status")
            p = out / "s" / "tech_etf_mvo_rotation_trade.py"
            code = p.read_text(encoding="utf-8") if p.is_file() else ""
            print("  [%s] status=%s size=%d sha16=%s" % (tag, st, len(code), sha256_text(code)[:16]))
            assert st != "FAIL" and code, "%s 渲染失败 status=%s" % (tag, st)
            return code
        finally:
            tf._load_template = orig

    print("=== 渲染（同一冻结输入 %s）===" % SRC_REL)
    ref_code = render("REF 无C", True)
    cur_code = render("CUR 有C", False)
    (EVID / "REF_product.py").write_text(ref_code, encoding="utf-8", newline="\n")
    (EVID / "CUR_product.py").write_text(cur_code, encoding="utf-8", newline="\n")

    al, bl = ref_code.split("\n"), cur_code.split("\n")
    ops = difflib.SequenceMatcher(None, al, bl).get_opcodes()
    blocks = [o for o in ops if o[0] != "equal"]

    print()
    print("=== 差异块 ===")
    print("  差异块数:", len(blocks))
    added_lines = []
    for i, (tag, i1, i2, j1, j2) in enumerate(blocks, 1):
        print("  块%d: %s 旧L%d-%d(%d) 新L%d-%d(%d)" % (i, tag, i1 + 1, i2, i2 - i1, j1 + 1, j2, j2 - j1))
        added_lines.extend(bl[j1:j2])
        for l in al[i1:i2]:
            print("      - " + l[:120])
        for l in bl[j1:j2]:
            print("      + " + l[:120])

    diff_text = "\n".join(difflib.unified_diff(al, bl, fromfile="REF", tofile="CUR", lineterm=""))
    (EVID / "diff.patch").write_text(diff_text + "\n", encoding="utf-8", newline="\n")

    # 断言：唯一差异块必须是纯 insert 的探针段
    ok = True
    def chk(cond, msg):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            ok = False

    print()
    print("=== 断言 ===")
    chk(len(blocks) == 1, "差异块数 == 1（实测 %d）" % len(blocks))
    chk(all(t == "insert" for t, *_ in blocks), "差异类型均为 insert（纯新增）")
    chk(len(added_lines) == 9, "新增行数 == 9（实测 %d）" % len(added_lines))
    joined = "\n".join(added_lines)
    chk("QS_PERSIST_PROBE" in joined, "新增段含 QS_PERSIST_PROBE")
    chk("write_read_back" in joined, "新增段含 write_read_back")
    chk("QS_PERSIST_PROBE.txt" in joined, "新增段含探针文件名")
    non_probe = [l for l in added_lines if l.strip() and not any(
        k in l for k in ("QS_PERSIST_PROBE", "_qs_probe", "_qs_back", "_qs_pf",
                         "probe-ok", "try:", "except Exception"))]
    chk(not non_probe, "无探针段之外的新增行" + ("" if not non_probe else "：%s" % non_probe[:3]))
    chk("BUILD" not in joined, "新增段不含 BUILD 行（BUILD 差异不在本判据内）")

    manifest = (
        "# PD-2026-0020 ⓒ C 探针差异核验证据\n\n"
        "| 项 | 值 |\n|---|---|\n"
        "| 对照版本 REF | `%s`（C 探针加入前模板） |\n"
        "| 冻结输入 | `%s` |\n"
        "| 策略/配置 | `tech_etf_mvo_rotation` / `daily_eod_1450`, persistence=true, isolation=false |\n"
        "| REF 产物 sha256 | `%s` |\n"
        "| CUR 产物 sha256 | `%s` |\n"
        "| REF 模板 sha256 | `%s` |\n"
        "| 差异块数 | %d（纯 insert） |\n"
        "| 新增行数 | %d（全为 C 探针段） |\n\n"
        "## BUILD 指纹边界\n\n"
        "`_build_tag()` 的 src 内容哈希读取**当前磁盘源码**，不反映 monkey-patch 的旧模板；"
        "故两次渲染 BUILD 行必然相同，**BUILD 差异不在本核验判据内**，"
        "跨源码状态的 BUILD 差异须在「冻结/跨版本」环节另立判据。\n\n"
        "## 结论\n\n"
        "持久化开启时，相对参照版本产物**唯一差异为 C 探针段（9 行纯 insert）**，无其他漂移。\n"
        % (REF, SRC_REL, sha256_text(ref_code), sha256_text(cur_code), sha256_text(old_src),
           len(blocks), len(added_lines)))
    (EVID / "manifest.md").write_text(manifest, encoding="utf-8", newline="\n")

    print()
    print("=== 证据落盘 ===")
    for f in sorted(EVID.iterdir()):
        print("   %s  %d B" % (f.name, f.stat().st_size))
    print()
    print("  结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
