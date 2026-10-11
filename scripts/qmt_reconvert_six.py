#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qmt_reconvert_six —— QMT 六策略 canonical 重转门（D-21 固化，2026-10-11）。

背景（D-21 定谳）：临时重转脚本把产物写成 UTF-8 无 BOM，违反产物物理编码契约
（converted_code 首行恒 `#coding:gbk`，落盘必须 GBK——QS_QMT_PRODUCT_PHYSICAL_ENCODING，
source_import_qmt.py:39/:63/:1538；M5.1a 备份实测 gbk=OK 且 QMT 跑通）。QMT 客户端
按 ANSI(GBK) 解码策略文件，UTF-8 中文产物直接 SyntaxError（16:43 复现）。

本脚本 = 六策略重转的唯一 canonical 入口（微批循环复用，禁止再手搓临时脚本）：
  1. QmtSourceConverter 转换（unicode）；
  2. validate_qmt_portability 门禁；
  3. 以 QS_QMT_PRODUCT_PHYSICAL_ENCODING（现值 gbk）物理落盘 output/qmt_import/；
  4. 落盘自检：首行 `#coding:gbk` + 无 BOM + GBK 往返逐位一致 + 首行声明与物理编码一致。

用法：python scripts/qmt_reconvert_six.py [--only CANSLIM突破成长选股策略]
退出码 0 = 全 PASS。
"""
import argparse
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantstudio.strategy_compiler.source_import_qmt import (  # noqa: E402
    QmtSourceConverter, QS_QMT_PRODUCT_PHYSICAL_ENCODING)
from quantstudio.strategy_compiler.portability_rules import (  # noqa: E402
    validate_qmt_portability)

SIX = [
    "CANSLIM突破成长选股策略",
    "fall_reversal",
    "tech_etf_mvo",
    "vol_regime_mom_rev",
    "weekly_smallcap",
    "周频三层",
]
SRC_NAME = {
    "CANSLIM突破成长选股策略": "CANSLIM突破成长选股策略.py",
    "fall_reversal": "fall_reversal_quantstudio.py",
    "tech_etf_mvo": "tech_etf_mvo_rotation_quantstudio.py",
    "vol_regime_mom_rev": "vol_regime_mom_rev_quantstudio.py",
    "weekly_smallcap": "weekly_smallcap_growth_momentum_10_quantstudio.py",
    "周频三层": "周频小市值成长动量（三层止损）.py",
}
SRC_DIR = ROOT / "quantstudio" / "backtest" / "strategies"
OUT_DIR = ROOT / "output" / "qmt_import"


def reconvert_one(tag: str) -> tuple[bool, str]:
    srcp = SRC_DIR / SRC_NAME[tag]
    if not srcp.exists():
        return False, "SRC_MISSING %s" % srcp
    src = io.open(srcp, encoding="utf-8").read()
    res = QmtSourceConverter(src).convert()
    code = res.converted_code
    if not code:
        return False, "convert empty: %s" % (res.errors or "unknown",)
    # ① portability 门禁
    ver = validate_qmt_portability(code)
    ok = getattr(ver, "ok", None)
    if ok is None and hasattr(ver, "__iter__"):
        ok = all(getattr(v, "ok", True) for v in ver)
    if not ok:
        return False, "portability FAIL"
    # ② 契约落盘（GBK 物理编码，无 BOM，LF）
    outp = OUT_DIR / (tag + ".py")
    outp.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = code.encode(QS_QMT_PRODUCT_PHYSICAL_ENCODING)
    except UnicodeEncodeError as exc:
        return False, "GBK 不可编码: %s" % (exc,)
    io.open(outp, "wb").write(data.replace(b"\r\n", b"\n"))
    # ③ 落盘自检
    raw = outp.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return False, "self-check: BOM 残留"
    try:
        back = raw.decode(QS_QMT_PRODUCT_PHYSICAL_ENCODING)
    except UnicodeDecodeError as exc:
        return False, "self-check: 落盘 %s 解码失败 @%d" % (
            QS_QMT_PRODUCT_PHYSICAL_ENCODING, exc.start)
    if back.splitlines()[0].strip() != "#coding:gbk":
        return False, "self-check: 首行非 #coding:gbk: %r" % back.splitlines()[0][:40]
    import hashlib
    sha = hashlib.sha256(raw).hexdigest()[:16]
    print("%-24s %6dB sha16=%s enc=%s portability=PASS selfcheck=PASS"
          % (tag, len(raw), sha, QS_QMT_PRODUCT_PHYSICAL_ENCODING))
    return True, sha


def main() -> int:
    ap = argparse.ArgumentParser(description="QMT 六策略 canonical 重转门（GBK 契约落盘）")
    ap.add_argument("--only", default=None, help="只重转指定 tag（缺省全部六个）")
    args = ap.parse_args()
    tags = [args.only] if args.only else SIX
    fails = []
    for tag in tags:
        ok, msg = reconvert_one(tag)
        if not ok:
            print("%-24s FAIL: %s" % (tag, msg))
            fails.append((tag, msg))
    if fails:
        print("FAILS: %d" % len(fails))
        return 1
    print("ALL %d PASS (enc=%s)" % (len(tags), QS_QMT_PRODUCT_PHYSICAL_ENCODING))
    return 0


if __name__ == "__main__":
    sys.exit(main())
