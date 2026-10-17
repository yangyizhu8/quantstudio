#coding:gbk
# docs/probe/qmt_profit_ability_probe_v4-20261016.py
# ============================================================================
# D-29 S2 探针 v4（生产口径复刻版；v1-v3 三轮仪器迭代后重铸）
#
# 权威依据（生产 wrapper 实测口径，source_import_qmt.py:1462 _qs_fin_to_rows_hist
# Panel 分支 + 其 HISTDBG3 取证分支 :1531-1571）：
#   1) list(raw.items)                      -> codes
#   2) 优先 raw.to_frame()（MultiIndex, frame_levels=2）；不可用则 percode 回落
#   3) **取子：raw[_code] -> DataFrame**（index=日期, columns=原生列名）
#      —— HISTDBG3 生产实证：_o0.columns 含原生列名（m_anntime/m_timetag/值列），
#         值列口径 = [c for c in columns if c not in ('m_anntime','m_timetag')]
#   4) 取值判据以 **子对象自身列名清单** 为准，**不假设**列名等于请求字段名
#      （v3 假设即落空 -> cells=0，本轮不重复该错误）
#
# v4 判据（可区分性硬门，沿用 v3 的拒判机制）：
#   前提1 POS(正对照 s_fa_eps_basic) 有效行数 > 0
#   前提2 NEG(负对照 __probe_invalid__) 有效行数 == 0
#   候选 CAND1-3 有效行数 > 0 => FIELD_CONFIRMED（走路径①补映射）
#   候选全 0 且前提成立       => NO_ROE_FIELD（转路径②移除+告警）
#   任一前提不成立            => INSTRUMENT_FAIL（拒绝产结论）
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间任选 -> 运行 -> 回抄 [ROE_PROBE4] 全部行
#   只读探针：仅取数打印，不下单、不写文件；无窗口敏感性，任意时点可跑。
# ============================================================================


def init(C):
    C.qs_probe4_done = False


_KEYS = ("m_anntime", "m_timetag")


def _qs_probe4_scan(raw, codes):
    """生产口径复刻：raw[_code] -> DataFrame -> 值列（排除 m_anntime/m_timetag）
       -> 统计「值列至少一个非空」的行数 = 有效行数（与 wrapper
       _qs_get_fundamentals 的完整行过滤同口径）。
    返回 (有效行数, 总行数, 值列名清单, 样本值)"""
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
        if not _vc and not value_cols:
            value_cols = _vc
        if not _vc:
            continue
        if not value_cols:
            value_cols = _vc
        # 按子对象自身列名取（非键列），逐行判「至少一列非空」
        try:
            sub = _one[_vc]
        except Exception:
            continue
        try:
            _okmask = sub.notna().any(axis=1)
            _okrows = sub[_okmask]
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
    if getattr(C, "qs_probe4_done", False):
        return
    C.qs_probe4_done = True

    codes = ["000001.SZ", "600000.SH"]
    start_date = "19900101"
    end_date = "20991231"

    targets = [
        ("PERSHAREINDEX.s_fa_eps_basic", "POS", "正对照（wrapper 生产已验证有数据）"),
        ("PERSHAREINDEX.__probe_invalid__", "NEG", "负对照（无效字段名）"),
        ("PERSHAREINDEX.s_fa_roe", "CAND1", "候选1"),
        ("PERSHAREINDEX.roe", "CAND2", "候选2"),
        ("PERSHAREINDEX.s_fa_roe_weighted", "CAND3", "候选3"),
    ]

    print("[ROE_PROBE4] begin codes=%s range=%s..%s" % (",".join(codes), start_date, end_date))

    res = {}
    for field, tag, note in targets:
        try:
            raw = C.get_financial_data([field], codes, start_date, end_date,
                                       report_type="announce_time")
        except Exception as exc:
            print("[ROE_PROBE4] %s field=%s EXC=%r note=%s" % (tag, field, exc, note))
            continue
        try:
            vr, tr, vc, sp = _qs_probe4_scan(raw, codes)
        except Exception as exc:
            print("[ROE_PROBE4] %s field=%s SCAN_EXC=%r note=%s" % (tag, field, exc, note))
            continue
        res[tag] = vr
        print("[ROE_PROBE4] %s field=%s valid_rows=%d total_rows=%d value_cols=%s sample=%s note=%s"
              % (tag, field, vr, tr, vc, sp, note))

    pos = res.get("POS", 0)
    neg = res.get("NEG", 0)
    cands = [t for t in ("CAND1", "CAND2", "CAND3") if res.get(t, 0) > 0]

    if pos <= 0:
        verdict = "INSTRUMENT_FAIL: 正对照 valid_rows<=0（取值口径仍有错配，勿下结论）"
    elif neg > 0:
        verdict = "INSTRUMENT_FAIL: 负对照 valid_rows>0（判据不可区分，勿下结论）"
    elif cands:
        verdict = "FIELD_CONFIRMED: %s（走路径①补映射 PERSHAREINDEX）" % ",".join(cands)
    else:
        verdict = "NO_ROE_FIELD: 三候选全 0 且对照成立（转路径②移除+告警，或评估合成路径）"

    print("[ROE_PROBE4] pos=%d neg=%d cands=%s" % (pos, neg, ",".join(cands) or "-"))
    print("[ROE_PROBE4] VERDICT=%s" % verdict)
    print("[ROE_PROBE4] end")
