# -*- coding: utf-8 -*-
"""api_portability 三公有 API 放宽 + 检测点迁移测试（2026-11-11 二审 A 项）。

覆盖（用户指令四项 + 二审 B6 三硬约束）：
  A-1  is_trade / set_commission 不再按「本地专属禁止调」DENY 拦截（公有 API）；
  A-2  get_research_path 不再按 PORTABILITY-FILE-DB-ACCESS 名单拦截；
  A-3  FILE-DB-ACCESS 检测点迁移：按真实文件访问调用点（AST，禁正则）匹配
       IO 调用面 × 路径条件（_PD_NOTEBOOK / get_research_path() 返回值 /
       含 '/' 或 '\\\\' 的字面量路径）；
  A-4  非 PTrade 公有 Profile 的本地专属 API 仍必须 BLOCK（一条不放开）；
  B6-a IO 调用面含完整清单（pathlib/csv/pickle/shutil/subprocess/os.remove 等）；
  B6-b 动态取函数（getattr 非字面量 / 别名赋值后调用）→ WARN（不得静默放行）；
  B6-c 负向三用例仍被拦：别名调用本地专属 API / getattr 动态取本地专属 API /
       pathlib 写文件。
"""

from __future__ import annotations

import pytest

from quantstudio.strategy_compiler.portability_rules import (
    DENY_REMOVE,
    PTRADE_REGISTERED_WARN,
    denylist,
)
from quantstudio.strategy_compiler.validators.validate_ptrade_portability import (
    validate_ptrade_portability,
)


def _src(*body_lines: str, imports: str = "", module_level: str = "") -> str:
    """构造最小可解析策略源码（body_lines 缩进进 handle_data）。"""
    out = imports + "\n" if imports else ""
    out += module_level + "\n" if module_level else ""
    out += "def initialize(context):\n    pass\n\n\ndef handle_data(context, data):\n"
    out += "\n".join("    " + ln for ln in body_lines) + "\n"
    return out


def _validate(code: str):
    return validate_ptrade_portability(code)


def _ids(violations) -> list[str]:
    return [v.rule_id for v in violations]


def _blocks(violations, rule_id: str) -> list:
    return [v for v in violations
            if v.rule_id == rule_id and v.severity == "BLOCK"]


# ===========================================================================
# 名单落位断言（A-1/A-2 静态面）
# ===========================================================================
class TestRuleListRelocation:
    def test_three_public_apis_out_of_denylist(self):
        """A-1/A-2：三公有 API 不在 DENY 名单。"""
        for name in ("is_trade", "set_commission", "get_research_path"):
            assert name not in DENY_REMOVE
            assert name not in denylist()

    def test_public_apis_registered_warn(self):
        """公有化迁入 PTRADE_REGISTERED_WARN（转换器 KEEP 分类承接）。"""
        for name in ("is_trade", "set_commission", "get_research_path"):
            assert name in PTRADE_REGISTERED_WARN

    def test_local_only_deny_untouched(self):
        """A-4：本地专属 API 名单语义保留，一条不放开。"""
        assert {"set_backtest", "get_etf_list_local", "get_strategy_events",
                "create_dir"} <= DENY_REMOVE
        assert denylist() >= DENY_REMOVE


