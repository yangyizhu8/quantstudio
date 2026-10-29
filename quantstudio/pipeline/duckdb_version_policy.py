# -*- coding: utf-8 -*-
"""duckdb 版本放行策略 —— **唯一判定源**（2026-10-29）。

放行集合（单一真相）
--------------------
    { 1.4.5 ≤ x < 1.5 }  ∪  { x == 1.5.6 }

为什么是集合而不是单值
----------------------
旧实现 `REQUIRED_SERIES = "1.4"` + `ver.startswith("1.4.")` 无法表达
「1.5.6 放行、1.5.5 拒绝」，也无法表达下界 ≥1.4.5（`1.4.0` 会被误判为合规）。
本模块用「区间元组 + 精确点」表达集合，并按**前三段整数**比较。

判定语义（fail-closed）
-----------------------
- 版本串取前三段整数：`1.5.6` → (1,5,6)；`1.5.6.dev0` → (1,5,6)
- 段数不足 / 含非数字 / 空串 → 解析失败 → **判为不合规**
- `import duckdb` 失败 → **判为不合规**（无法判定即拒绝）

调用形态（三个薄壳共用本模块；**禁止在别处复述版本规则**）
-----------------------------------------------------------
1. 启动门禁：`quantstudio/pipeline/duckdb_version_gate.py`（进程内调用）
2. 运维止损脚本：`check_wal.ps1` / `check_wal.sh`（单文件直调本模块）
3. 测试契约：`tests/test_duckdb_version_gate.py`（import 本模块）

本模块**无包内 import**，故可被 `python <本文件> --check` 单文件直接执行
（供 ps1/sh 薄壳调用，避免它们依赖可导入的项目包）。
"""
from __future__ import annotations

import json
import sys
from typing import Optional, Sequence, Tuple

Version = Tuple[int, int, int]

# 放行集合：每项为 (下界, 下界闭, 上界, 上界闭)
#   [1.4.5, 1.5.0)  ≡  { x : 1.4.5 ≤ x < 1.5 }
#   == 1.5.6
ALLOWED_RANGES: Tuple[Tuple[Version, bool, Version, bool], ...] = (
    ((1, 4, 5), True, (1, 5, 0), False),
    ((1, 5, 6), True, (1, 5, 6), True),
)

# 供人读与日志用的集合描述
POLICY_TEXT = "1.4.5 ≤ x < 1.5 或 x == 1.5.6"


# 版本串形态分层（2026-10-29 H1 裁定：严格三段口径，fail-closed 优先）
SHAPE_STRICT = "strict"          # 恰三段且各段全数字 → 唯一可参与集合判定的形态
SHAPE_SHORT = "short"            # 两段（缺省 patch=0）→ 可识别但不在集合 ⇒ 拒绝（exit 1）
SHAPE_SUFFIXED = "suffixed"      # 前三段可解析但带预发布/构建后缀 ⇒ 非严格形态 ⇒ 拒绝（exit 1）
SHAPE_UNPARSABLE = "unparsable"  # 无法解析 ⇒ ERROR（exit 2）


def classify(version: str) -> str:
    """版本串形态分层：strict / short / suffixed / unparsable。

    严格三段口径（2026-10-29 H1 裁定，fail-closed 优先）：
      · `1.5.6`                  → strict    （唯一可参与集合判定）
      · `1.5` / `2.1`            → short     （两段；可识别但不在集合 ⇒ FAIL / exit 1）
      · `1.5.6.dev0` / `…post1`  → suffixed  （带预发布/构建后缀 ⇒ 非严格形态 ⇒ FAIL / exit 1）
      · `unknown` / `1.5.6rc1` / 空串 → unparsable（⇒ ERROR / exit 2）
    """
    if not isinstance(version, str):
        return SHAPE_UNPARSABLE
    parts = version.strip().split(".")
    if not parts or any(not p for p in parts):
        return SHAPE_UNPARSABLE
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return SHAPE_STRICT
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return SHAPE_SHORT
    if len(parts) > 3 and all(p.isdigit() for p in parts[:3]):
        return SHAPE_SUFFIXED
    return SHAPE_UNPARSABLE


