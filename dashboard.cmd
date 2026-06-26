@echo off
REM ============================================================================
REM  dashboard.cmd - 一鍵啟動 RIDER 即時儀表板(雙擊即用,免選單)
REM  自動設 UTF-8 + venv python,起後端並開瀏覽器 http://127.0.0.1:8000
REM ============================================================================
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [錯誤] 找不到 venv: %PY%
    echo 請先建立虛擬環境:
    echo   py -m venv venv
    echo   venv\Scripts\python -m pip install -r requirements-windows.txt
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo   RIDER 即時儀表板
echo   瀏覽器將自動開啟 http://127.0.0.1:8000
echo   插上 RIDER USB 或開啟藍牙,對應燈號會轉綠
echo   按 Ctrl+C 停止
echo ============================================================
echo.
"%PY%" dashboard_server.py

REM server 結束後停留,讓使用者看見任何訊息
echo.
pause
