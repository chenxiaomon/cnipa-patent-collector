@echo off
echo.
echo ======================================================================
echo Starting Chrome browser with MITM proxy (127.0.0.1:8080)
echo ======================================================================
echo.
echo [*] Make sure MITM proxy is running in terminal 1
echo [*] python start_mitm_public_search.py
echo.
echo [*] Browser will start in 3 seconds...
echo.
timeout /t 3 /nobreak

"C:\Program Files\Google\Chrome\Application\chrome.exe" --proxy-server=http://127.0.0.1:8080 --ignore-certificate-errors https://cponline.cnipa.gov.cn/publicSearch

echo.
echo [*] Browser closed
echo.
pause
