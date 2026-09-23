@echo off
REM ============================================================================
REM QuantStudio 运行环境脚本（Windows cmd）
REM 用法：双击运行 或 在 cmd 中执行 scripts\activate_venv.bat
REM
REM 【官方解释器口径（2026-09-23 修正，诚实性缺陷修复）】
REM   官方推荐解释器 = Python311：
REM     C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
REM     —— 含全部运行依赖（PyQt6 / PyQt6-Fluent-Widgets / psutil / matplotlib /
REM        pyarrow）且 duckdb 1.4.5（符合钉版），GUI 与 daemon 均实测可跑。
REM   本脚本激活的 `_runtime\venv_quant_studio` 为**隔离运行环境**：
REM     2026-09-23 已降级 duckdb 1.5.4 → 1.4.5 并补齐 GUI/运维依赖
REM     （PyQt6-Fluent-Widgets / psutil / matplotlib / pyarrow），
REM     GUI 冒烟实测 1.76 s 起窗；两者均通过版本闸。
REM   修正原因：此前帮助文本写「python main_gui.py」但该 venv 当时缺
REM     qfluentwidgets 与 psutil —— 既跑不起 GUI，也使批一写锁自愈 fail-closed，
REM     属失实表述（GUI 实际一直由 Python311 运行）。
REM ============================================================================
cd /d "%~dp0\.."
call ..\_runtime\venv_quant_studio\Scripts\activate.bat
echo QuantStudio 隔离环境已激活（venv_quant_studio）
echo Python: & python --version
REM ---- 笔5（2026-09-23 A 案裁定①）：duckdb 版本闸（第三入口）----
REM 非 1.4.x 即拒绝继续，避免"激活了却起不来"或更糟的混版写库。
REM 逃生阀：set QS_DUCKDB_VERSION_GATE=0
python -c "from quantstudio.pipeline.duckdb_version_gate import require_duckdb_version; require_duckdb_version('activate_venv.bat (venv_quant_studio)')"
if errorlevel 1 (
  echo.
  echo [version-gate] 本环境的 duckdb 版本不合规，已拒绝继续。
  echo   修复: pip install "duckdb^>=1.4.5,^<1.5"
  echo   或改用已合规解释器: Python311\python.exe  ^|  C:\python3.12.9\python.exe
  echo.
  pause
  exit /b 3
)
echo.
echo 常用命令（本隔离环境）:
echo   python main_gui.py                    启动 GUI 控制台
echo   python -m quantstudio.pipeline.daemon --mode once --task kline_1d_baostock   单次拉数据
echo   python tests\test_pipeline_migration.py    跑测试
echo.
echo 说明：官方解释器口径见本脚本头部注释（推荐 Python311）；本环境为隔离运行环境。
echo.
cmd /k
