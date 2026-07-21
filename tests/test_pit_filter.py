"""清洗 PIT 能力测试（D10 强化：validator 入库层 AnnDateLogic 校验）。

验证目标：
1. AnnDateLogic 规则存在，是 validate 的第 10 步
2. ann_date < end_date（公告日早于报告期末日）→ REJECT（未来函数/数据错误）
3. ann_date 远在未来 → REJECT（异常数据）
4. ann_date == end_date（报告期末当天公告）→ PASS（合法）
5. 无 ann_date 的表（行情表 stock_daily 等）→ 自动跳过（不影响现有流程）
6. 管线唯一性：所有入库路径（增量/常驻）都过 validator（PIT 自动覆盖）
"""
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def _make_validator(table='balance_statement'):
    """构造 validator（用项目真实 alignment_rules.json）"""
    from quantstudio.pipeline.validator import PreIngestValidator
    return PreIngestValidator.from_config(ROOT / "config" / "alignment_rules.json")


# ========== AnnDateLogic 规则存在性 ==========

def test_anndate_logic_rule_exists():
    """PIT 规则 AnnDateLogic 已在 validator 中（D10 第 10 步）"""
    from quantstudio.pipeline.validator import PreIngestValidator
    src = Path(PreIngestValidator.__module__).read_text() if False else \
          (ROOT / "quantstudio" / "pipeline" / "validator.py").read_text(encoding="utf-8")
    assert "AnnDateLogic" in src, "validator 缺 AnnDateLogic 规则（D10 PIT 未实现）"
    assert "ann_date<end_date" in src


# ========== 规则 10a：ann_date < end_date 被拒绝 ==========

def test_anndate_before_enddate_rejected():
    """ann_date < end_date → REJECT（未来函数：报告还没结束就公告了）"""
    validator = _make_validator('balance_statement')
    # 构造违规数据：报告期 2025-12-31，但公告日 2025-06-30（报告还没结束就公告）
    end_20251231 = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann_20250630 = int(pd.Timestamp('2025-06-30').timestamp() * 1000)  # 早于 end_date
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end_20251231, 'ann_date': ann_20250630,
        'total_assets': 1e10, 'total_liabilities': 5e9, 'total_equity': 5e9,
    }])
    res = validator.validate(df, 'balance_statement', 'batch_pit_test', 'xtquant')
    assert len(res.rejected_rows) == 1
    assert any('AnnDateLogic' in r for r in res.rejected_rules[0])


def test_anndate_equal_enddate_passed():
    """ann_date == end_date → PASS（报告期末当天公告，合法）"""
    validator = _make_validator('balance_statement')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': end,  # 同日，合法
        'total_assets': 1e10, 'total_liabilities': 5e9, 'total_equity': 5e9,
    }])
    res = validator.validate(df, 'balance_statement', 'batch_pit_test', 'xtquant')
    assert len(res.rejected_rows) == 0, f"同日公告不应被拒，实际拒绝: {res.rejected_rules}"
    assert len(res.passed_df) == 1


def test_anndate_after_enddate_passed():
    """ann_date > end_date → PASS（正常情况：报告期结束后某天才公告）"""
    validator = _make_validator('balance_statement')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2026-03-30').timestamp() * 1000)  # 报告期 3 个月后公告
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'total_assets': 1e10, 'total_liabilities': 5e9, 'total_equity': 5e9,
    }])
    res = validator.validate(df, 'balance_statement', 'batch_pit_test', 'xtquant')
    assert len(res.rejected_rows) == 0
    assert len(res.passed_df) == 1


# ========== 规则 10b：ann_date 远在未来被拒绝 ==========

def test_anndate_in_far_future_rejected():
    """ann_date 远在未来（>当前+7天）→ REJECT（异常数据）"""
    validator = _make_validator('balance_statement')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    # 公告日设为 2030 年（远未来）
    ann_future = int(pd.Timestamp('2030-01-01').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann_future,
        'total_assets': 1e10, 'total_liabilities': 5e9, 'total_equity': 5e9,
    }])
    res = validator.validate(df, 'balance_statement', 'batch_pit_test', 'xtquant')
    assert len(res.rejected_rows) == 1
    assert any('AnnDateLogic' in r for r in res.rejected_rules[0])


# ========== 无 ann_date 的表自动跳过（不影响行情表）==========


