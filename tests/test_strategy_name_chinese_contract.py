"""Chinese naming contract tests (2026-08-22, 审核补充①③).

Covers:
- 补充① Windows 尾部陷阱：strategy_name 不得以 `.` 或空白（含全角空格）结尾，
  也不得以 `_`/空白开头；
- 非法文件名字符、无汉字、超长一律 BLOCK（schema pattern + 脚本双保险）；
- 补充③ 重名前置检查：新中文名与 strategies 目录任何现存文件 stem 冲突即
  R4/发布 BLOCK；`output.overwrite=true` 为显式豁免；目录缺失不 BLOCK；
- 发布文件名 = `<strategy_name>.py`（中文、无 ASCII 后缀）；候选文件名
  `<strategy_name>__candidate_quantstudio.py`；
- R5 身份匹配接受中文 strategy_file（候选 stem 与正式名 stem）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quantstudio-strategy-compiler" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_skill_common import (  # noqa: E402
    published_quantstudio_filename, strategy_name_conflict_errors,
    strategy_naming_errors, validate_design,
)
from prepare_user_backtest_candidate import prepare_candidate  # noqa: E402
from publish_agent_strategy import publish  # noqa: E402
from review_user_backtest_evidence import review_evidence  # noqa: E402
from user_backtest_flow import candidate_path  # noqa: E402
from validate_agent_strategy import validate_strategy  # noqa: E402
from tests.test_target_aware_strategy_skill import (  # noqa: E402
    local_etf_design, local_source,
)


def _cn_design() -> dict:
    """Self-contained design: normalize the unrelated execution_price_basis
    field so these tests stay green regardless of the shared fixture's state
    (its pre_adjusted_price→raw_trade_price alignment is a separate,
    not-yet-committed repair owned by another workstream)."""
    design = local_etf_design()
    design["market_data_contract"]["execution_price_basis"] = "raw_trade_price"
    return design


def _align_basis(design_path: Path) -> None:
    payload = json.loads(design_path.read_text(encoding="utf-8"))
    payload["market_data_contract"]["execution_price_basis"] = "raw_trade_price"
    design_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# --- naming contract (positive) ---------------------------------------------

def test_chinese_name_passes_schema_naming_and_validation():
    design = _cn_design()
    assert validate_design(design) == []
    assert strategy_naming_errors(design) == []
    report = validate_strategy(design, local_source(), target_profile="quantstudio")
    assert report["status"] == "PASS", report


def test_published_filename_is_chinese_name_without_ascii_suffix():
    design = _cn_design()
    assert published_quantstudio_filename(design) == "本地动态ETF轮动策略.py"


def test_candidate_path_uses_chinese_name_and_keeps_legacy_fallback():
    chinese = candidate_path("proj", "local_dynamic_etf", "本地动态ETF轮动策略")
    assert chinese.name == "本地动态ETF轮动策略__candidate_quantstudio.py"
    legacy = candidate_path("proj", "local_dynamic_etf")
    assert legacy.name == "local_dynamic_etf__candidate_quantstudio.py"


# --- naming contract (negative, 含审核补充① Windows 尾部陷阱) ------------------

BAD_NAMES = [
    "English Only Name",   # 无汉字
    "动量策略.",             # 补充①：尾点
    "动量策略 ",             # 补充①：尾空格
    "动量策略\u3000",        # 补充①：尾全角空格
    "动量策略\n",            # 补充①：尾换行（哈希绑定路径剥离风险）
    " 动量策略",             # 前导空格（Windows 不同层剥离不一致）
    "_动量策略",             # 前导下划线（PyQt 面板不显示）
    "动量/策略", "动量\\策略", "动量:策略", "动量*策略", "动量?策略",
    '动量"策略', "动量<策略", "动量>策略", "动量|策略",  # 非法文件名字符
    "策" * 51,              # 超长
]


@pytest.mark.parametrize("bad_name", BAD_NAMES)
def test_bad_names_are_blocked_by_schema_and_validator(bad_name):
    design = _cn_design()
    design["strategy_name"] = bad_name
    assert validate_design(design), bad_name  # schema pattern BLOCK
    errors = strategy_naming_errors(design)
    assert errors, bad_name
    assert all(item["rule_id"] == "STRATEGY-NAME-CONTRACT" for item in errors)
    report = validate_strategy(design, local_source(), target_profile="quantstudio")
    assert report["status"] == "BLOCKED", (bad_name, report)
    assert any(item["rule_id"] == "STRATEGY-NAME-CONTRACT"
               for item in report["issues"]), bad_name


# --- 补充③ stem 冲突前置检查 ---------------------------------------------------

def _strategies_dir_with_files(base: Path, *names: str) -> Path:
    strategies = base / "quantstudio" / "backtest" / "strategies"
    strategies.mkdir(parents=True, exist_ok=True)
    for name in names:
        (strategies / name).write_text("# existing strategy\n", encoding="utf-8")
    return strategies


def test_name_conflict_with_handwritten_chinese_file_blocks(tmp_path):
    strategies = _strategies_dir_with_files(
        tmp_path, "动量轮动策略_quantstudio.py", "双均线策略.py", "__init__.py")
    design = _cn_design()
    design["strategy_name"] = "双均线策略"
    errors = strategy_name_conflict_errors(design, strategies)
    assert errors and errors[0]["rule_id"] == "STRATEGY-NAME-CONFLICT"
    # output.overwrite=true 是客户确认的显式覆盖豁免
    design["output"]["overwrite"] = True
    assert strategy_name_conflict_errors(design, strategies) == []


def test_name_conflict_with_legacy_ascii_file_blocks(tmp_path):
    strategies = _strategies_dir_with_files(tmp_path, "fall_reversal_quantstudio.py")
    design = _cn_design()
    design["strategy_name"] = "fall_reversal_quantstudio"
    errors = strategy_name_conflict_errors(design, strategies)
    assert errors and errors[0]["rule_id"] == "STRATEGY-NAME-CONFLICT"


def test_name_conflict_check_skips_when_directory_missing(tmp_path):
    design = _cn_design()
    assert strategy_name_conflict_errors(design, None) == []
    assert strategy_name_conflict_errors(design, tmp_path / "no_such_dir") == []


def test_r4_validation_blocks_conflicting_name_via_strategies_dir(tmp_path):
    strategies = _strategies_dir_with_files(tmp_path, "双均线策略.py")
    design = _cn_design()
    design["strategy_name"] = "双均线策略"
    report = validate_strategy(design, local_source(), target_profile="quantstudio",
                               strategies_dir=strategies)
    assert report["status"] == "BLOCKED", report
    assert any(item["rule_id"] == "STRATEGY-NAME-CONFLICT" for item in report["issues"])


# --- 全流程发布：中文文件名 + 冲突前置拦截 ------------------------------------

def _publishable_workspace(tmp_path: Path):
    project = tmp_path / "project"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = project / "data" / "quantstudio.db"
    db.parent.mkdir(parents=True)
    db.touch()
    design = _cn_design()
    design_path = workspace / "agent_strategy_design.json"
    design_path.write_text(json.dumps(design), encoding="utf-8")
    strategy_path = workspace / "strategy.py"
    strategy_path.write_text(local_source(), encoding="utf-8")
    (workspace / "workspace_state.json").write_text(json.dumps({
        "stage": "BACKTEST_PASS",
        "backtest_status": "PASS",
        "backtest_data_source": "duckdb_provider",
        "backtest_db_path": str(db.resolve()),
    }), encoding="utf-8")
    return project, workspace, design_path, strategy_path


def test_publish_writes_chinese_filename_and_front_blocks_collision(tmp_path):
    project, workspace, design_path, strategy_path = _publishable_workspace(tmp_path)

    report = publish(strategy_path, design_path, project)
    formal = project / "quantstudio" / "backtest" / "strategies" / "本地动态ETF轮动策略.py"
    assert formal.exists()
    assert [t["path"] for t in report["targets"]] == [str(formal)]

    # 另一策略（不同 strategy_id）撞同一中文名：发布期前置检查必须拦截，
    # 而不是等到 FileExistsError。
    colliding = _cn_design()
    colliding["strategy_id"] = "another_rotation"
    colliding["strategy_name"] = "本地动态ETF轮动策略"
    colliding_path = workspace / "colliding_design.json"
    colliding_path.write_text(json.dumps(colliding), encoding="utf-8")
    with pytest.raises(ValueError, match="同名|STRATEGY-NAME-CONFLICT"):
        publish(strategy_path, colliding_path, project)


def test_candidate_preparation_blocks_conflicting_name(tmp_path):
    from tests.test_user_pyqt_candidate_flow import setup_workspace

    project, workspace, db, design, design_path, strategy_path = setup_workspace(tmp_path)
    _align_basis(design_path)
    _strategies_dir_with_files(project, "本地动态ETF轮动策略.py")  # 现存同名正式文件
    with pytest.raises(ValueError, match="同名|STRATEGY-NAME-CONFLICT"):
        prepare_candidate(strategy_path, design_path, project)


# --- R5 身份匹配接受中文 strategy_file ----------------------------------------

def test_r5_identity_accepts_chinese_formal_name_stem(tmp_path):
    """config.csv strategy_file 为中文正式名 stem（用户跑了正式文件而非候选）：
    identity 集合 {strategy_id, strategy_name, candidate_stem} 必须接受。"""
    from tests.test_user_pyqt_candidate_flow import (
        evidence_payload, setup_workspace, write_artifacts,
    )

    project, workspace, db, design, design_path, strategy_path = setup_workspace(tmp_path)
    _align_basis(design_path)
    candidate = prepare_candidate(strategy_path, design_path, project)
    result_dir = write_artifacts(workspace / "result", strategy_name="本地动态ETF轮动策略")
    evidence_path = workspace / "formal_name_evidence.json"
    evidence_path.write_text(json.dumps(evidence_payload(candidate, db, result_dir)),
                             encoding="utf-8")
    report = review_evidence(strategy_path, design_path, evidence_path, project)
    assert report["status"] == "PASS", report
