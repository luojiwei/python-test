@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 本工具需要带 tkinter 的 Python（托管 3.13.12 不含 tkinter）
set PYTHONW=%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe

if not exist "%PYTHONW%" (
    echo [错误] 未找到带 tkinter 的 Python: %PYTHONW%
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%~dp0main.py"
