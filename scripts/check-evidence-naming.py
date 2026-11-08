#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check-evidence-naming.py — 证据文件命名门禁（D-2 纪律机检）v2。

v1 缺陷（R-PERF-22 裁定）：17 个前缀豁免覆盖了全部 30 个文件 ⇒ compliant=0 / exempt=30 /
violations=0 ⇒ 空集恒真的假 PASS。

v2 设计（E-1~E-4）：
  E-1 只保留真历史锚（cprofile_/memtrace_/daily_cprofile_），删掉全部会持续新增的前缀。
  E-2 豁免 = 精确文件名 + mtime cutoff 冻结：只豁免「已存在、不再新增、且 mtime <= CUTOFF」的
      历史存量；文件一旦被覆写（mtime 变新）豁免即失效 —— 这正好抓住 D-2 违规的机理。
  E-3 PASS 判据含覆盖校验：compliant==0 且 exempt>0 ⇒ FAIL（门禁空转）；
      exempt/(compliant+exempt) > 50% ⇒ WARN。
  E-4 `--self-test`：三条负例回归（合规名 PASS / 覆写名 FAIL / 旧 mtime 豁免 PASS）。

命名格式：<probe>_<version>_<thermal>_<layer>_<window>_<stamp>.{json,log}
  version : 1.5.6 | dd156 等 duckdb 版本标识
  thermal : cold | hot | warm | cold-C-approx-v1
  layer   : wall | python-alloc | data-query | golden | mixed
  window  : YYYY-MM-DD 或 YYYY-MM-DD~YYYY-MM-DD

用法：
  python scripts/check-evidence-naming.py [目录...] [--strict]
  python scripts/check-evidence-naming.py --self-test
