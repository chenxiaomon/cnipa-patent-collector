@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] 项目环境不存在，请先运行 setup.bat。
    pause
    exit /b 1
)

"%PROJECT_PYTHON%" fetch_update.py %*
set "UPDATE_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %UPDATE_EXIT_CODE%
