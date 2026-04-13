@echo off
set PYTHONUNBUFFERED=1
set PATH=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311;C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\Scripts;%PATH%
cd /d C:\Users\Tsubasa\autoforge

:loop
echo [%date% %time%] webapp starting... >> C:\Users\Tsubasa\autoforge\logs\webapp_fuku.log 2>&1
python -m tools.run_webapp --instance fuku_ai_sns >> C:\Users\Tsubasa\autoforge\logs\webapp_fuku.log 2>&1
echo [%date% %time%] webapp exited, restarting in 5s... >> C:\Users\Tsubasa\autoforge\logs\webapp_fuku.log 2>&1
timeout /t 5 /nobreak > nul
goto loop