# ===========================================================================
# A-1/A-2：公有 API 调用不再报违规
# ===========================================================================
class TestPublicApiRelaxed:
    def test_is_trade_no_longer_blocked(self):
        code = _src(
            "if is_trade():",
            "    log.info('trading mode')",
            "if not is_trade():",
            "    set_commission(commission_ratio=5e-05, min_commission=5.0, type='ETF')",
        )
        ok, violations, _ = _validate(code)
        assert ok, f"is_trade/set_commission 公有 API 不应 BLOCK: {violations}"
        assert not [v for v in violations if "is_trade" in v.message]
        assert not [v for v in violations if "set_commission" in v.message]

    def test_set_commission_valid_keyword_call_passes(self):
        code = _src("set_commission(commission_ratio=0.0003, min_commission=5.0, type='STOCK')",
                    "set_commission(commission_ratio=0.0003, min_commission=5.0, type='ETF')",
                    "set_commission(commission_ratio=0.0003, min_commission=5.0, type='LOF')")
        ok, violations, _ = _validate(code)
        assert ok, violations

    def test_set_commission_platform_contract_checks_kept(self):
        """A-4 负向不放松：平台契约校验（位置参数/≤0 值）保留为 BLOCK。"""
        code = _src("set_commission('ETF', 0.0001, 5.0)")
        ok, violations, _ = _validate(code)
        assert not ok
        assert _blocks(violations, "PORTABILITY-COMMISSION-POSITIONAL")

        code2 = _src("set_commission(commission_ratio=0.0, min_commission=5.0)")
        ok2, violations2, _ = _validate(code2)
        assert not ok2
        assert _blocks(violations2, "PORTABILITY-COMMISSION-MIN-ZERO")

    def test_get_research_path_alone_not_flagged(self):
        """A-2：get_research_path() 本身公有，不再报 FILE-DB-ACCESS。"""
        code = _src("root = get_research_path()")
        ok, violations, _ = _validate(code)
        assert ok, violations
        assert not _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")


# ===========================================================================
# A-3：FILE-DB-ACCESS 检测点迁移（真实文件访问调用点 × 路径条件）
# ===========================================================================
class TestFileDbCallSiteDetection:
    def test_open_with_pd_notebook_blocked_at_open_site(self):
        code = _src("with open(_PD_NOTEBOOK + 'state.pkl', 'rb') as _pf:",
                    "    _saved = pickle.load(_pf)",
                    imports="import pickle\n",
                    module_level="try:\n    _PD_NOTEBOOK = get_research_path()\nexcept Exception:\n    _PD_NOTEBOOK = ''\n")
        ok, violations, _ = _validate(code)
        assert not ok
        file_db = _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")
        assert file_db, "应在 open() 调用处报 FILE-DB-ACCESS"
        assert any("open" in v.message for v in file_db)
        # 检测点=真实 IO 调用处：pickle.load(_pf) 本身无路径条件，不应单独报
        assert not any("pickle.load" in v.message for v in file_db)
        # get_research_path() 调用本身不再按 API 名报（检测点已迁移）
        assert not any("calls get_research_path()" in v.message for v in violations)

    def test_open_with_get_research_path_return_blocked(self):
        code = _src("with open(get_research_path() + 'state.pkl', 'wb') as _pf:",
                    "    pass")
        ok, violations, _ = _validate(code)
        assert not ok
        assert _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")

    def test_open_with_separator_literal_blocked(self):
        code = _src("with open('/home/fly/notebook/state.pkl', 'rb') as _pf:",
                    "    pass")
        ok, violations, _ = _validate(code)
        assert not ok
        assert _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")

    def test_open_relative_no_separator_not_flagged(self):
        """路径条件不满足（无 _PD_NOTEBOOK/返回值/分隔符字面量）不拦（spec 精确边界）。"""
        code = _src("with open('state.pkl', 'rb') as _pf:",
                    "    pass")
        ok, violations, _ = _validate(code)
        assert ok, violations
        assert not _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")

    def test_pickle_dump_without_path_condition_not_flagged(self):
        """pickle.dump 本身无路径条件（句柄参数）→ 不拦（与直接形态边界一致）。"""
        code = _src("pickle.dump(g.state, _buf)",
                    imports="import pickle\n")
        ok, violations, _ = _validate(code)
        assert ok, violations
        assert not _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")


