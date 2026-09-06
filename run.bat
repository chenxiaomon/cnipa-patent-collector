@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM run.bat — 一键启动采集（MITM 代理 + 主程序）
REM
REM 用法：
REM   run.bat                               正常采集（断点续传）
REM   run.bat --test 5                      测试模式，仅采集前 5 条
REM   run.bat --update-list data\retry.txt  强制重采指定列表
REM   run.bat collect-fwxx                  发文补采

set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] 项目环境不存在，请先运行 setup.bat。
    exit /b 1
)
set "MITM_LOG=%~dp0.mitm.log"

echo ============================================================
echo ^>  启动 MITM 代理...
echo ============================================================

REM 启动 MITM 代理（后台，输出重定向到日志）
start "MITM Proxy" /B "%PROJECT_PYTHON%" start_mitm_proxy.py > "%MITM_LOG%" 2>&1

REM 等待代理就绪（最多 10 秒）
set READY=0
for /L %%i in (1,1,10) do (
    if !READY!==0 (
        findstr /M "Serving on\|Proxy server listening\|proxy" "%MITM_LOG%" >nul 2>&1
        if not errorlevel 1 (
            set READY=1
            echo [OK] MITM 代理已就绪
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if !READY!==0 echo [WARN] 等待超时，继续启动采集...

echo.

REM ��发子命令
set MODE=%1
if "%MODE%"=="collect-fwxx" (
    echo ^>  启动发文补采...
    set USE_MITM_PROXY=true
    "%PROJECT_PYTHON%" collect_fwxx.py %2 %3 %4 %5
) else (
    echo ^>  启动主采集...
    set USE_MITM_PROXY=true
    "%PROJECT_PYTHON%" main_automation.py %*
)
set "COLLECTION_EXIT_CODE=%ERRORLEVEL%"

REM 停止 MITM 代理
taskkill /FI "WINDOWTITLE eq MITM Proxy" /F >nul 2>&1

exit /b %COLLECTION_EXIT_CODE%
