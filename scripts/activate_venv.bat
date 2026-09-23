@echo off
REM QuantStudio 虚拟环境激活脚本（Windows cmd）
REM 用法：双击运行 或 在 cmd 中执行 scripts\activate_venv.bat
cd /d "%~dp0\.."
call ..\_runtime\venv_quant_studio\Scripts\activate.bat
echo QuantStudio 虚拟环境已激活（venv_quant_studio）
echo Python: & python --version
REM ---- 笔5（2026-09-23 A 案裁定①）：duckdb 版本闸（第三入口）----
REM 官方主 venv 当前为 duckdb 1.5.4（不合钉版）；本闸不满足即拒绝继续，
REM 避免"激活了却起不来"或更糟的混版写库。逃生阀：set QS_DUCKDB_VERSION_GATE=0
python -c "from quantstudio.pipeline.duckdb_version_gate import require_duckdb_version; require_duckdb_version('activate_venv.bat (venv_quant_studio)')"
if errorlevel 1 (
  echo.
  echo [version-gate] 本 venv 的 duckdb 版本不合规，已拒绝继续。
  echo   修复: pip install "duckdb^>=1.4.5,^<1.5"
  echo   或改用已合规解释器: C:\python3.12.9\python.exe  ^|  Python311\python.exe
  echo.
  pause
  exit /b 3
)
echo.
echo 常用命令:
echo   python main_gui.py                    启动 GUI 控制台
echo   python -m quantstudio.pipeline.daemon --mode once --task kline_1d_baostock   单次拉数据
echo   python tests\test_pipeline_migration.py    跑测试
echo.
cmd /k
