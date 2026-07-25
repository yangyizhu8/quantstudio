@echo off
REM QuantStudio 虚拟环境激活脚本（Windows cmd）
REM 用法：双击运行 或 在 cmd 中执行 scripts\activate_venv.bat
cd /d "%~dp0\.."
call ..\_runtime\venv_quant_studio\Scripts\activate.bat
echo QuantStudio 虚拟环境已激活（venv_quant_studio）
echo Python: & python --version
echo.
echo 常用命令:
echo   python main_gui.py                    启动 GUI 控制台
echo   python -m quantstudio.pipeline.daemon --mode once --task kline_1d_baostock   单次拉数据
echo   python tests\test_pipeline_migration.py    跑测试
echo.
cmd /k
