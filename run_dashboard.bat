@echo off
setlocal
chcp 65001 >nul
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [ERROR] 没有找到项目虚拟环境：.venv\Scripts\python.exe
    echo 请先运行 install_windows.bat。
    pause
    exit /b 1
)

.venv\Scripts\python.exe -m streamlit run app\dashboard.py
if errorlevel 1 (
    echo.
    echo [ERROR] Dashboard 启动失败，请查看上方报错。
)
pause
