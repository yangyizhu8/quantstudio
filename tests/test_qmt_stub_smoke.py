# -*- coding: utf-8 -*-
"""QMT 管线 M3 · 块2/2：六策略桩冒烟驱动器 + 形状判据 S1-S7 断言（测试域）。

定位（docs/qmt-pipeline-m3-plan.md §②/§⑥，②审批准含三条件）：
- 对 M2b 六策略 QMT 产物（output/qmt_export/<sid>/qmt/<sid>_qmt.py，GBK 物理编码，
  块1 tests/qmt_stub.py 的 QmtStubRunner 已处理读入）逐个跑桩冒烟
  （init(C) ×1 → handlebar(C) ×N，N=20，plan §⑤ 驱动方式），逐条断言形状判据；
- 每策略结构化结果落盘 output/qmt_m3_smoke/<sid>.json（供验收方取证）。

判据强度说明（plan §⑥ 证据效力声明，写死）：
- 桩冒烟**仅证**「产物在 QMT API 契约下可被驱动、序列形状自洽」；
- **不证**数值正确性（桩自模拟撮合/复权/停牌 ⇒ 差异无法归因「桩失真 vs 策略缺陷」）；
- 数值对照归 M5 真实环境——**不得把 S1-S7 全绿表述为「数值正确」**。

判据清单（plan §⑥ checklist，逐条可机检；opType 23=买/24=卖 语义承块1
tests/qmt_stub.py 头注所引 05-枚举常量.md:43-44）：
- S1 无未捕获异常：run() 返回 error 为空；红则**原样**打印 traceback（禁筛选/简化）；
- S2 驱动节拍：init_calls == 1 且 bars_run == n_bars（默认 20）；
- S3 订单方向合理：每笔 opType ∈ {23, 24} 且 volume > 0（无零/负数量）；
  分类统计买(23)/卖(24)笔数；
- S4 时点单调：订单序列 barpos 非递减（无「未来 bar 下的单」）；
- S5 持仓非负：positions_series 任意时点 amount >= 0 且 enable_amount <= amount
  （plan §⑥ 原文「无负持仓」⇒ enable_amount >= 0 同查）；
- S6 净值量级：nav_series 每项 > 0 且 ∈ [0.1×init_cash, 10×init_cash]
  （防量级错乱/归零；序列为空 = 无数据可判，同判红）；
- S7 审计行在位：产物 print 的 QS_QMT_* 审计行至少 1 条被 audit_lines 捕获，
  逐策略打印捕获到的种类。

纪律（任务规格）：
- 产物属 output/ 本地工件域——缺失时 pytest.skip 不 fail（打印缺失路径）；
- 若某策略在桩下真实跑不通（S1 红）：**不得为通过而弱化断言**——保留失败并如实报告
  （这正是 M3 的价值；plan §⑥「任一红即 BLOCK，差异须逐项归因」）；
- 判据全部先评估后单点断言：任一判据红不遮蔽其余判据的红（一次运行给出完整红清单，
  便于验收方逐项归因；取证 JSON 在断言之前落盘，FAIL 时同样在位）。
"""

from __future__ import annotations

import json
import os
import re

import pytest

from tests.qmt_stub import QmtStubRunner

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 仓库根（tests/ 上一级；产物与取证 JSON 均按仓库根相对定位）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QMT_EXPORT_DIR = os.path.join(_REPO_ROOT, 'output', 'qmt_export')
_SMOKE_DUMP_DIR = os.path.join(_REPO_ROOT, 'output', 'qmt_m3_smoke')

#: 六策略横验证名单（M2b 产物，plan §④；产物已生成，本测试**不重新生成**）
STRATEGY_IDS = [
    'CANSLIM突破成长选股策略',
    'fall_reversal',
    'tech_etf_mvo_rotation',
    'vol_regime_mom_rev',
    'weekly_smallcap_growth_momentum_10',
    '周频小市值成长动量（三层止损）',
]

#: 驱动参数：handlebar ×20 根（S2 断言面）；初始资金 100 万（S6 量级界 [10万, 1000万]）
N_BARS = 20
INIT_CASH = 1000000.0

