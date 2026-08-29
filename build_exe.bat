@echo off
cd /d "%~dp0"
echo ======================================================
echo   Local Notes Station - Windows EXE Builder
echo ======================================================
echo.

REM ---- 1. Check Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ first:
    echo         https://www.python.org/downloads/
    echo         (Remember to check "Add python.exe to PATH")
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Using Python %PYVER%

REM ---- 2. Install build and runtime dependencies ----
echo [1/3] Installing dependencies (first run may take a while)...
python -m pip install --upgrade pyinstaller pywebview pythonnet
if errorlevel 1 (
    echo [ERROR] Failed to install build tools. Please check network.
    pause
    exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install runtime dependencies. Please check network.
    pause
    exit /b 1
)
echo        Dependencies ready.

REM ---- 3. Clean old artifacts ----
echo [2/3] Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

REM ---- 4. Build ----
echo [3/3] Building with PyInstaller (about 1-3 min, please wait)...
python -m PyInstaller --noconfirm desktop_notes.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ======================================================
echo   BUILD COMPLETE!
echo.
echo   Output:  dist\LocalNotesStation.exe  (single file)
echo.
echo   How to use:
echo     1. Put the exe into any folder
echo     2. Double-click to run. Notes data will be created
echo        beside the exe:
echo            notes.db     (database)
echo            uploads\     (your files)
echo     3. Backup / move: just copy the whole folder
echo.
echo   Tips:
echo     - First launch is slower (self-extract), that is normal
echo     - If antivirus warns, add an exception (PyInstaller is
echo       commonly false positive)
echo     - Needs WebView2 runtime (built into Windows 10/11)
echo ======================================================
pause
