@echo off
chcp 65001 > nul
cd /d "%~dp0"

REM setup.bat — 依赖安装脚本（Windows）

echo 正在安装项目依赖...

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python 3.10+ 并添加到 PATH
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo Python 版本: %PY_VER%

REM 升级 pip
echo 升级 pip...
python -m pip install --upgrade pip

REM 安装依赖
echo 安装项目依赖...
python -m pip install -r requirements.txt

echo.
echo [OK] 依赖安装完成！
echo 运行程序：python web_dashboard.py   （推荐，通过 Dashboard 操作）
echo 或直接运行：run.bat
echo.
pause
