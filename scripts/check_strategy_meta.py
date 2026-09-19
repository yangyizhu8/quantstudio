#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略文件元信息头校验（策略生态准入 · D2b 配套，2026-09-19）。

规则
    策略文件**首个 docstring** 内须包含以下 `@key: value` 形式（大小写不敏感、
    允许前后空白）的元信息行：

        @strategy_name  策略名称（非空）
        @author         作者（GitHub 用户名或署名，非空）
        @license        许可（如 MIT / Apache-2.0，非空）
        @risk_level     风险等级（低 / 中 / 高 之一）

    可选字段（建议但非强制）：@source 来源、@description 描述。

    仅对**新增文件**强制（存量策略无此头，由 CI 侧按 `--diff-filter=A` 只传新增件）；
    对本脚本单独调用时默认全部强制，可用 --optional 降级为提示。

用法
    python scripts/check_strategy_meta.py <文件...>
    python scripts/check_strategy_meta.py --optional <文件...>     # 仅提示，不失败
    退出码：0 = 通过；1 = 缺失/不合法；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REQUIRED = {
    "@strategy_name": "策略名称",
    "@author": "作者",
    "@license": "许可",
    "@risk_level": "风险等级",
}
OPTIONAL = {"@source": "来源", "@description": "描述"}
RISK_LEVELS = ("低", "中", "高")

_LINE_RE = re.compile(r"^\s*@([A-Za-z_]+)\s*[:：]\s*(.+?)\s*$")


def parse_meta(path: Path) -> tuple[dict[str, str], str | None]:
    """返回 (meta_dict, error)。"""
    try:
        src = path.read_text(encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        return {}, "无法读取：%s" % exc
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return {}, "语法错误：第 %s 行 %s" % (exc.lineno, exc.msg)

    doc = ast.get_docstring(tree, clean=False)
    if not doc:
        return {}, "缺少文件头 docstring（元信息头须写在首个 docstring 内）"

    meta: dict[str, str] = {}
    for line in doc.splitlines():
        m = _LINE_RE.match(line)
        if m:
            meta["@" + m.group(1).lower()] = m.group(2)
    return meta, None


def check_file(path: Path, *, optional: bool = False) -> tuple[list[str], list[str]]:
    """返回 (fails, warns)。"""
    if path.suffix.lower() != ".py":
        return [], []
    meta, err = parse_meta(path)
    if err:
        return ([] if optional else ["%s：%s" % (path, err)]), (["%s：%s" % (path, err)] if optional else [])

    fails: list[str] = []
    warns: list[str] = []
    for key, label in REQUIRED.items():
        if key not in meta:
            msg = "%s：缺少 %s（%s）" % (path, key, label)
            (warns if optional else fails).append(msg)
        elif not meta[key].strip():
            msg = "%s：%s 为空" % (path, key)
            (warns if optional else fails).append(msg)

    lvl = meta.get("@risk_level", "").strip()
    if lvl and lvl not in RISK_LEVELS:
        msg = "%s：@risk_level 取值 `%s` 不合法（应为 %s 之一）" % (
            path, lvl, "/".join(RISK_LEVELS))
        (warns if optional else fails).append(msg)

    for key in OPTIONAL:
        if key not in meta:
            warns.append("%s：建议补充 %s（可选）" % (path, key))
    return fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="策略文件元信息头校验")
    ap.add_argument("files", nargs="+", help="策略 .py 文件")
    ap.add_argument("--optional", action="store_true",
                    help="降级为提示（用于存量文件批量自检）")
    args = ap.parse_args(argv)

    paths = [Path(f) for f in args.files]
    if not paths:
        print("没有待检文件", file=sys.stderr)
        return 2

    n_fail = n_warn = 0
    for p in paths:
        if p.is_dir():
            continue
        fails, warns = check_file(p, optional=args.optional)
        if fails:
            n_fail += 1
            for x in fails:
                print("FAIL %s" % x, file=sys.stderr)
        if warns:
            n_warn += 1
            for x in warns[:4]:
                print("  (warn) %s" % x, file=sys.stderr)

    print("-" * 60)
    print("校验 %d 个文件：FAIL %d，WARNING %d" % (len(paths), n_fail, n_warn))
    if n_fail:
        print("结论：FAIL —— 新增策略须补齐元信息头（模板见 CONTRIBUTING-STRATEGIES.md）")
        return 1
    print("结论：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
