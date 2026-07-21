"""
Ptrade 策略回测入口
策略文件零 import 运行（通过 ptrade_import 统一注入）

用法：
    python -m quantstudio.backtest.run_ptrade_strategy <策略文件.py> [start] [end]
"""
import sys
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")

from quantstudio.backtest.backtest_engine import BacktestEngine, DEFAULT_TRADE_COST, EngineConfig
from quantstudio._paths import db_path
from quantstudio.backtest.strategy_runner import load_strategy as _load_strategy


def load_strategy(strategy_path: str) -> dict:
    """加载 Ptrade 策略文件，自动注入全部 API（通过 ptrade_import）"""
    return _load_strategy(strategy_path)


def _parse_flag(argv, flag_name, default=None, has_value=True):
    """解析 --flag value 或 --flag=value（返回值；has_value=False 时返回 bool）"""
    if not has_value:
        return flag_name in argv or any(a.startswith(flag_name + '=') for a in argv)
    for idx, a in enumerate(argv):
        if a == flag_name and idx + 1 < len(argv):
            return argv[idx + 1]
        if a.startswith(flag_name + '='):
            return a.split('=', 1)[1]
    return default


# 带值的 flag 列表（用于过滤位置参数时跳过这些 flag 的值）
_VALUE_FLAGS = {'--match-price', '--ptrade-dir', '--output', '--output-report', '--slippage'}


def _is_flag_value(argv, token):
    """判断 token 是否是某个带值 flag 的值（避免误判为位置参数）"""
    for idx, a in enumerate(argv):
        if a in _VALUE_FLAGS and idx + 1 < len(argv) and argv[idx + 1] == token:
            return True
    return False


def _check_data_readiness(db_path) -> bool:
    """G1 前置：校验回测四表非空 + 关键列存在，避免"静默错跑/空结果"。

    校验项：
      1. index_constituents 含 399101(中小板综) 成分股且非空
      2. stock_daily_valuation(每日) 或 stock_float_share(月度) 至少一张有估值数据
      3. stock_daily 含 preClose / is_st_reliable / is_delisting_risk / suspendFlag 列
    返回 True 表示就绪；False 表示不就绪（已打印可操作告警）。
    """
    import duckdb
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as e:
        print(f"❌ DuckDB 打开失败: {e}")
        return False
    ok = True
    # 1. 指数成分股（399101）
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM index_constituents WHERE index_code='399101'"
        ).fetchone()[0]
        if n == 0:
            print("❌ index_constituents 缺少 399101(中小板综) 成分股，请先采集指数成分")
            ok = False
        else:
            print(f"✓ index_constituents(399101): {n} 只")
    except Exception as e:
        print(f"❌ index_constituents 查询失败: {e}")
        ok = False
    # 2. 估值市值表（每日优先，月度回退）
    sdv, sfs = 0, 0
    try:
        sdv = con.execute("SELECT COUNT(*) FROM stock_daily_valuation").fetchone()[0]
    except Exception:
        pass
    try:
        sfs = con.execute("SELECT COUNT(*) FROM stock_float_share").fetchone()[0]
    except Exception:
        pass
    if sdv == 0 and sfs == 0:
        print("❌ stock_daily_valuation 与 stock_float_share 均为空，小市值选股无流通市值可用")
        ok = False
    else:
        src = "stock_daily_valuation(每日)" if sdv > 0 else "stock_float_share(月度回退)"
        print(f"✓ 估值市值源: {src} (sdv={sdv}, sfs={sfs})")
    # 3. stock_daily 关键列
    need_cols = {'preClose', 'is_st_reliable', 'is_delisting_risk', 'suspendFlag'}
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info('stock_daily')").fetchall()}
        missing = need_cols - cols
        if missing:
            print(f"❌ stock_daily 缺少列: {missing}（请重新跑数据管线补齐）")
            ok = False
        else:
            print("✓ stock_daily 含 preClose/is_st_reliable/is_delisting_risk/suspendFlag")
    except Exception as e:
        print(f"❌ stock_daily 列检查失败: {e}")
        ok = False
    con.close()
    return ok