退出码：0 PASS/WARN（非 strict）/ 1 FAIL 或 strict 下 WARN
"""
import io
import os
import re
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_DIRS = [os.path.join('agent_workspace', 'perf_eval_rperf01')]

# E-2：mtime cutoff —— 此时间点之后被写过的文件，一律不再享受豁免
CUTOFF_TS = time.mktime(time.strptime('2026-11-08 14:00:00', '%Y-%m-%d %H:%M:%S'))

# E-2：精确文件名豁免（历史存量，冻结；不含任何通配前缀）
EXEMPT_FILES = {
    # 1.4.5 时代唯一残留锚（原始读数已灭失，仅此旁证）
    'cprofile_readings.json', 'cprofile_golden.json', 'cprof.pstats',
    'memtrace_readings.json', 'memtrace_golden.json',
    'daily_cprofile_readings.json', 'cprof_daily.pstats',
    # D-2 违规案卷（历史证据，不得再被覆写）
    'baseline_STALE_1106_readings.json', 'baseline_STALE_1106_golden.json',
    'pytest_lf_r1r2.xml',
}

# R-PERF-23/24：已知违规（历史既成，不 FAIL 但**必须可见**）
# frozen_mtime = 「1.4.5 原版灭失时点」的登记值（首次覆写时刻）；实跑比对当前 mtime
#   —— 不一致 ⇒ KNOWN_VIOLATION_MUTATED（覆写在冻结期内**再次发生**，WARN / strict FAIL）
#   —— 文件不存在 ⇒ KNOWN_VIOLATIONS_MISSING（登记表与实际目录不对齐）
KNOWN_VIOLATIONS = {
    'baseline_readings.json': {
        'why': '01:57 被 1.5.6 跑覆写（D-2 案发），1.4.5 原版灭失；03:29 又发生第二次覆写（B2_pre_hot）；探针硬编码路径不可移走',
        'frozen_mtime': '2026-11-08 01:57:00',
    },
    'baseline_golden.json': {
        'why': 'golden 侧，1.4.5 原版灭失；03:29 第二次覆写；仅 cprofile/memtrace 旁证',
        'frozen_mtime': '2026-11-08 01:57:00',
    },
    'daily_baseline_readings.json': {
        'why': '01:50 被 1.5.6 跑覆写（D-2 案发），1.4.5 原版灭失',
        'frozen_mtime': '2026-11-08 01:50:56',
    },
}

# 已登记但目录中不存在（M 对齐：从 KNOWN_VIOLATIONS 移出，单列以免误读为「命中」）
KNOWN_VIOLATIONS_MISSING = {
    'daily_baseline_golden.json':
        '登记时即不存在（daily 探针只写 readings，不写 golden）；如需 golden 侧对照走 cprofile/daily_cprofile 旁证',
}

# 覆写判定的 mtime 容差（秒）：同一时刻写入的微小差异不计为 mutated
MUTATION_TOLERANCE_S = 60

# 归档冻结目录（K-2）：整目录排除，标 legacy-frozen
LEGACY_DIRNAME = 'legacy'

PATTERN = re.compile(
    r'^(?P<probe>[A-Za-z0-9_.-]+?)'
    r'_(?P<version>(?:\d+\.\d+\.\d+|dd\d{3}))'
    r'_(?P<thermal>cold-C-approx-v1|cold|hot|warm)'
    r'_(?P<layer>wall|python-alloc|data-query|golden|mixed)'
    r'_(?P<window>\d{4}-\d{2}-\d{2}(?:~\d{4}-\d{2}-\d{2})?)'
    r'_(?P<stamp>\d{8}(?:[_-]\d{4,6})?)'
    r'\.(json|log)$'
)


def is_exempt(name, mtime):
    """E-2：精确名 + mtime 冻结双重判定。"""
    return name in EXEMPT_FILES and mtime <= CUTOFF_TS


def is_known_violation(name, mtime):
    """R-PERF-23/24：已知违规——历史既成且 mtime 与登记值一致（未被再次覆写）才不 FAIL。"""
    rec = KNOWN_VIOLATIONS.get(name)
    if not rec:
        return False
    frozen = time.mktime(time.strptime(rec['frozen_mtime'], '%Y-%m-%d %H:%M:%S'))
    return abs(mtime - frozen) <= MUTATION_TOLERANCE_S


def scan(dirs, strict=False):
    compliant, exempt, known, legacy, bad = 0, 0, 0, 0, []
    mutated, missing = [], []
    for d in dirs:
        if not os.path.isdir(d):
            print(f'[skip] not a dir: {d}')
            continue
        for root, subdirs, files in os.walk(d):
            if LEGACY_DIRNAME in subdirs:
                # K-2：归档冻结目录整目录排除
                lp = os.path.join(root, LEGACY_DIRNAME)
                legacy += sum(len(fs) for _r, _s, fs in os.walk(lp))
                subdirs.remove(LEGACY_DIRNAME)
            for f in files:
                if not f.endswith(('.json', '.log')):
                    continue
                p = os.path.join(root, f)
                mt = os.path.getmtime(p)
                if PATTERN.match(f):
                    compliant += 1
                elif is_exempt(f, mt):
                    exempt += 1
                elif f in KNOWN_VIOLATIONS:
                    rec = KNOWN_VIOLATIONS[f]
                    frozen = time.mktime(time.strptime(rec['frozen_mtime'], '%Y-%m-%d %H:%M:%S'))
                    if abs(mt - frozen) <= MUTATION_TOLERANCE_S:
                        known += 1
                    else:
                        # M-1：冻结期内再次覆写 ⇒ 必须报
                        mutated.append((p, rec['frozen_mtime'],
                                        time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt))))
                else:
                    bad.append((p, 'name-not-conforming'))

    total = compliant + exempt + known + len(mutated) + len(bad)
    print(f'files={total}  compliant={compliant}  exempt={exempt}  '
          f'known_violations={known}  mutated={len(mutated)}  violations={len(bad)}  '
          f'legacy_frozen={legacy}')
    # R-PERF-23：已知违规必须可见（豁免是「不该管」，不是「管不了就藏起来」）
    if known:
        for n, rec in sorted(KNOWN_VIOLATIONS.items()):
            if any(os.path.basename(p) == n for p, _f, _now in mutated) is False and \
               os.path.exists(os.path.join(dirs[0], n)):
                print(f'  KNOWN_VIOLATION: {n} — frozen={rec["frozen_mtime"]} — {rec["why"]}')
    # M-1：冻结期内再次覆写
    for p, fz, now in mutated:
        print(f'  KNOWN_VIOLATION_MUTATED: {os.path.basename(p)} frozen={fz} now={now} '
              f'（冻结期内再次覆写；strict 下 FAIL）')
    # M 对齐：登记但不存在
    for n, why in sorted(KNOWN_VIOLATIONS_MISSING.items()):
        if not any(os.path.exists(os.path.join(d, n)) for d in dirs):
            print(f'  KNOWN_VIOLATIONS_MISSING: {n} — {why}')

    # E-3 判据（顺序重要：先报实质违规/冻结期覆写，再判门禁空转）
    judged = compliant + known + len(mutated) + len(bad)   # 真正进入判定的文件数（exempt 不算）
    verdict, code = 'PASS', 0
    if bad or mutated:
        tag = []
        if bad:
            tag.append(f'{len(bad)} name violations')
        if mutated:
            tag.append(f'{len(mutated)} frozen-record mutations')
        verdict = ('FAIL(strict: ' if strict else 'WARN(non-strict: ') + ', '.join(tag) + ')'
        code = 1 if strict else 0
    elif judged == 0 and exempt > 0:
        # v1 病灶：全部文件被豁免、一个都没判定 ⇒ 空集恒真
        verdict, code = 'FAIL(idle-gate: nothing actually checked)', 1
    elif total and exempt / total > 0.5:
        verdict, code = 'WARN(exempt>50%, gate coverage too low)', (1 if strict else 0)
    elif bad:
        verdict, code = ('FAIL(strict)' if strict else 'WARN(non-strict, not blocking)'), (1 if strict else 0)

    for p, why in bad:
        print(f'  VIOLATION[{why}]: {p}')
    print(f'RESULT: {verdict}  (cutoff={time.strftime("%Y-%m-%d %H:%M", time.localtime(CUTOFF_TS))})')
    return code


def self_test():
    """E-4 负例回归：三条断言，全部通过才 exit 0。"""
    tmp = tempfile.mkdtemp(prefix='naming_gate_selftest_')
    fails = []

    def run_case(fname, mtime, expect_ok, label):
        p = os.path.join(tmp, fname)
        io.open(p, 'w', encoding='utf-8').write('x')
        os.utime(p, (mtime, mtime))
        ok_name = bool(PATTERN.match(fname))
        mt = os.path.getmtime(p)
        ok = ok_name or is_exempt(fname, mt) or is_known_violation(fname, mt)
        got = 'PASS' if ok else 'FAIL'
        status = 'OK' if (got == 'PASS') == expect_ok else 'MISMATCH'
        if status != 'OK':
            fails.append(label)
        print(f'  [{status}] {label}: {fname} -> {got} (expect {"PASS" if expect_ok else "FAIL"})')

    now = time.time()
    frozen = time.mktime(time.strptime(KNOWN_VIOLATIONS['baseline_readings.json']['frozen_mtime'],
                                       '%Y-%m-%d %H:%M:%S'))
    stale = frozen - 86400

    # 1) 合规四要素名：PASS
    run_case('r20_1.5.6_cold-C-approx-v1_wall_2026-03-02~2026-03-06_20261108_0313.json',
             now, True, 'conforming-name')
    # 2) D-2 案发文件名 + 新鲜 mtime（覆写场景）：必须 FAIL  ← 核心负例
    run_case('baseline_readings.json', now, False, 'D-2-overwrite-case')
    # 3) 同一文件名 + mtime 与登记值一致（历史存量冻结）：known 通过
    run_case('baseline_readings.json', frozen, True, 'historical-frozen-at-registered-mtime')
    # 4) 同一文件名 + mtime 与登记值不一致（冻结期内再次覆写）：M-1 必须报
    run_case('baseline_readings.json', frozen + 7200, False, 'M-1-mutated-within-freeze')
    # 5) 已删除前缀不得再豁免（如 A1_post_cold.log）
    run_case('A1_post_cold.log', stale, False, 'removed-prefix-no-exempt')

    shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f'SELF_TEST: FAIL {fails}')
        return 1
    print('SELF_TEST: PASS (5/5)')
    return 0


def main():
    if '--self-test' in sys.argv:
        return self_test()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    strict = '--strict' in sys.argv
    return scan(args or DEFAULT_DIRS, strict)


if __name__ == '__main__':
    sys.exit(main())
