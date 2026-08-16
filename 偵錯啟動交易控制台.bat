title Trading Dashboard - Debug Launcher
cd /d "%~dp0"

echo ===================================================
echo [DEBUG] Step 1: Checking Python Environment...
python --version
echo ===================================================

echo [DEBUG] Step 2: Attempting to Launch main.py...
python main.py

echo ===================================================
echo [DEBUG] Process ended with exit code %errorlevel%.
echo If you saw a ModuleNotFoundError above, please run:
echo pip install -r requirements.txt
echo ===================================================
pause