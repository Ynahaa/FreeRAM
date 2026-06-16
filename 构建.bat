@echo off
cd /d "%~dp0"

echo ==========================================
echo   FreeRAM - Build
echo ==========================================
echo.

taskkill /f /im FreeRAM.exe >nul 2>&1
echo [1/4] Stopped FreeRAM

if exist "dist\FreeRAM.exe" del /q "dist\FreeRAM.exe"
if exist "build" rmdir /s /q "build"
if exist "FreeRAM.spec" del /q "FreeRAM.spec"
echo [2/4] Cleaned old build

echo [3/4] Building...
python -m PyInstaller --onefile --windowed --name "FreeRAM" --icon "FreeRAM.ico" --hidden-import psutil --hidden-import PySide6 --clean main.py

if %errorlevel% neq 0 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

if exist "build" rmdir /s /q "build"
if exist "FreeRAM.spec" del /q "FreeRAM.spec"
echo [4/4] Done

echo.
echo dist\FreeRAM.exe is ready
start "" "dist"
exit
