#coding:gbk
# docs/probe/qmt_profit_ability_probe_v2-20261016.py
# ============================================================================
# D-29 S2 探针 v2（v1 无效读数后重造：v1 用 len()/notna() 判数据存在性，
#   而平台返回非简单 DataFrame（wrapper 经 _qs_fin_to_rows_hist 形态解析层，
#   D-9 实测形态轨：Panel/单代码 DataFrame/证券代码模式识别；hist 表为
#   PIT 多行、index=code 重复）=> v1 的 rows=2 实为 index 维，且无效字段
#   __probe_invalid__ 读数与有效字段相同（不可区分）=> v1 结论作废。
#
# v2 设计（可区分性硬要求）：
#   1) 先**转储返回形态**（type / dict keys / DataFrame columns+shape），
#      正对照与负对照各一次 => 形态本身即可区分（若有差异，直接定谳）
#   2) 逐字段统计**多形态兼容的非空计数**（DataFrame / dict[code]->DataFrame /
#      dict[code]->dict / ndarray）
#   3) 三对照齐备：正对照 s_fa_eps_basic（wrapper 已验证有数据）
#      / 负对照 __probe_invalid__ / 候选三
#   4) 判据：候选非空数 > 负对照非空数 且 正对照非空数 > 0 => 该字段名确证
#            候选 ≈ 负对照（皆 0）=> 无效
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间任选 -> 运行 -> 回抄 [ROE_PROBE2] 全部行
#   只读探针：仅取数打印，不下单、不写文件。
# ============================================================================


def init(C):
    C.qs_probe2_done = False


def _qs_probe2_shape(raw, field):
    """多形态兼容：返回 (形态标签, 总行数, 非空计数, 附加信息)"""
    # 形态 A：DataFrame（index 可能重复=code）
    if hasattr(raw, "columns"):
        try:
            cols = [str(c) for c in list(raw.columns)]
        except Exception:
            cols = []
        n = -1
        try:
            n = len(raw)
        except Exception:
            pass
        nn = -1
        try:
            nn = int(raw[field].notna().sum()) if field in cols else 0
        except Exception:
            nn = -2
        return ("DataFrame", n, nn, "cols=%s" % cols[:6])
    # 形态 B：dict（code -> DataFrame / dict / ndarray）
    if isinstance(raw, dict):
        keys = list(raw.keys())
        sub = raw[keys[0]] if keys else None
        tag = "dict[%s]" % type(sub).__name__
        nn = 0
        try:
            for _k, _v in raw.items():
                if hasattr(_v, "columns"):
                    if field in [str(c) for c in list(_v.columns)]:
                        nn += int(_v[field].notna().sum())
                elif hasattr(_v, "get"):
                    _val = _v.get(field)
                    if _val is not None:
                        try:
                            if float(_val) == float(_val):
                                nn += 1
                        except Exception:
                            pass
                elif hasattr(_v, "__len__"):
                    nn += int(len(_v))
        except Exception:
            nn = -3
        return (tag, len(keys), nn, "keys=%s" % keys[:3])
    # 形态 C：ndarray / list
    if hasattr(raw, "shape") or isinstance(raw, (list, tuple)):
        n = -1
        try:
            n = len(raw)
        except Exception:
            pass
        return ("ndarray/list", n, -1, "type=%s" % type(raw).__name__)
    return ("unknown[%s]" % type(raw).__name__, -1, -1, "")


def handlebar(C):
    if getattr(C, "qs_probe2_done", False):
        return
    C.qs_probe2_done = True

    codes = ["000001.SZ", "600000.SH"]
    start_date = "19900101"
    end_date = "20991231"

    targets = [
        ("PERSHAREINDEX.s_fa_eps_basic", "正对照（wrapper 已验证有数据）"),
        ("PERSHAREINDEX.__probe_invalid__", "负对照（无效字段名）"),
        ("PERSHAREINDEX.s_fa_roe", "候选1"),
        ("PERSHAREINDEX.roe", "候选2"),
        ("PERSHAREINDEX.s_fa_roe_weighted", "候选3"),
    ]

    print("[ROE_PROBE2] begin codes=%s range=%s..%s" % (",".join(codes), start_date, end_date))

    for field, note in targets:
        try:
            raw = C.get_financial_data([field], codes, start_date, end_date,
                                       report_type="announce_time")
        except Exception as exc:
            print("[ROE_PROBE2] field=%s EXC=%r note=%s" % (field, exc, note))
            continue
        try:
            tag, n, nn, extra = _qs_probe2_shape(raw, field)
        except Exception as exc:
            print("[ROE_PROBE2] field=%s SHAPE_EXC=%r note=%s" % (field, exc, note))
            continue
        print("[ROE_PROBE2] field=%s shape=%s n=%s nonnull=%s %s note=%s"
              % (field, tag, n, nn, extra, note))

    # 形态转储（正/负对照各一次，看平台对无效字段是否回不同形态）
    for field in ("PERSHAREINDEX.s_fa_eps_basic", "PERSHAREINDEX.__probe_invalid__"):
        try:
            raw = C.get_financial_data([field], codes, start_date, end_date,
                                       report_type="announce_time")
            print("[ROE_PROBE2] dump field=%s type=%s repr=%s"
                  % (field, type(raw).__name__, repr(raw)[:300]))
        except Exception as exc:
            print("[ROE_PROBE2] dump field=%s EXC=%r" % (field, exc))

    print("[ROE_PROBE2] end")