# ===========================================================================
# B6-a：IO 调用面完整清单（不得只做 open）
# ===========================================================================
class TestIoSurfaceBreadth:
    def _assert_blocked(self, code: str, needle: str = ""):
        ok, violations, _ = _validate(code)
        blocks = _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")
        assert blocks, f"IO 调用面 {needle} 未被拦截: {violations}"
        if needle:
            assert any(needle in v.message for v in blocks), \
                f"未见 {needle} 调用点: {[v.message for v in blocks]}"
        return violations

    def test_os_path_family(self):
        self._assert_blocked(_src("p = os.path.join(_PD_NOTEBOOK, 'a.csv')",
                                  imports="import os\n"), "os.path.join")
        self._assert_blocked(_src("os.path.exists('/tmp/x')",
                                  imports="import os\n"), "os.path.exists")

    def test_os_remove_makedirs_rename_rmdir(self):
        self._assert_blocked(_src("os.remove('/tmp/x')", imports="import os\n"), "os.remove")
        self._assert_blocked(_src("os.makedirs('/tmp/dir')", imports="import os\n"), "os.makedirs")
        self._assert_blocked(_src("os.rename('/tmp/a', '/tmp/b')", imports="import os\n"), "os.rename")
        self._assert_blocked(_src("os.rmdir('/tmp/dir')", imports="import os\n"), "os.rmdir")

    def test_json_and_csv(self):
        self._assert_blocked(
            _src("json.dump(g.state, open('/home/fly/notebook/s.json', 'w'))",
                 imports="import json\n"), "json.dump")
        self._assert_blocked(
            _src("s = json.load(open('/home/fly/notebook/s.json'))",
                 imports="import json\n"), "json.load")
        self._assert_blocked(
            _src("for row in csv.reader(open('/home/fly/notebook/s.csv')):",
                 "    pass", imports="import csv\n"), "csv.reader")
        self._assert_blocked(
            _src("w = csv.writer(open('/home/fly/notebook/s.csv', 'w'))",
                 imports="import csv\n"), "csv.writer")

    def test_shutil_and_subprocess_any_attr(self):
        self._assert_blocked(_src("shutil.rmtree('/tmp/dir')",
                                  imports="import shutil\n"), "shutil.rmtree")
        self._assert_blocked(_src("shutil.copyfile('/tmp/a', '/tmp/b')",
                                  imports="import shutil\n"), "shutil.copyfile")
        self._assert_blocked(_src("subprocess.run(['cp', '/tmp/a', '/tmp/b'])",
                                  imports="import subprocess\n"), "subprocess.run")

    def test_pickle_with_path_condition(self):
        self._assert_blocked(
            _src("with open(_PD_NOTEBOOK + 's.pkl', 'wb') as _pf:",
                 "    pickle.dump(g.state, _pf)",
                 imports="import pickle\n",
                 module_level="_PD_NOTEBOOK = get_research_path()\n"), "open")

    def test_pd_read_family(self):
        self._assert_blocked(_src("df = pd.read_csv('/home/fly/notebook/s.csv')",
                                  imports="import pandas as pd\n"), "read_csv")
        self._assert_blocked(_src("df = pd.read_parquet('/home/fly/notebook/s.parquet')",
                                  imports="import pandas as pd\n"), "read_parquet")

    def test_dataframe_export_methods(self):
        self._assert_blocked(_src("df.to_csv(_PD_NOTEBOOK + 's.csv')",
                                  module_level="_PD_NOTEBOOK = get_research_path()\n"),
                             "to_csv")
        self._assert_blocked(_src("df.to_parquet('/tmp/s.parquet')"), "to_parquet")
        self._assert_blocked(_src("df.to_pickle(_PD_NOTEBOOK + 's.pkl')",
                                  module_level="_PD_NOTEBOOK = get_research_path()\n"),
                             "to_pickle")

    def test_pathlib_five_methods(self):
        imports = "from pathlib import Path\n"
        self._assert_blocked(_src("Path(_PD_NOTEBOOK + 's.txt').write_text('x')",
                                  module_level="_PD_NOTEBOOK = get_research_path()\n",
                                  imports=imports), "write_text")
        self._assert_blocked(_src("t = Path('/home/fly/notebook/s.txt').read_text()",
                                  imports=imports), "read_text")
        self._assert_blocked(_src("b = Path('/home/fly/notebook/s.bin').read_bytes()",
                                  imports=imports), "read_bytes")
        self._assert_blocked(_src("Path('/home/fly/notebook/s.bin').write_bytes(b'x')",
                                  imports=imports), "write_bytes")
        self._assert_blocked(_src("with Path('/home/fly/notebook/s.txt').open() as f:",
                                  "    pass", imports=imports), "open")

    def test_import_alias_and_from_import_forms(self):
        """import 别名 / from-import 成员形态归一后仍拦（防按别名绕过）。"""
        self._assert_blocked(
            _src("pk.dump(g.state, open('/home/fly/notebook/s.pkl', 'wb'))",
                 imports="import pickle as pk\n"), "pickle.dump")
        self._assert_blocked(
            _src("dump(g.state, open('/home/fly/notebook/s.pkl', 'wb'))",
                 imports="from pickle import dump\n"), "pickle.dump")
        self._assert_blocked(
            _src("p = osp.join(_PD_NOTEBOOK, 'a')",
                 imports="import os.path as osp\n",
                 module_level="_PD_NOTEBOOK = get_research_path()\n"), "os.path.join")


