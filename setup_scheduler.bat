@echo off
chcp 65001 > nul

:: ── 관리자 권한 확인 ─────────────────────────────────────────
net session > nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [오류] 관리자 권한으로 실행해주세요.
    echo 이 파일을 우클릭 후 "관리자 권한으로 실행"을 선택하세요.
    pause
    exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "BAT_FILE=%SCRIPT_DIR%run.bat"
set "TASK_NAME=NespressoMonitor"

:: 로그 폴더 생성
if not exist "%SCRIPT_DIR%logs" mkdir "%SCRIPT_DIR%logs"

:: PowerShell로 등록 (StartWhenAvailable 지원)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$action  = New-ScheduledTaskAction -Execute '\"%SCRIPT_DIR%run.bat\"';" ^
  "$trigger = New-ScheduledTaskTrigger -Daily -At '09:00';" ^
  "$settings = New-ScheduledTaskSettingsSet" ^
  "  -StartWhenAvailable" ^
  "  -ExecutionTimeLimit (New-TimeSpan -Hours 2)" ^
  "  -MultipleInstances IgnoreNew;" ^
  "Unregister-ScheduledTask -TaskName '%TASK_NAME%' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "Register-ScheduledTask -TaskName '%TASK_NAME%' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null;" ^
  "Write-Host 'OK'"

if %ERRORLEVEL% == 0 (
    echo.
    echo ──────────────────────────────────────────────
    echo  [성공] 예약 작업 등록 완료
    echo  작업 이름 : %TASK_NAME%
    echo  실행 시간 : 매일 오전 09:00
    echo  놓친 실행 : 컴퓨터 켜질 때 즉시 실행 (하루 1회)
    echo  스크립트  : %BAT_FILE%
    echo ──────────────────────────────────────────────
    echo.
    echo  확인: 작업 스케줄러 ^> 작업 스케줄러 라이브러리 ^> %TASK_NAME%
    echo  로그: %SCRIPT_DIR%logs\
) else (
    echo [오류] 예약 작업 등록 실패
)

pause
