#coding:gbk
# docs/probe/qmt_shared_surface_dat_probe-20261017.py
# ============================================================================
# 共享面「QMT 锚点」回填探针 · DAT 族（数据口径）
# 目标格：DAT-01 pctChg 合成口径 ｜ DAT-02 基准收益(000300) ｜ DAT-04 前复权 fq='pre'
#          ｜ DAT-05..15 日期边界/首末日/停牌日 K 线/指数日线可用性
#
# 判据（逐项）：
#   P1 复权口径：同窗口 close 在 dividend_type=none/front 下的值差 => DAT-04 可否锚定
#   P2 日期边界：count 与 start/end 组合取到的日期序列（首末日/是否含当bar）=> DAT-05..15
#   P3 基准：000300.SH 日线可得性 + 收盘序列 => DAT-02
#   P4 停牌/缺日：窗口内行数 vs 交易日数（缺失行为）=> DAT-05..15
#   P5 pctChg 面：平台是否直接提供涨跌幅字段 => DAT-01（合成口径对照）
#
# 用法（QMT）：新建回测策略 -> 粘贴全文 -> 区间建议 2024-01-02~2024-01-31 -> 运行
#   -> 回抄全部 [DAT_PROBE] 行
# ============================================================================


def init(C):
    C.qs_dat_done = False


def _qs_dat_call(C, fields, codes, **kw):
    try:
        return C.get_market_data_ex(fields, codes, **kw), None
    except Exception as _e:
        return None, repr(_e)


def _qs_dat_dump(tag, data):
    try:
        keys = list(data.keys()) if hasattr(data, "keys") else None
    except Exception:
        keys = None
    if keys is None:
        print("[DAT_PROBE] %s type=%s repr=%s" % (tag, type(data).__name__, repr(data)[:200]))
        return
    for _c in keys[:3]:
        try:
            _df = data[_c]
            _cols = [str(x) for x in list(_df.columns)]
            _idx = [str(x) for x in list(_df.index)[:6]]
            _vals = []
            try:
                _col0 = _df[_cols[0]]
                _vals = [None if v != v else round(float(v), 4) for v in list(_col0)[:6]]
            except Exception:
                pass
            print("[DAT_PROBE] %s code=%s rows=%d cols=%s idx_head=%s vals=%s"
                  % (tag, _c, len(_df), _cols, _idx, _vals))
        except Exception as _e:
            print("[DAT_PROBE] %s code=%s dump_exc=%r" % (tag, _c, _e))


def handlebar(C):
    if getattr(C, "qs_dat_done", False):
        return
    C.qs_dat_done = True
    print("[DAT_PROBE] begin ts=bar")

    code = "600000.SH"
    # P1 复权口径（none vs front vs back）
    for _div in ("none", "front", "back"):
        _d, _e = _qs_dat_call(C, ["close"], [code], period="1d", count=5,
                              dividend_type=_div)
        if _e:
            print("[DAT_PROBE] P1 div=%s EXC=%s" % (_div, _e))
        else:
            _qs_dat_dump("P1 div=%s" % _div, _d)

    # P2 日期边界（start/end 窗口 + count 语义）
    for _kw in ({"period": "1d", "start_time": "20240102", "end_time": "20240110"},
                {"period": "1d", "count": 3},
                {"period": "1d", "count": 3, "start_time": "20240102"}):
        _d, _e = _qs_dat_call(C, ["close"], [code], dividend_type="none", **_kw)
        if _e:
            print("[DAT_PROBE] P2 kw=%s EXC=%s" % (_kw, _e))
        else:
            _qs_dat_dump("P2 kw=%s" % _kw, _d)

    # P3 基准指数日线
    _d, _e = _qs_dat_call(C, ["close"], ["000300.SH"], period="1d", count=5,
                          dividend_type="none")
    if _e:
        print("[DAT_PROBE] P3 EXC=%s" % _e)
    else:
        _qs_dat_dump("P3 idx=000300.SH", _d)

    # P4 停牌/缺日：宽窗口行数（含停牌日与否）
    _d, _e = _qs_dat_call(C, ["close", "volume"], [code], period="1d",
                          start_time="20240102", end_time="20240229", dividend_type="none")
    if _e:
        print("[DAT_PROBE] P4 EXC=%s" % _e)
    else:
        _qs_dat_dump("P4 wide_window", _d)

    # P5 pctChg 字段面
    for _f in ("pctChg", "preClose", "open", "high", "low"):
        _d, _e = _qs_dat_call(C, [_f], [code], period="1d", count=2, dividend_type="none")
        if _e:
            print("[DAT_PROBE] P5 field=%s EXC=%s" % (_f, _e))
        else:
            _qs_dat_dump("P5 field=%s" % _f, _d)

    print("[DAT_PROBE] end")
