# -*- coding: utf-8 -*-
"""客户侧 QFQ 出厂自检（E3 · D5①）—— 随交付包交付，客户环境独立运行。

为什么客户要自己跑一遍
----------------------
交付包的复权还原依赖**两个独立源**：包内 aux 库（分子：当日 adj_factor）＋
包内主库/云端（分母：latest adj_factor）。两侧不同锚 ⇒ 价格静默错配，且**不报错**。
本脚本把「能不能安全使用复权价」变成一条可执行判据：任一项 FAIL 即**阻断启用**，
不靠客户读文档、也不靠我方口头承诺。

检查项
------
  V1 主库可读
  V2 released ↔ aux 路由自洽（released=true 却落到 legacy 路径、或指向包外 ⇒ FAIL）
  V3 因子库存在且核心表有数（adj_factor / fund_adj）
  V4 因子覆盖 ≥ 最近交易日（aux 快照 max(date) 与主库最近交易日比；落后 > 容差 ⇒ FAIL）
     · 判据引用 **⑱《D1/D4 消费侧判据》**（`QuantStudio-trading/docs/aq-dw-d1d4-consumer-criteria.md`）：
       缺失判定＝`max(源表日期) < T`；**`local_max > T`（超前）不判缺失**——消费端已按 `<= T` 取数。
  V5 抽样还原对账（读内置/随包基准样本；**样本缺失 ⇒ SKIP 不计 FAIL**，但会显式提示）
  V6 记录 aux_db_path 可解析（非异平台绝对路径、文件存在）
  V7 包内 `qfq_aux_paths.json` 在位（⑪ R-D 缺口：缺件时客户侧静默 fail-secure 回 legacy，
     我方却在文档里声称已配置 ⇒ 必须显式判 FAIL 而非静默降级）

用法（客户侧）
--------------
    python verify_qfq.py --data-root <数据目录>
    python verify_qfq.py --data-root <数据目录> --json > qfq_selfcheck.json
    python verify_qfq.py --data-root <数据目录> --baseline qfq_baseline_samples.json

退出码：0=全部 PASS 或仅有 SKIP/WARN（可用）；1=存在 FAIL（**不得启用**）。
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys

MAIN_DB = 'quantstudio.db'
AUX_PATHS = 'qfq_aux_paths.json'
LEGACY_AUX = 'qfq_aux.db'
CORE_TABLES = ('adj_factor', 'fund_adj')


def _looks_foreign_abs(raw):
    s = str(raw)
    if os.name == 'nt':
        return bool(re.match(r'^/', s)) and not re.match(r'^[A-Za-z]:', s)
    return bool(re.search(r'[A-Za-z]:[\\/]', s))


def _ms_to_date(v):
    """aux 的 time 列是**整数毫秒**（实测 1,793,948,400,000 = 2026-11-06）；
    也有测试/历史库用 TEXT 日期。两种都要能转，否则 `str(v)[:10]` 会得到 '1793948400'。"""
    if v is None:
        return None
    try:
        n = int(v)
        sec = n / 1000.0 if n > 10 ** 11 else n
        return dt.datetime.fromtimestamp(sec).strftime('%Y-%m-%d')
    except Exception:
        return str(v)[:10]


def _sqlite_max_date(path, table, exclude_future_days=7):
    """返回 (行数, max(time) 日期, err)。**远期哨兵（> now+N 天）先行排除**。

    2026-11-08 裁（哨兵处置甲）：辅助库可能存在 `time` 哨兵值（形态如 9997920000，
    按毫秒≈1970 / 按秒≈2286 年），一旦参与 `max()` 会把覆盖日期顶到远期，
    与「超前≠缺失」判据叠加后**反而掩盖真实缺失**。故一律先按 `time <= now+Nd` 截断再取 max。
    """
    try:
        cutoff_ms = int((dt.datetime.now() + dt.timedelta(days=exclude_future_days))
                        .timestamp() * 1000)
        con = sqlite3.connect('file:%s?mode=ro' % str(path).replace('\\', '/'),
                              uri=True, timeout=15)
        try:
            n = int(con.execute('SELECT count(*) FROM "%s"' % table).fetchone()[0])
            # 列声明类型决定是否用「毫秒截断」语义：TEXT 日期列直接取 max（无哨兵语义、
            # 也不能与整数 cutoff 比较，否则会得到 None 而误判缺失）。
            decl = ''
            for r in con.execute('PRAGMA table_info("%s")' % table):
                if str(r[1]).lower() == 'time':
                    decl = str(r[2] or '')
                    break
            if 'INT' in decl.upper() or decl == '':
                mx = con.execute('SELECT max(time) FROM "%s" WHERE time <= ?' % table,
                                 [cutoff_ms]).fetchone()[0]
                dropped = con.execute('SELECT count(*) FROM "%s" WHERE time > ?' % table,
                                      [cutoff_ms]).fetchone()[0]
            else:
                mx = con.execute('SELECT max(time) FROM "%s"' % table).fetchone()[0]
                dropped = 0
            return n, _ms_to_date(mx), None, \
                (None if not dropped else 'excluded_sentinel_rows=%d' % dropped)
        finally:
            con.close()
    except Exception as e:
        return None, None, str(e)[:160], None


def _main_recent_trade_date(main_db):
    """主库最近交易日期（毫秒级安全：失败返回 None，由调用方降级为 SKIP）。"""
    try:
        import duckdb
        con = duckdb.connect(str(main_db), read_only=True)
        try:
            r = con.execute("SELECT max(trade_time) FROM stock_minutes").fetchone()
            return str(r[0])[:10] if r and r[0] is not None else None
        finally:
            con.close()
    except Exception:
        return None


def _load_released(data_root):
    p = os.path.join(data_root, AUX_PATHS)
    if not os.path.isfile(p):
        return False, None, '缺 %s（客户端将 fail-secure 走 legacy）' % AUX_PATHS
    try:
        doc = json.load(open(p, encoding='utf-8'))
        return bool(doc.get('released', False)), doc, None
    except Exception as e:
        return False, None, '配置解析失败（按 false 处理）：%s' % str(e)[:120]


def run(data_root, baseline=None, max_lag_days=3, price_source='mcp'):
    root = os.path.abspath(data_root)
    rep = {'checked_at': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'data_root': root, 'price_source': price_source,
           'verdict': 'PASS', 'checks': []}

    def add(code, level, msg, ev=None):
        rep['checks'].append({'code': code, 'level': level, 'msg': msg,
                              **({'evidence': ev} if ev else {})})
        if level == 'FAIL':
            rep['verdict'] = 'FAIL'

    # V1
    main_db = os.path.join(root, MAIN_DB)
    if not os.path.isfile(main_db):
        add('V1', 'FAIL', '数据目录内缺主库 %s' % MAIN_DB)
        return rep
    add('V1', 'PASS', '主库存在', {'size_mb': round(os.path.getsize(main_db) / 2 ** 20, 1)})

    # V2 路由自洽
    released, doc, note = _load_released(root)
    legacy = os.path.join(root, LEGACY_AUX)
    route_path, route_reason = None, ''
    gens = (doc or {}).get('generations') or {}
    if released and gens:
        gen_name, raw = sorted(gens.items())[0]
        route_reason = 'gen1:%s' % gen_name
        cand = raw if os.path.isabs(str(raw)) else os.path.join(root, str(raw))
        route_path = os.path.abspath(cand)
    else:
        route_reason = 'legacy:released=false' if not released else 'legacy:no_generations'
        route_path = os.path.abspath(legacy)
    if released and route_path == os.path.abspath(legacy):
        add('V2', 'FAIL', 'released=true 但路由落到 legacy 路径（配置不完整，客户以为在用世代库）',
            {'reason': route_reason})
    elif route_path and os.path.dirname(route_path) != root:
        add('V2', 'FAIL', '路由指向数据目录之外：%s（跨目录/异平台风险）' % route_path,
            {'reason': route_reason})
    else:
        add('V2', 'PASS', '路由自洽：%s ⇒ %s' % (route_reason, os.path.basename(route_path or '')),
            {'reason': route_reason, 'route': route_path, 'note': note})

    # V3/V4 因子库与覆盖
    aux = route_path or legacy
    if not os.path.isfile(aux):
        add('V3', 'FAIL', '因子库文件不存在：%s（复权将 fail-closed）' % os.path.basename(aux))
        add('V4', 'FAIL', '因子库缺失 ⇒ 覆盖无法满足')
    else:
        rows, mx_adj, err, _note = _sqlite_max_date(aux, 'adj_factor')
        if err:
            add('V3', 'FAIL', '因子库不可读：%s' % err)
            add('V4', 'FAIL', '因子库不可读 ⇒ 覆盖无法判定')
        else:
            n_fund, mx_fund, _e2, _n2 = _sqlite_max_date(aux, 'fund_adj')
            empty = [t for t, n in (('adj_factor', rows), ('fund_adj', n_fund or 0)) if not n]
            ev = {'path': os.path.basename(aux), 'adj_factor_rows': rows,
                  'fund_adj_rows': n_fund, 'max_adj_date': mx_adj, 'max_fund_date': mx_fund}
            if empty:
                add('V3', 'FAIL', '因子核心表为空 %s（空库雷：复权会静默退化）' % empty, ev)
            else:
                add('V3', 'PASS', '因子核心表有数', ev)
            recent = _main_recent_trade_date(main_db)
            if recent is None:
                add('V4', 'SKIP', '主库最近交易日不可判定（客户库结构差异）；请人工核对因子日期',
                    {'max_adj_date': mx_adj, 'max_fund_date': mx_fund})
            else:
                if mx_adj and mx_adj > recent:
                    # D1 判据 + D5 存续约束：超前≠缺失（消费端按 <= T 取数）
                    add('V4', 'PASS', '因子覆盖 %s 超前于最近交易日 %s（判据 D1：超前≠缺失）'
                        % (mx_adj, recent),
                        {'max_adj_date': mx_adj, 'recent_trade_date': recent, 'ahead': True})
                else:
                    lag = (dt.date.fromisoformat(recent) - dt.date.fromisoformat(mx_adj)).days \
                        if mx_adj else 9999
                    ok = lag <= max_lag_days
                    add('V4', 'PASS' if ok else 'FAIL',
                        '因子覆盖 %s vs 最近交易日 %s（滞后 %d 天，容差 %d；判据 D1：max<T 即缺失）'
                        % (mx_adj, recent, lag, max_lag_days),
                        {'max_adj_date': mx_adj, 'max_fund_date': mx_fund,
                         'recent_trade_date': recent, 'lag_days': lag})

    # V5 抽样还原对账
    if not baseline:
        add('V5', 'SKIP', '未提供基准样本（--baseline）；抽样还原对账跳过——'
            '样本应由数据供给线 E1/E2 随包提供', {'baseline': None})
    elif not os.path.isfile(baseline):
        add('V5', 'SKIP', '基准样本文件不存在：%s（跳过，不判 FAIL）' % baseline)
    else:
        try:
            samples = json.load(open(baseline, encoding='utf-8'))
            samples = samples.get('samples', samples) if isinstance(samples, dict) else samples
            bad, checked = [], 0
            for s in samples:
                raw_p = float(s['raw_close']); adj = float(s['adj']); latest = float(s['latest'])
                exp = round(raw_p * (adj / latest), 3)
                got = s.get('expect_qfq_close')
                checked += 1
                if got is not None and abs(float(got) - exp) > 0.0015:
                    bad.append({'ts_code': s.get('ts_code'), 'day': s.get('day'),
                                'expect': exp, 'recorded': got})
            if bad:
                add('V5', 'FAIL', '抽样还原对账不一致 %d/%d' % (len(bad), checked), {'bad': bad[:10]})
            else:
                add('V5', 'PASS', '抽样还原对账一致（%d 条）' % checked, {'checked': checked})
        except Exception as e:
            add('V5', 'FAIL', '基准样本无法解析/对账异常：%s' % str(e)[:140])

    # V6 记录 aux_db_path 可解析（仅在能读主库时）
    try:
        import duckdb
        con = duckdb.connect(str(main_db), read_only=True)
        try:
            row = con.execute(
                "SELECT c.aux_db_path FROM qfq_active_cutover a JOIN qfq_source_cutover c "
                "ON c.cutover_id=a.cutover_id WHERE a.price_source=? LIMIT 1",
                [str(price_source)]).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            add('V6', 'SKIP', '主库无 active cutover 记录（legacy 路径即可用）')
        elif _looks_foreign_abs(row[0]):
            add('V6', 'FAIL', '记录 aux_db_path 为异平台绝对路径（客户侧会 fail-closed）：%s' % row[0])
        else:
            p = str(row[0])
            exists = os.path.isfile(p) or os.path.isfile(os.path.join(root, os.path.basename(p)))
            add('V6', 'PASS' if exists else 'WARN',
                '记录 aux_db_path=%s（本地可解析：%s）' % (p, exists))
    except Exception as e:
        add('V6', 'SKIP', '主库路由记录不可读（结构差异或权限）：%s' % str(e)[:120])

    # V7 ⑪ R-D：包内路由配置文件必须在位（缺件 ⇒ 客户静默回 legacy，与文档声明不符）
    cfg_path = os.path.join(root, AUX_PATHS)
    if not os.path.isfile(cfg_path):
        add('V7', 'FAIL', '包内缺 %s（客户侧将 fail-secure 回 legacy，与「已配置世代路由」的声明不符）'
            % AUX_PATHS, {'path': cfg_path})
    else:
        try:
            _d = json.load(open(cfg_path, encoding='utf-8'))
            add('V7', 'PASS', '包内 %s 在位（released=%s, generations=%s）'
                % (AUX_PATHS, bool(_d.get('released')), sorted((_d.get('generations') or {}).keys())))
        except Exception as e:
            add('V7', 'FAIL', '包内 %s 无法解析：%s' % (AUX_PATHS, str(e)[:120]))
    return rep


def _print(rep):
    print('[verify_qfq] data_root=%s price_source=%s' % (rep['data_root'], rep['price_source']))
    for c in rep['checks']:
        print('  %-3s %-4s %s' % (c['code'], c['level'], c['msg']))
    fails = [c for c in rep['checks'] if c['level'] == 'FAIL']
    print('[verify_qfq] verdict=%s%s' % (
        rep['verdict'],
        '（存在 FAIL：**不得启用**，请连同本 JSON 提交支持）' if fails else '（可用）'))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-root', required=False)
    ap.add_argument('--baseline', default=None)
    ap.add_argument('--max-lag-days', type=int, default=3)
    ap.add_argument('--price-source', default='mcp')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if not a.data_root:
        ap.print_help()
        return 2
    if not os.path.isdir(a.data_root):
        print('数据目录不存在：%s' % a.data_root)
        return 2
    rep = run(a.data_root, a.baseline, a.max_lag_days, a.price_source)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        _print(rep)
    return 0 if rep['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
