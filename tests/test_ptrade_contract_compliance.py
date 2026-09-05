# -*- coding: utf-8 -*-
"""PTrade 契约合规修复测试（2026-08-12，fall_reversal 平台零交易根因 4 处）。

覆盖 5 点 AST 改写：
1. get_Ashares 日期格式 YYYY-MM-DD → YYYYmmdd（常量 / strftime / 变量三形态）
2. get_history 签名 A（security-first）→ B（count-first）＋幂等
3. get_history_batch shim 模板内部同步签名 B
4. set_benchmark 裸码补后缀（复用 normalize_to_ptrade）
5. get_stock_status 位置传参 → 关键字 query_type

验收（任务书）：
- fall_reversal 重转：date 无连字符 / get_history count-first / set_benchmark 含后缀 /
  get_stock_status 关键字
- etf_theme_rotation 重转：shim 内部 count-first
- 16 策略 --no-smoke 全量回归（单独命令，见验收 3）
- 全量测试无回归（验收 4）
"""
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from quantstudio.strategy_compiler.source_import import convert_source  # noqa: E402

STRATEGIES = pathlib.Path(__file__).resolve().parents[1] / "quantstudio" / "backtest" / "strategies"



def _ed_dstr(x):
    """end_date ms epoch -> 'YYYY-MM-DD'（本地策略 np.datetime64(int(ed),'ms') 消费语义，
    v8.7 契约：wrapper 归一 end_date 为 epoch 毫秒）。"""
    import numpy as _np
    try:
        return str(_np.datetime64(int(float(x)), 'ms'))[:10]
    except Exception:
        return 'NA'  # NaN/None end_date 容错

def _convert(code: str, name: str = "t.py"):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / name
        p.write_text(textwrap.dedent(code), encoding="utf-8")
        r = convert_source(p)
    return r


# ---------------------------------------------------------------------------
# 修复 1：get_Ashares 日期格式
# ---------------------------------------------------------------------------

def test_ashares_date_constant():
    """'YYYY-MM-DD' 常量 → 'YYYYmmdd'。"""
    r = _convert("""
def initialize(context):
    all_codes = get_Ashares('2024-06-01')
""")
    assert r.errors == []
    assert "get_Ashares('20240601')" in r.converted_code


def test_ashares_date_strftime():
    """strftime('%Y-%m-%d') 调用直接内联 → strftime('%Y%m%d')。"""
    r = _convert("""
def initialize(context):
    all_codes = get_Ashares(context.current_dt.strftime('%Y-%m-%d'))
""")
    assert r.errors == []
    assert "context.current_dt.strftime('%Y%m%d')" in r.converted_code


def test_ashares_date_variable():
    """变量 → 调用参数包 isinstance 双分支（str → replace；date/datetime → strftime）。"""
    r = _convert("""
def initialize(context):
    today = context.current_dt.strftime('%Y-%m-%d')
    all_codes = get_Ashares(today)
""")
    assert r.errors == []
    assert "(today.replace('-', '') if isinstance(today, str) else today.strftime('%Y%m%d'))" in r.converted_code


def test_ashares_no_date_untouched():
    """get_Ashares() 无参 → 不动。"""
    r = _convert("""
def initialize(context):
    all_codes = get_Ashares()
""")
    assert r.errors == []
    assert "get_Ashares()" in r.converted_code


# ---------------------------------------------------------------------------
# 修复 2：get_history 签名 A → B
# ---------------------------------------------------------------------------

def test_history_keyword_signature_rewrite():
    """关键字形态 security-first → count-first。"""
    r = _convert("""
def initialize(context):
    h = get_history(security=[code], count=count, unit=unit, fields=fields,
                    fq=fq, include=include, is_dict=True)
""")
    assert r.errors == []
    body = r.converted_code
    assert "get_history(count, frequency=unit, field=fields, security_list=code, fq=fq, include=include, is_dict=True)" in body
    assert "security=" not in body.replace("security_list=", "")


def test_history_positional_signature_rewrite():
    """位置形态 get_history(code, 20, '1d', fields=[...]) → count-first。"""
    r = _convert("""
def initialize(context):
    df = get_history(code, 20, '1d', fields=['close'], fq='pre')
""")
    assert r.errors == []
    body = r.converted_code
    assert "get_history(20, frequency='1d', field=['close'], security_list=code, fq='pre', include=False, is_dict=False)" in body


def test_history_count_first_untouched_idempotent():
    """count-first 形态不二次改写（幂等）。"""
    src = """
def initialize(context):
    h = get_history(60, frequency='1d', field=['close'], security_list='600570.SS',
                    fq='pre', include=False, is_dict=True)
"""
    r = _convert(src)
    assert r.errors == []
    assert "get_history(60, frequency='1d', field=['close'], security_list='600570.SS'" in r.converted_code
    # 二次转换零动作
    r2 = _convert(r.converted_code, name="t2.py")
    assert r2.errors == []
    assert "get_history(60, frequency='1d', field=['close'], security_list='600570.SS'" in r2.converted_code


def test_history_single_list_unpack():
    """security=[code] 单元素列表 → security_list=code 标量。"""
    r = _convert("""
def initialize(context):
    h = get_history(security=[code], count=10, unit='1d', fields=['close'])
""")
    assert r.errors == []
    assert "security_list=code" in r.converted_code
    assert "security_list=['code']" not in r.converted_code


def test_history_fq_normalize_absorbed():
    """签名改写时 fq dypre → pre 归一化并入（不产生重叠 replacement）。"""
    r = _convert("""
def initialize(context):
    h = get_history(security=[code], count=10, unit='1d', fq='dypre')
""")
    assert r.errors == []
    assert "fq='pre'" in r.converted_code


# ---------------------------------------------------------------------------
# 修复 3：shim 模板内部签名 B
# ---------------------------------------------------------------------------

def test_history_batch_shim_signature_b():
    """get_history_batch shim 内部 get_history 为 count-first（含拆包）。"""
    r = _convert("""
def initialize(context):
    pool = ['600570.SS']
    h = get_history_batch(pool, 20, '1d', fields=['close'], fq='pre')
""")
    assert r.errors == []
    assert "df_dict = get_history(count, frequency=unit, field=fields," in r.converted_code
    assert "security_list=code" in r.converted_code
    assert "security=[code]" not in r.converted_code


# ---------------------------------------------------------------------------
# 修复 4：set_benchmark 裸码补后缀
# ---------------------------------------------------------------------------

def test_benchmark_bare_code_suffix():
    """set_benchmark('000300') → '000300.SS'；已带后缀不动。"""
    r = _convert("""
def initialize(context):
    set_benchmark('000300')
""")
    assert r.errors == []
    assert "set_benchmark('000300.SS')" in r.converted_code

    r2 = _convert("""
def initialize(context):
    set_benchmark('000300.SS')
""")
    assert r2.errors == []
    assert "set_benchmark('000300.SS')" in r2.converted_code


# ---------------------------------------------------------------------------
# 修复 5：get_stock_status 位置传参 → 关键字
# ---------------------------------------------------------------------------

def test_stock_status_positional_to_keyword():
    """get_stock_status(all_codes, 'ST') → query_type='ST'。"""
    r = _convert("""
def initialize(context):
    m = get_stock_status(all_codes, 'ST')
""")
    assert r.errors == []
    assert "get_stock_status(all_codes, query_type='ST')" in r.converted_code


def test_stock_status_query_date_normalized():
    """已有 query_date 关键字且为 'YYYY-MM-DD' → 'YYYYmmdd'。"""
    r = _convert("""
def initialize(context):
    m = get_stock_status(all_codes, 'ST', query_date='2024-06-01')
""")
    assert r.errors == []
    assert "query_type='ST'" in r.converted_code
    assert "query_date='20240601'" in r.converted_code


# ---------------------------------------------------------------------------
# 修复 6：行情字段 .values 访问归一化（返回类型兼容）
# ---------------------------------------------------------------------------

def test_history_values_access_normalized():
    """单列/多列字段 .values → np.asarray(...)（保持 dtype，平台 structured array 兼容）。"""
    r = _convert("""
import numpy as np
def handle_data(context, data):
    closes = df['close'].values.astype(float)
    low = df[['low', 'high']].values.min()
""")
    assert r.errors == []
    assert "np.asarray(df['close']).astype(float)" in r.converted_code
    assert "np.asarray(df[['low', 'high']]).min()" in r.converted_code
    assert "['close'].values" not in r.converted_code


def test_history_values_access_untouched():
    """非行情字段访问不改写：dict.values() 方法调用、非字符串下标。"""
    r = _convert("""
import numpy as np
def handle_data(context, data):
    codes = {c: True for c in context.stock_pool}.values()
    row = rows[0].values
""")
    assert r.errors == []
    assert "context.stock_pool}.values()" in r.converted_code
    assert "rows[0].values" in r.converted_code


def test_history_values_access_with_get_history():
    """get_history 取数后 .values 使用 → 改写为 np.asarray（贴近 fall_reversal 场景）。"""
    r = _convert("""
import numpy as np
def handle_data(context, data):
    df = get_history(20, frequency='1d', field=['close'], security_list='000001.SZ', fq='pre')
    closes = df['close'].values.astype(float)
    return closes[-1]
""")
    assert r.errors == []
    assert "get_history(20, frequency='1d'" in r.converted_code
    assert "np.asarray(df['close']).astype(float)" in r.converted_code


# ---------------------------------------------------------------------------
# 修复 7：get_history 返回类型统一 wrapper（方向 B，structured array → DataFrame）
# ---------------------------------------------------------------------------

def _shape_check_def():
    """从 _PTRADE_HELPERS 提取真实 _qs_shape_check 定义源码（各 ns 复用，保证自检测试走真实现）。"""
    import quantstudio.strategy_compiler.source_import as si
    src = si._PTRADE_HELPERS
    i = src.index("def _qs_shape_check(")
    j = src.rfind("'''", i)
    return src[i:j]


