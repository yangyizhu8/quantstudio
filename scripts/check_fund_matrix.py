#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_fund_matrix —— QuantStudio 平台契约矩阵门禁（D 底座，2026-08-31 批次1）。

职责（审计必改②③）:
  1. 唯一事实源 YAML（docs/evidence/fundamentals-contract-matrix.yaml）；
  2. 人读 MD 由 YAML 派生（--sync），--check 校验 MD 与生成内容一致（防双写漂移）；
  3. --check 门禁：meta.wrapper_hash != 当前 wrapper 模板（_QS_FUNDAMENTALS_EXT +
     _QS_INDUSTRY_EXT 原文）哈希 → exit 1 并列出受影响形态（提示复证）——挂回归必跑，
     哈希一致才过；复证完成后 --reverify 更新哈希。

用法:
  python scripts/check_fund_matrix.py                # ○ 缺口清单 + 哈希现状
  python scripts/check_fund_matrix.py --check        # 门禁（哈希 + MD 一致性，失败 exit 1）
  python scripts/check_fund_matrix.py --check --reverify  # 复证完成后更新哈希
  python scripts/check_fund_matrix.py --sync         # 由 YAML 重新生成人读 MD
依赖: PyYAML（pytest 环境自带）。
"""
import argparse
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "quantstudio" / "strategy_compiler" / "source_import.py"
YAML_PATH = ROOT / "docs" / "evidence" / "fundamentals-contract-matrix.yaml"
MD_PATH = ROOT / "docs" / "evidence" / "fundamentals-contract-matrix.md"


def wrapper_hash() -> str:
    src = SRC.read_text(encoding="utf-8")
    h = hashlib.sha256()
    for name in ("_QS_FUNDAMENTALS_EXT", "_QS_INDUSTRY_EXT"):
        m = re.search(name + r" = \'\'\'.*?\'\'\'", src, re.S)
        if m:
            h.update(m.group(0).encode("utf-8"))
    return h.hexdigest()[:12]


def load_yaml():
    import yaml
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def dump_yaml(data):
    import yaml
    YAML_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8")


def gaps(data):
    return [r for r in data["matrix"] if not (r.get("tested") and r.get("probed"))]


def gen_md(data) -> str:
    lines = []
    lines.append("# fundamentals-contract-matrix（平台契约白名单 · 唯一事实源 YAML 派生）\n")
    lines.append("> 本文件由 `scripts/check_fund_matrix.py --sync` 从 "
                 "`fundamentals-contract-matrix.yaml` 生成，**禁止手工编辑**（一致性由 --check 门禁校验）。\n")
    lines.append("> 状态：✅ tested/probed 均真；🔶 单侧；○ 缺口（probed 或 tested 假）。"
                 "RD-1/2/3 为 known-difference 固化断言（不计缺口）。\n")
    lines.append(f"> wrapper 模板哈希：`{data['meta']['wrapper_hash']}`；最后复证：`{data['meta']['last_reverified']}`\n")
    lines.append("## 一、形态矩阵（YAML 行）\n")
    lines.append("| id | shape | mode | table | fields | tested | probed | probe_ref | rd | notes |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in data["matrix"]:
        flds = ",".join(r.get("fields") or []) or "-"
        lines.append(f"| {r['id']} | {r.get('shape','-')} | {r.get('mode','-')} | {r.get('table','-')} "
                     f"| {flds} | {r.get('tested')} | {r.get('probed')} | {r.get('probe_ref','-')} "
                     f"| {r.get('rd') or '-'} | {r.get('notes','')} |")
    lines.append("")
    g = gaps(data)
    lines.append("## 二、○ 缺口清单（当前未证组合）\n")
    if g:
        for r in g:
            lines.append(f"- **{r['id']}** `{r['table']}` mode={r.get('mode')} "
                         f"shape={r.get('shape')}：tested={r.get('tested')} probed={r.get('probed')} "
                         f"→ P5 探针/契约测试补证")
    else:
        lines.append("- 无（矩阵全绿）")
    lines.append("")
    lines.append("## 三、探针证据索引\n")
    lines.append("| ref | 内容 |")
    lines.append("|---|---|")
    for k, v in (data["meta"].get("probe_refs") or {}).items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="平台契约矩阵门禁/缺口检查")
    ap.add_argument("--check", action="store_true", help="门禁：wrapper 哈希 + MD 一致性，失败 exit 1")
    ap.add_argument("--reverify", action="store_true", help="配合 --check：复证完成后更新 wrapper 哈希")
    ap.add_argument("--sync", action="store_true", help="由 YAML 重新生成人读 MD")
    args = ap.parse_args()

    data = load_yaml()
    cur = wrapper_hash()

    if args.sync:
        MD_PATH.write_text(gen_md(data), encoding="utf-8")
        print("synced ->", MD_PATH)

    if not args.check:
        g = gaps(data)
        print("wrapper hash:", cur, "| yaml:", data["meta"]["wrapper_hash"],
              "| 一致" if cur == data["meta"]["wrapper_hash"] else "| 不一致（需复证）")
        print("○ 缺口:", len(g))
        for r in g:
            print("  -", r["id"], r["table"], r.get("mode"), r.get("shape"))
        return 0

    # ---- 复证（若指定）：先更新哈希再判门，避免 exit(1) 截断 ----
    if args.reverify:
        data["meta"]["wrapper_hash"] = cur
        dump_yaml(data)
        MD_PATH.write_text(gen_md(data), encoding="utf-8")
        print("reverified: wrapper_hash ->", cur)
        data = load_yaml()

    # ---- 门禁 ----
    ok = True
    if cur != data["meta"]["wrapper_hash"]:
        ok = False
        print("FAIL: wrapper 模板已变更（%s → %s）——受影响形态需复证（测试 tested / 探针 probed）:"
              % (data["meta"]["wrapper_hash"], cur))
        for r in data["matrix"]:
            print("   -", r["id"], r["table"], r.get("mode"), r.get("shape"),
                  "tested=%s probed=%s" % (r.get("tested"), r.get("probed")))
        print("提示：补契约测试/平台探针，确认矩阵无 ○ 后运行 `--check --reverify` 更新哈希。")
    if MD_PATH.read_text(encoding="utf-8") != gen_md(data):
        ok = False
        print("FAIL: fundamentals-contract-matrix.md 与 YAML 派生内容不一致（需运行 --sync）。")
    if not ok:
        sys.exit(1)
    print("OK: 契约矩阵门禁通过（哈希一致 + MD 一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())