# ===========================================================================
# B6-c：负向三用例（必须仍被拦）
# ===========================================================================
class TestB6cNegatives:
    def test_alias_call_local_only_api_blocked(self):
        """B6-c(1)：别名赋值后调用本地专属 API → 仍 BLOCK。"""
        code = _src(
            "f = set_backtest",
            "f()",
            "alias2 = f",
            "alias2(1)",
        )
        ok, violations, _ = _validate(code)
        assert not ok
        blocks = _blocks(violations, "PORTABILITY-LOCAL-API")
        assert blocks, "别名调用 set_backtest 必须 BLOCK"
        assert any("alias" in v.message for v in blocks)

    def test_getattr_local_only_api_blocked(self):
        """B6-c(2)：getattr 动态取本地专属 API（字面量可解析）→ 仍 BLOCK。"""
        code = _src(
            "f = getattr(g, 'get_etf_list_local')",
            "if f:",
            "    pass",
        )
        ok, violations, _ = _validate(code)
        assert not ok
        assert _blocks(violations, "PORTABILITY-LOCAL-API")

        code2 = _src("getattr(g, 'get_etf_list_local')()")
        ok2, violations2, _ = _validate(code2)
        assert not ok2
        assert _blocks(violations2, "PORTABILITY-LOCAL-API")

    def test_pathlib_write_blocked(self):
        """B6-c(3)：pathlib 写文件（研究路径）→ 仍 BLOCK。"""
        code = _src(
            "Path(_PD_NOTEBOOK + 'state.pkl').write_text('x')",
            imports="from pathlib import Path\n",
            module_level="_PD_NOTEBOOK = get_research_path()\n",
        )
        ok, violations, _ = _validate(code)
        assert not ok
        assert _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")


# ===========================================================================
# B6-b：动态取函数 → WARN（不得静默放行；WARN 不翻 ok）
# ===========================================================================
class TestB6bDynamicWarn:
    def test_getattr_non_literal_warns_not_blocks(self):
        code = _src(
            "for name in ('amount', 'volume'):",
            "    v = getattr(g.pos, name, 0)",
        )
        ok, violations, _ = _validate(code)
        warns = [v for v in violations
                 if v.rule_id == "PORTABILITY-DYNAMIC-CALL" and v.severity == "WARN"]
        assert warns, "getattr 非字面量参数必须产出 WARN（不得静默放行）"
        assert ok, "WARN 不得翻 ok"

    def test_alias_call_unresolvable_warns(self):
        code = _src(
            "f = some_unknown_helper",
            "f(context, data)",
        )
        ok, violations, _ = _validate(code)
        warns = [v for v in violations
                 if v.rule_id == "PORTABILITY-DYNAMIC-CALL" and v.severity == "WARN"]
        assert warns, "别名赋值后调用（不可证实安全）必须产出 WARN"
        assert ok

    def test_alias_to_local_def_and_builtin_silent(self):
        """静态可证实非绕过（本文件 def / 内建）的别名调用不产生 WARN 噪声。"""
        code = _src(
            "h = _my_helper",
            "h()",
            "m = max",
            "r = m(1, 2)",
            module_level="def _my_helper():\n    return 1\n",
        )
        ok, violations, _ = _validate(code)
        assert ok
        assert not [v for v in violations
                    if v.rule_id == "PORTABILITY-DYNAMIC-CALL"], violations


