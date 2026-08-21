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

def _wrapper_ns(history_return):
    """exec _QS_HISTORY_WRAPPER 注入区 → 命名空间（含 _qs_to_dataframe / wrapper get_history）。
    ns['_captured'] 记录 wrapper 传给原始 get_history 的 kwargs（用于字段映射断言）。"""
    import numpy as np
    import quantstudio.strategy_compiler.source_import as si

    captured = {}

    def _fake_get_history(*args, **kwargs):
        captured["kwargs"] = kwargs
        return history_return

    ns = {"get_history": _fake_get_history}
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

    ns = {"get_history": _fake_get_history}
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