#: S7 审计行种类提取（块1 _AUDIT_PREFIX='QS_QMT_'；六产物实测种类为全大写形态——
#: QS_QMT_DENY_REMOVED / QS_QMT_SEMANTIC_DIFF / QS_QMT_LIFECYCLE_MOVED /
#: QS_QMT_UNDETERMINED / QS_QMT_WRAPPER_FETCH_FAIL / QS_QMT_WRAPPER_SKIP）。
#: 大小写异形行仍计入 S7 审计行总数，仅种类提取为 best-effort）。
_AUDIT_KIND_RE = re.compile(r'QS_QMT_[A-Z0-9_]+')

#: 违规明细展示上限（完整违规**总数**照实报告，仅展示样本截断）
_VIOL_SAMPLE_LIMIT = 10
#: 取证 JSON 内桩登记行（QMT_STUB_NOTE，块1 独立前缀）样本上限
_STUB_NOTE_SAMPLE_LIMIT = 30


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _product_path(strategy_id):
    """六策略 QMT 产物路径（output/ 本地工件域；GBK 读入由块1 runner 处理）。"""
    return os.path.join(_QMT_EXPORT_DIR, strategy_id, 'qmt',
                        '%s_qmt.py' % strategy_id)


def _audit_kinds(audit_lines):
    """S7 审计行种类统计 → {QS_QMT_XXX: 出现行数}（同行多种类分别计 1）。"""
    kinds = {}
    for line in audit_lines:
        for kind in set(_AUDIT_KIND_RE.findall(line)):
            kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def _check_s3_orders(orders):
    """S3：每笔 opType ∈ {23,24} 且 volume > 0 → 违规列表 [(序, 代码, opType, volume)]。"""
    bad = []
    for i, rec in enumerate(orders):
        op = rec.get('opType')
        vol = rec.get('volume')
        if op not in (23, 24) or vol is None or vol <= 0:
            bad.append((i, rec.get('orderCode'), op, vol))
    return bad


def _check_s4_monotonic(orders):
    """S4：订单 barpos 非递减 → 违规列表 [(前序, 前barpos, 后序, 后barpos)]。"""
    bad = []
    for i in range(1, len(orders)):
        prev = orders[i - 1].get('barpos')
        cur = orders[i].get('barpos')
        if prev is None or cur is None or cur < prev:
            bad.append((i - 1, prev, i, cur))
    return bad


def _check_s5_positions(positions_series):
    """S5：任意时点 amount >= 0、enable_amount >= 0、enable_amount <= amount。

    → 违规列表 [(bar序, 代码, amount, enable_amount, 原因)]（「无负持仓」plan §⑥）。
    """
    bad = []
    for bi, snap in enumerate(positions_series):
        for code in sorted(snap):
            pos = snap[code] or {}
            amt = pos.get('amount')
            en = pos.get('enable_amount')
            reasons = []
            if amt is None:
                reasons.append('amount 缺失')
            elif amt < 0:
                reasons.append('amount < 0')
            if en is None:
                reasons.append('enable_amount 缺失')
            elif en < 0:
                reasons.append('enable_amount < 0')
            if amt is not None and en is not None and en > amt:
                reasons.append('enable_amount > amount')
            if reasons:
                bad.append((bi, code, amt, en, '/'.join(reasons)))
    return bad


def _check_s6_nav(nav_series, init_cash):
    """S6：每项 nav > 0 且 ∈ [0.1×, 10×]init_cash → 违规列表 [(bar序, nav)]。

    序列为空 = 无任何 bar 净值可判 → 判红（bar 推进时 nav 逐根落档，
    仅在 init/驱动早期异常〔S1/S2 已红〕时出现，不构成独立误报源）。
    """
    if not nav_series:
        return [('nav_series 为空（无任何 bar 净值可判）')]
    lo = 0.1 * init_cash
    hi = 10.0 * init_cash
    bad = []
    for i, v in enumerate(nav_series):
        if v is None or not (v > 0) or v < lo or v > hi:
            bad.append((i, v))
    return bad


