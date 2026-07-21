"""daemon 端 close_df 准备逻辑测试（防止 ['ALL']/None 占位符导致 close_df=None）。

回归 bug：xtquant 全市场模式下 task['codes']=['ALL']，
旧逻辑 fs_codes=codes=['ALL'] → _prepare_close_df(['ALL']) →
normalize_code('ALL')→None → bare_codes 为空 → 返回 None →
aligner 输出 derive_mv_skip_no_close（circ_mv 全 NULL）。

修复后：识别 ['ALL']/None/['NONE'] 为占位符，从 raw_df 提取实际 codes。
"""
import pandas as pd
import pytest


def _resolve_fs_codes(codes_cfg, raw_codes_undefined, raw_df, raw_codes=None):
    """复刻 daemon L222-244 的 fs_codes 解析逻辑（修复后版本）。

    raw_codes_undefined: True 模拟 xtquant 路径（raw_codes 未定义），
                        False 模拟 tushare 路径（raw_codes 已赋值，需通过 raw_codes 参数传）
    """
    codes = codes_cfg
    fs_codes = None
    if not raw_codes_undefined:
        fs_codes = raw_codes if raw_codes is not None else codes
    else:
        fs_codes = codes  # fallback 到 task.codes

    fs_codes_is_placeholder = (
        not fs_codes
        or (isinstance(fs_codes, list) and len(fs_codes) == 1
            and str(fs_codes[0]).upper() in ("ALL", "NONE"))
    )
    if (fs_codes_is_placeholder or not fs_codes) and len(raw_df) > 0:
        code_col = next((c for c in ["code", "stock_code", "ts_code", "股票代码"]
                         if c in raw_df.columns), None)
        if code_col:
            fs_codes = raw_df[code_col].unique().tolist()
    return fs_codes


# ========== 1. xtquant 全市场路径（codes=['ALL']）==========

def test_xtquant_all_market_extracts_from_raw_df():
    """xtquant 路径 + codes=['ALL'] → 必须从 raw_df 提取实际 codes（不能直接用 ['ALL']）。
    这是回归 bug 的核心场景。
    """
    raw_df = pd.DataFrame({
        'code': ['600000', '000001', '300750'],
        'end_date': ['20260330']*3,
    })
    # xtquant 路径 raw_codes 未定义，codes_cfg=['ALL']
    fs_codes = _resolve_fs_codes(['ALL'], raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['600000', '000001', '300750']
    assert 'ALL' not in fs_codes


def test_xtquant_none_codes_extracts_from_raw_df():
    """xtquant 路径 + codes=None → 从 raw_df 提取。"""
    raw_df = pd.DataFrame({'code': ['600000', '000001'], 'end_date': ['20260330']*2})
    fs_codes = _resolve_fs_codes(None, raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['600000', '000001']


def test_xtquant_empty_codes_extracts_from_raw_df():
    """xtquant 路径 + codes=[] → 从 raw_df 提取。"""
    raw_df = pd.DataFrame({'code': ['600000', '000001'], 'end_date': ['20260330']*2})
    fs_codes = _resolve_fs_codes([], raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['600000', '000001']


def test_xtquant_none_placeholder_string():
    """codes=['NONE']（字符串占位符）→ 从 raw_df 提取。"""
    raw_df = pd.DataFrame({'code': ['600000'], 'end_date': ['20260330']})
    fs_codes = _resolve_fs_codes(['NONE'], raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['600000']


# ========== 2. tushare 路径（raw_codes 已赋值）==========

def test_tushare_uses_raw_codes():
    """tushare 路径 raw_codes 已赋值（具体 ts_code 列表）→ 直接用，不读 raw_df。"""
    raw_df = pd.DataFrame({'ts_code': ['600000.SH', '000001.SZ']})
    fs_codes = _resolve_fs_codes(['ALL'], raw_codes_undefined=False,
                                  raw_df=raw_df, raw_codes=['600000.SH', '000001.SZ'])
    assert fs_codes == ['600000.SH', '000001.SZ']


# ========== 3. 指定具体 codes（非占位符）==========

def test_specific_codes_preserved():
    """codes 是具体列表（非占位符）→ 直接用，不从 raw_df 提取。"""
    raw_df = pd.DataFrame({'code': ['600000', '000001', '300750']})
    fs_codes = _resolve_fs_codes(['600000', '000001'],
                                  raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['600000', '000001']


# ========== 4. raw_df 空 / 无 code 列 ==========

def test_empty_raw_df_returns_placeholder():
    """raw_df 空 + codes=['ALL'] → fs_codes 保持 ['ALL']（_prepare_close_df 会返回 None）。"""
    raw_df = pd.DataFrame({'code': [], 'end_date': []})
    fs_codes = _resolve_fs_codes(['ALL'], raw_codes_undefined=True, raw_df=raw_df)
    # raw_df 空，fallback 不执行，fs_codes 保持 ['ALL']
    assert fs_codes == ['ALL']


def test_raw_df_no_code_column():
    """raw_df 无 code 列 + codes=['ALL'] → fs_codes 保持 ['ALL']。"""
    raw_df = pd.DataFrame({'stock_id': ['600000'], 'date': ['20260330']})
    fs_codes = _resolve_fs_codes(['ALL'], raw_codes_undefined=True, raw_df=raw_df)
    assert fs_codes == ['ALL']
