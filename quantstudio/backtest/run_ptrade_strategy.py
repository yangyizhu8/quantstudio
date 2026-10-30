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
from quantstudio.pipeline.backtest_window import (
    CollectorStopContext, STOP_WAIT_SEC, BOUNDARY_HINT_SEC)


def load_strategy(strategy_path: str) -> dict:
    """加载 Ptrade 策略文件，自动注入全部 API（通过 ptrade_import）"""
    return _load_strategy(strategy_path)




def resolve_design_capital_contract(strategy_path):
    """设计契约本金**纯查询**（无副作用；D1 件一，2026-10-06）。

    返回 dict：{"required_initial_cash": float, "sizing_mode": str|None, "design_path": str}；
    不满足条件时返回 None。判据与 A 件完全一致（同源，杜绝两处判定分叉）：
    - 仅当 design_metadata 可信解析链给出 RESOLVED（ledger 生命周期 + SHA 绑定 + schema 2.3）；
    - 且 portfolio_contract.required_initial_cash 非空、> 0（runtime_total_value 的 null → None）；
    - 其余（legacy 策略 / 解析异常 / 读文件失败）一律 None。

    消费者：A 件的 _check_design_capital_contract（打日志）与 GUI 运行前确认门（弹窗）。
    """
    try:
        import json
        from quantstudio.strategy_compiler.design_metadata import find_design_for_strategy

        res = find_design_for_strategy(strategy_path, ROOT)
        if getattr(res, "status", None) != "RESOLVED":
            return None
        design_path = getattr(res, "design_path", None)
        if not design_path:
            return None
        with open(design_path, "r", encoding="utf-8") as fh:
            design = json.load(fh)
        contract = (design or {}).get("portfolio_contract") or {}
        required = contract.get("required_initial_cash")
        if required is None:
            return None
        required = float(required)
        if required <= 0:
            return None
        return {"required_initial_cash": required,
                "sizing_mode": contract.get("sizing_mode"),
                "design_path": design_path}
    except Exception:
        return None


def _check_design_capital_contract(strategy_path, capital):
    """A 件（2026-10-06）：按**设计契约**校验初始资金，不足则显式告警（**不阻断**）。

    动因：GUI/CLI 的默认初始资金都是 10 万，而按更大本金设计的固定名义金额策略
    （portfolio_contract.sizing_mode=fixed_notional + required_initial_cash）在资金不足时
    会**整程零成交且全程静默**（2026-10-05 用户实测 161 交易日零成交）。

    只读、无副作用。判据来源＝design_metadata 的可信解析链（ledger 生命周期 + SHA 绑定 +
    design schema 2.3 校验）。**仅当 RESOLVED 且 required_initial_cash 非空且 capital 低于它**
    时才输出一条 warning；其余情形（legacy 策略 / runtime_total_value 的 null / 资金充足 /
    任何异常）**零输出、零行为影响**。诊断自身绝不引入新失败路径（沿用 backtest_engine.py 既有守卫原则）。
    """
    try:
        info = resolve_design_capital_contract(strategy_path)
        if not info:
            return
        required = info["required_initial_cash"]
        if float(capital) >= required:
            return
        logging.getLogger(__name__).warning(
            "[Backtest] 初始资金低于策略设计契约：本策略声明 required_initial_cash=%.2f，"
            "本次 capital=%.2f（sizing_mode=%s）。若为 fixed_notional 策略，可能因此无法足额建仓、"
            "甚至全程零成交——请核对回测的初始资金设置（设计文件 %s）。"
            "本次回测仍按给定资金执行，不中断。",
            required, float(capital), info["sizing_mode"], info["design_path"])
    except Exception:
        pass

