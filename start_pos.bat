@echo off
setlocal
title NSP Cosmetic POS - Launcher

:: --- CONFIGURATION ---
set CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
set APP_URL=http://127.0.0.1:5000
set USER_PROFILE="C:\Chrome_POS_Profile"

:: 1. Navigate to Project Directory
cd /d "%~dp0"

echo ========================================
echo      NSP COSMETIC POS AUTO-LAUNCH      
echo ========================================

echo [1/4] Activating Virtual Environment...
if not exist ".venv-1\Scripts\activate.bat" (
    echo ERROR: Virtual environment '.venv-1' not found in %CD%
    echo Please ensure you are running this from the correct folder.
    pause
    exit /b
)
call .venv-1\Scripts\activate

echo [2/4] Verifying production dependencies...
python -m pip install waitress --quiet

echo [3/4] Starting Background Production Server...
:: start /b pythonw runs the script without a persistent console window
start /b pythonw run_server.py

echo [4/4] Waiting for server to initialize (3s)...
timeout /t 3 /nobreak > nul

echo Launching POS Kiosk Mode...
if exist %CHROME_PATH% (
    start "" %CHROME_PATH% --kiosk %APP_URL% --user-data-dir=%USER_PROFILE% --no-first-run --no-default-browser-check
) else (
    echo WARNING: Chrome not found at %CHROME_PATH%
    echo Attempting to launch via system default...
    start "" chrome --kiosk %APP_URL% --user-data-dir=%USER_PROFILE% --no-first-run --no-default-browser-check
)

echo.
echo Launch sequence complete. This window will now close.
timeout /t 2 > nul
exit