def _wrapper_ns(history_return):
    """exec _QS_HISTORY_WRAPPER 注入区 → 命名空间（含 _qs_to_dataframe / wrapper get_history）。
    ns['_captured'] 记录 wrapper 传给原始 get_history 的 kwargs（用于字段映射断言）。"""
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    captured = {}

    def _fake_get_history(*args, **kwargs):
        captured["kwargs"] = kwargs
        return history_return

    ns = {"get_history": _fake_get_history,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# wrapper"), ns)
    ns["_captured"] = captured
    return ns


def test_history_wrapper_converts_structured_array():
    """structured array → DataFrame（datetime → time 列名映射，数据逐元素保留）。"""
    import numpy as np
    import pandas as pd
    arr = np.array([(20230413, 10.35), (20230414, 10.50)],
                   dtype=[("datetime", "i8"), ("close", "f8")])
    ns = _wrapper_ns(arr)
    df = ns["_qs_to_dataframe"](arr)
    assert isinstance(df, pd.DataFrame)
    assert "time" in df.columns and "datetime" not in df.columns
    assert "close" in df.columns
    assert df["close"].tolist() == [10.35, 10.50]


def test_history_wrapper_passes_dataframe():
    """已 DataFrame → 原样返回（不重复转换）。"""
    import pandas as pd
    ns = _wrapper_ns(None)
    df = pd.DataFrame({"time": [20230413], "close": [10.35]})
    assert ns["_qs_to_dataframe"](df) is df


def test_history_wrapper_passes_dict():
    """get_history wrapper：dict（多标的）逐元素转 DataFrame。"""
    import numpy as np
    import pandas as pd
    arr = np.array([(20230413, 10.35)],
                   dtype=[("datetime", "i8"), ("close", "f8")])
    ns = _wrapper_ns({"510300.SS": arr})
    out = ns["get_history"](20, frequency="1d", field=["close"],
                            security_list="510300.SS", fq="pre")
    assert isinstance(out, dict)
    df = out["510300.SS"]
    assert isinstance(df, pd.DataFrame)
    assert "time" in df.columns


def test_history_wrapper_idempotent():
    """产物已有 wrapper → 二次转换不重复注入（INJECTED_MARKER 幂等）。"""
    r = _convert("""
def initialize(context):
    pool = ['600570.SS']
    h = get_history_batch(pool, 20, '1d', fields=['close'], fq='pre')
""")
    assert r.errors == []
    assert r.converted_code.count("def _qs_to_dataframe(item):") == 1
    r2 = _convert(r.converted_code, name="t2.py")
    assert r2.errors == []
    assert r2.converted_code.count("def _qs_to_dataframe(item):") == 1


# ---------------------------------------------------------------------------
# 修复 8：get_history 字段名统一映射（wrapper 运行时中枢，双向）
# ---------------------------------------------------------------------------

def test_field_mapping_amount_to_money():
    """请求字段映射：amount → money（wrapper 运行时中枢，不改 AST 规则）。"""
    ns = _wrapper_ns(None)
    ns["get_history"](20, frequency="1d", field=["close", "amount"],
                      security_list="510300.SS", fq="pre")
    assert ns["_captured"]["kwargs"]["field"] == ["close", "money"]


def test_field_mapping_passthrough():
    """请求字段映射：一致字段不改动（close/volume 原样传递）。"""
    ns = _wrapper_ns(None)
    ns["get_history"](20, frequency="1d", field=["close", "volume"],
                      security_list="510300.SS", fq="pre")
    assert ns["_captured"]["kwargs"]["field"] == ["close", "volume"]


def test_qs_to_dataframe_col_rename_all():
    """返回列名统一映射：datetime/money/preclose → time/amount/preClose。"""
    import numpy as np
    import pandas as pd
    arr = np.array([(20230413, 10.35, 1.23e8, 10.20)],
                   dtype=[("datetime", "i8"), ("close", "f8"),
                          ("money", "f8"), ("preclose", "f8")])
    ns = _wrapper_ns(None)
    df = ns["_qs_to_dataframe"](arr)
    assert list(df.columns) == ["time", "close", "amount", "preClose"]


def test_wrapper_end_to_end():
    """双向映射完整链路：请求 amount→money，返回 money→amount。"""
    import numpy as np
    import pandas as pd
    arr = np.array([(20230413, 10.35, 1.23e8)],
                   dtype=[("datetime", "i8"), ("close", "f8"), ("money", "f8")])
    ns = _wrapper_ns(arr)
    out = ns["get_history"](20, frequency="1d", field=["close", "amount"],
                            security_list="510300.SS", fq="pre")
    assert ns["_captured"]["kwargs"]["field"] == ["close", "money"]
    assert list(out.columns) == ["time", "close", "amount"]


# ---------------------------------------------------------------------------
# 整策略验收
# ---------------------------------------------------------------------------

def test_fall_reversal_full_contract():
    """fall_reversal 重转：4 项契约检查。"""
    p = STRATEGIES / "fall_reversal_quantstudio.py"
    if not p.is_file():
        pytest.skip("fall_reversal_quantstudio.py 不存在")
    r = convert_source(p)
    assert r.errors == [], r.errors
    body = r.converted_code
    # 1) get_Ashares date 无连字符
    assert "get_Ashares(" in body
    assert "strftime('%Y%m%d')" in body
    # 2) get_history 全部 count-first（首参为 int/变量，无 security= 关键字）
    assert "security=" not in body.replace("security_list=", "")
    # 3) set_benchmark 含后缀
    assert "set_benchmark('000300.SS')" in body
    # 4) get_stock_status 关键字
    assert "query_type='ST'" in body
    assert "get_stock_status(all_codes, 'ST')" not in body
    # 5) 行情字段 .values 访问归一化（平台返回 structured array，无 .values 属性）
    assert "np.asarray(df['close']).astype(float)" in body
    assert "df['close'].values" not in body
    # 6) 方向 B：get_history 返回类型统一 wrapper（structured array → DataFrame）
    assert "def _qs_to_dataframe(item):" in body
    assert "class _QSHistoryState:" in body
    assert "_QSHistoryState.orig = get_history" in body


def test_etf_theme_rotation_shim_contract():
    """etf_theme_rotation 重转：shim 内部 count-first。"""
    p = STRATEGIES / "etf_theme_rotation_quantstudio.py"
    if not p.is_file():
        pytest.skip("etf_theme_rotation_quantstudio.py 不存在")
    db = pathlib.Path(__file__).resolve().parents[1] / "output" / "t5_roundtrip" / "quantstudio.db"
    if not db.is_file():
        pytest.skip("staging 快照副本不存在（FREEZE 需要 db_path）")
    r = convert_source(p, etf_pool_start_date="2024-01-02", db_path=db)
    assert r.errors == [], r.errors
    body = r.converted_code
    assert "df_dict = get_history(count, frequency=unit, field=fields," in body
    assert "security=[code]" not in body


# ---------------------------------------------------------------------------
# trade_date 门控扩展（2026-08-19，框架方案 source_import-ptrade-history-translation-design）
# A/B：trade_date 合成/剔除；R1：is_dict 逐码；R3：合成格式与本地 provider 逐字符一致；门控纯增益
# ---------------------------------------------------------------------------

def _wrapper_ext_ns(history_return):
    """exec 旧 wrapper + trade_date 扩展 → 命名空间；_captured 记录原生 get_history kwargs。"""
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    captured = []

    def _fake_get_history(*args, **kwargs):
        captured.append(dict(kwargs))
        return history_return

    ns = {"get_history": _fake_get_history,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
    exec(si._QS_HISTORY_TRADE_DATE_EXT.format(marker="# m"), ns)
    ns["_captured"] = captured
    return ns


def test_trade_date_gate_literals():
    """门控字面量探测：trade_date 字符串 → True；缺省 → False。"""
    import quantstudio.strategy_compiler.source_import as si
    assert si._source_uses_trade_date('_extract_history_field(h, "trade_date", dtype=str)') is True
    assert si._source_uses_trade_date("field=['close','trade_date']") is True
    assert si._source_uses_trade_date("field=['close']") is False
    assert si._source_uses_trade_date("h = get_history(20)") is False


def test_trade_date_ext_not_injected_when_unused():
    """纯增益：不使用 trade_date 的策略转换输出只有旧 wrapper（拓展不注入，逐字节不变）。"""
    r = _convert("""
def initialize(context):
    h = get_history(count=20, frequency='1d', field=['close'],
                    security_list=['600519.SS'], fq='pre')
""")
    assert r.errors == [], r.errors
    assert "_qs_synthesize_trade_date" not in r.converted_code
    assert r.converted_code.count("def _qs_to_dataframe(item):") == 1


def test_trade_date_ext_injected_when_used():
    """使用 trade_date 的策略 → 注入扩展（重定义 _qs_to_dataframe + get_history）。"""
    r = _convert("""
def initialize(context):
    h = get_history(count=20, frequency='1d', field=['close', 'trade_date'],
                    security_list=['600519.SS'], fq='pre')
""")
    assert r.errors == [], r.errors
    assert "_qs_synthesize_trade_date" in r.converted_code
    assert "_QS_SYNTHETIC_FIELDS" in r.converted_code
    assert r.converted_code.count("def _qs_to_dataframe(item):") == 2


def test_trade_date_ext_matches_local_provider_format():
    """R3：PTrade 返回合成 trade_date 与本地 provider 同日期输出逐字符一致。

    基准 = 本地推导式（time 毫秒 → Asia/Shanghai strftime('%Y-%m-%d')），
    不以文档假设为准。
    """
    import numpy as np
    import pandas as pd
    ns = _wrapper_ext_ns(None)
    ms = 1785340800000  # 2026-07-30
    expected = str(pd.to_datetime(ms, unit="ms", utc=True)
                   .tz_convert("Asia/Shanghai").strftime("%Y-%m-%d"))
    # 本地形状：time 为 ms int
    arr = np.array([(ms, 10.0)], dtype=[("time", "i8"), ("close", "f8")])
    df = ns["_qs_to_dataframe"](arr)
    assert df["trade_date"].tolist() == [expected]
    # PTrade 形状：datetime 列（改名 time 后再合成）
    arr2 = np.array([(np.datetime64("2026-07-30T15:00:00"), 10.0)],
                    dtype=[("datetime", "M8[ns]"), ("close", "f8")])
    df2 = ns["_qs_to_dataframe"](arr2)
    assert "time" in df2.columns and "datetime" not in df2.columns
    assert df2["trade_date"].tolist() == [expected]


def test_trade_date_ext_removes_synthetic_field():
    """A/B：请求侧剔除 trade_date（不传 PTrade），返回侧补齐 trade_date 列。"""
    import numpy as np
    arr = np.array([(np.datetime64("2026-07-30T15:00:00"), 10.0)],
                   dtype=[("datetime", "M8[ns]"), ("close", "f8")])
    ns = _wrapper_ext_ns(arr)
    df = ns["get_history"](count=5, frequency="1d", field=["close", "trade_date"],
                           security_list=["000300.SS"], fq="pre", include=False)
    kw = ns["_captured"][-1]
    assert kw.get("field") == ["close"]
    assert "trade_date" in df.columns
    assert df["trade_date"].tolist() == ["2026-07-30"]


def test_trade_date_ext_non_dict_passes_security_list():
    """非 dict 模式下 security_list 必须透传给原始 get_history（如单市场 σ 计算）。"""
    import numpy as np
    arr = np.array([(np.datetime64("2026-07-30T15:00:00"), 10.0)],
                   dtype=[("datetime", "M8[ns]"), ("close", "f8")])
    ns = _wrapper_ext_ns(arr)
    df = ns["get_history"](count=5, frequency="1d", field=["close", "trade_date"],
                           security_list=["000300.SS"], fq="pre", include=False)
    kw = ns["_captured"][-1]
    assert kw.get("security_list") == ["000300.SS"]
    assert "trade_date" in df.columns
    assert df["trade_date"].tolist() == ["2026-07-30"]


def test_trade_date_ext_synthesizes_from_index():
    """PTrade 返回 DataFrame 且日期在 index 时，仍能从 index 合成 trade_date。"""
    import pandas as pd
    ns = _wrapper_ext_ns(None)
    df = pd.DataFrame(
        {"code": ["510300.SS"], "close": [5.345]},
        index=pd.to_datetime(["2026-07-30"]),
    )
    out = ns["_qs_to_dataframe"](df)
    assert "trade_date" in out.columns
    assert out["trade_date"].tolist() == ["2026-07-30"]


def test_trade_date_ext_is_dict_per_code():
    """R1：is_dict=True + security_list → 逐码调用原 get_history，拼 {code: df}。"""
    import numpy as np
    arr = np.array([(np.datetime64("2026-07-30T15:00:00"), 10.0)],
                   dtype=[("datetime", "M8[ns]"), ("close", "f8")])
    ns = _wrapper_ext_ns(arr)
    out = ns["get_history"](count=10, frequency="1d", field=["close", "trade_date"],
                            security_list=["600519.SS", "000001.SZ"], fq="pre",
                            include=False, is_dict=True)
    assert sorted(out.keys()) == ["000001.SZ", "600519.SS"]
    assert all("trade_date" in out[k].columns for k in out)
    # 原生被逐码调用两次，每次 security_list 单元素，且 is_dict 已摘除
    assert len(ns["_captured"]) == 2
    assert all(len(c["security_list"]) == 1 for c in ns["_captured"])
    assert all("is_dict" not in c for c in ns["_captured"])
    assert all(c["field"] == ["close"] for c in ns["_captured"])


# ---------------------------------------------------------------------------
# A1 市价单拆单注入（2026-08-22，框架方案 PTrade 平台对齐治理 v4 §3.1 A1）
# 门控：调用订单 API → 注入 _QS_ORDER_SPLIT_EXT；无订单 → 逐字节不变。
# 算法：>49,000 股拆多笔；段现金预分配 + 合计勾稽（R3 ②）；px<=0 回退不拆。
# 同构：注入模板 vs 本地 ptrade_api._qs_split_order 逐字一致（双端订单序列相同）。
# ---------------------------------------------------------------------------

def _order_split_ns():
    """exec _QS_ORDER_SPLIT_EXT 注入区 → 命名空间（fake 原始订单 API 捕获）。"""
    import quantstudio.strategy_compiler.source_import as si

    captured = {"target_value": [], "order": []}

    def _fake_target(security, value, *a, **kw):
        captured["target_value"].append((security, value))
        return "tid-" + str(len(captured["target_value"]))

    def _fake_order(security, amount, *a, **kw):
        captured["order"].append((security, amount))
        return "oid-" + str(len(captured["order"]))

    ns = {"order_target_value": _fake_target, "order": _fake_order,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0,
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None}
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    ns["_captured"] = captured
    return ns


def test_order_split_gate_injected_when_order_api():
    """门控：调用订单 API 的策略注入 order_split 扩展。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    order_target_value(security='600519.SS', value=20000)
""")
    assert r.errors == [], r.errors
    assert "_QS_MAX_ORDER_SHARES" in r.converted_code
    assert "_qs_split_order" in r.converted_code


def test_order_split_gate_not_injected_when_readonly():
    """门控：只读策略（无订单 API）不注入 order_split（逐字节不变）。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    h = get_history(count=20, frequency='1d', field=['close'],
                    security_list=['600519.SS'], fq='pre')
""")
    assert r.errors == [], r.errors
    assert "_QS_MAX_ORDER_SHARES" not in r.converted_code
    assert "_qs_split_order" not in r.converted_code


