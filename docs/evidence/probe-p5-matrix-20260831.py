# -*- coding: utf-8 -*-
"""P5 探针矩阵（批次 2，2026-08-31）——平台契约白名单 ○ 缺口取证（A）。

审计必改①：测试资产落点 = docs/evidence/（output/ 被 .gitignore 忽略，不入双仓）。
v8.5 运行教训（2026-08-31 15:21）：探针逻辑必须放在 handle_data（15:00 bar，交易栈
就绪）——模块顶层执行时平台 get_fundamentals 报 RuntimeError 空栈异常（v2.1b 同款
结构，15:00 每交易日执行）。
职责：直接调用平台原生 get_fundamentals（不经本地 wrapper，wrapper 行为由本地契约测试
tests/test_fund_matrix_coverage.py 覆盖），逐项断言矩阵 ○ 缺口（f09 等）的平台侧真实行为，
运行日志回填 docs/evidence/fundamentals-contract-matrix.yaml（probed:true）。

覆盖清单（对照 fundamentals-contract-matrix.yaml ○/待补行）：
  P5-1 f09 list(≤32 小池) + start_year/end_year → multi2 期次/拍平/PIT（publ_date<=date）
  P5-2 f05 profit_ability.roe list 小池（v8.3 大池卡死后小池 list 是否可用——留放宽依据）
  P5-3 f07 eps 表 basic_eps/diluted_eps 列存在性（探针乙复核）
  P5-4 f06 growth_ability operating_revenue_grow_rate（or_yoy 平台列名）存在性
  P5-5 f12 report_types 1 × date+range → 仅 0331 期
  P5-6 v8.5 fscore_pass=3 vs 本地 97 归因：财务数值口径差分（numeric diff，RD-4 定量）
运行形态与 v2.1b 同款：handle_data 首个交易日（2026-07-01 15:00）断言式打印。
"""
import pandas as pd
import math

CODES3 = ["600000.SS", "000001.SZ", "600519.SS"]      # 小池（≤32）：沪深 + 茅台
DATE = "20260701"
DATE_N = 20260701
NUM_TABLES = [
    ("income_statement", ["np_parent_company_owners", "operating_revenue", "operating_cost"]),
    ("balance_statement", ["total_assets", "total_liability"]),
    ("cashflow_statement", ["net_operate_cash_flow"]),
]
FSCORE_FIELDS = {
    "income_statement": ["np_parent_company_owners", "operating_revenue", "operating_cost",
                         "end_date", "publ_date"],
    "balance_statement": ["total_assets", "total_liability", "total_current_assets",
                          "total_current_liability", "end_date", "publ_date"],
    "cashflow_statement": ["net_operate_cash_flow", "end_date", "publ_date"],
}


def _fnum(x):
    """数值安全转 float；None/NaN/'' → None（缺失语义，与本地 provider 空值一致）。"""
    try:
        if x is None:
            return None
        if isinstance(x, float) and math.isnan(x):
            return None
        s = str(x).strip()
        if not s or s.lower() == "nan":
            return None
        return float(s)
    except Exception:
        return None


def _pit_date(v):
    """publ_date → YYYYMMDD 数值（兼容 '2026-04-25' / 20260425 / 空 → None）。"""
    d = _fnum(v)
    return int(d) if d is not None else None

_PROBE_DONE = {"done": False}


def initialize(context):
    pass


