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
    return PreIngestValidator.from_config(ROOT / "config" / "profiles" / "mcp_only" / "alignment_rules.json")


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
    """管线唯一性：canonical 入库路径一律过 validator，写入仅两合法通道。

    本用例口径（P2 定谳，2026-09-23）——**语义通道口径**，与权威契约一致：
      · canonical 路径：`validator.validate` ≥4 处 + `self._stamp_and_write(` 调用 ≥4 处
        （所有校验路径汇聚到 _stamp_and_write → validator 为唯一 chokepoint）；
      · 写入落点：`writer.write` **恰 2 处**，且分别位于两合法通道
        （passthrough 上下文 / `_stamp_and_write` 方法体内）——**禁止第三处裸写入**。

    ⚠️ 口径变更说明（旧断言已过期，非契约漂移）：
      原断言为 `len(write_lines) == 1`（单一写入入口假设）。该假设自
      `d960d33`（2026-08-03，passthrough 基础设施引入）起失效——passthrough 通道新增
      第二处合法 `writer.write`（服务 69 张非 canonical 表：全量覆盖、不推水位、
      不走 aligner/validator，与 canonical 表语义不同，**不可收口**）。
      权威契约 = `tests/test_writer_channel_contract.py`（`d2b0913`，2026-09-06）：
      「应恰 2 处（stamp+passthrough）+ stamp 体内必含 QFQ 防线 + passthrough 不推水位」。
      本条曾因硬编码 == 1 而长期红（约 7 周）⇒ 实质防护缺口（真出现第三处裸写入亦无信号）。
      现对齐权威契约，并以该测试为**单一规格引用**（避免两处规格再次漂移）。

    口径标注（三元）：容差＝无（精确计数）；样本口径＝**AST 语义调用点**
    （`self.writer.write(...)`，排除注释/文档串中的同名文本——与 grep 行口径不同，
    grep 计数会含同方法内多分支引用行）；子集说明＝仅 daemon.py。
    """
    import ast

    daemon_path = ROOT / "quantstudio" / "pipeline" / "daemon.py"
    daemon_src = daemon_path.read_text(encoding="utf-8")
    lines = daemon_src.split('\n')
    validate_lines = [i for i, l in enumerate(lines, 1)
                      if 'validator.validate' in l and 'def ' not in l]
    assert len(validate_lines) >= 4, f"应有 ≥4 处 validator.validate，实际 {len(validate_lines)}"
    stamp_calls = [i for i, line in enumerate(lines, 1)
                   if 'self._stamp_and_write(' in line and 'def ' not in line]
    assert len(stamp_calls) >= 4, f"应有 ≥4 处 _stamp_and_write 调用，实际 {len(stamp_calls)}"

    # --- 写入落点：AST 语义口径（排除注释/文档串中的同名文本）---
    tree = ast.parse(daemon_src)
    write_sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "write"
                and isinstance(f.value, ast.Attribute) and f.value.attr == "writer"):
            write_sites.append(node.lineno)
    assert len(write_sites) == 2, (
        f"writer.write 语义调用点应恰 2 处（stamp + passthrough），实际 {len(write_sites)}: "
        f"{write_sites} —— 出现第 3 处裸写入即通道违规（绕过 QFQ 自检/审计/契约），"
        f"请改走 _stamp_and_write 或 passthrough 通道。"
        f"权威契约见 tests/test_writer_channel_contract.py")
    assert "passthrough=True" in daemon_src, "passthrough 通道锚丢失（应含 passthrough=True）"

    # --- 通道归属：两处分别落在 passthrough 上下文与 _stamp_and_write 方法体内 ---
    stamp_def = next(i for i, line in enumerate(lines, 1)
                     if 'def _stamp_and_write(' in line)
    stamp_ends = [n.end_lineno for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_stamp_and_write"]
    stamp_end = stamp_ends[0]
    in_stamp = [ln for ln in write_sites if stamp_def < ln <= stamp_end]
    assert len(in_stamp) == 1, (
        f"_stamp_and_write 方法体内应恰 1 处 writer.write，实际 {len(in_stamp)}"
        f"（定义 L{stamp_def}-L{stamp_end}）")
    assert "_qfq_invariant_after_align" in daemon_src, \
        "stamp 通道 QFQ 自检防线锚丢失（_qfq_invariant_after_align）"
    pt_sites = [ln for ln in write_sites if ln not in in_stamp]
    assert len(pt_sites) == 1, f"passthrough 通道应恰 1 处 writer.write，实际 {len(pt_sites)}"
    pt_ln = pt_sites[0]
    pt_ctx = "\n".join(lines[max(0, pt_ln - 12): pt_ln + 3])
    assert "passthrough=True" in pt_ctx, (
        f"非 stamp 的写入落点（L{pt_ln}）必须位于 passthrough 通道"
        f"（邻近 ±12 行应见 passthrough=True）")


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
    rules = json.load(open(ROOT / "config" / "profiles" / "mcp_only" / "alignment_rules.json", encoding="utf-8"))
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
