@echo off
REM deploy 後の再起動用。Publisher Service のみ起動する (daemon/webapp は停止中)。
REM daemon/webapp を再開するには下の REM を外してください。
cd /d C:\Users\Tsubasa\autoforge

echo [restart] stopping python processes...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set PY=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\python.exe

echo [restart] starting fuku_ai_sns publisher...
wmic process call create "%PY% -m services.publisher --instance fuku_ai_sns","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

echo [restart] starting ai_bento publisher...
wmic process call create "%PY% -m services.publisher --instance ai_bento","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

REM --- autoforge (daemon/webapp) は停止中 ---
REM echo [restart] starting fuku_ai_sns daemon...
REM wmic process call create "%PY% -m tools.run_daemon --instance fuku_ai_sns","C:\Users\Tsubasa\autoforge" | findstr ReturnValue
REM
REM echo [restart] starting fuku_ai_sns webapp...
REM wmic process call create "%PY% -m tools.run_webapp --instance fuku_ai_sns","C:\Users\Tsubasa\autoforge" | findstr ReturnValue
REM
REM echo [restart] starting ai_bento daemon...
REM wmic process call create "%PY% -m tools.run_daemon --instance ai_bento","C:\Users\Tsubasa\autoforge" | findstr ReturnValue
REM
REM echo [restart] starting ai_bento webapp...
REM wmic process call create "%PY% -m tools.run_webapp --instance ai_bento","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

timeout /t 3 /nobreak >nul
echo [restart] done. verifying...
tasklist | findstr python