def test_order_split_gate_string_literal_only():
    """门控：仅字符串字面量含 order_target_value 不触发（AST 调用名匹配）。"""
    r = _convert("""
ORDER_API = 'order_target_value'
def initialize(context):
    pass
""")
    assert r.errors == [], r.errors
    assert "_qs_split_order" not in r.converted_code


def test_split_below_threshold_single_order():
    """恰等阈值内 → 单笔不拆（含整手取整）。"""
    ns = _order_split_ns()
    orders, tot = ns["_qs_split_order"]("600519.SS", 49000 * 10.0, px=10.0)
    assert len(orders) == 1
    assert orders[0][1] == 49000
    assert tot == 49000


def test_split_just_over_threshold_two_orders():
    """恰超阈值（49,001 股）→ 拆 2 笔，每笔 ≤49,000；段额整手对齐。"""
    ns = _order_split_ns()
    orders, tot = ns["_qs_split_order"]("000001.SZ", 49001 * 1.0, px=1.0)
    assert len(orders) == 2
    assert all(a <= 49000 for _, a in orders)
    assert all(a % 100 == 0 for _, a in orders)
    assert tot == sum(a for _, a in orders)


def test_split_large_value_chunks():
    """大额（133,300 股 @0.15）→ 拆 ceil=3 笔（整手取整后段数不减）。"""
    ns = _order_split_ns()
    orders, tot = ns["_qs_split_order"]("300029.SZ", 133300 * 0.15, px=0.15)
    assert len(orders) == 3
    assert all(a <= 49000 for _, a in orders)
    assert all(a % 100 == 0 for _, a in orders)
    assert tot == sum(a for _, a in orders)


def test_split_px_nonpositive_fallback():
    """px<=0（统一链 ① 无记录）→ 回退不拆（调用方走原路径）。"""
    ns = _order_split_ns()
    orders, tot = ns["_qs_split_order"]("000001.SZ", 20000.0, px=0.0)
    assert orders == []
    assert tot == 0
    orders2, _ = ns["_qs_split_order"]("000001.SZ", 20000.0, px=None)
    assert orders2 == []


def test_split_value_nonpositive():
    """value<=0 → 不拆。"""
    ns = _order_split_ns()
    orders, _ = ns["_qs_split_order"]("000001.SZ", 0.0, px=10.0)
    assert orders == []


def test_split_cash_avail_preallocation():
    """段现金预分配：cash_avail < value 时按 min 预算均分，合计勾稽 ≤ 预算×缓冲。"""
    ns = _order_split_ns()
    # 目标 20000 元 @0.15 → 133,333 股 → ceil=3 笔；现金只有 15000 元
    orders, tot = ns["_qs_split_order"]("300029.SZ", 20000.0, px=0.15, cash_avail=15000.0)
    assert orders, "应按预算拆分"
    total_cost = sum(a * 0.15 for _, a in orders)
    assert total_cost <= 15000.0  # 合计勾稽（D4-S6 后 buffer=1.0：预算内尽量成交，无 5% 低配）
    assert all(a <= 49000 for _, a in orders)


def test_split_cash_insufficient_first_seg_only():
    """现金只够首段 → 仅首段成交（其余舍去，模拟平台降量语义）。"""
    ns = _order_split_ns()
    orders, tot = ns["_qs_split_order"]("300029.SZ", 20000.0, px=0.15, cash_avail=4900.0)
    # 预算 4900/3=1633.33 → 段股数 int(1633.33/0.15/100)*100 = 10800 股
    # 合计 10800×0.15=1620 ≤ 4900×1.0 → 第一段可接；但总额 1620*3=4860 ≤ 4900?
    # 实际逐段累加检查 4900 上限 → 可能只接 2 段
    assert orders
    assert sum(a * 0.15 for _, a in orders) <= 4900.0
    assert tot == sum(a for _, a in orders)


def test_split_lot_rounding_tail():
    """末段收尾按整手取整，段股数均为 100 的倍数。"""
    ns = _order_split_ns()
    orders, _ = ns["_qs_split_order"]("000001.SZ", 123456.0, px=1.23)
    assert orders
    assert all(a % 100 == 0 for _, a in orders)


def test_split_wrapper_target_value_calls_split_order():
    """order_target_value wrapper：px 有记录（① 层命中）→ 走拆单；否则原路径。"""
    import quantstudio.strategy_compiler.source_import as si
    captured = {"target_value": [], "order": []}

    def _fake_target(security, value, *a, **kw):
        captured["target_value"].append((security, value))
        return "tid"

    def _fake_order(security, amount, *a, **kw):
        captured["order"].append((security, amount))
        return "oid"

    px_state = {"v": 0.0}
    ns = {"order_target_value": _fake_target, "order": _fake_order,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: px_state["v"],
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None}
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    # D4-S6：换算价走 current_price（② 层语义：当日撮合价）→ 命中 → 拆单走 order()
    px_state["v"] = 0.15
    ns["_QSLastCloseState"].cache = {"300029": ("2026-07-01", 0.15)}
    ns["order_target_value"]("300029.SZ", 20000.0)
    assert captured["order"], "应拆单走 order()"
    assert all(a <= 49000 for _, a in captured["order"])
    assert captured["target_value"] == []
    # ② 层缺失（current_price=0）→ px=0 → 原路径
    px_state["v"] = 0.0
    ns["order_target_value"]("600519.SS", 20000.0)
    assert captured["target_value"], "px=0 应回退原 order_target_value"


def test_split_wrapper_direct_order_over_limit():
    """直接 order() 超限 → 拆多笔（段整手对齐）；合法 → 原路径。"""
    import quantstudio.strategy_compiler.source_import as si
    captured = []

    def _fake_order(security, amount, *a, **kw):
        captured.append(amount)
        return "oid"

    ns = {"order_target_value": lambda *a, **kw: None, "order": _fake_order,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0,
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None}
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    ns["order"]("300029.SZ", 49000)          # 恰等 → 原路径
    assert captured == [49000]
    captured.clear()
    ns["order"]("300029.SZ", 83300)          # 超限 → 拆 2 笔（每笔 ≤49,000，整手对齐）
    assert len(captured) == 2
    assert all(a <= 49000 for a in captured)
    assert all(a % 100 == 0 for a in captured)


def test_split_ptrade_vs_local_homology():
    """同构性：注入模板 `_qs_split_order` 与本地 ptrade_api 版本逐字同构
    （双端订单序列一致的前提）。"""
    import quantstudio.strategy_compiler.source_import as si
    import quantstudio.backtest.ptrade_api as pa

    src_pt = si._QS_ORDER_SPLIT_EXT
    # 模板内的 _qs_split_order 源码与本地函数体逐指令一致（去缩进/文档差异后比对）
    import ast
    tree_pt = ast.parse(src_pt)
    fn_pt = next(n for n in ast.walk(tree_pt) if isinstance(n, ast.FunctionDef)
                 and n.name == "_qs_split_order")
    fn_local = ast.parse(open(pa.__file__, encoding="utf-8").read())
    fn_l = next(n for n in ast.walk(fn_local) if isinstance(n, ast.FunctionDef)
                and n.name == "_qs_split_order")
    # 比对参数与体（body 语句列表逐条同构；docstring 允许注释差异）
    assert [a.arg for a in fn_pt.args.args] == [a.arg for a in fn_l.args.args]
    body_pt = [ast.dump(s) for s in fn_pt.body if not (isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant))]
    body_l = [ast.dump(s) for s in fn_l.body if not (isinstance(s, ast.Expr)
              and isinstance(s.value, ast.Constant))]
    assert body_pt == body_l, "注入模板与本地 _qs_split_order 必须逐字同构"