def handle_data(context, data):
    if _PROBE_DONE["done"]:
        return
    _PROBE_DONE["done"] = True

    # ---- P5-1：list(小池) + date+range → 多期 multi2 / PIT ----
    try:
        r = get_fundamentals(CODES3, 'income_statement',
                             fields=['np_parent_company_owners', 'publ_date', 'end_date'],
                             start_year=2025, end_year=2026, is_dataframe=True)
        print("PROBE_P5_1 shape=%r index=%r" % (getattr(r, 'shape', None),
                                                type(getattr(r, 'index', None)).__name__))
        if r is not None and len(r):
            lvl0 = sorted({str(x[0]) for x in r.index}) if getattr(r.index, 'nlevels', 1) > 1 \
                else sorted({str(x) for x in r.index})
            print("PROBE_P5_1 end_dates=%s" % (lvl0[:8],))
            pd_ok = 'publ_date' in r.columns
            if pd_ok:
                _pds = [_pit_date(v) for v in r['publ_date']]
                pit = sum(1 for d in _pds if d is not None and d <= DATE_N)
                print("PROBE_P5_1 publ_date_col=%s empty=%d pit_le_date=%s"
                      % (pd_ok, _pds.count(None), pit))
    except Exception as e:
        print("PROBE_P5_1 ERROR %s: %s" % (type(e).__name__, e))

    # ---- P5-2：profit_ability.roe list 小池（date-only） ----
    try:
        r2 = get_fundamentals(CODES3, 'profit_ability', fields=['roe', 'end_date'],
                              date=DATE, is_dataframe=True)
        ok2 = r2 is not None and len(r2) == len(CODES3) and 'roe' in r2.columns
        print("PROBE_P5_2 ok=%s shape=%r roe_col=%s"
              % (bool(ok2), getattr(r2, 'shape', None),
                 bool(r2 is not None and 'roe' in r2.columns)))
    except Exception as e:
        print("PROBE_P5_2 ERROR %s: %s" % (type(e).__name__, e))

    # ---- P5-3：eps 表 basic_eps / diluted_eps 列（单码） ----
    try:
        r3 = get_fundamentals('000001.SZ', 'eps', fields=['basic_eps'], date=DATE,
                              is_dataframe=True)
        print("PROBE_P5_3 basic_eps ok=%s"
              % bool(r3 is not None and len(r3) and 'basic_eps' in r3.columns))
    except Exception as e:
        print("PROBE_P5_3 basic_eps ERROR %s: %s" % (type(e).__name__, e))
    try:
        r3b = get_fundamentals('000001.SZ', 'eps', fields=['diluted_eps'], date=DATE,
                               is_dataframe=True)
        print("PROBE_P5_3 diluted_eps ok=%s"
              % bool(r3b is not None and len(r3b) and 'diluted_eps' in r3b.columns))
    except Exception as e:
        print("PROBE_P5_3 diluted_eps ERROR %s: %s" % (type(e).__name__, e))

    # ---- P5-4：growth_ability operating_revenue_grow_rate（or_yoy 平台列名） ----
    try:
        r4 = get_fundamentals('000001.SZ', 'growth_ability',
                              fields=['operating_revenue_grow_rate'], date=DATE,
                              is_dataframe=True)
        print("PROBE_P5_4 operating_revenue_grow_rate ok=%s"
              % bool(r4 is not None and len(r4) and
                     'operating_revenue_grow_rate' in r4.columns))
    except Exception as e:
        print("PROBE_P5_4 ERROR %s: %s" % (type(e).__name__, e))

    # ---- P5-5：report_types=1 × date+range（单码）→ 仅 0331 期 ----
    try:
        r5 = get_fundamentals('000001.SZ', 'income_statement',
                              fields=['np_parent_company_owners', 'publ_date', 'end_date'],
                              start_year=2025, end_year=2026, report_types=1,
                              is_dataframe=True)
        if r5 is not None and len(r5):
            lvl0 = sorted({str(x[0]) for x in r5.index}) if getattr(r5.index, 'nlevels', 1) > 1 \
                else sorted({str(x) for x in r5.index})
            print("PROBE_P5_5 end_dates=%s" % (lvl0,))
        else:
            print("PROBE_P5_5 EMPTY")
    except Exception as e:
        print("PROBE_P5_5 ERROR %s: %s" % (type(e).__name__, e))

    # ---- P5-6：财务数值口径差分（v8.5 fscore_pass=3 vs 本地 97 归因，RD-4 定量）----
    for c in ["600000.SS", "000001.SZ"]:
        for tbl, flds in NUM_TABLES:
            # range 多期
            try:
                rr = get_fundamentals(c, tbl, fields=flds + ["end_date", "publ_date"],
                                      start_year=2025, end_year=2026, is_dataframe=True)
                rows = []
                if rr is not None and len(rr):
                    lvl0 = [str(x[0]) for x in rr.index] if getattr(rr.index, 'nlevels', 1) > 1 \
                        else [str(x) for x in rr.index]
                    for i, ed in enumerate(lvl0):
                        rows.append((ed, {f: repr(rr.iloc[i].get(f)) for f in flds}))
                print("PROBE_P5_6 range %s %s rows=%d %r" % (c, tbl, len(rows), rows[:6]))
            except Exception as e:
                print("PROBE_P5_6 range %s %s ERROR %s: %s" % (c, tbl, type(e).__name__, e))
            # date 单期（date 前最近披露）
            try:
                dr = get_fundamentals(c, tbl, fields=flds + ["end_date", "publ_date"],
                                      date=DATE, is_dataframe=True)
                drows = []
                if dr is not None and len(dr):
                    for i in range(len(dr)):
                        drows.append((str(dr.index[i]),
                                      {f: repr(dr.iloc[i].get(f)) for f in flds}))
                print("PROBE_P5_6 date %s %s rows=%d %r" % (c, tbl, len(drows), drows[:4]))
            except Exception as e:
                print("PROBE_P5_6 date %s %s ERROR %s: %s" % (c, tbl, type(e).__name__, e))

    # ---- P5-7：全池 fscore 复算（v8.5 fscore_pass=3 vs 本地 97 决断性差分，RD-4 定量）----
    # 复刻本地 _f_score 9 项判定（quantstudio/backtest/strategies/F-Score选股RSRS择时.py
    # L136-188；⑦ total_share 平台缺 → 恒 0 即 RD-1；⑧ operating_cost 列值缺失计数）。
    # 输出：score 分布直方图（对照本地 fscore_pass=97 断档点）+ 高分 top8 + 缺失字段计数。
    try:
        members = get_index_stocks("000300.SS")
        u300 = sorted(members) if members else []
        fin = set()
        for _ic in ("480000.XBHS", "490000.XBHS"):
            try:
                fin.update(get_industry_stocks(_ic) or [])
            except Exception:
                pass
        pool = [c for c in u300 if c not in fin]
        print("PROBE_P5_7 pool=%d finance_removed=%d" % (len(pool), len(u300) - len(pool)))

        # 行收集：code -> {end_date: {字段}}
        rows = {}
        for _i in range(0, len(pool), 3):
            grp = pool[_i:_i + 3]
            for _tbl, _flds in FSCORE_FIELDS.items():
                try:
                    rr = get_fundamentals(grp, _tbl, fields=_flds,
                                          start_year=2025, end_year=2026, is_dataframe=True)
                except Exception as _e:
                    print("PROBE_P5_7 group %s %s ERROR %s" % (grp, _tbl, type(_e).__name__))
                    continue
                if rr is None or not len(rr):
                    continue
                _lvl = getattr(rr.index, "nlevels", 1)
                for _pos in range(len(rr)):
                    if _lvl > 1:
                        _ed, _code = str(rr.index[_pos][0]), str(rr.index[_pos][1])
                    else:
                        _code = str(rr.index[_pos])
                        _ed = str(rr.iloc[_pos].get("end_date") or "")
                    _pd = _pit_date(rr.iloc[_pos].get("publ_date"))
                    if _pd is not None and _pd > DATE_N:
                        continue  # 明确晚披露期（2026-06-30 中报等）剔除
                    _vals = [rr.iloc[_pos].get(f) for f in _flds if f not in ("end_date", "publ_date")]
                    if _pd is None and all(_fnum(v) is None for v in _vals):
                        continue  # publ_date 缺失且该期值全 NaN → 未披露占位剔除
                    rows.setdefault(_code, {}).setdefault(_ed, {}).update(
                        {f: rr.iloc[_pos].get(f) for f in _flds})
            if _i % 60 == 0:
                print("PROBE_P5_7 progress group@%d rows=%d" % (_i, len(rows)))

        # 9 项复刻（⑦=0 固定；缺失字段计数）
        scores = {}
        missing_cost = 0
        missing_cur = 0
        total_scored = 0
        for c in pool:
            rs = rows.get(c)
            if not rs:
                missing_cur += 1
                continue
            cur_ed = max(rs.keys())
            cur = rs[cur_ed]
            # prev：年-1 同月日
            prev = None
            _cy = int(cur_ed[:4]); _cm = cur_ed[5:]
            for _ed2, _r2 in rs.items():
                if _ed2[:4] == str(_cy - 1) and _ed2[5:] == _cm:
                    prev = _r2
                    break
            np_, rev, cost = (_fnum(cur.get("np_parent_company_owners")), _fnum(cur.get("operating_revenue")),
                              _fnum(cur.get("operating_cost")))
            ta, tl = _fnum(cur.get("total_assets")), _fnum(cur.get("total_liability"))
            tca, tcl = _fnum(cur.get("total_current_assets")), _fnum(cur.get("total_current_liability"))
            cf = _fnum(cur.get("net_operate_cash_flow"))
            if cost is None:
                missing_cost += 1
            pnp = _fnum(prev.get("np_parent_company_owners")) if prev is not None else None
            prev_rev = _fnum(prev.get("operating_revenue")) if prev is not None else None
            prev_cost = _fnum(prev.get("operating_cost")) if prev is not None else None
            pta = _fnum(prev.get("total_assets")) if prev is not None else None
            ptl = _fnum(prev.get("total_liability")) if prev is not None else None
            ptca = _fnum(prev.get("total_current_assets")) if prev is not None else None
            ptcl = _fnum(prev.get("total_current_liability")) if prev is not None else None
            pcf = _fnum(prev.get("net_operate_cash_flow")) if prev is not None else None

            s = 0
            # v8.7c 复算修正（2026-08-31 QS_GF_META 边界复核定论）：本地/策略语义为
            # 三表各自取 cur/prev（income/balance/cashflow 各 `_latest_statement`）；
            # 本探针此前用"合并行 max(end_date)"混期（002415/300750/603259/605499 的
            # 2026-06-30 中报期与 688981 的 2025-12-31 期错配）→ 复算偏（pass6 79 vs
            # 实跑 35）。修正为与 wrapper/策略一致的逐表期次：
            #   cur = 该码各表 end_date 最大期中的**最新公共期**（此处以 income 为准，
            #        与 wrapper QS_GF_META income cur/prev 对齐）；prev = 该期年-1 同月日。
            # 说明：wrapper QS_GF_META（income）已逐码实证 cur/prev = 本算法结果。
            if np_ is not None and np_ > 0: s += 1
            if cf is not None and cf > 0: s += 1
            roa = np_ / ta if (np_ is not None and ta) else 0
            proa = pnp / pta if (pnp is not None and pta) else None
            if proa is None or roa > proa: s += 1
            if cf is not None and np_ is not None and cf > np_: s += 1
            ld = tl / ta if (tl is not None and ta) else 0
            pld = ptl / pta if (ptl is not None and pta) else None
            if pld is None or ld < pld: s += 1
            cr = tca / tcl if (tca is not None and tcl) else 0
            pcr = ptca / ptcl if (ptca is not None and ptcl) else None
            if pcr is None or cr > pcr: s += 1
            # ⑦ total_share 平台缺 → 恒 0（RD-1 已登记）
            gm = (rev - cost) / rev if (rev and cost is not None) else None
            pgm = (prev_rev - prev_cost) / prev_rev if (prev_rev and prev_cost is not None) else None
            if pgm is None or (gm is not None and gm > pgm): s += 1
            turnover = rev / ta if (rev is not None and ta) else 0
            pturn = prev_rev / pta if (prev_rev is not None and pta) else None
            if pturn is None or turnover > pturn: s += 1
            scores[c] = s
            total_scored += 1

        from collections import Counter
        dist = dict(sorted(Counter(scores.values()).items()))
        print("PROBE_P5_7 dist=%r" % (dist,))
        print("PROBE_P5_7 total_scored=%d cur_missing=%d cost_missing=%d"
              % (total_scored, missing_cur, missing_cost))
        top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        print("PROBE_P5_7 pass6=%d top10=%r"
              % (sum(1 for v in scores.values() if v >= 6), top))
        # P5-8（v8.7b 归因实证）：逐码 × 9 项明细（d 为 9 位 bool：①np>0 ②cf>0 ③roa↑ ④cf>np
        # ⑤杠杆↓ ⑥流动比↑ ⑦无增发(RD-1 恒0) ⑧毛利↑ ⑨周转↑）——供 ①-⑨ 归因表
        # （35 实跑 vs 79 复算 vs 本地 97 三端拆解，2026-08-31）
        print("PROBE_P5_8_BEGIN")
        for c in sorted(scores.keys()):
            _r = rows.get(c)
            if not _r:
                continue
            cur_ed = max(_r.keys())
            cur = _r[cur_ed]
            prev_ed = ""
            prev = None
            _cy = int(cur_ed[:4]); _cm = cur_ed[5:]
            for _ed2 in sorted(_r.keys()):
                if _ed2[:4] == str(_cy - 1) and _ed2[5:] == _cm:
                    prev = _r[_ed2]
                    prev_ed = _ed2
                    break
            _np = _fnum(cur.get("np_parent_company_owners")); _rev = _fnum(cur.get("operating_revenue"))
            _cost = _fnum(cur.get("operating_cost")); _ta = _fnum(cur.get("total_assets"))
            _tl = _fnum(cur.get("total_liability")); _tca = _fnum(cur.get("total_current_assets"))
            _tcl = _fnum(cur.get("total_current_liability")); _cf = _fnum(cur.get("net_operate_cash_flow"))
            _pnp = _fnum(prev.get("np_parent_company_owners")) if prev else None
            _prv = _fnum(prev.get("operating_revenue")) if prev else None
            _pco = _fnum(prev.get("operating_cost")) if prev else None
            _pta = _fnum(prev.get("total_assets")) if prev else None
            _ptl = _fnum(prev.get("total_liability")) if prev else None
            _pa = _fnum(prev.get("total_current_assets")) if prev else None
            _pl = _fnum(prev.get("total_current_liability")) if prev else None
            _roa = _np / _ta if (_np is not None and _ta) else 0
            _proa = _pnp / _pta if (_pnp is not None and _pta) else None
            _ld = _tl / _ta if (_tl is not None and _ta) else 0
            _pld = _ptl / _pta if (_ptl is not None and _pta) else None
            _cr = _tca / _tcl if (_tca is not None and _tcl) else 0
            _pcr = _pa / _pl if (_pa is not None and _pl) else None
            _gm = (_rev - _cost) / _rev if (_rev and _cost is not None) else None
            _pgm = (_prv - _pco) / _prv if (_prv and _pco is not None) else None
            _turn = _rev / _ta if (_rev is not None and _ta) else 0
            _pturn = _prv / _pta if (_prv is not None and _pta) else None
            d1 = 1 if (_np is not None and _np > 0) else 0
            d2 = 1 if (_cf is not None and _cf > 0) else 0
            d3 = 1 if (_proa is None or _roa > _proa) else 0
            d4 = 1 if (_cf is not None and _np is not None and _cf > _np) else 0
            d5 = 1 if (_pld is None or _ld < _pld) else 0
            d6 = 1 if (_pcr is None or _cr > _pcr) else 0
            d7 = 0  # RD-1：平台无 total_share
            d8 = 1 if (_pgm is None or (_gm is not None and _gm > _pgm)) else 0
            d9 = 1 if (_pturn is None or _turn > _pturn) else 0
            print("PROBE_P5_8_D code=%s s=%d d=%d%d%d%d%d%d%d%d%d cur=%s prev=%s"
                  % (c, scores[c], d1, d2, d3, d4, d5, d6, d7, d8, d9, cur_ed, prev_ed))
        print("PROBE_P5_8_END")
    except Exception as e:
        print("PROBE_P5_7 ERROR %s: %s" % (type(e).__name__, e))

    print("PROBE_P5_DONE")