def _fmt_pos_snapshot(snap):
    """持仓快照一行化（归因用；空快照 = 该 bar 无持仓，合法形态）。"""
    if not snap:
        return '(空：该 bar 无持仓)'
    parts = []
    for code in sorted(snap):
        pos = snap[code] or {}
        parts.append('%s(amount=%r,enable=%r,cost=%r)' % (
            code, pos.get('amount'), pos.get('enable_amount'),
            pos.get('avg_cost')))
    return '{%s}' % ', '.join(parts)


def _dump_smoke_json(strategy_id, runner, result, kinds, s3_bad):
    """每策略结构化结果落盘 output/qmt_m3_smoke/<sid>.json（取证面）。

    规格八键：bars_run / init_calls / orders（含计数与样本）/ positions_tail /
    nav_min / nav_max / audit_lines / error；另附取证补充键（strategy_id /
    product_path / n_bars / init_cash / nav_series / audit_kinds /
    stub_note_sample）。
    落盘失败不改变判据 verdict——打印 WARN 后继续（断言面仅 S1-S7）。
    """
    orders = result.get('orders') or []
    nav = result.get('nav_series') or []
    n = len(orders)
    payload = {
        'strategy_id': strategy_id,
        'product_path': runner.product_path,
        'n_bars': runner.n_bars,
        'init_cash': runner.init_cash,
        'bars_run': result.get('bars_run'),
        'init_calls': result.get('init_calls'),
        'orders': {
            'count': n,
            'buy_count_opType23': sum(
                1 for o in orders if o.get('opType') == 23),
            'sell_count_opType24': sum(
                1 for o in orders if o.get('opType') == 24),
            'invalid_count': len(s3_bad),
            'sample_head5': orders[:5],
            'sample_tail5': orders[-5:] if n > 10 else [],
        },
        'positions_tail': (result['positions_series'][-1]
                           if result.get('positions_series') else {}),
        'nav_min': min(nav) if nav else None,
        'nav_max': max(nav) if nav else None,
        'nav_series': list(nav),
        'audit_lines': list(result.get('audit_lines') or []),
        'audit_kinds': kinds,
        'stub_note_sample': [
            ln for ln in (result.get('stdout_lines') or [])
            if ln.startswith('QMT_STUB_NOTE')][:_STUB_NOTE_SAMPLE_LIMIT],
        'error': result.get('error'),
    }
    try:
        os.makedirs(_SMOKE_DUMP_DIR, exist_ok=True)
        path = os.path.join(_SMOKE_DUMP_DIR, '%s.json' % strategy_id)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return path
    except Exception as exc:  # 取证面 best-effort：落盘失败只 WARN，绝不遮蔽判据 verdict
        print('[M3-SMOKE][WARN] 取证 JSON 落盘失败（不影响 S1-S7 判据）: %r' % (exc,))
        return None


