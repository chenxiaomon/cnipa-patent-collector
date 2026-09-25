@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set "DESKTOP_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%DESKTOP_PYTHON%" (
    echo [ERROR] 项目环境不存在，请先在项目目录运行：uv sync --python 3.11 --extra desktop
    pause
    exit /b 1
)

"%DESKTOP_PYTHON%" "%~dp0desktop_dashboard.py" %*
set "DESKTOP_EXIT_CODE=%ERRORLEVEL%"
if not "%DESKTOP_EXIT_CODE%"=="0" pause
exit /b %DESKTOP_EXIT_CODE%