def main():
    # 解析选项参数（A2 撮合价 / B4 对照）
    match_price_mode = _parse_flag(sys.argv[1:], '--match-price', 'close')
    do_compare = _parse_flag(sys.argv[1:], '--compare', has_value=False)
    ptrade_dir = _parse_flag(sys.argv[1:], '--ptrade-dir', None)
    output_report = (_parse_flag(sys.argv[1:], '--output', None) or
                     _parse_flag(sys.argv[1:], '--output-report', None))
    slippage = _parse_flag(sys.argv[1:], '--slippage', None)
    if match_price_mode not in ("close", "open", "next_open"):
        print(f"❌ --match-price 必须是 close/open/next_open，got {match_price_mode!r}")
        sys.exit(1)

    # 位置参数（过滤掉 -- 开头的选项）
    args = [a for a in sys.argv[1:] if not a.startswith('--') and not _is_flag_value(sys.argv[1:], a)]
    strategy_path = args[0] if len(args) > 0 else \
        str(ROOT / "quantstudio" / "backtest" / "strategies" / "小市值策略ptrade.py")
    start = args[1] if len(args) > 1 else "2026-01-01"
    end = args[2] if len(args) > 2 else "2026-04-29"  # B0 确定的标准对照区间

    db = db_path()
    if not db.exists():
        print(f"❌ DuckDB 不存在: {db}")
        sys.exit(1)

    # P2 数据前置：四表非空 + 关键列存在，否则给出可操作告警后退出
    if not _check_data_readiness(db):
        print("❌ 数据未就绪，请先完成数据拉取后再运行回测（退出码 3）")
        sys.exit(3)

    print(f"加载策略: {strategy_path}")
    strategy_funcs, strategy_module = load_strategy(strategy_path)

    # A1：入口显式构造 EngineConfig 传入引擎，源码可移植（迁移机器只改这里）
    config = EngineConfig(
        db_path=db,
        output_dir=ROOT / "output",
        research_dir=ROOT / "output" / "research",
    )

    # 成本模型：默认 DEFAULT_TRADE_COST；--slippage 用于对齐 Ptrade 平台默认 0 滑点
    cost = DEFAULT_TRADE_COST
    if slippage is not None:
        try:
            slip = float(slippage)
            cost = DEFAULT_TRADE_COST.__class__(**{**DEFAULT_TRADE_COST.__dict__, 'slippage_rate': slip})
        except (TypeError, ValueError):
            print(f"⚠️ --slippage 解析失败: {slippage!r}，退回默认滑点")

    engine = BacktestEngine(
        db_path=str(db),       # 保留向后兼容；config 优先级更高
        config=config,
        strategy=strategy_funcs,
        start=start,
        end=end,
        capital=100_000,  # 对齐 Ptrade 平台回测初始资金 10 万
        cost=cost,  # 统一成本常量（与所有入口共用，保证口径一致）
        strategy_type="ptrade",
        match_price_mode=match_price_mode,  # A2：默认 close，对照验证时可切 next_open
    )

    engine._strategy_name = Path(strategy_path).stem

    result, output_dir = engine.run()
    result.report()
    print(f"\n结果导出: {output_dir}")

    # B4：对照 Ptrade 基准（--compare）
    if do_compare:
        if not ptrade_dir:
            print("❌ --compare 需要 --ptrade-dir 指定 Ptrade 样本目录")
            sys.exit(2)
        exit_code = _run_compare(result, ptrade_dir, output_report, strategy_path, config)
        sys.exit(exit_code)


def _run_compare(local_result, ptrade_dir, output_report, strategy_name, config) -> int:
    """B4：加载 Ptrade 基准，跑 L1-L4 对照，输出报告，返回退出码（0/1/2）"""
    from pathlib import Path
    from quantstudio.backtest.ptrade_baseline import PtradeBaseline
    from quantstudio.backtest.fidelity_compare import FidelityComparator
    import pandas as pd

    print(f"\n{'='*60}\n本地 vs Ptrade 对照\n{'='*60}")
    # 加载 Ptrade 基准（source_id 默认 juyuan：Ptrade 平台导出 = 聚源）
    bl = PtradeBaseline().load_dir(ptrade_dir)
    bl.compute_nav()
    print(f"Ptrade 基准: {len(bl.trades)} 笔交易 / {len(bl.holdings)} 行持仓 / {len(bl.nav)} 天净值")

    # 本地结果转 DataFrame
    local_nav = pd.DataFrame(local_result.nav_history)
    local_trades = pd.DataFrame(local_result.trade_records)
    if 'date' not in local_trades.columns:
        local_trades = pd.DataFrame()
    # 本地 code 用 .SH/.SZ，对照时归一化到裸码（fidelity_compare 内部处理）

    comparator = FidelityComparator(local_nav, local_trades, bl,
                                    engine_data_source=config.data_source)
    report = comparator.compare()

    # 打印对照结论
    print(f"\n对照结论: {report.verdict} (exit_code={report.exit_code})")
    if report.source_check:
        cross = "（跨源已知容差）" if report.source_check.get("cross_source") else ""
        print(f"数据源口径: {report.source_check.get('message')}{cross}")
    for name in ['L1', 'L2', 'L3', 'L4']:
        m = report.metrics.get(name)
        if m:
            tag = "✅" if m.passed else ("🟡" if m.is_soft else "❌")
            print(f"  {tag} {name}: {m.summary}")

    # 打印归因
    if report.attributable_diffs:
        print(f"\n差异归因:")
        for attr in report.attributable_diffs:
            print(f"  [{attr['category']}] {attr['count']} 条 - {attr['attribution']}")
            for ex in attr.get('examples', [])[:2]:
                print(f"      例: {ex}")

    # 落盘 report.json
    if output_report:
        report.to_json(output_report)
        print(f"\n对照报告已落盘: {output_report}")
    else:
        # 默认输出到回测结果目录
        safe_name = Path(str(strategy_name)).stem
        default_path = Path("output") / f"compare_{safe_name}.json"
        default_path.parent.mkdir(parents=True, exist_ok=True)
        report.to_json(default_path)
        print(f"\n对照报告已落盘: {default_path}")

    return report.exit_code


if __name__ == "__main__":
    main()
