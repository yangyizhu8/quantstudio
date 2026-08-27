"""续跑停止守护：检测续跑进程退出后立即执行全量检查（staging 版）。"""
import subprocess, time, os, datetime

LOG_OUT = "data/logs/c4_resume_result.txt"
LOG_RUN = "data/logs/c4_resume.log"
os.makedirs("data/logs", exist_ok=True)

def find_resume_pid():
    """找续跑进程 PID（python -m quantstudio.pipeline.qfq_orchestrator_cli ... bootstrap-run）"""
    out = subprocess.run(['powershell.exe','-Command',
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'bootstrap-run' -and $_.CommandLine -match 'c4resume_staging' -and $_.Name -eq 'python.exe' } | Select-Object -ExpandProperty ProcessId"
    ], capture_output=True, text=True).stdout.strip()
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    return pids[0] if pids else None

def proc_alive(pid):
    if not pid: return False
    out = subprocess.run(['powershell.exe','-Command',
        f'(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName'],
        capture_output=True, text=True).stdout.strip()
    return bool(out)

def run_check():
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [f"=== 续跑停止检测 @ {ts} ==="]
    try:
        import duckdb
        con = duckdb.connect('data/quantstudio_c4resume_staging.db', read_only=True)
        runs = con.execute('''
            SELECT bootstrap_run_id, status, total_count, completed_count,
                   blocked_count, failed_count, started_at, finished_at
            FROM qfq_bootstrap_run WHERE bootstrap_run_id='bs_07b91d6bea'
        ''').fetchall()
        cols = ['run_id','status','total','completed','blocked','failed','started_at','finished_at']
        lines.append("--- run ---")
        for r in runs: lines.append(str(dict(zip(cols, r))))
        n = con.execute("SELECT status, COUNT(*) FROM qfq_bootstrap_item WHERE bootstrap_run_id='bs_07b91d6bea' GROUP BY status").fetchall()
        lines.append("--- items by status ---")
        for s, c in n: lines.append(f"  {s}: {c}")
        # failed/blocked 明细
        fd = con.execute("SELECT code, asset_type, status, last_error FROM qfq_bootstrap_item WHERE bootstrap_run_id='bs_07b91d6bea' AND status IN ('failed','blocked') ORDER BY status, code LIMIT 100").fetchall()
        if fd:
            lines.append("--- failed/blocked 明细 ---")
            for r in fd: lines.append(f"  {r[1]} {r[0]} [{r[2]}] {str(r[3])[:100]}")
        con.close()
    except Exception as e:
        lines.append(f"ERROR: {type(e).__name__}: {e}")
    result = "\n".join(lines)
    with open(LOG_OUT, "w", encoding="utf-8") as f: f.write(result)
    print(result)

pid = find_resume_pid()
print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 续跑停止守护启动，PID={pid}")
if not pid:
    print("未找到续跑进程，可能已停止")
    run_check()
else:
    while True:
        if not proc_alive(pid):
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 续跑已停止! 立即检查...")
            run_check()
            break
        time.sleep(30)
print("守护结束")
