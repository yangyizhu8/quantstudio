"""PD-2026-0019 IMPORT-FRESHNESS 闸门固件（三态 + legacy 兼容 + 第一跳写入点验收）。

背景：deploy.yaml 的 `source` 指向 output/ptrade_export 的既有**第一跳产物**；若该产物
早于第一跳源码，则第一跳的代码改动不参与渲染 ⇒ 产物**看似全新、内核陈旧**（假重转），
而 P0–P6 全 PASS 不报任何异常（实测 2026-11-06：产物 11-03 09:23 vs source_import.py
11-06 22:14，产物中 _qs_idempotent_admit / qs_cash_reserved 均 0 处）。

判据载体＝**产物内嵌时刻戳** `# PTRADE-DEPLOY:GEN <ISO8601>`（不依赖 FS mtime——mtime 会被
git 检出/操作重置，测试夹具的 mtime 更只是「写入时刻」；2026-11-07 实测 mtime 版误伤
19 条既有固件，收敛作用域后仍误伤 7 条）。

本固件同时承担「时序自证」：内嵌时刻戳即为「本产物由哪个时刻的第一跳生成」的证据。
"""

import datetime
from pathlib import Path

from quantstudio.ptrade_deploy.preflight import check_import_freshness

REPO = Path(__file__).resolve().parents[1]

#: 与 PD-0018 固件同形的最小策略（能被 convert_source 正常转换）
SRC_MIN = '''def initialize(context):
    g.done = False


def handle_data(context, data):
    if not g.done:
        order_target_value('512890.SS', 200000)
        g.done = True
'''


def _gen_line(dt: datetime.datetime) -> str:
    return "# PTRADE-DEPLOY:GEN " + dt.strftime("%Y-%m-%dT%H:%M:%S")


def test_stale_gen_marker_is_rejected(tmp_path):
    """GEN 时刻早于第一跳源码 ⇒ 拒部署（假重转）。"""
    src = tmp_path / "stale_ptrade.py"
    old = datetime.datetime.now() - datetime.timedelta(days=3)
    src.write_text(_gen_line(old) + "\n# x\n", encoding="utf-8")
    reasons = check_import_freshness(src, src.read_text(encoding="utf-8"))
    assert reasons, "陈旧产物必须被拒"
    assert "IMPORT-FRESHNESS" in reasons[0]


def test_fresh_gen_marker_is_allowed(tmp_path):
    """GEN 时刻晚于第一跳源码 ⇒ 放行。"""
    src = tmp_path / "fresh_ptrade.py"
    now = datetime.datetime.now() + datetime.timedelta(seconds=5)
    src.write_text(_gen_line(now) + "\n# x\n", encoding="utf-8")
    assert check_import_freshness(src, src.read_text(encoding="utf-8")) == []


def test_legacy_product_without_gen_marker_is_only_warned(tmp_path):
    """无 GEN 标记的 legacy 产物（quick 模式/历史产物）⇒ 不拒，仅提示。"""
    src = tmp_path / "legacy_ptrade.py"
    src.write_text("# legacy product\n", encoding="utf-8")
    assert check_import_freshness(src, src.read_text(encoding="utf-8")) == []


def test_missing_source_is_fail_closed(tmp_path):
    """source 不存在 ⇒ fail-closed 拒。"""
    reasons = check_import_freshness(tmp_path / "nope.py")
    assert reasons and "IMPORT-FRESHNESS" in reasons[0]


def test_first_hop_writes_gen_marker(tmp_path):
    """第一跳装配点已写入 GEN 标记（产物级验收）＋ 新鲜产物判放行。"""
    from quantstudio.strategy_compiler.source_import import convert_source

    src = tmp_path / "s.py"
    src.write_text(SRC_MIN, encoding="utf-8")
    res = convert_source(str(src), strategy_id="t_fresh", verbose=False)
    code = res.converted_code
    assert "PTRADE-DEPLOY:GEN" in code, "第一跳产物缺 GEN 标记（写入点未生效）"
    assert check_import_freshness(src, code) == [], "刚生成的产物必须判新鲜放行"