# ===========================================================================
# A-4：本地专属 API 仍 BLOCK（负向不放松）
# ===========================================================================
class TestA4LocalOnlyStillBlocked:
    @pytest.mark.parametrize("expr,rule", [
        ("set_backtest(True)", "PORTABILITY-LOCAL-API"),
        ("get_etf_list_local()", "PORTABILITY-LOCAL-API"),
        ("get_strategy_events('x')", "PORTABILITY-LOCAL-API"),
        ("create_dir('/local/db')", "PORTABILITY-FILE-DB-ACCESS"),
        ("get_trades_file()", "PORTABILITY-FILE-DB-ACCESS"),
        ("convert_position_from_csv('pos.csv')", "PORTABILITY-FILE-DB-ACCESS"),
        ("load_research_signals('x.csv')", "PORTABILITY-LOCAL-API"),
        ("SharedCostModel()", "PORTABILITY-LOCAL-API"),
        ("get_history_batch(g.stock_list, 5)", "PORTABILITY-LOCAL-EXTENSION-BAN"),
        ("get_fundamentals_batch([], 'eps')", "PORTABILITY-LOCAL-EXTENSION-BAN"),
    ])
    def test_direct_calls_blocked(self, expr, rule):
        code = _src(expr)
        ok, violations, _ = _validate(code)
        assert not ok, f"{expr} 必须 BLOCK"
        assert _blocks(violations, rule), f"{expr} 应报 {rule}: {violations}"


# ===========================================================================
# 回归：四象限产物关键形态（不依赖外部文件的最小复刻）
# ===========================================================================
class TestFourQuadrantShapes:
    """四象限 ETF_f926a7_trade.py 的三类关键形态（公有化后）：

    - is_trade()/set_commission → 无违规；
    - get_research_path() 赋 _PD_NOTEBOOK → 不再报 FILE-DB；
    - open(_PD_NOTEBOOK + ...) 两处 → 在 open() 调用处报 FILE-DB-ACCESS。
    """

    CODE = (
        "import pickle\n"
        "try:\n"
        "    _PD_NOTEBOOK = get_research_path()\n"
        "except Exception:\n"
        "    _PD_NOTEBOOK = ''\n"
        "def initialize(context):\n"
        "    try:\n"
        "        with open(_PD_NOTEBOOK + 'ETF_f926a7_state.pkl', 'rb') as _pf:\n"
        "            _saved_state = pickle.load(_pf)\n"
        "    except Exception:\n"
        "        _saved_state = None\n"
        "    g.state = _saved_state if _saved_state is not None else {'version': 1}\n"
        "    set_benchmark('000300.SS')\n"
        "    if not is_trade():\n"
        "        set_commission(commission_ratio=5e-05, min_commission=5.0, type='ETF')\n"
        "    set_universe(['510300.SS'])\n"
        "def handle_data(context, data):\n"
        "    if is_trade():\n"
        "        log.info('trading')\n"
        "def after_trading_end(context, data):\n"
        "    try:\n"
        "        with open(_PD_NOTEBOOK + 'ETF_f926a7_state.pkl', 'wb') as _pf:\n"
        "            pickle.dump(g.state, _pf, -1)\n"
        "    except Exception as _pe:\n"
        "        log.warning('persist failed: %s' % _pe)\n"
    )

    def test_is_trade_and_set_commission_violations_gone(self):
        _, violations, _ = _validate(self.CODE)
        local_api = [v for v in violations
                     if v.rule_id in ("PORTABILITY-LOCAL-API",
                                      "PORTABILITY-LOCAL-EXTENSION-BAN")]
        assert not local_api, f"is_trade 相关 LOCAL-API 违规应消失: {local_api}"

    def test_no_name_based_get_research_path_flag(self):
        """不再按 API 名报 get_research_path 为 FILE-DB-ACCESS（检测点已迁移）。"""
        _, violations, _ = _validate(self.CODE)
        file_db = _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")
        for v in file_db:
            assert "calls get_research_path()" not in v.message
            assert "open" in v.message or "operand uses" in v.message

    def test_file_db_reported_at_open_call_sites(self):
        ok, violations, _ = _validate(self.CODE)
        assert not ok
        file_db = _blocks(violations, "PORTABILITY-FILE-DB-ACCESS")
        assert len(file_db) == 2, f"两处 open() 调用点各报一条: {file_db}"
        lines = {v.location for v in file_db}
        assert any("line 8" in loc for loc in lines), lines   # 'rb' 读
        assert any("line 22" in loc for loc in lines), lines  # 'wb' 写
