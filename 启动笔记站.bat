@echo off
REM ==============================================
REM  Local Notes Station - One-click Launcher
REM  Works offline. Pure ASCII (no encoding issues).
REM ==============================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo   Local Notes Station starting...
echo   URL: http://127.0.0.1:8848
echo   Keep this window open. Close to stop.
echo ============================================
echo.

REM ---- Pick a Python interpreter ----
set "PYCMD="
if exist ".venv\Scripts\python.exe" (
    set "PYCMD=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYCMD=venv\Scripts\python.exe"
)

if defined PYCMD goto :launch

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYCMD=python"
    goto :launch
)
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYCMD=py -3"
    goto :launch
)

echo.
echo [ERROR] Python not found on PATH.
echo Install Python 3, or put it in a folder named .venv .
echo.
pause
exit /b 1

:launch
echo Using: %PYCMD%
echo.

REM ---- Dependency check ----
%PYCMD% -c "import flask, bleach, markdown, werkzeug" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] Dependencies not installed for this Python.
    echo Run this, then try again:
    echo.
    echo     %PYCMD% -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ---- Open browser after short delay, then start server ----
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8848"
%PYCMD% app.py

echo.
echo Server stopped.
pause