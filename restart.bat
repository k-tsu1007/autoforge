@echo off
cd /d C:\Users\Tsubasa\autoforge

echo [restart] stopping python processes...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [restart] starting daemon...
start /B python -m tools.run_daemon --instance fuku_ai_sns > data\daemon.log 2>&1

echo [restart] starting webapp...
start /B python -m tools.run_webapp --instance fuku_ai_sns > data\webapp.log 2>&1

timeout /t 3 /nobreak >nul
echo [restart] done. verifying...
tasklist | findstr python
