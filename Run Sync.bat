@echo off
setlocal
cd /d "%~dp0"

rem --- 1. find Python ---
set "PYCMD="
python --version >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
  py -3 --version >nul 2>nul && set "PYCMD=py -3"
)
if not defined PYCMD (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^)
  goto :fail
)

rem --- 2. use the project's own environment (.venv). It is called directly, never "activated",
rem        so it keeps working even if you move or rename this folder. ---
set "VPY=%~dp0.venv\Scripts\python.exe"

if exist "%VPY%" (
  "%VPY%" -c "pass" >nul 2>nul
  if errorlevel 1 (
    echo The saved environment is broken. Rebuilding it...
    rmdir /s /q ".venv"
  )
)

if not exist "%VPY%" (
  echo First run: setting things up, this takes a minute...
  %PYCMD% -m venv .venv
  if errorlevel 1 goto :fail
)

rem --- 3. make sure the packages are really inside it ---
"%VPY%" -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo Installing the required packages...
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
)

rem --- 4. run ---
"%VPY%" moodle_sync.py --no-pause %*
pause
exit /b 0

:fail
echo.
echo Setup did not finish. Read the message above. You can also try typing:
echo     pip install playwright
echo and then run this file again.
pause
exit /b 1