def test_stock_float_share_ann_lt_end_warned_not_rejected():
    """stock_float_share 的 ann_date<end_date 应 WARN 放行（股本生效日早于公告日是正常业务）"""
    validator = _make_validator('stock_float_share')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2025-06-30').timestamp() * 1000)  # 早于 end_date
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'free_share': 1000.0, 'total_share': 5000.0,
        'circ_mv': 1e8, 'total_mv': 2e8,
    }])
    res = validator.validate(df, 'stock_float_share', 'batch_test', 'xtquant')
    # 不应被 AnnDateLogic 拒绝（WARN 放行）
    rules_flat = [r for rules in res.rejected_rules for r in rules]
    assert not any('ann_date<end_date' in r for r in rules_flat), \
        "stock_float_share 的 ann_date<end_date 应 WARN 放行，不应 REJECT"


def test_no_anndate_table_skipped():
    """stock_daily 无 ann_date/end_date → AnnDateLogic 自动跳过，不影响行情"""
    validator = _make_validator('stock_daily')
    day_ms = int(pd.Timestamp('2026-01-05').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'time': day_ms,
        'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2,
        'volume': 1000000, 'amount': 1e7, 'preClose': 10.0, 'pctChg': 2.0,
        'turn': 1.5, 'isST': 0, 'suspendFlag': 0,
    }])
    # 不应因 AnnDateLogic 报错（stock_daily 无 ann_date）
    res = validator.validate(df, 'stock_daily', 'batch_test', 'tushare')
    # 行情数据应通过（除非命中其他规则，但不应是 AnnDateLogic）
    pit_rejects = [r for r in res.rejected_rules if any('AnnDateLogic' in x for x in r)] if res.rejected_rules else []
    assert len(pit_rejects) == 0, "stock_daily 不应触发 AnnDateLogic"


# ========== 混合：合法+违规同时存在，只拒违规 ==========

def test_mixed_valid_and_invalid_only_rejects_invalid():
    """一批数据里部分违规部分合法，只拒违规的，合法的入库"""
    validator = _make_validator('balance_statement')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann_ok = int(pd.Timestamp('2026-03-30').timestamp() * 1000)
    ann_bad = int(pd.Timestamp('2025-06-30').timestamp() * 1000)  # 早于 end_date
    df = pd.DataFrame([
        {'code': '000001', 'end_date': end, 'ann_date': ann_ok,
         'total_assets': 1e10, 'total_liabilities': 5e9, 'total_equity': 5e9},
        {'code': '000002', 'end_date': end, 'ann_date': ann_bad,  # 违规
         'total_assets': 2e10, 'total_liabilities': 1e10, 'total_equity': 1e10},
        {'code': '000003', 'end_date': end, 'ann_date': ann_ok,
         'total_assets': 3e10, 'total_liabilities': 1.5e10, 'total_equity': 1.5e10},
    ])
    res = validator.validate(df, 'balance_statement', 'batch_pit_test', 'xtquant')
    assert len(res.rejected_rows) == 1           # 只拒 000002
    assert len(res.passed_df) == 2          # 000001 + 000003 通过
    assert res.passed_df['code'].tolist() == ['000001', '000003']


# ========== 管线唯一性：所有入库路径过 validator ==========

def test_validator_is_single_chokepoint():
    """管线唯一性：所有校验路径汇聚到唯一写入 chokepoint。"""
    daemon_src = (ROOT / "quantstudio" / "pipeline" / "daemon.py").read_text(encoding="utf-8")
    lines = daemon_src.split('\n')
    validate_lines = [i for i, l in enumerate(daemon_src.split('\n'), 1)
                      if 'validator.validate' in l and 'def ' not in l]
    assert len(validate_lines) >= 4, f"应有 ≥4 处 validator.validate，实际 {len(validate_lines)}"
    stamp_calls = [i for i, line in enumerate(lines, 1)
                   if 'self._stamp_and_write(' in line and 'def ' not in line]
    assert len(stamp_calls) >= 4, f"应有 ≥4 处 _stamp_and_write 调用，实际 {len(stamp_calls)}"
    write_lines = [i for i, line in enumerate(lines, 1)
                   if 'writer.write' in line and 'def ' not in line]
    assert len(write_lines) == 1, f"writer.write 应仅存在于统一入口，实际 {len(write_lines)}"
    stamp_def = next(i for i, line in enumerate(lines, 1)
                     if 'def _stamp_and_write(' in line)
    assert write_lines[0] > stamp_def, "writer.write 必须位于 _stamp_and_write 内"