def _build_summary(strategy_id, runner, result, kinds,
                   s3_bad, s4_bad, s5_bad, s6_bad):
    """该策略结构化摘要（断言失败信息必备面，供归因；error 原样含入）。"""
    orders = result.get('orders') or []
    nav = result.get('nav_series') or []
    pos_series = result.get('positions_series') or []
    buy_n = sum(1 for o in orders if o.get('opType') == 23)
    sell_n = sum(1 for o in orders if o.get('opType') == 24)
    barpos_seq = [o.get('barpos') for o in orders]
    lim = _VIOL_SAMPLE_LIMIT
    lines = []
    add = lines.append
    add('strategy_id = %s' % strategy_id)
    add('product     = %s' % runner.product_path)
    add('drive       = n_bars=%d warmup_bars=%d init_cash=%.1f'
        % (runner.n_bars, runner.warmup_bars, runner.init_cash))
    add('S2 节拍     = init_calls=%r bars_run=%r（期望 1 / %d）'
        % (result.get('init_calls'), result.get('bars_run'), runner.n_bars))
    add('S3 订单     = 总数=%d 买(23)=%d 卖(24)=%d 违规=%d'
        % (len(orders), buy_n, sell_n, len(s3_bad)))
    if orders:
        add('    barpos 序列 前%d=%r 后%d=%r'
            % (lim, barpos_seq[:lim], lim, barpos_seq[-lim:]))
    if s3_bad:
        add('    S3 违规样本(前%d/%d)=%r' % (lim, len(s3_bad), s3_bad[:lim]))
    if s4_bad:
        add('    S4 违规样本(前%d/%d)=%r' % (lim, len(s4_bad), s4_bad[:lim]))
    add('S5 持仓     = 序列长=%d 末档=%s'
        % (len(pos_series),
           _fmt_pos_snapshot(pos_series[-1]) if pos_series else '(无任何 bar 快照)'))
    if s5_bad:
        add('    S5 违规样本(前%d/%d)=%r' % (lim, len(s5_bad), s5_bad[:lim]))
    add('S6 净值     = len=%d min=%r max=%r 界=[%.1f, %.1f]'
        % (len(nav), min(nav) if nav else None, max(nav) if nav else None,
           0.1 * runner.init_cash, 10.0 * runner.init_cash))
    if s6_bad:
        add('    S6 违规样本(前%d/%d)=%r' % (lim, len(s6_bad), s6_bad[:lim]))
    audit_lines = result.get('audit_lines') or []
    add('S7 审计行   = 总数=%d 种类=%r' % (len(audit_lines), kinds))
    for ln in audit_lines[:5]:
        add('    %s' % ln)
    error = result.get('error')
    if error:
        add('S1 error    = 非空，traceback 原样如下（禁筛选/简化）')
        add('---- S1 traceback 原样 开始 ----')
        add(error)
        add('---- S1 traceback 原样 结束 ----')
    else:
        add('S1 error    = (无)')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 测试主体
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('strategy_id', STRATEGY_IDS, ids=STRATEGY_IDS)
def test_qmt_stub_smoke_s1_s7(strategy_id):
    """六策略桩冒烟：run() 一次 → 取证 JSON 落盘 → S1-S7 全评估 → 任一红即 FAIL。

    桩冒烟仅证「产物可被驱动 + 序列形状自洽」，不证数值正确（数值对照归 M5）。
    """
    path = _product_path(strategy_id)
    if not os.path.isfile(path):
        pytest.skip('QMT 产物缺失（output/ 本地工件域，skip 不 fail）: %s'
                    % os.path.abspath(path))

    runner = QmtStubRunner(path, n_bars=N_BARS, init_cash=INIT_CASH)
    result = runner.run()  # 异常捕获不抛：'error' 存 traceback 字符串（S1 断言面）

    # ---- 判据评估（全部先评估后断言：任一判据红不遮蔽其余判据的红）------------
    reds = []

    # S1 无未捕获异常（红则原样打印 traceback——禁筛选/简化；print 使其进入
    # pytest 捕获输出，断言信息内亦原样含入，双通道保真）
    error = result.get('error')
    if error:
        print('[M3-SMOKE][S1-RED] 策略=%s —— traceback 原样:\n%s'
              % (strategy_id, error))
        reds.append('[S1 无未捕获异常] run() error 非空——traceback 原样如下'
                    '（禁筛选/简化）:\n%s' % error)

    # S2 驱动节拍：init ×1、handlebar ×n_bars
    if (result.get('init_calls') != 1
            or result.get('bars_run') != runner.n_bars):
        reds.append('[S2 驱动节拍] init_calls=%r（期望 1）、bars_run=%r（期望 %d）'
                    % (result.get('init_calls'), result.get('bars_run'),
                       runner.n_bars))

    # S3 订单方向合理（opType ∈ {23,24}；volume > 0）
    orders = result.get('orders') or []
    s3_bad = _check_s3_orders(orders)
    buy_n = sum(1 for o in orders if o.get('opType') == 23)
    sell_n = sum(1 for o in orders if o.get('opType') == 24)
    if s3_bad:
        reds.append('[S3 订单方向合理] 违规 %d 笔（样本前 %d）: %r'
                    % (len(s3_bad), _VIOL_SAMPLE_LIMIT,
                       s3_bad[:_VIOL_SAMPLE_LIMIT]))

    # S4 时点单调（barpos 非递减）
    s4_bad = _check_s4_monotonic(orders)
    if s4_bad:
        reds.append('[S4 时点单调] barpos 递减违规 %d 处（样本前 %d）: %r'
                    % (len(s4_bad), _VIOL_SAMPLE_LIMIT,
                       s4_bad[:_VIOL_SAMPLE_LIMIT]))

    # S5 持仓非负（amount >= 0；enable_amount ∈ [0, amount]）
    pos_series = result.get('positions_series') or []
    s5_bad = _check_s5_positions(pos_series)
    if not pos_series:
        reds.append('[S5 持仓非负] positions_series 为空（无任何 bar 快照可判；'
                    'bar 推进时逐根落档，仅驱动早期异常时出现）')
    elif s5_bad:
        reds.append('[S5 持仓非负] 违规 %d 处（样本前 %d）: %r'
                    % (len(s5_bad), _VIOL_SAMPLE_LIMIT,
                       s5_bad[:_VIOL_SAMPLE_LIMIT]))

    # S6 净值量级（> 0 且 ∈ [0.1×, 10×]init_cash）
    nav = result.get('nav_series') or []
    s6_bad = _check_s6_nav(nav, runner.init_cash)
    if s6_bad:
        reds.append('[S6 净值量级] 违规 %d 项（界 [%.1f, %.1f]，样本前 %d）: %r'
                    % (len(s6_bad), 0.1 * runner.init_cash,
                       10.0 * runner.init_cash, _VIOL_SAMPLE_LIMIT,
                       s6_bad[:_VIOL_SAMPLE_LIMIT]))

    # S7 审计行在位（≥1 条；逐策略打印捕获到的种类）
    audit_lines = result.get('audit_lines') or []
    kinds = _audit_kinds(audit_lines)
    if not audit_lines:
        reds.append('[S7 审计行在位] 未捕获任何 QS_QMT_* 审计行（期望 ≥1 条）')

    # ---- 取证 JSON 落盘（断言之前：FAIL 时结构化结果同样在位供归因）------------
    dump_path = _dump_smoke_json(strategy_id, runner, result, kinds, s3_bad)

    # ---- 逐策略信息打印（pytest 捕获：-s / -rA / 失败回显时可见）----------------
    print('[M3-SMOKE] 策略=%s' % strategy_id)
    print('[M3-SMOKE] 产物=%s' % path)
    print('[M3-SMOKE] S2: init_calls=%r bars_run=%r/%d'
          % (result.get('init_calls'), result.get('bars_run'), runner.n_bars))
    print('[M3-SMOKE] S3: 订单总数=%d 买(23)=%d 卖(24)=%d'
          % (len(orders), buy_n, sell_n))
    print('[M3-SMOKE] S6: nav_len=%d nav_min=%r nav_max=%r'
          % (len(nav), min(nav) if nav else None, max(nav) if nav else None))
    print('[M3-SMOKE] S7: 审计行=%d 捕获种类=%r' % (len(audit_lines), kinds))
    print('[M3-SMOKE] 取证 JSON=%s' % (dump_path or '(落盘失败，见上方 WARN)'))

    # ---- 断言（任一红即 FAIL；失败信息含该策略结构化摘要，供归因）----------------
    summary = _build_summary(strategy_id, runner, result, kinds,
                             s3_bad, s4_bad, s5_bad, s6_bad)
    msg_parts = [
        'M3 桩冒烟形状判据 FAIL: strategy_id=%s' % strategy_id,
        '（plan §⑥：S1-S7 任一红即 BLOCK；桩冒烟仅证「产物可被驱动 + 序列形状'
        '自洽」，不证数值正确——数值对照归 M5 真实环境）',
        '---- 红判据明细（共 %d 项）----' % len(reds),
    ]
    msg_parts.extend(reds)
    msg_parts.append('---- 结构化摘要（供归因）----')
    msg_parts.append(summary)
    msg_parts.append('---- 取证 ----')
    msg_parts.append(('JSON: %s' % dump_path) if dump_path
                     else 'JSON: 落盘失败（见上方 WARN 打印）')
    assert not reds, '\n'.join(msg_parts)
