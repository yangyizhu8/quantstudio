# -*- coding: utf-8 -*-
"""qs-deploy 管线端到端测试（对应设计文档 docs/ptrade-deploy-pipeline-design.md §12 V 项 / M3 验收）。

固件经 run_deploy 全流水线（P0→P6）必须全 PASS；产物三件（交易工件/报告/runbook）齐备。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantstudio.ptrade_deploy import run_deploy  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "deploy_fixtures"
CASES = ["deploy_daily_eod.yaml", "deploy_minute.yaml"]


@pytest.mark.parametrize("case", CASES)
def test_pipeline_all_stages_pass(case):
    report = run_deploy(str(FIXTURE_DIR / case))
    d = report.to_dict()
    assert d["status"] == "PASS", d
    for stage, payload in d["stages"].items():
        assert payload["status"] == "PASS", (stage, payload)


@pytest.mark.parametrize("case", CASES)
def test_pipeline_artifacts_delivered(case):
    d = run_deploy(str(FIXTURE_DIR / case)).to_dict()
    artifacts = d["artifacts"]
    assert set(artifacts) >= {"product", "report", "runbook"}, artifacts
    for _, p in artifacts.items():
        assert Path(p).exists(), p


def test_daily_product_contains_mount_and_persist():
    d = run_deploy(str(FIXTURE_DIR / "deploy_daily_eod.yaml")).to_dict()
    text = Path(d["artifacts"]["product"]).read_text(encoding="utf-8")
    assert "run_daily(" in text          # 日线档执行时点挂载（F4）
    assert "pickle" in text              # 持久化层注入（F2）
    assert "PTRADE-DEPLOY:PERSIST" in text   # 白名单标记（§5.4）
    assert "PTRADE-DEPLOY:GUARD" in text     # 白名单标记（§5.4）


def test_minute_product_keeps_handle_data():
    d = run_deploy(str(FIXTURE_DIR / "deploy_minute.yaml")).to_dict()
    text = Path(d["artifacts"]["product"]).read_text(encoding="utf-8")
    assert "def handle_data(" in text    # minute_keep 档保持原生命周期（§6.2）
    assert "run_daily(" not in text      # 分钟档不挂载 run_daily


def test_deterministic_output_sha():
    """同一输入双跑产物逐位一致（§7.3 哈希门禁前提；V3 前置）。"""
    import hashlib
    h = []
    for _ in range(2):
        d = run_deploy(str(FIXTURE_DIR / "deploy_daily_eod.yaml")).to_dict()
        h.append(hashlib.sha256(
            Path(d["artifacts"]["product"]).read_bytes()).hexdigest())
    assert h[0] == h[1]
