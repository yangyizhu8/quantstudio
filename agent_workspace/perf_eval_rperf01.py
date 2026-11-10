# -*- coding: utf-8 -*-
'''R-PERF-01 步骤2/3：短窗基线与 cProfile——minute-bar-v1 × first_board_high_open_1to2

用法: python agent_workspace/perf_eval_rperf01.py baseline|cprofile

环境隔离（铁律）：
  - QUANTSTUDIO_DATA_ROOT=data/backup_import_20261101_040935（只读副本，不碰主库/daemon）
  - 进程内先设 env 再 import 引擎（既有探针范式 profile_bt.py）

产物（agent_workspace/perf_eval_rperf01/）：
  <mode>_readings.json  墙钟/峰值RSS/黄金sha256/产物CSV sha256/环境声明
  <mode>_golden.json    nav_history + trade_records 规范化 JSON（黄金对照源）
  cprof.pstats          cProfile 模式的 pstats dump（步骤3 分析用）
'''
import os
import sys
import time
import json
import hashlib
from pathlib import Path

ROOT = Path(r'D:\miniQMT策略实盘\QuantStudio')
DATA_ROOT = ROOT / 'data' / 'backup_import_20261101_040935'
os.environ['QUANTSTUDIO_DATA_ROOT'] = str(DATA_ROOT)  # 必须在 import 引擎之前
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

import psutil  # noqa: E402

PROC = psutil.Process()

START, END = '2026-03-02', '2026-03-06'
if len(sys.argv) >= 4:
    START, END = sys.argv[2], sys.argv[3]   # R-PERF-03 全窗终验：命令行窗口覆盖
STRAT = str(ROOT / 'output' / 'generated_strategies' / 'first_board_high_open_1to2' / 'strategy.py')
OUTDIR = ROOT / 'agent_workspace' / 'perf_eval_rperf01'
OUTDIR.mkdir(parents=True, exist_ok=True)
TAG_SUFFIX = f"_{START}_{END}" if START != '2026-03-02' else ''


# 门3（R-PERF-41）：产物一律四要素命名，禁写固定名 baseline_readings.json / baseline_golden.json
# 格式：<probe>_<version>_<thermal>_<layer>_<window>_<stamp>.{json}
#   version = duckdb 版本（自动取）；thermal = $QS_THERMAL（默认 hot）；window = START~END（~ 连接）
def _four_element_name(layer: str, mode: str) -> str:
    import datetime as _dt
    try:
        import duckdb as _ddb
        _ver = str(_ddb.__version__)
    except Exception:
        _ver = 'unknown'
    _thermal = (os.environ.get('QS_THERMAL') or 'hot').strip()
    _stamp = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'{mode}_{_ver}_{_thermal}_{layer}_{START}~{END}_{_stamp}.json'


def _conv(o):
    if hasattr(o, 'item'):
        try:
            return o.item()
        except Exception:
            pass
    return str(o)


def golden_payload(result):
    return {
        'nav_history': result.nav_history,
        'trade_records': result.trade_records,
    }


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
    import duckdb
    import pandas
    from quantstudio.backtest.run_ptrade_strategy import run_backtest

    kw = dict(engine_profile='minute-bar-v1', match_price_mode='close')
    use_memtrace = (mode == 'memtrace')
    if use_memtrace:
        import tracemalloc
        tracemalloc.start()
    t0 = time.perf_counter()
    if mode == 'cprofile':
        import cProfile
        pr = cProfile.Profile()
        pr.enable()
        result, outdir, engine = run_backtest(STRAT, START, END, **kw)
        pr.disable()
        pr.dump_stats(str(OUTDIR / 'cprof.pstats'))
    else:
        result, outdir, engine = run_backtest(STRAT, START, END, **kw)
    wall = time.perf_counter() - t0
    mem = PROC.memory_info()
    trace_cur = trace_peak = None
    if use_memtrace:
        trace_cur, trace_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    payload = golden_payload(result)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_conv)
    sha = hashlib.sha256(blob.encode('utf-8')).hexdigest()

    csv_hashes = {}
    for f in sorted(Path(outdir).glob('*.csv')):
        csv_hashes[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()

    readings = {
        'mode': mode,
        'window': [START, END],
        'wall_sec': round(wall, 3),
        'wall_note': ('cProfile 含 profiler 开销，墙钟以 baseline 模式为准'
                      if mode == 'cprofile' else
                      'memtrace 含 tracemalloc 开销，仅用于内存定论，不作墙钟口径'
                      if use_memtrace else '裸跑'),
        'peak_wset_mb': round(mem.peak_wset / 1048576, 1),
        'rss_end_mb': round(mem.rss / 1048576, 1),
        'tracemalloc_cur_mb': round(trace_cur / 1048576, 1) if trace_cur is not None else None,
        'tracemalloc_peak_mb': round(trace_peak / 1048576, 1) if trace_peak is not None else None,
        'nav_days': len(result.nav_history),
        'trades': len(result.trade_records),
        'golden_sha256': sha,
        'csv_sha256': csv_hashes,
        'export_dir': str(outdir),
        'env': {
            'python': sys.version.split()[0],
            'duckdb': duckdb.__version__,
            'pandas': pandas.__version__,
            'data_root': str(DATA_ROOT),
            'cwd': os.getcwd(),
        },
    }
    _readings_path = OUTDIR / _four_element_name('wall', mode)
    _golden_path = OUTDIR / _four_element_name('golden', mode)
    _readings_path.write_text(
        json.dumps(readings, ensure_ascii=False, indent=2), encoding='utf-8')
    _golden_path.write_text(
        json.dumps(payload, ensure_ascii=False, default=_conv), encoding='utf-8')
    print(json.dumps(readings, ensure_ascii=False, indent=2))
    print(f'[output] readings={_readings_path.name}')
    print(f'[output] golden={_golden_path.name}')


if __name__ == '__main__':
    main()