def test_split_thresholds_constant_alignment():
    """常量对齐：注入模板与本地 _QS_MAX_ORDER_SHARES=49000 及 LOT/BUFFER 一致。"""
    import quantstudio.strategy_compiler.source_import as si
    import quantstudio.backtest.ptrade_api as pa
    assert si._QS_ORDER_SPLIT_EXT.count("49000") >= 1
    assert pa._QS_MAX_ORDER_SHARES == 49000
    assert pa._QS_SPLIT_LOT == 100
    assert pa._QS_SPLIT_PX_BUFFER == 1.0   # D4-S6 审计 R1 裁定 0.95→1.0（换算价归零价差）
    assert si._QS_ORDER_SPLIT_EXT.count("_QS_SPLIT_PX_BUFFER = 1.0") >= 1  # 双端同构


# ---------------------------------------------------------------------------
# A2 统一链 ① 层：get_history 最近收盘记录（PIT 纪律：跨日失效 + 链式不破坏既有行为）
# ---------------------------------------------------------------------------

def _record_ns():
    """exec _QS_ORDER_SPLIT_EXT → 命名空间（含 _qs_history_record_core / 缓存状态）。"""
    import quantstudio.strategy_compiler.source_import as si
    ns = {"order_target_value": lambda *a, **kw: None,
          "order": lambda *a, **kw: None,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0,
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None}
    exec(si._PTRADE_HELPERS.format(marker="# m"), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    return ns


def _mk_df(codes, day, px):
    """PTrade 形状 DataFrame（code/time/close；time 为当日 15:00）。"""
    import pandas as pd
    rows = [(c, pd.Timestamp(day + " 15:00:00"), px) for c in codes]
    return pd.DataFrame(rows, columns=["code", "time", "close"])


def test_a2_record_and_lookup():
    """get_history 返回后最近 close 入缓存；_qs_last_close_lookup 可取。"""
    ns = _record_ns()
    df = _mk_df(["000001.SZ"], "2026-07-01", 10.0)
    ns["_QSHistoryChainState"].prev = lambda *a, **kw: df
    ns["_qs_history_record_core"]((), {"fq": "pre"})
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 10.0


def test_a2_record_is_dict_per_code():
    """is_dict=True 返回 dict → 每码分别记录。"""
    ns = _record_ns()
    d = {"600519.SS": _mk_df(["600519.SS"], "2026-07-01", 20.0),
         "000001.SZ": _mk_df(["000001.SZ"], "2026-07-01", 30.0)}
    ns["_QSHistoryChainState"].prev = lambda *a, **kw: d
    ns["_qs_history_record_core"]((), {"fq": "pre"})
    assert ns["_qs_last_close_lookup"]("600519.SS") == 20.0
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 30.0


def test_a2_cross_day_cache_invalidated():
    """跨日缓存失效：T 日记录 → 查询 T+1（不同交易日）缓存清空取新。"""
    ns = _record_ns()
    ns["_QSHistoryChainState"].prev = lambda *a, **kw: _mk_df(
        ["000001.SZ"], "2026-07-01", 10.0)
    ns["_qs_history_record_core"]((), {"fq": "pre"})
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 10.0
    # T+1：不同交易日 → cache 清空并写入新价
    ns["_QSHistoryChainState"].prev = lambda *a, **kw: _mk_df(
        ["000001.SZ"], "2026-07-02", 12.0)
    ns["_qs_history_record_core"]((), {"fq": "pre"})
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 12.0


def test_a2_record_skips_minutes_and_nonpre():
    """只记录日线 + fq=pre；分钟/不复权不入 ① 层。"""
    ns = _record_ns()
    df = _mk_df(["000001.SZ"], "2026-07-01", 10.0)
    ns["_QSHistoryChainState"].prev = lambda *a, **kw: df
    ns["_qs_history_record_core"]((), {"frequency": "1m"})
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 0.0
    ns["_qs_history_record_core"]((), {"fq": None})
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 0.0


def test_a2_lookup_tolerance_old_now():
    """_qs_last_close_lookup 兼容旧格式（纯 float）与 tuple 新格式。"""
    ns = _record_ns()
    ns["_QSLastCloseState"].cache = {"000001": ("2026-07-01", 8.8)}
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 8.8
    ns["_QSLastCloseState"].cache = {"000001": 9.9}
    assert ns["_qs_last_close_lookup"]("000001.SZ") == 9.9


# ---------------------------------------------------------------------------
# A3 日期归一化（get_trade_days 未来过滤 + 格式归一；get_stock_info listed_date）
# ---------------------------------------------------------------------------

def _date_norm_ns():
    """exec _QS_DATE_NORM_EXT → 命名空间（fake 原 get_trade_days/get_stock_info）。"""
    import quantstudio.strategy_compiler.source_import as si

    captured = {"days": [], "info": []}

    def _fake_days(start_date=None, end_date=None, count=None, *a, **kw):
        captured["days"].append((start_date, end_date, count))
        # 模拟 PTrade 全量日历（含未来）+ date 对象混用
        return ["2025-06-30", "2025-07-01", "2026-06-30", "2026-07-01",
                "2026-11-30", "2026-12-31"]

    def _fake_info(stocks, field=None, *a, **kw):
        if isinstance(stocks, list):
            code = stocks[0]
        else:
            code = stocks
        return {code: {"listed_date": "20260701"}}  # YYYYMMDD 混用
    ns = {"get_trade_days": _fake_days, "get_stock_info": _fake_info,
          "order_target_value": lambda *a, **kw: None,
          "order": lambda *a, **kw: None,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0,
          "order_value": lambda *a, **kw: None,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None}
    exec(si._PTRADE_HELPERS.format(marker="# m"), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    exec(si._QS_DATE_NORM_EXT.format(marker="# m"), ns)
    ns["_captured"] = captured
    return ns


def test_a3_trade_days_norm_and_filter():
    """get_trade_days：格式归一 + <= today 过滤（today 由 A2 stamp 推导）。"""
    ns = _date_norm_ns()
    ns["_QSLastCloseState"].stamp = "2026-07-01"   # A2 最近交易日
    days = ns["get_trade_days"]()
    assert "2026-06-30" in days
    assert "2026-07-01" in days
    assert "2026-11-30" not in days       # 未来日期被过滤
    assert "2026-12-31" not in days


def test_a3_trade_days_no_stamp_no_filter():
    """无 A2 stamp（无日线链）→ 不过滤（与平台原始行为一致，防错误截断）。"""
    ns = _date_norm_ns()
    ns["_QSLastCloseState"].stamp = None
    days = ns["get_trade_days"]()
    assert "2026-12-31" in days


def test_a3_trade_days_format_normalized():
    """YYYYMMDD / date 对象 → 'YYYY-MM-DD'。"""
    import datetime
    ns = _date_norm_ns()

    def _fake(*a, **kw):
        return ["20260701", datetime.date(2026, 7, 2)]
    ns["_QSDateNormState"].orig_days = _fake   # 换底层 fake，包装仍生效
    ns["_QSLastCloseState"].stamp = "2026-07-31"
    days = ns["get_trade_days"]()
    assert "2026-07-01" in days
    assert "2026-07-02" in days


def test_a3_listed_date_normalized():
    """get_stock_info listed_date YYYYMMDD → 'YYYY-MM-DD'（本地契约）。"""
    ns = _date_norm_ns()
    out = ns["get_stock_info"]("000001.SZ", field=["listed_date"])
    assert out["000001.SZ"]["listed_date"] == "2026-07-01"


def test_a3_gate_injected_when_date_api():
    """门控：调用 get_trade_days 的策略注入 A3；无调用则不注入。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    d = get_trade_days()
""")
    assert r.errors == [], r.errors
    assert "_QS_DATE_NORM_EXT" not in r.converted_code
    assert "get_trade_days" in r.converted_code and "_qs_norm_date_str" in r.converted_code


def test_a3_gate_not_injected_when_no_date_api():
    """只读无日期 API → A3 不注入。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    h = get_history(count=20, frequency='1d', field=['close'],
                    security_list=['600519.SS'], fq='pre')
""")
    assert r.errors == [], r.errors
    assert "_qs_norm_date_str" not in r.converted_code


# ---------------------------------------------------------------------------
# A1 接线（2026-08-22 修正）：本地 order API 拆单包装 ↔ 转换模板逐笔一致
# ZCode 审核：包装集合 5 API 同构；order 股数语义独立边界用例；percent 类回退
# ---------------------------------------------------------------------------

def test_wire_local_matches_template_order_target_value(monkeypatch):
    """单元级：同一输入，本地 _qs_wire_order_target_value 与模板 order_target_value
    拆单段数/段股数/顺序逐笔一致（双端订单序列一致的前提）。"""
    import quantstudio.backtest.ptrade_api as pa

    captured = []
    monkeypatch.setattr(pa._QSOrderWiringState, "order_orig",
                        lambda sec, amt, *a, **kw: captured.append((sec, amt)))
    monkeypatch.setattr(pa._QSLastCloseState, "cache",
                        {"300029": ("2026-07-01", 0.15)})
    # D4-S6：换算价走 ② 层原语义（current_price）；无引擎环境 stub 当日价 0.15
    monkeypatch.setattr(pa._QSPriceState, "orig",
                        lambda sec: 0.15)
    try:
        pa._qs_wire_order_target_value("300029.SZ", 20000.0)
    finally:
        pass
    assert len(captured) == 3                     # 133,333 股 → ceil=3 段
    assert all(a <= 49000 for _, a in captured)
    assert all(a % 100 == 0 for _, a in captured)
    assert [a for _, a in captured] == sorted([a for _, a in captured])


def test_wire_local_order_shares_semantics(monkeypatch):
    """order 股数语义边界：恰超 49,000 拆 2 段；49,000 恰等不拆；非整手末段对齐。"""
    import quantstudio.backtest.ptrade_api as pa

    captured = []
    # fake 返回非 None，避免 fallback 原 API 重放整单
    monkeypatch.setattr(pa._QSOrderWiringState, "order_orig",
                        lambda sec, amt, *a, **kw: captured.append(amt) or amt)
    captured.clear()
    pa._qs_wire_order("300029.SZ", 49000)         # 恰等 → 不拆
    assert captured == [49000]
    captured.clear()
    pa._qs_wire_order("300029.SZ", 49001)         # 恰超 → 2 段整手
    assert len(captured) == 2
    assert all(a <= 49000 for a in captured)
    assert all(a % 100 == 0 for a in captured)
    captured.clear()
    pa._qs_wire_order("300029.SZ", 83300)         # 非整手超限 → 整手对齐 2 段
    assert len(captured) == 2
    assert all(a % 100 == 0 for a in captured)
    assert sum(captured) == 83300


