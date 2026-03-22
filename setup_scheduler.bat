@echo off
net session > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Run as administrator: right-click and select "Run as administrator"
    pause
    exit /b 1
)
if not exist "C:\kanu\logs" mkdir "C:\kanu\logs"
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\kanu\setup_task.ps1"
pause
