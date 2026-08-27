#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_platform_ashares.py - 平台 A 股池快照构建器（PTrade 保真模式 P-A0）

输入：平台探针 probe_fidelity_ashares_ptrade.py 导出的日志文本文件（含 FASHARES- 行）。
处理：
  1. 解析 FASHARES-DATE（快照采集日）与 FASHARES-SUMMARY（total + sha256）；
  2. 收集 FASHARES-CODE 行 → 裸码（前 6 位数字）；
  3. 完整性校验：裸码数量 == total 且排序串 SHA-256 == summary.sha256，否则失败退出（fail-closed）；
  4. 写 data/ptrade_fidelity/ashares_<snapshot_date>.parquet（列 code: str，裸码）
     + 同名 .meta.json（snapshot_date / total / sha256 / source_log / created_at）。

用法：
  python scripts/probe_platform_ashares.py <平台日志文件路径> [--out data/ptrade_fidelity]

PIT 门禁（由引擎侧执行）：快照只能用于 backtest_start_date >= snapshot_date 的短窗验证，
本脚本只负责构建快照，不负责回测校验。
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

CODE_RE = re.compile(r'FASHARES-CODE\s+(\S+)')
DATE_RE = re.compile(r'FASHARES-DATE\s+(\S+)')
SUMMARY_RE = re.compile(r'FASHARES-SUMMARY\s+total=(\d+)\s+sha256=(\w+)')
MARKER_RE = re.compile(r'FASHARES-(?:DATE|SUMMARY|CODE)\b')


def _bare(code: str) -> str:
    """取裸码：前 6 位数字（忽略 .SZ/.SS/.XSHE/.XSHG 等后缀）。"""
    s = code.strip().upper()
    digits = ''
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return digits[:6] if len(digits) >= 6 else digits.zfill(6) if digits else ''


def parse_log(path: Path) -> dict:
    date, total, sha256 = None, None, None
    codes: list[str] = []
    text = None
    for enc in ('utf-8-sig', 'gbk', 'utf-8'):
        try:
            text = path.read_text(encoding=enc, errors='replace')
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        sys.exit('FAIL: 无法解码日志文件（尝试 utf-8/gbk 均失败）')
    for raw in text.splitlines():
        line = raw.rstrip('\n').strip()
        if not MARKER_RE.search(line):
            continue
        m = DATE_RE.search(line)
        if m:
            date = m.group(1).strip()
            continue
        m = SUMMARY_RE.search(line)
        if m:
            total, sha256 = int(m.group(1)), m.group(2)
            continue
        m = CODE_RE.search(line)
        if m:
            bare = _bare(m.group(1))
            if bare:
                codes.append(bare)
    return {'date': date, 'total': total, 'sha256': sha256, 'codes': codes}


def verify(parsed: dict) -> None:
    if parsed['date'] is None:
        sys.exit('FAIL: FASHARES-DATE 缺失（快照无采集日，PIT 门禁无法执行）')
    if parsed['total'] is None or parsed['sha256'] is None:
        sys.exit('FAIL: FASHARES-SUMMARY 缺失或不完整')
    codes = parsed['codes']
    if len(codes) != parsed['total']:
        sys.exit('FAIL: 裸码数量 %d != summary total %d（日志不完整或重复）' % (len(codes), parsed['total']))
    bare_sorted = sorted(set(codes))
    if len(bare_sorted) != len(codes):
        sys.exit('FAIL: 存在重复裸码（%d 唯一 %d）' % (len(codes), len(bare_sorted)))
    digest = hashlib.sha256('|'.join(bare_sorted).encode('utf-8')).hexdigest()
    if digest != parsed['sha256']:
        sys.exit('FAIL: SHA-256 不匹配（日志损坏或被截断）\n  期望 %s\n  计算 %s'
                 % (parsed['sha256'], digest))
    print('OK: total=%d sha256=%s date=%s' % (len(bare_sorted), digest, parsed['date']))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('log_path', help='平台探针日志文本文件')
    ap.add_argument('--out', default='data/ptrade_fidelity')
    args = ap.parse_args()
    log_path = Path(args.log_path)
    if not log_path.exists():
        sys.exit('FAIL: 日志文件不存在 %s' % log_path)

    parsed = parse_log(log_path)
    verify(parsed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / ('ashares_%s.parquet' % parsed['date'])
    meta_path = parquet_path.with_suffix('.parquet.meta.json')
    if parquet_path.exists():
        print('WARN: 快照已存在，覆盖 %s' % parquet_path)

    import pandas as pd
    df = pd.DataFrame({'code': sorted(set(parsed['codes']))})
    df.to_parquet(parquet_path, index=False)
    meta = {
        'snapshot_date': parsed['date'],
        'total': len(df),
        'sha256': parsed['sha256'],
        'source_log': str(log_path),
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    print('WROTE %s' % parquet_path)
    print('WROTE %s' % meta_path)


if __name__ == '__main__':
    main()