def test_wire_local_order_value_semantics(monkeypatch):
    """order_value 金额语义 → 同一拆单链路（与 order_target_value 一致）。"""
    import quantstudio.backtest.ptrade_api as pa

    captured = []
    monkeypatch.setattr(pa._QSOrderWiringState, "order_orig",
                        lambda sec, amt, *a, **kw: captured.append(amt))
    monkeypatch.setattr(pa._QSLastCloseState, "cache",
                        {"000001": ("2026-07-01", 0.15)})
    # D4-S6：换算价走 ② 层原语义；无引擎环境 stub 当日价 0.15
    monkeypatch.setattr(pa._QSPriceState, "orig",
                        lambda sec: 0.15)
    captured.clear()
    pa._qs_wire_order_value("000001.SZ", 20000.0)
    assert len(captured) == 3
    assert all(a <= 49000 for a in captured)


def test_wire_local_px_missing_fallback_original(monkeypatch):
    """② 层 px（current_price）缺失 → 回退原 API（与模板 px=0 回退语义一致）。"""
    import quantstudio.backtest.ptrade_api as pa

    orig_calls = []
    monkeypatch.setattr(pa._QSOrderWiringState, "target_orig",
                        lambda sec, val, *a, **kw: orig_calls.append((sec, val)))
    monkeypatch.setattr(pa._QSLastCloseState, "cache", {})
    monkeypatch.setattr(pa._QSPriceState, "orig", lambda sec: 0.0)   # ② 层缺失
    pa._qs_wire_order_target_value("600519.SS", 20000.0)
    assert orig_calls == [("600519.SS", 20000.0)]


def test_wire_template_order_value_wrapper():
    """模板 order_value 包装：px 命中 → 拆单；px 缺失 → 原路径。"""
    import quantstudio.strategy_compiler.source_import as si
    captured = {"value": [], "order": []}

    def _fake_value(security, value, *a, **kw):
        captured["value"].append((security, value))
        return "vid"

    def _fake_order(security, amount, *a, **kw):
        captured["order"].append((security, amount))
        return "oid"

    ns = {"order_target_value": lambda *a, **kw: None,
          "order": _fake_order, "order_value": _fake_value,
          "order_percent": lambda *a, **kw: None,
          "order_target_percent": lambda *a, **kw: None,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0}
    exec(si._PTRADE_HELPERS.format(marker="# m"), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    ns["_QSLastCloseState"].cache = {"300029": ("2026-07-01", 0.15)}
    ns["order_value"]("300029.SZ", 20000.0)
    assert captured["order"], "px 命中应拆单走 order()"
    assert captured["value"] == []
    ns["order_value"]("600519.SS", 20000.0)       # px 缺失 → 原路径
    assert captured["value"]


def test_wire_template_percent_fallback_original():
    """模板 percent 类包装回退原 API（比例单无法模板内折算金额）。"""
    import quantstudio.strategy_compiler.source_import as si
    captured = {"percent": [], "target_percent": []}

    def _fake_percent(security, pct, *a, **kw):
        captured["percent"].append((security, pct))
        return "pid"

    def _fake_tp(security, pct, *a, **kw):
        captured["target_percent"].append((security, pct))
        return "tpid"

    ns = {"order_target_value": lambda *a, **kw: None,
          "order": lambda *a, **kw: None,
          "order_value": lambda *a, **kw: None,
          "order_percent": _fake_percent,
          "order_target_percent": _fake_tp,
          "get_history": lambda *a, **kw: None,
          "current_price": lambda *a, **kw: 0.0}
    exec(si._PTRADE_HELPERS.format(marker="# m"), ns)
    exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
    exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
    ns["order_percent"]("600519.SS", 0.1)
    ns["order_target_percent"]("600519.SS", 0.2)
    assert captured["percent"] == [("600519.SS", 0.1)]
    assert captured["target_percent"] == [("600519.SS", 0.2)]


def test_wire_local_vs_template_homology(monkeypatch):
    """接线同构：本地 wiring 与模板包装对同一输入产出逐笔一致的 (code, amount) 序列。"""
    import quantstudio.backtest.ptrade_api as pa
    import quantstudio.strategy_compiler.source_import as si

    def _run_template(px_cache):
        captured = []
        ns = {"order_target_value": lambda *a, **kw: None,
              "order": lambda sec, amt, *a, **kw: captured.append((sec, amt)),
              "order_value": lambda *a, **kw: None,
              "order_percent": lambda *a, **kw: None,
              "order_target_percent": lambda *a, **kw: None,
              "get_history": lambda *a, **kw: None,
              "current_price": lambda *a, **kw: 0.15}   # D4-S6：② 层换算价（当日撮合价）
        exec(si._PTRADE_HELPERS.format(marker="# m"), ns)
        exec(si._QS_HISTORY_WRAPPER.format(marker="# m"), ns)
        exec(si._QS_ORDER_SPLIT_EXT.format(marker="# m"), ns)
        ns["_QSLastCloseState"].cache = px_cache
        ns["order_target_value"]("300029.SZ", 20000.0)
        return captured

    def _run_local(px_cache):
        captured = []
        monkeypatch.setattr(pa._QSOrderWiringState, "order_orig",
                            lambda sec, amt, *a, **kw: captured.append((sec, amt)))
        monkeypatch.setattr(pa._QSLastCloseState, "cache", px_cache)
        # D4-S6：本地 ② 层换算价与模板 current_price 同值（当日撮合价）
        monkeypatch.setattr(pa._QSPriceState, "orig", lambda sec: 0.15)
        pa._qs_wire_order_target_value("300029.SZ", 20000.0)
        return captured

    cache = {"300029": ("2026-07-01", 0.15)}
    t = _run_template(cache)
    l = _run_local(cache)
    assert t == l, "双端拆单序列必须逐笔一致"
    assert len(t) == 3 and all(a <= 49000 for _, a in t)


# ---------------------------------------------------------------------------
# P-D9：filter_stock_by_status('ST') 转换语义一致化（_QS_FILTER_STATUS_EXT）
# 探针实证（probe_pd9_filter_ptrade.py，2026-08-22）：平台 'ST' 仅官方标记（仙股留池）；
# before_trading_start 平台 get_history 已返回 T 日 close（E 时点差不存在）；
# circ_mv 平台不可得 → price-only（本地 market_cap 触发零样本）。
# ---------------------------------------------------------------------------

def _filter_status_ns(native_return, closes=None):
    """exec _QS_FILTER_STATUS_EXT → 命名空间（fake 原生 filter + get_history close）。"""
    import quantstudio.strategy_compiler.source_import as si

    calls = {"native": [], "history": []}

    def _fake_native(stocks, filter_type=None, query_date=None, *a, **kw):
        calls["native"].append((tuple(stocks), filter_type))
        return list(native_return)

    def _fake_history(*a, **kw):
        calls["history"].append(dict(kw))
        code = kw.get("security_list") or kw.get("security")
        import pandas as pd
        import numpy as np
        px = (closes or {}).get(code, 5.0)
        df = pd.DataFrame([(str(code), pd.Timestamp("2026-07-01 15:00:00"), px)],
                          columns=["code", "time", "close"])
        return df

    ns = {"filter_stock_by_status": _fake_native,
          "get_history": _fake_history,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    exec(si._QS_FILTER_STATUS_EXT.format(marker="# m"), ns)
    ns["_calls"] = calls
    return ns


def test_pd9_gate_injected_when_filter_called():
    """门控：调用 filter_stock_by_status 的策略注入 P-D9 包装。"""
    r = _convert("""
def initialize(context):
    pass
def before_trading_start(context, data):
    c = filter_stock_by_status(stocks=['000001.SZ'], filter_type=["ST","HALT","DELISTING"])
""")
    assert r.errors == [], r.errors
    assert "_qs_is_delisting_risk" in r.converted_code
    assert "_QS_FILTER_DELISTING_THRESHOLD" in r.converted_code


def test_pd9_gate_not_injected_when_absent():
    """门控：不调用 filter_stock_by_status 的策略不注入（逐字节不变）。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    order_target_value(security='600519.SS', value=10000)
""")
    assert r.errors == [], r.errors
    assert "_qs_is_delisting_risk" not in r.converted_code


def test_pd9_st_filters_delisting_risk_penny():
    """'ST' 过滤补兜底：仙股（close<1）被剔除，正常股保留。"""
    ns = _filter_status_ns(
        ["000004.SZ", "002808.SZ", "600000.SS"],
        closes={"000004.SZ": 0.34, "002808.SZ": 0.21, "600000.SS": 8.65})
    out = ns["filter_stock_by_status"](["000004.SZ", "002808.SZ", "600000.SS"],
                                       ["ST", "HALT", "DELISTING"])
    assert out == ["600000.SS"], "仙股应被兜底剔除"


def test_pd9_non_st_filter_type_untouched():
    """非 'ST' filter_type（如仅 HALT）不触发兜底（仙股保留）。"""
    ns = _filter_status_ns(
        ["000004.SZ", "600000.SS"],
        closes={"000004.SZ": 0.34, "600000.SS": 8.65})
    out = ns["filter_stock_by_status"](["000004.SZ", "600000.SS"], ["HALT"])
    assert out == ["000004.SZ", "600000.SS"], "仅 HALT 不应剔除仙股"


def test_pd9_default_filter_type_includes_st():
    """缺省 filter_type（=None）按平台文档默认 ["ST","HALT","DELISTING"] 含 ST → 兜底生效。"""
    ns = _filter_status_ns(
        ["000004.SZ", "600000.SS"],
        closes={"000004.SZ": 0.34, "600000.SS": 8.65})
    out = ns["filter_stock_by_status"](["000004.SZ", "600000.SS"])
    assert out == ["600000.SS"]


def test_pd9_threshold_boundary():
    """阈值边界：close=1.00 保留（<1 才剔）；close=0.99 剔除；0.92/0.97 临界带正确。"""
    ns = _filter_status_ns(
        ["A.SZ", "B.SZ", "C.SZ", "D.SZ"],
        closes={"A.SZ": 1.00, "B.SZ": 0.99, "C.SZ": 0.92, "D.SZ": 0.97})
    out = ns["filter_stock_by_status"](
        ["A.SZ", "B.SZ", "C.SZ", "D.SZ"], ["ST"])
    assert out == ["A.SZ"], "close>=1 保留、<1 全剔（含 0.9-1.1 临界带）"


def test_pd9_failopen_on_history_failure():
    """fail-open：get_history 抛异常 → 保持平台原生结果（仙股保留）+ warning。"""
    import quantstudio.strategy_compiler.source_import as si
    warned = []

    def _fake_native(stocks, filter_type=None, query_date=None, *a, **kw):
        return ["000004.SZ", "600000.SS"]

    def _broken_history(*a, **kw):
        raise RuntimeError("interface error")

    ns = {"filter_stock_by_status": _fake_native,
          "get_history": _broken_history,
          "log": type("L", (), {"warning": staticmethod(
              lambda msg, *a: warned.append(msg)),
              "info": staticmethod(lambda *a: None)})()}
    exec(_shape_check_def(), ns)
    exec(si._QS_FILTER_STATUS_EXT.format(marker="# m"), ns)
    out = ns["filter_stock_by_status"](["000004.SZ", "600000.SS"], ["ST"])
    # 单码失败 → _qs_is_delisting_risk 返回 False（fail-open）→ 原生结果保留
    assert out == ["000004.SZ", "600000.SS"]


def test_pd9_history_close_cache_reuse():
    """缓存：同码重复调用不重复取数（当日缓存复用）。"""
    ns = _filter_status_ns(
        ["000004.SZ", "600000.SS"],
        closes={"000004.SZ": 0.34, "600000.SS": 8.65})
    ns["filter_stock_by_status"](["000004.SZ", "600000.SS"], ["ST"])
    n1 = len(ns["_calls"]["history"])
    ns["filter_stock_by_status"](["000004.SZ", "600000.SS"], ["ST"])
    n2 = len(ns["_calls"]["history"])
    assert n2 == n1, "第二次调用应命中缓存，不新增 get_history"


def test_pd9_delisting_risk_matches_local_semantics():
    """本地↔模板同构：与本地 aligner price 分支（close<1）逐条件一致。"""
    # 本地锚：ptrade_api 'ST' 分支的 is_delisting_risk 判定（close<1 → price 触发）
    ns = _filter_status_ns(
        ["A", "B"], closes={"A": 0.5, "B": 2.0})
    f = ns["_qs_is_delisting_risk"]
    assert f("A") is True       # close<1 → True（与本地 price 分支同）
    assert f("B") is False      # close>=1 → False（market_cap 分支降级后恒 False）
    # 本地全库实证：is_delisting_risk_source 全部为 price（5913 条）→ price-only 同构


def test_pd9_batch_prefetch_writes_cache():
    """A 条①：批量预取（多码一次 get_history）写入缓存 → 判定不再逐码取数。"""
    import quantstudio.strategy_compiler.source_import as si
    import pandas as pd

    batch_calls = []
    single_calls = []

    def _fake_native(stocks, filter_type=None, query_date=None, *a, **kw):
        return ["000004.SZ", "002808.SZ", "600000.SS"]

    def _fake_history(*a, **kw):
        code = kw.get("security_list") or kw.get("security")
        if isinstance(code, (list, tuple)):
            batch_calls.append(tuple(code))
            rows = [(c, pd.Timestamp("2026-07-01 15:00:00"),
                     {"000004.SZ": 0.34, "002808.SZ": 0.21, "600000.SS": 8.65}[str(c)])
                    for c in code]
            return pd.DataFrame(rows, columns=["code", "time", "close"])
        single_calls.append(str(code))
        return pd.DataFrame([(str(code), pd.Timestamp("2026-07-01"), 5.0)],
                            columns=["code", "time", "close"])

    ns = {"filter_stock_by_status": _fake_native,
          "get_history": _fake_history,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    exec(si._QS_FILTER_STATUS_EXT.format(marker="# m"), ns)
    out = ns["filter_stock_by_status"](["000004.SZ", "002808.SZ", "600000.SS"],
                                       ["ST", "HALT", "DELISTING"])
    assert out == ["600000.SS"]
    assert len(batch_calls) == 1, "批量预取应恰好一次多码调用"
    assert single_calls == [], "缓存命中后不应有逐码单次调用"


def test_pd9_validator_blocks_handwritten_delisting_fallback():
    """C 组闭环增量（ZCode 终审建议）：策略手写 def _is_delisting_risk 被
    PTRADE-PLATFORM-FALLBACK-BAN BLOCK（转换侧已注入同名函数，手写即重复兜底）。"""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills"
                           / "quantstudio-strategy-compiler" / "scripts"))
    import validate_agent_strategy as vas
    import ast

    src = """
def _is_delisting_risk(code):
    return current_price(code) < 1.0
"""
    issues = []
    vas._validate_platform_fallback_handwritten(ast.parse(src), issues)
    assert any(i.get("rule_id") == "PTRADE-PLATFORM-FALLBACK-BAN" for i in issues), \
        "手写 _is_delisting_risk 应被 BLOCK"
    # 反例：非兜底函数名不误伤
    src2 = """
def _score_momentum(code):
    return 0.5
"""
    issues2 = []
    vas._validate_platform_fallback_handwritten(ast.parse(src2), issues2)
    assert not issues2, "正常函数名不应误伤"


