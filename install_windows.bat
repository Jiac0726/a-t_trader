@echo off
setlocal
chcp 65001 >nul
cd /d %~dp0

echo ========================================
echo A股做T分析器 - Windows 安装
echo ========================================
echo.

set "PY_CMD="
py -3.12 --version >nul 2>&1 && set "PY_CMD=py -3.12"
if not defined PY_CMD py --version >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD python --version >nul 2>&1 && set "PY_CMD=python"

if not defined PY_CMD (
    echo [ERROR] 没有找到可用的 Python。
    echo.
    echo 推荐安装 Python 3.12：
    echo   winget install -e --id Python.Python.3.12
    echo.
    echo 安装完成后请关闭当前命令行窗口，重新打开 CMD，
    echo 确认下面命令能输出 Python 版本：
    echo   py -3.12 --version
    echo.
    echo 然后重新运行 install_windows.bat。
    pause
    exit /b 1
)

echo [OK] 使用 Python: %PY_CMD%

if exist .venv (
    echo [INFO] 检测到已有 .venv，先删除后重建。
    rmdir /s /q .venv
)

%PY_CMD% -m venv .venv
if errorlevel 1 (
    echo [ERROR] 创建虚拟环境失败。
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] 激活虚拟环境失败。
    pause
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 goto :install_failed

python -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed

set /p USEAK=是否安装 AKShare + BaoStock 备用数据源? (y/n): 
if /I "%USEAK%"=="y" (
    python -m pip install akshare "baostock>=0.9.3"
    if errorlevel 1 goto :install_failed
)

echo.
echo [OK] 安装完成。
echo 下一步双击 run_dashboard.bat，或执行：
echo   .venv\Scripts\python.exe -m streamlit run app\dashboard.py
echo.
pause
exit /b 0

:install_failed
echo.
echo [ERROR] Python 依赖安装失败，请查看上方报错。
pause
exit /b 1
