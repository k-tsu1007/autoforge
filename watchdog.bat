@echo off
REM watchdog.bat — 5分ごとに実行。fuku_ai_sns daemon/webapp が落ちていたら再起動する
set PATH=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311;C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\Scripts;%PATH%

cd /d C:\Users\Tsubasa\autoforge

REM --- fuku_ai_sns daemon チェック ---
wmic process where "name='python.exe'" get commandline 2>nul | findstr "fuku_ai_sns" | findstr "run_daemon" > nul
if errorlevel 1 (
    echo [%date% %time%] watchdog: fuku_ai_sns daemon not running, restarting... >> logs\daemon.log
    schtasks /Run /TN autoforge-daemon > nul 2>&1
    echo [%date% %time%] watchdog: started autoforge-daemon task >> logs\daemon.log
) else (
    echo [%date% %time%] watchdog: fuku_ai_sns daemon OK >> logs\watchdog.log
)

REM --- fuku_ai_sns webapp チェック ---
wmic process where "name='python.exe'" get commandline 2>nul | findstr "fuku_ai_sns" | findstr "run_webapp" > nul
if errorlevel 1 (
    echo [%date% %time%] watchdog: fuku_ai_sns webapp not running, restarting... >> logs\daemon.log
    schtasks /Run /TN autoforge-webapp > nul 2>&1
    echo [%date% %time%] watchdog: started autoforge-webapp task >> logs\daemon.log
)

exit /b 0