def run_backtest(strategy_path, start, end, *,
                 db_path=None, capital=100_000,
                 match_price_mode='close', engine_profile='daily-bar-v1',
                 etf_t0=False, cost=None, rebalance_mode='legacy',
                 cost_basis_method='diluted', match_profile='ptrade',
                 fidelity_config=None, progress_callback=None,
                 coexistence=True,
                 coexistence_boundary_timeout=BOUNDARY_HINT_SEC,
                 coexistence_stop_timeout=STOP_WAIT_SEC,
                 cancel_check=None,
                 wait_callback=None):
    """CLI 和 GUI 共用的回测入口（含「回测前停采集」语义）。

    口径见 specs/001-daemon-backtest-coexistence（2026-10-27 变更）：
      · 发起回测时若采集在跑：请求其**优雅停止并退出**（任务边界 30s → 任务内单元边界中断）；
      · 等待期经 progress_callback 输出**真实读数**（不做 ETA），cancel_check() 为真则取消；
      · 回测结束（成功/失败/取消）**不重启采集**——由用户手动启动，界面/终端据留痕提示。
    coexistence=False ⇒ 完全回退到改动前行为（不停采集、不等待、不预检）。
    """
    # 裸文件名便捷解析（cwd 找不到时回退 strategies；透明打印，见 _resolve_strategy_path）
    strategy_path = _resolve_strategy_path(strategy_path)

    kwargs = dict(
        db_path=db_path, capital=capital, match_price_mode=match_price_mode,
        engine_profile=engine_profile, etf_t0=etf_t0, cost=cost,
        rebalance_mode=rebalance_mode, cost_basis_method=cost_basis_method,
        match_profile=match_profile, fidelity_config=fidelity_config,
        progress_callback=progress_callback,
    )
    if not coexistence:
        return _run_backtest_impl(strategy_path, start, end, **kwargs)

    # 停采集是**有代价的动作**（结束后需用户手动恢复）：任何不依赖库的前置校验
    # 必须放在它之前——低级错误（路径不存在/不可解析）不得以"采集被停掉"为代价。
    _precheck_strategy_before_stop(strategy_path)

    def _emit(msg: str) -> None:
        """等待读数的出口。

        注意：**不能复用 `progress_callback`**——引擎侧签名是
        `(current, total, date_str)` 三参数（`backtest_engine.py:554` 调用），
        而等待读数是单条字符串；混用会 TypeError（2026-10-28 实证）。
        """
        if wait_callback is not None:
            try:
                wait_callback(msg)
            except Exception:  # noqa: BLE001  读数出口异常不得中断等待
                pass
        else:
            print(msg, file=sys.stderr, flush=True)   # CLI 默认：stderr，不污染产物

    def _on_wait(waited: float, phase: str) -> None:
        # 0.1s 精度：`:.0f` 会把 0.5s 显示成 "0s"，看着像"没等"，容易被误读为缺陷
        _emit(f"[coexist] 已等待 {waited:.1f}s · {phase}")

    ctx = CollectorStopContext(
        str(strategy_path),
        timeout=coexistence_stop_timeout,
        boundary_hint=coexistence_boundary_timeout,
        on_wait=_on_wait,
        should_cancel=cancel_check,
    )
    try:
        with ctx:
            # 采集已退出、互斥已拿到：此刻打不开库就是真不可用 → 显式拒绝（不空跑）
            _precheck_db_available(db_path)
            # F-6：停采集后**复检数据就绪**（采集持库时 main 已跳过前置检查；
            # 此刻无锁竞争，四表仍空/缺列即真不就绪 → 显式拒绝，禁止空数据静默跑完）
            if not _check_data_readiness(
                    Path(db_path) if db_path is not None else Path(_default_db_path())):
                raise RuntimeError(
                    "数据未就绪（停采集后复检：四表为空或关键列缺失）。"
                    "共存流程已执行完毕；请先完成数据拉取。")
            # 2026-10-30 分钟覆盖闸（QD-2026-0015）：仅 minute-bar-v1 生效——日线 profile
            # 在调用点直接短路（连闸函数都不调用、无任何新增 IO/输出），「日线 profile
            # 零行为改变·黄金结果逐位一致」由结构保证；BLOCK ⇒ RuntimeError（退出码非 0）。
            minute_gate_report = None
            if engine_profile == "minute-bar-v1":
                minute_gate_report = _precheck_minute_coverage(
                    Path(db_path) if db_path is not None else Path(_default_db_path()),
                    start, end, engine_profile)
            from quantstudio.backtest.providers import duckdb_data_access as _dda
            _dda.set_fail_closed(True)
            try:
                result, output_dir, engine = _run_backtest_impl(
                    strategy_path, start, end, **kwargs)
            finally:
                _dda.set_fail_closed(False)   # 线程本地开关，必须恢复（纯增益）
            _leave_minute_gate_trace(output_dir, minute_gate_report)
            _assert_result_not_empty(result, output_dir)
            return result, output_dir, engine
    finally:
        # 不重启采集（spec FR-003/004）：只如实提示，恢复时点由用户决定
        if ctx.stop_requested:
            _emit("[coexist] 采集已停止（原因：回测）。需要时请手动启动采集。")


