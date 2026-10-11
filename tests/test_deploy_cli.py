# -*- coding: utf-8 -*-
"""qs-deploy CLI 壳测试（M4；设计文档 §7.1/§7.3 + docs/ptrade-deploy-pipeline-design.md）。

- CLI deploy 子命令端到端 exit 0；
- **parity 门禁**：CLI 壳产物与内核 run_deploy 直调产物 SHA256 逐位一致（§7.3）；
- 用户输入错 exit 2；内核失败结构化传播 exit 1；
- 既有子命令（package/import）注册与 --help 冒烟零回归。
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402
from quantstudio.strategy_compiler.cli import build_parser, main  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"
DAILY_CFG = FIXTURE_DIR / "deploy_daily_eod.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_deploy_fixture_passes():
    assert main(["deploy", str(DAILY_CFG)]) == 0


def test_cli_kernel_parity_sha():
    """同一配置：CLI 壳产物 == 内核直调产物（哈希门禁，§7.3 的 CLI 侧形态）。"""
    kernel = run_deploy(str(DAILY_CFG)).to_dict()
    assert kernel["status"] == "PASS"
    h_kernel = _sha(Path(kernel["artifacts"]["product"]))

    assert main(["deploy", str(DAILY_CFG)]) == 0
    h_cli = _sha(Path(kernel["artifacts"]["product"]))  # 同路径确定性重写
    assert h_cli == h_kernel, (h_cli, h_kernel)


def test_cli_missing_config_exit2():
    assert main(["deploy", str(FIXTURE_DIR / "nope.yaml")]) == 2


def test_cli_bad_suffix_exit2(tmp_path):
    bad = tmp_path / "deploy.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["deploy", str(bad)]) == 2


def test_cli_kernel_failure_exit1(tmp_path):
    bad = tmp_path / "deploy_bad.yaml"
    bad.write_text("strategy_id: bad_case\nsource: nonexistent_strategy.py\n"
                   "execution:\n  profile: daily_eod_1450\n", encoding="utf-8")
    assert main(["deploy", str(bad)]) == 1


def test_existing_subcommands_registered():
    """既有子命令 package/import 零回归 + deploy 新增注册（冒烟门）。"""
    parser = build_parser()
    actions = {a for a in parser._subparsers._group_actions[0]._name_parser_map}
    assert {"package", "import", "deploy"} <= actions


def test_import_help_smoke():
    with pytest.raises(SystemExit) as exc:
        main(["import", "--help"])
    assert exc.value.code == 0
