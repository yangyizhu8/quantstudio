"""解析 pytest -v 输出，提取 (nodeid -> status) 映射，输出 JSON。

用法：
    python scripts/benchmarks/parse_pytest_status.py <pytest_v_log> <out_json>

Status 取值：passed / failed / error / skipped / xfail / xpass / deselected。
仅解析形如 "tests/...::name STATUS" 的行，便于后续做 baseline/optimized 全集比对。
"""
from __future__ import annotations

import json
import re
import sys

_STATUS_RE = re.compile(
    r"^(?P<nodeid>tests/\S+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
)


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    # PowerShell 重定向默认写 UTF-16LE(BOM)；pytest 自身输出常为 UTF-8。
    # 依次尝试，保证跨环境可解析。
    for enc in ("utf-8-sig", "utf-16", "gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def parse(path: str) -> dict:
    result = {}
    for line in _read_text(path).splitlines():
        m = _STATUS_RE.match(line.strip())
        if not m:
            continue
        status = m.group("status").lower()
        nodeid = m.group("nodeid")
        # 同一 nodeid 可能出现多次（如 xpass 后再 summary 行），以首次为准
        result.setdefault(nodeid, status)
    return result


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mapping = parse(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=2)
    print(f"parsed {len(mapping)} nodeids -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
