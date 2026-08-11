@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYTHON=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe

if not exist "%PYTHON%" (
    echo [错误] Python 不存在: %PYTHON%
    pause
    exit /b 1
)

echo ========================================
echo   地图数据同步
echo   从地图标记工具 → 自动打怪脚本
echo ========================================
echo.

"%PYTHON%" sync_maps.py %*
echo.
pause
