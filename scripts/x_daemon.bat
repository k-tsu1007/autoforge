@echo off
REM X投稿デーモン — 常駐してpost_time_slotsで自動投稿
cd /d "%~dp0\.."

for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if not "%%a"=="" if not "%%a:~0,1"=="#" set "%%a=%%b"
)

python x_post_daemon.py >> logs\x_daemon.log 2>&1
