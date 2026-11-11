# -*- coding: utf-8 -*-
"""交付包 QFQ 出厂质量门（R-A 硬门 · 止血形态）—— 2026-11-08 节点21 / D5②

背景（⑪ 探查 R-A）
-------------------
当前交付包 `qfq_aux_paths.json` 为 `released=false`，客户运行时按 legacy 路由读包内
`qfq_aux.db` —— 合规。但一旦有人把包内配置置 `released=true`，路由会切到 gen1 世代库；
而 gen1 库实测 **6 表全 0 行** ⇒ 客户侧因子为空 ⇒ 复权静默退化为「ratio=1 或抛错」，
即「静默错价」。本脚本就是这道门的**只读检查器**。

定位（重要）
------------
本脚本**不接入打包链**（接入＝改生产核心，须走六步，方案见
`docs/design-d5-qfq-quality-gate-20261108.md`）。当前形态＝**打包 SOP 检查项**：
发版前人工/CI 跑一次，产出 `qfq_quality_certificate.json`；任一 FAIL 即拒绝发布。

检查项
------
  G1 包内主库存在
  G2 released 取值可判定（读 qfq_aux_paths.json；缺文件/坏 JSON ⇒ 按 false + WARN）
  G3 **R-A 硬门**：released=true ⇒ 世代库必须存在且核心表（adj_factor/fund_adj）行数 > 0；
     released=false ⇒ 包内 legacy `qfq_aux.db` 必须存在且非空（客户将读它）
  G4 主库路由记录自洽：released=true 但主库无 active cutover ⇒ FAIL（会 fail-secure 回 legacy，
     客户以为在用 gen1 实则 legacy，属口径不透明）
  G5 记录里的 aux_db_path 不得是异平台绝对路径（客户侧会 fail-closed 抛 AuxRouteError）

用法
----
    python scripts/qfq_release_gate.py --package-dir <交付包目录>
    python scripts/qfq_release_gate.py --selftest          # 负向/正向演练（临时目录，不碰生产）

退出码：0=全部 PASS（可发布）；1=有 FAIL（拒发）；2=用法/环境错误。
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import tempfile

CORE_TABLES = ('adj_factor', 'fund_adj')      # 因子核心表：任一为 0 行即判空库雷
AUX_PATHS = 'qfq_aux_paths.json'
LEGACY_AUX = 'qfq_aux.db'
MAIN_DB = 'quantstudio.db'
CERT_NAME = 'qfq_quality_certificate.json'


def _now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _looks_foreign_abs(raw):
    """Windows 盘符在 posix 上/posix 绝对路径在 Windows 上 —— 与 aux_router 同族判据。"""
    s = str(raw)
    if os.name == 'nt':
        return bool(re.match(r'^/', s)) and not re.match(r'^[A-Za-z]:', s)
    return bool(re.search(r'[A-Za-z]:[\\/]', s))


def _sqlite_tables_rows(path):
    """返回 ({表: 行数}, err)。空库/非 db 文件 ⇒ err。"""
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=10)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            out = {}
            for n in names:
                try:
                    out[n] = int(con.execute('SELECT count(*) FROM "%s"' % n).fetchone()[0])
                except Exception:
                    out[n] = -1
            return out, None
        finally:
            con.close()
    except Exception as e:
        return {}, str(e)[:160]


def _duckdb_active_cutover(main_db, price_source='mcp'):
    """读主库 active cutover（只读）。返回 (row|None, err)。"""
    try:
        import duckdb
        con = duckdb.connect(str(main_db), read_only=True)
        try:
            rows = con.execute(
                "SELECT a.cutover_id, c.source_generation, c.aux_db_path "
                "FROM qfq_active_cutover a JOIN qfq_source_cutover c "
                "ON c.cutover_id=a.cutover_id WHERE a.price_source=? LIMIT 1",
                [str(price_source)]).fetchall()
            return (rows[0] if rows else None), None
        finally:
            con.close()
    except Exception as e:
        return None, str(e)[:160]


def run_gate(pkg, price_source='mcp'):
    """执行 5 项检查，返回 certificate dict（不打印、不写文件）。"""
    pkg = os.path.abspath(pkg)
    cert = {'checked_at': _now(), 'package_dir': pkg, 'price_source': price_source,
            'verdict': 'PASS', 'checks': []}

    def add(code, level, msg, evidence=None):
        cert['checks'].append({'code': code, 'level': level, 'msg': msg,
                               **({'evidence': evidence} if evidence else {})})
        if level == 'FAIL':
            cert['verdict'] = 'FAIL'

    main_db = os.path.join(pkg, MAIN_DB)
    if not os.path.isfile(main_db):
        add('G1', 'FAIL', '包内缺主库 %s' % MAIN_DB, {'path': main_db})
        return cert
    add('G1', 'PASS', '包内主库存在', {'size_mb': round(os.path.getsize(main_db) / 2 ** 20, 1)})

    # G2 released
    cfg_path = os.path.join(pkg, AUX_PATHS)
    released, cfg_note = False, ''
    if os.path.isfile(cfg_path):
        try:
            doc = json.load(open(cfg_path, encoding='utf-8'))
            released = bool(doc.get('released', False))
            gens = doc.get('generations') or {}
            cfg_note = 'released=%s, generations=%s' % (released, sorted(gens.keys()))
        except Exception as e:
            cfg_note = '配置解析失败（按 released=false 处理）：%s' % str(e)[:120]
            add('G2', 'WARN', cfg_note)
    else:
        cfg_note = '缺 %s（客户侧 fail-secure 回 legacy）' % AUX_PATHS
    add('G2', 'PASS' if released or released is False else 'WARN',
        'released=%s；%s' % (released, cfg_note), {'released': released})

    # G3 R-A 硬门
    if released:
        # 世代库：优先 generations 列出的文件，其次目录内 qfq_aux_*gen*.db
        cands = []
        if os.path.isfile(cfg_path):
            try:
                gens = (json.load(open(cfg_path, encoding='utf-8')).get('generations') or {})
                cands += [os.path.join(pkg, os.path.basename(str(v))) for v in gens.values()]
            except Exception:
                pass
        if not cands:
            cands = [os.path.join(pkg, f) for f in sorted(os.listdir(pkg))
                     if f.startswith('qfq_aux') and f.endswith('.db') and f != LEGACY_AUX]
        if not cands:
            add('G3', 'FAIL', 'R-A 硬门：released=true 但包内找不到世代辅助库（客户将读到空因子）')
        for p in cands:
            if not os.path.isfile(p):
                add('G3', 'FAIL', 'R-A 硬门：世代库文件缺失 %s' % os.path.basename(p), {'path': p})
                continue
            rows, err = _sqlite_tables_rows(p)
            if err:
                add('G3', 'FAIL', 'R-A 硬门：世代库无法读取 %s：%s'
                    % (os.path.basename(p), err), {'path': p})
                continue
            empty = [t for t in CORE_TABLES if rows.get(t, 0) <= 0]
            zeros = [t for t, n in rows.items() if n == 0]
            ev = {'path': os.path.basename(p), 'core_rows': {t: rows.get(t, 0) for t in CORE_TABLES},
                  'zero_tables': zeros[:12], 'table_count': len(rows)}
            if empty:
                add('G3', 'FAIL', 'R-A 硬门：released=true 而世代库核心表为空 %s（静默错价风险）'
                    % empty, ev)
            elif zeros:
                add('G3', 'WARN', 'R-A 硬门：世代库核心表有数，但存在 0 行表 %s（确认是否应空）'
                    % zeros[:6], ev)
            else:
                add('G3', 'PASS', 'R-A 硬门：世代库 %s 核心表有数' % os.path.basename(p), ev)
    else:
        legacy = os.path.join(pkg, LEGACY_AUX)
        if not os.path.isfile(legacy):
            add('G3', 'FAIL', 'released=false 但包内缺 legacy 因子库 %s（客户侧路由会指向不存在的库）'
                % LEGACY_AUX, {'path': legacy})
        else:
            rows, err = _sqlite_tables_rows(legacy)
            if err:
                add('G3', 'FAIL', 'legacy 因子库无法读取：%s' % err, {'path': legacy})
            else:
                empty = [t for t in CORE_TABLES if rows.get(t, 0) <= 0]
                ev = {'path': LEGACY_AUX, 'core_rows': {t: rows.get(t, 0) for t in CORE_TABLES}}
                if empty:
                    add('G3', 'FAIL', 'released=false 而包内 legacy 库核心表为空 %s（客户立即踩空库）'
                        % empty, ev)
                else:
                    add('G3', 'PASS', 'legacy 因子库核心表有数（客户按 legacy 路由可读）', ev)

    # G4/G5 主库路由记录
    row, err = _duckdb_active_cutover(main_db, price_source)
    if err:
        add('G4', 'WARN', '主库路由记录不可读（若客户环境同样不可读则 fail-secure 回 legacy）：%s' % err)
    elif row is None:
        if released:
            msg = ('released=true 但主库无 active cutover(%s) 记录 ⇒ 实际会 fail-secure 回 legacy，'
                   '客户以为在用 gen1 实则 legacy' % price_source)
            # ②审条件：补一行「口径透明」日志——把「声明口径 vs 实际口径」显式落进证书，
            # 不靠读者推断（发布报告/E4 口径卡可直接引用本行）。
            add('G4', 'FAIL', msg,
                {'declared_route': 'gen1（released=true）', 'effective_route': 'legacy（fail-secure）',
                 'transparency': 'DECLARED≠EFFECTIVE：客户实际读包内 legacy qfq_aux.db，'
                                 '并非 qfq_aux_paths.json 声明的世代库'})
            print('[qfq_release_gate][口径透明] 声明=gen1(released=true) / 实际=legacy(fail-secure) '
                  '⇒ 二者不一致，已在证书 G4.evidence.transparency 落字')
        else:
            add('G4', 'PASS', '无 active cutover 且 released=false ⇒ 声明与生效口径一致（均 legacy）',
                {'declared_route': 'legacy（released=false）', 'effective_route': 'legacy'})
    else:
        cid, gen, aux_path = row[0], row[1], row[2]
        add('G4', 'PASS', 'active cutover=%s generation=%s（released=%s）' % (cid, gen, released),
            {'cutover_id': cid, 'source_generation': gen})
        if aux_path and _looks_foreign_abs(aux_path):
            add('G5', 'FAIL', '记录 aux_db_path 为异平台绝对路径（客户侧 fail-closed）：%s' % aux_path,
                {'aux_db_path': str(aux_path), 'host': 'windows' if os.name == 'nt' else 'posix'})
        elif aux_path and not os.path.isabs(str(aux_path)):
            add('G5', 'PASS', '记录 aux_db_path 为相对路径（客户侧依赖 config 解析）：%s' % aux_path)
        elif aux_path:
            add('G5', 'WARN', '记录 aux_db_path 为本机绝对路径（换机器即失效，建议改相对或走 config）：%s'
                % aux_path)
        else:
            add('G5', 'WARN', '记录 aux_db_path 为空（客户侧 fail-closed）')
    return cert


def write_certificate(cert, out_dir=None):
    path = os.path.join(out_dir or cert['package_dir'], CERT_NAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(cert, ensure_ascii=False, indent=1) + '\n')
    return path


def _print(cert):
    print('[qfq_release_gate] package=%s price_source=%s' % (cert['package_dir'], cert['price_source']))
    for c in cert['checks']:
        print('  %-3s %-4s %s' % (c['code'], c['level'], c['msg']))
    print('[qfq_release_gate] verdict=%s（PASS=可发布 / FAIL=拒发）' % cert['verdict'])


def selftest():
    """负向/正向演练：临时目录造包，验证「空 gen1 + released=true ⇒ 拒发」必被拦住。"""
    import shutil
    d = tempfile.mkdtemp(prefix='qfq_gate_selftest_')
    ok = True
    try:
        def make_pkg(name, released, gen_rows, legacy_rows, write_gen=True):
            p = os.path.join(d, name)
            os.makedirs(p, exist_ok=True)
            m = os.path.join(p, MAIN_DB)          # 造极小 duckdb 主库（无 cutover 表 → G4 WARN/FAIL 依 released）
            try:
                import duckdb
                con = duckdb.connect(m)
                con.execute('CREATE TABLE IF NOT EXISTS qfq_active_cutover(cutover_id VARCHAR, price_source VARCHAR)')
                con.execute('CREATE TABLE IF NOT EXISTS qfq_source_cutover('
                            'cutover_id VARCHAR, source_generation VARCHAR, aux_db_path VARCHAR)')
                con.close()
            except Exception:
                open(m, 'wb').close()
            for fn, rows_ in ((LEGACY_AUX, legacy_rows),
                              ('qfq_aux_mcp_gen1.db', gen_rows)):
                if fn != LEGACY_AUX and not write_gen:
                    continue
                con = sqlite3.connect(os.path.join(p, fn))
                for t in CORE_TABLES:
                    con.execute('CREATE TABLE IF NOT EXISTS %s(time VARCHAR, code VARCHAR, adj_factor REAL)' % t)
                    for i in range(rows_):
                        con.execute('INSERT INTO %s VALUES(?,?,?)' % t, ('2026-11-06', 'X%d' % i, 1.0))
                con.commit(); con.close()
            cfg = {'released': released, 'generations': {'mcp-gen1': 'qfq_aux_mcp_gen1.db'}}
            open(os.path.join(p, AUX_PATHS), 'w', encoding='utf-8').write(
                json.dumps(cfg, ensure_ascii=False))
            return p

        # 负向 1：R-A 雷——released=true + 空 gen1 ⇒ 必须 FAIL
        p1 = make_pkg('neg_empty_gen1_released', True, 0, 5)
        c1 = run_gate(p1)
        r1 = c1['verdict'] == 'FAIL' and any(x['code'] == 'G3' and x['level'] == 'FAIL'
                                            for x in c1['checks'])
        print('  [负向1] released=true + 空 gen1 库 ⇒ %s（预期 FAIL）%s'
              % (c1['verdict'], 'OK' if r1 else '❌ 未拦住'))
        ok &= r1

        # 负向 2：released=false + 空 legacy ⇒ 必须 FAIL（客户立刻踩空库）
        p2 = make_pkg('neg_empty_legacy', False, 5, 0)
        c2 = run_gate(p2)
        r2 = c2['verdict'] == 'FAIL' and any(x['code'] == 'G3' and x['level'] == 'FAIL'
                                            for x in c2['checks'])
        print('  [负向2] released=false + 空 legacy 库 ⇒ %s（预期 FAIL）%s'
              % (c2['verdict'], 'OK' if r2 else '❌ 未拦住'))
        ok &= r2

        # 负向 3：released=true 但世代库文件缺失 ⇒ FAIL
        p3 = make_pkg('neg_missing_gen1', True, 0, 5, write_gen=False)
        c3 = run_gate(p3)
        r3 = c3['verdict'] == 'FAIL'
        print('  [负向3] released=true + 世代库缺失 ⇒ %s（预期 FAIL）%s'
              % (c3['verdict'], 'OK' if r3 else '❌ 未拦住'))
        ok &= r3

        # 正向：released=false + legacy 有数 ⇒ PASS（当前交付形态）
        p4 = make_pkg('pos_legacy_ok', False, 0, 5)
        c4 = run_gate(p4)
        r4 = c4['verdict'] == 'PASS'
        print('  [正向 ] released=false + legacy 有数 ⇒ %s（预期 PASS）%s'
              % (c4['verdict'], 'OK' if r4 else '❌ 误杀'))
        ok &= r4

        # 证书落盘
        cp = write_certificate(c4, d)
        print('  证书样例：%s（%d bytes）' % (os.path.basename(cp), os.path.getsize(cp)))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print('[selftest] %s' % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--package-dir')
    ap.add_argument('--price-source', default='mcp')
    ap.add_argument('--certificate', action='store_true', help='在包目录写 qfq_quality_certificate.json')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.package_dir:
        ap.print_help()
        return 2
    if not os.path.isdir(a.package_dir):
        print('包目录不存在：%s' % a.package_dir)
        return 2
    cert = run_gate(a.package_dir, a.price_source)
    if a.json:
        print(json.dumps(cert, ensure_ascii=False, indent=1))
    else:
        _print(cert)
    if a.certificate:
        print('[qfq_release_gate] 证书已写：%s' % write_certificate(cert))
    return 0 if cert['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
