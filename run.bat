@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: 로그 폴더 생성
if not exist "logs" mkdir logs

:: 오늘 날짜로 로그 파일명 생성
set LOG_DATE=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG_FILE=logs\monitor_%LOG_DATE%.log

echo [%time%] 네스프레소 이벤트 모니터 시작 >> "%LOG_FILE%"
python nespresso_monitor.py >> "%LOG_FILE%" 2>&1
echo [%time%] 완료 >> "%LOG_FILE%"