def _resolve_strategy_path(strategy_path) -> str:
    """裸文件名（不含路径分隔符）在 cwd 找不到时，透明回退到 strategies 目录。

    动机：GUI 是从 `quantstudio/backtest/strategies/` 下拉选文件，用户直觉上
    也会在 CLI 里只写文件名；而 CLI 原语义是"相对 cwd 解析"，于是必失败。
    仅在「裸名 + cwd 不存在 + strategies 存在同名」时回退，并打印一行说明（不隐藏）。
    """
    raw = str(strategy_path)
    p = Path(raw)
    if p.exists() or p.is_absolute() or ("/" in raw) or ("\\" in raw):
        return raw
    cand = ROOT / "quantstudio" / "backtest" / "strategies" / p.name
    if cand.exists():
        try:
            shown = cand.relative_to(ROOT)
        except ValueError:
            shown = cand
        print(f"[resolve] 当前目录无 {p.name}，改用 {shown}")
        return str(cand)
    return raw


def _precheck_strategy_before_stop(strategy_path) -> None:
    """停采集**之前**的前置校验（只做不依赖数据库的检查）。

    动机（2026-10-28 实证）：策略路径解析失败时，采集已经被停掉——用户白停一次
    采集还要手动恢复。因此把「文件存在 + 可 AST 解析」提前到停止之前；
    不通过就**不停采集**、直接报错，并给出可直接复制的正确命令。
    """
    p = Path(str(strategy_path))
    if not p.exists():
        strategies_dir = ROOT / "quantstudio" / "backtest" / "strategies"
        lines = [
            f"策略文件不存在（**采集未停止**）：{str(strategy_path)!r}",
            f"  当前工作目录：{Path.cwd()}",
        ]
        same = strategies_dir / p.name
        if same.exists():
            try:
                rel = same.relative_to(ROOT)
            except ValueError:
                rel = same
            lines.append(f"  ✅ 在 strategies 目录找到同名文件：{rel}")
            lines.append(f"  请改用：python -m quantstudio.backtest.run_ptrade_strategy "
                         f"\"{rel}\" <start> <end>")
        else:
            try:
                cands = sorted(strategies_dir.glob("*.py"),
                               key=lambda x: x.stat().st_mtime, reverse=True)[:8]
            except Exception:  # noqa: BLE001
                cands = []
            if cands:
                lines.append("  strategies 目录下可用策略（最近修改 8 个）：")
                for c in cands:
                    try:
                        lines.append(f"    - {c.relative_to(ROOT)}")
                    except ValueError:
                        lines.append(f"    - {c}")
        raise FileNotFoundError("\n".join(lines))
    try:
        from quantstudio.backtest.strategy_runner import StrategyIsolationGuard
        StrategyIsolationGuard.validate_path(str(strategy_path))
    except FileNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"策略文件校验失败（**采集未停止**）：{type(e).__name__}: {e}") from e


