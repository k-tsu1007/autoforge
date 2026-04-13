@echo off
set PYTHONUNBUFFERED=1
cd /d C:\Users\Tsubasa\autoforge

:loop
echo [%date% %time%] daemon starting... >> C:\Users\Tsubasa\autoforge\logs\daemon.log 2>&1
python -m tools.run_daemon --instance fuku_ai_sns >> C:\Users\Tsubasa\autoforge\logs\daemon.log 2>&1
echo [%date% %time%] daemon exited, restarting in 5s... >> C:\Users\Tsubasa\autoforge\logs\daemon.log 2>&1
timeout /t 5 /nobreak > nul
goto loop
