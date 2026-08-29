@echo off
REM Local Notes Station - Desktop (pywebview / WebView2) Launcher
REM Reuses the Web UI inside a system WebView2 window; HTML/SPA renders fully.
cd /d "%~dp0"

REM Check core deps
python -c "import flask, bleach, markdown" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Missing core deps. Installing from requirements.txt ...
    python -m pip install -r requirements.txt
)

REM Check pywebview (WebView2 shell)
python -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo [WARN] pywebview not found. Installing...
    python -m pip install pywebview
)

echo Starting desktop app (WebView2 window)...
python desktop_webview.py
pause