@echo off
REM deploy 後の再起動用。wmic で fully detached で起動し、
REM ssh 切断・deploy後に webapp/daemon が死なないようにする。
cd /d C:\Users\Tsubasa\autoforge

echo [restart] stopping python processes...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set PY=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\python.exe

echo [restart] starting fuku_ai_sns daemon...
wmic process call create "%PY% -m tools.run_daemon --instance fuku_ai_sns","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

echo [restart] starting fuku_ai_sns webapp...
wmic process call create "%PY% -m tools.run_webapp --instance fuku_ai_sns","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

echo [restart] starting ai_bento daemon...
wmic process call create "%PY% -m tools.run_daemon --instance ai_bento","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

echo [restart] starting ai_bento webapp...
wmic process call create "%PY% -m tools.run_webapp --instance ai_bento","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

timeout /t 3 /nobreak >nul
echo [restart] done. verifying...
tasklist | findstr python