def parse_version(version: str) -> Optional[Version]:
    """**严格三段**版本 → (major, minor, patch)；非严格形态返回 None（⇒ 不合规）。"""
    if classify(version) != SHAPE_STRICT:
        return None
    nums = [int(p) for p in version.strip().split(".")]
    return (nums[0], nums[1], nums[2])


def _in_range(v: Version, lo: Version, lo_inc: bool, hi: Version, hi_inc: bool) -> bool:
    lower = (v >= lo) if lo_inc else (v > lo)
    upper = (v <= hi) if hi_inc else (v < hi)
    return lower and upper


def is_allowed(version: str) -> bool:
    """版本是否落在放行集合内；**非严格三段形态一律不合规**（fail-closed）。"""
    parsed = parse_version(version)
    if parsed is None:
        return False
    return any(_in_range(parsed, lo, lo_inc, hi, hi_inc)
               for lo, lo_inc, hi, hi_inc in ALLOWED_RANGES)


def describe_policy() -> str:
    """人类可读的放行集合描述（供日志与报错文案）。"""
    return POLICY_TEXT


def probe_version() -> Tuple[bool, str]:
    """探测当前解释器的 duckdb 版本。返回 (ok, 版本串 或 失败原因)。"""
    try:
        import duckdb  # noqa: F401
    except Exception as exc:  # noqa: BLE001 —— 任何导入异常都视为无法判定
        return False, f"无法导入 duckdb: {type(exc).__name__}: {exc}"
    ver = str(getattr(duckdb, "__version__", "") or "")
    if not ver:
        return False, "duckdb 版本串为空"
    return True, ver


def main(argv: Sequence[str]) -> int:
    """CLI：--check [版本串]（省略版本串则自动探测当前解释器）/ --json / --describe。

    退出码：0 = 放行（PASS）；1 = 解析成功但不在集合（FAIL）；2 = 无法判定（ERROR）。
    """
    args = list(argv)
    as_json = "--json" in args
    if as_json:
        args.remove("--json")

    if args and args[0] == "--describe":
        print(describe_policy())
        return 0

    target: Optional[str] = None
    if args:
        if args[0] in ("--check", "-c"):
            args = args[1:]
            target = args[0] if args else None
        else:
            print(f"未知参数: {args[0]}", file=sys.stderr)
            print("用法: python duckdb_version_policy.py --check [版本串] [--json]", file=sys.stderr)
            return 2
    else:
        print("用法: python duckdb_version_policy.py --check [版本串] [--json]", file=sys.stderr)
        return 2

    if target is None:
        ok, detail = probe_version()
        if not ok:
            if as_json:
                print(json.dumps({"allowed": False, "version": None, "parsed": None,
                                  "reason": "import_failed", "detail": detail},
                                 ensure_ascii=False))
            else:
                print("ERROR import_failed")
            print(detail, file=sys.stderr)
            return 2
        version = detail
    else:
        version = target

    shape = classify(version)
    if shape == SHAPE_UNPARSABLE:
        if as_json:
            print(json.dumps({"allowed": False, "version": version, "parsed": None,
                              "reason": "unparsable"}, ensure_ascii=False))
        else:
            print("ERROR unparsable")
        return 2

    if shape != SHAPE_STRICT:
        # short / suffixed：可识别但**非严格三段形态** ⇒ 判为不合规（FAIL / exit 1）
        if as_json:
            print(json.dumps({"allowed": False, "version": version, "parsed": None,
                              "reason": "non_strict_shape", "shape": shape},
                             ensure_ascii=False))
        else:
            print("FAIL " + version)
        return 1

    parsed = parse_version(version)
    allowed = is_allowed(version)
    reason = "in_range" if allowed else "out_of_range"
    if as_json:
        print(json.dumps({"allowed": allowed, "version": version,
                          "parsed": list(parsed), "reason": reason},
                         ensure_ascii=False))
    else:
        print(("PASS " if allowed else "FAIL ") + version)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
