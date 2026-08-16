@echo off
title Trading Control Panel Launcher
cd /d "%~dp0"

if exist venv\Scripts\activate.bat (
    echo Activating virtual environment (venv)...
    call venv\Scripts\activate.bat
)

echo Starting trading dashboard...
python main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to launch. Please verify Python is installed and in your PATH.
    pause
)