@echo off
title Empyrion Playfield Studio - Launcher
echo ========================================================
echo   Empyrion Playfield Studio - Setup & Launch
echo ========================================================
echo.

:: Check for Python
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3 is not installed or not in PATH!
    echo Please install Python 3.10+ from python.org and check 'Add to PATH'.
    pause
    exit /b 1
)

:: Install/Upgrade dependencies
echo [*] Checking dependencies in requirements.txt...
pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo [!] Warning: Some dependencies failed to install. Retrying...
    pip install -r requirements.txt
)

echo [*] Starting Empyrion Playfield Studio...
echo.
python main.py
pause
