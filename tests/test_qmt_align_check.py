# -*- coding: utf-8 -*-
"""test_qmt_align_check.py —— M5.2 件2 对齐执行器测试（任务包判据 1-5 + 异常分支）。

全部用构造样本验证（任务包第 3 节：测试自构造最小样本，不依赖真实库）：
- 判据1：JSONL 每行恰含 {date, event, code, qty, price, raw}，raw=原行文本；
- 判据2：五维对照逐项 in_tol/out_tol，容差表可配（缺省价差 0.1%、数量差 0）；
- 判据3：归因三分 category ∈ {data_source, timing, fees, other}，other 非零 exit 1；
- 判据4：纯观测——不 import 引擎模块（AST 静态核验），仅写 --out 两产物；
- 判据5：--help 正常；空输入/格式坏行明确报错不崩溃（坏行计数入报告 skipped）；
- 异常分支：日志不存在 → exit 2 中文报错；轨迹 schema 不符 → exit 2 指明缺字段。
"""
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'qmt_align_check.py'

# 以文件路径加载被测模块（脚本独立于包结构，且判据4 禁 import 引擎模块）
_spec = importlib.util.spec_from_file_location('qmt_align_check_under_test', SCRIPT)
MOD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MOD)

DIM_NAMES = ('rebalance_dates', 'order_side_qty', 'price', 'position_qty', 'fees')


def run_align(tmp_path, log_text, trades, extra_args=(), log_name='qmt.log'):
    """写构造输入 → 调 main() → 返回 (rc, report, events)；报错路径下产物为 None/[]。"""
    log_path = tmp_path / log_name
    trades_path = tmp_path / 'trades.json'
    out_dir = tmp_path / 'out'
    if log_text is not None:
        log_path.write_text(log_text, encoding='utf-8')
    if trades is not None:
        trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding='utf-8')
    rc = MOD.main(['--qmt-log', str(log_path), '--local-trades', str(trades_path),
                   '--out', str(out_dir), *extra_args])
    report_path = out_dir / 'align_report.json'
    events_path = out_dir / 'qmt_events.jsonl'
    report = (json.loads(report_path.read_text(encoding='utf-8'))
              if report_path.exists() else None)
    events = ([json.loads(x) for x in events_path.read_text(encoding='utf-8').splitlines()]
              if events_path.exists() else [])
    return rc, report, events


# ---- 判据1：JSONL 六键 + 事件提取 + 非审计行剔除 ------------------------------

def test_jsonl_six_keys_and_event_extraction(tmp_path):
    lines = [
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=1000 price=10.50 fee=5.25",
        "[QS-QMT log.warning] QS_ORDER_AUDIT date=20260105 code=000001.SZ op=24 qty=500 price=11.00",
        "[QS-QMT log.info] 无标记普通日志行 hello",
        "QS_QMT_SEMANTIC_DIFF: get_Ashares date='20260105' 不消费（示例上下文行）",
        "普通平台输出行（非审计，不入 JSONL）",
    ]
    trades = [{'date': '20260105', 'code': '600000.SH', 'side': 'buy',
               'qty': 1000, 'price': 10.50},
              {'date': '20260105', 'code': '000001.SZ', 'side': 'sell',
               'qty': 500, 'price': 11.00}]
    rc, report, events = run_align(tmp_path, '\n'.join(lines) + '\n', trades)
    assert rc == 0
    assert len(events) == 4                       # 非审计行被剔除
    for ev in events:
        assert set(ev.keys()) == {'date', 'event', 'code', 'qty', 'price', 'raw'}
    assert events[0]['raw'] == lines[0]           # raw=原行文本
    assert events[0]['event'] == 'QS_ORDER_AUDIT'  # log 体首全大写标记
    assert events[0]['date'] == '20260105' and events[0]['qty'] == 1000.0
    assert events[0]['price'] == 10.50 and events[0]['code'] == '600000.SH'
    assert events[1]['event'] == 'QS_ORDER_AUDIT'
    assert events[2]['event'] == 'log.info'       # 无标记 → log.<level>
    assert events[2]['code'] is None
    assert events[3]['event'] == 'QS_QMT_SEMANTIC_DIFF'  # 独立前缀行
    assert events[3]['date'] == '20260105'        # 引号值剥离后归一
    assert report['parse_summary']['non_audit_lines'] == 1


