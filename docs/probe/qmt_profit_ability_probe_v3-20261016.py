#coding:gbk
# docs/probe/qmt_profit_ability_probe_v3-20261016.py
# ============================================================================
# D-29 S2 探针 v3（读值版；v1/v2 形态错配后定谳形态，本轮终版）
#
# 形态定谳（v2 实测 2026-10-16 22:06）：
#   get_financial_data 返回 **pandas.core.panel.Panel**：
#     items(2) x major_axis(8743, 日期 19901219..20261016) x minor_axis(1, 字段名)
#   **无效字段名亦返回同形同维 Panel**（minor_axis 仅回显请求字段名）
#   => 形态/维度/标签均不可区分，**判别信号只在 Panel 内部值**
#      （有效字段=真实数值；无效字段=全 NaN）。
#
# v3 判据（读值）：
#   A. 正对照 s_fa_eps_basic：nonnull > 0 且 sample 为合理数值
#      （否则调用链/取值方式有误，先修探针，勿下结论）
#   B. 负对照 __probe_invalid__：nonnull == 0（全 NaN）
#   C. 候选：nonnull > 0 且 sample 合理 => **字段名确证**（走路径①补映射）
#             nonnull == 0            => 该候选无效（三候选全 0 => 转路径②）
#   探针自带 VERDICT 行（仅供参考，最终判定由 DSH 侧对表给出）。
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间任选 -> 运行 -> 回抄 [ROE_PROBE3] 全部行
# ============================================================================


def init(C):
    C.qs_probe3_done = False


def _qs_probe3_panel_stats(panel, field):
    """Panel(items=code, major=date, minor=field) -> (nonnull, cells, first_sample, last_sample)"""
    nonnull = 0
    cells = 0
    first_sample = None
    last_sample = None
    try:
        items = list(panel.items)
    except Exception:
        items = []
    for code in items:
        try:
            df = panel[code]           # DataFrame: index=date, columns=field
        except Exception:
            continue
        try:
            cols = [str(c) for c in list(df.columns)]
        except Exception:
            cols = []
        if field not in cols:
            continue
        try:
            col = df[field]
            cells += int(len(col))
            nn = int(col.notna().sum())
            nonnull += nn
            if nn > 0:
                vals = col.dropna()
                try:
                    first_sample = float(vals.iloc[0])
                except Exception:
                    pass
                try:
                    last_sample = float(vals.iloc[-1])
                except Exception:
                    pass
        except Exception:
            continue
    return nonnull, cells, first_sample, last_sample


def handlebar(C):
    if getattr(C, "qs_probe3_done", False):
        return
    C.qs_probe3_done = True

    codes = ["000001.SZ", "600000.SH"]
    start_date = "19900101"
    end_date = "20991231"

    targets = [
        ("PERSHAREINDEX.s_fa_eps_basic", "POS", "正对照（wrapper 已验证有数据）"),
        ("PERSHAREINDEX.__probe_invalid__", "NEG", "负对照（无效字段名）"),
        ("PERSHAREINDEX.s_fa_roe", "CAND1", "候选1"),
        ("PERSHAREINDEX.roe", "CAND2", "候选2"),
        ("PERSHAREINDEX.s_fa_roe_weighted", "CAND3", "候选3"),
    ]

    print("[ROE_PROBE3] begin codes=%s range=%s..%s" % (",".join(codes), start_date, end_date))

    results = {}
    for field, tag, note in targets:
        try:
            raw = C.get_financial_data([field], codes, start_date, end_date,
                                       report_type="announce_time")
        except Exception as exc:
            print("[ROE_PROBE3] %s field=%s EXC=%r note=%s" % (tag, field, exc, note))
            continue
        nonnull, cells, fs, ls = _qs_probe3_panel_stats(raw, field)
        results[tag] = (nonnull, fs, ls)
        print("[ROE_PROBE3] %s field=%s nonnull=%d cells=%d first=%s last=%s note=%s"
              % (tag, field, nonnull, cells, fs, ls, note))

    pos = results.get("POS", (0, None, None))[0]
    neg = results.get("NEG", (0, None, None))[0]
    cands = [t for t in ("CAND1", "CAND2", "CAND3") if results.get(t, (0, None, None))[0] > 0]

    if pos <= 0:
        verdict = "INSTRUMENT_FAIL: 正对照 nonnull<=0（调用链/取值方式有误，勿下结论）"
    elif neg > 0:
        verdict = "INSTRUMENT_FAIL: 负对照 nonnull>0（判据不可区分，勿下结论）"
    elif cands:
        verdict = "FIELD_CONFIRMED: %s（走路径①补映射）" % ",".join(cands)
    else:
        verdict = "NO_ROE_FIELD: 三候选全 0 且对照有效（转路径②移除+告警，或评估合成路径）"

    print("[ROE_PROBE3] pos=%d neg=%d cands=%s" % (pos, neg, ",".join(cands) or "-"))
    print("[ROE_PROBE3] VERDICT=%s" % verdict)
    print("[ROE_PROBE3] end")