def _assert_result_not_empty(result, output_dir) -> None:
    """FR-011：跑完却没有任何净值记录 ⇒ 判失败（不产出"成功但空"的结果）。

    注意：**合法零交易**仍有逐日净值记录，不会被误判；只有取数全程拿不到数据才会空。
    """
    nav = getattr(result, "nav_history", None)
    if nav:
        return
    raise RuntimeError(
        f"回测未产出任何净值记录（output_dir={output_dir}）：取数层可能全程未拿到真实数据。"
        "本次结果不可采信，请检查数据可用性后重试。")


def _precheck_db_available(db_path=None) -> None:
    """只读试连 + 试查：不可用则抛显式错误（绝不进入"空数据静默跑完"路径）。"""
    import duckdb

    p = Path(db_path) if db_path is not None else Path(_default_db_path())
    if not p.exists():
        raise RuntimeError(f"数据主库不存在：{p}")
    try:
        con = duckdb.connect(str(p), read_only=True)
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"数据主库当前不可用（{type(e).__name__}: {e}）。"
            "可能原因：采集进程仍持有库 / WAL 回放中 / 文件被其他进程占用。"
            "共存窗已等待到采集边界；若持续失败，请确认采集进程状态后重试。"
        ) from e


def _precheck_minute_coverage(db_path, start, end, engine_profile) -> dict:
    """回测前分钟数据覆盖闸（2026-10-30 六步流水线 ③，QD-2026-0015 三型判据）。

    唯一实现 = quantstudio/backtest/libs/coverage_gate.py（本函数与
    scripts/check_minute_coverage.py 均复用它，禁双份逻辑）：
      · 仅 minute-bar-v1 profile 生效（日线 profile 不打开任何库，零行为变化）；
      · ①整日缺 BLOCK（STRICT 默认开，fail-closed）⇒ RuntimeError（退出码非 0）；
      · ②低覆盖/③副本陈旧 WARN 不阻断；主库不可读时 ③ 自动跳过并说明；
      · QS_MINUTE_COVERAGE_SKIP=1 整闸跳过 ⇒ stdout 留痕（本函数打印摘要）+
        结果目录留痕（_leave_minute_gate_trace，防静默绕过）；
      · 只读打开 DuckDB，禁写禁 CHECKPOINT，不停 daemon（闸在停采集互斥窗内执行）。
    返回完整报告 dict（供留痕/测试断言）。
    """
    from quantstudio.backtest.libs.coverage_gate import (
        check_minute_coverage, format_summary)

    report = check_minute_coverage(db_path, start, end, engine_profile)
    for line in format_summary(report):
        print(line)
    verdict = report.get("verdict")
    if verdict == "BLOCK":
        raise RuntimeError(f"回测前分钟覆盖闸 BLOCK：{report.get('block_reason')}")
    if verdict == "ERROR":
        # 闸自身出错：默认 fail-closed 拒跑（防带病静默）；STRICT=0 时降级放行（留痕）
        if report.get("strict", True):
            raise RuntimeError(
                f"回测前分钟覆盖闸自身出错（fail-closed 拒跑）：{report.get('error')}")
        print(f"[minute-gate] ⚠️ 闸 ERROR 已按非 STRICT 降级放行：{report.get('error')}")
    return report


def _leave_minute_gate_trace(output_dir, gate_report) -> None:
    """QS_MINUTE_COVERAGE_SKIP=1 跳过时在结果目录留痕（方案 §三：防静默绕过）。

    best-effort：留痕失败绝不影响回测主流程（stdout 留痕仍然有效，补一条提示）。
    """
    if not gate_report or not gate_report.get("skipped"):
        return
    try:
        import json
        p = Path(output_dir) / "minute_coverage_gate_SKIPPED.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: gate_report.get(k) for k in (
            "gate", "schema_version", "db_path", "start", "end", "engine_profile",
            "skipped", "skip_reason", "verdict", "elapsed_sec")}
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[minute-gate] 跳过留痕已写入结果目录: {p}")
    except Exception as e:  # noqa: BLE001
        print(f"[minute-gate] ⚠️ 跳过留痕写入失败（{type(e).__name__}: {e}）——stdout 留痕仍有效")


