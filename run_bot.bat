@echo off
REM Telegram Casino Bot Startup Script
REM ===================================

echo.
echo Starting Telegram Casino Bot...
echo.

REM Navigate to the bot directory
cd /d "%~dp0"

REM Check if virtual environment exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Check dependencies
echo Checking dependencies...
pip install -r requirements.txt -q 2>nul

REM Run the bot (token is read from .env — never hardcoded)
echo.
echo ========================================
echo Starting bot via: python -m optimus
echo ========================================
echo.

python -m optimus

REM If bot exits, show exit code
if errorlevel 1 (
    echo.
    echo Error: Bot exited with error code %errorlevel%
    pause
) else (
    echo.
    echo Bot stopped successfully.
    pause
)
