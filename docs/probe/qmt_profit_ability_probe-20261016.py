#coding:gbk
# docs/probe/qmt_profit_ability_probe-20261016.py
# ============================================================================
# D-29 S2 探针：确证 QMT PERSHAREINDEX（关键指标）是否存在 ROE 类字段
#
# 背景：本地契约 profit_ability 表含 roe/roa/roic/... （ptrade_api.py:899 契约段），
#       QMT 财务表集（文档：CAPITALSTRUCTURE/PERSHAREINDEX/ASHAREINCOME/
#       ASHARECASHFLOW/ASHAREBALANCESHEET）无 profit_ability 同名表
#       => 候选映射 = PERSHAREINDEX，候选字段名待本探针确证。
#
# 依据 Context7 /websites/dict_thinktrader_net：
#   - innerApi/data_function.html：get_financial_data(fieldList, stockList, startDate, endDate, report_type=...)
#   - VBA/system_function.html：支持表名清单（无 profit_ability）
#
# 用法（QMT 客户端）：新建回测策略 -> 粘贴本文件全文 -> 回测区间任选（如 2023-01-01~2023-12-31）
#   -> 运行 -> 在日志中检索 [ROE_PROBE]
#   （只读探针：仅取数打印，不下单、不写文件、无窗口敏感性；盘中/盘后皆可）
#
# 判据（读数解读）：
#   A. 正对照 nonnull>0  => 调用链正确（否则先查调用方式，勿下结论）
#   B. 任一候选 nonnull>0 => 该字段名确证 => 走路径①（补映射 PERSHAREINDEX/<该字段>）
#   C. 三候选全 nonnull=0 且正对照>0 => PERSHAREINDEX 无 ROE 字段 => 走路径②（移除+告警）
#      或评估由 ASHAREINCOME/ASHAREBALANCESHEET 合成（成本高，须证口径一致）
#   D. 诱导回显若输出有效字段清单 => 直接定谳（优先于 B/C 推断）
# ============================================================================


def init(C):
    C.qs_roe_probe_done = False


def handlebar(C):
    if getattr(C, "qs_roe_probe_done", False):
        return
    C.qs_roe_probe_done = True

    codes = ["000001.SZ", "600000.SH"]
    start_date = "20171209"
    end_date = "20231204"

    candidates = [
        ("PERSHAREINDEX.s_fa_eps_basic", "正对照（已知可用：wrapper eps 映射）"),
        ("PERSHAREINDEX.s_fa_roe", "候选1（s_fa_* 命名惯例）"),
        ("PERSHAREINDEX.roe", "候选2（无前缀裸字段名）"),
        ("PERSHAREINDEX.s_fa_roe_weighted", "候选3（加权 ROE 口径）"),
        ("PERSHAREINDEX.__probe_invalid__", "诱导回显（观察平台是否列出有效字段名）"),
    ]

    print("[ROE_PROBE] begin codes=%s range=%s..%s" % (",".join(codes), start_date, end_date))

    for field, note in candidates:
        try:
            data = C.get_financial_data([field], codes, start_date, end_date,
                                        report_type="announce_time")
        except Exception as exc:
            print("[ROE_PROBE] field=%s EXC=%r note=%s" % (field, exc, note))
            continue
        try:
            rows = len(data)
        except Exception:
            rows = -1
        nonnull = -1
        try:
            nonnull = int(data.notna().sum().sum())
        except Exception:
            pass
        print("[ROE_PROBE] field=%s rows=%d nonnull=%d note=%s" % (field, rows, nonnull, note))

    print("[ROE_PROBE] end")
