#coding:gbk
# docs/probe/qmt_profit_ability_probe_v5-20261017.py
# ============================================================================
# D-29 S5 合成可行性探针 v5（终裁裁②'：先探合成；候选改用**文档明列字段名**）
#
# 背景与转折（2026-10-17 00:1x）：
#   v4 定谳 NO_ROE_FIELD，但**候选集本身命名规则错误**——据 Context7
#   /websites/dict_thinktrader_net（nativeApi/xtdata.html「PershareIndex - Major
#   Indicators」全字段表）QMT PERSHAREINDEX 字段分**两族**：
#     族A「每股/财务分析」：s_fa_ocfps / s_fa_bps / s_fa_eps_basic / s_fa_eps_diluted ...
#     族B「比率类，无 s_fa_ 前缀」：du_return_on_equity(净资产收益率) /
#           equity_roe(加权ROE) / net_roe(摊薄ROE) / total_roe(摊薄ROA) /
#           gross_profit(毛利率) / net_profit(净利率) ...
#   v4 测的 s_fa_roe / roe / s_fa_roe_weighted **跨族猜名** => 全 0 属预期，
#   **不构成「QMT 无 ROE」结论**。v5 改用文档明列名重测（文档依据，非惯例推断）。
#
# 本地契约对照（ptrade_api.py:899-903 profit_ability）：
#   roe / roa / roic / net_profit_margin / gross_profit_margin /
#   operating_profit_margin / total_profit_net_profit / expense_to_revenue /
#   operate_profit_to_profit / net_profit_to_balance / roe_avg / roa_avg
#
# 判据（沿用 v4 生产口径 + 拒判机制）：
#   前提1 POS(s_fa_eps_basic) valid>0 ；前提2 NEG(__probe_invalid__) valid==0
#   候选 valid>0 => FIELD_CONFIRMED（记录字段名/样本值/有效率，供口径裁定）
#   候选全 0     => NO_ROE_FIELD（真结论：连文档字段名也无效）
#   前提不成立   => INSTRUMENT_FAIL
#
# 量级参考（判样本值合理性）：ROE 0.05~0.25 ；毛利率 0.1~0.6 ；净利率 0.0~0.4
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间任选 -> 运行 -> 回抄 [ROE_PROBE5] 全部行
# ============================================================================


def init(C):
    C.qs_probe5_done = False


_KEYS = ("m_anntime", "m_timetag")


def _qs_probe5_scan(raw, codes):
    """生产口径复刻（与 v4 同）：raw[_code] -> 按自身 columns 取非键列
       -> 有效行数＝值列至少一非空。返回 (valid, total, value_cols, sample)"""
    valid_rows = 0
    total_rows = 0
    value_cols = []
    sample = None
    for _code in codes:
        try:
            _one = raw[_code]
        except Exception:
            continue
        if _one is None or not hasattr(_one, "columns"):
            continue
        try:
            _cols = [str(c) for c in list(_one.columns)]
        except Exception:
            continue
        try:
            total_rows += len(_one)
        except Exception:
            pass
        _vc = [c for c in _cols if c not in _KEYS]
        if not _vc:
            continue
        if not value_cols:
            value_cols = _vc
        try:
            sub = _one[_vc]
        except Exception:
            continue
        try:
            _okrows = sub[sub.notna().any(axis=1)]
            valid_rows += int(len(_okrows))
            if sample is None and len(_okrows) > 0:
                try:
                    sample = float(_okrows.iloc[0][_vc[0]])
                except Exception:
                    sample = None
        except Exception:
            continue
    return valid_rows, total_rows, value_cols[:6], sample


def handlebar(C):
    if getattr(C, "qs_probe5_done", False):
        return
    C.qs_probe5_done = True

    codes = ["000001.SZ", "600000.SH"]
    start_date = "19900101"
    end_date = "20991231"

    targets = [
        ("PERSHAREINDEX.s_fa_eps_basic", "POS", "正对照（族A·已知有效）"),
        ("PERSHAREINDEX.__probe_invalid__", "NEG", "负对照（无效字段名）"),
        ("PERSHAREINDEX.du_return_on_equity", "C1", "文档：净资产收益率"),
        ("PERSHAREINDEX.equity_roe", "C2", "文档：加权净资产收益率"),
        ("PERSHAREINDEX.net_roe", "C3", "文档：摊薄净资产收益率"),
        ("PERSHAREINDEX.total_roe", "C4", "文档：摊薄总资产收益率(ROA)"),
        ("PERSHAREINDEX.gross_profit", "C5", "文档：毛利率"),
        ("PERSHAREINDEX.net_profit", "C6", "文档：净利率"),
    ]

    print("[ROE_PROBE5] begin codes=%s range=%s..%s" % (",".join(codes), start_date, end_date))

    res = {}
    for field, tag, note in targets:
        try:
            raw = C.get_financial_data([field], codes, start_date, end_date,
                                       report_type="announce_time")
        except Exception as exc:
            print("[ROE_PROBE5] %s field=%s EXC=%r note=%s" % (tag, field, exc, note))
            continue
        try:
            vr, tr, vc, sp = _qs_probe5_scan(raw, codes)
        except Exception as exc:
            print("[ROE_PROBE5] %s field=%s SCAN_EXC=%r note=%s" % (tag, field, exc, note))
            continue
        res[tag] = (vr, sp)
        ratio = (float(vr) / tr) if tr else -1.0
        print("[ROE_PROBE5] %s field=%s valid=%d total=%d ratio=%.3f cols=%s sample=%s note=%s"
              % (tag, field, vr, tr, ratio, vc, sp, note))

    pos = res.get("POS", (0, None))[0]
    neg = res.get("NEG", (0, None))[0]
    hits = []
    for tag, name in (("C1", "du_return_on_equity"), ("C2", "equity_roe"),
                      ("C3", "net_roe"), ("C4", "total_roe"),
                      ("C5", "gross_profit"), ("C6", "net_profit")):
        if res.get(tag, (0, None))[0] > 0:
            hits.append("%s(sample=%s)" % (name, res[tag][1]))

    if pos <= 0:
        verdict = "INSTRUMENT_FAIL: 正对照 valid<=0（取值口径仍有错配，勿下结论）"
    elif neg > 0:
        verdict = "INSTRUMENT_FAIL: 负对照 valid>0（判据不可区分，勿下结论）"
    elif hits:
        verdict = "FIELD_CONFIRMED: %s（QMT 有原生比率字段 -> 走路径①补映射，无需合成）" % ";".join(hits)
    else:
        verdict = "NO_ROE_FIELD: 文档候选全 0（真结论：转路径②移除+告警 或 评估 ASHARE* 合成）"

    print("[ROE_PROBE5] pos=%d neg=%d hits=%d" % (pos, neg, len(hits)))
    print("[ROE_PROBE5] VERDICT=%s" % verdict)
    print("[ROE_PROBE5] end")
