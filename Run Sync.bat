@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^)
  pause
  exit /b 1
)
if not exist ".venv\Scripts\activate.bat" (
  echo First run: setting things up, this takes a minute...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)
python moodle_sync.py %*
pause