# ---- 判据2：五维全一致（含 op=24 定向 / qty 负号定向 / 费用缺失 na）------------

def test_all_in_tol_pass(tmp_path):
    log = "\n".join([
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=1000 price=10.50 fee=5.25",
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=000001.SZ op=24 qty=500 price=11.00 fee=2.75",
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=000002.SZ qty=-400 price=6.00",
    ]) + "\n"
    # dict + trades 键包装形态亦受支持；000002 双侧无 fee → fees 维度记 na
    trades = {'trades': [
        {'date': '2026-01-05', 'code': '600000.SH', 'side': 'buy',
         'qty': 1000, 'price': 10.51, 'amount': 10510.0, 'fee': 5.25},
        {'date': '2026-01-05', 'code': '000001.SZ', 'side': 'sell',
         'qty': 500, 'price': 11.00, 'amount': 5500.0, 'fee': 2.75},
        {'date': '2026/01/05', 'code': '000002.SZ', 'side': 'sell',
         'qty': 400, 'price': 6.00, 'amount': 2400.0},
    ]}
    rc, report, events = run_align(tmp_path, log, trades)
    assert rc == 0
    assert set(report['dimensions'].keys()) == set(DIM_NAMES)
    for name in DIM_NAMES:
        d = report['dimensions'][name]
        assert d['out_tol'] == 0 and d['status'] == 'pass', (name, d)
    assert report['dimensions']['price']['in_tol'] == 3
    assert report['dimensions']['position_qty']['in_tol'] == 3
    assert report['dimensions']['fees']['na'] == 1      # 费用信息缺失 → na 非 out_tol
    assert report['attribution'] == {'data_source': 0, 'timing': 0, 'fees': 0, 'other': 0}
    assert report['local_trades_count'] == 3
    assert events[2]['qty'] == -400.0                   # JSONL 保留原始解析值


# ---- 判据2+3：out_tol 逐维差异 + 归因三分 + other → exit 1 --------------------

def test_out_tol_attribution_and_exit1(tmp_path):
    log = "\n".join([
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=1000 price=10.80 fee=5.25",
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260119 code=600519.SH side=buy qty=100",
    ]) + "\n"
    trades = [
        {'date': '20260105', 'code': '600000.SH', 'side': 'buy',
         'qty': 1000, 'price': 10.51, 'fee': 9.99},          # 价差>0.1% + 费差
        {'date': '20260112', 'code': '000001.SZ', 'side': 'sell',
         'qty': 500, 'price': 11.00, 'fee': 2.75},           # 本地独有调仓日
        {'date': '20260119', 'code': '600519.SH', 'side': 'sell',
         'qty': 100, 'price': 1700.00},                      # 方向矛盾 buy vs sell
    ]
    rc, report, _ = run_align(tmp_path, log, trades)
    assert rc == 1                                       # other 非零 → exit 1（判据3）
    assert report['exit_code'] == 1
    # 归因：价/量/持仓差=data_source；日集差=timing；费差=fees；方向矛盾=other
    assert report['attribution'] == {'data_source': 4, 'timing': 1, 'fees': 1, 'other': 1}
    assert len(report['other_detail']) == 1
    assert report['other_detail'][0]['kind'] == 'side_conflict'
    assert report['other_detail'][0]['qmt_side'] == 'buy'
    assert report['other_detail'][0]['local_side'] == 'sell'
    cats = {i['category'] for d in report['dimensions'].values() for i in d['items']
            if i['in_tol'] is False}
    assert cats == {'data_source', 'timing', 'fees', 'other'}
    assert {i['which'] for i in report['dimensions']['rebalance_dates']['items']} == {'local_only'}


# ---- 判据2：容差表可配（--tol-price 放宽容差后 out_tol → in_tol）---------------

