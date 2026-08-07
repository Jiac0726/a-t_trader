@echo off
chcp 65001 >nul
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
set /p USEAK=是否安装 AKShare 备用数据源? (y/n): 
if /I "%USEAK%"=="y" pip install akshare
echo.
echo 安装完成。运行 run_dashboard.bat 启动界面。
pause
