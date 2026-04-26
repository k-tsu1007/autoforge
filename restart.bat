@echo off
REM deploy 後の再起動用。Publisher Service のみ起動する (daemon/webapp は停止中)。
REM daemon/webapp を再開するには下の REM を外してください。
cd /d C:\Users\Tsubasa\autoforge

echo [restart] stopping python processes...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

set PY=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\python.exe

REM ログディレクトリ確保
if not exist logs mkdir logs

REM 前回ログをローテート (タイムスタンプ付きで退避)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set TS=%%I
set TS=%TS:~0,8%_%TS:~8,6%
if exist logs\publisher_fuku.log move /Y logs\publisher_fuku.log logs\publisher_fuku_%TS%.log >nul
if exist logs\publisher_ai_bento.log move /Y logs\publisher_ai_bento.log logs\publisher_ai_bento_%TS%.log >nul

echo [restart] starting fuku_ai_sns publisher (log: logs\publisher_fuku.log)...
wmic process call create "cmd /c %PY% -m services.publisher --instance fuku_ai_sns >> logs\publisher_fuku.log 2>&1","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

echo [restart] starting ai_bento publisher (log: logs\publisher_ai_bento.log)...
wmic process call create "cmd /c %PY% -m services.publisher --instance ai_bento >> logs\publisher_ai_bento.log 2>&1","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

REM echo [restart] starting fuku_ai_sns SNS service...
REM wmic process call create "%PY% -m services.sns --instance fuku_ai_sns --port 8020","C:\Users\Tsubasa\autoforge" | findstr ReturnValue

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