def test_tolerance_configurable(tmp_path):
    log = ("[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH "
           "side=buy qty=1000 price=10.60\n")
    trades = [{'date': '20260105', 'code': '600000.SH', 'side': 'buy',
               'qty': 1000, 'price': 10.50}]
    rc1, rep1, _ = run_align(tmp_path, log, trades)
    assert rc1 == 0
    assert rep1['tolerances'] == {'price_rel': 0.001, 'qty_abs': 0, 'fee_abs': 0.0}
    assert rep1['dimensions']['price']['out_tol'] == 1     # 缺省 0.1% 判超差
    rc2, rep2, _ = run_align(tmp_path, log, trades, extra_args=['--tol-price', '0.05'])
    assert rc2 == 0
    assert rep2['tolerances']['price_rel'] == 0.05
    assert rep2['dimensions']['price']['out_tol'] == 0     # 放宽后 in_tol
    assert rep2['dimensions']['price']['in_tol'] == 1


# ---- 判据5：格式坏行 → skipped 计数入报告，不崩溃 -----------------------------

def test_bad_lines_skipped(tmp_path):
    log = "\n".join([
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH qty=abc price=10.5",   # qty 非数
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20261399 code=600000.SH qty=100 price=10.5",   # 非法日期
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=maybe qty=100 price=10.5",  # side 不可识别
        "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5",    # 好行
    ]) + "\n"
    trades = [{'date': '20260105', 'code': '600000.SH', 'side': 'buy',
               'qty': 100, 'price': 10.5}]
    rc, report, events = run_align(tmp_path, log, trades)
    assert rc == 0                                       # 坏行不崩溃，好行照常对照
    ps = report['parse_summary']
    assert ps['skipped_bad_lines'] == 3 and ps['skipped'] == 3   # 判据5 skipped 计数
    assert [d['line_no'] for d in ps['skipped_detail']] == [1, 2, 3]
    assert any('abc' in d['reason'] for d in ps['skipped_detail'])
    assert any(d['reason'].startswith('date 无法解析') for d in ps['skipped_detail'])
    assert any('side 无法识别' in d['reason'] for d in ps['skipped_detail'])
    assert ps['parsed_events'] == 1 and len(events) == 1  # 坏行不入 JSONL


# ---- 判据5 + 异常分支：空输入 / 文件不存在 / schema 不符 → exit 2 中文报错 -----

def test_empty_log_file(tmp_path, capsys):
    rc, report, _ = run_align(tmp_path, '', [{'date': '20260105', 'code': 'X',
                                              'side': 'buy', 'qty': 1, 'price': 1.0}])
    assert rc == 2 and report is None
    assert '空' in capsys.readouterr().err


def test_log_without_any_audit_line(tmp_path, capsys):
    rc, report, _ = run_align(tmp_path, '普通输出\n另一行\n',
                              [{'date': '20260105', 'code': 'X', 'side': 'buy',
                                'qty': 1, 'price': 1.0}])
    assert rc == 2
    assert '未解析到任何审计事件行' in capsys.readouterr().err


def test_empty_local_trades_list(tmp_path, capsys):
    log = "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5\n"
    rc, _, _ = run_align(tmp_path, log, [])
    assert rc == 2
    assert '空列表' in capsys.readouterr().err


def test_qmt_log_not_found(tmp_path, capsys):
    rc, _, _ = run_align(tmp_path, None, [{'date': '20260105', 'code': 'X',
                                           'side': 'buy', 'qty': 1, 'price': 1.0}])
    assert rc == 2
    err = capsys.readouterr().err
    assert '不存在' in err and 'QMT 日志' in err   # 中文报错（异常分支）


def test_local_trades_schema_missing_field(tmp_path, capsys):
    log = "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5\n"
    bad = [{'date': '20260105', 'code': '600000.SH', 'side': 'buy', 'qty': 100}]  # 缺 price
    rc, _, _ = run_align(tmp_path, log, bad)
    assert rc == 2
    err = capsys.readouterr().err
    assert 'schema 不符' in err and '缺少字段' in err and 'price' in err  # 指明缺字段


def test_local_trades_invalid_json(tmp_path, capsys):
    log = "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5\n"
    log_path = tmp_path / 'qmt.log'
    log_path.write_text(log, encoding='utf-8')
    trades_path = tmp_path / 'trades.json'
    trades_path.write_text('{not-json', encoding='utf-8')
    rc = MOD.main(['--qmt-log', str(log_path), '--local-trades', str(trades_path),
                   '--out', str(tmp_path / 'out')])
    assert rc == 2
    assert 'JSON 解析失败' in capsys.readouterr().err


