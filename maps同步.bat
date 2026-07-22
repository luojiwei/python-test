@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   地图数据同步
echo   从地图标记工具 → 自动打怪脚本
echo ========================================
echo.
python sync_maps.py %*
echo.
pause