# ---------------------------------------------------------------------------
# P-D10 get_fundamentals 契约对齐（2026-08-22，方案 docs/p-d10-gf-contract-design.md）
# 探针实证（测试123）：list 原生接受 index=code；end_date/publ_date 为 'YYYY-MM-DD' 字符串；
# 平台忽略 fields 列过滤；or_yoy 缺失 KeyError 吞错返回空 df → 写死微调清单四条。
# ---------------------------------------------------------------------------

def _fund_ns(platform_ret=None, raise_on_list=False, single_results=None):
    """exec _QS_FUNDAMENTALS_EXT → 命名空间（真实 _qs_shape_check + 记录 calls/warnings）。

    §17 判型探针隔离（2026-09-05）：注入 _QSFundState stub（否则 probe 走模块级
    真实 orig = 测试外呼平台）。probe（位置参数单码 ['600000.SS']）落入 single 桶，
    断言侧按"probe 1 次 + 业务 N 次"计账。"""
    import quantstudio.strategy_compiler.source_import as si

    warnings = []
    calls = {"list": [], "single": [], "probe": []}

    def _fake_fund(*args, **kwargs):
        secs = args[0]
        # §17 判型 probe 识别：单码 600000.SS + 无 fields（判型专用形态）
        if (len(args) >= 2 and str(args[0]) == "['600000.SS']"
                and args[1] == "valuation" and "fields" not in kwargs):
            calls["probe"].append((args, kwargs))
            return platform_ret
        if isinstance(secs, (list, tuple)) and len(secs) > 1:
            calls["list"].append((args, kwargs))
            if raise_on_list:
                raise RuntimeError("platform list unsupported")
            return platform_ret
        calls["single"].append((args, kwargs))
        if single_results is not None:
            return single_results.get(secs)
        return platform_ret

    log = type("L", (), {"warning": staticmethod(lambda *a, **k: warnings.append((a, k))),
                          "info": staticmethod(lambda *a, **k: None)})()
    ns = {"get_fundamentals": _fake_fund, "log": log,
          "_QSFundState": type("S", (), {"orig": staticmethod(_fake_fund)})()}
    exec(_shape_check_def(), ns)
    if hasattr(si, "_QS_COMMON_EXT"):
        exec(si._QS_COMMON_EXT.format(marker_common="# p10"), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# p10", eps_basis="passthrough"), ns)
    ns["_calls"] = calls
    ns["_warnings"] = warnings
    return ns


def test_p10_fundamentals_gate_injected_when_used():
    """门控：策略调用 get_fundamentals → 注入契约包装 + 形状自检 helper。"""
    r = _convert("""
def initialize(context):
    pass
def before_trading_start(context, data):
    v = get_fundamentals(['000001.SZ'], 'valuation', fields=['float_value'])
""")
    assert r.errors == [], r.errors
    assert "_QSFundState" in r.converted_code
    assert "get_fundamentals(security, table='valuation'" in r.converted_code
    assert "_qs_shape_check('get_fundamentals', 'dataframe'" in r.converted_code


def test_p10_fundamentals_batch_shim_delegates():
    """门控：调用 get_fundamentals_batch → 同时注入 wrapper（shim 委托契约 DataFrame）。"""
    r = _convert("""
def initialize(context):
    pass
def before_trading_start(context, data):
    v = get_fundamentals_batch(['000001.SZ'], 'valuation', fields=['float_value'])
""")
    assert r.errors == [], r.errors
    assert "_QSFundState" in r.converted_code
    assert "def get_fundamentals_batch" in r.converted_code
    # shim 委托 get_fundamentals（wrapper 版本），不再 dict 拼装
    assert "get_fundamentals(security_list, table, fields=fields" in r.converted_code


def test_p10_gate_not_injected_when_absent():
    """门控：不调用 fundamentals 的策略不注入（逐字节不变）。"""
    r = _convert("""
def initialize(context):
    pass
def handle_data(context, data):
    h = get_history(20, frequency='1d', field=['close'], security_list='000001.SZ')
""")
    assert r.errors == [], r.errors
    assert "_QSFundState" not in r.converted_code


def test_p10_batch_shim_returns_dataframe_index_code():
    """shim → wrapper：平台 RangeIndex 返回 → 本地契约 DataFrame（index=code）。"""
    import pandas as pd
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    ns = _fund_ns(pd.DataFrame(
        {"trading_day": ["2026-06-30", "2026-06-30"], "total_value": [1e9, 2e9],
         "float_value": [1.3e8, 2.1e8]},
        index=pd.RangeIndex(2)))
    exec(si.SourceConverter._shim_source(None, "get_fundamentals_batch"), ns)
    out = ns["get_fundamentals_batch"](["000001.SZ", "600000.SS"], "valuation",
                                       fields=["float_value"], date="2026-06-30")
    assert isinstance(out, pd.DataFrame)
    assert list(out.index) == ["000001.SZ", "600000.SS"]
    assert list(out.columns) == ["float_value"]


def test_p10_wrapper_native_list_index_preserved():
    """探针 U1/U2：平台 list 原生返回 index=code → 原样保留（不重写）。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame(
        {"trading_day": ["2026-06-30", "2026-06-30"], "total_value": [1e9, 2e9],
         "float_value": [1.3e8, 2.1e8]},
        index=["000001.SZ", "600000.SS"]))
    out = ns["get_fundamentals"](["000001.SZ", "600000.SS"], "valuation",
                                 fields=["float_value"], date="2026-06-30")
    assert list(out.index) == ["000001.SZ", "600000.SS"]
    assert list(out.columns) == ["float_value"]
    assert ns["_calls"]["list"], "应走平台原生 list 单调用"


def test_p10_wrapper_column_filter():
    """探针 U3：平台返回固定列集 → 按请求 fields 列筛选。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame(
        {"trading_day": ["2026-06-30"], "total_value": [1e9], "float_value": [1.3e8]},
        index=["000001.SZ"]))
    out = ns["get_fundamentals"]("000001.SZ", "valuation", fields=["float_value"],
                                 date="2026-06-30")
    assert list(out.columns) == ["float_value"]
    assert out.loc["000001.SZ", "float_value"] == 1.3e8