def _db_is_locked(db_path) -> bool:
    """库文件当前是否被其他进程持有（典型：daemon 采集轮写锁）。只探测、不持续占用。

    F-6（2026-10-28 09:36 实证）：采集轮持库时发起回测，main() 的 `_check_data_readiness`
    抢在共存流程之前 exit 3 —— 用户被迫等轮结束或手动停采集，违背「免手动停采集」的核心需求。
    本函数用于让共存模式识别"锁被采集持有"并放行进入停采集流程，停完再复检。
    """
    import duckdb

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        con.close()
        return False
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        return ("另一个程序正在使用" in msg) or ("already open" in msg) \
            or ("Could not set lock" in msg) or ("被其他进程占用" in msg)


def _run_backtest_impl(strategy_path, start, end, *,
                 db_path=None, capital=100_000,
                 match_price_mode='close', engine_profile='daily-bar-v1',
                 etf_t0=False, cost=None, rebalance_mode='legacy',
                 cost_basis_method='diluted', match_profile='ptrade',
                 fidelity_config=None, progress_callback=None):
    """CLI 和 GUI 共用的回测入口。统一所有默认值，确保双端口径一致。

    所有默认值对齐 PTrade 平台实证（2026-08-14）：
      - match_price_mode='close'（PTrade 日线/分钟均用 close 撮合）
      - engine_profile='daily-bar-v1'（默认日线）
      - cost=DEFAULT_TRADE_COST（滑点 0.0、佣金万3.5、印花税千1）
      - capital=100_000（对齐 PTrade 回测初始资金）
      - cost_basis_method='diluted'（POS-01 摊薄成本，对齐 PTrade 平台实证 2026-10-06）

    fidelity_config（P-A0/P-A1/P-A2，2026-08-24）：PTrade 保真模式配置
    （opt-in，验证转换产物用）。非 None 时在 engine.run() 前注入
    ptrade_api（set_fidelity_config），并执行 resolve(backtest_start_date)
    PIT 门禁校验（回测起点 >= 快照日否则 ValueError fail-closed）。
    默认 None = 本地保持正确语义锚（P-D9 裁定），零行为影响。

    返回 (result, output_dir, engine) 三元组。
    """
    if db_path is None:
        db_path = _default_db_path()
    db_path = Path(db_path)
    if cost is None:
        cost = DEFAULT_TRADE_COST

    strategy_funcs, _ = load_strategy(strategy_path)

    config = EngineConfig(
        db_path=db_path,
        output_dir=ROOT / "output",
        research_dir=ROOT / "output" / "research",
        rebalance_mode=rebalance_mode,
    )

    engine = BacktestEngine(
        db_path=str(db_path),
        config=config,
        strategy=strategy_funcs,
        start=start,
        end=end,
        capital=capital,
        cost=cost,
        strategy_type="ptrade",
        match_price_mode=match_price_mode,
        engine_profile=engine_profile,
        etf_t0=etf_t0,
        cost_basis_method=cost_basis_method,
        match_profile=match_profile,  # M5.2 件7 口径标签（缺省 ptrade 零行为改变）
        progress_callback=progress_callback,
    )
    engine._strategy_name = Path(strategy_path).stem

    # P-A0/P-A1/P-A2：保真模式注入（opt-in）。resolve() 做 PIT 门禁 + eps 双端校验；
    # 校验失败（如回测起点 < 快照日 / eps basis 单端缺失）→ 显性 ValueError fail-closed。
    if fidelity_config is not None:
        from quantstudio.backtest.ptrade_api import _api as _ptrade_api
        from quantstudio.backtest.fidelity_config import PTradeFidelityConfig
        if not isinstance(fidelity_config, PTradeFidelityConfig):
            raise TypeError(
                "fidelity_config 必须是 PTradeFidelityConfig 实例，got %r"
                % (type(fidelity_config).__name__,))
        fidelity_config.resolve(str(start))  # PIT 门禁 + 超龄告警 + eps 双端校验
        _ptrade_api.set_fidelity_config(fidelity_config)
        if fidelity_config.fidelity_ashares_snapshot:
            print(f"⚡ PTrade 保真模式 ON（验证用）: "
                  f"ashares快照={fidelity_config.fidelity_ashares_snapshot} "
                  f"st_filter={fidelity_config.fidelity_st_filter} "
                  f"eps_basis={fidelity_config.fidelity_eps_basis}")

    _check_design_capital_contract(strategy_path, capital)

    result, output_dir = engine.run()
    return result, output_dir, engine


