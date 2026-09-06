@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

REM setup.bat — 依赖安装脚本（Windows）

echo 正在安装项目依赖...

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 uv，请先安装并重新打开终端：
    echo https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

echo 安装锁定依赖和 Python 3.11 到项目 .venv...
call uv sync --frozen --python 3.11 --no-dev
if errorlevel 1 (
    echo [ERROR] 环境安装失败，请检查上方错误后重试。
    pause
    exit /b 1
)

echo.
echo [OK] 依赖安装完成！
echo 运行程序：.venv\Scripts\python.exe web_dashboard.py   （推荐，通过 Dashboard 操作）
echo 或直接运行：run.bat
echo.
pause
exit /b 0
