@echo off
cd /d "%~dp0"

echo ==========================================
echo   FreeRAM — 打包工具
echo ==========================================
echo.

:: 检查 PyInstaller
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 正在安装 PyInstaller...
    python -m pip install pyinstaller --quiet --disable-pip-version-check
    if !errorlevel! neq 0 (
        echo [ERROR] 安装失败
        pause
        exit /b 1
    )
)
echo [OK] PyInstaller 已就绪

:: 清理旧构建
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

:: 打包
echo.
echo [..] 正在打包成单文件 exe...
echo.

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "FreeRAM" ^
    --icon "FreeRAM.ico" ^
    --hidden-import psutil ^
    --hidden-import PySide6 ^
    --hidden-import memory_cleaner ^
    --hidden-import safe_detector ^
    --clean ^
    main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 打包失败！
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   打包完成！
echo   exe 位置: dist\FreeRAM.exe
echo ==========================================
echo.

start "" "dist"
pause