def _default_db_path():
    """获取默认数据库路径（延迟导入避免循环依赖）"""
    return db_path()


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
_VALUE_FLAGS = {'--match-price', '--ptrade-dir', '--output', '--output-report', '--slippage', '--profile', '--etf-t0',
                '--fidelity', '--eps-basis',
                '--coexistence-stop-timeout', '--coexistence-boundary-timeout'}


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
    # 解析选项参数（A2 撮合价 / B4 对照 / PR4 引擎 Profile）
    match_price_mode = _parse_flag(sys.argv[1:], '--match-price', 'close')
    do_compare = _parse_flag(sys.argv[1:], '--compare', has_value=False)
    ptrade_dir = _parse_flag(sys.argv[1:], '--ptrade-dir', None)
    output_report = (_parse_flag(sys.argv[1:], '--output', None) or
                     _parse_flag(sys.argv[1:], '--output-report', None))
    slippage = _parse_flag(sys.argv[1:], '--slippage', None)
    # PR4: 引擎 Profile 选择（daily-bar-v1 / minute-bar-v1）+ ETF T+0 开关
    engine_profile = _parse_flag(sys.argv[1:], '--profile', 'daily-bar-v1')
    etf_t0_flag = _parse_flag(sys.argv[1:], '--etf-t0', 'false')
    # POS-01: 成本记账口径（diluted=PTrade 摊薄默认 / moving_avg=legacy 对照）
    cost_basis_method = _parse_flag(sys.argv[1:], '--cost-basis', 'diluted')
    # P-A0/P-A1/P-A2: 保真模式旗标（opt-in，验证转换产物用；默认全关 = 本地语义锚）
    fidelity_flag = _parse_flag(sys.argv[1:], '--fidelity', '')
    eps_basis_flag = _parse_flag(sys.argv[1:], '--eps-basis', 'passthrough')
    # 共存语义（specs/001-daemon-backtest-coexistence）：默认「回测前停采集，结束后不自动重启」
    coexistence = not _parse_flag(sys.argv[1:], '--no-coexistence', has_value=False)
    _stop_timeout_raw = _parse_flag(sys.argv[1:], '--coexistence-stop-timeout', None)
    _boundary_raw = _parse_flag(sys.argv[1:], '--coexistence-boundary-timeout', None)
    try:
        coexistence_stop_timeout = float(_stop_timeout_raw) if _stop_timeout_raw else None
        coexistence_boundary_timeout = float(_boundary_raw) if _boundary_raw else None
    except ValueError:
        print("❌ --coexistence-stop-timeout / --coexistence-boundary-timeout 必须是数字（秒）")
        sys.exit(1)
    if match_price_mode not in ("close", "open", "next_open"):
        print(f"❌ --match-price 必须是 close/open/next_open，got {match_price_mode!r}")
        sys.exit(1)
    if engine_profile not in ("daily-bar-v1", "minute-bar-v1", "daily-open-close-proxy-v1"):
        print(f"❌ --profile 必须是 daily-bar-v1/minute-bar-v1/daily-open-close-proxy-v1，got {engine_profile!r}")
        sys.exit(1)
    if cost_basis_method not in ("diluted", "moving_avg"):
        print(f"❌ --cost-basis 必须是 diluted/moving_avg，got {cost_basis_method!r}")
        sys.exit(1)
    # 保真旗标校验（非法值 fail-closed，禁止静默忽略）
    _FIDELITY_OPTS = {"ashares", "st_filter"}
    if fidelity_flag:
        fid_parts = {p.strip() for p in fidelity_flag.replace('+', ',').split(',') if p.strip()}
        unknown = fid_parts - _FIDELITY_OPTS
        if unknown:
            print(f"❌ --fidelity 只接受 ashares/st_filter（逗号或+分隔），got 未知项 {sorted(unknown)!r}")
            sys.exit(1)
    else:
        fid_parts = set()
    if eps_basis_flag not in ("passthrough", "basic", "diluted"):
        print(f"❌ --eps-basis 必须是 passthrough/basic/diluted，got {eps_basis_flag!r}")
        sys.exit(1)
    # PR4 决策 4：etf_t0 只在 minute-bar-v1 生效（日线 Profile 强制 False）
    etf_t0 = (etf_t0_flag.lower() == 'true') and (engine_profile == 'minute-bar-v1')

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
    # F-6：采集轮持库时（readiness 只因打不开库而失败），共存模式必须放行——
    # 停采集后在 run_backtest 内复检；否则「免手动停采集」在采集轮内反而失效。
    if coexistence and _db_is_locked(db):
        print("⚠ 数据主库正被采集进程持有——跳过前置检查，进入共存流程（自动停采集后复检数据就绪）")
    elif not _check_data_readiness(db):
        print("❌ 数据未就绪，请先完成数据拉取后再运行回测（退出码 3）")
        sys.exit(3)

    print(f"加载策略: {strategy_path}")

    # 成本模型：默认 DEFAULT_TRADE_COST；--slippage 用于对齐 Ptrade 平台默认 0 滑点
    cost = DEFAULT_TRADE_COST
    if slippage is not None:
        try:
            slip = float(slippage)
            cost = DEFAULT_TRADE_COST.__class__(**{**DEFAULT_TRADE_COST.__dict__, 'slippage_rate': slip})
        except (TypeError, ValueError):
            print(f"⚠️ --slippage 解析失败: {slippage!r}，退回默认滑点")

    # P-A0/P-A1/P-A2: 保真模式配置（opt-in；空 → None = 默认全关本地语义锚）
    fidelity_config = None
    if fid_parts or eps_basis_flag != "passthrough":
        from quantstudio.backtest.fidelity_config import PTradeFidelityConfig
        fidelity_config = PTradeFidelityConfig(
            fidelity_ashares_snapshot=("ashares" in fid_parts),
            fidelity_st_filter=("st_filter" in fid_parts),
            fidelity_eps_basis=eps_basis_flag,
        )

    # P2：通过共用入口 run_backtest 确保双端口径一致
    from quantstudio.pipeline.backtest_window import CollectorStopTimeout, WindowCancelled

    def _cli_wait(msg: str) -> None:
        """等待读数走 stderr（不污染正常产物输出流）。

        注意：与引擎的 `progress_callback(current, total, date_str)` 是**两个通道**，
        不可混用（2026-10-28 实证：混用会 TypeError）。
        """
        print(msg, file=sys.stderr, flush=True)

    _kw = {}
    if coexistence_stop_timeout is not None:
        _kw["coexistence_stop_timeout"] = coexistence_stop_timeout
    if coexistence_boundary_timeout is not None:
        _kw["coexistence_boundary_timeout"] = coexistence_boundary_timeout

    try:
        result, output_dir, engine = run_backtest(
            strategy_path, start, end,
            db_path=db,
            match_price_mode=match_price_mode,
            engine_profile=engine_profile,
            etf_t0=etf_t0,
            cost=cost,
            cost_basis_method=cost_basis_method,
            fidelity_config=fidelity_config,
            coexistence=coexistence,
            wait_callback=_cli_wait,
            **_kw,
        )
    except WindowCancelled:
        print("[coexist] 已取消：回测未开始；采集保持当前状态（需要时请手动启动）",
              file=sys.stderr)
        sys.exit(130)
    except CollectorStopTimeout as e:
        print(f"[coexist] {e}。可先手动停止采集后重试，或加大 --coexistence-stop-timeout",
              file=sys.stderr)
        sys.exit(4)
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