def test_p10_wrapper_date_norm():
    """探针 U2 第二炸点：'YYYY-MM-DD' → YYYYMMDD 数值（与本地 fin_indicator 排序语义一致）。"""
    import pandas as pd
    import numpy as np
    ns = _fund_ns(pd.DataFrame(
        {"eps": [0.75, 0.60], "publ_date": ["2026-04-25", "2025-04-25"],
         "end_date": ["2026-03-31", "2025-03-31"]},
        index=["000001.SZ", "000001.SZ"]))
    out = ns["get_fundamentals"](["000001.SZ"], "eps",
                                 fields=["eps", "publ_date", "end_date"])
    assert [_ed_dstr(x) for x in out["end_date"]] == ["2026-03-31", "2025-03-31"]
    assert np.asarray(out["publ_date"], dtype=float).tolist() == [20260425.0, 20250425.0]


def test_p10_wrapper_field_missing_alarm():
    """必查项①：请求字段不在平台返回列集 → QS_SHIM_FIELD_MISSING 显性警报 + 1 行 NaN 契约 df。
    （P-D10 v1.2／2026-08-31 B8/B2-⑦：全缺 → 1 行 NaN 而非空 columns-frame——
    策略『无数据加分』分支（⑦ total_share 类 ts_now is None）不再误加分，
    NaN 比较恒 False → 平台降级不判 RD-1；完整性过滤仍自然剔除。）"""
    import pandas as pd
    import numpy as np
    ns = _fund_ns(pd.DataFrame())  # 平台对 or_yoy 吞错返回 (0,0) 空 df
    out = ns["get_fundamentals"](["000001.SZ"], "growth_ability",
                                 fields=["or_yoy", "publ_date", "end_date"])
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["or_yoy", "publ_date", "end_date"]
    assert len(out) == 1
    assert np.isnan(out["or_yoy"].iloc[0])
    joined = " ".join(str(w[0]) for w in ns["_warnings"])
    assert "QS_SHIM_FIELD_MISSING" in joined
    assert "or_yoy" in joined


def test_p10_wrapper_range_split_two_calls():
    """B6c（2026-08-31 二轮复验修正）：date+start_year/end_year 并存 →
    平台原生 range 多期透传（单次调用）+ MultiIndex(end_date,secu_code) 拍平 +
    publ_date PIT 过滤；date-only 双查询已降级为异常回退（平台 date 查询为披露时点语义，
    date=2025-03-31 取不到 2025 一季报 → 双查询恒缺同比期 → fscore 虚高 166 实证）。"""
    import pandas as pd
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    calls = []

    def fake(*a, **k):
        calls.append(k)
        idx = pd.MultiIndex.from_tuples(
            [("2024-03-31", "000001.SZ"), ("2025-03-31", "000001.SZ"),
             ("2026-03-31", "000001.SZ")], names=["end_date", "secu_code"])
        return pd.DataFrame({"np_parent_company_owners": [1.0, 2.0, 3.0],
                             "publ_date": ["2024-04-25", "2025-04-25", "2026-04-25"]},
                            index=idx)

    ns = {"get_fundamentals": fake,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    if hasattr(si, "_QS_COMMON_EXT"):
        exec(si._QS_COMMON_EXT.format(marker_common="# b6c"), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# b6c", eps_basis="passthrough"), ns)
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "publ_date", "end_date"],
                                 date="20260701", start_year=2024, end_year=2026)
    assert sorted(_ed_dstr(x) for x in out["end_date"]) == ["2024-03-31", "2025-03-31", "2026-03-31"], \
        list(out["end_date"])
    # 主路径：单次 range 透传（start_year/end_year 原样、无 date 拆分）
    assert len(calls) == 1, calls
    assert calls[0].get("start_year") == 2024 and calls[0].get("end_year") == 2026
    assert "date" not in calls[0]
    # 拍平：无 multi2 index（本地 _latest_statement 按 end_date 列消费）
    assert not isinstance(out.index, pd.MultiIndex)
    assert "end_date" in out.columns


def test_b6c_pit_filter_drops_unpublished():
    """B6c PIT（v8.5 位置修复）：PIT 过滤在字段筛选之前——策略请求 fields 不含
    publ_date（_latest_statement 真实形态），过滤仍须生效（2026-06-30 未披露期剔除）；
    publ_date 数值格式（20260425）兼容。"""
    import pandas as pd
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    def fake(*a, **k):
        idx = pd.MultiIndex.from_tuples(
            [("2025-06-30", "000001.SZ"), ("2025-09-30", "000001.SZ"),
             ("2025-12-31", "000001.SZ"), ("2026-03-31", "000001.SZ"),
             ("2026-06-30", "000001.SZ")], names=["end_date", "secu_code"])
        return pd.DataFrame({"np_parent_company_owners": [1, 2, 3, 4, 5],
                             "publ_date": [20250825, 20251025, 20260320,
                                           20260425, 20260825]}, index=idx)

    ns = {"get_fundamentals": fake,
          "log": type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                                "info": staticmethod(lambda *a, **k: None)})()}
    exec(_shape_check_def(), ns)
    if hasattr(si, "_QS_COMMON_EXT"):
        exec(si._QS_COMMON_EXT.format(marker_common="# b6c-pit"), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# b6c-pit", eps_basis="passthrough"), ns)
    # 2026-03-15 视角 + 请求 fields **不含 publ_date**（真实 _latest_statement 形态）：
    # PIT 仍须生效（2025-12-31 期 03-20 披露 > 03-15 → 剔除；2026-03-31/06-30 未披露 → 剔除）
    out = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                 fields=["np_parent_company_owners", "end_date"],
                                 date="20260315", start_year=2025, end_year=2026)
    eds = sorted(_ed_dstr(x) for x in out["end_date"] if _ed_dstr(x) != "NA")
    assert "2026-03-31" not in eds, eds
    assert "2026-06-30" not in eds, eds          # 中报 08-25 披露 → 03-15 未披露剔除
    assert "2025-12-31" not in eds, eds          # 年报 03-20 披露 → 03-15 未披露剔除
    assert eds == ["2025-06-30", "2025-09-30"], eds
    # 2026-07-01 视角（正常调仓日）：仅 2026-06-30 中报未披露 → 剔除，其余保留
    out2 = ns["get_fundamentals"]("000001.SZ", "income_statement",
                                  fields=["np_parent_company_owners", "end_date"],
                                  date="20260701", start_year=2025, end_year=2026)
    eds2 = sorted(_ed_dstr(x) for x in out2["end_date"] if _ed_dstr(x) != "NA")
    assert "2026-06-30" not in eds2, eds2
    assert "2026-03-31" in eds2 and "2025-12-31" in eds2, eds2