def test_local_trades_bad_side_and_date(tmp_path, capsys):
    log = "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5\n"
    base = {'date': '20260105', 'code': '600000.SH', 'qty': 100, 'price': 10.5}
    rc, _, _ = run_align(tmp_path, log, [{**base, 'side': 'hold'}])
    assert rc == 2 and 'side 无法识别' in capsys.readouterr().err
    capsys.readouterr()
    rc, _, _ = run_align(tmp_path, log, [{**base, 'side': 'buy', 'date': '20261399'}])
    assert rc == 2 and 'date 无法解析' in capsys.readouterr().err


def test_local_trades_wrong_top_level(tmp_path, capsys):
    log = "[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH side=buy qty=100 price=10.5\n"
    rc, _, _ = run_align(tmp_path, log, {'records': []})
    assert rc == 2
    assert '顶层' in capsys.readouterr().err


# ---- 判据4：纯观测——不 import 引擎模块；--out 仅两产物；头部禁令在位 ----------

def test_pure_observer_no_engine_import():
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {(node.module or '').split('.')[0]}
        else:
            continue
        assert 'quantstudio' not in roots, "判据4 禁 import 引擎模块"


def test_out_dir_contains_only_two_artifacts(tmp_path):
    log = ("[QS-QMT log.info] QS_ORDER_AUDIT date=20260105 code=600000.SH "
           "side=buy qty=100 price=10.5\n")
    trades = [{'date': '20260105', 'code': '600000.SH', 'side': 'buy',
               'qty': 100, 'price': 10.5}]
    rc, _, _ = run_align(tmp_path, log, trades)
    assert rc == 0
    assert sorted(p.name for p in (tmp_path / 'out').iterdir()) == \
        ['align_report.json', 'qmt_events.jsonl']


def test_header_prohibitions_present():
    """任务包第 6 节三条禁令必须写入脚本头部注释。"""
    head = '\n'.join(SCRIPT.read_text(encoding='utf-8').splitlines()[:12])
    assert '只允许写任务包第 2 节清单内两个文件' in head
    assert '禁止脑补格式' in head
    assert '禁动 quantstudio' in head and '禁改契约' in head and '禁顺手重构' in head


# ---- 判据5：--help 冒烟 -------------------------------------------------------

