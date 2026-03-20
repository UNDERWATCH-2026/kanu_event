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

:: 기존 작업 삭제 후 재등록
schtasks /delete /tn "%TASK_NAME%" /f > nul 2>&1

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%BAT_FILE%\"" ^
  /sc daily ^
  /st 09:00 ^
  /rl highest ^
  /f

if %ERRORLEVEL% == 0 (
    echo.
    echo ──────────────────────────────────────────────
    echo  [성공] 예약 작업 등록 완료
    echo  작업 이름 : %TASK_NAME%
    echo  실행 시간 : 매일 오전 09:00
    echo  스크립트  : %BAT_FILE%
    echo ──────────────────────────────────────────────
    echo.
    echo  확인: 작업 스케줄러 ^> 작업 스케줄러 라이브러리 ^> %TASK_NAME%
    echo  로그: %SCRIPT_DIR%logs\
) else (
    echo [오류] 예약 작업 등록 실패
)

pause
