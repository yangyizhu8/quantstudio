"""PD-2026-0019 IMPORT-FRESHNESS 闸门固件（sidecar 形态 · 裁定乙）。

背景：deploy.yaml 的 `source` 指向 output/ptrade_export 的既有**第一跳产物**；若该产物
早于第一跳源码，则第一跳的代码改动不参与渲染 ⇒ 产物**看似全新、内核陈旧**（假重转），
而 P0–P6 全 PASS 不报任何异常（实测 2026-11-06：产物 11-03 09:23 vs source_import.py
11-06 22:14，产物中 _qs_idempotent_admit / qs_cash_reserved 均 0 处）。

判据载体演进（三次定性）：
  1. FS mtime ✗ —— 会被 git 检出/操作重置，测试夹具 mtime 只是「写入时刻」⇒ 误伤 19 条既有固件。
  2. 产物内嵌注释 ✗ —— 第二跳 AST 往返丢弃注释 ⇒ 交付产物无标记；且使产物哈希跨次不可复现。
  3. **旁挂 sidecar ✓** —— `<product>.gen.json` 携带 gen_ts：产物本体零污染、哈希可复现、
     标记随产物同行可审计（2026-11-07 用户裁定乙）。
"""

import datetime
import json
from pathlib import Path

from quantstudio.ptrade_deploy.preflight import check_import_freshness

REPO = Path(__file__).resolve().parents[1]

SRC_MIN = '''def initialize(context):
    g.done = False


def handle_data(context, data):
    if not g.done:
        order_target_value('512890.SS', 200000)
        g.done = True
'''


def _write_sidecar(product: Path, gen_ts: datetime.datetime, strategy_id="t") -> Path:
    side = Path(str(product) + ".gen.json")
    side.write_text(json.dumps({
        "strategy_id": strategy_id,
        "product": product.name,
        "gen_ts": gen_ts.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def test_stale_sidecar_is_rejected(tmp_path):
    """sidecar 的 gen_ts 早于第一跳源码 ⇒ 拒部署（假重转）。"""
    prod = tmp_path / "stale_ptrade.py"
    prod.write_text("# product\n", encoding="utf-8")
    _write_sidecar(prod, datetime.datetime.now() - datetime.timedelta(days=3))
    reasons = check_import_freshness(prod)
    assert reasons, "陈旧产物必须被拒"
    assert "IMPORT-FRESHNESS" in reasons[0]


def test_fresh_sidecar_is_allowed(tmp_path):
    """sidecar 的 gen_ts 晚于第一跳源码 ⇒ 放行。"""
    prod = tmp_path / "fresh_ptrade.py"
    prod.write_text("# product\n", encoding="utf-8")
    _write_sidecar(prod, datetime.datetime.now() + datetime.timedelta(seconds=5))
    assert check_import_freshness(prod) == []


def test_legacy_product_without_sidecar_is_only_warned(tmp_path):
    """无 sidecar 的 legacy 产物（quick 模式/历史产物）⇒ 不拒，仅提示。"""
    prod = tmp_path / "legacy_ptrade.py"
    prod.write_text("# legacy product\n", encoding="utf-8")
    assert check_import_freshness(prod) == []


def test_missing_source_is_fail_closed(tmp_path):
    """source 不存在 ⇒ fail-closed 拒。"""
    reasons = check_import_freshness(tmp_path / "nope.py")
    assert reasons and "IMPORT-FRESHNESS" in reasons[0]


def test_product_body_is_reproducible_and_sidecar_works(tmp_path):
    """产物本体可复现（哈希跨次一致）＋ orchestrator sidecar 写入器可被闸门读取。

    「产物本体零污染」是本裁定（乙）的核心：内嵌时刻戳会让同一策略两次转换产出
    不同文本，破坏黄金对照；改 sidecar 后 converted_code 必须逐字节一致。
    """
    from quantstudio.strategy_compiler.orchestrator import _write_gen_sidecar
    from quantstudio.strategy_compiler.source_import import convert_source

    src = tmp_path / "s.py"
    src.write_text(SRC_MIN, encoding="utf-8")
    a = convert_source(str(src), strategy_id="t_repro", verbose=False).converted_code
    b = convert_source(str(src), strategy_id="t_repro", verbose=False).converted_code
    assert a == b, "第一跳产物不可复现（产物本体被时间戳污染）"
    assert "PTRADE-DEPLOY:GEN" not in a, "产物本体不应内嵌 GEN 标记"

    prod = tmp_path / "t_repro_ptrade.py"
    prod.write_text(a, encoding="utf-8")
    side = _write_gen_sidecar(prod, "t_repro")
    assert side.exists() and side.name.endswith(".gen.json")
    assert check_import_freshness(prod) == [], "刚写入 sidecar 的产物必须判新鲜放行"


def test_runbook_documents_gen_sidecar():
    """部署 SOP 须注明 sidecar 的**用途**与**可删性**（PD-2026-0019 交付面）。"""

    class _StubCfg:
        strategy_id = "t_sid"

        class execution:
            profile = "daily_eod_1450"

        class persistence:
            state_file = "t_sid_state.pkl"

    from quantstudio.ptrade_deploy.deliver import build_runbook

    text = build_runbook(_StubCfg())
    assert "t_sid_trade.py.gen.json" in text, "runbook 未指名 sidecar 文件名"
    assert "IMPORT-FRESHNESS" in text, "runbook 未注明闸门用途"
    assert "可安全删除" in text, "runbook 未注明可删性"
    assert "不参与" in text and "确定性" in text, "runbook 未注明确定性边界"