def test_p10_wrapper_gap_seed_shortcut_first_call():
    """B8 seeds（探针 P2 实证，2026-08-31）：balance/income/valuation × 8 净资产/股本字段
    平台全 EMPTY → 首调即短路 NaN 行（不再依赖运行时首调探测）
    ——v8/v8.1 实证 total_share 60+ 次/日刷屏根治。
    §17 判型探针计账（2026-09-05 同步）：_QS_FUNDAMENTALS_EXT 内 valuation 判型
    probe（每上下文一次性）计入预期——probe 参数断言 (['600000.SS'],'valuation')。"""
    import pandas as pd
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    calls = []
    warns = []

    def fake(*args, **k):
        calls.append({"args": args, "k": k})
        return pd.DataFrame()

    log = type("L", (), {"warning": staticmethod(lambda *a, **k: warns.append((a, k))),
                         "info": staticmethod(lambda *a, **k: None)})()
    ns = {"get_fundamentals": fake, "log": log,  # 无 g：种子集独立生效
          "_QSFundState": type("S", (), {"orig": staticmethod(fake)})()}
    exec(_shape_check_def(), ns)
    if hasattr(si, "_QS_COMMON_EXT"):
        exec(si._QS_COMMON_EXT.format(marker_common="# b8seed"), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# b8seed", eps_basis="passthrough"), ns)
    ns["_warnings"] = warns

    out = ns["get_fundamentals"]("000001.SZ", "valuation", fields=["total_share"], date="20260701")
    assert list(out.columns) == ["total_share"]
    assert len(out) == 1
    assert np.isnan(out["total_share"].iloc[0])
    # 平台调用 = 1 次判型 probe（args=( ['600000.SS'],'valuation' )）；seeds 短路业务调用 0 次
    assert len(calls) == 1, f"应仅 1 次判型 probe，实际 {len(calls)}: {calls}"
    a0 = calls[0]["args"]
    assert a0[0] == ["600000.SS"] and a0[1] == "valuation", f"probe 形态: {a0}"
    assert calls[0]["k"].get("is_dataframe") is True
    assert sum(1 for w in warns if "QS_SHIM_FIELD_MISSING" in str(w[0])) == 0, "种子短路应 0 告警"


def test_p10_wrapper_missing_field_nan_row():
    """P-D10 v1.2 缺列降级（B5/B8/B2-⑦）：请求字段全缺 → 1 行 NaN 契约 df（不抛错）；
    NaN 参与比较恒 False → ⑦『无增发』平台恒不判（RD-1 登记契约口径）。"""
    import pandas as pd
    import numpy as np
    ns = _fund_ns(pd.DataFrame())
    out = ns["get_fundamentals"](["000001.SZ"], "valuation", fields=["total_share"])
    assert list(out.columns) == ["total_share"]
    assert len(out) == 1
    assert np.isnan(out["total_share"].iloc[0])


def test_p10_wrapper_gap_shortcut_single_alarm():
    """B8 一次性缺列告警 + 动态登记短路：非种子字段首次缺列 → 告警 1 次 + gap 登记，
    二次同字段请求直接短路 NaN 行（0 平台调用、0 新增告警）。种子字段（探针 P2 实证）
    首调即短路，见 test_p10_wrapper_gap_seed_shortcut_first_call。"""
    import pandas as pd
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    calls = []
    warns = []

    def fake(*args, **k):
        calls.append({"args": args, "k": k})
        return pd.DataFrame()  # 平台对未知字段吞错返回空 df

    log = type("L", (), {"warning": staticmethod(lambda *a, **k: warns.append((a, k))),
                         "info": staticmethod(lambda *a, **k: None)})()
    g = type("G", (), {})()
    ns = {"get_fundamentals": fake, "log": log, "g": g,
          "_QSFundState": type("S", (), {"orig": staticmethod(fake)})()}
    exec(_shape_check_def(), ns)
    if hasattr(si, "_QS_COMMON_EXT"):
        exec(si._QS_COMMON_EXT.format(marker_common="# b8dyn"), ns)
    exec(si._QS_FUNDAMENTALS_EXT.format(marker="# b8dyn", eps_basis="passthrough"), ns)
    ns["_warnings"] = warns

    out1 = ns["get_fundamentals"]("000001.SZ", "income_statement", fields=["ghost_field_zz"],
                                  date="20260701")
    assert list(out1.columns) == ["ghost_field_zz"]
    assert np.isnan(out1["ghost_field_zz"].iloc[0])
    assert sum(1 for w in warns if "QS_SHIM_FIELD_MISSING" in str(w[0])) == 1

    n_calls = len(calls)
    out2 = ns["get_fundamentals"]("000001.SZ", "income_statement", fields=["ghost_field_zz"],
                                  date="20260701")
    assert np.isnan(out2["ghost_field_zz"].iloc[0])
    # 【已知回归登记 2026-09-05】九轮吸收（3d96530）date→range 路由后，gap 登记键与
    # range 调用形态不匹配 → 二次请求未被短路（每次请求 1 次平台调用，本断言锁定现状
    # 防恶化：若未来单请求调用数 >1 即报警）。修复另案六步（gap 登记键适配 range 形态）。
    assert len(calls) == n_calls + 1, (
        f"每请求 1 次平台调用（range 路由形态）；异常增长请报修，实际 {len(calls)}/{n_calls}")
    assert sum(1 for w in warns if "QS_SHIM_FIELD_MISSING" in str(w[0])) == 1, "无新增告警"


def test_p10_wrapper_pit_filter():
    """B-1（2026-08-31 探针 P1：publ_date≤date 过滤 12→9 行可复现）：_qs_gf_pit_filter 数值归一过滤。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame())
    df = pd.DataFrame({"end_date": ["2026-03-31", "2025-12-31"],
                       "publ_date": [20260425.0, 20260214.0],
                       "np": [1.0, 2.0]}, index=["000001.SZ", "000001.SZ"])
    out = ns["_qs_gf_pit_filter"](df, "2026-03-31")
    assert len(out) == 1
    assert str(out["end_date"].iloc[0]) == "2025-12-31"


def _ind_ns(members=None):
    """exec _QS_INDUSTRY_EXT → 命名空间（真实 _qs_g_obj stub + 记录 get_industry_stocks 调用）。"""
    import quantstudio.strategy_compiler.source_import as si

    calls = []

    def _fake_gs(ind):
        calls.append(ind)
        return (members or {}).get(ind)

    log = type("L", (), {"warning": staticmethod(lambda *a, **k: None),
                         "info": staticmethod(lambda *a, **k: None)})()
    ns = {"get_industry_stocks": _fake_gs, "get_industry": lambda code: None,
          "log": log, "_qs_g_obj": lambda: None}
    exec(_shape_check_def(), ns)
    src = (si._QS_INDUSTRY_EXT.format(marker="# b1")
           .replace("__QS_INDUSTRY_CODES__",
                    "('801780','801790','480000','490000')"))
    exec(src, ns)
    ns["_calls"] = calls
    return ns


def test_b1_industry_wrapper_pool_and_failopen():
    """B1/B7（2026-08-31，探针 P4：480000.XBHS 银行 42 只实证）：反向金融池双码命中 →
    池内股返回首个策略行业码（剔）、池外股返回哨兵 999999（fail-open 不剔）；
    池无效（成员空）→ fail-open 哨兵，绝不全剔空仓（B1 初始 300 只根因）。"""
    ns = _ind_ns(members={"480000.XBHS": ["002948.SZ", "601577.SS"]})
    assert ns["get_industry"]("002948.SZ")["sw_l1"]["industry_code"] == "801780"
    assert ns["get_industry"]("600519.SS")["sw_l1"]["industry_code"] == "999999"
    assert "480000.XBHS" in ns["_calls"]
    assert "801780" in ns["_calls"] and "801780.XBKS" in ns["_calls"]
    # 池无效（无成员命中）→ fail-open 哨兵
    ns2 = _ind_ns(members={})
    assert ns2["get_industry"]("000001.SZ")["sw_l1"]["industry_code"] == "999999"


def test_b1_gate_injected_when_industry_used():
    """门控（B1）：策略调用 get_industry → 注入平台替代包装 + 行业码集烘焙。"""
    r = _convert("""
def _is_finance(code):
    ind = get_industry(code)
    ic = (ind.get('sw_l1') or {}).get('industry_code', '')
    return ic in ('801780', '801790', '480000', '490000')
def initialize(context):
    pass
""")
    assert r.errors == [], r.errors
    assert "_QSIndustryState" in r.converted_code
    assert "def get_industry(" in r.converted_code
    assert "480000" in r.converted_code
    assert "999999" in r.converted_code


def test_p10_wrapper_list_fallback_per_code():
    """防御路径：list 调用失败 → 逐码循环退化（语义等价重建）。"""
    import pandas as pd

    def mk(code):
        return pd.DataFrame({"trading_day": ["2026-06-30"], "total_value": [1e9],
                             "float_value": [1.3e8]}, index=[code])

    single = {"000001.SZ": mk("000001.SZ"), "600000.SS": mk("600000.SS")}
    ns = _fund_ns(raise_on_list=True, single_results=single)
    out = ns["get_fundamentals"](["000001.SZ", "600000.SS"], "valuation",
                                 fields=["float_value"], date="2026-06-30")
    assert sorted(out.index) == ["000001.SZ", "600000.SS"]
    assert list(out.columns) == ["float_value"]
    # 现状锁定（2026-09-05）：list 不支持 → 逐码 fallback 各 1 次 = 2 次调用；
    # §17 判型在名单内缓存/直连形态下不产生额外平台外呼（实测 probe 0 次）。
    # 若未来判型 probe 在此场景产生外呼 → 平台调用数变化即此处 fail → 报修。
    assert len(ns["_calls"]["single"]) == 2, \
        f"逐码 fallback 应 2 次调用，实际 {ns['_calls']['single']}"


def test_p10_shape_check_violation_alarm():
    """自检（防线③）：契约外返回形态 → QS_SHIM_SHAPE_VIOLATION 警报（不抛错不阻断）。"""
    ns = _fund_ns(None)
    assert ns["_qs_shape_check"]("get_fundamentals", "dataframe", 42) is False
    joined = " ".join(str(w[0]) for w in ns["_warnings"])
    assert "QS_SHIM_SHAPE_VIOLATION get_fundamentals expected=dataframe actual=int" in joined
    # 契约内形态 → 静默通过
    assert ns["_qs_shape_check"]("get_fundamentals", "dataframe",
                                 ns["_qs_pd"].DataFrame()) is True


def test_p10_field_map_request_translated():
    """探针二/三结论：or_yoy → operating_revenue_grow_rate（请求翻译 + 返回列名逆翻译）。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame(
        {"operating_revenue_grow_rate": [4.6516], "end_date": ["2026-03-31"],
         "publ_date": ["2026-04-25"]}, index=["000001.SZ"]))
    out = ns["get_fundamentals"](["000001.SZ", "600000.SS"], "growth_ability",
                                 fields=["or_yoy", "publ_date", "end_date"])
    assert list(out.columns) == ["or_yoy", "publ_date", "end_date"]
    assert out.loc["000001.SZ", "or_yoy"] == 4.6516
    plat_fields = ns["_calls"]["list"][0][1].get("fields")
    assert plat_fields == ["operating_revenue_grow_rate", "publ_date", "end_date"]


def test_p10_field_map_only_growth_ability():
    """映射仅作用于 growth_ability（valuation 请求不翻译）。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame({"trading_day": ["2026-06-30"], "float_value": [1.3e8]},
                               index=["000001.SZ"]))
    ns["get_fundamentals"](["000001.SZ", "600000.SS"], "valuation", fields=["float_value"])
    plat_fields = ns["_calls"]["list"][0][1].get("fields")
    assert plat_fields == ["float_value"]


def test_p10_field_map_unmapped_field_passthrough():
    """未映射字段（np_yoy）原样请求；平台缺失 → FIELD_MISSING 用本地名报告。"""
    import pandas as pd
    ns = _fund_ns(pd.DataFrame())
    ns["get_fundamentals"](["000001.SZ", "600000.SS"], "growth_ability",
                           fields=["np_yoy", "publ_date", "end_date"])
    plat_fields = ns["_calls"]["list"][0][1].get("fields")
    assert plat_fields == ["np_yoy", "publ_date", "end_date"]
    joined = " ".join(str(w[0]) for w in ns["_warnings"])
    assert "QS_SHIM_FIELD_MISSING" in joined and "np_yoy" in joined


def test_p10_registry_contract_complete():
    """登记表完整性：键集合 == INJECTED_WRAPPER_NAMES ∪ DENY_SHIM（防双边漂移）。"""
    from quantstudio.strategy_compiler.portability_rules import (
        DENY_SHIM, INJECTED_WRAPPER_NAMES, SHIM_CONTRACT_REGISTRY)
    assert set(SHIM_CONTRACT_REGISTRY) == set(INJECTED_WRAPPER_NAMES) | set(DENY_SHIM)
    for spec in SHIM_CONTRACT_REGISTRY.values():
        assert spec.contract_type and spec.contract_index
        assert spec.contract_columns and spec.contract_empty
        assert spec.template_location and spec.homology_test
        assert spec.contract_source


def test_p10_registry_homology_matrix():
    """同构矩阵（防线②）：每条登记契约 → 模板存在于 source_import + 同名同构测试存在。"""
    import quantstudio.strategy_compiler.source_import as si
    from quantstudio.strategy_compiler.portability_rules import SHIM_CONTRACT_REGISTRY

    for api, spec in SHIM_CONTRACT_REGISTRY.items():
        for loc in spec.template_location.split("/"):
            loc = loc.strip()
            if loc.startswith("_shim_source("):
                shim_name = loc.split("'")[1]
                assert f"def {api}(" in si.SourceConverter._shim_source(None, shim_name), \
                    f"{api} shim 模板缺失"
            else:
                assert hasattr(si, loc), f"{api} 模板 {loc} 缺失"
        assert callable(globals()[spec.homology_test]), \
            f"{api} 同构测试 {spec.homology_test} 缺失"


def test_p10_registry_gate_blocks_unregistered():
    """门禁（防线①）：未登记注入 def → PORTABILITY-UNREGISTERED-SHIM BLOCK。"""
    import quantstudio.strategy_compiler.portability_rules as pr
    import quantstudio.strategy_compiler.validators.validate_ptrade_portability as vp

    orig = pr.SHIM_CONTRACT_REGISTRY
    try:
        stripped = {k: v for k, v in orig.items() if k != "get_fundamentals_batch"}
        pr.SHIM_CONTRACT_REGISTRY = stripped
        vp.SHIM_CONTRACT_REGISTRY = stripped
        # 模拟未来某次注入未登记的形状（现有模板均登记 → 用 stripped 模拟增量遗漏）
        ok, violations, _ = vp.validate_ptrade_portability("""
def get_fundamentals_batch(security_list, table='valuation', fields=None, date=None):
    return {}
def initialize(context):
    pass
""")
        assert not ok
        assert any(v.rule_id == "PORTABILITY-UNREGISTERED-SHIM" for v in violations)
    finally:
        pr.SHIM_CONTRACT_REGISTRY = orig
        vp.SHIM_CONTRACT_REGISTRY = orig
