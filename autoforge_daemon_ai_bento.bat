@echo off
REM 単発起動。落ちたら Web UI から手動で再起動する。
set PYTHONUNBUFFERED=1
set PATH=C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311;C:\Users\Tsubasa\AppData\Local\Programs\Python\Python311\Scripts;%PATH%
set PLAYWRIGHT_BROWSERS_PATH=C:\Users\Tsubasa\AppData\Local\ms-playwright
cd /d C:\Users\Tsubasa\autoforge

echo [%date% %time%] daemon starting... >> C:\Users\Tsubasa\autoforge\instances\ai_bento\daemon.log 2>&1
python -m tools.run_daemon --instance ai_bento >> C:\Users\Tsubasa\autoforge\instances\ai_bento\daemon.log 2>&1
echo [%date% %time%] daemon exited. >> C:\Users\Tsubasa\autoforge\instances\ai_bento\daemon.log 2>&1
