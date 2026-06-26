@echo off
REM ============================================================================
REM  run.bat - rider-connect-demo 一鍵選單(雙擊即用)
REM  自動設定 UTF-8 編碼與 venv python,不需手打任何指令。
REM ============================================================================
setlocal enabledelayedexpansion
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

:menu
cls
echo ============================================================
echo   rider-connect-demo  -  Dronetag RIDER / OpenDroneID
echo ============================================================
echo.
echo   ---- 不需硬體(現在就能玩)----
echo   [1]  序列傳輸  離線 demo (合成一架無人機,完整解碼)
echo   [2]  WiFi 傳輸 離線 demo (Beacon / NAN)
echo   [3]  BLE 掃描   (列出附近藍牙裝置,測本機藍牙)
echo   [4]  驗證下降速度修正 (dtpyodid patch 自我驗證)
echo.
echo   ---- 需要真實 RIDER ----
echo   [5]  列出可用的 COM 序列埠
echo   [6]  序列接收  (USB 連 RIDER,需輸入 COM 埠)
echo   [7]  BLE 接收   (藍牙連 RIDER,自動搜尋)
echo.
echo   ===========================================================
echo   [8]  ** 即時儀表板 ** (深色網頁,USB+BLE 燈號 + raw)
echo   ===========================================================
echo.
echo   [0]  離開
echo.
set /p "choice=請選擇並按 Enter: "

if "%choice%"=="1" ( "%PY%" test_offline_demo.py & goto done )
if "%choice%"=="2" ( "%PY%" test_offline_demo_wifi.py & goto done )
if "%choice%"=="3" ( "%PY%" ble_reader.py --scan & goto done )
if "%choice%"=="4" ( "%PY%" dtpyodid_patch.py & goto done )
if "%choice%"=="5" ( "%PY%" -c "import serial.tools.list_ports as p; ports=list(p.comports()); [print('  ', x.device, '-', x.description) for x in ports] or (len(ports)==0 and print('  (no serial ports found)'))" & goto done )
if "%choice%"=="6" (
    set /p "port=輸入 COM 埠 (例 COM3): "
    echo 連線中... 按 Ctrl+C 停止
    "%PY%" odid_slip_reader.py -p "!port!" --init "2A0A0A"
    goto done
)
if "%choice%"=="7" ( echo 自動搜尋 RIDER... 按 Ctrl+C 停止 & "%PY%" ble_reader.py & goto done )
if "%choice%"=="8" ( echo 啟動即時儀表板... 瀏覽器將自動開啟 http://127.0.0.1:8000  ^(按 Ctrl+C 停止^) & "%PY%" dashboard_server.py & goto done )
if "%choice%"=="0" ( exit /b 0 )

echo.
echo [!] 無效的選擇: %choice%
timeout /t 1 >nul
goto menu

:done
echo.
echo ------------------------------------------------------------
pause
goto menu