# ========== 规则 11：PositiveNumeric（负值/0 脏数据）==========

def test_negative_free_share_rejected():
    """规则 11：free_share <= 0 → REJECT（schema 声明 gt:0）"""
    validator = _make_validator('stock_float_share')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2026-03-30').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'free_share': -1000.0, 'total_share': 5000.0,
        'circ_mv': 1e8, 'total_mv': 2e8,
    }])
    res = validator.validate(df, 'stock_float_share', 'batch_test', 'xtquant')
    assert len(res.rejected_rows) >= 1
    rules_flat = [r for rules in res.rejected_rules for r in rules]
    assert any('PositiveNumeric' in r for r in rules_flat)


def test_zero_free_share_rejected():
    """规则 11：free_share == 0 → REJECT"""
    validator = _make_validator('stock_float_share')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2026-03-30').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'free_share': 0.0, 'total_share': 5000.0,
        'circ_mv': 1e8, 'total_mv': 2e8,
    }])
    res = validator.validate(df, 'stock_float_share', 'batch_test', 'xtquant')
    assert len(res.rejected_rows) >= 1
    rules_flat = [r for rules in res.rejected_rules for r in rules]
    assert any('PositiveNumeric' in r for r in rules_flat)


# ========== 规则 12：InfCheck（无穷值）==========

def test_inf_value_rejected():
    """规则 12：float('inf') → REJECT"""
    validator = _make_validator('stock_float_share')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2026-03-30').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'free_share': float('inf'), 'total_share': 5000.0,
        'circ_mv': 1e8, 'total_mv': 2e8,
    }])
    res = validator.validate(df, 'stock_float_share', 'batch_test', 'xtquant')
    assert any('InfCheck' in r for r in res.rejected_rules[0])


def test_negative_inf_rejected():
    """规则 12：float('-inf') → REJECT"""
    validator = _make_validator('stock_float_share')
    end = int(pd.Timestamp('2025-12-31').timestamp() * 1000)
    ann = int(pd.Timestamp('2026-03-30').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'end_date': end, 'ann_date': ann,
        'free_share': float('-inf'), 'total_share': 5000.0,
        'circ_mv': 1e8, 'total_mv': 2e8,
    }])
    res = validator.validate(df, 'stock_float_share', 'batch_test', 'xtquant')
    assert any('InfCheck' in r for r in res.rejected_rules[0])


# ========== 规则 13：ExtremeValue（WARN 不拒）==========

def test_extreme_pe_warned_not_rejected():
    """规则 13：peTTM > 1e6 → WARN 放行（亏损股 PE 极大正常，不拒绝）"""
    validator = _make_validator('stock_daily')
    day_ms = int(pd.Timestamp('2026-01-05').timestamp() * 1000)
    df = pd.DataFrame([{
        'code': '000001', 'time': day_ms,
        'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2,
        'volume': 1000000, 'amount': 1e7, 'preClose': 10.0, 'pctChg': 2.0,
        'turn': 1.5, 'isST': 0, 'suspendFlag': 0,
        'peTTM': 2_000_000.0,
    }])
    res = validator.validate(df, 'stock_daily', 'batch_test', 'tushare')
    extreme_rejects = [r for r in res.rejected_rules
                       if any('ExtremeValue' in x for x in r)] if res.rejected_rules else []
    assert len(extreme_rejects) == 0, "ExtremeValue 应 WARN 不 REJECT"


# ========== 新 schema 生效验证 ==========

def test_stock_float_share_schema_has_anndate():
    """schema 已含 ann_date + end_date，主键改为 (code,end_date,ann_date)"""
    import json
    rules = json.load(open(ROOT / "config" / "alignment_rules.json", encoding="utf-8"))
    s = rules['schemas']['stock_float_share']
    assert 'ann_date' in s['columns']
    assert 'end_date' in s['columns']
    assert s['primary_key'] == ['code', 'end_date', 'ann_date']
    assert s['time_key'] == 'end_date'
    assert s['columns']['free_share'].get('gt') == 0  # 规则 11 触发条件


def test_stock_float_share_ddl_has_anndate():
    """writers.py 的 DDL 已含 ann_date + end_date + 新主键"""
    src = (ROOT / "quantstudio" / "pipeline" / "writers.py").read_text(encoding="utf-8")
    assert "code VARCHAR, end_date BIGINT, ann_date BIGINT" in src
    assert "PRIMARY KEY(code, end_date, ann_date)" in src
