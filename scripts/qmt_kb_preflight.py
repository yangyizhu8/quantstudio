#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qmt_kb_preflight —— QMT 转换预检读取器（D-33 件 D；2026-10-21 A3 薄壳化）

公共核已抽取至 scripts/kb_preflight.py（多库 --kb + 域过滤 --domain + hits + validate_entry）；
本壳仅：固定 KB_DIR＝knowledge/qmt ＋ 再导出旧版公开名（签名不变，既有用例 importlib 直调）
＋ main 委托公共核。判定逻辑与文案逐字符住在公共核：
B1-B3 BLOCK／B4 降 WARN 待证／W1-W6 分级／QS_KB_ACK 豁免降级。

边界表（唯一权威）：docs/qmt-kb-preflight-boundary.md

用法：
  python scripts/qmt_kb_preflight.py --source <策略源码.py> [--product <QMT产物.py>] [--json]
退出码：0＝无 BLOCK（可含 WARN）｜1＝存在 BLOCK（阻断转换）｜2＝用法/读文件错误
目录缺失：新实现报错 exit 2（旧版静默 0 条目 PASS），属有意分叉（A3 泛化修复，不复制旧缺陷）。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kb_preflight as _core  # noqa: E402 （同目录导入；CLI 与 importlib 双语境可用）

ROOT = pathlib.Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "knowledge" / "qmt"

# ---- 再导出（签名与旧版一致；既有 10 用例 importlib 直调以下名字） ----
BLOCK_RULES = _core.BLOCK_RULES
WARN_RULES = _core.WARN_RULES
ACK_RE = _core.ACK_RE
check_product = _core.check_product
check_source = _core.check_source


def load_kb_index():
    """读 knowledge/qmt/*.md frontmatter → 已知坑清单索引（无参签名＝旧版兼容）。"""
    return _core.load_kb_index(KB_DIR)


def main(argv=None):
    """旧 CLI 入口：--source/--product/--json 行为与退出码 0/1/2 不变（委托公共核）。"""
    return _core.main(argv, default_kb=KB_DIR)


if __name__ == "__main__":
    sys.exit(main())