def test_help_smoke():
    proc = subprocess.run([sys.executable, str(SCRIPT), '--help'],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert proc.returncode == 0
    assert '--qmt-log' in proc.stdout and '--tol-price' in proc.stdout


# ---- 订单审计行（格式 A/B，docs/qmt-order-audit-line-design-20261104.md §三）----
# 格式 A：[QS-QMT log.info] QS_QMT_ORDER date=%s code=%s op=%d qty=%d price=%.4f
#         prType=%d（_qs_passorder 审计行，进五维对照；op=23→buy/24→sell）
# 格式 B：[QS-QMT log.info] QS_QMT_ORDER_TARGET_NATIVE date=%s code=%s
#         target_value=%.2f（原生转发观测行，无 qty ⇒ 非订单型，不进五维）
# date 空值（timetag 不可得 fail-safe 降级 ⇒ date= 渲染空）⇒ TOKEN_RE 不捕获
# date ⇒ excluded_no_date 排除（非坏行）——2026-11-04 续批裁定口径。

def test_format_a_order_line_parsed(tmp_path):
    """V1①：格式 A 解析为订单事件——op=23→buy/24→sell、bare 码、qty/price 提取；
    date 空值行 → excluded_no_date（非坏行，fail-safe 降级裁定口径）。"""
    lines = [
        "[QS-QMT log.info] QS_QMT_ORDER date=2026-06-01 code=600000.SH op=23 qty=1000 price=10.5000 prType=5",
        "[QS-QMT log.info] QS_QMT_ORDER date=20260602 code=000001.SZ op=24 qty=500 price=11.0000 prType=5",
        "[QS-QMT log.info] QS_QMT_ORDER date= code=600519.SH op=23 qty=100 price=0.0000 prType=5",
    ]
    trades = [
        {'date': '20260601', 'code': '600000.SH', 'side': 'buy', 'qty': 1000, 'price': 10.50},
        {'date': '20260602', 'code': '000001.SZ', 'side': 'sell', 'qty': 500, 'price': 11.00},
    ]
    # 解析层直判（JSONL 六键不含 side）：op 定向 + date 两种形态归一 + 空值降级
    _, p0 = MOD.parse_qmt_log_line(lines[0])
    _, p1 = MOD.parse_qmt_log_line(lines[1])
    _, p2 = MOD.parse_qmt_log_line(lines[2])
    assert p0['event'] == 'QS_QMT_ORDER' and p0['side'] == 'buy' and p0['date'] == '20260601'
    assert p1['side'] == 'sell' and p1['date'] == '20260602'
    assert p2['side'] == 'buy' and p2['date'] is None and p2['price'] == 0.0
    rc, report, events = run_align(tmp_path, '\n'.join(lines) + '\n', trades)
    assert rc == 0
    ps = report['parse_summary']
    assert ps['skipped_bad_lines'] == 0          # date 空值不是坏行
    assert ps['excluded_no_date'] == 1           # 订单型缺 date → 计数排除
    assert events[0]['code'] == '600000.SH' and events[0]['qty'] == 1000.0
    assert events[0]['price'] == 10.5            # %.4f 词法提取
    for name in DIM_NAMES:
        d = report['dimensions'][name]
        assert d['out_tol'] == 0, (name, d)      # 空值行不入对照 ⇒ 五维不受污染


def test_format_b_not_in_five_dims(tmp_path):
    """V1②：格式 B 行（原生转发观测，无 qty）不进五维——仅入 JSONL。"""
    log = "\n".join([
        "[QS-QMT log.info] QS_QMT_ORDER date=20260601 code=600000.SH op=23 qty=1000 price=10.5000 prType=5",
        "[QS-QMT log.info] QS_QMT_ORDER_TARGET_NATIVE date=20260601 code=600000.SH target_value=99999.00",
    ]) + "\n"
    trades = [{'date': '20260601', 'code': '600000.SH', 'side': 'buy',
               'qty': 1000, 'price': 10.50}]
    rc, report, events = run_align(tmp_path, log, trades)
    assert rc == 0
    assert len(events) == 2 and events[1]['event'] == 'QS_QMT_ORDER_TARGET_NATIVE'
    assert events[1]['qty'] is None and events[1]['date'] == '20260601'
    ps = report['parse_summary']
    assert ps['skipped_bad_lines'] == 0 and ps['excluded_no_date'] == 0
    # B 无 qty → 非订单型 → 五维仅由 A 行驱动且全 in_tol
    for name in DIM_NAMES:
        d = report['dimensions'][name]
        assert d['out_tol'] == 0, (name, d)
    assert report['dimensions']['order_side_qty']['in_tol'] == 1


def test_format_a_b_mixed_no_substring_swallow(tmp_path):
    """V1③（v1.1 混合负向）：A/B 同流时 A 计数不含 B——事件名全 token 比对，
    防 QS_QMT_ORDER 子串误吞 QS_QMT_ORDER_TARGET_NATIVE（qty 聚合污染）。"""
    log = "\n".join([
        "[QS-QMT log.info] QS_QMT_ORDER date=20260601 code=600000.SH op=23 qty=1000 price=10.5000 prType=5",
        "[QS-QMT log.info] QS_QMT_ORDER date=20260601 code=600000.SH op=23 qty=500 price=10.5000 prType=5",
        "[QS-QMT log.info] QS_QMT_ORDER_TARGET_NATIVE date=20260601 code=600000.SH target_value=15000.00",
    ]) + "\n"
    # 本地恰为两笔 A 单（1000+500）：若 B 被误吞为订单事件（名字层已分立），
    # 事件名断言即红；聚合面 1500 vs 1500 须 in_tol。
    trades = [
        {'date': '20260601', 'code': '600000.SH', 'side': 'buy', 'qty': 1000, 'price': 10.50},
        {'date': '20260601', 'code': '600000.SH', 'side': 'buy', 'qty': 500, 'price': 10.50},
    ]
    rc, report, events = run_align(tmp_path, log, trades)
    assert rc == 0
    assert [e['event'] for e in events] == [
        'QS_QMT_ORDER', 'QS_QMT_ORDER', 'QS_QMT_ORDER_TARGET_NATIVE']
    assert report['dimensions']['order_side_qty']['out_tol'] == 0
    assert report['dimensions']['order_side_qty']['in_tol'] == 1  # 1000+500 聚合 in_tol
